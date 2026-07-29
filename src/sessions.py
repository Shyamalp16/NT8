"""CME Globex Equity session calendar and timezone helpers.

Provider timestamps are kept in UTC.  Session labels and analysis windows use
daylight-saving-aware America/New_York time.  MNQ trades from 18:00 Eastern on
the prior calendar day through 17:00 Eastern on the session date, with
product-specific holiday closes supplied by pandas-market-calendars.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_market_calendars as market_calendars


UTC = ZoneInfo("UTC")
EASTERN = ZoneInfo("America/New_York")
CME_EQUITY_CALENDAR = "CME Globex Equity"
SESSION_OPEN_ET = time(18, 0)
SESSION_CLOSE_ET = time(17, 0)
RTH_OPEN_ET = time(9, 30)
RTH_CLOSE_ET = time(16, 0)

# pandas-market-calendars does not carry this unscheduled one-off closure.
# CME SER-9499R states that U.S. equity-index products closed at 08:30 Central
# (09:30 Eastern) for the January 9, 2025 National Day of Mourning.
SPECIAL_SESSION_CLOSES_ET = {
    date(2025, 1, 9): (
        time(9, 30),
        "CME_SER_9499R_national_day_of_mourning",
    ),
}


def session_date_for_timestamp(value: datetime | pd.Timestamp) -> date:
    """Return the CME trade-date label for one timezone-aware timestamp."""
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError("Session assignment requires a timezone-aware timestamp")
    local = timestamp.tz_convert(EASTERN)
    label = local.date()
    if local.time() >= SESSION_OPEN_ET:
        label += timedelta(days=1)
    return label


def assign_session_dates(values: pd.Series | pd.DatetimeIndex) -> pd.Series:
    """Vectorized CME session labels for UTC timestamps."""
    series = pd.Series(values)
    if series.dt.tz is None:
        raise ValueError("Session assignment requires timezone-aware timestamps")
    local = series.dt.tz_convert(EASTERN)
    labels = local.dt.normalize().dt.tz_localize(None)
    labels = labels + pd.to_timedelta((local.dt.hour >= 18).astype(int), unit="D")
    return labels.dt.date


def cme_equity_schedule(start_date: date, end_date: date) -> pd.DataFrame:
    """Return the authoritative product calendar at one row per trade date."""
    calendar = market_calendars.get_calendar(CME_EQUITY_CALENDAR)
    schedule = calendar.schedule(
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    ).rename(columns={"market_open": "expected_open_utc", "market_close": "expected_close_utc"})
    schedule.index = pd.Index(
        [pd.Timestamp(value).date() for value in schedule.index],
        name="session_date",
    )
    schedule["calendar_override"] = False
    schedule["calendar_note"] = ""
    for session_date, (close_clock, note) in SPECIAL_SESSION_CLOSES_ET.items():
        if session_date not in schedule.index:
            continue
        local_close = datetime.combine(session_date, close_clock, EASTERN)
        schedule.loc[session_date, "expected_close_utc"] = pd.Timestamp(
            local_close
        ).tz_convert(UTC)
        schedule.loc[session_date, "calendar_override"] = True
        schedule.loc[session_date, "calendar_note"] = note
    schedule["expected_minutes"] = (
        (
            schedule["expected_close_utc"] - schedule["expected_open_utc"]
        ).dt.total_seconds()
        // 60
    ).astype(int)
    close_local = schedule["expected_close_utc"].dt.tz_convert(EASTERN)
    schedule["is_early_close"] = close_local.dt.time != SESSION_CLOSE_ET
    schedule["expected_open_et"] = schedule["expected_open_utc"].dt.tz_convert(EASTERN)
    schedule["expected_close_et"] = close_local
    return schedule


def expected_minutes(
    expected_open_utc: pd.Timestamp,
    expected_close_utc: pd.Timestamp,
) -> pd.DatetimeIndex:
    """Return bar-open timestamps expected for a half-open session interval."""
    return pd.date_range(
        expected_open_utc,
        expected_close_utc,
        freq="min",
        inclusive="left",
    )


def rth_mask(values: pd.DatetimeIndex | pd.Series) -> pd.Series:
    """Identify regular-hours bars using daylight-saving-aware Eastern time."""
    series = pd.Series(values)
    if series.dt.tz is None:
        raise ValueError("RTH classification requires timezone-aware timestamps")
    local = series.dt.tz_convert(EASTERN)
    clock = local.dt.time
    return (clock >= RTH_OPEN_ET) & (clock < RTH_CLOSE_ET)


def split_name(session_date: date, split_config: dict[str, Any]) -> str:
    """Assign one session to a frozen chronological split."""
    for name, bounds in split_config["splits"].items():
        start = date.fromisoformat(str(bounds["start_date"]))
        end = date.fromisoformat(str(bounds["end_date"]))
        if start <= session_date <= end:
            return name
    return "outside_research"
