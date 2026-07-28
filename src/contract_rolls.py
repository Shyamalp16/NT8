"""Quarterly contract inventory and executable roll-policy construction."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, fields
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Mapping


QUARTERLY_MONTH_CODES = {3: "H", 6: "M", 9: "U", 12: "Z"}


@dataclass(frozen=True)
class ContractSpec:
    product: str
    provider_symbol: str
    contract_year: int
    contract_month: int
    expiry_date: date
    planned_roll_date: date
    roll_method: str = "pending_volume_crossover"
    selected_roll_date: date | None = None

    @property
    def effective_roll_date(self) -> date:
        return self.selected_roll_date or self.planned_roll_date


def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    first_friday = first + timedelta(days=(4 - first.weekday()) % 7)
    return first_friday + timedelta(days=14)


def business_days_before(value: date, count: int) -> date:
    current = value
    remaining = count
    while remaining:
        current -= timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def provider_symbol(product: str, year: int, month: int) -> str:
    if month not in QUARTERLY_MONTH_CODES:
        raise ValueError(f"Unsupported quarterly contract month: {month}")
    return f"{product}{QUARTERLY_MONTH_CODES[month]}{year % 10}"


def build_quarterly_inventory(
    product: str,
    start_date: date,
    end_date: date,
    fallback_business_days: int,
) -> list[ContractSpec]:
    contracts: list[ContractSpec] = []
    for year in range(start_date.year, end_date.year + 1):
        for month in QUARTERLY_MONTH_CODES:
            expiry = third_friday(year, month)
            planned_roll = business_days_before(expiry, fallback_business_days)
            if planned_roll < start_date:
                continue
            if planned_roll > end_date + timedelta(days=120):
                continue
            contracts.append(
                ContractSpec(
                    product=product,
                    provider_symbol=provider_symbol(product, year, month),
                    contract_year=year,
                    contract_month=month,
                    expiry_date=expiry,
                    planned_roll_date=planned_roll,
                )
            )
    return contracts


def choose_roll_date(
    front_volume: Mapping[date, float],
    next_volume: Mapping[date, float],
    expiry_date: date,
    confirmation_sessions: int,
    fallback_business_days: int,
) -> tuple[date, str]:
    """Use completed daily bars and activate the new contract next session."""
    common_dates = sorted(set(front_volume) & set(next_volume))
    streak = 0
    for session_date in common_dates:
        if session_date >= expiry_date:
            break
        if next_volume[session_date] > front_volume[session_date]:
            streak += 1
            if streak >= confirmation_sessions:
                decision_date = session_date
                return _next_weekday(decision_date), "confirmed_volume_crossover_next_session"
        else:
            streak = 0

    return (
        business_days_before(expiry_date, fallback_business_days),
        "fallback_business_days_before_expiry",
    )


def _next_weekday(value: date) -> date:
    candidate = value + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def write_contract_inventory(path: Path, contracts: Iterable[ContractSpec]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for contract in contracts:
        row = asdict(contract)
        row["expiry_date"] = contract.expiry_date.isoformat()
        row["planned_roll_date"] = contract.planned_roll_date.isoformat()
        row["selected_roll_date"] = (
            contract.selected_roll_date.isoformat()
            if contract.selected_roll_date
            else ""
        )
        rows.append(row)

    fieldnames = [field.name for field in fields(ContractSpec)]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_contract_inventory(path: Path) -> list[ContractSpec]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [
        ContractSpec(
            product=row["product"],
            provider_symbol=row["provider_symbol"],
            contract_year=int(row["contract_year"]),
            contract_month=int(row["contract_month"]),
            expiry_date=date.fromisoformat(row["expiry_date"]),
            planned_roll_date=date.fromisoformat(row["planned_roll_date"]),
            roll_method=row["roll_method"],
            selected_roll_date=(
                date.fromisoformat(row["selected_roll_date"])
                if row.get("selected_roll_date")
                else None
            ),
        )
        for row in rows
    ]
