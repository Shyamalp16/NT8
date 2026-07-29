"""Phase 6 preregistered event studies for MNQ development data.

The module evaluates only the hypotheses frozen in ``config/phase6.yaml``.
It does not inspect validation or final-test outcomes, optimize event
definitions, simulate trades, or make tradability claims.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import yaml

from src.features import FINAL_SPLIT, load_analysis_features
from src.normalize import sha256_file
from src.statistics import (
    benjamini_hochberg,
    bootstrap_mean_interval,
    direction_permutation_pvalue,
    sign_flip_permutation_pvalue,
)


ROOT = Path(__file__).resolve().parents[1]
UTC = ZoneInfo("UTC")
DEVELOPMENT_SPLIT = "development"
EXPECTED_ANALYSIS_CLASS = "preregistered_event_study_not_strategy"

BASE_COLUMNS = (
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
    "rth_minute",
    "calendar_year",
)
EVENT_COLUMNS = (
    "family_id",
    "hypothesis_id",
    "event_label",
    "session_date",
    "calendar_year",
    "trigger_clock_minute",
    "entry_clock_minute",
    "horizon_minutes",
    "event_direction",
    "event_magnitude_bps",
    "entry_price",
    "exit_price",
    "forward_return_bps",
    "effect_bps",
)


def run_phase6(
    research_config_path: Path = ROOT / "config" / "research.yaml",
    phase6_config_path: Path = ROOT / "config" / "phase6.yaml",
    split_config_path: Path = ROOT / "config" / "data_splits.yaml",
    feature_path: Path = ROOT / "data" / "features" / "mnq_1m_features.parquet",
    feature_manifest_path: Path = ROOT / "data" / "manifests" / "feature_manifest.json",
    phase5_manifest_path: Path = ROOT / "data" / "manifests" / "phase5_manifest.json",
    results_dir: Path = ROOT / "results" / "phase6",
    manifests_dir: Path = ROOT / "data" / "manifests",
    report_path: Path = ROOT / "reports" / "event_studies_report.md",
) -> dict[str, Any]:
    """Build, validate, and persist the complete Phase 6 artifacts."""
    research_config = _load_yaml(research_config_path)
    phase6_config = _load_yaml(phase6_config_path)
    split_config = _load_yaml(split_config_path)
    feature_manifest = json.loads(feature_manifest_path.read_text(encoding="utf-8"))
    phase5_manifest = json.loads(phase5_manifest_path.read_text(encoding="utf-8"))
    _validate_inputs(
        research_config=research_config,
        phase6_config=phase6_config,
        split_config=split_config,
        feature_path=feature_path,
        feature_manifest=feature_manifest,
        phase5_manifest=phase5_manifest,
    )

    columns = _required_feature_columns(phase6_config)
    features = load_analysis_features(
        feature_path,
        splits=(DEVELOPMENT_SPLIT,),
        columns=columns,
    )
    _validate_loaded_features(features, feature_manifest)

    hypotheses = preregister_hypotheses(phase6_config)
    observations = build_event_observations(features, phase6_config)
    ledger = summarize_hypotheses(observations, hypotheses, phase6_config)
    family_summary = summarize_families(ledger)
    stability = summarize_year_stability(observations)
    artifacts = {
        "event_observations": observations,
        "hypothesis_ledger": ledger,
        "family_summary": family_summary,
        "hypothesis_year_stability": stability,
    }
    validation = validate_phase6_outputs(
        features=features,
        hypotheses=hypotheses,
        artifacts=artifacts,
        phase6_config=phase6_config,
        feature_manifest=feature_manifest,
    )
    if not validation["gate_passed"]:
        raise ValueError(
            "Phase 6 validation gate failed: "
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
            "Phase 6 validation gate failed: "
            + "; ".join(validation["failed_checks"])
        )

    alpha = float(
        phase6_config["inference"]["multiple_testing"]["alpha"]
    )
    advancing = ledger.loc[ledger["advances_to_phase7"]].copy()
    source_hashes = {
        "bar_features": sha256_file(feature_path),
        "feature_manifest": sha256_file(feature_manifest_path),
        "phase5_manifest": sha256_file(phase5_manifest_path),
        "research_config": sha256_file(research_config_path),
        "phase6_preregistration": sha256_file(phase6_config_path),
        "split_config": sha256_file(split_config_path),
        "pipeline_code": sha256_file(Path(__file__)),
        "statistics_code": sha256_file(ROOT / "src" / "statistics.py"),
    }
    summary: dict[str, Any] = {
        "phase": 6,
        "version": phase6_config["version"],
        "gate_passed": True,
        "analysis_class": phase6_config["analysis_class"],
        "created_by": "python -m src.event_studies run",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "input_split": DEVELOPMENT_SPLIT,
        "forbidden_splits": phase6_config["data_scope"]["forbidden_splits"],
        "source_hashes": source_hashes,
        "preregistration": {
            "config_path": _display_path(phase6_config_path),
            "sha256": source_hashes["phase6_preregistration"],
            "hypotheses": int(len(hypotheses)),
            "families": int(hypotheses["family_id"].nunique()),
        },
        "coverage": {
            "development_rows": int(len(features)),
            "development_sessions": int(features["session_date"].nunique()),
            "event_observations": int(len(observations)),
            "hypotheses": int(len(ledger)),
            "sample_sufficient_hypotheses": int(
                ledger["sample_sufficient"].sum()
            ),
            "bh_rejections": int(ledger["bh_reject"].sum()),
            "advancing_hypotheses": int(ledger["advances_to_phase7"].sum()),
            "validation_rows_used": 0,
            "final_test_rows_used": 0,
        },
        "method": {
            "bootstrap": "session-level percentile bootstrap",
            "randomized_nulls": sorted(ledger["randomized_null"].unique().tolist()),
            "multiple_testing": (
                f"Benjamini-Hochberg within family at alpha={alpha:g}"
            ),
            "alternative": phase6_config["inference"]["alternative"],
            "bootstrap_replicates": int(
                phase6_config["inference"]["bootstrap_replicates"]
            ),
            "permutation_replicates": int(
                phase6_config["inference"]["permutation_replicates"]
            ),
            "minimum_observations": int(
                phase6_config["inference"]["minimum_observations"]
            ),
        },
        "findings": {
            "advancing_hypotheses": advancing[
                [
                    "hypothesis_id",
                    "family_id",
                    "observations",
                    "mean_effect_bps",
                    "bootstrap_ci_low",
                    "bootstrap_ci_high",
                    "permutation_p_value",
                    "bh_q_value",
                ]
            ].to_dict(orient="records"),
            "interpretation": (
                "Positive directional effects indicate continuation and negative "
                "effects indicate reversal. Advancement is an event-study screen, "
                "not evidence of tradability."
            ),
        },
        "validation": validation,
        "limitations": [
            "All Phase 6 results use the same development period that informed the Phase 5 descriptive map.",
            "Benjamini-Hochberg correction is applied within prespecified families, not across every conceivable event definition.",
            "One-minute OHLC bars do not reveal within-bar path or executable fill sequence.",
            "No commissions, slippage, orders, stops, targets, or position sizing are part of this event study.",
            "Validation and final-test outcomes remain untouched.",
        ],
        "artifacts": {},
        "runtime_versions": _runtime_versions(),
    }
    for name, path in artifact_paths.items():
        summary["artifacts"][name] = _artifact_profile(path)

    manifest_path = manifests_dir / "phase6_manifest.json"
    summary["artifacts"]["phase6_manifest"] = {
        "path": _display_path(manifest_path)
    }
    _write_json_atomic(manifest_path, summary)
    summary["artifacts"]["phase6_manifest"].update(
        {
            "sha256": sha256_file(manifest_path),
            "bytes": manifest_path.stat().st_size,
        }
    )
    _write_report_atomic(report_path, _render_report(summary, ledger, family_summary))
    return summary


def preregister_hypotheses(config: dict[str, Any]) -> pd.DataFrame:
    """Expand the frozen family definitions into a complete hypothesis ledger."""
    rows: list[dict[str, Any]] = []
    for family in config["event_families"]:
        family_id = str(family["family_id"])
        event_type = family["event_type"]
        if event_type == "fixed_clock":
            for anchor in family["anchor_minutes"]:
                for horizon in family["horizons_minutes"]:
                    start = _rth_label(int(anchor))
                    end = _rth_label(int(anchor) + int(horizon))
                    rows.append(
                        _hypothesis_row(
                            family,
                            f"{family_id}__{start.replace(':', '')}_{end.replace(':', '')}",
                            f"{start}-{end} raw return",
                            int(horizon),
                            anchor_minute=int(anchor),
                        )
                    )
        elif event_type == "directional_anchor":
            for horizon in family["horizons_minutes"]:
                rows.append(
                    _hypothesis_row(
                        family,
                        f"{family_id}__{int(horizon)}m",
                        f"{family_id.replace('_', ' ')} over {int(horizon)} minutes",
                        int(horizon),
                        anchor_minute=int(family["anchor_minute"]),
                    )
                )
        elif event_type == "first_range_break":
            for reference in family["reference_ranges"]:
                for horizon in family["horizons_minutes"]:
                    range_id = str(reference["range_id"])
                    rows.append(
                        _hypothesis_row(
                            family,
                            f"{family_id}__{range_id}__{int(horizon)}m",
                            f"first {range_id.replace('_', ' ')} break, {int(horizon)} minutes",
                            int(horizon),
                            reference_range=range_id,
                        )
                    )
        else:
            raise ValueError(f"Unknown event_type in preregistration: {event_type}")
    frame = pd.DataFrame(rows)
    frame.sort_values(
        ["family_id", "hypothesis_id"],
        inplace=True,
        kind="mergesort",
    )
    frame.reset_index(drop=True, inplace=True)
    if frame["hypothesis_id"].duplicated().any():
        raise ValueError("Preregistered hypothesis IDs must be unique")
    return frame


def build_event_observations(
    features: pd.DataFrame,
    config: dict[str, Any],
) -> pd.DataFrame:
    """Extract one development-session observation per eligible hypothesis."""
    _guard_development_only(features)
    rth = features.loc[features["is_rth"]].copy()
    rth.sort_values(
        ["session_date", "timestamp_utc"],
        inplace=True,
        kind="mergesort",
    )
    sessions = [
        session.reset_index(drop=True)
        for _, session in rth.groupby("session_date", sort=True)
    ]
    rows: list[dict[str, Any]] = []
    for family in config["event_families"]:
        event_type = family["event_type"]
        if event_type == "fixed_clock":
            rows.extend(_fixed_clock_events(sessions, family))
        elif event_type == "directional_anchor":
            rows.extend(_directional_anchor_events(sessions, family))
        elif event_type == "first_range_break":
            rows.extend(_range_break_events(sessions, family))
        else:
            raise ValueError(f"Unknown event_type: {event_type}")
    result = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    if result.empty:
        return result
    result["session_date"] = pd.to_datetime(result["session_date"])
    result.sort_values(
        ["family_id", "hypothesis_id", "session_date"],
        inplace=True,
        kind="mergesort",
    )
    result.reset_index(drop=True, inplace=True)
    return result


def summarize_hypotheses(
    observations: pd.DataFrame,
    hypotheses: pd.DataFrame,
    config: dict[str, Any],
) -> pd.DataFrame:
    """Attach session-aware inference and within-family BH correction."""
    inference = config["inference"]
    confidence = float(inference["confidence_level"])
    bootstrap_replicates = int(inference["bootstrap_replicates"])
    permutation_replicates = int(inference["permutation_replicates"])
    base_seed = int(inference["random_seed"])
    alternative = str(inference["alternative"])
    minimum = int(inference["minimum_observations"])
    alpha = float(inference["multiple_testing"]["alpha"])
    rows: list[dict[str, Any]] = []
    for hypothesis in hypotheses.itertuples(index=False):
        sample = observations.loc[
            observations["hypothesis_id"] == hypothesis.hypothesis_id
        ]
        effects = sample["effect_bps"].to_numpy(dtype=float)
        n = int(len(sample))
        if n:
            ci_low, ci_high = bootstrap_mean_interval(
                effects,
                confidence_level=confidence,
                replicates=bootstrap_replicates,
                random_seed=_stable_seed(
                    base_seed, hypothesis.hypothesis_id, "bootstrap"
                ),
            )
            if hypothesis.randomized_null == "rademacher_sign_flip":
                p_value = sign_flip_permutation_pvalue(
                    effects,
                    replicates=permutation_replicates,
                    random_seed=_stable_seed(
                        base_seed, hypothesis.hypothesis_id, "permutation"
                    ),
                    alternative=alternative,
                )
            else:
                p_value = direction_permutation_pvalue(
                    sample["event_direction"].to_numpy(dtype=float),
                    sample["forward_return_bps"].to_numpy(dtype=float),
                    replicates=permutation_replicates,
                    random_seed=_stable_seed(
                        base_seed, hypothesis.hypothesis_id, "permutation"
                    ),
                    alternative=alternative,
                )
            mean_effect = float(np.mean(effects))
            median_effect = float(np.median(effects))
            standard_deviation = (
                float(np.std(effects, ddof=1)) if n >= 2 else float("nan")
            )
            positive_rate = float(np.mean(effects > 0.0))
            first_session = pd.Timestamp(sample["session_date"].min())
            last_session = pd.Timestamp(sample["session_date"].max())
            years = int(sample["calendar_year"].nunique())
        else:
            ci_low = ci_high = p_value = float("nan")
            mean_effect = median_effect = standard_deviation = float("nan")
            positive_rate = float("nan")
            first_session = last_session = pd.NaT
            years = 0
        row = hypothesis._asdict()
        row.update(
            {
                "observations": n,
                "years": years,
                "first_session": first_session,
                "last_session": last_session,
                "mean_effect_bps": mean_effect,
                "median_effect_bps": median_effect,
                "standard_deviation_bps": standard_deviation,
                "positive_effect_rate": positive_rate,
                "bootstrap_ci_low": ci_low,
                "bootstrap_ci_high": ci_high,
                "permutation_p_value": p_value,
                "sample_sufficient": n >= minimum,
            }
        )
        rows.append(row)
    ledger = pd.DataFrame(rows)
    ledger["bh_q_value"] = np.nan
    ledger["bh_reject"] = False
    for family_id, positions in ledger.groupby("family_id", sort=True).groups.items():
        position_array = np.asarray(list(positions), dtype=int)
        q_values, rejected = benjamini_hochberg(
            ledger.loc[position_array, "permutation_p_value"].to_numpy(dtype=float),
            alpha=alpha,
        )
        ledger.loc[position_array, "bh_q_value"] = q_values
        ledger.loc[position_array, "bh_reject"] = rejected
    ledger["bh_reject"] = (
        ledger["bh_reject"].astype(bool) & ledger["sample_sufficient"].astype(bool)
    )
    ledger["bootstrap_ci_excludes_zero"] = (
        (ledger["bootstrap_ci_low"] > 0.0)
        | (ledger["bootstrap_ci_high"] < 0.0)
    ) & ledger["sample_sufficient"]
    ledger["advances_to_phase7"] = (
        ledger["bh_reject"] & ledger["bootstrap_ci_excludes_zero"]
    )
    ledger["result"] = np.select(
        [
            ~ledger["sample_sufficient"],
            ledger["advances_to_phase7"],
        ],
        ["insufficient_sample", "advances_to_phase7"],
        default="does_not_advance",
    )
    ledger.sort_values(
        ["family_id", "hypothesis_id"],
        inplace=True,
        kind="mergesort",
    )
    ledger.reset_index(drop=True, inplace=True)
    return ledger


def summarize_families(ledger: pd.DataFrame) -> pd.DataFrame:
    """Summarize complete winner/failure counts without dropping hypotheses."""
    rows: list[dict[str, Any]] = []
    for family_id, group in ledger.groupby("family_id", sort=True):
        rows.append(
            {
                "family_id": family_id,
                "hypotheses": int(len(group)),
                "sample_sufficient_hypotheses": int(
                    group["sample_sufficient"].sum()
                ),
                "bh_rejections": int(group["bh_reject"].sum()),
                "advancing_hypotheses": int(
                    group["advances_to_phase7"].sum()
                ),
                "minimum_p_value": float(group["permutation_p_value"].min()),
                "minimum_q_value": float(group["bh_q_value"].min()),
                "maximum_observations": int(group["observations"].max()),
                "minimum_observations": int(group["observations"].min()),
            }
        )
    return pd.DataFrame(rows)


def summarize_year_stability(observations: pd.DataFrame) -> pd.DataFrame:
    """Retain calendar-year effect signs as a transparent stability diagnostic."""
    if observations.empty:
        return pd.DataFrame(
            columns=[
                "family_id",
                "hypothesis_id",
                "calendar_year",
                "observations",
                "mean_effect_bps",
                "median_effect_bps",
                "positive_effect_rate",
            ]
        )
    result = (
        observations.groupby(
            ["family_id", "hypothesis_id", "calendar_year"],
            sort=True,
            observed=True,
        )["effect_bps"]
        .agg(
            observations="size",
            mean_effect_bps="mean",
            median_effect_bps="median",
            positive_effect_rate=lambda values: float(np.mean(values > 0.0)),
        )
        .reset_index()
    )
    return result


def validate_phase6_outputs(
    *,
    features: pd.DataFrame,
    hypotheses: pd.DataFrame,
    artifacts: dict[str, pd.DataFrame],
    phase6_config: dict[str, Any],
    feature_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Run the Phase 6 gate checks without consulting later splits."""
    observations = artifacts["event_observations"]
    ledger = artifacts["hypothesis_ledger"]
    family_summary = artifacts["family_summary"]
    expected_rows = int(
        feature_manifest["validation"]["details"]["split_row_counts"][
            DEVELOPMENT_SPLIT
        ]
    )
    expected_ids = set(hypotheses["hypothesis_id"])
    ledger_ids = set(ledger["hypothesis_id"])
    observation_ids = set(observations["hypothesis_id"])
    valid_inference = ledger.loc[ledger["sample_sufficient"]]
    key = ["hypothesis_id", "session_date"]
    family_counts_match = bool(
        family_summary.set_index("family_id")["hypotheses"].to_dict()
        == ledger.groupby("family_id").size().to_dict()
    )
    ci_valid = bool(
        (
            valid_inference["bootstrap_ci_low"]
            <= valid_inference["mean_effect_bps"]
        ).all()
        and (
            valid_inference["mean_effect_bps"]
            <= valid_inference["bootstrap_ci_high"]
        ).all()
    )
    checks = {
        "preregistration_flag_frozen": (
            phase6_config.get("preregistered_before_phase6_outcomes") is True
        ),
        "development_split_only": set(features["split"].dropna().unique())
        == {DEVELOPMENT_SPLIT},
        "locked_final_rows_absent": (
            not features["final_test_locked"].any()
            and FINAL_SPLIT not in set(features["split"].dropna().unique())
        ),
        "phase4_development_row_count_preserved": len(features) == expected_rows,
        "hypothesis_ids_unique": not hypotheses["hypothesis_id"].duplicated().any(),
        "full_hypothesis_ledger_retained": ledger_ids == expected_ids,
        "no_unregistered_observations": observation_ids.issubset(expected_ids),
        "one_observation_per_session_hypothesis": not observations.duplicated(
            key
        ).any(),
        "event_observations_nonempty": not observations.empty,
        "family_summary_complete": family_counts_match,
        "sample_sizes_visible": (ledger["observations"] >= 0).all(),
        "bootstrap_intervals_valid": ci_valid,
        "permutation_p_values_valid": (
            valid_inference["permutation_p_value"].between(0.0, 1.0).all()
        ),
        "bh_q_values_valid": valid_inference["bh_q_value"].between(0.0, 1.0).all(),
        "advancement_rule_enforced": (
            ledger["advances_to_phase7"]
            == (ledger["bh_reject"] & ledger["bootstrap_ci_excludes_zero"])
        ).all(),
        "all_failures_retained": len(ledger) == len(hypotheses),
        "analysis_is_event_study_not_strategy": (
            phase6_config["analysis_class"] == EXPECTED_ANALYSIS_CLASS
            and phase6_config["outcome_semantics"]["no_costs_or_fill_simulation"]
            is True
            and phase6_config["reporting"][
                "prohibit_strategy_or_tradability_claims"
            ]
            is True
        ),
    }
    checks = {name: bool(value) for name, value in checks.items()}
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "gate_passed": not failed,
        "checks": checks,
        "failed_checks": failed,
        "details": {
            "development_rows": int(len(features)),
            "development_sessions": int(features["session_date"].nunique()),
            "observed_splits": sorted(features["split"].dropna().unique().tolist()),
            "hypotheses": int(len(hypotheses)),
            "families": int(hypotheses["family_id"].nunique()),
            "event_observations": int(len(observations)),
            "sample_sufficient_hypotheses": int(
                ledger["sample_sufficient"].sum()
            ),
            "bh_rejections": int(ledger["bh_reject"].sum()),
            "advancing_hypotheses": int(ledger["advances_to_phase7"].sum()),
            "validation_rows_used": 0,
            "final_test_rows_used": 0,
        },
    }


def _fixed_clock_events(
    sessions: Sequence[pd.DataFrame],
    family: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    family_id = str(family["family_id"])
    for session in sessions:
        for anchor in family["anchor_minutes"]:
            for horizon in family["horizons_minutes"]:
                anchor = int(anchor)
                horizon = int(horizon)
                index = _clock_index(session, anchor)
                if index is None or not _window_is_contiguous(
                    session, index, horizon
                ):
                    continue
                entry = float(session.iloc[index]["open"])
                exit_price = float(session.iloc[index + horizon - 1]["close"])
                if not _valid_prices(entry, exit_price):
                    continue
                start = _rth_label(anchor)
                end = _rth_label(anchor + horizon)
                forward = (exit_price / entry - 1.0) * 10_000.0
                rows.append(
                    _event_row(
                        family_id=family_id,
                        hypothesis_id=(
                            f"{family_id}__{start.replace(':', '')}_"
                            f"{end.replace(':', '')}"
                        ),
                        event_label=f"{start}-{end}",
                        session=session,
                        trigger_minute=anchor,
                        entry_minute=anchor,
                        horizon=horizon,
                        direction=1.0,
                        magnitude_bps=float("nan"),
                        entry=entry,
                        exit_price=exit_price,
                        forward_return_bps=forward,
                        effect_bps=forward,
                    )
                )
    return rows


def _directional_anchor_events(
    sessions: Sequence[pd.DataFrame],
    family: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    family_id = str(family["family_id"])
    anchor = int(family["anchor_minute"])
    threshold = float(family["minimum_absolute_event_bps"])
    feature = str(family["direction_feature"])
    validity = str(family["validity_feature"])
    for session in sessions:
        index = _clock_index(session, anchor)
        if index is None:
            continue
        anchor_row = session.iloc[index]
        if not bool(anchor_row[validity]):
            continue
        raw_event = float(anchor_row[feature])
        event_bps = raw_event * 10_000.0
        if not np.isfinite(event_bps) or abs(event_bps) < threshold:
            continue
        direction = float(np.sign(event_bps))
        for horizon in family["horizons_minutes"]:
            horizon = int(horizon)
            if not _window_is_contiguous(session, index, horizon):
                continue
            entry = float(session.iloc[index]["open"])
            exit_price = float(session.iloc[index + horizon - 1]["close"])
            if not _valid_prices(entry, exit_price):
                continue
            forward = (exit_price / entry - 1.0) * 10_000.0
            rows.append(
                _event_row(
                    family_id=family_id,
                    hypothesis_id=f"{family_id}__{horizon}m",
                    event_label=family_id.replace("_", " "),
                    session=session,
                    trigger_minute=anchor,
                    entry_minute=anchor,
                    horizon=horizon,
                    direction=direction,
                    magnitude_bps=abs(event_bps),
                    entry=entry,
                    exit_price=exit_price,
                    forward_return_bps=forward,
                    effect_bps=direction * forward,
                )
            )
    return rows


def _range_break_events(
    sessions: Sequence[pd.DataFrame],
    family: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    family_id = str(family["family_id"])
    first_minute = int(family["first_eligible_trigger_minute"])
    last_minute = int(family["last_eligible_trigger_minute"])
    for reference in family["reference_ranges"]:
        range_id = str(reference["range_id"])
        upper_feature = str(reference["upper_feature"])
        lower_feature = str(reference["lower_feature"])
        validity_feature = str(reference["validity_feature"])
        for session in sessions:
            eligible = session.loc[
                session["rth_minute"].between(first_minute, last_minute)
            ]
            trigger_index: int | None = None
            direction = 0.0
            reference_level = float("nan")
            for index, row in eligible.iterrows():
                if not bool(row[validity_feature]):
                    continue
                upper = float(row[upper_feature])
                lower = float(row[lower_feature])
                close = float(row["close"])
                if not all(np.isfinite([upper, lower, close])):
                    continue
                if close > upper:
                    trigger_index = int(index)
                    direction = 1.0
                    reference_level = upper
                    break
                if close < lower:
                    trigger_index = int(index)
                    direction = -1.0
                    reference_level = lower
                    break
            if trigger_index is None:
                continue
            entry_index = trigger_index + 1
            if entry_index >= len(session):
                continue
            trigger_minute = int(session.iloc[trigger_index]["rth_minute"])
            entry_minute = int(session.iloc[entry_index]["rth_minute"])
            if entry_minute != trigger_minute + 1:
                continue
            trigger_close = float(session.iloc[trigger_index]["close"])
            magnitude = (
                direction * (trigger_close / reference_level - 1.0) * 10_000.0
            )
            for horizon in family["horizons_minutes"]:
                horizon = int(horizon)
                if not _window_is_contiguous(session, entry_index, horizon):
                    continue
                entry = float(session.iloc[entry_index]["open"])
                exit_price = float(
                    session.iloc[entry_index + horizon - 1]["close"]
                )
                if not _valid_prices(entry, exit_price):
                    continue
                forward = (exit_price / entry - 1.0) * 10_000.0
                rows.append(
                    _event_row(
                        family_id=family_id,
                        hypothesis_id=(
                            f"{family_id}__{range_id}__{horizon}m"
                        ),
                        event_label=f"first {range_id.replace('_', ' ')} break",
                        session=session,
                        trigger_minute=trigger_minute,
                        entry_minute=entry_minute,
                        horizon=horizon,
                        direction=direction,
                        magnitude_bps=float(magnitude),
                        entry=entry,
                        exit_price=exit_price,
                        forward_return_bps=forward,
                        effect_bps=direction * forward,
                    )
                )
    return rows


def _event_row(
    *,
    family_id: str,
    hypothesis_id: str,
    event_label: str,
    session: pd.DataFrame,
    trigger_minute: int,
    entry_minute: int,
    horizon: int,
    direction: float,
    magnitude_bps: float,
    entry: float,
    exit_price: float,
    forward_return_bps: float,
    effect_bps: float,
) -> dict[str, Any]:
    first = session.iloc[0]
    return {
        "family_id": family_id,
        "hypothesis_id": hypothesis_id,
        "event_label": event_label,
        "session_date": first["session_date"],
        "calendar_year": int(first["calendar_year"]),
        "trigger_clock_minute": int(trigger_minute),
        "entry_clock_minute": int(entry_minute),
        "horizon_minutes": int(horizon),
        "event_direction": float(direction),
        "event_magnitude_bps": float(magnitude_bps),
        "entry_price": float(entry),
        "exit_price": float(exit_price),
        "forward_return_bps": float(forward_return_bps),
        "effect_bps": float(effect_bps),
    }


def _hypothesis_row(
    family: dict[str, Any],
    hypothesis_id: str,
    label: str,
    horizon_minutes: int,
    *,
    anchor_minute: int | None = None,
    reference_range: str | None = None,
) -> dict[str, Any]:
    effect = str(family["effect"])
    return {
        "family_id": str(family["family_id"]),
        "hypothesis_id": hypothesis_id,
        "hypothesis_label": label,
        "family_description": str(family["description"]).strip(),
        "event_type": str(family["event_type"]),
        "horizon_minutes": int(horizon_minutes),
        "anchor_minute": anchor_minute,
        "reference_range": reference_range,
        "effect_definition": effect,
        "effect_interpretation": (
            "positive=raw upward return; negative=raw downward return"
            if effect == "raw_forward_return_bps"
            else "positive=continuation; negative=reversal"
        ),
        "alternative": "two_sided",
        "randomized_null": str(family["randomized_null"]),
    }


def _required_feature_columns(config: dict[str, Any]) -> tuple[str, ...]:
    columns = set(BASE_COLUMNS)
    for family in config["event_families"]:
        if family["event_type"] == "directional_anchor":
            columns.add(str(family["direction_feature"]))
            columns.add(str(family["validity_feature"]))
        elif family["event_type"] == "first_range_break":
            for reference in family["reference_ranges"]:
                columns.update(
                    {
                        str(reference["upper_feature"]),
                        str(reference["lower_feature"]),
                        str(reference["validity_feature"]),
                    }
                )
    return tuple(sorted(columns))


def _guard_development_only(features: pd.DataFrame) -> None:
    required = set(BASE_COLUMNS)
    missing = sorted(required - set(features.columns))
    if missing:
        raise ValueError(f"Event-study input is missing columns: {missing}")
    if set(features["split"].dropna().unique()) != {DEVELOPMENT_SPLIT}:
        raise PermissionError("Phase 6 event studies may use development rows only")
    if features["final_test_locked"].any():
        raise PermissionError("Locked final-test rows are prohibited in Phase 6")


def _clock_index(session: pd.DataFrame, minute: int) -> int | None:
    matches = np.flatnonzero(session["rth_minute"].to_numpy(dtype=int) == minute)
    if len(matches) != 1:
        return None
    return int(matches[0])


def _window_is_contiguous(
    session: pd.DataFrame,
    start_index: int,
    horizon: int,
) -> bool:
    end_index = start_index + int(horizon) - 1
    if start_index < 0 or horizon <= 0 or end_index >= len(session):
        return False
    minutes = session["rth_minute"].to_numpy(dtype=int)
    return int(minutes[end_index]) == int(minutes[start_index]) + horizon - 1


def _valid_prices(entry: float, exit_price: float) -> bool:
    return bool(np.isfinite(entry) and np.isfinite(exit_price) and entry > 0.0)


def _rth_label(minute: int) -> str:
    total = 9 * 60 + 30 + int(minute)
    hour, minute_of_hour = divmod(total, 60)
    return f"{hour:02d}:{minute_of_hour:02d}"


def _stable_seed(base_seed: int, *parts: str) -> int:
    payload = "|".join([str(base_seed), *map(str, parts)]).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def _validate_inputs(
    *,
    research_config: dict[str, Any],
    phase6_config: dict[str, Any],
    split_config: dict[str, Any],
    feature_path: Path,
    feature_manifest: dict[str, Any],
    phase5_manifest: dict[str, Any],
) -> None:
    if research_config["project"]["mcp_read_only"] is not True:
        raise ValueError("The project must remain MCP read-only")
    if research_config["research_period"]["exclude_current_incomplete_session"] is not True:
        raise ValueError("The current incomplete session must remain excluded")
    if phase6_config["analysis_class"] != EXPECTED_ANALYSIS_CLASS:
        raise ValueError("Unexpected Phase 6 analysis class")
    if phase6_config["data_scope"]["allowed_split"] != DEVELOPMENT_SPLIT:
        raise ValueError("Phase 6 must be development-only")
    if set(phase6_config["data_scope"]["forbidden_splits"]) != {
        "validation",
        FINAL_SPLIT,
    }:
        raise ValueError("Phase 6 forbidden split policy changed")
    if split_config["splits"][FINAL_SPLIT]["locked"] is not True:
        raise ValueError("The final-test split must remain locked")
    if not feature_path.exists():
        raise FileNotFoundError(feature_path)
    if not feature_manifest.get("gate_passed"):
        raise ValueError("Phase 4 feature gate has not passed")
    if not phase5_manifest.get("gate_passed"):
        raise ValueError("Phase 5 gate has not passed")
    if feature_manifest["artifacts"]["bar_features"]["sha256"] != sha256_file(
        feature_path
    ):
        raise ValueError("Feature file checksum does not match Phase 4 manifest")
    if phase6_config["inference"]["resampling_unit"] != "session":
        raise ValueError("Phase 6 inference must resample sessions")
    if phase6_config["inference"]["multiple_testing"]["method"] != "benjamini_hochberg":
        raise ValueError("Phase 6 must use Benjamini-Hochberg correction")
    if phase6_config["inference"]["multiple_testing"]["scope"] != "within_family":
        raise ValueError("Phase 6 BH correction must be within family")


def _validate_loaded_features(
    features: pd.DataFrame,
    feature_manifest: dict[str, Any],
) -> None:
    if features.empty:
        raise ValueError("No development feature rows were loaded")
    _guard_development_only(features)
    expected = int(
        feature_manifest["validation"]["details"]["split_row_counts"][
            DEVELOPMENT_SPLIT
        ]
    )
    if len(features) != expected:
        raise ValueError(
            f"Expected {expected:,} development rows; loaded {len(features):,}"
        )


def _verify_reproducible_outputs(
    *,
    artifacts: dict[str, pd.DataFrame],
    artifact_paths: dict[str, Path],
) -> dict[str, Any]:
    details: dict[str, Any] = {"artifacts": {}}
    with TemporaryDirectory(prefix="phase6-rebuild-", dir=ROOT) as directory:
        temporary_dir = Path(directory)
        for name, frame in artifacts.items():
            rebuilt = temporary_dir / f"{name}.parquet"
            frame.to_parquet(
                rebuilt,
                index=False,
                compression="zstd",
                engine="pyarrow",
            )
            original_hash = sha256_file(artifact_paths[name])
            rebuilt_hash = sha256_file(rebuilt)
            details["artifacts"][name] = {
                "original_sha256": original_hash,
                "rebuilt_sha256": rebuilt_hash,
                "byte_identical": original_hash == rebuilt_hash,
            }
    details["byte_identical"] = all(
        item["byte_identical"] for item in details["artifacts"].values()
    )
    return details


def _render_report(
    summary: dict[str, Any],
    ledger: pd.DataFrame,
    family_summary: pd.DataFrame,
) -> str:
    coverage = summary["coverage"]
    advancing = ledger.loc[ledger["advances_to_phase7"]]
    if advancing.empty:
        result_sentence = (
            "No preregistered event passed both the within-family BH gate and "
            "the bootstrap interval gate."
        )
    else:
        labels = ", ".join(advancing["hypothesis_label"].tolist())
        result_sentence = (
            f"{len(advancing)} preregistered event(s) passed both gates: {labels}."
        )
    family_lines = "\n".join(
        (
            f"- `{row.family_id}`: {int(row.hypotheses)} tests, "
            f"{int(row.bh_rejections)} BH rejections, "
            f"{int(row.advancing_hypotheses)} advancing."
        )
        for row in family_summary.itertuples(index=False)
    )
    advancing_lines = (
        "\n".join(
            (
                f"- `{row.hypothesis_id}`: {row.mean_effect_bps:.3f} bps "
                f"(95% bootstrap CI {row.bootstrap_ci_low:.3f} to "
                f"{row.bootstrap_ci_high:.3f}; q={row.bh_q_value:.4f}; "
                f"n={int(row.observations)})."
            )
            for row in advancing.itertuples(index=False)
        )
        if not advancing.empty
        else "- None."
    )
    return f"""# Phase 6 Event Studies

## Technical summary

The Phase 6 gate passed using the frozen development split only.
{result_sentence} The full ledger retains all {coverage['hypotheses']}
preregistered hypotheses, including failures. Validation and final-test usage
remain zero.

These are event-study screens, not strategies or evidence of tradability.
Positive directional effects mean continuation; negative effects mean reversal.

## Family-level results

{family_lines}

## Events eligible for Phase 7 consideration

{advancing_lines}

Eligibility requires at least {summary['method']['minimum_observations']}
session observations, a within-family Benjamini-Hochberg rejection, and a 95%
session-bootstrap interval that excludes zero. Phase 7 must still freeze an
unambiguous setup, next-event entry, exits, costs, and conservative fill rules.

## Scope and definitions

- Population: {coverage['development_rows']:,} one-minute development bars from
  {coverage['development_sessions']:,} usable sessions.
- Outcome: entry-bar open to the final included bar close, in basis points.
- Fixed-clock events enter at the anchor bar open.
- Opening and gap features are known at their anchor; range breaks require a
  confirming close and enter at the next bar open.
- Directional effect: event direction multiplied by forward return.
- No commissions, slippage, order simulation, stop, target, or sizing model is
  included.

## Inference and multiplicity

- Session-level percentile bootstrap with
  {summary['method']['bootstrap_replicates']:,} replicates.
- Session-level randomized nulls with
  {summary['method']['permutation_replicates']:,} replicates.
- Two-sided p-values with the plus-one correction.
- {summary['method']['multiple_testing']}.
- Deterministic hypothesis-specific random seeds.

## Limitations and robustness boundary

- Phase 5 descriptive findings and Phase 6 inference use the same development
  period. The full 13-block clock family mitigates isolated-extrema selection,
  but this is not independent out-of-sample confirmation.
- Correction is within the four preregistered families, not across every event
  definition that could have been imagined.
- One-minute bars cannot establish within-bar paths or executable fills.
- Annual cuts are diagnostics only and are not additional selection gates.
- Validation and final-test outcomes remain untouched.

## Gate evidence

{chr(10).join(f"- {'PASS' if passed else 'FAIL'}: `{name}`" for name, passed in summary['validation']['checks'].items())}

## Reproducible artifacts

- `config/phase6.yaml`
- `results/phase6/event_observations.parquet`
- `results/phase6/hypothesis_ledger.parquet`
- `results/phase6/family_summary.parquet`
- `results/phase6/hypothesis_year_stability.parquet`
- `data/manifests/phase6_manifest.json`
- `src/event_studies.py`
- `src/statistics.py`
- `tests/test_event_studies.py`
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


def _runtime_versions() -> dict[str, str]:
    packages = ["numpy", "pandas", "pyarrow", "pyyaml", "scipy"]
    versions = {"python": platform.python_version()}
    for package in packages:
        versions[package] = importlib.metadata.version(package)
    return versions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    for command in ("run", "validate"):
        command_parser = subparsers.add_parser(
            command,
            help=f"{command.capitalize()} Phase 6 preregistered event studies",
        )
        command_parser.add_argument(
            "--feature-path",
            type=Path,
            default=ROOT / "data" / "features" / "mnq_1m_features.parquet",
        )
    args = parser.parse_args()
    if args.command in {"run", "validate"}:
        summary = run_phase6(feature_path=args.feature_path)
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
