"""Validated schemas for the subset of NinjaTrader MCP used by the project."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable


REQUIRED_OHLC_FIELDS = ("open", "high", "low", "close")


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 provider timestamp and require timezone information."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp is not timezone-aware: {value}")
    return parsed


@dataclass(frozen=True)
class RequestSpec:
    request_id: str
    purpose: str
    symbol: str
    contract_year: int
    bar_type: str
    bar_size: int
    from_utc: str
    to_utc: str
    close_only: bool = False
    volume_profile: bool = False

    def to_mcp_arguments(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "barType": self.bar_type,
            "barSize": self.bar_size,
            "from": self.from_utc,
            "to": self.to_utc,
            "closeOnly": self.close_only,
            "volumeProfile": self.volume_profile,
        }

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["mcp_arguments"] = self.to_mcp_arguments()
        return record


@dataclass(frozen=True)
class MarketBar:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    up_volume: float
    down_volume: float
    up_ticks: int | None = None
    down_ticks: int | None = None

    @property
    def total_volume(self) -> float:
        return self.up_volume + self.down_volume

    @classmethod
    def from_provider(cls, raw: dict[str, Any]) -> "MarketBar":
        missing = [field for field in ("timestamp", *REQUIRED_OHLC_FIELDS) if field not in raw]
        if missing:
            raise ValueError(f"Bar is missing required fields: {missing}")

        parse_timestamp(str(raw["timestamp"]))
        values = {field: float(raw[field]) for field in REQUIRED_OHLC_FIELDS}
        if min(values.values()) <= 0:
            raise ValueError(f"Bar contains a non-positive price: {raw}")
        if values["high"] < max(values["open"], values["low"], values["close"]):
            raise ValueError(f"Bar high violates OHLC ordering: {raw}")
        if values["low"] > min(values["open"], values["high"], values["close"]):
            raise ValueError(f"Bar low violates OHLC ordering: {raw}")

        up_volume = float(raw.get("upVolume", 0))
        down_volume = float(raw.get("downVolume", 0))
        if up_volume < 0 or down_volume < 0:
            raise ValueError(f"Bar contains negative volume: {raw}")

        return cls(
            timestamp=str(raw["timestamp"]),
            open=values["open"],
            high=values["high"],
            low=values["low"],
            close=values["close"],
            up_volume=up_volume,
            down_volume=down_volume,
            up_ticks=_optional_int(raw.get("upTicks")),
            down_ticks=_optional_int(raw.get("downTicks")),
        )


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


@dataclass(frozen=True)
class MarketHistoryResponse:
    symbol: str
    bar_type: str
    bar_size: int
    bars: tuple[MarketBar, ...]
    duplicate_timestamp_count: int
    out_of_order_timestamp_count: int

    @classmethod
    def from_tool_result(cls, result: dict[str, Any]) -> "MarketHistoryResponse":
        """Accept structuredContent, a direct payload, or a saved tool envelope."""
        payload = result.get("structuredContent", result)
        if "structuredContent" in payload:
            payload = payload["structuredContent"]

        missing = [field for field in ("symbol", "barType", "barSize", "bars") if field not in payload]
        if missing:
            raise ValueError(f"Market-history response is missing fields: {missing}")

        bars = tuple(MarketBar.from_provider(raw) for raw in payload["bars"])
        duplicate_count, out_of_order_count = _timestamp_diagnostics(bars)
        return cls(
            symbol=str(payload["symbol"]),
            bar_type=str(payload["barType"]),
            bar_size=int(payload["barSize"]),
            bars=bars,
            duplicate_timestamp_count=duplicate_count,
            out_of_order_timestamp_count=out_of_order_count,
        )


def _timestamp_diagnostics(bars: Iterable[MarketBar]) -> tuple[int, int]:
    previous: datetime | None = None
    seen: set[datetime] = set()
    duplicate_count = 0
    out_of_order_count = 0
    for bar in bars:
        timestamp = parse_timestamp(bar.timestamp)
        if timestamp in seen:
            duplicate_count += 1
        if previous is not None and timestamp < previous:
            out_of_order_count += 1
        seen.add(timestamp)
        previous = timestamp
    return duplicate_count, out_of_order_count
