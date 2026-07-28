import json
from dataclasses import replace
from datetime import date

import pytest

from src.contract_rolls import build_quarterly_inventory
from src.data_download import (
    acquisition_status,
    ingest_response,
    plan_minute_requests,
    plan_roll_daily_requests,
    retry_delay_seconds,
)
from src.mcp_client import RequestSpec
from src.mcp_client.models import MarketHistoryResponse


def _tool_result(close=100.25):
    return {
        "structuredContent": {
            "symbol": "MNQU6",
            "barType": "Minute",
            "barSize": 1,
            "bars": [
                {
                    "timestamp": "2026-07-27T13:30:00Z",
                    "open": 100.0,
                    "high": 100.5,
                    "low": 99.75,
                    "close": close,
                    "upVolume": 10,
                    "downVolume": 8,
                    "upTicks": 3,
                    "downTicks": 2,
                }
            ],
        }
    }


def _request():
    return RequestSpec(
        request_id="pilot",
        purpose="minute_discovery",
        symbol="MNQU6",
        contract_year=2026,
        bar_type="Minute",
        bar_size=1,
        from_utc="2026-07-27T04:00:00Z",
        to_utc="2026-07-28T03:59:59Z",
    )


def test_minute_plan_is_capped_and_stops_at_research_end():
    contracts = build_quarterly_inventory(
        "MNQ",
        date(2026, 6, 1),
        date(2026, 7, 27),
        fallback_business_days=5,
    )
    requests = plan_minute_requests(
        contracts,
        research_start=date(2026, 6, 1),
        research_end=date(2026, 7, 27),
        max_calendar_days=7,
    )

    assert requests
    assert requests[-1].to_utc.startswith("2026-07-28T03:59:59")
    assert all(request.bar_type == "Minute" for request in requests)


def test_roll_plan_requests_front_and_next_in_same_window():
    contracts = build_quarterly_inventory(
        "MNQ",
        date(2025, 12, 1),
        date(2026, 7, 27),
        fallback_business_days=5,
    )

    requests = plan_roll_daily_requests(contracts, lookback_calendar_days=35)

    assert len(requests) == 2 * (len(contracts) - 1)
    assert requests[0].from_utc == requests[1].from_utc
    assert requests[0].to_utc == requests[1].to_utc
    assert requests[0].symbol != requests[1].symbol


def test_minute_plan_uses_selected_roll_date():
    contracts = build_quarterly_inventory(
        "MNQ",
        date(2026, 3, 1),
        date(2026, 7, 27),
        fallback_business_days=5,
    )
    contracts[0] = replace(
        contracts[0],
        selected_roll_date=date(2026, 3, 10),
        roll_method="confirmed_volume_crossover_next_session",
    )

    requests = plan_minute_requests(
        contracts,
        research_start=date(2026, 3, 1),
        research_end=date(2026, 7, 27),
        max_calendar_days=7,
    )

    first_next_contract = next(
        request for request in requests if request.symbol == contracts[1].provider_symbol
    )
    assert first_next_contract.from_utc.startswith("2026-03-10")


def test_schema_records_duplicate_timestamps_without_mutating_raw_data():
    result = _tool_result()
    result["structuredContent"]["bars"].append(
        dict(result["structuredContent"]["bars"][0])
    )

    parsed = MarketHistoryResponse.from_tool_result(result)

    assert parsed.duplicate_timestamp_count == 1
    assert len(parsed.bars) == 2


def test_ingestion_is_idempotent_and_rejects_mutation(tmp_path):
    raw_dir = tmp_path / "raw"
    ledger = tmp_path / "download_ledger.jsonl"

    first = ingest_response(_request(), _tool_result(), raw_dir, ledger)
    second = ingest_response(_request(), _tool_result(), raw_dir, ledger)

    assert first["status"] == "success"
    assert second["status"] == "cached_verified"
    assert len(ledger.read_text().splitlines()) == 1
    assert json.loads(ledger.read_text())["row_count"] == 1

    with pytest.raises(FileExistsError, match="Immutable raw response differs"):
        ingest_response(_request(), _tool_result(close=100.4), raw_dir, ledger)


def test_status_and_backoff_support_resume(tmp_path):
    manifest = tmp_path / "requests.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    manifest.write_text(
        "\n".join(
            [
                json.dumps({"request_id": "done"}),
                json.dumps({"request_id": "pending"}),
            ]
        )
        + "\n"
    )
    ledger.write_text(
        json.dumps({"request_id": "done", "status": "success"}) + "\n"
    )
    config = {"data_policy": {"request_retry_backoff_seconds": [1, 2, 4]}}

    assert acquisition_status(manifest, ledger) == {
        "planned": 2,
        "completed": 1,
        "pending": 1,
    }
    assert retry_delay_seconds(config, 1) == 1
    assert retry_delay_seconds(config, 10) == 4
