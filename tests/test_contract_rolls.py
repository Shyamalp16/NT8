from datetime import date

from src.contract_rolls import (
    build_quarterly_inventory,
    choose_roll_date,
    provider_symbol,
    third_friday,
)


def test_contract_symbol_and_expiry_are_explicit():
    assert provider_symbol("MNQ", 2021, 9) == "MNQU1"
    assert third_friday(2026, 9) == date(2026, 9, 18)


def test_inventory_covers_research_period_contracts():
    inventory = build_quarterly_inventory(
        "MNQ",
        date(2021, 7, 28),
        date(2026, 7, 27),
        fallback_business_days=5,
    )

    assert inventory[0].provider_symbol == "MNQU1"
    assert inventory[-1].provider_symbol == "MNQU6"
    assert all(item.contract_year >= 2021 for item in inventory)


def test_roll_activates_next_session_after_confirmed_crossover():
    front = {
        date(2026, 9, 8): 200,
        date(2026, 9, 9): 100,
        date(2026, 9, 10): 90,
    }
    nxt = {
        date(2026, 9, 8): 150,
        date(2026, 9, 9): 110,
        date(2026, 9, 10): 120,
    }

    roll_date, method = choose_roll_date(
        front,
        nxt,
        expiry_date=date(2026, 9, 18),
        confirmation_sessions=2,
        fallback_business_days=5,
    )

    assert roll_date == date(2026, 9, 11)
    assert method == "confirmed_volume_crossover_next_session"

