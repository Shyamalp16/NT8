"""Phase 4 point-in-time feature construction for MNQ one-minute bars.

All bar-derived values on a row are available only at ``timestamp_utc + 1
minute`` because provider timestamps denote bar opens.  The feature table is
therefore suitable for decisions at the next bar open, never the current bar
open.  The untouched final split is constructed structurally but the public
analysis loader refuses to expose it without an explicit final-evaluation
unlock.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import yaml

from src.normalize import sha256_file
from src.sessions import EASTERN, cme_equity_schedule


ROOT = Path(__file__).resolve().parents[1]
UTC = ZoneInfo("UTC")
FINAL_SPLIT = "final_untouched_test"
FEATURE_FAMILIES = {
    "calendar",
    "prior_session",
    "overnight",
    "opening",
    "level",
    "volatility",
    "regime",
}
@dataclass(frozen=True)
class FeatureSpec:
    """Machine-readable point-in-time contract for one feature."""

    name: str
    family: str
    dtype: str
    definition: str
    availability_column: str
    source_window: str
    uses_future_data: bool = False


def validate_feature_specs(specs: Iterable[FeatureSpec]) -> None:
    """Reject incomplete or explicitly forward-looking feature definitions."""
    seen: set[str] = set()
    errors: list[str] = []
    for spec in specs:
        if spec.name in seen:
            errors.append(f"duplicate feature name: {spec.name}")
        seen.add(spec.name)
        if spec.family not in FEATURE_FAMILIES:
            errors.append(f"{spec.name}: unknown family {spec.family}")
        if not spec.availability_column:
            errors.append(f"{spec.name}: availability column is required")
        if not spec.source_window:
            errors.append(f"{spec.name}: source window is required")
        if spec.uses_future_data:
            errors.append(f"{spec.name}: future data is prohibited")
    if errors:
        raise ValueError("Invalid point-in-time feature specification: " + "; ".join(errors))


def run_phase4(
    config_path: Path = ROOT / "config" / "research.yaml",
    feature_config_path: Path = ROOT / "config" / "features.yaml",
    split_config_path: Path = ROOT / "config" / "data_splits.yaml",
    normalized_path: Path = ROOT / "data" / "normalized" / "mnq_1m.parquet",
    quality_path: Path = ROOT / "data" / "manifests" / "session_quality.csv",
    normalization_manifest_path: Path = (
        ROOT / "data" / "manifests" / "normalization_manifest.json"
    ),
    output_dir: Path = ROOT / "data" / "features",
    manifests_dir: Path = ROOT / "data" / "manifests",
    report_path: Path = ROOT / "reports" / "feature_construction_report.md",
) -> dict[str, Any]:
    """Build and validate the complete Phase 4 feature artifacts."""
    config = _load_yaml(config_path)
    feature_config = _load_yaml(feature_config_path)
    split_config = _load_yaml(split_config_path)
    normalization_manifest = json.loads(
        normalization_manifest_path.read_text(encoding="utf-8")
    )
    _validate_inputs(
        config=config,
        feature_config=feature_config,
        split_config=split_config,
        normalized_path=normalized_path,
        quality_path=quality_path,
        normalization_manifest=normalization_manifest,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    quality = _load_quality(quality_path)
    specs = build_feature_catalog(feature_config)
    validate_feature_specs(specs)

    with TemporaryDirectory(prefix=".phase4_", dir=output_dir) as temporary:
        work_dir = Path(temporary)
        aggregate_path = work_dir / "session_aggregates.parquet"
        session_path = work_dir / "mnq_session_features.parquet"
        bar_path = work_dir / "mnq_1m_features.parquet"

        _aggregate_sessions(normalized_path, aggregate_path, feature_config)
        aggregates = pd.read_parquet(aggregate_path)
        session_features = _assemble_session_features(
            quality=quality,
            aggregates=aggregates,
            feature_config=feature_config,
        )
        session_features.to_parquet(
            session_path,
            index=False,
            compression="zstd",
            engine="pyarrow",
        )
        _build_bar_features(
            normalized_path=normalized_path,
            session_path=session_path,
            output_path=bar_path,
            feature_config=feature_config,
        )

        session_final = output_dir / "mnq_session_features.parquet"
        bar_final = output_dir / "mnq_1m_features.parquet"
        os.replace(session_path, session_final)
        os.replace(bar_path, bar_final)

    validation = validate_phase4_outputs(
        normalized_path=normalized_path,
        quality_path=quality_path,
        session_path=session_final,
        bar_path=bar_final,
        specs=specs,
        feature_config=feature_config,
        split_config=split_config,
    )
    reproducibility = _verify_reproducible_outputs(
        normalized_path=normalized_path,
        output_dir=output_dir,
        session_features=session_features,
        session_path=session_final,
        bar_path=bar_final,
        feature_config=feature_config,
    )
    validation["checks"]["byte_reproducible_rebuild"] = reproducibility[
        "byte_identical"
    ]
    validation["details"]["reproducibility"] = reproducibility
    validation["failed_checks"] = [
        name for name, passed in validation["checks"].items() if not passed
    ]
    validation["gate_passed"] = not validation["failed_checks"]
    if not validation["gate_passed"]:
        raise ValueError(
            "Phase 4 validation gate failed: "
            + "; ".join(validation["failed_checks"])
        )

    catalog_path = manifests_dir / "feature_catalog.json"
    _write_json_atomic(
        catalog_path,
        {
            "version": feature_config["version"],
            "bar_semantics": feature_config["bar_semantics"],
            "features": [asdict(spec) for spec in specs],
        },
    )
    source_hashes = {
        "normalized": sha256_file(normalized_path),
        "session_quality": sha256_file(quality_path),
        "research_config": sha256_file(config_path),
        "feature_config": sha256_file(feature_config_path),
        "split_config": sha256_file(split_config_path),
    }
    artifacts = {
        "session_features": _artifact_profile(session_final),
        "bar_features": _artifact_profile(bar_final),
        "feature_catalog": {
            "path": _display_path(catalog_path),
            "sha256": sha256_file(catalog_path),
            "bytes": catalog_path.stat().st_size,
        },
    }
    summary = {
        "phase": 4,
        "version": feature_config["version"],
        "gate_passed": True,
        "created_by": "python -m src.features run",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source_hashes": source_hashes,
        "source_normalization_policy": normalization_manifest["normalization_policy"],
        "artifacts": artifacts,
        "validation": validation,
        "feature_count": len(specs),
        "feature_families": sorted(FEATURE_FAMILIES),
        "analysis_policy": {
            **feature_config["analysis_policy"],
            "construction_includes_locked_final_split": True,
            "analysis_loader_default_excludes_locked_final_split": True,
        },
        "limitations": [
            "Opening-window features are null on sessions that close before the window ends.",
            "Prior-session, overnight, and lagged-regime features are null whenever required expected predecessor sessions are excluded.",
            "One-minute bars imply next-bar-open availability for bar-close-derived features.",
        ],
        "runtime_versions": {
            "python": platform.python_version(),
            **{
                package: importlib.metadata.version(package)
                for package in ("duckdb", "numpy", "pandas", "pyarrow", "pyyaml")
            },
        },
    }
    manifest_path = manifests_dir / "feature_manifest.json"
    _write_json_atomic(manifest_path, summary)
    summary["artifacts"]["feature_manifest"] = {
        "path": _display_path(manifest_path),
        "sha256": sha256_file(manifest_path),
        "bytes": manifest_path.stat().st_size,
    }
    _write_report_atomic(report_path, _render_report(summary))
    return summary


def build_feature_catalog(feature_config: dict[str, Any]) -> list[FeatureSpec]:
    """Return the complete reader-facing feature contract."""
    specs: list[FeatureSpec] = []

    def add(
        name: str,
        family: str,
        dtype: str,
        definition: str,
        availability: str,
        source_window: str,
    ) -> None:
        specs.append(
            FeatureSpec(
                name=name,
                family=family,
                dtype=dtype,
                definition=definition,
                availability_column=availability,
                source_window=source_window,
            )
        )

    calendar = {
        "calendar_year": ("int16", "Eastern trade-date year"),
        "calendar_quarter": ("int8", "Eastern trade-date quarter"),
        "calendar_month": ("int8", "Eastern trade-date month"),
        "calendar_iso_week": ("int8", "ISO week of the Eastern trade date"),
        "calendar_weekday": ("int8", "Monday=0 through Friday=4"),
        "calendar_is_month_end_session": (
            "bool",
            "Last expected CME equity session in the trade-date month",
        ),
        "session_minute": ("int16", "Zero-based minute from the session open"),
        "rth_minute": ("int16", "Minute offset from 09:30 Eastern"),
        "session_segment": (
            "string",
            "Overnight, opening, morning, afternoon, or post-RTH segment",
        ),
    }
    for name, (dtype, definition) in calendar.items():
        availability = (
            "timestamp_utc"
            if name in {"session_minute", "rth_minute", "session_segment"}
            else "calendar_available_at_utc"
        )
        add(
            name,
            "calendar",
            dtype,
            definition,
            availability,
            "known calendar and current bar timestamp",
        )

    prior = {
        "prior_session_valid": ("bool", "Immediate expected predecessor is usable"),
        "prior_full_open": ("float64", "Prior full-session open"),
        "prior_full_high": ("float64", "Prior full-session high"),
        "prior_full_low": ("float64", "Prior full-session low"),
        "prior_full_close": ("float64", "Prior full-session close"),
        "prior_full_range": ("float64", "Prior full-session high minus low"),
        "prior_full_return": ("float64", "Prior full-session close/open minus one"),
        "prior_full_volume": ("float64", "Prior full-session total volume"),
        "prior_rth_open": ("float64", "Prior RTH open"),
        "prior_rth_high": ("float64", "Prior RTH high"),
        "prior_rth_low": ("float64", "Prior RTH low"),
        "prior_rth_close": ("float64", "Prior RTH close"),
        "prior_rth_range": ("float64", "Prior RTH high minus low"),
        "prior_rth_return": ("float64", "Prior RTH close/open minus one"),
        "prior_rth_volume": ("float64", "Prior RTH total volume"),
    }
    for name, (dtype, definition) in prior.items():
        add(
            name,
            "prior_session",
            dtype,
            definition,
            "prior_session_available_at_utc",
            "immediate previous expected session only",
        )

    overnight = {
        "overnight_valid": ("bool", "Complete overnight window and usable predecessor"),
        "overnight_open": ("float64", "Current session 18:00 Eastern open"),
        "overnight_high": ("float64", "High before 09:30 Eastern"),
        "overnight_low": ("float64", "Low before 09:30 Eastern"),
        "overnight_close": ("float64", "Last close before 09:30 Eastern"),
        "overnight_range": ("float64", "Overnight high minus low"),
        "overnight_return": ("float64", "Overnight close/open minus one"),
        "overnight_volume": ("float64", "Overnight total volume"),
        "overnight_gap_from_prior_rth": (
            "float64",
            "Overnight close/prior RTH close minus one",
        ),
        "rth_open_gap_from_prior_rth": (
            "float64",
            "Current RTH open/prior RTH close minus one",
        ),
    }
    for name, (dtype, definition) in overnight.items():
        add(
            name,
            "overnight",
            dtype,
            definition,
            "overnight_available_at_utc",
            "current session open through 09:29 Eastern plus immediate predecessor",
        )

    for window in feature_config["windows"]["opening_minutes"]:
        availability = f"opening_{window}_available_at_utc"
        for suffix, definition in {
            "valid": f"Complete first {window} RTH minutes",
            "open": f"Open of the first {window} RTH minutes",
            "high": f"High of the first {window} RTH minutes",
            "low": f"Low of the first {window} RTH minutes",
            "close": f"Close of the first {window} RTH minutes",
            "range": f"High minus low over the first {window} RTH minutes",
            "return": f"Close/open minus one over the first {window} RTH minutes",
            "volume": f"Volume in the first {window} RTH minutes",
        }.items():
            add(
                f"opening_{window}_{suffix}",
                "opening",
                "bool" if suffix == "valid" else "float64",
                definition,
                availability,
                f"09:30 through {window} minutes after RTH open",
            )

    level_features = {
        "close_minus_prior_rth_high": "Close minus prior RTH high in points",
        "close_minus_prior_rth_low": "Close minus prior RTH low in points",
        "close_minus_prior_rth_close": "Close minus prior RTH close in points",
        "close_minus_overnight_high": "Close minus overnight high in points",
        "close_minus_overnight_low": "Close minus overnight low in points",
        "close_minus_opening_30_high": "Close minus 30-minute opening high in points",
        "close_minus_opening_30_low": "Close minus 30-minute opening low in points",
        "close_vs_prior_rth_range": "Distance from prior RTH close in prior-range units",
        "above_prior_rth_high": "Close is above prior RTH high",
        "below_prior_rth_low": "Close is below prior RTH low",
        "above_overnight_high": "Close is above overnight high",
        "below_overnight_low": "Close is below overnight low",
    }
    for name, definition in level_features.items():
        add(
            name,
            "level",
            "bool" if name.startswith(("above_", "below_")) else "float64",
            definition,
            "bar_available_at_utc",
            "current bar close and the named point-in-time reference level",
        )

    for window in feature_config["windows"]["return_minutes"]:
        add(
            f"bar_return_{window}",
            "volatility",
            "float64",
            f"Current close / close {window} session bars earlier minus one",
            "bar_available_at_utc",
            f"current and {window}-bar lagged closes within the same session",
        )
    for window in feature_config["windows"]["realized_volatility_minutes"]:
        add(
            f"realized_vol_{window}",
            "volatility",
            "float64",
            f"Sample standard deviation of one-minute log returns over {window} bars",
            "bar_available_at_utc",
            f"trailing {window} bars in the current session including current close",
        )
    for window in feature_config["windows"]["rolling_range_minutes"]:
        add(
            f"rolling_range_{window}",
            "volatility",
            "float64",
            f"Trailing {window}-bar maximum high minus minimum low",
            "bar_available_at_utc",
            f"trailing {window} current-session bars including the current bar",
        )
    for name, definition in {
        "session_high_so_far": "Maximum high from session open through current bar",
        "session_low_so_far": "Minimum low from session open through current bar",
        "session_range_so_far": "Session high so far minus session low so far",
        "session_cumulative_volume": "Volume from session open through current bar",
    }.items():
        add(
            name,
            "volatility",
            "float64",
            definition,
            "bar_available_at_utc",
            "current session from open through current bar",
        )

    regime_window = int(feature_config["windows"]["daily_regime_sessions"])
    short_window = int(feature_config["windows"]["short_regime_sessions"])
    regimes = {
        f"lagged_rth_range_mean_{regime_window}": (
            "float64",
            f"Mean RTH range across the prior {regime_window} expected sessions",
        ),
        f"lagged_rth_range_std_{regime_window}": (
            "float64",
            f"Sample standard deviation of RTH range across prior {regime_window} sessions",
        ),
        f"lagged_rth_volume_mean_{regime_window}": (
            "float64",
            f"Mean RTH volume across the prior {regime_window} expected sessions",
        ),
        f"lagged_close_return_{short_window}": (
            "float64",
            f"Return ending at the prior RTH close across {short_window} sessions",
        ),
        "lagged_volatility_ratio": (
            "float64",
            "Prior RTH range divided by its lagged rolling mean",
        ),
        "lagged_volume_ratio": (
            "float64",
            "Prior RTH volume divided by its lagged rolling mean",
        ),
        "trend_regime": ("string", "down, flat, or up from lagged close return"),
        "volatility_regime": (
            "string",
            "low, normal, or high from lagged volatility ratio",
        ),
        "volume_regime": (
            "string",
            "low, normal, or high from lagged volume ratio",
        ),
    }
    for name, (dtype, definition) in regimes.items():
        add(
            name,
            "regime",
            dtype,
            definition,
            "regime_available_at_utc",
            f"strictly lagged expected-session window ending at the prior close",
        )
    return specs


def load_analysis_features(
    path: Path = ROOT / "data" / "features" / "mnq_1m_features.parquet",
    splits: Sequence[str] = ("development",),
    *,
    final_evaluation_unlocked: bool = False,
    columns: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Load feature rows while guarding the untouched final-test boundary."""
    requested = tuple(dict.fromkeys(splits))
    if not requested:
        raise ValueError("At least one split is required")
    if FINAL_SPLIT in requested and not final_evaluation_unlocked:
        raise PermissionError(
            "The final_untouched_test split is locked. Feature analysis and "
            "candidate selection may not inspect it."
        )
    allowed = {"development", "validation", FINAL_SPLIT}
    unknown = sorted(set(requested) - allowed)
    if unknown:
        raise ValueError(f"Unknown split names: {unknown}")
    projection = "*" if columns is None else ", ".join(
        _quote_identifier(column) for column in columns
    )
    placeholders = ", ".join("?" for _ in requested)
    connection = duckdb.connect()
    try:
        return connection.execute(
            f"""
            SELECT {projection}
            FROM read_parquet(?)
            WHERE split IN ({placeholders})
            ORDER BY timestamp_utc
            """,
            [str(path), *requested],
        ).fetchdf()
    finally:
        connection.close()


def _aggregate_sessions(
    normalized_path: Path,
    output_path: Path,
    feature_config: dict[str, Any],
) -> None:
    opening_windows = [
        int(value) for value in feature_config["windows"]["opening_minutes"]
    ]
    opening_sql: list[str] = []
    for window in opening_windows:
        predicate = (
            "local_clock >= TIME '09:30:00' "
            f"AND local_clock < TIME '09:{30 + window:02d}:00'"
            if window <= 29
            else "local_clock >= TIME '09:30:00' "
            f"AND local_clock < TIME '{(9 * 60 + 30 + window) // 60:02d}:"
            f"{(9 * 60 + 30 + window) % 60:02d}:00'"
        )
        opening_sql.extend(
            [
                f"count(*) FILTER (WHERE {predicate}) AS opening_{window}_bars",
                f"arg_min(open, timestamp_utc) FILTER (WHERE {predicate}) "
                f"AS opening_{window}_open",
                f"max(high) FILTER (WHERE {predicate}) AS opening_{window}_high",
                f"min(low) FILTER (WHERE {predicate}) AS opening_{window}_low",
                f"arg_max(close, timestamp_utc) FILTER (WHERE {predicate}) "
                f"AS opening_{window}_close",
                f"sum(total_volume) FILTER (WHERE {predicate}) "
                f"AS opening_{window}_volume",
            ]
        )
    query = f"""
        COPY (
            WITH usable AS (
                SELECT
                    *,
                    CAST(
                        timezone('America/New_York', timestamp_utc) AS TIME
                    ) AS local_clock
                FROM read_parquet({_sql_literal(normalized_path)})
                WHERE session_usable = TRUE
                  AND row_valid = TRUE
            )
            SELECT
                session_date,
                count(*) AS full_bars,
                arg_min(open, timestamp_utc) AS full_open,
                max(high) AS full_high,
                min(low) AS full_low,
                arg_max(close, timestamp_utc) AS full_close,
                sum(total_volume) AS full_volume,
                sum(total_ticks) AS full_ticks,
                count(*) FILTER (
                    WHERE local_clock < TIME '09:30:00'
                    OR local_clock >= TIME '18:00:00'
                ) AS overnight_bars,
                arg_min(open, timestamp_utc) FILTER (
                    WHERE local_clock < TIME '09:30:00'
                    OR local_clock >= TIME '18:00:00'
                ) AS overnight_open,
                max(high) FILTER (
                    WHERE local_clock < TIME '09:30:00'
                    OR local_clock >= TIME '18:00:00'
                ) AS overnight_high,
                min(low) FILTER (
                    WHERE local_clock < TIME '09:30:00'
                    OR local_clock >= TIME '18:00:00'
                ) AS overnight_low,
                arg_max(close, timestamp_utc) FILTER (
                    WHERE local_clock < TIME '09:30:00'
                    OR local_clock >= TIME '18:00:00'
                ) AS overnight_close,
                sum(total_volume) FILTER (
                    WHERE local_clock < TIME '09:30:00'
                    OR local_clock >= TIME '18:00:00'
                ) AS overnight_volume,
                count(*) FILTER (WHERE is_rth) AS rth_bars,
                arg_min(open, timestamp_utc) FILTER (WHERE is_rth) AS rth_open,
                max(high) FILTER (WHERE is_rth) AS rth_high,
                min(low) FILTER (WHERE is_rth) AS rth_low,
                arg_max(close, timestamp_utc) FILTER (WHERE is_rth) AS rth_close,
                sum(total_volume) FILTER (WHERE is_rth) AS rth_volume,
                {", ".join(opening_sql)}
            FROM usable
            GROUP BY session_date
            ORDER BY session_date
        ) TO {_sql_literal(output_path)}
        (FORMAT PARQUET, COMPRESSION ZSTD)
    """
    connection = duckdb.connect()
    try:
        connection.execute(query)
    finally:
        connection.close()


def _assemble_session_features(
    quality: pd.DataFrame,
    aggregates: pd.DataFrame,
    feature_config: dict[str, Any],
) -> pd.DataFrame:
    """Combine session aggregates with strict expected-session lag rules."""
    frame = quality.sort_values("session_date", kind="mergesort").reset_index(drop=True)
    frame = frame.merge(aggregates, how="left", on="session_date", validate="one_to_one")
    usable = frame["session_usable"].fillna(False).astype(bool)
    frame["calendar_year"] = frame["session_date"].map(lambda value: value.year).astype(
        "int16"
    )
    frame["calendar_quarter"] = frame["session_date"].map(
        lambda value: (value.month - 1) // 3 + 1
    ).astype("int8")
    frame["calendar_month"] = frame["session_date"].map(
        lambda value: value.month
    ).astype("int8")
    frame["calendar_iso_week"] = frame["session_date"].map(
        lambda value: value.isocalendar().week
    ).astype("int8")
    frame["calendar_weekday"] = frame["session_date"].map(
        lambda value: value.weekday()
    ).astype("int8")
    calendar = cme_equity_schedule(
        frame["session_date"].min(),
        frame["session_date"].max() + timedelta(days=40),
    )
    calendar_dates = pd.Series(calendar.index, index=calendar.index)
    calendar_month_keys = pd.Series(
        [(value.year, value.month) for value in calendar_dates.index],
        index=calendar_dates.index,
    )
    last_sessions = set(
        calendar_dates.groupby(calendar_month_keys).max().to_list()
    )
    frame["calendar_is_month_end_session"] = frame["session_date"].isin(last_sessions)

    frame["calendar_available_at_utc"] = frame["expected_open_utc"]
    frame["rth_open_utc"] = frame["session_date"].map(_rth_open_utc)
    frame["prior_session_date"] = frame["session_date"].shift(1)
    frame["prior_session_available_at_utc"] = frame["expected_close_utc"].shift(1)
    prior_valid = usable.shift(1, fill_value=False) & frame["expected_session"].shift(
        1, fill_value=False
    )
    frame["prior_session_valid"] = prior_valid

    prior_map = {
        "full_open": "prior_full_open",
        "full_high": "prior_full_high",
        "full_low": "prior_full_low",
        "full_close": "prior_full_close",
        "full_volume": "prior_full_volume",
        "rth_open": "prior_rth_open",
        "rth_high": "prior_rth_high",
        "rth_low": "prior_rth_low",
        "rth_close": "prior_rth_close",
        "rth_volume": "prior_rth_volume",
    }
    for source, target in prior_map.items():
        frame[target] = frame[source].shift(1).where(prior_valid)
    frame["prior_full_range"] = (
        frame["prior_full_high"] - frame["prior_full_low"]
    )
    frame["prior_full_return"] = _safe_ratio(
        frame["prior_full_close"], frame["prior_full_open"]
    )
    frame["prior_rth_range"] = frame["prior_rth_high"] - frame["prior_rth_low"]
    frame["prior_rth_return"] = _safe_ratio(
        frame["prior_rth_close"], frame["prior_rth_open"]
    )

    expected_overnight = (
        (
            frame["rth_open_utc"]
            .where(frame["expected_close_utc"] >= frame["rth_open_utc"], frame["expected_close_utc"])
            - frame["expected_open_utc"]
        ).dt.total_seconds()
        // 60
    )
    overnight_valid = (
        usable
        & prior_valid
        & frame["overnight_bars"].eq(expected_overnight)
        & frame["overnight_open"].notna()
    )
    frame["overnight_valid"] = overnight_valid
    frame["overnight_available_at_utc"] = frame["rth_open_utc"].where(
        frame["expected_close_utc"] >= frame["rth_open_utc"],
        frame["expected_close_utc"],
    )
    for column in (
        "overnight_open",
        "overnight_high",
        "overnight_low",
        "overnight_close",
        "overnight_volume",
    ):
        frame[column] = frame[column].where(overnight_valid)
    frame["overnight_range"] = frame["overnight_high"] - frame["overnight_low"]
    frame["overnight_return"] = _safe_ratio(
        frame["overnight_close"], frame["overnight_open"]
    )
    frame["overnight_gap_from_prior_rth"] = _safe_ratio(
        frame["overnight_close"], frame["prior_rth_close"]
    ).where(overnight_valid)
    frame["rth_open_gap_from_prior_rth"] = _safe_ratio(
        frame["rth_open"], frame["prior_rth_close"]
    ).where(overnight_valid & frame["rth_open"].notna())

    for window_value in feature_config["windows"]["opening_minutes"]:
        window = int(window_value)
        available = frame["rth_open_utc"] + pd.to_timedelta(window, unit="m")
        frame[f"opening_{window}_available_at_utc"] = available
        expected_window = frame["expected_close_utc"].ge(available)
        valid = (
            usable
            & expected_window
            & frame[f"opening_{window}_bars"].eq(window)
            & frame[f"opening_{window}_open"].notna()
        )
        frame[f"opening_{window}_valid"] = valid
        for suffix in ("open", "high", "low", "close", "volume"):
            column = f"opening_{window}_{suffix}"
            frame[column] = frame[column].where(valid)
        frame[f"opening_{window}_range"] = (
            frame[f"opening_{window}_high"] - frame[f"opening_{window}_low"]
        )
        frame[f"opening_{window}_return"] = _safe_ratio(
            frame[f"opening_{window}_close"],
            frame[f"opening_{window}_open"],
        )

    long_window = int(feature_config["windows"]["daily_regime_sessions"])
    short_window = int(feature_config["windows"]["short_regime_sessions"])
    lagged_range = frame["rth_high"].sub(frame["rth_low"]).where(usable).shift(1)
    lagged_volume = frame["rth_volume"].where(usable).shift(1)
    frame[f"lagged_rth_range_mean_{long_window}"] = lagged_range.rolling(
        long_window, min_periods=long_window
    ).mean()
    frame[f"lagged_rth_range_std_{long_window}"] = lagged_range.rolling(
        long_window, min_periods=long_window
    ).std()
    frame[f"lagged_rth_volume_mean_{long_window}"] = lagged_volume.rolling(
        long_window, min_periods=long_window
    ).mean()
    lagged_close = frame["rth_close"].where(usable).shift(1)
    base_close = frame["rth_close"].where(usable).shift(short_window + 1)
    strict_short = (
        usable.shift(1)
        .rolling(short_window + 1, min_periods=short_window + 1)
        .sum()
        .eq(short_window + 1)
    )
    frame[f"lagged_close_return_{short_window}"] = _safe_ratio(
        lagged_close, base_close
    ).where(strict_short)
    frame["lagged_volatility_ratio"] = (
        frame["prior_rth_range"]
        / frame[f"lagged_rth_range_mean_{long_window}"].replace(0, np.nan)
    )
    frame["lagged_volume_ratio"] = (
        frame["prior_rth_volume"]
        / frame[f"lagged_rth_volume_mean_{long_window}"].replace(0, np.nan)
    )
    trend_threshold = float(feature_config["regime_thresholds"]["trend_return"])
    trend_return = frame[f"lagged_close_return_{short_window}"]
    frame["trend_regime"] = np.select(
        [trend_return <= -trend_threshold, trend_return >= trend_threshold],
        ["down", "up"],
        default="flat",
    )
    frame.loc[trend_return.isna(), "trend_regime"] = None
    _classify_ratio_regime(
        frame,
        source="lagged_volatility_ratio",
        target="volatility_regime",
        low=float(feature_config["regime_thresholds"]["volatility_ratio_low"]),
        high=float(feature_config["regime_thresholds"]["volatility_ratio_high"]),
    )
    _classify_ratio_regime(
        frame,
        source="lagged_volume_ratio",
        target="volume_regime",
        low=float(feature_config["regime_thresholds"]["volume_ratio_low"]),
        high=float(feature_config["regime_thresholds"]["volume_ratio_high"]),
    )
    regime_columns = [
        f"lagged_rth_range_mean_{long_window}",
        f"lagged_rth_range_std_{long_window}",
        f"lagged_rth_volume_mean_{long_window}",
        f"lagged_close_return_{short_window}",
        "lagged_volatility_ratio",
        "lagged_volume_ratio",
        "trend_regime",
        "volatility_regime",
        "volume_regime",
    ]
    regime_valid = (
        usable
        & prior_valid
        & frame[f"lagged_rth_range_mean_{long_window}"].notna()
        & frame[f"lagged_rth_volume_mean_{long_window}"].notna()
        & frame[f"lagged_close_return_{short_window}"].notna()
    )
    frame["regime_available_at_utc"] = frame[
        "prior_session_available_at_utc"
    ].where(regime_valid)
    frame.loc[~regime_valid, regime_columns] = np.nan

    keep = [
        "session_date",
        "split",
        "session_usable",
        "is_early_close",
        "expected_open_utc",
        "expected_close_utc",
        "rth_open_utc",
        "calendar_available_at_utc",
        "calendar_year",
        "calendar_quarter",
        "calendar_month",
        "calendar_iso_week",
        "calendar_weekday",
        "calendar_is_month_end_session",
        "prior_session_date",
        "prior_session_available_at_utc",
        *prior_map.values(),
        "prior_full_range",
        "prior_full_return",
        "prior_rth_range",
        "prior_rth_return",
        "prior_session_valid",
        "overnight_available_at_utc",
        "overnight_valid",
        "overnight_open",
        "overnight_high",
        "overnight_low",
        "overnight_close",
        "overnight_range",
        "overnight_return",
        "overnight_volume",
        "overnight_gap_from_prior_rth",
        "rth_open_gap_from_prior_rth",
    ]
    for window_value in feature_config["windows"]["opening_minutes"]:
        window = int(window_value)
        keep.extend(
            [
                f"opening_{window}_available_at_utc",
                f"opening_{window}_valid",
                f"opening_{window}_open",
                f"opening_{window}_high",
                f"opening_{window}_low",
                f"opening_{window}_close",
                f"opening_{window}_range",
                f"opening_{window}_return",
                f"opening_{window}_volume",
            ]
        )
    keep.extend(["regime_available_at_utc", *regime_columns])
    output = frame.loc[usable, keep].copy()
    output["final_test_locked"] = output["split"].eq(FINAL_SPLIT)
    return output.reset_index(drop=True)


def _build_bar_features(
    normalized_path: Path,
    session_path: Path,
    output_path: Path,
    feature_config: dict[str, Any],
) -> None:
    returns = [int(value) for value in feature_config["windows"]["return_minutes"]]
    vol_windows = [
        int(value)
        for value in feature_config["windows"]["realized_volatility_minutes"]
    ]
    range_windows = [
        int(value) for value in feature_config["windows"]["rolling_range_minutes"]
    ]
    opening_windows = [
        int(value) for value in feature_config["windows"]["opening_minutes"]
    ]
    lag_exprs = [
        f"close / nullif(lag(close, {window}) OVER session_window, 0) - 1 "
        f"AS bar_return_{window}"
        for window in returns
    ]
    vol_exprs = [
        f"CASE WHEN count(log_return_1) OVER (PARTITION BY session_date "
        f"ORDER BY timestamp_utc ROWS BETWEEN {window - 1} PRECEDING AND CURRENT ROW) "
        f"= {window} THEN stddev_samp(log_return_1) OVER (PARTITION BY session_date "
        f"ORDER BY timestamp_utc ROWS BETWEEN {window - 1} PRECEDING AND CURRENT ROW) "
        f"END AS realized_vol_{window}"
        for window in vol_windows
    ]
    range_exprs = [
        f"CASE WHEN count(*) OVER (PARTITION BY session_date ORDER BY timestamp_utc "
        f"ROWS BETWEEN {window - 1} PRECEDING AND CURRENT ROW) = {window} "
        f"THEN max(high) OVER (PARTITION BY session_date ORDER BY timestamp_utc "
        f"ROWS BETWEEN {window - 1} PRECEDING AND CURRENT ROW) - "
        f"min(low) OVER (PARTITION BY session_date ORDER BY timestamp_utc "
        f"ROWS BETWEEN {window - 1} PRECEDING AND CURRENT ROW) "
        f"END AS rolling_range_{window}"
        for window in range_windows
    ]
    opening_selects: list[str] = []
    for window in opening_windows:
        availability = f"opening_{window}_available_at_utc"
        opening_selects.append(f"b.{availability}")
        opening_selects.append(
            f"CASE WHEN b.bar_available_at_utc >= b.{availability} "
            f"THEN b.opening_{window}_valid END AS opening_{window}_valid"
        )
        for suffix in ("open", "high", "low", "close", "range", "return", "volume"):
            name = f"opening_{window}_{suffix}"
            opening_selects.append(
                f"CASE WHEN b.bar_available_at_utc >= b.{availability} "
                f"THEN b.{name} ELSE NULL END AS {name}"
            )
    long_window = int(feature_config["windows"]["daily_regime_sessions"])
    short_window = int(feature_config["windows"]["short_regime_sessions"])
    query = f"""
        COPY (
            WITH base AS (
                SELECT
                    timestamp_utc,
                    timestamp_utc + INTERVAL 1 MINUTE AS bar_available_at_utc,
                    timestamp_et,
                    session_date,
                    n.split,
                    n.split = '{FINAL_SPLIT}' AS final_test_locked,
                    n.symbol,
                    n.open,
                    n.high,
                    n.low,
                    n.close,
                    n.up_volume,
                    n.down_volume,
                    n.total_volume,
                    n.up_ticks,
                    n.down_ticks,
                    n.total_ticks,
                    n.is_rth,
                    date_diff(
                        'minute',
                        min(timestamp_utc) OVER (PARTITION BY session_date),
                        timestamp_utc
                    )::SMALLINT AS session_minute,
                    date_diff(
                        'minute',
                        s.rth_open_utc,
                        timestamp_utc
                    )::SMALLINT AS rth_minute,
                    ln(close / nullif(lag(close) OVER session_window, 0))
                        AS log_return_1,
                    {", ".join(lag_exprs)},
                    max(high) OVER cumulative_session AS session_high_so_far,
                    min(low) OVER cumulative_session AS session_low_so_far,
                    sum(total_volume) OVER cumulative_session
                        AS session_cumulative_volume,
                    s.*
                FROM read_parquet({_sql_literal(normalized_path)}) n
                INNER JOIN read_parquet({_sql_literal(session_path)}) s
                    USING (session_date)
                WHERE n.session_usable = TRUE
                  AND n.row_valid = TRUE
                WINDOW
                    session_window AS (
                        PARTITION BY session_date ORDER BY timestamp_utc
                    ),
                    cumulative_session AS (
                        PARTITION BY session_date ORDER BY timestamp_utc
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    )
            ),
            dynamics AS (
                SELECT
                    *,
                    session_high_so_far - session_low_so_far
                        AS session_range_so_far,
                    {", ".join(vol_exprs)},
                    {", ".join(range_exprs)}
                FROM base
            )
            SELECT
                b.timestamp_utc,
                b.bar_available_at_utc,
                b.timestamp_et,
                b.session_date,
                b.split,
                b.final_test_locked,
                b.symbol,
                b.open,
                b.high,
                b.low,
                b.close,
                b.up_volume,
                b.down_volume,
                b.total_volume,
                b.up_ticks,
                b.down_ticks,
                b.total_ticks,
                b.is_rth,
                b.calendar_available_at_utc,
                b.calendar_year,
                b.calendar_quarter,
                b.calendar_month,
                b.calendar_iso_week,
                b.calendar_weekday,
                b.calendar_is_month_end_session,
                b.session_minute,
                b.rth_minute,
                CASE
                    WHEN b.rth_minute < 0 THEN 'overnight'
                    WHEN b.rth_minute < 30 THEN 'rth_opening'
                    WHEN b.rth_minute < 150 THEN 'rth_morning'
                    WHEN b.rth_minute < 390 THEN 'rth_afternoon'
                    ELSE 'post_rth'
                END AS session_segment,
                b.prior_session_date,
                b.prior_session_available_at_utc,
                b.prior_session_valid,
                b.prior_full_open,
                b.prior_full_high,
                b.prior_full_low,
                b.prior_full_close,
                b.prior_full_range,
                b.prior_full_return,
                b.prior_full_volume,
                b.prior_rth_open,
                b.prior_rth_high,
                b.prior_rth_low,
                b.prior_rth_close,
                b.prior_rth_range,
                b.prior_rth_return,
                b.prior_rth_volume,
                b.overnight_available_at_utc,
                CASE WHEN b.bar_available_at_utc >= b.overnight_available_at_utc
                    THEN b.overnight_valid END AS overnight_valid,
                CASE WHEN b.bar_available_at_utc >= b.overnight_available_at_utc
                    THEN b.overnight_open END AS overnight_open,
                CASE WHEN b.bar_available_at_utc >= b.overnight_available_at_utc
                    THEN b.overnight_high END AS overnight_high,
                CASE WHEN b.bar_available_at_utc >= b.overnight_available_at_utc
                    THEN b.overnight_low END AS overnight_low,
                CASE WHEN b.bar_available_at_utc >= b.overnight_available_at_utc
                    THEN b.overnight_close END AS overnight_close,
                CASE WHEN b.bar_available_at_utc >= b.overnight_available_at_utc
                    THEN b.overnight_range END AS overnight_range,
                CASE WHEN b.bar_available_at_utc >= b.overnight_available_at_utc
                    THEN b.overnight_return END AS overnight_return,
                CASE WHEN b.bar_available_at_utc >= b.overnight_available_at_utc
                    THEN b.overnight_volume END AS overnight_volume,
                CASE WHEN b.bar_available_at_utc >= b.overnight_available_at_utc
                    THEN b.overnight_gap_from_prior_rth END
                    AS overnight_gap_from_prior_rth,
                CASE WHEN b.bar_available_at_utc >= b.overnight_available_at_utc
                    THEN b.rth_open_gap_from_prior_rth END
                    AS rth_open_gap_from_prior_rth,
                {", ".join(opening_selects)},
                b.bar_return_1,
                {", ".join(f"b.bar_return_{window}" for window in returns if window != 1)},
                {", ".join(f"b.realized_vol_{window}" for window in vol_windows)},
                {", ".join(f"b.rolling_range_{window}" for window in range_windows)},
                b.session_high_so_far,
                b.session_low_so_far,
                b.session_range_so_far,
                b.session_cumulative_volume,
                b.close - b.prior_rth_high AS close_minus_prior_rth_high,
                b.close - b.prior_rth_low AS close_minus_prior_rth_low,
                b.close - b.prior_rth_close AS close_minus_prior_rth_close,
                CASE WHEN b.bar_available_at_utc >= b.overnight_available_at_utc
                    THEN b.close - b.overnight_high END
                    AS close_minus_overnight_high,
                CASE WHEN b.bar_available_at_utc >= b.overnight_available_at_utc
                    THEN b.close - b.overnight_low END
                    AS close_minus_overnight_low,
                CASE WHEN b.bar_available_at_utc >= b.opening_30_available_at_utc
                    THEN b.close - b.opening_30_high END
                    AS close_minus_opening_30_high,
                CASE WHEN b.bar_available_at_utc >= b.opening_30_available_at_utc
                    THEN b.close - b.opening_30_low END
                    AS close_minus_opening_30_low,
                (b.close - b.prior_rth_close) / nullif(b.prior_rth_range, 0)
                    AS close_vs_prior_rth_range,
                CASE WHEN b.prior_session_valid
                    THEN b.close > b.prior_rth_high END AS above_prior_rth_high,
                CASE WHEN b.prior_session_valid
                    THEN b.close < b.prior_rth_low END AS below_prior_rth_low,
                CASE WHEN b.bar_available_at_utc >= b.overnight_available_at_utc
                    AND b.overnight_valid
                    THEN b.close > b.overnight_high END AS above_overnight_high,
                CASE WHEN b.bar_available_at_utc >= b.overnight_available_at_utc
                    AND b.overnight_valid
                    THEN b.close < b.overnight_low END AS below_overnight_low,
                b.regime_available_at_utc,
                b.lagged_rth_range_mean_{long_window},
                b.lagged_rth_range_std_{long_window},
                b.lagged_rth_volume_mean_{long_window},
                b.lagged_close_return_{short_window},
                b.lagged_volatility_ratio,
                b.lagged_volume_ratio,
                b.trend_regime,
                b.volatility_regime,
                b.volume_regime
            FROM dynamics b
            ORDER BY b.timestamp_utc
        ) TO {_sql_literal(output_path)}
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 122880)
    """
    connection = duckdb.connect()
    try:
        connection.execute("SET preserve_insertion_order = true")
        connection.execute(query)
    finally:
        connection.close()


def validate_phase4_outputs(
    normalized_path: Path,
    quality_path: Path,
    session_path: Path,
    bar_path: Path,
    specs: Sequence[FeatureSpec],
    feature_config: dict[str, Any],
    split_config: dict[str, Any],
) -> dict[str, Any]:
    """Run the Phase 4 gate checks without analyzing final-test outcomes."""
    validate_feature_specs(specs)
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}
    connection = duckdb.connect()
    try:
        source = _sql_literal(normalized_path)
        bars = _sql_literal(bar_path)
        sessions = _sql_literal(session_path)
        source_rows = connection.execute(
            f"SELECT count(*) FROM read_parquet({source}) "
            "WHERE session_usable = TRUE AND row_valid = TRUE"
        ).fetchone()[0]
        output_rows, output_sessions, distinct_timestamps = connection.execute(
            f"""
            SELECT count(*), count(DISTINCT session_date),
                   count(DISTINCT timestamp_utc)
            FROM read_parquet({bars})
            """
        ).fetchone()
        details.update(
            {
                "source_usable_rows": int(source_rows),
                "bar_feature_rows": int(output_rows),
                "bar_feature_sessions": int(output_sessions),
            }
        )
        checks["usable_row_count_preserved"] = source_rows == output_rows
        checks["bar_timestamps_unique"] = distinct_timestamps == output_rows

        quality = _load_quality(quality_path)
        expected_usable_sessions = int(quality["session_usable"].sum())
        session_rows = connection.execute(
            f"SELECT count(*) FROM read_parquet({sessions})"
        ).fetchone()[0]
        checks["usable_session_count_preserved"] = (
            session_rows == expected_usable_sessions == output_sessions
        )
        details["expected_usable_sessions"] = expected_usable_sessions

        bad_split_rows = connection.execute(
            f"""
            SELECT count(*)
            FROM read_parquet({bars})
            WHERE final_test_locked != (split = '{FINAL_SPLIT}')
               OR split NOT IN ('development', 'validation', '{FINAL_SPLIT}')
            """
        ).fetchone()[0]
        checks["frozen_split_labels_preserved"] = bad_split_rows == 0
        details["split_row_counts"] = {
            row[0]: int(row[1])
            for row in connection.execute(
                f"SELECT split, count(*) FROM read_parquet({bars}) "
                "GROUP BY split ORDER BY split"
            ).fetchall()
        }

        predecessor_violations = connection.execute(
            f"""
            SELECT count(*)
            FROM read_parquet({sessions})
            WHERE NOT prior_session_valid
              AND (
                  prior_full_high IS NOT NULL
                  OR prior_rth_high IS NOT NULL
                  OR overnight_valid
                  OR overnight_high IS NOT NULL
                  OR regime_available_at_utc IS NOT NULL
              )
            """
        ).fetchone()[0]
        checks["excluded_predecessors_invalidate_features"] = (
            predecessor_violations == 0
        )
        details["predecessor_violations"] = int(predecessor_violations)

        features_by_availability: dict[str, list[str]] = {}
        for spec in specs:
            features_by_availability.setdefault(
                spec.availability_column, []
            ).append(spec.name)
        availability_checks = {
            availability: (
                "("
                + " OR ".join(
                    f"{_quote_identifier(name)} IS NOT NULL" for name in names
                )
                + f") AND {_quote_identifier(availability)} > bar_available_at_utc"
            )
            for availability, names in features_by_availability.items()
        }
        availability_violations: dict[str, int] = {}
        for family, predicate in availability_checks.items():
            count = connection.execute(
                f"SELECT count(*) FROM read_parquet({bars}) WHERE {predicate}"
            ).fetchone()[0]
            availability_violations[family] = int(count)
        checks["point_in_time_availability"] = not any(
            availability_violations.values()
        )
        details["availability_violations"] = availability_violations

        bar_columns = set(pq.ParquetFile(bar_path).schema_arrow.names)
        missing_catalog_columns = sorted(
            spec.name for spec in specs if spec.name not in bar_columns
        )
        missing_availability_columns = sorted(
            {
                spec.availability_column
                for spec in specs
                if spec.availability_column not in bar_columns
            }
        )
        checks["catalog_matches_bar_schema"] = not (
            missing_catalog_columns or missing_availability_columns
        )
        details["missing_catalog_columns"] = missing_catalog_columns
        details["missing_availability_columns"] = missing_availability_columns

        bar_delay_violations = connection.execute(
            f"""
            SELECT count(*)
            FROM read_parquet({bars})
            WHERE bar_available_at_utc != timestamp_utc + INTERVAL 1 MINUTE
            """
        ).fetchone()[0]
        checks["bar_close_features_delayed_one_minute"] = bar_delay_violations == 0

        spot_checks = connection.execute(
            f"""
            WITH source AS (
                SELECT
                    timestamp_utc,
                    session_date,
                    close,
                    lag(close, 5) OVER (
                        PARTITION BY session_date ORDER BY timestamp_utc
                    ) AS close_lag_5,
                    max(high) OVER (
                        PARTITION BY session_date ORDER BY timestamp_utc
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ) AS high_so_far,
                    min(low) OVER (
                        PARTITION BY session_date ORDER BY timestamp_utc
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ) AS low_so_far
                FROM read_parquet({source})
                WHERE session_usable = TRUE AND row_valid = TRUE
            )
            SELECT count(*)
            FROM source s
            JOIN read_parquet({bars}) f
              USING (timestamp_utc, session_date)
            WHERE (
                s.close_lag_5 IS NULL AND f.bar_return_5 IS NOT NULL
            ) OR (
                s.close_lag_5 IS NOT NULL
                AND abs(f.bar_return_5 - (s.close / s.close_lag_5 - 1)) > 1e-12
            ) OR abs(f.session_range_so_far - (s.high_so_far - s.low_so_far)) > 1e-12
            """
        ).fetchone()[0]
        checks["calculation_spot_checks"] = spot_checks == 0
        details["calculation_spot_check_violations"] = int(spot_checks)

        min_date, max_date = connection.execute(
            f"SELECT min(session_date), max(session_date) FROM read_parquet({bars})"
        ).fetchone()
        research = split_config["research_period"]
        checks["research_boundaries_preserved"] = (
            min_date >= date.fromisoformat(str(research["start_date"]))
            and max_date <= date.fromisoformat(str(research["end_date"]))
        )
        details["feature_min_session_date"] = min_date.isoformat()
        details["feature_max_session_date"] = max_date.isoformat()
    finally:
        connection.close()

    # This guard is itself a deliberate negative test: an injected
    # forward-looking definition must be rejected.
    injected = FeatureSpec(
        name="deliberate_future_close",
        family="volatility",
        dtype="float64",
        definition="Tomorrow's close",
        availability_column="bar_available_at_utc",
        source_window="one future session",
        uses_future_data=True,
    )
    try:
        validate_feature_specs([*specs, injected])
    except ValueError:
        checks["deliberate_leakage_injection_rejected"] = True
    else:
        checks["deliberate_leakage_injection_rejected"] = False

    try:
        load_analysis_features(
            bar_path,
            splits=(FINAL_SPLIT,),
            columns=("timestamp_utc",),
        )
    except PermissionError:
        checks["locked_final_split_analysis_rejected"] = True
    else:
        checks["locked_final_split_analysis_rejected"] = False

    failed = [name for name, passed in checks.items() if not passed]
    return {
        "gate_passed": not failed,
        "checks": checks,
        "failed_checks": failed,
        "details": details,
    }


def _validate_inputs(
    config: dict[str, Any],
    feature_config: dict[str, Any],
    split_config: dict[str, Any],
    normalized_path: Path,
    quality_path: Path,
    normalization_manifest: dict[str, Any],
) -> None:
    if not config["project"]["mcp_read_only"]:
        raise ValueError("Project must remain MCP read-only")
    if not normalized_path.exists() or not quality_path.exists():
        raise FileNotFoundError("Phase 3 normalized data and quality manifest are required")
    actual_hash = sha256_file(normalized_path)
    if actual_hash != normalization_manifest["sha256"]:
        raise ValueError("Normalized input checksum disagrees with Phase 3 manifest")
    if split_config["status"] != "finalized_phase_3":
        raise ValueError("Chronological split configuration is not finalized")
    final = split_config["splits"][FINAL_SPLIT]
    if not final.get("locked") or not final.get("structurally_audited_only"):
        raise ValueError("Final-test split must remain locked and structurally audited only")
    if feature_config["analysis_policy"]["forbidden_split"] != FINAL_SPLIT:
        raise ValueError("Feature analysis policy must forbid the locked final split")
    if feature_config["bar_semantics"]["timestamp_meaning"] != "bar_open":
        raise ValueError("Only explicit bar-open timestamp semantics are supported")


def _load_quality(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["session_date"] = pd.to_datetime(
        frame["session_date"], errors="raise"
    ).dt.date
    for column in ("expected_open_utc", "expected_close_utc"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    for column in ("session_usable", "expected_session", "is_early_close"):
        if frame[column].dtype != bool:
            frame[column] = frame[column].map(
                {"True": True, "False": False, True: True, False: False}
            ).fillna(False)
    return frame


def _rth_open_utc(session_date: date) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(session_date, time(9, 30), EASTERN)).tz_convert(
        UTC
    )


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    result = numerator / denominator.replace(0, np.nan) - 1
    return result.where(np.isfinite(result))


def _classify_ratio_regime(
    frame: pd.DataFrame,
    source: str,
    target: str,
    low: float,
    high: float,
) -> None:
    values = frame[source]
    frame[target] = np.select(
        [values <= low, values >= high],
        ["low", "high"],
        default="normal",
    )
    frame.loc[values.isna(), target] = None


def _artifact_profile(path: Path) -> dict[str, Any]:
    parquet = pq.ParquetFile(path)
    return {
        "path": _display_path(path),
        "format": "parquet",
        "compression": "zstd",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "row_count": parquet.metadata.num_rows,
        "column_count": len(parquet.schema_arrow.names),
        "schema": str(parquet.schema_arrow),
    }


def _verify_reproducible_outputs(
    normalized_path: Path,
    output_dir: Path,
    session_features: pd.DataFrame,
    session_path: Path,
    bar_path: Path,
    feature_config: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild both feature tables and compare their exact file bytes."""
    with TemporaryDirectory(prefix=".phase4_rebuild_", dir=output_dir) as temporary:
        work_dir = Path(temporary)
        session_rebuild = work_dir / "mnq_session_features.parquet"
        bar_rebuild = work_dir / "mnq_1m_features.parquet"
        session_features.to_parquet(
            session_rebuild,
            index=False,
            compression="zstd",
            engine="pyarrow",
        )
        _build_bar_features(
            normalized_path=normalized_path,
            session_path=session_rebuild,
            output_path=bar_rebuild,
            feature_config=feature_config,
        )
        original_session_hash = sha256_file(session_path)
        rebuilt_session_hash = sha256_file(session_rebuild)
        original_bar_hash = sha256_file(bar_path)
        rebuilt_bar_hash = sha256_file(bar_rebuild)
    return {
        "byte_identical": (
            original_session_hash == rebuilt_session_hash
            and original_bar_hash == rebuilt_bar_hash
        ),
        "session_original_sha256": original_session_hash,
        "session_rebuilt_sha256": rebuilt_session_hash,
        "bar_original_sha256": original_bar_hash,
        "bar_rebuilt_sha256": rebuilt_bar_hash,
    }


def _render_report(summary: dict[str, Any]) -> str:
    validation = summary["validation"]
    split_rows = validation["details"]["split_row_counts"]
    return f"""# Phase 4 Feature Construction Report

## Outcome

The Phase 4 gate **passed**. The pipeline built point-in-time session and
one-minute MNQ features from the immutable Phase 3 normalized dataset. It
retained {validation["details"]["bar_feature_rows"]:,} usable bars across
{validation["details"]["bar_feature_sessions"]:,} usable sessions.

No strategy, candidate, or performance analysis was run. The locked final-test
split was constructed only so the frozen feature rules can later be applied
without reinterpretation; the analysis loader rejects that split unless an
explicit final-evaluation unlock is supplied.

## Feature contract

- Families: {", ".join(summary["feature_families"])}
- Catalogued features: {summary["feature_count"]}
- Bar timestamps denote opens; bar-derived features become available one minute
  later and are suitable only for next-bar-open decisions.
- Prior-session and overnight values use the immediate expected predecessor.
  They do not skip over excluded sessions.
- Rolling regimes are strictly lagged and require complete expected-session
  windows.

## Output coverage

- Development rows: {split_rows.get("development", 0):,}
- Validation rows: {split_rows.get("validation", 0):,}
- Locked final-test rows: {split_rows.get(FINAL_SPLIT, 0):,}

## Gate evidence

{chr(10).join(f"- {'PASS' if passed else 'FAIL'}: `{name}`" for name, passed in validation["checks"].items())}

## Reproducible artifacts

- `data/features/mnq_session_features.parquet`
- `data/features/mnq_1m_features.parquet`
- `data/manifests/feature_catalog.json`
- `data/manifests/feature_manifest.json`
- `config/features.yaml`
- `tests/test_features.py`

## Known limitations

{chr(10).join(f"- {item}" for item in summary["limitations"])}
"""


def _write_report_atomic(path: Path, value: str) -> None:
    payload = value.encode("utf-8")
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


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _sql_literal(path: Path) -> str:
    return "'" + str(path).replace("\\", "/").replace("'", "''") + "'"


def _quote_identifier(value: str) -> str:
    if not value.replace("_", "").isalnum():
        raise ValueError(f"Unsafe column name: {value}")
    return '"' + value.replace('"', '""') + '"'


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser(
        "run", help="Build and validate the complete Phase 4 feature artifacts"
    )
    run_parser.add_argument(
        "--config", type=Path, default=ROOT / "config" / "research.yaml"
    )
    run_parser.add_argument(
        "--feature-config", type=Path, default=ROOT / "config" / "features.yaml"
    )
    run_parser.add_argument(
        "--splits", type=Path, default=ROOT / "config" / "data_splits.yaml"
    )
    run_parser.add_argument(
        "--normalized",
        type=Path,
        default=ROOT / "data" / "normalized" / "mnq_1m.parquet",
    )
    run_parser.add_argument(
        "--quality",
        type=Path,
        default=ROOT / "data" / "manifests" / "session_quality.csv",
    )
    run_parser.add_argument(
        "--normalization-manifest",
        type=Path,
        default=ROOT / "data" / "manifests" / "normalization_manifest.json",
    )
    run_parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "data" / "features"
    )
    run_parser.add_argument(
        "--manifests-dir", type=Path, default=ROOT / "data" / "manifests"
    )
    run_parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "reports" / "feature_construction_report.md",
    )
    args = parser.parse_args()
    if args.command not in (None, "run"):
        parser.error("Unknown command")
    result = run_phase4(
        config_path=getattr(args, "config", ROOT / "config" / "research.yaml"),
        feature_config_path=getattr(
            args, "feature_config", ROOT / "config" / "features.yaml"
        ),
        split_config_path=getattr(
            args, "splits", ROOT / "config" / "data_splits.yaml"
        ),
        normalized_path=getattr(
            args,
            "normalized",
            ROOT / "data" / "normalized" / "mnq_1m.parquet",
        ),
        quality_path=getattr(
            args,
            "quality",
            ROOT / "data" / "manifests" / "session_quality.csv",
        ),
        normalization_manifest_path=getattr(
            args,
            "normalization_manifest",
            ROOT / "data" / "manifests" / "normalization_manifest.json",
        ),
        output_dir=getattr(args, "output_dir", ROOT / "data" / "features"),
        manifests_dir=getattr(
            args, "manifests_dir", ROOT / "data" / "manifests"
        ),
        report_path=getattr(
            args,
            "report",
            ROOT / "reports" / "feature_construction_report.md",
        ),
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
