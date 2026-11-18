"""Import of the daily volumes into Home Assistant long-term statistics.

One external statistic per subscription, `veolia_eau:<subscription>_water`, in litres, one row per
day at local midnight of the day the water was consumed. Importing the same days again rewrites
the same rows: the running sum is rebuilt from the last row stored before the imported window.
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

from .const import DOMAIN
from .models import DailyReading

# How far back the row preceding the window is searched (gaps in Veolia's data are days, not years).
LOOKBACK = timedelta(days=400)


def statistic_id(subscription: str) -> str:
    """Id of the external statistic of a subscription."""
    return f"{DOMAIN}:{subscription}_water"


def local_midnight(day: date, tz: tzinfo) -> datetime:
    """Start of `day` in `tz`, as an aware UTC datetime (DST-safe)."""
    return datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(dt_util.UTC)


def build_rows(
    readings: Iterable[DailyReading], base_sum: float, tz: tzinfo
) -> list[dict[str, Any]]:
    """Statistic rows of the days that have a volume, with the running sum continued from base."""
    rows: list[dict[str, Any]] = []
    total = base_sum
    for reading in sorted(readings, key=lambda r: r.day):
        if reading.litres is None:
            continue
        total += reading.litres
        rows.append(
            {"start": local_midnight(reading.day, tz), "state": reading.litres, "sum": total}
        )
    return rows


def _metadata(subscription: str, name: str) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "has_mean": False,
        "has_sum": True,
        "name": name,
        "source": DOMAIN,
        "statistic_id": statistic_id(subscription),
        "unit_of_measurement": UnitOfVolume.LITERS,
    }
    # Newer Home Assistant versions want these two as well.
    models = importlib.import_module("homeassistant.components.recorder.models")
    if hasattr(models, "StatisticMeanType"):
        meta["mean_type"] = models.StatisticMeanType.NONE
        meta["unit_class"] = "volume"
    return meta


async def async_import_daily(
    hass: HomeAssistant, subscription: str, name: str, readings: list[DailyReading]
) -> int:
    """Import (or re-import) the given days. Returns the number of rows written."""
    days = [r for r in readings if r.litres is not None]
    if not days:
        return 0
    tz = dt_util.get_default_time_zone()
    first = local_midnight(min(r.day for r in days), tz)
    stat_id = statistic_id(subscription)
    before = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, first - LOOKBACK, first, {stat_id}, "hour", None, {"sum"}
    )
    previous = before.get(stat_id) or []
    base = float(previous[-1].get("sum") or 0.0) if previous else 0.0
    rows = build_rows(days, base, tz)
    async_add_external_statistics(hass, _metadata(subscription, name), rows)  # type: ignore[arg-type]
    return len(rows)


async def async_has_statistics(hass: HomeAssistant, subscription: str) -> bool:
    """True once at least one day has been imported for this subscription."""
    stat_id = statistic_id(subscription)
    last = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, stat_id, True, {"sum"}
    )
    return bool(last.get(stat_id))
