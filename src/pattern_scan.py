"""Phase 5 unconditional intraday market-clock analysis for MNQ.

This module is descriptive by construction.  It loads development rows only,
summarizes forward returns and excursions at fixed clock anchors, measures
stability across pre-known calendar and lagged-regime cuts, and records session
high/low timing.  It does not create signals, optimize rules, simulate fills,
or report any result as a strategy.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import yaml

from src.features import FINAL_SPLIT, load_analysis_features
from src.normalize import sha256_file
from src.statistics import mean_confidence_interval, wilson_interval


ROOT = Path(__file__).resolve().parents[1]
UTC = ZoneInfo("UTC")
DEVELOPMENT_SPLIT = "development"
SCOPE_ETH = "eth_full_session"
SCOPE_RTH = "rth"
WEEKDAY_LABELS = {
    0: "Monday",
    1: "Tuesday",
    2: "Wednesday",
    3: "Thursday",
    4: "Friday",
}
STABILITY_DIMENSIONS = {
    "calendar_year",
    "calendar_quarter",
    "calendar_weekday",
    "trend_regime",
    "volatility_regime",
    "volume_regime",
}
INPUT_COLUMNS = (
    "timestamp_utc",
    "session_date",
    "split",
    "final_test_locked",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "is_rth",
    "session_minute",
    "rth_minute",
    "calendar_year",
    "calendar_quarter",
    "calendar_weekday",
    "trend_regime",
    "volatility_regime",
    "volume_regime",
)


def run_phase5(
    research_config_path: Path = ROOT / "config" / "research.yaml",
    phase5_config_path: Path = ROOT / "config" / "phase5.yaml",
    split_config_path: Path = ROOT / "config" / "data_splits.yaml",
    feature_path: Path = ROOT / "data" / "features" / "mnq_1m_features.parquet",
    feature_manifest_path: Path = ROOT / "data" / "manifests" / "feature_manifest.json",
    results_dir: Path = ROOT / "results" / "phase5",
    manifests_dir: Path = ROOT / "data" / "manifests",
    report_path: Path = ROOT / "reports" / "unconditional_time_analysis.md",
) -> dict[str, Any]:
    """Build, validate, and persist the complete Phase 5 artifacts."""
    research_config = _load_yaml(research_config_path)
    phase5_config = _load_yaml(phase5_config_path)
    split_config = _load_yaml(split_config_path)
    feature_manifest = json.loads(feature_manifest_path.read_text(encoding="utf-8"))
    _validate_inputs(
        research_config=research_config,
        phase5_config=phase5_config,
        split_config=split_config,
        feature_path=feature_path,
        feature_manifest=feature_manifest,
    )

    features = load_analysis_features(
        feature_path,
        splits=(DEVELOPMENT_SPLIT,),
        columns=INPUT_COLUMNS,
    )
    _validate_loaded_features(features, feature_manifest)

    horizons = tuple(
        int(value) for value in phase5_config["market_clock"]["horizons_minutes"]
    )
    anchor_step = int(phase5_config["market_clock"]["anchor_step_minutes"])
    confidence_level = float(
        phase5_config["statistics"]["confidence_level"]
    )
    min_stability = int(
        phase5_config["statistics"]["min_stability_observations"]
    )

    outcome_frames = [
        build_forward_outcomes(
            features,
            scope=scope,
            horizons=horizons,
            anchor_step_minutes=anchor_step,
        )
        for scope in (SCOPE_ETH, SCOPE_RTH)
    ]
    outcomes = pd.concat(outcome_frames, ignore_index=True)
    market_clock = summarize_market_clock(
        outcomes,
        confidence_level=confidence_level,
    )
    stability = summarize_stability_cuts(
        outcomes,
        dimensions=phase5_config["stability"]["dimensions"],
        confidence_level=confidence_level,
        missing_regime_label=phase5_config["stability"]["missing_regime_label"],
        min_observations=min_stability,
    )
    stability_summary = summarize_stability_agreement(
        stability,
        min_observations=min_stability,
        excluded_cut_values={
            phase5_config["stability"]["missing_regime_label"]
        },
    )
    session_timing = build_session_timing(features)
    timing_distribution = summarize_turning_point_timing(
        session_timing,
        scope_bins={
            scope: int(spec["turning_point_bin_minutes"])
            for scope, spec in phase5_config["market_clock"]["scopes"].items()
        },
        confidence_level=confidence_level,
    )
    clock_blocks = build_report_clock_blocks(market_clock, phase5_config)

    artifacts = {
        "market_clock": market_clock,
        "market_clock_stability": stability,
        "market_clock_stability_summary": stability_summary,
        "session_timing": session_timing,
        "turning_point_timing": timing_distribution,
        "report_clock_blocks": clock_blocks,
    }
    validation = validate_phase5_outputs(
        features=features,
        outcomes=outcomes,
        artifacts=artifacts,
        phase5_config=phase5_config,
        feature_manifest=feature_manifest,
    )
    if not validation["gate_passed"]:
        raise ValueError(
            "Phase 5 validation gate failed: "
            + "; ".join(validation["failed_checks"])
        )

    results_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_paths: dict[str, Path] = {}
    for name, frame in artifacts.items():
        path = results_dir / f"{name}.parquet"
        _write_parquet_atomic(path, frame)
        artifact_paths[name] = path

    reproducibility = _verify_reproducible_outputs(
        artifacts=artifacts,
        artifact_paths=artifact_paths,
        results_dir=results_dir,
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
            "Phase 5 validation gate failed: "
            + "; ".join(validation["failed_checks"])
        )

    findings = extract_descriptive_findings(
        market_clock=market_clock,
        stability=stability,
        session_timing=session_timing,
        timing_distribution=timing_distribution,
        phase5_config=phase5_config,
    )
    source_hashes = {
        "bar_features": sha256_file(feature_path),
        "feature_manifest": sha256_file(feature_manifest_path),
        "research_config": sha256_file(research_config_path),
        "phase5_config": sha256_file(phase5_config_path),
        "split_config": sha256_file(split_config_path),
        "pipeline_code": sha256_file(Path(__file__)),
        "statistics_code": sha256_file(ROOT / "src" / "statistics.py"),
    }
    summary: dict[str, Any] = {
        "phase": 5,
        "version": phase5_config["version"],
        "gate_passed": True,
        "analysis_class": phase5_config["analysis_class"],
        "created_by": "python -m src.pattern_scan run",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "input_split": DEVELOPMENT_SPLIT,
        "forbidden_splits": phase5_config["data_scope"]["forbidden_splits"],
        "source_hashes": source_hashes,
        "coverage": {
            "development_rows": int(len(features)),
            "development_sessions": int(features["session_date"].nunique()),
            "rth_sessions": int(
                features.loc[features["is_rth"], "session_date"].nunique()
            ),
            "forward_outcomes": int(len(outcomes)),
            "market_clock_cells": int(len(market_clock)),
            "stability_cells": int(len(stability)),
        },
        "method": {
            "scopes": phase5_config["market_clock"]["scopes"],
            "anchor_step_minutes": anchor_step,
            "horizons_minutes": list(horizons),
            "confidence_level": confidence_level,
            "mean_interval": "Student-t",
            "positive_rate_interval": "Wilson",
            "stability_dimensions": phase5_config["stability"]["dimensions"],
            "minimum_stability_observations": min_stability,
            "interval_definition": (
                "Entry is the fixed clock bar open; exit is the final included "
                "bar close; MFE/MAE use the highs/lows of the included bars."
            ),
        },
        "findings": findings,
        "validation": validation,
        "limitations": [
            "All results are exploratory, descriptive, and unadjusted for the many clock cells examined.",
            "Overlapping forward horizons are correlated and must not be treated as independent tests.",
            "One-minute OHLC bars do not reveal within-bar path or executable fill sequence.",
            "The full-session and RTH clocks overlap because RTH is nested inside the Globex session.",
            "Early-close sessions contribute only to clock cells whose entire horizon is observed.",
            "No validation or final-test outcomes were inspected, and no strategy or tradable performance was produced.",
        ],
        "runtime_versions": {
            "python": platform.python_version(),
            **{
                package: importlib.metadata.version(package)
                for package in ("numpy", "pandas", "pyarrow", "pyyaml", "scipy")
            },
        },
    }
    manifest_path = manifests_dir / "phase5_manifest.json"
    summary["artifacts"] = {
        name: _artifact_profile(path) for name, path in artifact_paths.items()
    }
    summary["artifacts"]["phase5_manifest"] = {
        "path": _display_path(manifest_path),
    }
    _write_json_atomic(manifest_path, summary)
    summary["artifacts"]["phase5_manifest"].update(
        {
            "sha256": sha256_file(manifest_path),
            "bytes": manifest_path.stat().st_size,
        }
    )
    _write_report_atomic(report_path, _render_report(summary))
    return summary


def build_forward_outcomes(
    features: pd.DataFrame,
    *,
    scope: str,
    horizons: Sequence[int],
    anchor_step_minutes: int,
) -> pd.DataFrame:
    """Create fixed-clock forward outcomes without strategy semantics."""
    if scope not in {SCOPE_ETH, SCOPE_RTH}:
        raise ValueError(f"Unknown market-clock scope: {scope}")
    if anchor_step_minutes <= 0:
        raise ValueError("anchor_step_minutes must be positive")
    horizons = tuple(sorted({int(value) for value in horizons}))
    if not horizons or any(value <= 0 for value in horizons):
        raise ValueError("All horizons must be positive integers")
    required = {
        "timestamp_utc",
        "session_date",
        "split",
        "open",
        "high",
        "low",
        "close",
        "is_rth",
        "session_minute",
        "rth_minute",
        "calendar_year",
        "calendar_quarter",
        "calendar_weekday",
        "trend_regime",
        "volatility_regime",
        "volume_regime",
    }
    missing = sorted(required - set(features.columns))
    if missing:
        raise ValueError(f"Forward-outcome input is missing columns: {missing}")
    if set(features["split"].dropna().unique()) != {DEVELOPMENT_SPLIT}:
        raise PermissionError("Phase 5 outcomes may use development rows only")

    frame = features.loc[features["is_rth"]].copy() if scope == SCOPE_RTH else features.copy()
    clock_column = "rth_minute" if scope == SCOPE_RTH else "session_minute"
    rows: list[pd.DataFrame] = []
    for session_date, session in frame.groupby("session_date", sort=True):
        session = session.sort_values("timestamp_utc", kind="mergesort").reset_index(
            drop=True
        )
        minutes = session[clock_column].to_numpy(dtype=np.int64)
        opens = session["open"].to_numpy(dtype=float)
        highs = session["high"].to_numpy(dtype=float)
        lows = session["low"].to_numpy(dtype=float)
        closes = session["close"].to_numpy(dtype=float)
        if len(session) == 0:
            continue
        metadata = session.iloc[0]
        for horizon in horizons:
            if len(session) < horizon:
                continue
            candidate_indices = np.arange(
                0,
                len(session) - horizon + 1,
                dtype=np.int64,
            )
            starts = candidate_indices[
                minutes[candidate_indices] % anchor_step_minutes == 0
            ]
            if len(starts) == 0:
                continue
            expected_end = minutes[starts] + horizon - 1
            contiguous = minutes[starts + horizon - 1] == expected_end
            starts = starts[contiguous]
            if len(starts) == 0:
                continue
            high_windows = np.lib.stride_tricks.sliding_window_view(highs, horizon)
            low_windows = np.lib.stride_tricks.sliding_window_view(lows, horizon)
            entry = opens[starts]
            exit_price = closes[starts + horizon - 1]
            maximum = high_windows[starts].max(axis=1)
            minimum = low_windows[starts].min(axis=1)
            valid_prices = (
                np.isfinite(entry)
                & np.isfinite(exit_price)
                & np.isfinite(maximum)
                & np.isfinite(minimum)
                & (entry > 0)
            )
            if not valid_prices.any():
                continue
            starts = starts[valid_prices]
            entry = entry[valid_prices]
            exit_price = exit_price[valid_prices]
            maximum = maximum[valid_prices]
            minimum = minimum[valid_prices]
            clock_minutes = minutes[starts]
            rows.append(
                pd.DataFrame(
                    {
                        "scope": scope,
                        "session_date": session_date,
                        "calendar_year": int(metadata["calendar_year"]),
                        "calendar_quarter": int(metadata["calendar_quarter"]),
                        "calendar_weekday": int(metadata["calendar_weekday"]),
                        "trend_regime": metadata["trend_regime"],
                        "volatility_regime": metadata["volatility_regime"],
                        "volume_regime": metadata["volume_regime"],
                        "clock_minute": clock_minutes.astype(np.int16),
                        "horizon_minutes": np.full(
                            len(starts), horizon, dtype=np.int16
                        ),
                        "return_points": exit_price - entry,
                        "return_bps": (exit_price / entry - 1.0) * 10_000.0,
                        "mfe_points": maximum - entry,
                        "mfe_bps": (maximum / entry - 1.0) * 10_000.0,
                        "mae_points": minimum - entry,
                        "mae_bps": (minimum / entry - 1.0) * 10_000.0,
                    }
                )
            )
    if not rows:
        return pd.DataFrame(
            columns=[
                "scope",
                "session_date",
                "calendar_year",
                "calendar_quarter",
                "calendar_weekday",
                "trend_regime",
                "volatility_regime",
                "volume_regime",
                "clock_minute",
                "horizon_minutes",
                "return_points",
                "return_bps",
                "mfe_points",
                "mfe_bps",
                "mae_points",
                "mae_bps",
            ]
        )
    result = pd.concat(rows, ignore_index=True)
    result.sort_values(
        ["scope", "session_date", "horizon_minutes", "clock_minute"],
        inplace=True,
        kind="mergesort",
    )
    result.reset_index(drop=True, inplace=True)
    return result


def summarize_market_clock(
    outcomes: pd.DataFrame,
    *,
    confidence_level: float,
) -> pd.DataFrame:
    """Aggregate unconditional returns and excursions by clock and horizon."""
    return _summarize_groups(
        outcomes,
        group_columns=["scope", "clock_minute", "horizon_minutes"],
        confidence_level=confidence_level,
    )


def summarize_stability_cuts(
    outcomes: pd.DataFrame,
    *,
    dimensions: Iterable[str],
    confidence_level: float,
    missing_regime_label: str,
    min_observations: int,
) -> pd.DataFrame:
    """Summarize each clock cell by calendar and strictly lagged regimes."""
    frames: list[pd.DataFrame] = []
    for dimension in dimensions:
        if dimension not in STABILITY_DIMENSIONS:
            raise ValueError(f"Unsupported stability dimension: {dimension}")
        cut = outcomes.copy()
        if dimension == "calendar_year":
            cut["cut_value"] = cut[dimension].astype("Int64").astype(str)
        elif dimension == "calendar_quarter":
            cut["cut_value"] = "Q" + cut[dimension].astype("Int64").astype(str)
        elif dimension == "calendar_weekday":
            cut["cut_value"] = cut[dimension].map(WEEKDAY_LABELS)
        else:
            cut["cut_value"] = cut[dimension].fillna(missing_regime_label).astype(str)
        summary = _summarize_groups(
            cut,
            group_columns=[
                "scope",
                "clock_minute",
                "horizon_minutes",
                "cut_value",
            ],
            confidence_level=confidence_level,
        )
        summary.insert(3, "cut_type", dimension)
        summary["sample_sufficient"] = summary["observations"] >= min_observations
        frames.append(summary)
    result = pd.concat(frames, ignore_index=True)
    result.sort_values(
        [
            "scope",
            "horizon_minutes",
            "clock_minute",
            "cut_type",
            "cut_value",
        ],
        inplace=True,
        kind="mergesort",
    )
    result.reset_index(drop=True, inplace=True)
    return result


def summarize_stability_agreement(
    stability: pd.DataFrame,
    *,
    min_observations: int,
    excluded_cut_values: set[str] | None = None,
) -> pd.DataFrame:
    """Measure sign agreement across sufficiently populated stability cuts."""
    eligible = stability.loc[
        stability["observations"] >= min_observations
    ].copy()
    if excluded_cut_values:
        eligible = eligible.loc[
            ~eligible["cut_value"].isin(excluded_cut_values)
        ].copy()
    if eligible.empty:
        return pd.DataFrame(
            columns=[
                "scope",
                "clock_minute",
                "clock_label",
                "horizon_minutes",
                "end_clock_label",
                "cut_type",
                "eligible_groups",
                "positive_groups",
                "negative_groups",
                "zero_groups",
                "majority_sign",
                "majority_sign_share",
                "minimum_group_mean_bps",
                "maximum_group_mean_bps",
            ]
        )
    eligible["sign"] = np.sign(eligible["mean_return_bps"])
    keys = [
        "scope",
        "clock_minute",
        "clock_label",
        "horizon_minutes",
        "end_clock_label",
        "cut_type",
    ]
    grouped = eligible.groupby(keys, observed=True, sort=True)
    result = grouped.agg(
        eligible_groups=("cut_value", "size"),
        positive_groups=("sign", lambda values: int((values > 0).sum())),
        negative_groups=("sign", lambda values: int((values < 0).sum())),
        zero_groups=("sign", lambda values: int((values == 0).sum())),
        minimum_group_mean_bps=("mean_return_bps", "min"),
        maximum_group_mean_bps=("mean_return_bps", "max"),
    ).reset_index()
    positive_majority = result["positive_groups"] >= result["negative_groups"]
    result["majority_sign"] = np.where(positive_majority, "positive", "negative")
    result.loc[
        result["positive_groups"] == result["negative_groups"], "majority_sign"
    ] = "tie"
    result["majority_sign_share"] = (
        result[["positive_groups", "negative_groups", "zero_groups"]].max(axis=1)
        / result["eligible_groups"]
    )
    return result


def build_session_timing(features: pd.DataFrame) -> pd.DataFrame:
    """Record first session high/low timing and range position by scope."""
    if set(features["split"].dropna().unique()) != {DEVELOPMENT_SPLIT}:
        raise PermissionError("Phase 5 timing analysis may use development rows only")
    rows: list[dict[str, Any]] = []
    for scope in (SCOPE_ETH, SCOPE_RTH):
        frame = (
            features.loc[features["is_rth"]].copy()
            if scope == SCOPE_RTH
            else features.copy()
        )
        clock_column = "rth_minute" if scope == SCOPE_RTH else "session_minute"
        for session_date, session in frame.groupby("session_date", sort=True):
            session = session.sort_values("timestamp_utc", kind="mergesort")
            if session.empty:
                continue
            high_value = float(session["high"].max())
            low_value = float(session["low"].min())
            high_row = session.loc[session["high"].eq(high_value)].iloc[0]
            low_row = session.loc[session["low"].eq(low_value)].iloc[0]
            first_open = float(session.iloc[0]["open"])
            last_close = float(session.iloc[-1]["close"])
            price_range = high_value - low_value
            rows.append(
                {
                    "scope": scope,
                    "session_date": session_date,
                    "calendar_year": int(session.iloc[0]["calendar_year"]),
                    "calendar_quarter": int(session.iloc[0]["calendar_quarter"]),
                    "calendar_weekday": int(session.iloc[0]["calendar_weekday"]),
                    "high_clock_minute": int(high_row[clock_column]),
                    "low_clock_minute": int(low_row[clock_column]),
                    "high_clock_label": _clock_label(
                        scope, int(high_row[clock_column])
                    ),
                    "low_clock_label": _clock_label(
                        scope, int(low_row[clock_column])
                    ),
                    "high_before_low": bool(
                        int(high_row[clock_column]) < int(low_row[clock_column])
                    ),
                    "same_bar_extremes": bool(
                        int(high_row[clock_column]) == int(low_row[clock_column])
                    ),
                    "range_points": price_range,
                    "range_bps": (
                        price_range / first_open * 10_000.0
                        if first_open > 0
                        else np.nan
                    ),
                    "close_location_in_range": (
                        (last_close - low_value) / price_range
                        if price_range > 0
                        else np.nan
                    ),
                }
            )
    result = pd.DataFrame(rows)
    result.sort_values(["scope", "session_date"], inplace=True, kind="mergesort")
    result.reset_index(drop=True, inplace=True)
    return result


def summarize_turning_point_timing(
    session_timing: pd.DataFrame,
    *,
    scope_bins: dict[str, int],
    confidence_level: float,
) -> pd.DataFrame:
    """Summarize the distribution of first high and low timestamps."""
    frames: list[pd.DataFrame] = []
    for scope, scope_frame in session_timing.groupby("scope", sort=True):
        bin_width = int(scope_bins[scope])
        sessions = int(len(scope_frame))
        for extreme in ("high", "low"):
            minutes = scope_frame[f"{extreme}_clock_minute"].astype(int)
            bin_start = (minutes // bin_width) * bin_width
            counts = bin_start.value_counts().sort_index()
            maximum_minute = 1380 if scope == SCOPE_ETH else 390
            expected_starts = range(0, maximum_minute, bin_width)
            for start in expected_starts:
                count = int(counts.get(start, 0))
                lower, upper = wilson_interval(
                    count,
                    sessions,
                    confidence_level=confidence_level,
                )
                frames.append(
                    pd.DataFrame(
                        {
                            "scope": [scope],
                            "extreme_type": [extreme],
                            "bin_start_minute": [start],
                            "bin_end_minute": [min(start + bin_width, maximum_minute)],
                            "bin_label": [
                                f"{_clock_label(scope, start)}-"
                                f"{_clock_label(scope, min(start + bin_width, maximum_minute))}"
                            ],
                            "observations": [sessions],
                            "count": [count],
                            "share": [count / sessions if sessions else np.nan],
                            "share_ci_low": [float(lower)],
                            "share_ci_high": [float(upper)],
                        }
                    )
                )
    result = pd.concat(frames, ignore_index=True)
    result.sort_values(
        ["scope", "extreme_type", "bin_start_minute"],
        inplace=True,
        kind="mergesort",
    )
    result.reset_index(drop=True, inplace=True)
    return result


def build_report_clock_blocks(
    market_clock: pd.DataFrame,
    phase5_config: dict[str, Any],
) -> pd.DataFrame:
    """Select non-overlapping, report-friendly clock blocks."""
    reporting = phase5_config["reporting"]
    selections = [
        (
            SCOPE_RTH,
            int(reporting["rth_summary_horizon_minutes"]),
            int(reporting["rth_summary_start_step_minutes"]),
        ),
        (
            SCOPE_ETH,
            int(reporting["full_session_summary_horizon_minutes"]),
            int(reporting["full_session_summary_start_step_minutes"]),
        ),
    ]
    frames = []
    for scope, horizon, step in selections:
        subset = market_clock.loc[
            (market_clock["scope"] == scope)
            & (market_clock["horizon_minutes"] == horizon)
            & (market_clock["clock_minute"] % step == 0)
        ].copy()
        frames.append(subset)
    result = pd.concat(frames, ignore_index=True)
    result.sort_values(
        ["scope", "clock_minute"], inplace=True, kind="mergesort"
    )
    result.reset_index(drop=True, inplace=True)
    return result


def validate_phase5_outputs(
    *,
    features: pd.DataFrame,
    outcomes: pd.DataFrame,
    artifacts: dict[str, pd.DataFrame],
    phase5_config: dict[str, Any],
    feature_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Run Phase 5 gate checks without consulting later data splits."""
    market_clock = artifacts["market_clock"]
    stability = artifacts["market_clock_stability"]
    timing = artifacts["session_timing"]
    turning = artifacts["turning_point_timing"]
    expected_rows = int(
        feature_manifest["validation"]["details"]["split_row_counts"][
            DEVELOPMENT_SPLIT
        ]
    )
    expected_sessions = int(
        features["session_date"].nunique()
    )
    requested_horizons = set(
        int(value)
        for value in phase5_config["market_clock"]["horizons_minutes"]
    )
    requested_dimensions = set(phase5_config["stability"]["dimensions"])
    outcome_key = ["scope", "session_date", "clock_minute", "horizon_minutes"]
    clock_key = ["scope", "clock_minute", "horizon_minutes"]
    stability_key = [*clock_key, "cut_type", "cut_value"]
    share_sums = (
        turning.groupby(["scope", "extreme_type"], observed=True)["share"]
        .sum()
        .to_numpy(dtype=float)
    )
    checks = {
        "development_split_only": (
            set(features["split"].dropna().unique()) == {DEVELOPMENT_SPLIT}
            and set(outcomes["session_date"]).issubset(set(features["session_date"]))
        ),
        "locked_final_rows_absent": (
            not features["final_test_locked"].any()
            and FINAL_SPLIT not in set(features["split"].dropna().unique())
        ),
        "phase4_development_row_count_preserved": len(features) == expected_rows,
        "outcomes_nonempty": not outcomes.empty,
        "required_scopes_complete": set(outcomes["scope"].unique())
        == {SCOPE_ETH, SCOPE_RTH},
        "required_horizons_complete": set(outcomes["horizon_minutes"].unique())
        == requested_horizons,
        "outcome_keys_unique": not outcomes.duplicated(outcome_key).any(),
        "market_clock_keys_unique": not market_clock.duplicated(clock_key).any(),
        "stability_keys_unique": not stability.duplicated(stability_key).any(),
        "stability_dimensions_complete": set(stability["cut_type"].unique())
        == requested_dimensions,
        "sample_sizes_visible_and_bounded": (
            (market_clock["observations"] > 0).all()
            and (market_clock["observations"] <= expected_sessions).all()
            and (stability["observations"] > 0).all()
            and (stability["observations"] <= expected_sessions).all()
        ),
        "mean_confidence_intervals_valid": (
            (
                market_clock["mean_return_ci_low"]
                <= market_clock["mean_return_bps"]
            ).all()
            and (
                market_clock["mean_return_bps"]
                <= market_clock["mean_return_ci_high"]
            ).all()
        ),
        "positive_rate_intervals_valid": (
            (market_clock["positive_rate_ci_low"] >= 0).all()
            and (market_clock["positive_rate_ci_high"] <= 1).all()
            and (
                market_clock["positive_rate_ci_low"]
                <= market_clock["positive_rate"]
            ).all()
            and (
                market_clock["positive_rate"]
                <= market_clock["positive_rate_ci_high"]
            ).all()
        ),
        "excursion_direction_valid": (
            (market_clock["mean_mfe_bps"] >= -1e-12).all()
            and (market_clock["mean_mae_bps"] <= 1e-12).all()
        ),
        "timing_session_counts_valid": (
            len(timing.loc[timing["scope"] == SCOPE_ETH]) == expected_sessions
            and len(timing.loc[timing["scope"] == SCOPE_RTH])
            == int(features.loc[features["is_rth"], "session_date"].nunique())
        ),
        "turning_point_shares_sum_to_one": np.allclose(
            share_sums, 1.0, atol=1e-12
        ),
        "analysis_is_descriptive_only": (
            phase5_config["analysis_class"] == "descriptive_not_strategy"
            and phase5_config["reporting"]["prohibit_strategy_language"] is True
        ),
    }
    checks = {name: bool(passed) for name, passed in checks.items()}
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "gate_passed": not failed,
        "checks": checks,
        "failed_checks": failed,
        "details": {
            "development_rows": int(len(features)),
            "development_sessions": expected_sessions,
            "rth_sessions": int(
                features.loc[features["is_rth"], "session_date"].nunique()
            ),
            "outcome_rows": int(len(outcomes)),
            "market_clock_cells": int(len(market_clock)),
            "stability_cells": int(len(stability)),
            "timing_rows": int(len(timing)),
            "turning_point_rows": int(len(turning)),
            "observed_splits": sorted(features["split"].dropna().unique()),
        },
    }


def extract_descriptive_findings(
    *,
    market_clock: pd.DataFrame,
    stability: pd.DataFrame,
    session_timing: pd.DataFrame,
    timing_distribution: pd.DataFrame,
    phase5_config: dict[str, Any],
) -> dict[str, Any]:
    """Extract restrained report facts without promoting a candidate."""
    reporting = phase5_config["reporting"]
    rth_horizon = int(reporting["rth_summary_horizon_minutes"])
    rth_step = int(reporting["rth_summary_start_step_minutes"])
    rth_blocks = market_clock.loc[
        (market_clock["scope"] == SCOPE_RTH)
        & (market_clock["horizon_minutes"] == rth_horizon)
        & (market_clock["clock_minute"] % rth_step == 0)
    ].copy()
    strongest = rth_blocks.loc[rth_blocks["mean_return_bps"].idxmax()]
    weakest = rth_blocks.loc[rth_blocks["mean_return_bps"].idxmin()]

    year_stability = stability.loc[
        (stability["scope"] == SCOPE_RTH)
        & (stability["horizon_minutes"] == rth_horizon)
        & (stability["clock_minute"] % rth_step == 0)
        & (stability["cut_type"] == "calendar_year")
        & stability["sample_sufficient"]
    ].copy()

    def year_agreement(row: pd.Series) -> dict[str, Any]:
        groups = year_stability.loc[
            year_stability["clock_minute"] == row["clock_minute"]
        ]
        positive = int((groups["mean_return_bps"] > 0).sum())
        negative = int((groups["mean_return_bps"] < 0).sum())
        return {
            "eligible_years": int(len(groups)),
            "positive_years": positive,
            "negative_years": negative,
        }

    rth_timing = timing_distribution.loc[
        timing_distribution["scope"] == SCOPE_RTH
    ]
    modal_high = rth_timing.loc[
        rth_timing["extreme_type"] == "high"
    ].sort_values(["share", "bin_start_minute"], ascending=[False, True]).iloc[0]
    modal_low = rth_timing.loc[
        rth_timing["extreme_type"] == "low"
    ].sort_values(["share", "bin_start_minute"], ascending=[False, True]).iloc[0]
    rth_sessions = session_timing.loc[session_timing["scope"] == SCOPE_RTH]
    high_before_low_rate = float(rth_sessions["high_before_low"].mean())
    return {
        "rth_strongest_30m_block": {
            **_finding_row(strongest),
            **year_agreement(strongest),
        },
        "rth_weakest_30m_block": {
            **_finding_row(weakest),
            **year_agreement(weakest),
        },
        "rth_modal_high_bin": {
            "bin_label": str(modal_high["bin_label"]),
            "share": float(modal_high["share"]),
            "observations": int(modal_high["observations"]),
        },
        "rth_modal_low_bin": {
            "bin_label": str(modal_low["bin_label"]),
            "share": float(modal_low["share"]),
            "observations": int(modal_low["observations"]),
        },
        "rth_high_before_low_rate": high_before_low_rate,
        "interpretation": (
            "These are unadjusted descriptive extrema among non-overlapping "
            "30-minute RTH blocks. They are inputs to Phase 6 preregistration, "
            "not candidate signals or evidence of tradability."
        ),
    }


def _summarize_groups(
    frame: pd.DataFrame,
    *,
    group_columns: Sequence[str],
    confidence_level: float,
) -> pd.DataFrame:
    grouped = frame.groupby(list(group_columns), observed=True, sort=True)
    result = grouped.agg(
        observations=("return_bps", "size"),
        positive_observations=("return_bps", lambda values: int((values > 0).sum())),
        mean_return_bps=("return_bps", "mean"),
        median_return_bps=("return_bps", "median"),
        standard_deviation_bps=("return_bps", "std"),
        mean_return_points=("return_points", "mean"),
        median_return_points=("return_points", "median"),
        mean_mfe_bps=("mfe_bps", "mean"),
        median_mfe_bps=("mfe_bps", "median"),
        mean_mae_bps=("mae_bps", "mean"),
        median_mae_bps=("mae_bps", "median"),
        mean_mfe_points=("mfe_points", "mean"),
        mean_mae_points=("mae_points", "mean"),
    ).reset_index()
    quantiles = (
        grouped["return_bps"]
        .quantile([0.25, 0.75])
        .unstack(fill_value=np.nan)
        .rename(columns={0.25: "return_bps_p25", 0.75: "return_bps_p75"})
        .reset_index()
    )
    result = result.merge(
        quantiles,
        on=list(group_columns),
        how="left",
        validate="one_to_one",
    )
    result["positive_rate"] = (
        result["positive_observations"] / result["observations"]
    )
    mean_low, mean_high = mean_confidence_interval(
        result["mean_return_bps"].to_numpy(),
        result["standard_deviation_bps"].to_numpy(),
        result["observations"].to_numpy(),
        confidence_level=confidence_level,
    )
    rate_low, rate_high = wilson_interval(
        result["positive_observations"].to_numpy(),
        result["observations"].to_numpy(),
        confidence_level=confidence_level,
    )
    result["mean_return_ci_low"] = mean_low
    result["mean_return_ci_high"] = mean_high
    result["positive_rate_ci_low"] = rate_low
    result["positive_rate_ci_high"] = rate_high
    result["standard_error_bps"] = (
        result["standard_deviation_bps"] / np.sqrt(result["observations"])
    )
    result["clock_label"] = [
        _clock_label(scope, minute)
        for scope, minute in zip(result["scope"], result["clock_minute"])
    ]
    result["end_clock_label"] = [
        _clock_label(scope, int(minute) + int(horizon))
        for scope, minute, horizon in zip(
            result["scope"],
            result["clock_minute"],
            result["horizon_minutes"],
        )
    ]
    leading = list(group_columns)
    if "cut_value" in leading:
        leading = [
            "scope",
            "clock_minute",
            "horizon_minutes",
            "cut_value",
        ]
    ordered = []
    for column in leading:
        ordered.append(column)
        if column == "clock_minute":
            ordered.append("clock_label")
        if column == "horizon_minutes":
            ordered.append("end_clock_label")
    remaining = [column for column in result.columns if column not in ordered]
    result = result[ordered + remaining]
    result.sort_values(
        list(group_columns), inplace=True, kind="mergesort"
    )
    result.reset_index(drop=True, inplace=True)
    return result


def _clock_label(scope: str, minute: int) -> str:
    base_minutes = 18 * 60 if scope == SCOPE_ETH else 9 * 60 + 30
    clock_minutes = (base_minutes + int(minute)) % (24 * 60)
    return f"{clock_minutes // 60:02d}:{clock_minutes % 60:02d}"


def _finding_row(row: pd.Series) -> dict[str, Any]:
    return {
        "start": str(row["clock_label"]),
        "end": str(row["end_clock_label"]),
        "observations": int(row["observations"]),
        "mean_return_bps": float(row["mean_return_bps"]),
        "mean_return_ci_low": float(row["mean_return_ci_low"]),
        "mean_return_ci_high": float(row["mean_return_ci_high"]),
        "median_return_bps": float(row["median_return_bps"]),
        "positive_rate": float(row["positive_rate"]),
        "mean_mfe_bps": float(row["mean_mfe_bps"]),
        "mean_mae_bps": float(row["mean_mae_bps"]),
    }


def _validate_inputs(
    *,
    research_config: dict[str, Any],
    phase5_config: dict[str, Any],
    split_config: dict[str, Any],
    feature_path: Path,
    feature_manifest: dict[str, Any],
) -> None:
    if not research_config["project"]["mcp_read_only"]:
        raise ValueError("The project MCP mode must remain read-only")
    if phase5_config["data_scope"]["allowed_split"] != DEVELOPMENT_SPLIT:
        raise ValueError("Phase 5 must use the development split only")
    forbidden = set(phase5_config["data_scope"]["forbidden_splits"])
    if forbidden != {"validation", FINAL_SPLIT}:
        raise ValueError("Phase 5 must forbid validation and final-test rows")
    if not split_config["splits"][FINAL_SPLIT]["locked"]:
        raise ValueError("The final-test split must remain locked")
    if not feature_manifest.get("gate_passed"):
        raise ValueError("Phase 4 feature gate must pass before Phase 5")
    expected_hash = feature_manifest["artifacts"]["bar_features"]["sha256"]
    if sha256_file(feature_path) != expected_hash:
        raise ValueError("Phase 4 bar-feature checksum mismatch")
    horizons = phase5_config["market_clock"]["horizons_minutes"]
    if sorted(set(int(value) for value in horizons)) != [1, 5, 15, 30, 60]:
        raise ValueError("Phase 5 horizons must remain 1, 5, 15, 30, and 60 minutes")
    if set(phase5_config["stability"]["dimensions"]) != STABILITY_DIMENSIONS:
        raise ValueError("Phase 5 stability dimensions are incomplete")


def _validate_loaded_features(
    features: pd.DataFrame,
    feature_manifest: dict[str, Any],
) -> None:
    if features.empty:
        raise ValueError("No development feature rows were loaded")
    splits = set(features["split"].dropna().unique())
    if splits != {DEVELOPMENT_SPLIT}:
        raise PermissionError(f"Unexpected Phase 5 splits: {sorted(splits)}")
    if features["final_test_locked"].any():
        raise PermissionError("Locked final-test rows entered Phase 5")
    expected_rows = int(
        feature_manifest["validation"]["details"]["split_row_counts"][
            DEVELOPMENT_SPLIT
        ]
    )
    if len(features) != expected_rows:
        raise ValueError(
            f"Expected {expected_rows:,} development rows; loaded {len(features):,}"
        )
    if features.duplicated(["timestamp_utc", "session_date"]).any():
        raise ValueError("Development feature timestamps are not unique")


def _verify_reproducible_outputs(
    *,
    artifacts: dict[str, pd.DataFrame],
    artifact_paths: dict[str, Path],
    results_dir: Path,
) -> dict[str, Any]:
    comparisons: dict[str, dict[str, Any]] = {}
    with TemporaryDirectory(prefix=".phase5_rebuild_", dir=results_dir) as temporary:
        work_dir = Path(temporary)
        for name, frame in artifacts.items():
            rebuilt = work_dir / f"{name}.parquet"
            frame.to_parquet(
                rebuilt,
                index=False,
                compression="zstd",
                engine="pyarrow",
            )
            original_hash = sha256_file(artifact_paths[name])
            rebuilt_hash = sha256_file(rebuilt)
            comparisons[name] = {
                "original_sha256": original_hash,
                "rebuilt_sha256": rebuilt_hash,
                "byte_identical": original_hash == rebuilt_hash,
            }
    return {
        "byte_identical": all(
            item["byte_identical"] for item in comparisons.values()
        ),
        "artifacts": comparisons,
    }


def _render_report(summary: dict[str, Any]) -> str:
    findings = summary["findings"]
    strongest = findings["rth_strongest_30m_block"]
    weakest = findings["rth_weakest_30m_block"]
    high_bin = findings["rth_modal_high_bin"]
    low_bin = findings["rth_modal_low_bin"]
    validation = summary["validation"]
    return f"""# Phase 5 Unconditional Time Analysis

## Technical summary

The Phase 5 gate **passed** using development data only:
{summary["coverage"]["development_rows"]:,} one-minute bars across
{summary["coverage"]["development_sessions"]:,} usable Globex sessions and
{summary["coverage"]["rth_sessions"]:,} sessions with RTH observations. The
pipeline measured {summary["coverage"]["market_clock_cells"]:,} unconditional
clock/horizon cells and {summary["coverage"]["stability_cells"]:,} calendar and
lagged-regime cuts. It did not inspect validation or final-test outcomes and did
not create a strategy.

Among non-overlapping 30-minute RTH blocks, the largest development-sample mean
was {strongest["start"]}-{strongest["end"]} at
{strongest["mean_return_bps"]:.2f} bps (95% CI
{strongest["mean_return_ci_low"]:.2f} to
{strongest["mean_return_ci_high"]:.2f}; n={strongest["observations"]:,}).
The smallest was {weakest["start"]}-{weakest["end"]} at
{weakest["mean_return_bps"]:.2f} bps (95% CI
{weakest["mean_return_ci_low"]:.2f} to
{weakest["mean_return_ci_high"]:.2f}; n={weakest["observations"]:,}).
These are unadjusted descriptive extrema, not signals or evidence of
tradability.

## Key descriptive findings

- The strongest 30-minute block was positive in
  {strongest["positive_years"]} of {strongest["eligible_years"]} sufficiently
  populated calendar-year cuts; the weakest was negative in
  {weakest["negative_years"]} of {weakest["eligible_years"]}.
- The most common first RTH session-high bin was {high_bin["bin_label"]}
  ({high_bin["share"]:.1%} of {high_bin["observations"]:,} sessions).
- The most common first RTH session-low bin was {low_bin["bin_label"]}
  ({low_bin["share"]:.1%} of {low_bin["observations"]:,} sessions).
- The RTH high occurred before the RTH low in
  {findings["rth_high_before_low_rate"]:.1%} of sessions. This is descriptive
  ordering, not an executable entry rule.

## Scope and metric definitions

- **Development population:** the frozen development split only. Validation and
  final-test rows are prohibited.
- **ETH full-session clock:** the contiguous 18:00-17:00 Eastern Globex
  session.
- **RTH clock:** 09:30-16:00 Eastern, analyzed separately and nested inside the
  full-session clock.
- **Anchors:** fixed five-minute clock starts.
- **Horizons:** 1, 5, 15, 30, and 60 minutes.
- **Return:** final included one-minute bar close divided by the fixed-clock
  entry bar open, minus one, reported in basis points.
- **MFE/MAE:** highest high and lowest low during the interval relative to the
  entry bar open. These are descriptive bar excursions, not simulated fills.
- **Uncertainty:** two-sided 95% Student-t intervals for the mean and Wilson
  intervals for the positive-return rate.

## Stability analysis

Every clock/horizon cell was cut by calendar year, calendar quarter, weekday,
and the strictly lagged trend, volatility, and volume regimes produced in Phase
4. A cut is marked sufficiently populated at 30 observations. Sample sizes,
means, medians, dispersion, return quartiles, positive rates, excursions, and
intervals remain available in the machine-readable outputs. Overlapping
horizons are correlated, and no multiple-testing correction is applied in this
descriptive phase.

## Gate evidence

{chr(10).join(f"- {'PASS' if passed else 'FAIL'}: `{name}`" for name, passed in validation["checks"].items())}

## Reproducible artifacts

- `results/phase5/market_clock.parquet`
- `results/phase5/market_clock_stability.parquet`
- `results/phase5/market_clock_stability_summary.parquet`
- `results/phase5/session_timing.parquet`
- `results/phase5/turning_point_timing.parquet`
- `results/phase5/report_clock_blocks.parquet`
- `data/manifests/phase5_manifest.json`
- `config/phase5.yaml`
- `src/pattern_scan.py`
- `src/statistics.py`
- `tests/test_pattern_scan.py`

## Limitations and Phase 6 boundary

{chr(10).join(f"- {item}" for item in summary["limitations"])}

Phase 6 may use these descriptive maps to preregister a limited set of
interpretable event families. It must retain failures, use bootstrap and
permuted-null inference, and correct within-family multiplicity before any
event is considered for candidate generation.
"""


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


def _write_parquet_atomic(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".parquet",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(
            temporary,
            index=False,
            compression="zstd",
            engine="pyarrow",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


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


def _write_report_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(value)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser(
        "run", help="Build and validate all Phase 5 descriptive artifacts"
    )
    run_parser.add_argument(
        "--feature-path",
        type=Path,
        default=ROOT / "data" / "features" / "mnq_1m_features.parquet",
    )
    validate_parser = subparsers.add_parser(
        "validate", help="Re-run Phase 5 from immutable inputs and validate the gate"
    )
    validate_parser.add_argument(
        "--feature-path",
        type=Path,
        default=ROOT / "data" / "features" / "mnq_1m_features.parquet",
    )
    args = parser.parse_args()
    if args.command in {"run", "validate"}:
        summary = run_phase5(feature_path=args.feature_path)
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
