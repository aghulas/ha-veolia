"""Import of the volumes into Home Assistant long-term statistics.

One external statistic per subscription, `veolia_eau:<subscription>_water`, in litres, one row per
day at local midnight of the day the water was consumed. Importing the same days again rewrites
the same rows: the running sum is rebuilt from the last row stored before the imported window.

Optionally a second one, `veolia_eau:<subscription>_water_hourly`, one row per hour Veolia
publishes, at the real start of that hour. Hours Veolia does not publish get no row.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN, VEOLIA_TIME_ZONE
from .models import DailyReading, HourlyReading

# How far back the row preceding the window is searched (gaps in Veolia's data are days, not years).
LOOKBACK = timedelta(days=400)


def statistic_id(subscription: str) -> str:
    """Id of the external statistic of a subscription."""
    return f"{DOMAIN}:{subscription}_water"


def hourly_statistic_id(subscription: str) -> str:
    """Id of the optional hourly statistic of a subscription."""
    return f"{DOMAIN}:{subscription}_water_hourly"


def local_midnight(day: date, tz: tzinfo) -> datetime:
    """Start of `day` in `tz`, as an aware UTC datetime (DST-safe)."""
    return datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(dt_util.UTC)


def build_rows(
    monthly: Iterable[DailyReading], daily: Iterable[DailyReading], base_sum: float, tz: tzinfo
) -> list[dict[str, Any]]:
    """Statistic rows with the running sum continued from `base_sum`.

    `monthly` (first run only) gives one row per month; `daily` one row per day, and every day
    between its first and last volume gets a row: a day without a volume is written as 0 with the
    sum carried over. Otherwise a day Veolia withdrew after we stored it would keep its old row
    and make the next day's difference wrong (even negative).
    """
    rows: list[dict[str, Any]] = []
    total = base_sum

    def add(day: date, litres: float) -> None:
        nonlocal total
        total += litres
        rows.append({"start": local_midnight(day, tz), "state": litres, "sum": total})

    for reading in sorted(monthly, key=lambda r: r.day):
        if reading.litres is not None:
            add(reading.day, reading.litres)
    volumes = {r.day: r.litres for r in daily if r.litres is not None}
    if volumes:
        day, last = min(volumes), max(volumes)
        while day <= last:
            add(day, volumes.get(day, 0.0))
            day += timedelta(days=1)
    return rows


def build_hourly_rows(
    readings: Iterable[HourlyReading], base_sum: float, source_tz: tzinfo
) -> list[dict[str, Any]]:
    """Hourly statistic rows with the running sum continued from `base_sum`.

    Veolia's naive local hours are placed in `source_tz`, then in UTC. Two local hours that land
    on the same UTC hour (an hour that does not exist when clocks go forward) are added up, so
    no water is lost.
    """
    by_start: dict[datetime, float] = {}
    for reading in readings:
        start = reading.start.replace(tzinfo=source_tz).astimezone(dt_util.UTC)
        by_start[start] = by_start.get(start, 0.0) + reading.litres
    rows: list[dict[str, Any]] = []
    total = base_sum
    for start in sorted(by_start):
        total += by_start[start]
        rows.append({"start": start, "state": by_start[start], "sum": total})
    return rows


def _metadata(stat_id: str, name: str) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "has_mean": False,
        "has_sum": True,
        "name": name,
        "source": DOMAIN,
        "statistic_id": stat_id,
        "unit_of_measurement": UnitOfVolume.LITERS,
    }
    # Newer Home Assistant versions want these two as well.
    models = importlib.import_module("homeassistant.components.recorder.models")
    if hasattr(models, "StatisticMeanType"):
        meta["mean_type"] = models.StatisticMeanType.NONE
        meta["unit_class"] = "volume"
    return meta


async def async_import_daily(
    hass: HomeAssistant,
    subscription: str,
    name: str,
    monthly: list[DailyReading],
    daily: list[DailyReading],
) -> int:
    """Import (or re-import) the given days. Returns the number of rows written."""
    tz = dt_util.get_default_time_zone()
    days = [r.day for r in (*monthly, *daily) if r.litres is not None]
    if not days:
        return 0
    stat_id = statistic_id(subscription)
    base = await _sum_before(hass, stat_id, local_midnight(min(days), tz))
    rows = build_rows(monthly, daily, base, tz)
    async_add_external_statistics(hass, _metadata(stat_id, name), rows)  # type: ignore[arg-type]
    return len(rows)


async def async_import_hourly(
    hass: HomeAssistant, subscription: str, name: str, readings: list[HourlyReading]
) -> int:
    """Import (or re-import) the given hours. Returns the number of rows written."""
    if not readings:
        return 0
    # Loaded without blocking the event loop; HA's own zone if it were ever missing.
    source_tz = (
        await dt_util.async_get_time_zone(VEOLIA_TIME_ZONE) or dt_util.get_default_time_zone()
    )
    stat_id = hourly_statistic_id(subscription)
    first = min(r.start for r in readings).replace(tzinfo=source_tz).astimezone(dt_util.UTC)
    rows = build_hourly_rows(readings, await _sum_before(hass, stat_id, first), source_tz)
    async_add_external_statistics(hass, _metadata(stat_id, name), rows)  # type: ignore[arg-type]
    return len(rows)


async def _sum_before(hass: HomeAssistant, stat_id: str, first: datetime) -> float:
    """Sum of the last row stored before `first` (0 when there is none)."""
    before = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, first - LOOKBACK, first, {stat_id}, "hour", None, {"sum"}
    )
    previous = before.get(stat_id) or []
    return float(previous[-1].get("sum") or 0.0) if previous else 0.0


async def async_has_statistics(
    hass: HomeAssistant, subscription: str, hourly: bool = False
) -> bool:
    """True once at least one row has been imported for this subscription (daily or hourly)."""
    stat_id = hourly_statistic_id(subscription) if hourly else statistic_id(subscription)
    last = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, stat_id, True, {"sum"}
    )
    return bool(last.get(stat_id))
