"""Shapes of the Veolia consumption rows (pure functions, no network, no Home Assistant)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

# fiabilite_index / fiabilite_conso
RELIABILITY = {"M": "measured", "E": "estimated", "C": "corrected", "R": "read"}


@dataclass(frozen=True)
class DailyReading:
    """One day of consumption, dated on the day it was consumed."""

    day: date
    litres: float | None
    index_m3: float | None
    reliability: str | None


@dataclass(frozen=True)
class MonthlyReading:
    """One month of consumption."""

    year: int
    month: int
    m3: float | None
    index_m3: float | None
    reliability: str | None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _field(row: dict[str, Any], key: str, unit: str) -> float | None:
    inner = row.get(key)
    return _number(inner.get(unit)) if isinstance(inner, dict) else None


def parse_daily(rows: Iterable[dict[str, Any]]) -> list[DailyReading]:
    """Daily rows → readings sorted by day, one per day (the last row of a day wins).

    Rows without a valid `date_releve` are dropped.
    """
    by_day: dict[date, DailyReading] = {}
    for row in rows:
        try:
            day = date.fromisoformat(str(row.get("date_releve"))[:10])
        except ValueError:
            continue
        code = row.get("fiabilite_index")
        by_day[day] = DailyReading(
            day=day,
            litres=_field(row, "consommation", "litre"),
            index_m3=_field(row, "index", "m3"),
            reliability=RELIABILITY.get(code, code) if isinstance(code, str) else None,
        )
    return [by_day[day] for day in sorted(by_day)]


def parse_monthly(rows: Iterable[dict[str, Any]]) -> list[MonthlyReading]:
    """Monthly rows → readings sorted by (year, month), one per month."""
    by_month: dict[tuple[int, int], MonthlyReading] = {}
    for row in rows:
        year, month = row.get("annee"), row.get("mois")
        if not isinstance(year, int) or not isinstance(month, int) or not 1 <= month <= 12:
            continue
        code = row.get("fiabilite_conso")
        by_month[(year, month)] = MonthlyReading(
            year=year,
            month=month,
            m3=_field(row, "consommation", "m3"),
            index_m3=_field(row, "index", "m3"),
            reliability=RELIABILITY.get(code, code) if isinstance(code, str) else None,
        )
    return [by_month[key] for key in sorted(by_month)]


def months_between(start: date, end: date) -> list[tuple[int, int]]:
    """(year, month) pairs covering [start, end], in order; empty if end < start."""
    out: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out
