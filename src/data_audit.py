"""Phase 3 normalization and data-quality audit for MNQ one-minute bars."""

from __future__ import annotations

import argparse
import csv
import importlib.metadata
import json
import os
import platform
from collections import defaultdict
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any, Iterable

import duckdb
import pandas as pd
import yaml

from src.contract_rolls import read_contract_inventory
from src.data_download import write_jsonl
from src.normalize import load_jsonl, normalize_response, sha256_file
from src.sessions import (
    EASTERN,
    cme_equity_schedule,
    expected_minutes,
    rth_mask,
    split_name,
)


ROOT = Path(__file__).resolve().parents[1]


def run_phase3(
    config_path: Path = ROOT / "config" / "research.yaml",
    split_config_path: Path = ROOT / "config" / "data_splits.yaml",
    raw_dir: Path = ROOT / "data" / "raw",
    manifests_dir: Path = ROOT / "data" / "manifests",
    normalized_dir: Path = ROOT / "data" / "normalized",
) -> dict[str, Any]:
    """Run the complete, restartable Phase 3 audit."""
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    split_config = yaml.safe_load(split_config_path.read_text(encoding="utf-8"))
    period = config["research_period"]
    research_start = date.fromisoformat(str(period["start_date"]))
    research_end = date.fromisoformat(str(period["end_date"]))
    tick_size = float(config["instruments"]["tick_size"])

    ledger = [
        row
        for row in load_jsonl(manifests_dir / "download_ledger.jsonl")
        if row.get("purpose") == "minute_discovery"
        and row.get("status") in {"success", "cached_verified"}
    ]
    ledger.sort(key=lambda row: (row["from_utc"], row["request_id"]))
    planned = load_jsonl(manifests_dir / "minute_requests.jsonl")
    planned_ids = {row["request_id"] for row in planned}
    acquired_ids = {row["request_id"] for row in ledger}
    if planned_ids != acquired_ids:
        missing = sorted(planned_ids - acquired_ids)
        extra = sorted(acquired_ids - planned_ids)
        raise ValueError(
            f"Minute acquisition is incomplete or inconsistent; missing={missing}, extra={extra}"
        )

    normalized_dir.mkdir(parents=True, exist_ok=True)
    request_audits: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    with TemporaryDirectory(prefix=".phase3_", dir=normalized_dir) as temporary:
        work_dir = Path(temporary)
        batch_dir = work_dir / "batches"
        batch_dir.mkdir()
        for index, ledger_row in enumerate(ledger):
            raw_path = (
                raw_dir
                / str(ledger_row["purpose"])
                / f"{ledger_row['request_id']}.json"
            )
            batch = normalize_response(
                raw_path=raw_path,
                ledger_row=ledger_row,
                split_config=split_config,
                tick_size=tick_size,
            )
            batch.frame["bar_signature"] = pd.util.hash_pandas_object(
                batch.frame[
                    [
                        "timestamp_utc",
                        "symbol",
                        "open",
                        "high",
                        "low",
                        "close",
                        "up_volume",
                        "down_volume",
                        "up_ticks",
                        "down_ticks",
                    ]
                ],
                index=False,
            ).astype("uint64")
            batch_path = batch_dir / f"{index:04d}_{ledger_row['request_id']}.parquet"
            batch.frame.to_parquet(
                batch_path,
                index=False,
                compression="zstd",
                engine="pyarrow",
            )
            request_audits.append(batch.request_audit)
            issues.extend(batch.issues)

        base_path = work_dir / "deduplicated.parquet"
        duplicate_audit = _deduplicate_batches(batch_dir, base_path)
        issues.extend(duplicate_audit["issues"])

        bars_for_audit = pd.read_parquet(
            base_path,
            columns=[
                "timestamp_utc",
                "timestamp_et",
                "session_date",
                "symbol",
                "open",
                "close",
                "row_valid",
            ],
        )
        inventory = read_contract_inventory(manifests_dir / "contract_inventory.csv")
        roll_dates = {
            contract.selected_roll_date
            for contract in inventory[:-1]
            if contract.selected_roll_date is not None
        }

        quality, exclusions, gap_runs = audit_sessions(
            bars=bars_for_audit,
            research_start=research_start,
            research_end=research_end,
            split_config=split_config,
            roll_dates=roll_dates,
            duplicate_sessions=duplicate_audit["session_counts"],
        )
        roll_audit = audit_roll_boundaries(
            bars=bars_for_audit,
            inventory=inventory,
            tick_size=tick_size,
        )
        for exclusion in exclusions:
            for reason in exclusion["reasons"]:
                issues.append(
                    {
                        "scope": "session",
                        "session_date": exclusion["session_date"],
                        "split": exclusion["split"],
                        "issue_code": reason,
                        "severity": exclusion["severity"],
                        "action": exclusion["action"],
                    }
                )

        quality_path = manifests_dir / "session_quality.csv"
        exclusions_path = manifests_dir / "exclusions.jsonl"
        gap_runs_path = manifests_dir / "missing_minute_runs.jsonl"
        request_audit_path = manifests_dir / "request_quality.jsonl"
        issue_path = manifests_dir / "data_quality_issues.jsonl"
        roll_audit_path = manifests_dir / "roll_boundary_audit.csv"
        _write_csv_atomic(quality_path, quality)
        write_jsonl(exclusions_path, exclusions)
        write_jsonl(gap_runs_path, gap_runs)
        write_jsonl(request_audit_path, request_audits)
        write_jsonl(issue_path, issues)
        _write_csv_atomic(roll_audit_path, roll_audit)

        quality_parquet = work_dir / "session_quality.parquet"
        quality.to_parquet(quality_parquet, index=False)
        final_path = normalized_dir / "mnq_1m.parquet"
        _write_final_normalized(
            base_path=base_path,
            quality_path=quality_parquet,
            output_path=final_path,
            research_start=research_start,
            research_end=research_end,
        )

    final_profile = _profile_final(final_path)
    summary = _build_summary(
        quality=quality,
        exclusions=exclusions,
        request_audits=request_audits,
        issues=issues,
        duplicate_audit=duplicate_audit,
        roll_audit=roll_audit,
        final_profile=final_profile,
        research_start=research_start,
        research_end=research_end,
        source_rows=sum(int(row["row_count"]) for row in ledger),
        outside_research_rows=int(
            (
                (bars_for_audit["session_date"] < research_start)
                | (bars_for_audit["session_date"] > research_end)
            ).sum()
        ),
    )
    summary_path = manifests_dir / "data_quality_summary.json"
    _write_json_atomic(summary_path, summary)

    normalized_manifest = {
        "artifact": final_path.relative_to(ROOT).as_posix(),
        "format": "parquet",
        "compression": "zstd",
        "sha256": sha256_file(final_path),
        "bytes": final_path.stat().st_size,
        **final_profile,
        "source_manifest": "data/manifests/minute_requests.jsonl",
        "source_ledger": "data/manifests/download_ledger.jsonl",
        "session_quality": "data/manifests/session_quality.csv",
        "exclusions": "data/manifests/exclusions.jsonl",
        "created_by": "python -m src.data_audit run",
        "normalization_policy": "phase3_v1",
        "runtime_versions": {
            "python": platform.python_version(),
            **{
                package: importlib.metadata.version(package)
                for package in (
                    "duckdb",
                    "pandas",
                    "pandas-market-calendars",
                    "pyarrow",
                    "pytz",
                )
            },
        },
    }
    _write_json_atomic(
        manifests_dir / "normalization_manifest.json",
        normalized_manifest,
    )
    return summary


def audit_sessions(
    bars: pd.DataFrame,
    research_start: date,
    research_end: date,
    split_config: dict[str, Any],
    roll_dates: set[date],
    duplicate_sessions: dict[date, dict[str, int]] | None = None,
) -> tuple[pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    """Build one quality record for every expected or observed session."""
    duplicate_sessions = duplicate_sessions or {}
    schedule = cme_equity_schedule(research_start, research_end)
    in_scope = bars[
        (bars["session_date"] >= research_start)
        & (bars["session_date"] <= research_end)
    ].copy()
    groups = {
        key: group.sort_values(["timestamp_utc", "symbol"], kind="mergesort")
        for key, group in in_scope.groupby("session_date", sort=True)
    }
    all_dates = sorted(set(schedule.index) | set(groups))

    rows: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    missing_runs: list[dict[str, Any]] = []
    for session_date in all_dates:
        expected_session = session_date in schedule.index
        observed = groups.get(session_date, in_scope.iloc[0:0])
        observed_timestamps = pd.DatetimeIndex(
            observed["timestamp_utc"].drop_duplicates().sort_values()
        )
        reasons: list[str] = []
        severity = "none"
        first_missing: str | None = None
        last_missing: str | None = None
        largest_missing_run = 0

        if expected_session:
            calendar_row = schedule.loc[session_date]
            is_early_close = bool(calendar_row["is_early_close"])
            expected_index = expected_minutes(
                calendar_row["expected_open_utc"],
                calendar_row["expected_close_utc"],
            )
            observed_within = observed_timestamps[
                (observed_timestamps >= calendar_row["expected_open_utc"])
                & (observed_timestamps < calendar_row["expected_close_utc"])
            ]
            missing = expected_index.difference(observed_within)
            unexpected = observed_timestamps.difference(expected_index)
            expected_rth = expected_index[rth_mask(expected_index).to_numpy()]
            observed_rth = observed_within[rth_mask(observed_within).to_numpy()]
            missing_rth = expected_rth.difference(observed_rth)
            runs = _contiguous_runs(missing)
            if runs:
                first_missing = runs[0][0].isoformat()
                last_missing = runs[-1][1].isoformat()
                largest_missing_run = max(run[2] for run in runs)
                for run_start, run_end, count in runs:
                    run_index = pd.date_range(run_start, run_end, freq="min")
                    if session_date == research_start:
                        classification = "acquisition_boundary_partial_session"
                    elif session_date in roll_dates:
                        classification = "roll_session_missing_minutes"
                    elif is_early_close:
                        classification = "provider_gap_on_early_close_session"
                    else:
                        classification = "unexplained_provider_gap"
                    missing_runs.append(
                        {
                            "session_date": session_date.isoformat(),
                            "split": split_name(session_date, split_config),
                            "start_utc": run_start.isoformat(),
                            "end_utc": run_end.isoformat(),
                            "minutes": count,
                            "rth_overlap": bool(rth_mask(run_index).any()),
                            "classification": classification,
                        }
                    )
            if len(missing):
                reasons.append("missing_expected_minutes")
                severity = "critical" if len(observed_within) == 0 else "high"
            if len(unexpected):
                reasons.append("timestamps_outside_expected_session")
                severity = _max_severity(severity, "high")
            expected_open_utc = calendar_row["expected_open_utc"].isoformat()
            expected_close_utc = calendar_row["expected_close_utc"].isoformat()
            expected_open_et = calendar_row["expected_open_et"].isoformat()
            expected_close_et = calendar_row["expected_close_et"].isoformat()
            expected_count = int(calendar_row["expected_minutes"])
            expected_rth_count = len(expected_rth)
            calendar_override = bool(calendar_row["calendar_override"])
            calendar_note = str(calendar_row["calendar_note"])
        else:
            missing = pd.DatetimeIndex([])
            missing_rth = pd.DatetimeIndex([])
            unexpected = observed_timestamps
            reasons.append("observed_noncalendar_session")
            severity = "high"
            expected_open_utc = None
            expected_close_utc = None
            expected_open_et = None
            expected_close_et = None
            expected_count = 0
            expected_rth_count = 0
            is_early_close = False
            calendar_override = False
            calendar_note = ""

        symbols = list(dict.fromkeys(observed["symbol"].astype(str)))
        symbol_transitions = (
            int(observed["symbol"].ne(observed["symbol"].shift()).sum() - 1)
            if len(observed)
            else 0
        )
        invalid_rows = int((~observed["row_valid"]).sum()) if len(observed) else 0
        duplicate_counts = duplicate_sessions.get(session_date, {})
        exact_duplicates = int(duplicate_counts.get("exact_duplicate_rows", 0))
        conflicting_duplicates = int(
            duplicate_counts.get("conflicting_duplicate_keys", 0)
        )

        if invalid_rows:
            reasons.append("invalid_bar_values")
            severity = _max_severity(severity, "critical")
        if conflicting_duplicates:
            reasons.append("conflicting_duplicate_bars")
            severity = _max_severity(severity, "critical")
        if len(symbols) > 1 or symbol_transitions > 0:
            reasons.append("multiple_contracts_within_session")
            severity = _max_severity(severity, "high")
        if session_date in roll_dates:
            reasons.append("roll_boundary_session")
            severity = _max_severity(severity, "high")

        reasons = list(dict.fromkeys(reasons))
        excluded = bool(reasons)
        quality_status = "excluded" if excluded else (
            "corrected" if exact_duplicates else "pass"
        )
        if exact_duplicates and not excluded:
            severity = "low"

        record = {
            "session_date": session_date,
            "split": split_name(session_date, split_config),
            "expected_session": expected_session,
            "expected_open_utc": expected_open_utc,
            "expected_close_utc": expected_close_utc,
            "expected_open_et": expected_open_et,
            "expected_close_et": expected_close_et,
            "is_early_close": is_early_close,
            "calendar_override": calendar_override,
            "calendar_note": calendar_note,
            "expected_minutes": expected_count,
            "expected_rth_minutes": expected_rth_count,
            "observed_rows": int(len(observed)),
            "observed_unique_minutes": int(len(observed_timestamps)),
            "missing_minutes": int(len(missing)),
            "missing_rth_minutes": int(len(missing_rth)),
            "unexpected_minutes": int(len(unexpected)),
            "invalid_rows": invalid_rows,
            "exact_duplicate_rows_removed": exact_duplicates,
            "conflicting_duplicate_keys": conflicting_duplicates,
            "symbol_count": len(symbols),
            "symbols": "|".join(symbols),
            "contract_transitions": symbol_transitions,
            "first_missing_utc": first_missing,
            "last_missing_utc": last_missing,
            "largest_missing_run_minutes": largest_missing_run,
            "quality_status": quality_status,
            "session_usable": not excluded,
            "severity": severity,
            "exclusion_reasons": "|".join(reasons),
        }
        rows.append(record)
        if excluded:
            exclusions.append(
                {
                    "session_date": session_date.isoformat(),
                    "split": record["split"],
                    "severity": severity,
                    "reasons": reasons,
                    "observed_rows": record["observed_rows"],
                    "expected_minutes": expected_count,
                    "missing_minutes": record["missing_minutes"],
                    "missing_rth_minutes": record["missing_rth_minutes"],
                    "unexpected_minutes": record["unexpected_minutes"],
                    "symbols": symbols,
                    "action": "exclude_entire_session_from_downstream_research",
                }
            )

    quality = pd.DataFrame(rows).sort_values("session_date").reset_index(drop=True)
    return quality, exclusions, missing_runs


def audit_roll_boundaries(
    bars: pd.DataFrame,
    inventory: list[Any],
    tick_size: float,
) -> pd.DataFrame:
    """Describe the actual intraday contract switch at each selected roll date."""
    records: list[dict[str, Any]] = []
    for index, front in enumerate(inventory[:-1]):
        session_date = front.selected_roll_date
        if session_date is None:
            continue
        next_contract = inventory[index + 1]
        session = bars[bars["session_date"] == session_date].sort_values(
            ["timestamp_utc", "source_row_number"]
            if "source_row_number" in bars
            else ["timestamp_utc"],
            kind="mergesort",
        )
        transitions = session[session["symbol"].ne(session["symbol"].shift())]
        transition = transitions.iloc[1] if len(transitions) > 1 else None
        if transition is not None:
            position = session.index.get_loc(transition.name)
            previous = session.iloc[position - 1]
            gap_points = float(transition["open"] - previous["close"])
            switch_utc = transition["timestamp_utc"]
            switch_et = switch_utc.tz_convert(EASTERN)
            previous_symbol = str(previous["symbol"])
            next_symbol = str(transition["symbol"])
        else:
            gap_points = None
            switch_utc = None
            switch_et = None
            previous_symbol = None
            next_symbol = None
        records.append(
            {
                "session_date": session_date,
                "planned_front_symbol": front.provider_symbol,
                "planned_next_symbol": next_contract.provider_symbol,
                "observed_symbols": "|".join(
                    dict.fromkeys(session["symbol"].astype(str))
                ),
                "actual_previous_symbol": previous_symbol,
                "actual_next_symbol": next_symbol,
                "actual_switch_utc": switch_utc.isoformat() if switch_utc is not None else None,
                "actual_switch_et": switch_et.isoformat() if switch_et is not None else None,
                "gap_points": gap_points,
                "gap_ticks": gap_points / tick_size if gap_points is not None else None,
                "action": "exclude_roll_session",
            }
        )
    return pd.DataFrame(records)


def _deduplicate_batches(batch_dir: Path, output_path: Path) -> dict[str, Any]:
    pattern = (batch_dir / "*.parquet").as_posix().replace("'", "''")
    output = output_path.as_posix().replace("'", "''")
    connection = duckdb.connect()
    try:
        connection.execute(
            f"CREATE VIEW raw_bars AS SELECT * FROM read_parquet('{pattern}')"
        )
        duplicate_rows = connection.execute(
            """
            SELECT
                session_date,
                symbol,
                timestamp_utc,
                count(*) AS row_count,
                count(DISTINCT bar_signature) AS signature_count
            FROM raw_bars
            GROUP BY ALL
            HAVING count(*) > 1
            ORDER BY timestamp_utc, symbol
            """
        ).fetchdf()
        connection.execute(
            f"""
            COPY (
                SELECT * EXCLUDE (duplicate_rank)
                FROM (
                    SELECT
                        *,
                        row_number() OVER (
                            PARTITION BY symbol, timestamp_utc
                            ORDER BY source_request_id, source_row_number
                        ) AS duplicate_rank
                    FROM raw_bars
                )
                WHERE duplicate_rank = 1
                ORDER BY timestamp_utc, symbol
            ) TO '{output}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
    finally:
        connection.close()

    session_counts: dict[date, dict[str, int]] = defaultdict(
        lambda: {"exact_duplicate_rows": 0, "conflicting_duplicate_keys": 0}
    )
    issues: list[dict[str, Any]] = []
    for row in duplicate_rows.itertuples(index=False):
        label = pd.Timestamp(row.session_date).date()
        if int(row.signature_count) == 1:
            removed = int(row.row_count) - 1
            session_counts[label]["exact_duplicate_rows"] += removed
            issue_code = "exact_duplicate_bar"
            severity = "low"
        else:
            session_counts[label]["conflicting_duplicate_keys"] += 1
            issue_code = "conflicting_duplicate_bar"
            severity = "critical"
        issues.append(
            {
                "scope": "duplicate_key",
                "session_date": label.isoformat(),
                "symbol": row.symbol,
                "timestamp_utc": row.timestamp_utc.isoformat(),
                "issue_code": issue_code,
                "severity": severity,
                "row_count": int(row.row_count),
                "distinct_signatures": int(row.signature_count),
                "deterministic_rule": (
                    "keep lexicographically first source_request_id and source_row_number"
                ),
            }
        )
    return {
        "duplicate_keys": int(len(duplicate_rows)),
        "exact_duplicate_rows_removed": int(
            sum(
                counts["exact_duplicate_rows"]
                for counts in session_counts.values()
            )
        ),
        "conflicting_duplicate_keys": int(
            sum(
                counts["conflicting_duplicate_keys"]
                for counts in session_counts.values()
            )
        ),
        "session_counts": dict(session_counts),
        "issues": issues,
    }


def _write_final_normalized(
    base_path: Path,
    quality_path: Path,
    output_path: Path,
    research_start: date,
    research_end: date,
) -> None:
    base = base_path.as_posix().replace("'", "''")
    quality = quality_path.as_posix().replace("'", "''")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        suffix=".parquet",
        dir=output_path.parent,
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    output = temporary.as_posix().replace("'", "''")
    connection = duckdb.connect()
    try:
        connection.execute(
            f"""
            COPY (
                SELECT
                    b.* EXCLUDE (bar_signature),
                    q.session_usable,
                    q.quality_status AS session_quality_status,
                    q.exclusion_reasons
                FROM read_parquet('{base}') AS b
                LEFT JOIN read_parquet('{quality}') AS q
                    USING (session_date)
                WHERE b.session_date BETWEEN DATE '{research_start.isoformat()}'
                    AND DATE '{research_end.isoformat()}'
                ORDER BY b.timestamp_utc, b.symbol
            ) TO '{output}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
    finally:
        connection.close()
    os.replace(temporary, output_path)


def _profile_final(path: Path) -> dict[str, Any]:
    parquet = path.as_posix().replace("'", "''")
    connection = duckdb.connect()
    try:
        row = connection.execute(
            f"""
            SELECT
                count(*) AS row_count,
                count(*) FILTER (WHERE session_usable) AS usable_row_count,
                min(timestamp_utc) AS min_timestamp_utc,
                max(timestamp_utc) AS max_timestamp_utc,
                count(DISTINCT session_date) AS observed_session_count,
                count(DISTINCT session_date) FILTER (WHERE session_usable)
                    AS usable_session_count
            FROM read_parquet('{parquet}')
            """
        ).fetchone()
    finally:
        connection.close()
    minimum = pd.Timestamp(row[2]).tz_convert("UTC")
    maximum = pd.Timestamp(row[3]).tz_convert("UTC")
    return {
        "row_count": int(row[0]),
        "usable_row_count": int(row[1]),
        "min_timestamp_utc": minimum.isoformat().replace("+00:00", "Z"),
        "max_timestamp_utc": maximum.isoformat().replace("+00:00", "Z"),
        "observed_session_count": int(row[4]),
        "usable_session_count": int(row[5]),
    }


def _build_summary(
    quality: pd.DataFrame,
    exclusions: list[dict[str, Any]],
    request_audits: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    duplicate_audit: dict[str, Any],
    roll_audit: pd.DataFrame,
    final_profile: dict[str, Any],
    research_start: date,
    research_end: date,
    source_rows: int,
    outside_research_rows: int,
) -> dict[str, Any]:
    expected = quality[quality["expected_session"]]
    usable = quality[quality["session_usable"]]
    split_summary: dict[str, Any] = {}
    for split, group in quality.groupby("split", sort=False):
        split_summary[str(split)] = {
            "quality_records": int(len(group)),
            "usable_sessions": int(group["session_usable"].sum()),
            "excluded_sessions": int((~group["session_usable"]).sum()),
            "observed_rows": int(group["observed_rows"].sum()),
        }
    return {
        "phase": 3,
        "status": "complete",
        "gate_passed": bool(
            len(expected)
            and expected["session_date"].nunique() == len(expected)
            and expected["quality_status"].isin({"pass", "corrected", "excluded"}).all()
        ),
        "research_start": research_start.isoformat(),
        "research_end": research_end.isoformat(),
        "source_request_count": len(request_audits),
        "source_rows": source_rows,
        "outside_research_rows_removed": outside_research_rows,
        "normalized_rows": final_profile["row_count"],
        "usable_rows": final_profile["usable_row_count"],
        "expected_session_count": int(len(expected)),
        "quality_record_count": int(len(quality)),
        "usable_session_count": int(len(usable)),
        "excluded_session_count": len(exclusions),
        "early_close_session_count": int(expected["is_early_close"].sum()),
        "sessions_with_missing_minutes": int(expected["missing_minutes"].gt(0).sum()),
        "missing_minutes": int(expected["missing_minutes"].sum()),
        "missing_rth_minutes": int(expected["missing_rth_minutes"].sum()),
        "unexpected_minutes": int(quality["unexpected_minutes"].sum()),
        "invalid_bar_rows": int(quality["invalid_rows"].sum()),
        "exact_duplicate_rows_removed": duplicate_audit[
            "exact_duplicate_rows_removed"
        ],
        "conflicting_duplicate_keys": duplicate_audit[
            "conflicting_duplicate_keys"
        ],
        "roll_sessions_excluded": int(len(roll_audit)),
        "issue_record_count": len(issues),
        "split_summary": split_summary,
        "nq_confirmation_status": (
            "deferred_until_strong_mnq_candidates_exist_per_phase_2_scope"
        ),
        "calendar": {
            "name": "CME Globex Equity",
            "timezone": "America/New_York",
            "bar_interval": "[timestamp, timestamp + 1 minute)",
        },
    }


def _contiguous_runs(
    timestamps: pd.DatetimeIndex,
) -> list[tuple[pd.Timestamp, pd.Timestamp, int]]:
    if len(timestamps) == 0:
        return []
    ordered = timestamps.sort_values()
    breaks = ordered.to_series().diff().ne(pd.Timedelta(minutes=1))
    group_ids = breaks.cumsum()
    runs: list[tuple[pd.Timestamp, pd.Timestamp, int]] = []
    for _, group in ordered.to_series().groupby(group_ids):
        runs.append((group.iloc[0], group.iloc[-1], int(len(group))))
    return runs


def _max_severity(left: str, right: str) -> str:
    rank = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    return left if rank[left] >= rank[right] else right


def _write_csv_atomic(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        newline="",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        frame.to_csv(handle, index=False, quoting=csv.QUOTE_MINIMAL)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="Run the complete Phase 3 audit")
    run_parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config" / "research.yaml",
    )
    run_parser.add_argument(
        "--splits",
        type=Path,
        default=ROOT / "config" / "data_splits.yaml",
    )
    run_parser.add_argument("--raw-dir", type=Path, default=ROOT / "data" / "raw")
    run_parser.add_argument(
        "--manifests-dir",
        type=Path,
        default=ROOT / "data" / "manifests",
    )
    run_parser.add_argument(
        "--normalized-dir",
        type=Path,
        default=ROOT / "data" / "normalized",
    )
    args = parser.parse_args()
    if args.command not in (None, "run"):
        parser.error("Unknown command")
    result = run_phase3(
        config_path=args.config,
        split_config_path=args.splits,
        raw_dir=args.raw_dir,
        manifests_dir=args.manifests_dir,
        normalized_dir=args.normalized_dir,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
