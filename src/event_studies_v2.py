"""Phase 6 v2 preregistered event studies on development data only.

This second wave evaluates two event families that are distinct from the
Phase 6 v1 directional-return tests: conditional volatility transitions and
uptick/downtick volume-pressure bursts. It never loads validation or final-test
rows and does not simulate a trading strategy.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml

from src.event_studies import (
    _artifact_profile,
    _display_path,
    _runtime_versions,
    _stable_seed,
    _verify_reproducible_outputs,
    _write_json_atomic,
    _write_parquet_atomic,
    _write_report_atomic,
)
from src.features import FINAL_SPLIT, load_analysis_features
from src.normalize import sha256_file
from src.statistics import (
    benjamini_hochberg,
    bootstrap_mean_interval,
    clustered_two_group_difference_interval,
    direction_permutation_pvalue,
    stratified_label_permutation_pvalue,
)


ROOT = Path(__file__).resolve().parents[1]
UTC = ZoneInfo("UTC")
DEVELOPMENT_SPLIT = "development"
EXPECTED_ANALYSIS_CLASS = "preregistered_second_wave_event_study_not_strategy"

BASE_COLUMNS = (
    "timestamp_utc",
    "session_date",
    "split",
    "final_test_locked",
    "open",
    "high",
    "low",
    "close",
    "is_rth",
    "rth_minute",
    "calendar_year",
    "realized_vol_30",
    "up_volume",
    "down_volume",
    "total_volume",
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
    "event_state",
    "event_direction",
    "event_value",
    "event_activity",
    "threshold_primary",
    "threshold_secondary",
    "baseline_history_observations",
    "entry_price",
    "exit_price",
    "forward_return_bps",
    "forward_abs_return_bps",
    "forward_range_bps",
    "effect_bps",
)


def run_phase6_v2(
    *,
    phase6_v2_config_path: Path = ROOT / "config" / "phase6_v2.yaml",
    preregistration_path: Path = (
        ROOT / "data" / "manifests" / "phase6_v2_preregistration.json"
    ),
    feature_path: Path = ROOT / "data" / "features" / "mnq_1m_features.parquet",
    feature_manifest_path: Path = ROOT / "data" / "manifests" / "feature_manifest.json",
    phase6_v1_ledger_path: Path = (
        ROOT / "results" / "phase6" / "hypothesis_ledger.parquet"
    ),
    results_dir: Path = ROOT / "results" / "phase6_v2",
    manifest_path: Path = ROOT / "data" / "manifests" / "phase6_v2_manifest.json",
    report_path: Path = ROOT / "reports" / "event_studies_v2_report.md",
) -> dict[str, Any]:
    """Build, validate, and persist all frozen Phase 6 v2 artifacts."""
    config = _load_yaml(phase6_v2_config_path)
    preregistration = json.loads(preregistration_path.read_text(encoding="utf-8"))
    feature_manifest = json.loads(feature_manifest_path.read_text(encoding="utf-8"))
    _validate_frozen_inputs(
        config=config,
        preregistration=preregistration,
        phase6_v2_config_path=phase6_v2_config_path,
        feature_path=feature_path,
        feature_manifest_path=feature_manifest_path,
        phase6_v1_ledger_path=phase6_v1_ledger_path,
    )

    features = load_analysis_features(
        feature_path,
        splits=(DEVELOPMENT_SPLIT,),
        columns=BASE_COLUMNS,
    )
    _guard_development_only(features)
    expected_rows = int(
        feature_manifest["validation"]["details"]["split_row_counts"][
            DEVELOPMENT_SPLIT
        ]
    )
    if len(features) != expected_rows:
        raise ValueError(
            f"Expected {expected_rows:,} development rows; loaded {len(features):,}"
        )

    hypotheses = preregister_v2_hypotheses(config)
    observations = build_v2_event_observations(features, config)
    v1_ledger = pd.read_parquet(phase6_v1_ledger_path)
    ledger, cumulative = summarize_v2_hypotheses(
        observations,
        hypotheses,
        config,
        v1_ledger,
    )
    family_summary = summarize_v2_families(ledger)
    stability = summarize_v2_year_stability(observations)
    artifacts = {
        "event_observations": observations,
        "hypothesis_ledger": ledger,
        "cumulative_hypothesis_ledger": cumulative,
        "family_summary": family_summary,
        "hypothesis_year_stability": stability,
    }
    validation = validate_v2_outputs(
        features=features,
        hypotheses=hypotheses,
        artifacts=artifacts,
        config=config,
        feature_manifest=feature_manifest,
        preregistration=preregistration,
        phase6_v2_config_path=phase6_v2_config_path,
    )
    if not validation["gate_passed"]:
        raise ValueError(
            "Phase 6 v2 validation failed: "
            + "; ".join(validation["failed_checks"])
        )

    results_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
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
            "Phase 6 v2 validation failed: "
            + "; ".join(validation["failed_checks"])
        )

    advancing = ledger.loc[ledger["advances_to_phase7"]]
    alpha = float(config["inference"]["multiple_testing"]["alpha"])
    summary: dict[str, Any] = {
        "phase": "6_v2",
        "version": config["version"],
        "analysis_class": config["analysis_class"],
        "gate_passed": True,
        "created_by": "python -m src.event_studies_v2 run",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "input_split": DEVELOPMENT_SPLIT,
        "forbidden_splits": config["data_scope"]["forbidden_splits"],
        "preregistration": {
            "config_path": _display_path(phase6_v2_config_path),
            "receipt_path": _display_path(preregistration_path),
            "registered_at_utc": preregistration["preregistered_at_utc"],
            "sha256": sha256_file(phase6_v2_config_path),
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
            "within_family_bh_rejections": int(ledger["bh_reject"].sum()),
            "cumulative_bh_rejections_among_v2": int(
                ledger["cumulative_bh_reject"].sum()
            ),
            "advancing_hypotheses": int(ledger["advances_to_phase7"].sum()),
            "validation_rows_used": 0,
            "final_test_rows_used": 0,
        },
        "method": {
            "bootstrap": "session-cluster percentile bootstrap",
            "randomized_nulls": sorted(
                ledger["randomized_null"].unique().tolist()
            ),
            "within_family_multiple_testing": (
                f"Benjamini-Hochberg within each v2 family at alpha={alpha:g}"
            ),
            "cumulative_sensitivity": (
                "Benjamini-Hochberg across 21 Phase 6 v1 plus 4 v2 hypotheses"
            ),
            "alternative": config["inference"]["alternative"],
            "bootstrap_replicates": int(
                config["inference"]["bootstrap_replicates"]
            ),
            "permutation_replicates": int(
                config["inference"]["permutation_replicates"]
            ),
        },
        "findings": {
            "advancing_hypotheses": advancing[
                [
                    "hypothesis_id",
                    "family_id",
                    "horizon_minutes",
                    "observations",
                    "session_clusters",
                    "mean_effect_bps",
                    "bootstrap_ci_low",
                    "bootstrap_ci_high",
                    "permutation_p_value",
                    "bh_q_value",
                    "cumulative_bh_q_value",
                ]
            ].to_dict(orient="records"),
            "interpretation": (
                "Volatility effects are high-state minus low-state future "
                "range. Pressure effects are direction-aligned future return. "
                "Neither is a tradability claim."
            ),
        },
        "provider_semantics": {
            "signed_pressure_burst": (
                "up_volume and down_volume are treated as provider-described "
                "uptick/downtick volume, not bid/ask aggressor volume"
            )
        },
        "source_hashes": {
            "bar_features": sha256_file(feature_path),
            "feature_manifest": sha256_file(feature_manifest_path),
            "phase6_v1_hypothesis_ledger": sha256_file(
                phase6_v1_ledger_path
            ),
            "phase6_v2_preregistration": sha256_file(
                phase6_v2_config_path
            ),
            "preregistration_receipt": sha256_file(preregistration_path),
            "pipeline_code": sha256_file(Path(__file__)),
            "statistics_code": sha256_file(ROOT / "src" / "statistics.py"),
        },
        "limitations": [
            "Both v2 families are evaluated on the development period only.",
            "The pressure feature is an uptick/downtick proxy, not true "
            "historical trade-aggressor imbalance.",
            "One-minute OHLCV cannot establish intrabar paths or fills.",
            "No costs, orders, stops, targets, sizing, or fill simulation are "
            "part of this event study.",
            "Validation and final-test outcomes remain untouched.",
        ],
        "validation": validation,
        "runtime_versions": _runtime_versions(),
        "artifacts": {},
    }
    for name, path in artifact_paths.items():
        summary["artifacts"][name] = _artifact_profile(path)
    summary["artifacts"]["phase6_v2_manifest"] = {
        "path": _display_path(manifest_path)
    }
    _write_json_atomic(manifest_path, summary)
    summary["artifacts"]["phase6_v2_manifest"].update(
        {
            "sha256": sha256_file(manifest_path),
            "bytes": manifest_path.stat().st_size,
        }
    )
    _write_report_atomic(report_path, _render_v2_report(summary, ledger))
    return summary


def preregister_v2_hypotheses(config: dict[str, Any]) -> pd.DataFrame:
    """Expand the four frozen v2 hypotheses."""
    rows: list[dict[str, Any]] = []
    for family in config["event_families"]:
        family_id = str(family["family_id"])
        for horizon in family["horizons_minutes"]:
            horizon = int(horizon)
            if family_id == "volatility_state_transition":
                label = f"high minus low volatility state, {horizon}m range"
                interpretation = (
                    "positive means high trailing volatility precedes a wider "
                    "future range than low trailing volatility"
                )
            elif family_id == "signed_pressure_burst":
                label = f"first signed pressure burst, {horizon}m response"
                interpretation = (
                    "positive means continuation in the pressure direction; "
                    "negative means exhaustion or reversal"
                )
            else:
                raise ValueError(f"Unknown Phase 6 v2 family: {family_id}")
            rows.append(
                {
                    "family_id": family_id,
                    "hypothesis_id": f"{family_id}__{horizon}m",
                    "hypothesis_label": label,
                    "family_description": str(family["description"]).strip(),
                    "event_type": str(family["event_type"]),
                    "horizon_minutes": horizon,
                    "effect_definition": str(family["effect"]),
                    "effect_interpretation": interpretation,
                    "alternative": str(config["inference"]["alternative"]),
                    "randomized_null": str(family["randomized_null"]),
                }
            )
    result = pd.DataFrame(rows)
    result.sort_values(
        ["family_id", "hypothesis_id"],
        inplace=True,
        kind="mergesort",
    )
    result.reset_index(drop=True, inplace=True)
    if len(result) != 4 or result["hypothesis_id"].duplicated().any():
        raise ValueError("Phase 6 v2 must contain exactly four unique hypotheses")
    return result


def build_v2_event_observations(
    features: pd.DataFrame,
    config: dict[str, Any],
) -> pd.DataFrame:
    """Extract the two frozen event families from development RTH bars."""
    _guard_development_only(features)
    rth = features.loc[features["is_rth"]].copy()
    rth.sort_values(
        ["session_date", "rth_minute", "timestamp_utc"],
        inplace=True,
        kind="mergesort",
    )
    if rth.duplicated(["session_date", "rth_minute"]).any():
        raise ValueError("RTH session/minute keys must be unique")
    session_frames = {
        session_date: frame.set_index("rth_minute", drop=False)
        for session_date, frame in rth.groupby("session_date", sort=True)
    }
    family_map = {
        str(family["family_id"]): family
        for family in config["event_families"]
    }
    rows = _volatility_state_events(
        rth,
        session_frames,
        family_map["volatility_state_transition"],
    )
    rows.extend(
        _signed_pressure_events(
            rth,
            session_frames,
            family_map["signed_pressure_burst"],
        )
    )
    result = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    if result.empty:
        return result
    result["session_date"] = pd.to_datetime(result["session_date"])
    result.sort_values(
        [
            "family_id",
            "hypothesis_id",
            "session_date",
            "entry_clock_minute",
        ],
        inplace=True,
        kind="mergesort",
    )
    result.reset_index(drop=True, inplace=True)
    return result


def _volatility_state_events(
    rth: pd.DataFrame,
    session_frames: dict[Any, pd.DataFrame],
    family: dict[str, Any],
) -> list[dict[str, Any]]:
    trigger_offset = int(family["trigger_offset_minutes"])
    candidates = rth.loc[
        rth["rth_minute"].isin(
            [int(value) + trigger_offset for value in family["entry_minutes"]]
        ),
        [
            "session_date",
            "calendar_year",
            "rth_minute",
            "realized_vol_30",
        ],
    ].copy()
    candidates["entry_clock_minute"] = (
        candidates["rth_minute"].astype(int) - trigger_offset
    )
    candidates.rename(
        columns={"realized_vol_30": "event_value"},
        inplace=True,
    )
    candidates = _attach_lagged_same_clock_quantile(
        candidates,
        value_column="event_value",
        clock_column="entry_clock_minute",
        history_sessions=int(family["baseline_history_sessions"]),
        minimum_history=int(family["minimum_history_sessions"]),
        quantiles={
            "threshold_primary": float(family["low_state_quantile"]),
            "threshold_secondary": float(family["high_state_quantile"]),
        },
    )
    candidates["event_state"] = np.select(
        [
            candidates["event_value"] <= candidates["threshold_primary"],
            candidates["event_value"] >= candidates["threshold_secondary"],
        ],
        ["low", "high"],
        default="",
    )
    candidates = candidates.loc[candidates["event_state"] != ""]
    rows: list[dict[str, Any]] = []
    for event in candidates.itertuples(index=False):
        session = session_frames[event.session_date]
        for horizon in family["horizons_minutes"]:
            outcome = _forward_outcome(
                session,
                entry_minute=int(event.entry_clock_minute),
                horizon=int(horizon),
            )
            if outcome is None:
                continue
            rows.append(
                {
                    "family_id": str(family["family_id"]),
                    "hypothesis_id": (
                        f"{family['family_id']}__{int(horizon)}m"
                    ),
                    "event_label": (
                        f"{event.event_state} trailing-volatility state at "
                        f"{_rth_label(int(event.entry_clock_minute))}"
                    ),
                    "session_date": event.session_date,
                    "calendar_year": int(event.calendar_year),
                    "trigger_clock_minute": int(event.rth_minute),
                    "entry_clock_minute": int(event.entry_clock_minute),
                    "horizon_minutes": int(horizon),
                    "event_state": str(event.event_state),
                    "event_direction": np.nan,
                    "event_value": float(event.event_value),
                    "event_activity": np.nan,
                    "threshold_primary": float(event.threshold_primary),
                    "threshold_secondary": float(event.threshold_secondary),
                    "baseline_history_observations": int(
                        event.baseline_history_observations
                    ),
                    **outcome,
                    "effect_bps": float(outcome["forward_range_bps"]),
                }
            )
    return rows


def _signed_pressure_events(
    rth: pd.DataFrame,
    session_frames: dict[Any, pd.DataFrame],
    family: dict[str, Any],
) -> list[dict[str, Any]]:
    trailing = int(family["trailing_minutes"])
    candidates = rth[
        [
            "session_date",
            "calendar_year",
            "rth_minute",
            family["up_feature"],
            family["down_feature"],
            family["total_feature"],
        ]
    ].copy()
    group = candidates.groupby("session_date", sort=False)
    for source, target in (
        (family["up_feature"], "trailing_up"),
        (family["down_feature"], "trailing_down"),
        (family["total_feature"], "event_activity"),
    ):
        candidates[target] = group[source].transform(
            lambda values: values.rolling(
                trailing,
                min_periods=trailing,
            ).sum()
        )
    candidates["event_value"] = np.divide(
        candidates["trailing_up"] - candidates["trailing_down"],
        candidates["event_activity"],
        out=np.full(len(candidates), np.nan, dtype=float),
        where=candidates["event_activity"].to_numpy(dtype=float) > 0.0,
    )
    candidates["absolute_pressure"] = candidates["event_value"].abs()
    candidates = candidates.loc[
        candidates["rth_minute"].between(
            int(family["first_eligible_trigger_minute"]),
            int(family["last_eligible_trigger_minute"]),
        )
    ].copy()
    candidates = _attach_lagged_same_clock_quantile(
        candidates,
        value_column="absolute_pressure",
        clock_column="rth_minute",
        history_sessions=int(family["baseline_history_sessions"]),
        minimum_history=int(family["minimum_history_sessions"]),
        quantiles={
            "threshold_primary": float(family["absolute_pressure_quantile"]),
        },
    )
    activity_baseline = _attach_lagged_same_clock_quantile(
        candidates[
            [
                "session_date",
                "rth_minute",
                "event_activity",
            ]
        ].copy(),
        value_column="event_activity",
        clock_column="rth_minute",
        history_sessions=int(family["baseline_history_sessions"]),
        minimum_history=int(family["minimum_history_sessions"]),
        quantiles={"threshold_secondary": float(family["activity_quantile"])},
    )
    candidates["threshold_secondary"] = activity_baseline[
        "threshold_secondary"
    ].to_numpy()
    candidates["activity_history_observations"] = activity_baseline[
        "baseline_history_observations"
    ].to_numpy()
    eligible = candidates.loc[
        candidates["event_value"].notna()
        & (candidates["event_value"] != 0.0)
        & (
            candidates["absolute_pressure"]
            >= candidates["threshold_primary"]
        )
        & (candidates["event_activity"] >= candidates["threshold_secondary"])
        & (
            candidates["baseline_history_observations"]
            >= int(family["minimum_history_sessions"])
        )
        & (
            candidates["activity_history_observations"]
            >= int(family["minimum_history_sessions"])
        )
    ].copy()
    eligible.sort_values(
        ["session_date", "rth_minute"],
        inplace=True,
        kind="mergesort",
    )
    eligible = eligible.groupby("session_date", sort=True).head(1)
    rows: list[dict[str, Any]] = []
    for event in eligible.itertuples(index=False):
        entry_minute = int(event.rth_minute) + 1
        session = session_frames[event.session_date]
        direction = float(np.sign(event.event_value))
        for horizon in family["horizons_minutes"]:
            outcome = _forward_outcome(
                session,
                entry_minute=entry_minute,
                horizon=int(horizon),
            )
            if outcome is None:
                continue
            rows.append(
                {
                    "family_id": str(family["family_id"]),
                    "hypothesis_id": (
                        f"{family['family_id']}__{int(horizon)}m"
                    ),
                    "event_label": (
                        f"first {'positive' if direction > 0 else 'negative'} "
                        f"pressure burst at {_rth_label(int(event.rth_minute))}"
                    ),
                    "session_date": event.session_date,
                    "calendar_year": int(event.calendar_year),
                    "trigger_clock_minute": int(event.rth_minute),
                    "entry_clock_minute": entry_minute,
                    "horizon_minutes": int(horizon),
                    "event_state": (
                        "positive_pressure"
                        if direction > 0
                        else "negative_pressure"
                    ),
                    "event_direction": direction,
                    "event_value": float(event.event_value),
                    "event_activity": float(event.event_activity),
                    "threshold_primary": float(event.threshold_primary),
                    "threshold_secondary": float(event.threshold_secondary),
                    "baseline_history_observations": int(
                        min(
                            event.baseline_history_observations,
                            event.activity_history_observations,
                        )
                    ),
                    **outcome,
                    "effect_bps": (
                        direction * float(outcome["forward_return_bps"])
                    ),
                }
            )
    return rows


def _attach_lagged_same_clock_quantile(
    frame: pd.DataFrame,
    *,
    value_column: str,
    clock_column: str,
    history_sessions: int,
    minimum_history: int,
    quantiles: dict[str, float],
) -> pd.DataFrame:
    """Attach thresholds using only strictly earlier same-clock sessions."""
    result = frame.sort_values(
        [clock_column, "session_date"],
        kind="mergesort",
    ).copy()
    grouped = result.groupby(clock_column, sort=False)[value_column]
    shifted = grouped.shift(1)
    result["baseline_history_observations"] = (
        shifted.groupby(result[clock_column], sort=False)
        .transform(
            lambda values: values.rolling(
                history_sessions,
                min_periods=1,
            ).count()
        )
        .fillna(0)
        .astype(int)
    )
    for output_column, quantile in quantiles.items():
        result[output_column] = shifted.groupby(
            result[clock_column],
            sort=False,
        ).transform(
            lambda values: values.rolling(
                history_sessions,
                min_periods=minimum_history,
            ).quantile(quantile)
        )
    return result.sort_index()


def _forward_outcome(
    session: pd.DataFrame,
    *,
    entry_minute: int,
    horizon: int,
) -> dict[str, float] | None:
    required_minutes = np.arange(entry_minute, entry_minute + horizon)
    if not np.isin(required_minutes, session.index.to_numpy()).all():
        return None
    window = session.loc[required_minutes]
    if not np.array_equal(
        window["rth_minute"].to_numpy(dtype=int),
        required_minutes,
    ):
        return None
    entry = float(window.iloc[0]["open"])
    exit_price = float(window.iloc[-1]["close"])
    high = float(window["high"].max())
    low = float(window["low"].min())
    if (
        not np.isfinite([entry, exit_price, high, low]).all()
        or min(entry, exit_price, high, low) <= 0.0
    ):
        return None
    forward = (exit_price / entry - 1.0) * 10_000.0
    return {
        "entry_price": entry,
        "exit_price": exit_price,
        "forward_return_bps": float(forward),
        "forward_abs_return_bps": float(abs(forward)),
        "forward_range_bps": float((high - low) / entry * 10_000.0),
    }


def summarize_v2_hypotheses(
    observations: pd.DataFrame,
    hypotheses: pd.DataFrame,
    config: dict[str, Any],
    phase6_v1_ledger: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply clustered inference, family BH, and cumulative BH sensitivity."""
    inference = config["inference"]
    confidence = float(inference["confidence_level"])
    bootstrap_replicates = int(inference["bootstrap_replicates"])
    permutation_replicates = int(inference["permutation_replicates"])
    base_seed = int(inference["random_seed"])
    alternative = str(inference["alternative"])
    minimum = int(inference["minimum_observations"])
    minimum_group = int(inference["minimum_group_observations"])
    alpha = float(inference["multiple_testing"]["alpha"])
    rows: list[dict[str, Any]] = []
    for hypothesis in hypotheses.itertuples(index=False):
        sample = observations.loc[
            observations["hypothesis_id"] == hypothesis.hypothesis_id
        ].copy()
        n = int(len(sample))
        clusters = int(sample["session_date"].nunique()) if n else 0
        if hypothesis.family_id == "volatility_state_transition" and n:
            group = (sample["event_state"] == "high").astype(int).to_numpy()
            values = sample["forward_range_bps"].to_numpy(dtype=float)
            high_count = int(np.count_nonzero(group == 1))
            low_count = int(np.count_nonzero(group == 0))
            high_mean = float(values[group == 1].mean())
            low_mean = float(values[group == 0].mean())
            mean_effect = high_mean - low_mean
            median_effect = float(
                np.median(values[group == 1]) - np.median(values[group == 0])
            )
            ci_low, ci_high = clustered_two_group_difference_interval(
                values,
                group,
                sample["session_date"].to_numpy(),
                confidence_level=confidence,
                replicates=bootstrap_replicates,
                random_seed=_stable_seed(
                    base_seed,
                    hypothesis.hypothesis_id,
                    "bootstrap",
                ),
            )
            p_value = stratified_label_permutation_pvalue(
                values,
                group,
                sample["entry_clock_minute"].to_numpy(),
                replicates=permutation_replicates,
                random_seed=_stable_seed(
                    base_seed,
                    hypothesis.hypothesis_id,
                    "permutation",
                ),
                alternative=alternative,
            )
            standard_deviation = float("nan")
            positive_rate = float("nan")
            group_sufficient = (
                high_count >= minimum_group and low_count >= minimum_group
            )
        elif n:
            effects = sample["effect_bps"].to_numpy(dtype=float)
            high_count = low_count = 0
            high_mean = low_mean = float("nan")
            mean_effect = float(effects.mean())
            median_effect = float(np.median(effects))
            standard_deviation = (
                float(np.std(effects, ddof=1)) if n >= 2 else float("nan")
            )
            positive_rate = float(np.mean(effects > 0.0))
            ci_low, ci_high = bootstrap_mean_interval(
                effects,
                confidence_level=confidence,
                replicates=bootstrap_replicates,
                random_seed=_stable_seed(
                    base_seed,
                    hypothesis.hypothesis_id,
                    "bootstrap",
                ),
            )
            p_value = direction_permutation_pvalue(
                sample["event_direction"].to_numpy(dtype=float),
                sample["forward_return_bps"].to_numpy(dtype=float),
                replicates=permutation_replicates,
                random_seed=_stable_seed(
                    base_seed,
                    hypothesis.hypothesis_id,
                    "permutation",
                ),
                alternative=alternative,
            )
            group_sufficient = True
        else:
            high_count = low_count = 0
            high_mean = low_mean = mean_effect = median_effect = float("nan")
            standard_deviation = positive_rate = float("nan")
            ci_low = ci_high = p_value = float("nan")
            group_sufficient = False
        row = hypothesis._asdict()
        row.update(
            {
                "observations": n,
                "session_clusters": clusters,
                "years": (
                    int(sample["calendar_year"].nunique()) if n else 0
                ),
                "first_session": (
                    pd.Timestamp(sample["session_date"].min())
                    if n
                    else pd.NaT
                ),
                "last_session": (
                    pd.Timestamp(sample["session_date"].max())
                    if n
                    else pd.NaT
                ),
                "high_state_observations": high_count,
                "low_state_observations": low_count,
                "high_state_mean_outcome_bps": high_mean,
                "low_state_mean_outcome_bps": low_mean,
                "mean_effect_bps": mean_effect,
                "median_effect_bps": median_effect,
                "standard_deviation_bps": standard_deviation,
                "positive_effect_rate": positive_rate,
                "bootstrap_ci_low": ci_low,
                "bootstrap_ci_high": ci_high,
                "permutation_p_value": p_value,
                "group_sample_sufficient": bool(group_sufficient),
                "sample_sufficient": bool(
                    n >= minimum
                    and clusters >= minimum
                    and group_sufficient
                ),
            }
        )
        rows.append(row)
    ledger = pd.DataFrame(rows)
    ledger["bh_q_value"] = np.nan
    ledger["bh_reject"] = False
    for _, positions in ledger.groupby("family_id", sort=True).groups.items():
        position_array = np.asarray(list(positions), dtype=int)
        q_values, rejected = benjamini_hochberg(
            ledger.loc[
                position_array,
                "permutation_p_value",
            ].to_numpy(dtype=float),
            alpha=alpha,
        )
        ledger.loc[position_array, "bh_q_value"] = q_values
        ledger.loc[position_array, "bh_reject"] = rejected
    ledger["bh_reject"] = (
        ledger["bh_reject"].astype(bool)
        & ledger["sample_sufficient"].astype(bool)
    )

    cumulative = pd.concat(
        [
            phase6_v1_ledger[
                ["family_id", "hypothesis_id", "permutation_p_value"]
            ].assign(wave="phase6_v1"),
            ledger[
                ["family_id", "hypothesis_id", "permutation_p_value"]
            ].assign(wave="phase6_v2"),
        ],
        ignore_index=True,
    )
    cumulative_q, cumulative_reject = benjamini_hochberg(
        cumulative["permutation_p_value"].to_numpy(dtype=float),
        alpha=alpha,
    )
    cumulative["cumulative_bh_q_value"] = cumulative_q
    cumulative["cumulative_bh_reject"] = cumulative_reject
    cumulative.sort_values(
        ["wave", "family_id", "hypothesis_id"],
        inplace=True,
        kind="mergesort",
    )
    cumulative.reset_index(drop=True, inplace=True)
    v2_cumulative = cumulative.loc[
        cumulative["wave"] == "phase6_v2",
        ["hypothesis_id", "cumulative_bh_q_value", "cumulative_bh_reject"],
    ]
    ledger = ledger.merge(
        v2_cumulative,
        on="hypothesis_id",
        validate="one_to_one",
    )
    ledger["cumulative_bh_reject"] = (
        ledger["cumulative_bh_reject"].astype(bool)
        & ledger["sample_sufficient"].astype(bool)
    )
    ledger["bootstrap_ci_excludes_zero"] = (
        (ledger["bootstrap_ci_low"] > 0.0)
        | (ledger["bootstrap_ci_high"] < 0.0)
    ) & ledger["sample_sufficient"]
    ledger["advances_to_phase7"] = (
        ledger["bh_reject"]
        & ledger["cumulative_bh_reject"]
        & ledger["bootstrap_ci_excludes_zero"]
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
    return ledger, cumulative


def summarize_v2_families(ledger: pd.DataFrame) -> pd.DataFrame:
    """Retain family-level pass and failure counts."""
    rows: list[dict[str, Any]] = []
    for family_id, group in ledger.groupby("family_id", sort=True):
        rows.append(
            {
                "family_id": family_id,
                "hypotheses": int(len(group)),
                "sample_sufficient_hypotheses": int(
                    group["sample_sufficient"].sum()
                ),
                "within_family_bh_rejections": int(group["bh_reject"].sum()),
                "cumulative_bh_rejections": int(
                    group["cumulative_bh_reject"].sum()
                ),
                "advancing_hypotheses": int(
                    group["advances_to_phase7"].sum()
                ),
                "minimum_p_value": float(group["permutation_p_value"].min()),
                "minimum_within_family_q_value": float(
                    group["bh_q_value"].min()
                ),
                "minimum_cumulative_q_value": float(
                    group["cumulative_bh_q_value"].min()
                ),
                "minimum_observations": int(group["observations"].min()),
                "maximum_observations": int(group["observations"].max()),
            }
        )
    return pd.DataFrame(rows)


def summarize_v2_year_stability(observations: pd.DataFrame) -> pd.DataFrame:
    """Describe annual effect signs without creating additional gates."""
    rows: list[dict[str, Any]] = []
    for (family_id, hypothesis_id, year), group in observations.groupby(
        ["family_id", "hypothesis_id", "calendar_year"],
        sort=True,
    ):
        if family_id == "volatility_state_transition":
            high = group.loc[
                group["event_state"] == "high",
                "forward_range_bps",
            ]
            low = group.loc[
                group["event_state"] == "low",
                "forward_range_bps",
            ]
            effect = (
                float(high.mean() - low.mean())
                if not high.empty and not low.empty
                else float("nan")
            )
        else:
            effect = float(group["effect_bps"].mean())
        rows.append(
            {
                "family_id": family_id,
                "hypothesis_id": hypothesis_id,
                "calendar_year": int(year),
                "observations": int(len(group)),
                "session_clusters": int(group["session_date"].nunique()),
                "mean_effect_bps": effect,
            }
        )
    return pd.DataFrame(rows)


def validate_v2_outputs(
    *,
    features: pd.DataFrame,
    hypotheses: pd.DataFrame,
    artifacts: dict[str, pd.DataFrame],
    config: dict[str, Any],
    feature_manifest: dict[str, Any],
    preregistration: dict[str, Any],
    phase6_v2_config_path: Path,
) -> dict[str, Any]:
    """Run structural, temporal, multiplicity, and isolation checks."""
    observations = artifacts["event_observations"]
    ledger = artifacts["hypothesis_ledger"]
    cumulative = artifacts["cumulative_hypothesis_ledger"]
    family_summary = artifacts["family_summary"]
    expected_rows = int(
        feature_manifest["validation"]["details"]["split_row_counts"][
            DEVELOPMENT_SPLIT
        ]
    )
    pressure = observations.loc[
        observations["family_id"] == "signed_pressure_burst"
    ]
    volatility = observations.loc[
        observations["family_id"] == "volatility_state_transition"
    ]
    valid_inference = ledger.loc[ledger["sample_sufficient"]]
    advancement_expected = (
        ledger["bh_reject"]
        & ledger["cumulative_bh_reject"]
        & ledger["bootstrap_ci_excludes_zero"]
    )
    checks = {
        "preregistration_hash_matches_receipt": (
            sha256_file(phase6_v2_config_path)
            == preregistration["source_hashes"]["phase6_v2_config"]
        ),
        "preregistered_before_outcomes": (
            config["preregistered_before_phase6_v2_outcomes"] is True
            and preregistration["outcomes_computed_at_registration"] is False
        ),
        "development_split_only": (
            set(features["split"].dropna().unique()) == {DEVELOPMENT_SPLIT}
        ),
        "locked_final_rows_absent": (
            not features["final_test_locked"].any()
            and FINAL_SPLIT not in set(features["split"].dropna().unique())
        ),
        "development_row_count_preserved": len(features) == expected_rows,
        "exactly_four_registered_hypotheses": (
            len(hypotheses) == 4
            and hypotheses["hypothesis_id"].is_unique
            and set(ledger["hypothesis_id"])
            == set(hypotheses["hypothesis_id"])
        ),
        "only_registered_observations": set(
            observations["hypothesis_id"]
        ).issubset(set(hypotheses["hypothesis_id"])),
        "next_bar_entry_enforced": (
            (
                observations["entry_clock_minute"]
                == observations["trigger_clock_minute"] + 1
            ).all()
        ),
        "strictly_lagged_minimum_history_enforced": (
            observations["baseline_history_observations"]
            >= min(
                int(family["minimum_history_sessions"])
                for family in config["event_families"]
            )
        ).all(),
        "volatility_state_thresholds_enforced": (
            (
                (
                    (volatility["event_state"] == "low")
                    & (
                        volatility["event_value"]
                        <= volatility["threshold_primary"]
                    )
                )
                | (
                    (volatility["event_state"] == "high")
                    & (
                        volatility["event_value"]
                        >= volatility["threshold_secondary"]
                    )
                )
            ).all()
        ),
        "pressure_thresholds_enforced": (
            (
                pressure["event_value"].abs()
                >= pressure["threshold_primary"]
            ).all()
            and (
                pressure["event_activity"]
                >= pressure["threshold_secondary"]
            ).all()
        ),
        "first_pressure_event_per_session": (
            not pressure.duplicated(
                ["hypothesis_id", "session_date"]
            ).any()
        ),
        "volatility_clock_events_unique": (
            not volatility.duplicated(
                [
                    "hypothesis_id",
                    "session_date",
                    "entry_clock_minute",
                ]
            ).any()
        ),
        "forward_outcomes_finite": np.isfinite(
            observations[
                [
                    "entry_price",
                    "exit_price",
                    "forward_return_bps",
                    "forward_range_bps",
                    "effect_bps",
                ]
            ].to_numpy(dtype=float)
        ).all(),
        "family_summary_complete": (
            family_summary.set_index("family_id")["hypotheses"].to_dict()
            == ledger.groupby("family_id").size().to_dict()
        ),
        "sample_sizes_visible": (
            (ledger["observations"] >= 0)
            & (ledger["session_clusters"] >= 0)
        ).all(),
        "bootstrap_intervals_valid": (
            (
                valid_inference["bootstrap_ci_low"]
                <= valid_inference["mean_effect_bps"]
            ).all()
            and (
                valid_inference["mean_effect_bps"]
                <= valid_inference["bootstrap_ci_high"]
            ).all()
        ),
        "permutation_p_values_valid": valid_inference[
            "permutation_p_value"
        ].between(0.0, 1.0).all(),
        "within_family_q_values_valid": valid_inference[
            "bh_q_value"
        ].between(0.0, 1.0).all(),
        "cumulative_q_values_valid": (
            cumulative["cumulative_bh_q_value"].between(0.0, 1.0).all()
            and len(cumulative) == 25
            and (cumulative["wave"] == "phase6_v1").sum() == 21
            and (cumulative["wave"] == "phase6_v2").sum() == 4
        ),
        "advancement_rule_enforced": (
            ledger["advances_to_phase7"] == advancement_expected
        ).all(),
        "analysis_is_event_study_not_strategy": (
            config["analysis_class"] == EXPECTED_ANALYSIS_CLASS
            and config["outcome_semantics"]["no_costs_or_fill_simulation"]
            is True
            and config["reporting"][
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
            "observed_splits": sorted(
                features["split"].dropna().unique().tolist()
            ),
            "hypotheses": int(len(ledger)),
            "families": int(ledger["family_id"].nunique()),
            "event_observations": int(len(observations)),
            "sample_sufficient_hypotheses": int(
                ledger["sample_sufficient"].sum()
            ),
            "within_family_bh_rejections": int(ledger["bh_reject"].sum()),
            "cumulative_bh_rejections_among_v2": int(
                ledger["cumulative_bh_reject"].sum()
            ),
            "advancing_hypotheses": int(
                ledger["advances_to_phase7"].sum()
            ),
            "validation_rows_used": 0,
            "final_test_rows_used": 0,
        },
    }


def _validate_frozen_inputs(
    *,
    config: dict[str, Any],
    preregistration: dict[str, Any],
    phase6_v2_config_path: Path,
    feature_path: Path,
    feature_manifest_path: Path,
    phase6_v1_ledger_path: Path,
) -> None:
    if config["analysis_class"] != EXPECTED_ANALYSIS_CLASS:
        raise ValueError("Unexpected Phase 6 v2 analysis class")
    if config["data_scope"]["allowed_split"] != DEVELOPMENT_SPLIT:
        raise ValueError("Phase 6 v2 must be development-only")
    if set(config["data_scope"]["forbidden_splits"]) != {
        "validation",
        FINAL_SPLIT,
    }:
        raise ValueError("Both later splits must remain forbidden")
    if config["inference"]["resampling_unit"] != "session_cluster":
        raise ValueError("Phase 6 v2 must resample session clusters")
    if (
        config["inference"]["multiple_testing"]["method"]
        != "benjamini_hochberg"
    ):
        raise ValueError("Phase 6 v2 must use Benjamini-Hochberg correction")
    expected_hashes = preregistration["source_hashes"]
    observed_hashes = {
        "phase6_v2_config": sha256_file(phase6_v2_config_path),
        "feature_manifest": sha256_file(feature_manifest_path),
        "bar_features": sha256_file(feature_path),
        "phase6_v1_hypothesis_ledger": sha256_file(
            phase6_v1_ledger_path
        ),
    }
    mismatches = [
        name
        for name, value in observed_hashes.items()
        if expected_hashes.get(name) != value
    ]
    if mismatches:
        raise ValueError(
            "Frozen preregistration input checksum mismatch: "
            + ", ".join(mismatches)
        )
    preregister_v2_hypotheses(config)


def _guard_development_only(features: pd.DataFrame) -> None:
    observed_splits = set(features["split"].dropna().unique())
    if observed_splits != {DEVELOPMENT_SPLIT}:
        raise PermissionError(
            "Phase 6 v2 event studies may use development rows only"
        )
    if features["final_test_locked"].any():
        raise PermissionError("Locked final-test rows are forbidden")


def _render_v2_report(
    summary: dict[str, Any],
    ledger: pd.DataFrame,
) -> str:
    coverage = summary["coverage"]
    result_lines = []
    for row in ledger.sort_values(
        ["bh_q_value", "permutation_p_value"],
        kind="mergesort",
    ).itertuples(index=False):
        result_lines.append(
            "- `{}`: effect {:.3f} bps, 95% cluster CI [{:.3f}, {:.3f}], "
            "p={:.6f}, within-family q={:.6f}, cumulative q={:.6f}, "
            "n={} observations / {} sessions — {}.".format(
                row.hypothesis_id,
                row.mean_effect_bps,
                row.bootstrap_ci_low,
                row.bootstrap_ci_high,
                row.permutation_p_value,
                row.bh_q_value,
                row.cumulative_bh_q_value,
                row.observations,
                row.session_clusters,
                row.result.replace("_", " "),
            )
        )
    advance_text = (
        "At least one frozen v2 hypothesis cleared every gate."
        if coverage["advancing_hypotheses"]
        else "No frozen v2 hypothesis cleared every gate."
    )
    checks = "\n".join(
        f"- {'PASS' if passed else 'FAIL'}: `{name}`"
        for name, passed in summary["validation"]["checks"].items()
    )
    return f"""# Phase 6 v2 Event Studies

## Decision

{advance_text} The ledger retains all four tests, including failures.
Validation and final-test usage remain zero. These are event-study screens, not
strategies or evidence of tradability.

## Complete hypothesis ledger

{chr(10).join(result_lines)}

## What is genuinely new

- **Volatility-state transition:** conditions on an extreme, strictly lagged
  same-clock volatility state and measures future range, not mean direction.
- **Signed pressure burst:** conditions on the first extreme five-minute
  uptick/downtick volume-pressure proxy and measures direction-aligned return.
  The proxy is not historical bid/ask aggressor imbalance.

## Frozen inference and advancement gate

- Session-cluster percentile bootstrap: 5,000 replicates.
- Randomized nulls: 5,000 replicates with two-sided plus-one p-values.
- Primary correction: Benjamini-Hochberg within each new family.
- Sensitivity correction: Benjamini-Hochberg across all 21 v1 and 4 v2 tests.
- Advancement requires minimum samples, both BH gates, and a cluster-bootstrap
  interval excluding zero.

## Gate evidence

{checks}

## Scope boundary

- Development bars: {coverage['development_rows']:,} across
  {coverage['development_sessions']:,} sessions.
- Event observations: {coverage['event_observations']:,}.
- Validation rows used: 0.
- Final-test rows used: 0.
- No commissions, slippage, orders, stops, targets, sizing, or fill simulation.
"""


def _rth_label(minute: int) -> str:
    total_minutes = 9 * 60 + 30 + int(minute)
    return f"{(total_minutes // 60) % 24:02d}:{total_minutes % 60:02d}"


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument(
        "--feature-path",
        type=Path,
        default=ROOT / "data" / "features" / "mnq_1m_features.parquet",
    )
    args = parser.parse_args()
    if args.command == "run":
        summary = run_phase6_v2(feature_path=args.feature_path)
        print(json.dumps(summary["coverage"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
