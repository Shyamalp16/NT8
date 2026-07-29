import hashlib
import json
from datetime import date

import pandas as pd

from src.data_audit import _contiguous_runs, audit_sessions
from src.normalize import normalize_response
from src.sessions import (
    cme_equity_schedule,
    expected_minutes,
    session_date_for_timestamp,
)


SPLITS = {
    "splits": {
        "development": {
            "start_date": "2021-07-28",
            "end_date": "2024-07-26",
        },
        "validation": {
            "start_date": "2024-07-29",
            "end_date": "2025-07-25",
        },
        "final_untouched_test": {
            "start_date": "2025-07-28",
            "end_date": "2026-07-27",
        },
    }
}


def test_session_assignment_uses_eastern_trade_date():
    assert session_date_for_timestamp(
        pd.Timestamp("2026-07-26T22:00:00Z")
    ) == date(2026, 7, 27)
    assert session_date_for_timestamp(
        pd.Timestamp("2026-07-27T20:59:00Z")
    ) == date(2026, 7, 27)
    assert session_date_for_timestamp(
        pd.Timestamp("2026-07-27T22:00:00Z")
    ) == date(2026, 7, 28)


def test_cme_equity_calendar_handles_dst_and_early_close():
    winter = cme_equity_schedule(date(2026, 1, 5), date(2026, 1, 5)).iloc[0]
    summer = cme_equity_schedule(date(2026, 7, 6), date(2026, 7, 6)).iloc[0]
    holiday = cme_equity_schedule(date(2026, 7, 3), date(2026, 7, 3)).iloc[0]
    mourning = cme_equity_schedule(date(2025, 1, 9), date(2025, 1, 9)).iloc[0]

    assert winter["expected_open_utc"].hour == 23
    assert summer["expected_open_utc"].hour == 22
    assert winter["expected_minutes"] == 1380
    assert summer["expected_minutes"] == 1380
    assert not winter["is_early_close"]
    assert not summer["is_early_close"]
    assert holiday["is_early_close"]
    assert holiday["expected_minutes"] == 1140
    assert mourning["calendar_override"]
    assert mourning["expected_minutes"] == 930


def test_normalization_preserves_utc_and_flags_research_session(tmp_path):
    result = {
        "structuredContent": {
            "symbol": "MNQU6",
            "barType": "Minute",
            "barSize": 1,
            "bars": [
                {
                    "timestamp": "2026-07-26T22:00:00Z",
                    "open": 100.0,
                    "high": 100.5,
                    "low": 99.75,
                    "close": 100.25,
                    "upVolume": 10,
                    "downVolume": 8,
                    "upTicks": 3,
                    "downTicks": 2,
                }
            ],
        }
    }
    payload = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    raw_path = tmp_path / "response.json"
    raw_path.write_bytes(payload)
    ledger = {
        "request_id": "request",
        "symbol": "MNQU6",
        "contract_year": 2026,
        "row_count": 1,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }

    batch = normalize_response(raw_path, ledger, SPLITS, tick_size=0.25)

    assert batch.request_audit["sha256_verified"]
    assert not batch.issues
    assert batch.frame.iloc[0]["session_date"] == date(2026, 7, 27)
    assert batch.frame.iloc[0]["split"] == "final_untouched_test"
    assert batch.frame.iloc[0]["row_valid"]


def test_complete_session_passes_and_roll_session_is_excluded():
    schedule = cme_equity_schedule(date(2026, 7, 27), date(2026, 7, 27))
    row = schedule.iloc[0]
    timestamps = expected_minutes(
        row["expected_open_utc"],
        row["expected_close_utc"],
    )
    bars = pd.DataFrame(
        {
            "timestamp_utc": timestamps,
            "timestamp_et": timestamps.tz_convert("America/New_York"),
            "session_date": date(2026, 7, 27),
            "symbol": "MNQU6",
            "open": 100.0,
            "close": 100.0,
            "row_valid": True,
        }
    )

    quality, exclusions, _ = audit_sessions(
        bars,
        research_start=date(2026, 7, 27),
        research_end=date(2026, 7, 27),
        split_config=SPLITS,
        roll_dates=set(),
    )
    assert quality.iloc[0]["quality_status"] == "pass"
    assert not exclusions

    quality, exclusions, _ = audit_sessions(
        bars,
        research_start=date(2026, 7, 27),
        research_end=date(2026, 7, 27),
        split_config=SPLITS,
        roll_dates={date(2026, 7, 27)},
    )
    assert quality.iloc[0]["quality_status"] == "excluded"
    assert exclusions[0]["reasons"] == ["roll_boundary_session"]


def test_contiguous_missing_minutes_are_grouped_deterministically():
    missing = pd.DatetimeIndex(
        [
            "2026-07-27T13:30:00Z",
            "2026-07-27T13:31:00Z",
            "2026-07-27T13:33:00Z",
        ]
    )

    runs = _contiguous_runs(missing)

    assert [(start.minute, end.minute, count) for start, end, count in runs] == [
        (30, 31, 2),
        (33, 33, 1),
    ]
