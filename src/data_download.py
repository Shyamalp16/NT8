"""Restartable planning and ingestion for read-only historical data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import fields, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import yaml

from src.contract_rolls import (
    ContractSpec,
    build_quarterly_inventory,
    choose_roll_date,
    read_contract_inventory,
    write_contract_inventory,
)
from src.mcp_client import MarketHistoryResponse, RequestSpec
from src.mcp_client.models import parse_timestamp


ROOT = Path(__file__).resolve().parents[1]
UTC = ZoneInfo("UTC")


def load_config(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def month_contracts(config: dict[str, Any], product: str) -> list[ContractSpec]:
    period = config["research_period"]
    return build_quarterly_inventory(
        product=product,
        start_date=date.fromisoformat(str(period["start_date"])),
        end_date=date.fromisoformat(str(period["end_date"])),
        fallback_business_days=int(
            config["contract_rolls"]["fallback_business_days_before_expiry"]
        ),
    )


def plan_roll_daily_requests(
    contracts: list[ContractSpec],
    lookback_calendar_days: int,
) -> list[RequestSpec]:
    requests: list[RequestSpec] = []
    for index, front_contract in enumerate(contracts[:-1]):
        next_contract = contracts[index + 1]
        start = front_contract.expiry_date - timedelta(days=lookback_calendar_days)
        end = front_contract.expiry_date
        for role, contract in (
            ("front", front_contract),
            ("next", next_contract),
        ):
            requests.append(
                _request(
                    purpose=f"roll_daily_{role}",
                    contract=contract,
                    bar_type="Daily",
                    bar_size=1,
                    start_date=start,
                    end_date=end,
                )
            )
    return requests


def plan_minute_requests(
    contracts: list[ContractSpec],
    research_start: date,
    research_end: date,
    max_calendar_days: int,
) -> list[RequestSpec]:
    """Plan non-overlapping date chunks using the provisional roll calendar."""
    if max_calendar_days < 1:
        raise ValueError("max_calendar_days must be positive")

    requests: list[RequestSpec] = []
    interval_start = research_start
    for index, contract in enumerate(contracts):
        interval_end = min(contract.effective_roll_date - timedelta(days=1), research_end)
        if index == len(contracts) - 1:
            interval_end = research_end
        if interval_end < interval_start:
            continue

        chunk_start = interval_start
        while chunk_start <= interval_end:
            chunk_end = min(
                chunk_start + timedelta(days=max_calendar_days - 1),
                interval_end,
            )
            requests.append(
                _request(
                    purpose="minute_discovery",
                    contract=contract,
                    bar_type="Minute",
                    bar_size=1,
                    start_date=chunk_start,
                    end_date=chunk_end,
                )
            )
            chunk_start = chunk_end + timedelta(days=1)

        interval_start = interval_end + timedelta(days=1)
        if interval_start > research_end:
            break
    return requests


def _request(
    purpose: str,
    contract: ContractSpec,
    bar_type: str,
    bar_size: int,
    start_date: date,
    end_date: date,
) -> RequestSpec:
    eastern = ZoneInfo("America/New_York")
    start_utc = datetime.combine(start_date, time.min, eastern).astimezone(UTC)
    end_utc = datetime.combine(end_date, time(23, 59, 59), eastern).astimezone(UTC)
    identity = "|".join(
        [
            purpose,
            contract.provider_symbol,
            str(contract.contract_year),
            bar_type,
            str(bar_size),
            start_utc.isoformat(),
            end_utc.isoformat(),
        ]
    )
    request_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return RequestSpec(
        request_id=request_id,
        purpose=purpose,
        symbol=contract.provider_symbol,
        contract_year=contract.contract_year,
        bar_type=bar_type,
        bar_size=bar_size,
        from_utc=_iso_z(start_utc),
        to_utc=_iso_z(end_utc),
    )


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    _atomic_write(path, payload.encode("utf-8"))


def ingest_response(
    request: RequestSpec,
    tool_result: dict[str, Any],
    raw_dir: Path,
    ledger_path: Path,
) -> dict[str, Any]:
    response = MarketHistoryResponse.from_tool_result(tool_result)
    if response.symbol != request.symbol:
        raise ValueError(
            f"Response symbol {response.symbol} does not match request {request.symbol}"
        )
    if response.bar_type != request.bar_type or response.bar_size != request.bar_size:
        raise ValueError("Response bar aggregation does not match the request")

    canonical = json.dumps(tool_result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    checksum = hashlib.sha256(canonical).hexdigest()
    raw_path = raw_dir / request.purpose / f"{request.request_id}.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    if raw_path.exists():
        existing_checksum = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        if existing_checksum != checksum:
            raise FileExistsError(
                f"Immutable raw response differs for request {request.request_id}"
            )
        status = "cached_verified"
    else:
        _atomic_write(raw_path, canonical)
        status = "success" if response.bars else "empty"

    timestamps = [bar.timestamp for bar in response.bars]
    parsed_timestamps = [parse_timestamp(value) for value in timestamps]
    ledger_record = {
        "request_id": request.request_id,
        "purpose": request.purpose,
        "symbol": request.symbol,
        "contract_year": request.contract_year,
        "bar_type": request.bar_type,
        "bar_size": request.bar_size,
        "from_utc": request.from_utc,
        "to_utc": request.to_utc,
        "status": status,
        "attempts": 1,
        "row_count": len(response.bars),
        "provider_first_timestamp": timestamps[0] if timestamps else None,
        "provider_last_timestamp": timestamps[-1] if timestamps else None,
        "min_timestamp": _iso_z(min(parsed_timestamps)) if parsed_timestamps else None,
        "max_timestamp": _iso_z(max(parsed_timestamps)) if parsed_timestamps else None,
        "duplicate_timestamp_count": response.duplicate_timestamp_count,
        "out_of_order_timestamp_count": response.out_of_order_timestamp_count,
        "sha256": checksum,
        "raw_path": _display_path(raw_path),
        "ingested_at_utc": _iso_z(datetime.now(UTC)),
    }
    _append_unique_ledger(ledger_path, ledger_record)
    return ledger_record


def _append_unique_ledger(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if path.exists():
        existing = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    for row in existing:
        if row["request_id"] == record["request_id"]:
            if row["sha256"] != record["sha256"]:
                raise ValueError(f"Ledger checksum conflict for {record['request_id']}")
            return
    existing.append(record)
    write_jsonl(path, existing)


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def build_plans(config_path: Path, output_dir: Path) -> dict[str, int]:
    config = load_config(config_path)
    product = config["instruments"]["primary_product"]
    provisional_contracts = month_contracts(config, product)
    write_contract_inventory(
        output_dir / "contract_inventory_provisional.csv",
        provisional_contracts,
    )
    final_inventory_path = output_dir / "contract_inventory.csv"
    contracts = (
        read_contract_inventory(final_inventory_path)
        if final_inventory_path.exists()
        else provisional_contracts
    )

    roll_requests = plan_roll_daily_requests(
        provisional_contracts,
        int(config["contract_rolls"]["volume_crossover_lookback_calendar_days"]),
    )
    period = config["research_period"]
    minute_requests = plan_minute_requests(
        contracts,
        date.fromisoformat(str(period["start_date"])),
        date.fromisoformat(str(period["end_date"])),
        int(config["data_policy"]["minute_request_max_calendar_days"]),
    )
    write_jsonl(
        output_dir / "roll_requests.jsonl",
        (request.to_record() for request in roll_requests),
    )
    write_jsonl(
        output_dir / "minute_requests.jsonl",
        (request.to_record() for request in minute_requests),
    )
    latest_session = date.fromisoformat(str(period["latest_fully_completed_session"]))
    current_symbol = config["instruments"]["current_primary_contract"]
    pilot_contract = next(
        contract
        for contract in provisional_contracts
        if contract.provider_symbol == current_symbol
    )
    pilot_request = _request(
        purpose="pilot_minute",
        contract=pilot_contract,
        bar_type="Minute",
        bar_size=1,
        start_date=latest_session,
        end_date=latest_session,
    )
    write_jsonl(output_dir / "pilot_requests.jsonl", [pilot_request.to_record()])
    return {
        "contracts": len(contracts),
        "roll_requests": len(roll_requests),
        "minute_requests": len(minute_requests),
        "pilot_requests": 1,
    }


def request_from_manifest(path: Path, request_id: str) -> RequestSpec:
    allowed = {field.name for field in fields(RequestSpec)}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("request_id") == request_id:
            return RequestSpec(**{key: row[key] for key in allowed})
    raise KeyError(f"Request ID not found in manifest: {request_id}")


def ingest_response_directory(
    request_manifest: Path,
    response_dir: Path,
    raw_dir: Path,
    ledger_path: Path,
    failed_log_path: Path | None = None,
) -> dict[str, int]:
    records = [
        json.loads(line)
        for line in request_manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    succeeded = 0
    missing = 0
    failed = 0
    for row in records:
        response_path = response_dir / f"{row['request_id']}.json"
        if not response_path.exists():
            missing += 1
            continue
        try:
            request = request_from_manifest(request_manifest, row["request_id"])
            tool_result = json.loads(response_path.read_text(encoding="utf-8"))
            ingest_response(request, tool_result, raw_dir, ledger_path)
            succeeded += 1
        except (FileExistsError, KeyError, TypeError, ValueError) as error:
            failed += 1
            if failed_log_path is not None:
                _append_failure(
                    failed_log_path,
                    request_id=row["request_id"],
                    error=error,
                    attempt=1,
                )
    return {"succeeded": succeeded, "missing": missing, "failed": failed}


def acquisition_status(request_manifest: Path, ledger_path: Path) -> dict[str, int]:
    planned = {
        json.loads(line)["request_id"]
        for line in request_manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    ledger_rows = []
    if ledger_path.exists():
        ledger_rows = [
            json.loads(line)
            for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    completed_statuses = {"success", "empty", "cached_verified"}
    completed = {
        row["request_id"]
        for row in ledger_rows
        if row["request_id"] in planned and row["status"] in completed_statuses
    }
    return {
        "planned": len(planned),
        "completed": len(completed),
        "pending": len(planned - completed),
    }


def retry_delay_seconds(config: dict[str, Any], attempt: int) -> int:
    delays = config["data_policy"]["request_retry_backoff_seconds"]
    if attempt < 1:
        raise ValueError("attempt must be at least 1")
    return int(delays[min(attempt - 1, len(delays) - 1)])


def _append_failure(
    path: Path,
    request_id: str,
    error: Exception,
    attempt: int,
) -> None:
    rows = []
    if path.exists():
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    rows.append(
        {
            "request_id": request_id,
            "attempt": attempt,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "failed_at_utc": _iso_z(datetime.now(UTC)),
        }
    )
    write_jsonl(path, rows)


def finalize_roll_inventory(
    config: dict[str, Any],
    raw_dir: Path,
    output_path: Path,
) -> list[ContractSpec]:
    contracts = month_contracts(config, config["instruments"]["primary_product"])
    lookback_days = int(
        config["contract_rolls"]["volume_crossover_lookback_calendar_days"]
    )
    confirmation_sessions = int(
        config["contract_rolls"]["volume_crossover_confirmation_sessions"]
    )
    fallback_days = int(
        config["contract_rolls"]["fallback_business_days_before_expiry"]
    )
    selected: list[ContractSpec] = []

    for index, front_contract in enumerate(contracts[:-1]):
        next_contract = contracts[index + 1]
        start = front_contract.expiry_date - timedelta(days=lookback_days)
        end = front_contract.expiry_date
        front_request = _request(
            "roll_daily_front", front_contract, "Daily", 1, start, end
        )
        next_request = _request(
            "roll_daily_next", next_contract, "Daily", 1, start, end
        )
        front_volume = _daily_volume(
            raw_dir / front_request.purpose / f"{front_request.request_id}.json"
        )
        next_volume = _daily_volume(
            raw_dir / next_request.purpose / f"{next_request.request_id}.json"
        )
        roll_date, method = choose_roll_date(
            front_volume,
            next_volume,
            front_contract.expiry_date,
            confirmation_sessions,
            fallback_days,
        )
        selected.append(
            replace(
                front_contract,
                selected_roll_date=roll_date,
                roll_method=method,
            )
        )

    selected.append(replace(contracts[-1], roll_method="research_end_before_roll"))
    write_contract_inventory(output_path, selected)
    return selected


def _daily_volume(path: Path) -> dict[date, float]:
    if not path.exists():
        raise FileNotFoundError(f"Missing roll response: {path}")
    result = json.loads(path.read_text(encoding="utf-8"))
    response = MarketHistoryResponse.from_tool_result(result)
    return {
        parse_timestamp(bar.timestamp).date(): bar.total_volume
        for bar in response.bars
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    plan_parser = subparsers.add_parser("plan", help="Build contract and request manifests")
    plan_parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config" / "research.yaml",
    )
    plan_parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "manifests",
    )

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Validate and cache one saved MCP market-history response",
    )
    ingest_parser.add_argument("--request-manifest", type=Path, required=True)
    ingest_parser.add_argument("--request-id", required=True)
    ingest_parser.add_argument("--response", type=Path, required=True)
    ingest_parser.add_argument("--raw-dir", type=Path, default=ROOT / "data" / "raw")
    ingest_parser.add_argument(
        "--ledger",
        type=Path,
        default=ROOT / "data" / "manifests" / "download_ledger.jsonl",
    )

    batch_parser = subparsers.add_parser(
        "ingest-batch",
        help="Validate and cache a directory of saved MCP responses",
    )
    batch_parser.add_argument("--request-manifest", type=Path, required=True)
    batch_parser.add_argument("--response-dir", type=Path, required=True)
    batch_parser.add_argument("--raw-dir", type=Path, default=ROOT / "data" / "raw")
    batch_parser.add_argument(
        "--ledger",
        type=Path,
        default=ROOT / "data" / "manifests" / "download_ledger.jsonl",
    )
    batch_parser.add_argument(
        "--failed-log",
        type=Path,
        default=ROOT / "data" / "manifests" / "failed_requests.jsonl",
    )

    finalize_parser = subparsers.add_parser(
        "finalize-rolls",
        help="Select roll dates from paired daily-volume responses",
    )
    finalize_parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config" / "research.yaml",
    )
    finalize_parser.add_argument("--raw-dir", type=Path, default=ROOT / "data" / "raw")
    finalize_parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "manifests" / "contract_inventory.csv",
    )

    status_parser = subparsers.add_parser(
        "status",
        help="Report completed and pending requests for one manifest",
    )
    status_parser.add_argument("--request-manifest", type=Path, required=True)
    status_parser.add_argument(
        "--ledger",
        type=Path,
        default=ROOT / "data" / "manifests" / "download_ledger.jsonl",
    )

    args = parser.parse_args()
    if args.command in (None, "plan"):
        config_path = getattr(args, "config", ROOT / "config" / "research.yaml")
        output_dir = getattr(args, "output_dir", ROOT / "data" / "manifests")
        result = build_plans(config_path, output_dir)
    elif args.command == "ingest":
        request = request_from_manifest(args.request_manifest, args.request_id)
        tool_result = json.loads(args.response.read_text(encoding="utf-8"))
        result = ingest_response(request, tool_result, args.raw_dir, args.ledger)
    elif args.command == "ingest-batch":
        result = ingest_response_directory(
            args.request_manifest,
            args.response_dir,
            args.raw_dir,
            args.ledger,
            args.failed_log,
        )
    elif args.command == "finalize-rolls":
        config = load_config(args.config)
        contracts = finalize_roll_inventory(config, args.raw_dir, args.output)
        result = {
            "contracts": len(contracts),
            "volume_crossover_rolls": sum(
                contract.roll_method == "confirmed_volume_crossover_next_session"
                for contract in contracts
            ),
            "fallback_rolls": sum(
                contract.roll_method == "fallback_business_days_before_expiry"
                for contract in contracts
            ),
        }
    else:
        result = acquisition_status(args.request_manifest, args.ledger)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
