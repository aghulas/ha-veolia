"""Leak detection by the closed-day baseline (pure functions).

On a day when the building is empty, whatever flows is a permanent flow: a running flush, a
dripping tap, a leak. A baseline above the threshold on several closed days in a row is a
possible leak.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from datetime import date

from .models import DailyReading

SUNDAY = 6
DEFAULT_CLOSED_WEEKDAYS: frozenset[int] = frozenset({SUNDAY})
DEFAULT_THRESHOLD_L = 150.0
DEFAULT_CONSECUTIVE = 2


def closed_day_baseline(
    readings: Iterable[DailyReading],
    closed_weekdays: Collection[int] = DEFAULT_CLOSED_WEEKDAYS,
    closed_dates: Collection[date] = (),
) -> list[tuple[date, float]]:
    """(day, litres) of the closed days that have a published volume, sorted by day.

    `closed_weekdays` uses `date.weekday()` numbering (Monday = 0 … Sunday = 6);
    `closed_dates` adds individual days (holidays). Days without a volume are left out:
    an unknown day is neither a leak nor the end of one.
    """
    out = [
        (r.day, r.litres)
        for r in readings
        if r.litres is not None and (r.day.weekday() in closed_weekdays or r.day in closed_dates)
    ]
    return sorted(out)


def possible_leak(
    baseline: list[tuple[date, float]],
    threshold_l: float = DEFAULT_THRESHOLD_L,
    consecutive: int = DEFAULT_CONSECUTIVE,
) -> bool:
    """True when the last `consecutive` closed days are all strictly above the threshold."""
    if consecutive < 1 or len(baseline) < consecutive:
        return False
    return all(litres > threshold_l for _, litres in baseline[-consecutive:])
