from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.features import (
    FINAL_SPLIT,
    FeatureSpec,
    _assemble_session_features,
    build_feature_catalog,
    load_analysis_features,
    validate_feature_specs,
)


ROOT = Path(__file__).resolve().parents[1]
FEATURE_CONFIG = yaml.safe_load(
    (ROOT / "config" / "features.yaml").read_text(encoding="utf-8")
)


def _quality_rows() -> pd.DataFrame:
    dates = [
        date(2026, 7, 20),
        date(2026, 7, 21),
        date(2026, 7, 22),
        date(2026, 7, 23),
    ]
    opens = pd.to_datetime(
        [f"{value - pd.Timedelta(days=1)} 22:00:00+00:00" for value in dates],
        utc=True,
    )
    closes = opens + pd.Timedelta(hours=23)
    return pd.DataFrame(
        {
            "session_date": dates,
            "split": ["development"] * 4,
            "session_usable": [True, False, True, True],
            "expected_session": [True] * 4,
            "is_early_close": [False] * 4,
            "expected_open_utc": opens,
            "expected_close_utc": closes,
        }
    )


def _aggregates() -> pd.DataFrame:
    rows = []
    for index, session_date in enumerate(_quality_rows()["session_date"]):
        base = 100.0 + index
        row = {
            "session_date": session_date,
            "full_bars": 1380,
            "full_open": base,
            "full_high": base + 5,
            "full_low": base - 5,
            "full_close": base + 1,
            "full_volume": 1000,
            "full_ticks": 500,
            "overnight_bars": 930,
            "overnight_open": base,
            "overnight_high": base + 2,
            "overnight_low": base - 2,
            "overnight_close": base + 0.5,
            "overnight_volume": 400,
            "rth_bars": 390,
            "rth_open": base + 0.5,
            "rth_high": base + 4,
            "rth_low": base - 3,
            "rth_close": base + 1,
            "rth_volume": 600,
        }
        for window in FEATURE_CONFIG["windows"]["opening_minutes"]:
            row.update(
                {
                    f"opening_{window}_bars": window,
                    f"opening_{window}_open": base + 0.5,
                    f"opening_{window}_high": base + 1.5,
                    f"opening_{window}_low": base,
                    f"opening_{window}_close": base + 1,
                    f"opening_{window}_volume": window * 10,
                }
            )
        rows.append(row)
    return pd.DataFrame(rows)


def test_catalog_rejects_deliberate_future_feature():
    specs = build_feature_catalog(FEATURE_CONFIG)
    validate_feature_specs(specs)
    leaked = FeatureSpec(
        name="future_close",
        family="volatility",
        dtype="float64",
        definition="future close",
        availability_column="bar_available_at_utc",
        source_window="next session",
        uses_future_data=True,
    )

    with pytest.raises(ValueError, match="future data is prohibited"):
        validate_feature_specs([*specs, leaked])


def test_excluded_immediate_predecessor_is_not_skipped():
    features = _assemble_session_features(
        _quality_rows(),
        _aggregates(),
        FEATURE_CONFIG,
    ).set_index("session_date")

    after_exclusion = features.loc[date(2026, 7, 22)]
    assert not after_exclusion["prior_session_valid"]
    assert pd.isna(after_exclusion["prior_rth_high"])
    assert not after_exclusion["overnight_valid"]
    assert pd.isna(after_exclusion["overnight_high"])

    next_session = features.loc[date(2026, 7, 23)]
    assert next_session["prior_session_valid"]
    assert next_session["prior_rth_high"] == pytest.approx(106.0)
    assert next_session["overnight_valid"]


def test_final_split_loader_requires_explicit_unlock(tmp_path):
    path = tmp_path / "features.parquet"
    pd.DataFrame(
        {
            "timestamp_utc": pd.to_datetime(
                ["2026-07-27T13:30:00Z"], utc=True
            ),
            "split": [FINAL_SPLIT],
        }
    ).to_parquet(path, index=False)

    with pytest.raises(PermissionError, match="locked"):
        load_analysis_features(
            path,
            splits=(FINAL_SPLIT,),
            columns=("timestamp_utc", "split"),
        )

    unlocked = load_analysis_features(
        path,
        splits=(FINAL_SPLIT,),
        final_evaluation_unlocked=True,
        columns=("timestamp_utc", "split"),
    )
    assert len(unlocked) == 1


def test_catalog_has_every_required_family_and_availability():
    specs = build_feature_catalog(FEATURE_CONFIG)

    assert {spec.family for spec in specs} == {
        "calendar",
        "prior_session",
        "overnight",
        "opening",
        "level",
        "volatility",
        "regime",
    }
    assert all(spec.availability_column for spec in specs)
    assert all(not spec.uses_future_data for spec in specs)
