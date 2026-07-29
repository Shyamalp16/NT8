from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from src.event_studies_v2 import (
    EVENT_COLUMNS,
    _attach_lagged_same_clock_quantile,
    build_v2_event_observations,
    preregister_v2_hypotheses,
    summarize_v2_hypotheses,
)
from src.statistics import (
    clustered_two_group_difference_interval,
    stratified_label_permutation_pvalue,
)


ROOT = Path(__file__).resolve().parents[1]


def _config() -> dict:
    config = yaml.safe_load(
        (ROOT / "config" / "phase6_v2.yaml").read_text(encoding="utf-8")
    )
    config["inference"]["bootstrap_replicates"] = 100
    config["inference"]["permutation_replicates"] = 100
    config["inference"]["minimum_observations"] = 5
    config["inference"]["minimum_group_observations"] = 5
    for family in config["event_families"]:
        family["baseline_history_sessions"] = 10
        family["minimum_history_sessions"] = 5
    return config


def _bars(split: str = "development") -> pd.DataFrame:
    frames = []
    start = date(2026, 1, 2)
    for session_index in range(12):
        minutes = np.arange(361, dtype=int)
        session_date = start + timedelta(days=session_index)
        timestamps = pd.date_range(
            f"{session_date.isoformat()}T14:30:00Z",
            periods=len(minutes),
            freq="min",
        )
        price = 100.0 + session_index * 0.1 + minutes * 0.001
        pressure = 0.10 + session_index * 0.02
        total = np.full(len(minutes), 100.0)
        up = total * (1.0 + pressure) / 2.0
        down = total - up
        frames.append(
            pd.DataFrame(
                {
                    "timestamp_utc": timestamps,
                    "session_date": [session_date] * len(minutes),
                    "split": [split] * len(minutes),
                    "final_test_locked": [
                        split == "final_untouched_test"
                    ]
                    * len(minutes),
                    "open": price,
                    "high": price + 0.05,
                    "low": price - 0.05,
                    "close": price + 0.01,
                    "is_rth": [True] * len(minutes),
                    "rth_minute": minutes,
                    "calendar_year": [2026] * len(minutes),
                    "realized_vol_30": [
                        0.001 + session_index * 0.0001
                    ]
                    * len(minutes),
                    "up_volume": up,
                    "down_volume": down,
                    "total_volume": total,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def test_v2_preregistration_contains_only_four_new_hypotheses():
    hypotheses = preregister_v2_hypotheses(_config())

    assert len(hypotheses) == 4
    assert hypotheses["hypothesis_id"].is_unique
    assert hypotheses.groupby("family_id").size().to_dict() == {
        "signed_pressure_burst": 2,
        "volatility_state_transition": 2,
    }


def test_lagged_same_clock_thresholds_do_not_use_current_or_future_values():
    frame = pd.DataFrame(
        {
            "session_date": pd.date_range("2026-01-01", periods=8),
            "clock": [60] * 8,
            "value": np.arange(8, dtype=float),
        }
    )
    first = _attach_lagged_same_clock_quantile(
        frame,
        value_column="value",
        clock_column="clock",
        history_sessions=4,
        minimum_history=2,
        quantiles={"threshold": 0.5},
    )
    changed = frame.copy()
    changed.loc[6:, "value"] = 10_000.0
    second = _attach_lagged_same_clock_quantile(
        changed,
        value_column="value",
        clock_column="clock",
        history_sessions=4,
        minimum_history=2,
        quantiles={"threshold": 0.5},
    )

    assert np.isnan(first.loc[0, "threshold"])
    assert first.loc[2, "threshold"] == pytest.approx(0.5)
    pd.testing.assert_series_equal(
        first.loc[:6, "threshold"],
        second.loc[:6, "threshold"],
    )


def test_v2_events_enter_next_bar_and_keep_only_first_pressure_burst():
    observations = build_v2_event_observations(_bars(), _config())

    assert not observations.empty
    assert (
        observations["entry_clock_minute"]
        == observations["trigger_clock_minute"] + 1
    ).all()
    pressure = observations.loc[
        observations["family_id"] == "signed_pressure_burst"
    ]
    assert not pressure.duplicated(["hypothesis_id", "session_date"]).any()
    assert set(pressure["trigger_clock_minute"]) == {30}
    assert (pressure["event_value"].abs() >= pressure["threshold_primary"]).all()
    assert (
        pressure["event_activity"] >= pressure["threshold_secondary"]
    ).all()


def test_v2_extraction_rejects_later_splits():
    with pytest.raises(PermissionError, match="development"):
        build_v2_event_observations(_bars("validation"), _config())
    with pytest.raises(PermissionError, match="development|Locked"):
        build_v2_event_observations(
            _bars("final_untouched_test"),
            _config(),
        )


def test_clustered_inference_and_cumulative_bh_are_deterministic():
    config = _config()
    hypotheses = preregister_v2_hypotheses(config)
    rows = []
    sessions = pd.date_range("2026-01-01", periods=20)
    for hypothesis in hypotheses.itertuples(index=False):
        for index, session_date in enumerate(sessions):
            if hypothesis.family_id == "volatility_state_transition":
                state = "low" if index < 10 else "high"
                direction = np.nan
                forward_range = 10.0 if state == "low" else 20.0
                forward_return = 1.0
                effect = forward_range
            else:
                state = "positive_pressure" if index % 2 == 0 else "negative_pressure"
                direction = 1.0 if index % 2 == 0 else -1.0
                forward_return = direction * 2.0
                forward_range = 5.0
                effect = 2.0
            rows.append(
                {
                    "family_id": hypothesis.family_id,
                    "hypothesis_id": hypothesis.hypothesis_id,
                    "event_label": "synthetic",
                    "session_date": session_date,
                    "calendar_year": 2026,
                    "trigger_clock_minute": 59,
                    "entry_clock_minute": 60,
                    "horizon_minutes": hypothesis.horizon_minutes,
                    "event_state": state,
                    "event_direction": direction,
                    "event_value": 1.0,
                    "event_activity": 100.0,
                    "threshold_primary": 0.5,
                    "threshold_secondary": 0.5,
                    "baseline_history_observations": 10,
                    "entry_price": 100.0,
                    "exit_price": 100.01,
                    "forward_return_bps": forward_return,
                    "forward_abs_return_bps": abs(forward_return),
                    "forward_range_bps": forward_range,
                    "effect_bps": effect,
                }
            )
    observations = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    v1 = pd.DataFrame(
        {
            "family_id": ["v1"] * 21,
            "hypothesis_id": [f"v1_{index}" for index in range(21)],
            "permutation_p_value": np.linspace(0.1, 0.9, 21),
        }
    )

    first_ledger, first_cumulative = summarize_v2_hypotheses(
        observations,
        hypotheses,
        config,
        v1,
    )
    second_ledger, second_cumulative = summarize_v2_hypotheses(
        observations,
        hypotheses,
        config,
        v1,
    )

    pd.testing.assert_frame_equal(first_ledger, second_ledger)
    pd.testing.assert_frame_equal(first_cumulative, second_cumulative)
    assert len(first_cumulative) == 25
    assert first_ledger["sample_sufficient"].all()


def test_clustered_statistical_helpers_are_seed_stable():
    values = np.array([1.0, 2.0, 4.0, 5.0, 2.0, 6.0])
    groups = np.array([0, 0, 1, 1, 0, 1])
    clusters = np.array(["a", "a", "a", "b", "b", "b"])
    strata = np.array([1, 2, 1, 2, 1, 2])

    first_ci = clustered_two_group_difference_interval(
        values,
        groups,
        clusters,
        replicates=200,
        random_seed=9,
    )
    second_ci = clustered_two_group_difference_interval(
        values,
        groups,
        clusters,
        replicates=200,
        random_seed=9,
    )
    first_p = stratified_label_permutation_pvalue(
        values,
        groups,
        strata,
        replicates=200,
        random_seed=10,
    )
    second_p = stratified_label_permutation_pvalue(
        values,
        groups,
        strata,
        replicates=200,
        random_seed=10,
    )

    assert first_ci == second_ci
    assert first_ci[0] <= first_ci[1]
    assert first_p == second_p
    assert 0.0 < first_p <= 1.0
