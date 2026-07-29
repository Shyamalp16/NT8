from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.pattern_scan import (
    DEVELOPMENT_SPLIT,
    SCOPE_ETH,
    SCOPE_RTH,
    build_forward_outcomes,
    build_session_timing,
    summarize_market_clock,
    summarize_turning_point_timing,
)
from src.statistics import mean_confidence_interval, wilson_interval


def _bars(split: str = DEVELOPMENT_SPLIT) -> pd.DataFrame:
    timestamps = pd.date_range(
        "2026-07-27T13:30:00Z",
        periods=6,
        freq="min",
    )
    return pd.DataFrame(
        {
            "timestamp_utc": timestamps,
            "session_date": [date(2026, 7, 27)] * 6,
            "split": [split] * 6,
            "final_test_locked": [split == "final_untouched_test"] * 6,
            "symbol": ["MNQU6"] * 6,
            "open": [100, 101, 102, 103, 104, 105],
            "high": [102, 103, 104, 105, 106, 107],
            "low": [99, 100, 101, 102, 103, 104],
            "close": [101, 102, 103, 104, 105, 106],
            "is_rth": [True] * 6,
            "session_minute": [930, 931, 932, 933, 934, 935],
            "rth_minute": [0, 1, 2, 3, 4, 5],
            "calendar_year": [2026] * 6,
            "calendar_quarter": [3] * 6,
            "calendar_weekday": [0] * 6,
            "trend_regime": ["up"] * 6,
            "volatility_regime": ["normal"] * 6,
            "volume_regime": ["normal"] * 6,
        }
    )


def test_forward_outcomes_use_entry_open_final_close_and_interval_extremes():
    outcomes = build_forward_outcomes(
        _bars(),
        scope=SCOPE_RTH,
        horizons=(1, 5),
        anchor_step_minutes=5,
    )

    one_minute = outcomes.loc[
        (outcomes["horizon_minutes"] == 1)
        & (outcomes["clock_minute"] == 0)
    ].iloc[0]
    five_minute = outcomes.loc[
        (outcomes["horizon_minutes"] == 5)
        & (outcomes["clock_minute"] == 0)
    ].iloc[0]

    assert one_minute["return_points"] == pytest.approx(1.0)
    assert one_minute["mfe_points"] == pytest.approx(2.0)
    assert one_minute["mae_points"] == pytest.approx(-1.0)
    assert five_minute["return_points"] == pytest.approx(5.0)
    assert five_minute["mfe_points"] == pytest.approx(6.0)
    assert five_minute["mae_points"] == pytest.approx(-1.0)


def test_forward_outcomes_reject_non_development_rows():
    with pytest.raises(PermissionError, match="development"):
        build_forward_outcomes(
            _bars(split="final_untouched_test"),
            scope=SCOPE_ETH,
            horizons=(1,),
            anchor_step_minutes=5,
        )


def test_forward_outcomes_drop_windows_that_cross_a_missing_minute():
    bars = _bars().drop(index=2).reset_index(drop=True)
    outcomes = build_forward_outcomes(
        bars,
        scope=SCOPE_RTH,
        horizons=(5,),
        anchor_step_minutes=1,
    )

    assert outcomes.empty


def test_market_clock_summary_has_valid_intervals_and_visible_sample_size():
    bars = pd.concat(
        [
            _bars(),
            _bars().assign(
                session_date=date(2026, 7, 28),
                timestamp_utc=lambda frame: frame["timestamp_utc"]
                + pd.Timedelta(days=1),
                close=lambda frame: frame["close"] - 2,
            ),
        ],
        ignore_index=True,
    )
    outcomes = build_forward_outcomes(
        bars,
        scope=SCOPE_RTH,
        horizons=(1,),
        anchor_step_minutes=5,
    )
    summary = summarize_market_clock(outcomes, confidence_level=0.95)

    assert set(summary["observations"]) == {2}
    assert (
        summary["mean_return_ci_low"] <= summary["mean_return_bps"]
    ).all()
    assert (
        summary["mean_return_bps"] <= summary["mean_return_ci_high"]
    ).all()
    assert (summary["positive_rate_ci_low"] >= 0).all()
    assert (summary["positive_rate_ci_high"] <= 1).all()


def test_statistical_intervals_handle_known_edge_cases():
    mean_low, mean_high = mean_confidence_interval(2.0, 1.0, 10)
    assert float(mean_low) < 2.0 < float(mean_high)

    rate_low, rate_high = wilson_interval(0, 10)
    assert float(rate_low) == pytest.approx(0.0)
    assert 0.0 < float(rate_high) < 1.0

    rate_low, rate_high = wilson_interval(10, 10)
    assert 0.0 < float(rate_low) < 1.0
    assert float(rate_high) == pytest.approx(1.0)


def test_turning_point_timing_uses_first_occurrence_and_shares_sum_to_one():
    bars = _bars()
    bars.loc[1, "high"] = 110
    bars.loc[4, "high"] = 110
    bars.loc[3, "low"] = 90
    timing = build_session_timing(bars)

    rth = timing.loc[timing["scope"] == SCOPE_RTH].iloc[0]
    assert rth["high_clock_minute"] == 1
    assert rth["low_clock_minute"] == 3
    assert rth["high_before_low"]

    distribution = summarize_turning_point_timing(
        timing,
        scope_bins={SCOPE_ETH: 60, SCOPE_RTH: 30},
        confidence_level=0.95,
    )
    sums = distribution.groupby(["scope", "extreme_type"])["share"].sum()
    assert np.allclose(sums.to_numpy(), 1.0)
