"""Optional hourly statistic: parsing, time zones, bounded fetch, idempotent import."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.veolia_eau.const import DOMAIN
from custom_components.veolia_eau.diagnostics import async_get_config_entry_diagnostics
from custom_components.veolia_eau.models import HourlyReading, parse_hourly
from custom_components.veolia_eau.statistics import (
    async_import_hourly,
    build_hourly_rows,
    hourly_statistic_id,
)

from .conftest import PASSWORD, SUB, USER, Backend
from .fakes import FakeHttp

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 11, 18, 11, 0, tzinfo=PARIS)


@pytest.fixture(autouse=True)
async def paris(recorder_mock, hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    await hass.config.async_set_time_zone("Europe/Paris")
    freezer.move_to(NOW)


def entry(**options) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=USER,
        data={"username": USER, "password": PASSWORD, "subscriptions": [SUB]},
        options=options,
    )


async def settle(hass: HomeAssistant) -> None:
    for _ in range(3):
        await hass.async_block_till_done()
        await async_wait_recording_done(hass)


async def setup(hass: HomeAssistant, config: MockConfigEntry) -> None:
    config.add_to_hass(hass)
    await hass.config_entries.async_setup(config.entry_id)
    await settle(hass)


async def hourly_stats(hass: HomeAssistant) -> list[dict]:
    stat_id = hourly_statistic_id(SUB)
    found = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 1, 1, tzinfo=dt_util.UTC),
        None,
        {stat_id},
        "hour",
        None,
        {"state", "sum"},
    )
    return found.get(stat_id, [])


def hourly_days(http: FakeHttp) -> list[str]:
    return [c.kwargs["params"]["jour"] for c in http.backend_calls if c.path.endswith("/horaires")]


def utc(day: date, hour: int) -> float:
    return datetime(day.year, day.month, day.day, hour, tzinfo=PARIS).timestamp()


def test_parse_hourly() -> None:
    rows = [
        {"heure_debut_releve": "2026-11-17T03:00:00", "consommation": {"litre": 4}},
        {"heure_debut_releve": "2026-11-17T02:00:00", "consommation": {"litre": 1}},
        {"heure_debut_releve": "2026-11-17T02:00:00", "consommation": {"litre": 2}},  # wins
        {"heure_debut_releve": "2026-11-17T04:00:00", "consommation": None},  # unknown hour
        {"heure_debut_releve": "not a date", "consommation": {"litre": 9}},
        {"consommation": {"litre": 9}},
    ]
    assert parse_hourly(rows) == [
        HourlyReading(datetime(2026, 11, 17, 2), 2.0),
        HourlyReading(datetime(2026, 11, 17, 3), 4.0),
    ]


def test_hourly_rows_are_placed_at_the_real_hour_across_dst() -> None:
    readings = [
        HourlyReading(datetime(2026, 3, 29, 2), 5.0),  # does not exist in Paris: clocks go forward
        HourlyReading(datetime(2026, 3, 29, 3), 7.0),  # the same UTC hour: added up
        HourlyReading(datetime(2026, 11, 17, 2), 3.0),
    ]
    rows = build_hourly_rows(readings, 100.0, PARIS)
    assert [(r["start"].isoformat(), r["state"], r["sum"]) for r in rows] == [
        ("2026-03-29T01:00:00+00:00", 12.0, 112.0),
        ("2026-11-17T01:00:00+00:00", 3.0, 115.0),
    ]


async def test_nothing_hourly_is_fetched_by_default(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    config = entry()
    await setup(hass, config)
    assert hourly_days(http) == []
    assert await hourly_stats(hass) == []
    diagnostics = await async_get_config_entry_diagnostics(hass, config)
    assert diagnostics["subscriptions"][0]["hourly_hours_last_day"] is None


async def test_hourly_backfill_then_refresh_is_bounded_and_idempotent(
    hass: HomeAssistant, http: FakeHttp, backend: Backend, freezer: FrozenDateTimeFactory
) -> None:
    config = entry(hourly_statistic=True)
    await setup(hass, config)
    # First time: 7 days, newest first, one GET each, nothing else on the hourly route.
    assert hourly_days(http) == [f"2026-11-{d}" for d in range(17, 10, -1)]
    assert {c.method for c in http.backend_calls} == {"GET"}
    before = await hourly_stats(hass)
    assert len(before) == 7 * 3
    assert before[0]["start"] == utc(date(2026, 11, 11), 2)
    assert before[-1]["start"] == utc(date(2026, 11, 17), 4)
    assert before[-1]["sum"] == 7 * (12.0 + 13.0 + 14.0)
    assert all(b["sum"] - a["sum"] == b["state"] for a, b in zip(before, before[1:], strict=False))
    diagnostics = await async_get_config_entry_diagnostics(hass, config)
    assert diagnostics["subscriptions"][0]["hourly_hours_last_day"] == 3
    assert diagnostics["options"] == {"hourly_statistic": True}

    # Next day: only the last 3 days are read again; the sum goes on, nothing is counted twice.
    http.calls.clear()
    backend.last_day = date(2026, 11, 18)
    freezer.move_to(datetime(2026, 11, 19, 9, 0, tzinfo=PARIS))
    await config.runtime_data.async_refresh()
    await settle(hass)
    assert hourly_days(http) == ["2026-11-18", "2026-11-17", "2026-11-16"]
    after = await hourly_stats(hass)
    assert after[: len(before)] == before
    assert len(after) == len(before) + 3
    assert after[-1]["sum"] == before[-1]["sum"] + 39.0

    await config.runtime_data.async_refresh()
    await settle(hass)
    assert await hourly_stats(hass) == after


async def test_a_refused_day_ends_the_hourly_walk_without_failing_the_refresh(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    backend.hourly_refused_before = "2026-11-15"
    config = entry(hourly_statistic=True)
    await setup(hass, config)
    assert config.runtime_data.last_update_success
    assert hourly_days(http) == ["2026-11-17", "2026-11-16", "2026-11-15", "2026-11-14"]
    assert len(await hourly_stats(hass)) == 3 * 3


async def test_no_hourly_day_before_the_subscription_start(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    backend.start = "2026-11-15"
    await setup(hass, entry(hourly_statistic=True))
    assert hourly_days(http) == ["2026-11-17", "2026-11-16", "2026-11-15"]


async def test_full_hourly_curve_is_imported_as_it_comes(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    """With the consent given by the account owner on Veolia's site, every hour is there."""
    backend.hours = list(range(24))
    config = entry(hourly_statistic=True)
    await setup(hass, config)
    assert len(await hourly_stats(hass)) == 7 * 24
    diagnostics = await async_get_config_entry_diagnostics(hass, config)
    assert diagnostics["subscriptions"][0]["hourly_hours_last_day"] == 24


async def test_empty_hourly_answers_import_nothing(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    backend.hours = []
    config = entry(hourly_statistic=True)
    await setup(hass, config)
    assert await hourly_stats(hass) == []
    assert await async_import_hourly(hass, SUB, "x", []) == 0
    diagnostics = await async_get_config_entry_diagnostics(hass, config)
    assert diagnostics["subscriptions"][0]["hourly_hours_last_day"] == 0
