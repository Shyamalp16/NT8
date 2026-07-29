from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from src.event_studies import (
    build_event_observations,
    preregister_hypotheses,
    summarize_hypotheses,
)
from src.statistics import (
    benjamini_hochberg,
    bootstrap_mean_interval,
    direction_permutation_pvalue,
    sign_flip_permutation_pvalue,
)


ROOT = Path(__file__).resolve().parents[1]


def _config() -> dict:
    return yaml.safe_load(
        (ROOT / "config" / "phase6.yaml").read_text(encoding="utf-8")
    )


def _bars(split: str = "development") -> pd.DataFrame:
    minutes = np.arange(121, dtype=int)
    timestamps = pd.date_range(
        "2026-07-27T13:30:00Z",
        periods=len(minutes),
        freq="min",
    )
    prices = 100.0 + minutes * 0.05
    close = prices + 0.02
    close[2] = 103.0
    return pd.DataFrame(
        {
            "timestamp_utc": timestamps,
            "session_date": [date(2026, 7, 27)] * len(minutes),
            "split": [split] * len(minutes),
            "final_test_locked": [split == "final_untouched_test"] * len(minutes),
            "symbol": ["MNQU6"] * len(minutes),
            "open": prices,
            "high": np.maximum(prices, close) + 0.1,
            "low": np.minimum(prices, close) - 0.1,
            "close": close,
            "is_rth": [True] * len(minutes),
            "rth_minute": minutes,
            "calendar_year": [2026] * len(minutes),
            "overnight_valid": [True] * len(minutes),
            "rth_open_gap_from_prior_rth": [0.002] * len(minutes),
            "opening_30_valid": [True] * len(minutes),
            "opening_30_return": [0.002] * len(minutes),
            "prior_session_valid": [True] * len(minutes),
            "prior_rth_high": [102.0] * len(minutes),
            "prior_rth_low": [98.0] * len(minutes),
            "overnight_high": [110.0] * len(minutes),
            "overnight_low": [90.0] * len(minutes),
        }
    )


def test_phase6_preregistration_expands_all_families_and_clock_blocks():
    hypotheses = preregister_hypotheses(_config())

    assert len(hypotheses) == 21
    assert hypotheses["hypothesis_id"].is_unique
    assert hypotheses.groupby("family_id").size().to_dict() == {
        "opening_30_response": 2,
        "overnight_gap_response": 2,
        "reference_level_break_response": 4,
        "rth_fixed_clock_30m": 13,
    }


def test_event_extraction_uses_anchor_open_and_next_open_after_confirmed_break():
    observations = build_event_observations(_bars(), _config())

    gap = observations.loc[
        observations["hypothesis_id"] == "overnight_gap_response__30m"
    ].iloc[0]
    opening = observations.loc[
        observations["hypothesis_id"] == "opening_30_response__30m"
    ].iloc[0]
    level_break = observations.loc[
        observations["hypothesis_id"]
        == "reference_level_break_response__prior_rth_range__15m"
    ].iloc[0]

    assert gap["entry_clock_minute"] == 0
    assert gap["entry_price"] == pytest.approx(_bars().iloc[0]["open"])
    assert opening["entry_clock_minute"] == 30
    assert opening["entry_price"] == pytest.approx(_bars().iloc[30]["open"])
    assert level_break["trigger_clock_minute"] == 2
    assert level_break["entry_clock_minute"] == 3
    assert level_break["entry_price"] == pytest.approx(_bars().iloc[3]["open"])
    assert level_break["event_direction"] == 1.0
    assert level_break["effect_bps"] == pytest.approx(
        level_break["forward_return_bps"]
    )


def test_event_extraction_rejects_later_splits_and_locked_rows():
    with pytest.raises(PermissionError, match="development"):
        build_event_observations(
            _bars(split="validation"),
            _config(),
        )
    with pytest.raises(PermissionError, match="development|Locked"):
        build_event_observations(
            _bars(split="final_untouched_test"),
            _config(),
        )


def test_inference_is_deterministic_and_retains_failed_hypotheses():
    config = _config()
    config["inference"]["bootstrap_replicates"] = 200
    config["inference"]["permutation_replicates"] = 200
    hypotheses = preregister_hypotheses(config)
    observations = build_event_observations(_bars(), config)

    first = summarize_hypotheses(observations, hypotheses, config)
    second = summarize_hypotheses(observations, hypotheses, config)

    pd.testing.assert_frame_equal(first, second)
    assert len(first) == 21
    assert set(first["result"]) == {"insufficient_sample"}
    assert not first["advances_to_phase7"].any()


def test_phase6_statistical_helpers_cover_intervals_nulls_and_bh():
    values = np.array([1.0, 2.0, 3.0, 4.0])
    low, high = bootstrap_mean_interval(
        values,
        replicates=500,
        random_seed=7,
    )
    assert low <= values.mean() <= high

    sign_p_one = sign_flip_permutation_pvalue(
        values,
        replicates=500,
        random_seed=11,
    )
    sign_p_two = sign_flip_permutation_pvalue(
        values,
        replicates=500,
        random_seed=11,
    )
    assert sign_p_one == sign_p_two
    assert 0.0 < sign_p_one <= 1.0

    association_p = direction_permutation_pvalue(
        np.array([1, 1, -1, -1]),
        np.array([2.0, 1.0, -1.0, -2.0]),
        replicates=500,
        random_seed=13,
    )
    assert 0.0 < association_p <= 1.0

    q_values, rejected = benjamini_hochberg(
        [0.01, 0.04, 0.03, 0.20],
        alpha=0.05,
    )
    assert q_values == pytest.approx([0.04, 0.0533333333, 0.0533333333, 0.20])
    assert rejected.tolist() == [True, False, False, False]
