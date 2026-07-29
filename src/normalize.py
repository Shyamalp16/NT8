"""Normalize immutable NinjaTrader responses into research-ready bar batches."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.sessions import EASTERN, assign_session_dates, rth_mask, split_name


PRICE_COLUMNS = ("open", "high", "low", "close")
BAR_COLUMNS = (
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "upVolume",
    "downVolume",
    "upTicks",
    "downTicks",
)


@dataclass
class NormalizedBatch:
    frame: pd.DataFrame
    request_audit: dict[str, Any]
    issues: list[dict[str, Any]]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_response(
    raw_path: Path,
    ledger_row: dict[str, Any],
    split_config: dict[str, Any],
    tick_size: float,
) -> NormalizedBatch:
    """Validate and normalize one immutable one-minute response."""
    issues: list[dict[str, Any]] = []
    source_raw_path = str(ledger_row.get("raw_path", raw_path.as_posix())).replace(
        "\\", "/"
    )
    actual_checksum = sha256_file(raw_path)
    expected_checksum = str(ledger_row["sha256"])
    if actual_checksum != expected_checksum:
        raise ValueError(
            f"Raw checksum mismatch for {ledger_row['request_id']}: "
            f"{actual_checksum} != {expected_checksum}"
        )

    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    payload = raw.get("structuredContent", raw)
    if "structuredContent" in payload:
        payload = payload["structuredContent"]

    for required in ("symbol", "barType", "barSize", "bars"):
        if required not in payload:
            raise ValueError(f"{raw_path} is missing response field {required}")
    if str(payload["symbol"]) != str(ledger_row["symbol"]):
        raise ValueError(f"{raw_path} symbol disagrees with the download ledger")
    if str(payload["barType"]) != "Minute" or int(payload["barSize"]) != 1:
        raise ValueError(f"{raw_path} is not a one-minute response")

    source = pd.DataFrame.from_records(payload["bars"])
    missing_columns = [column for column in BAR_COLUMNS if column not in source]
    if missing_columns:
        raise ValueError(f"{raw_path} is missing bar fields {missing_columns}")
    if len(source) != int(ledger_row["row_count"]):
        raise ValueError(f"{raw_path} row count disagrees with the download ledger")

    frame = pd.DataFrame()
    parsed_timestamps = pd.to_datetime(source["timestamp"], utc=True, errors="coerce")
    frame["timestamp_utc"] = parsed_timestamps
    timestamp_valid = parsed_timestamps.notna()
    aligned_timestamp = (
        parsed_timestamps.dt.second.fillna(-1).eq(0)
        & parsed_timestamps.dt.microsecond.fillna(-1).eq(0)
    )

    numeric_valid = pd.Series(True, index=source.index)
    for column in PRICE_COLUMNS + ("upVolume", "downVolume", "upTicks", "downTicks"):
        converted = pd.to_numeric(source[column], errors="coerce")
        frame[_normalized_name(column)] = converted
        numeric_valid &= converted.notna() & np.isfinite(converted)

    positive_price = frame[list(PRICE_COLUMNS)].gt(0).all(axis=1)
    ohlc_valid = (
        frame["high"].ge(frame[["open", "low", "close"]].max(axis=1))
        & frame["low"].le(frame[["open", "high", "close"]].min(axis=1))
    )
    volume_valid = frame[["up_volume", "down_volume"]].ge(0).all(axis=1)
    ticks_valid = frame[["up_ticks", "down_ticks"]].ge(0).all(axis=1)
    tick_aligned = pd.Series(True, index=frame.index)
    for column in PRICE_COLUMNS:
        scaled = frame[column] / tick_size
        tick_aligned &= (scaled - scaled.round()).abs().le(1e-8)

    checks = {
        "invalid_timestamp": ~timestamp_valid,
        "non_minute_timestamp": timestamp_valid & ~aligned_timestamp,
        "non_numeric_or_non_finite": ~numeric_valid,
        "non_positive_price": numeric_valid & ~positive_price,
        "invalid_ohlc_order": numeric_valid & ~ohlc_valid,
        "negative_volume": numeric_valid & ~volume_valid,
        "negative_ticks": numeric_valid & ~ticks_valid,
        "off_tick_price": numeric_valid & ~tick_aligned,
    }
    row_valid = pd.Series(True, index=frame.index)
    issue_code = pd.Series("", index=frame.index, dtype="string")
    for code, failed in checks.items():
        row_valid &= ~failed
        issue_code = issue_code.mask(
            failed,
            issue_code.where(issue_code.eq(""), issue_code + "|") + code,
        )
        count = int(failed.sum())
        if count:
            sample_indices = failed[failed].index[:3]
            issues.append(
                {
                    "scope": "bar",
                    "request_id": ledger_row["request_id"],
                    "raw_path": source_raw_path,
                    "issue_code": code,
                    "count": count,
                    "severity": "critical" if code in {
                        "invalid_timestamp",
                        "non_numeric_or_non_finite",
                        "invalid_ohlc_order",
                    } else "high",
                    "sample_source_rows": [int(value) for value in sample_indices],
                }
            )

    frame["total_volume"] = frame["up_volume"] + frame["down_volume"]
    frame["total_ticks"] = frame["up_ticks"] + frame["down_ticks"]
    frame["symbol"] = str(payload["symbol"])
    frame["contract_year"] = int(ledger_row["contract_year"])
    frame["source_request_id"] = str(ledger_row["request_id"])
    frame["source_raw_path"] = source_raw_path
    frame["source_row_number"] = source.index.astype("int32")
    frame["row_valid"] = row_valid
    frame["row_issue_codes"] = issue_code

    usable_timestamp = frame["timestamp_utc"].notna()
    frame = frame.loc[usable_timestamp].copy()
    local_timestamps = frame["timestamp_utc"].dt.tz_convert(EASTERN)
    frame["timestamp_et"] = local_timestamps.map(lambda value: value.isoformat())
    frame["session_date"] = assign_session_dates(frame["timestamp_utc"])
    frame["split"] = [
        split_name(value, split_config) for value in frame["session_date"]
    ]
    frame["is_rth"] = rth_mask(frame["timestamp_utc"]).to_numpy()
    frame["utc_offset_minutes"] = (
        local_timestamps.map(lambda value: value.utcoffset().total_seconds() / 60)
    ).astype("int16")

    audit = {
        "request_id": ledger_row["request_id"],
        "symbol": str(payload["symbol"]),
        "contract_year": int(ledger_row["contract_year"]),
        "raw_path": source_raw_path,
        "sha256_verified": True,
        "source_rows": int(len(source)),
        "normalized_rows": int(len(frame)),
        "invalid_rows": int((~row_valid).sum()),
        "min_timestamp_utc": (
            frame["timestamp_utc"].min().isoformat() if len(frame) else None
        ),
        "max_timestamp_utc": (
            frame["timestamp_utc"].max().isoformat() if len(frame) else None
        ),
    }
    return NormalizedBatch(frame=frame, request_audit=audit, issues=issues)


def exact_bar_signature(frame: pd.DataFrame) -> pd.Series:
    """Stable content signature used to distinguish exact and conflicting duplicates."""
    columns = [
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
    return pd.util.hash_pandas_object(frame[columns], index=False)


def _normalized_name(provider_name: str) -> str:
    mapping = {
        "upVolume": "up_volume",
        "downVolume": "down_volume",
        "upTicks": "up_ticks",
        "downTicks": "down_ticks",
    }
    return mapping.get(provider_name, provider_name)
