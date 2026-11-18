"""Set-up, statistics import, sensors, leak detection, refused password, diagnostics."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.veolia_eau.const import DOMAIN
from custom_components.veolia_eau.diagnostics import async_get_config_entry_diagnostics
from custom_components.veolia_eau.models import DailyReading
from custom_components.veolia_eau.statistics import build_rows, local_midnight, statistic_id

from .conftest import PASSWORD, SUB, USER, Backend
from .fakes import FakeHttp, FakeResponse, auth_error

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
    """Let a refresh finish and the recorder commit what it queued (the refresh itself waits on
    the recorder thread, so one pass is not always enough)."""
    for _ in range(3):
        await hass.async_block_till_done()
        await async_wait_recording_done(hass)


async def setup(hass: HomeAssistant, config: MockConfigEntry) -> None:
    config.add_to_hass(hass)
    await hass.config_entries.async_setup(config.entry_id)
    await settle(hass)


async def stats(hass: HomeAssistant) -> list[dict]:
    stat_id = statistic_id(SUB)
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


def test_build_rows_uses_local_midnight_of_the_real_day() -> None:
    readings = [
        DailyReading(date(2026, 10, 25), 100.0, None, None),  # DST change day
        DailyReading(date(2026, 10, 24), 50.0, None, None),
        DailyReading(date(2026, 10, 26), None, None, None),
        DailyReading(date(2026, 10, 27), 10.0, None, None),
    ]
    rows = build_rows(readings, 1000.0, PARIS)
    assert [(r["start"].isoformat(), r["state"], r["sum"]) for r in rows] == [
        ("2026-10-23T22:00:00+00:00", 50.0, 1050.0),
        ("2026-10-24T22:00:00+00:00", 100.0, 1150.0),
        ("2026-10-26T23:00:00+00:00", 10.0, 1160.0),
    ]
    assert local_midnight(date(2026, 7, 1), PARIS).isoformat() == "2026-06-30T22:00:00+00:00"


async def test_setup_backfills_since_the_subscription_start(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    config = entry()
    await setup(hass, config)
    assert config.state is ConfigEntryState.LOADED
    months = [c.kwargs["params"]["mois"] for c in http.backend_calls if "journalieres" in c.path]
    assert months == ["8", "9", "10", "11"]
    assert {c.method for c in http.backend_calls} == {"GET"}
    assert "/espace-client/pro" in [c.path for c in http.backend_calls]  # habilitation opened

    rows = await stats(hass)
    days = (backend.last_day - date(2026, 8, 15)).days + 1
    assert len(rows) == days
    assert rows[0]["start"] == local_midnight(date(2026, 8, 15), PARIS).timestamp()
    assert rows[-1]["start"] == local_midnight(date(2026, 11, 17), PARIS).timestamp()
    expected = sum(
        backend.litres(date.fromordinal(date(2026, 8, 15).toordinal() + i)) for i in range(days)
    )
    assert rows[-1]["sum"] == expected
    assert config.data["refresh_token"] == "refresh-1"


async def test_reimport_is_idempotent_and_continues_the_sum(
    hass: HomeAssistant, http: FakeHttp, backend: Backend, freezer: FrozenDateTimeFactory
) -> None:
    config = entry()
    await setup(hass, config)
    before = await stats(hass)
    http.calls.clear()

    backend.last_day = date(2026, 11, 18)
    freezer.move_to(datetime(2026, 11, 19, 11, 0, tzinfo=PARIS))
    async_fire_time_changed(hass)  # the daily trigger (10:xx) is now due: exactly one refresh
    await settle(hass)
    assert len([c for c in http.backend_calls if c.path.endswith("/facturation")]) == 1

    months = [c.kwargs["params"]["mois"] for c in http.backend_calls if "journalieres" in c.path]
    assert months == ["10", "11"]  # only the refresh window, not the whole history
    after = await stats(hass)
    assert after[: len(before)] == before
    assert len(after) == len(before) + 1
    assert after[-1]["sum"] == before[-1]["sum"] + 5000.0

    await config.runtime_data.async_refresh()
    await settle(hass)
    assert await stats(hass) == after


async def test_sensors(hass: HomeAssistant, http: FakeHttp) -> None:
    await setup(hass, entry())
    state = lambda key: hass.states.get(f"sensor.veolia_{SUB}_{key}")  # noqa: E731
    assert state("last_daily_volume").state == "5000.0"
    assert state("last_daily_volume").attributes["day"] == "2026-11-17"
    assert state("closed_day_baseline").state == "60.0"
    assert state("closed_day_baseline").attributes["day"] == "2026-11-15"
    assert state("meter_index").state == "1234.5"
    assert state("last_reading_date").state == "2026-11-17"
    assert state("reading_mode").state == "TELERELEVE"
    assert state("amount_due").state == "42.5"
    assert state("last_invoice_amount").state == "912.3"
    assert state("last_invoice_date").state == "2026-09-30"
    assert hass.states.get(f"binary_sensor.veolia_{SUB}_possible_leak").state == "off"


@pytest.mark.parametrize(
    ("sundays", "expected"),
    [
        ({date(2026, 11, 8): 185.0, date(2026, 11, 15): 327.0}, "on"),
        ({date(2026, 11, 8): 327.0, date(2026, 11, 15): 80.0}, "off"),
        ({date(2026, 11, 1): 327.0, date(2026, 11, 15): 327.0}, "off"),
    ],
)
async def test_leak_sensor(
    hass: HomeAssistant, http: FakeHttp, backend: Backend, sundays: dict, expected: str
) -> None:
    backend.litres = lambda day: sundays.get(day, 60.0 if day.weekday() == 6 else 5000.0)
    await setup(hass, entry())
    assert hass.states.get(f"binary_sensor.veolia_{SUB}_possible_leak").state == expected


async def test_leak_options(hass: HomeAssistant, http: FakeHttp) -> None:
    await setup(
        hass, entry(leak_threshold_l=50.0, closed_weekdays=["sun"], leak_consecutive_days=3)
    )
    assert hass.states.get(f"binary_sensor.veolia_{SUB}_possible_leak").state == "on"


async def test_refused_password_stops_everything(
    hass: HomeAssistant, http: FakeHttp, freezer: FrozenDateTimeFactory
) -> None:
    config = entry()
    await setup(hass, config)
    coordinator = config.runtime_data
    http.calls.clear()
    http.cognito = lambda c: auth_error("NotAuthorizedException")
    coordinator.client.session.forget_access_token()

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    # refresh token refused, then ONE password login refused
    assert [c.flow for c in http.cognito_calls] == ["REFRESH_TOKEN_AUTH", "USER_PASSWORD_AUTH"]
    assert http.backend_calls == []
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"auth_refused_{config.entry_id}")
    assert any(
        f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress()
    )

    for _ in range(3):  # later triggers send nothing at all
        await coordinator.async_refresh()
    for day in (19, 20, 21):
        freezer.move_to(datetime(2026, 11, day, 12, 0, tzinfo=PARIS))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert len(http.cognito_calls) == 2 and http.backend_calls == []


async def test_refused_password_at_startup(hass: HomeAssistant, http: FakeHttp) -> None:
    http.cognito = lambda c: auth_error("NotAuthorizedException")
    config = entry()
    await setup(hass, config)
    assert config.state is ConfigEntryState.SETUP_ERROR
    assert len(http.cognito_calls) == 1


async def test_backend_failure_is_not_an_auth_failure(hass: HomeAssistant, http: FakeHttp) -> None:
    config = entry()
    await setup(hass, config)
    http.backend = lambda c: FakeResponse(500)
    await config.runtime_data.async_refresh()
    assert config.runtime_data.last_update_success is False
    assert config.runtime_data.client.session.refused is None
    assert not hass.config_entries.flow.async_progress()
    assert hass.states.get(f"sensor.veolia_{SUB}_amount_due").state == "unavailable"


async def test_no_fetch_before_the_configured_hour(
    hass: HomeAssistant, http: FakeHttp, freezer: FrozenDateTimeFactory
) -> None:
    await setup(hass, entry(fetch_hour=10))
    http.calls.clear()
    freezer.move_to(datetime(2026, 11, 19, 9, 59, 59, tzinfo=PARIS))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert http.calls == []
    freezer.move_to(datetime(2026, 11, 19, 11, 0, 0, tzinfo=PARIS))
    async_fire_time_changed(hass)
    await settle(hass)
    assert len([c for c in http.backend_calls if c.path.endswith("/facturation")]) == 1


async def test_options_change_reloads_but_token_update_does_not(
    hass: HomeAssistant, http: FakeHttp
) -> None:
    config = entry()
    await setup(hass, config)
    first = config.runtime_data
    hass.config_entries.async_update_entry(config, data={**config.data, "refresh_token": "x"})
    await hass.async_block_till_done()
    assert config.runtime_data is first
    hass.config_entries.async_update_entry(config, options={"leak_threshold_l": 10.0})
    await hass.async_block_till_done()
    assert config.runtime_data is not first


async def test_diagnostics_hold_no_identifier(hass: HomeAssistant, http: FakeHttp) -> None:
    config = entry()
    await setup(hass, config)
    text = str(await async_get_config_entry_diagnostics(hass, config))
    for secret in (
        SUB,
        USER,
        PASSWORD,
        "PDS-TEST",
        "SYNTHETIC-IBAN",
        "refresh-1",
        "access-1",
        "1234.5",
    ):
        assert secret not in text
    assert "'possible_leak': False" in text and "'subscription_count': 1" in text


async def test_unload(hass: HomeAssistant, http: FakeHttp) -> None:
    config = entry()
    await setup(hass, config)
    assert await hass.config_entries.async_unload(config.entry_id)
    assert config.state is ConfigEntryState.NOT_LOADED
