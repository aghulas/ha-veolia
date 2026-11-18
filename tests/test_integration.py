"""Set-up, statistics import, sensors, leak detection, refused password, diagnostics."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    MockPlatform,
    async_fire_time_changed,
    mock_integration,
    mock_platform,
)
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.veolia_eau.const import DOMAIN
from custom_components.veolia_eau.coordinator import event_days
from custom_components.veolia_eau.diagnostics import async_get_config_entry_diagnostics
from custom_components.veolia_eau.models import DailyReading
from custom_components.veolia_eau.statistics import build_rows, local_midnight, statistic_id

from .conftest import PASSWORD, SUB, USER, Backend
from .fakes import FakeHttp, FakeResponse, auth_error, auth_ok

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


async def wait_for(hass: HomeAssistant, condition: Callable[[], bool]) -> None:
    """Wait for a refresh started by a timer: it runs in its own task and waits on the recorder
    thread, so settling once does not guarantee it is over."""
    for _ in range(300):
        await settle(hass)
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


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
        DailyReading(date(2026, 10, 26), None, None, None),  # no volume: 0, sum carried over
        DailyReading(date(2026, 10, 27), 10.0, None, None),
        DailyReading(date(2026, 10, 28), None, None, None),  # after the last volume: no row
    ]
    monthly = [DailyReading(date(2026, 9, 1), 3000.0, None, None)]
    rows = build_rows(monthly, readings, 1000.0, PARIS)
    assert [(r["start"].isoformat(), r["state"], r["sum"]) for r in rows] == [
        ("2026-08-31T22:00:00+00:00", 3000.0, 4000.0),
        ("2026-10-23T22:00:00+00:00", 50.0, 4050.0),
        ("2026-10-24T22:00:00+00:00", 100.0, 4150.0),
        ("2026-10-25T23:00:00+00:00", 0.0, 4150.0),
        ("2026-10-26T23:00:00+00:00", 10.0, 4160.0),
    ]
    assert local_midnight(date(2026, 7, 1), PARIS).isoformat() == "2026-06-30T22:00:00+00:00"
    assert build_rows([], [], 5.0, PARIS) == []


async def test_a_day_veolia_withdraws_does_not_break_the_sum(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    config = entry()
    await setup(hass, config)
    before = {r["start"]: r for r in await stats(hass)}
    gone = date(2026, 11, 10)
    normal = backend.litres
    backend.litres = lambda day: None if day == gone else normal(day)
    await config.runtime_data.async_refresh()
    await settle(hass)
    after = {r["start"]: r for r in await stats(hass)}
    at = local_midnight(gone, PARIS).timestamp()
    assert after[at]["state"] == 0.0
    assert after[at]["sum"] == before[at]["sum"] - 5000.0
    rows = list(after.values())
    assert all(b["sum"] - a["sum"] == b["state"] for a, b in zip(rows, rows[1:], strict=False))
    assert rows[-1]["sum"] == list(before.values())[-1]["sum"] - 5000.0


async def test_setup_backfills_since_the_subscription_start(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    config = entry()
    await setup(hass, config)
    assert config.state is ConfigEntryState.LOADED
    months = [c.kwargs["params"]["mois"] for c in http.backend_calls if "journalieres" in c.path]
    assert months == ["11", "10", "9", "8"]
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
    freezer.move_to(datetime(2026, 11, 19, 9, 0, tzinfo=PARIS))  # before the daily trigger
    await config.runtime_data.async_refresh()
    await settle(hass)
    assert len([c for c in http.backend_calls if c.path.endswith("/facturation")]) == 1

    months = [c.kwargs["params"]["mois"] for c in http.backend_calls if "journalieres" in c.path]
    assert months == ["11", "10"]  # only the refresh window, not the whole history
    after = await stats(hass)
    assert after[: len(before)] == before
    assert len(after) == len(before) + 1
    assert after[-1]["sum"] == before[-1]["sum"] + 5000.0

    await config.runtime_data.async_refresh()
    await settle(hass)
    assert await stats(hass) == after


async def test_months_before_the_daily_data_come_from_monthly_volumes(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    backend.start, backend.daily_start = "2025-11-10", "2026-09-01"
    await setup(hass, entry())
    years = [c.kwargs["params"]["annee"] for c in http.backend_calls if "mensuelles" in c.path]
    assert years == ["2026", "2025"]
    found = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2025, 1, 1, tzinfo=dt_util.UTC),
        None,
        {statistic_id(SUB)},
        "hour",
        None,
        {"state", "sum"},
    )
    rows = found[statistic_id(SUB)]
    monthly = rows[:10]  # November 2025 … August 2026, one row on the 1st of each month
    assert [r["state"] for r in monthly] == [100_000.0] * 10
    assert monthly[0]["start"] == local_midnight(date(2025, 11, 1), PARIS).timestamp()
    assert monthly[-1]["start"] == local_midnight(date(2026, 8, 1), PARIS).timestamp()
    assert rows[10]["start"] == local_midnight(date(2026, 9, 1), PARIS).timestamp()
    assert rows[10]["sum"] == 1_000_000.0 + backend.litres(date(2026, 9, 1))
    assert len(rows) == 10 + (backend.last_day - date(2026, 9, 1)).days + 1


async def test_backfill_stops_where_veolia_refuses_older_months(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    backend.start = "2020-03-01"
    backend.daily_start, backend.daily_refused_before = "2026-07-01", "2026-07"
    backend.monthly_refused_before = 2025
    config = entry()
    await setup(hass, config)
    assert config.state is ConfigEntryState.LOADED
    daily = [c.kwargs["params"]["mois"] for c in http.backend_calls if "journalieres" in c.path]
    assert daily == ["11", "10", "9", "8", "7", "6"]  # June is refused: no older month is asked
    years = [c.kwargs["params"]["annee"] for c in http.backend_calls if "mensuelles" in c.path]
    assert years == ["2026", "2025", "2024"]  # 2024 is refused: 2023 is not asked
    found = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2020, 1, 1, tzinfo=dt_util.UTC),
        None,
        {statistic_id(SUB)},
        "hour",
        None,
        {"state", "sum"},
    )
    rows = found[statistic_id(SUB)]
    assert rows[0]["start"] == local_midnight(date(2025, 1, 1), PARIS).timestamp()
    assert [r["state"] for r in rows[:18]] == [100_000.0] * 18  # Jan 2025 … June 2026
    assert rows[18]["start"] == local_midnight(date(2026, 7, 1), PARIS).timestamp()


async def test_every_recent_month_refused_is_an_error(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    backend.daily_refused_before = "2026-12"
    config = entry()
    await setup(hass, config)
    reason = str(config.runtime_data.last_exception)
    assert config.runtime_data.last_update_success is False
    assert SUB not in reason and "<id>" in reason


async def test_current_month_without_data_yet_is_not_an_error(
    hass: HomeAssistant, http: FakeHttp, backend: Backend, freezer: FrozenDateTimeFactory
) -> None:
    """On the 1st, nothing is published for the new month: Veolia may refuse that month."""
    config = entry()
    await setup(hass, config)
    before = await stats(hass)
    backend.last_day = date(2026, 11, 30)
    refused = {"12"}
    daily = backend.__call__

    def december_refused(call):
        if "journalieres" in call.path and call.kwargs["params"]["mois"] in refused:
            return FakeResponse(422, {"message": "synthetic"})
        return daily(call)

    http.backend = december_refused
    freezer.move_to(datetime(2026, 12, 1, 9, 0, tzinfo=PARIS))
    await config.runtime_data.async_refresh()
    await settle(hass)
    assert config.runtime_data.last_update_success is True
    assert len(await stats(hass)) == len(before) + 13  # 18 … 30 November


async def test_monthly_volumes_are_not_fetched_again_after_the_first_run(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    backend.start, backend.daily_start = "2025-11-10", "2026-09-01"
    config = entry()
    await setup(hass, config)
    before = await stats(hass)
    http.calls.clear()
    await config.runtime_data.async_refresh()
    await settle(hass)
    assert not [c for c in http.backend_calls if "mensuelles" in c.path]
    assert await stats(hass) == before


async def test_refresh_button(hass: HomeAssistant, http: FakeHttp) -> None:
    await setup(hass, entry())
    http.calls.clear()
    for _ in range(3):  # several presses in a row are one fetch
        await hass.services.async_call(
            "button", "press", {"entity_id": f"button.veolia_{SUB}_refresh"}, blocking=True
        )
    await wait_for(hass, lambda: any(c.path.endswith("/factures") for c in http.backend_calls))
    await settle(hass)
    assert len([c for c in http.backend_calls if c.path.endswith("/facturation")]) == 1
    assert {c.method for c in http.backend_calls} == {"GET"}


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
    assert state("last_billed_volume").state == "131.5"
    assert state("last_billed_volume").attributes["from"] == "2026-06-30"
    assert state("last_billed_volume").attributes["to"] == "2026-09-30"
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


async def test_refused_password_at_startup(
    hass: HomeAssistant, http: FakeHttp, freezer: FrozenDateTimeFactory
) -> None:
    http.cognito = lambda c: auth_error("NotAuthorizedException")
    config = entry()
    await setup(hass, config)
    assert config.state is ConfigEntryState.LOADED  # no set-up retry loop
    assert any(
        f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress()
    )
    assert hass.states.get(f"sensor.veolia_{SUB}_amount_due").state == "unavailable"
    freezer.move_to(NOW + timedelta(hours=2))  # no delayed retry either after a refusal
    async_fire_time_changed(hass)
    await settle(hass)
    assert len(http.cognito_calls) == 1 and http.backend_calls == []


async def test_refusal_survives_a_restart_until_reauth(hass: HomeAssistant, http: FakeHttp) -> None:
    http.cognito = lambda c: auth_error("NotAuthorizedException")
    config = entry()
    await setup(hass, config)
    assert config.data["auth_refused"] == "NotAuthorizedException"
    assert len(http.cognito_calls) == 1

    for _ in range(3):  # restarts, reloads, option changes: the password is not sent again
        await hass.config_entries.async_reload(config.entry_id)
        await settle(hass)
    hass.config_entries.async_update_entry(config, options={"fetch_hour": 7})
    await settle(hass)
    assert len(http.cognito_calls) == 1 and http.backend_calls == []
    assert config.runtime_data.client.session.refused == "NotAuthorizedException"

    http.cognito = lambda c: auth_ok("a", "refresh-new")
    flow = next(
        f for f in hass.config_entries.flow.async_progress() if f["context"]["source"] == "reauth"
    )
    done = await hass.config_entries.flow.async_configure(flow["flow_id"], {"password": "new"})
    await settle(hass)
    assert done["reason"] == "reauth_successful"
    assert config.data["auth_refused"] is None
    assert config.runtime_data.last_update_success is True


async def test_backend_down_at_startup_is_retried_once_then_waits(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    later: list[tuple[float, Callable]] = []

    def call_later(hass_: HomeAssistant, delay: float, action: Callable) -> Callable[[], None]:
        later.append((delay, action))
        return lambda: None

    http.backend = lambda c: FakeResponse(503)
    config = entry()
    with patch("custom_components.veolia_eau.async_call_later", side_effect=call_later):
        await setup(hass, config)
    assert config.state is ConfigEntryState.LOADED  # no set-up retry loop by Home Assistant
    assert config.runtime_data.last_update_success is False
    assert len(http.backend_calls) == 1
    ((delay, retry),) = later  # exactly one delayed retry
    assert delay == 30 * 60

    http.backend = backend
    await retry(NOW + timedelta(minutes=30))
    await settle(hass)
    assert config.runtime_data.last_update_success is True
    assert hass.states.get(f"sensor.veolia_{SUB}_amount_due").state == "42.5"
    count = len(http.backend_calls)
    await retry(NOW + timedelta(minutes=31))  # a late duplicate does nothing once it succeeded
    await settle(hass)
    assert len(http.backend_calls) == count
    assert len(later) == 1


async def test_no_delayed_retry_when_the_first_fetch_succeeds(
    hass: HomeAssistant, http: FakeHttp
) -> None:
    with patch("custom_components.veolia_eau.async_call_later") as call_later:
        await setup(hass, entry())
    call_later.assert_not_called()


async def test_backend_failure_is_not_an_auth_failure(hass: HomeAssistant, http: FakeHttp) -> None:
    config = entry()
    await setup(hass, config)
    http.backend = lambda c: FakeResponse(500)
    await config.runtime_data.async_refresh()
    assert config.runtime_data.last_update_success is False
    assert config.runtime_data.client.session.refused is None
    assert not hass.config_entries.flow.async_progress()
    assert hass.states.get(f"sensor.veolia_{SUB}_amount_due").state == "unavailable"


async def test_daily_fetch_is_scheduled_once_a_day_at_the_configured_hour(
    hass: HomeAssistant, http: FakeHttp
) -> None:
    """The schedule is checked directly: moving a frozen clock and firing timers made this test
    hang now and then on the CI (Home Assistant's timers and the recorder under freezegun)."""
    scheduled: list[tuple[Callable, dict]] = []

    def track(hass_: HomeAssistant, action: Callable, **when: int) -> Callable[[], None]:
        scheduled.append((action, when))
        return lambda: None

    with patch("custom_components.veolia_eau.async_track_time_change", side_effect=track):
        await setup(hass, entry(fetch_hour=15))
    ((action, when),) = scheduled
    assert when["hour"] == 15
    assert 0 <= when["minute"] < 60 and 0 <= when["second"] < 60
    assert set(when) == {"hour", "minute", "second"}  # once a day, not every hour or minute

    http.calls.clear()
    await action(datetime(2026, 11, 18, 15, 7, 0, tzinfo=PARIS))
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


# --- Holiday calendar ---------------------------------------------------------------------

CALENDAR = "calendar.holidays"
LEAK = f"binary_sensor.veolia_{SUB}_possible_leak"
# Monday 16 and Tuesday 17 November as one all-day event (its end is exclusive), and a timed
# event on Thursday 12 that ends at midnight.
HOLIDAYS = [
    CalendarEvent(summary="synthetic", start=date(2026, 11, 16), end=date(2026, 11, 18)),
    CalendarEvent(
        summary="synthetic",
        start=datetime(2026, 11, 12, 8, 0, tzinfo=PARIS),
        end=datetime(2026, 11, 13, 0, 0, tzinfo=PARIS),
    ),
]
HOLIDAY_DAYS = {date(2026, 11, 12), date(2026, 11, 16), date(2026, 11, 17)}


class Holidays(CalendarEntity):
    """A calendar entity with fixed events, served by the real `calendar.get_events` service."""

    _attr_name = "Holidays"

    def __init__(self, events: list[CalendarEvent]) -> None:
        self.events = events
        self.queries: list[tuple[datetime, datetime]] = []

    @property
    def event(self) -> CalendarEvent | None:
        return None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        self.queries.append((start_date, end_date))
        return self.events


async def add_calendar(hass: HomeAssistant, events: list[CalendarEvent]) -> Holidays:
    calendar = Holidays(events)

    async def async_setup_platform(hass, config, async_add_entities, discovery_info=None) -> None:
        async_add_entities([calendar])

    mock_integration(hass, MockModule("test"))
    mock_platform(hass, "test.calendar", MockPlatform(async_setup_platform=async_setup_platform))
    assert await async_setup_component(hass, "calendar", {"calendar": {"platform": "test"}})
    await hass.async_block_till_done()
    assert hass.states.get(CALENDAR) is not None
    return calendar


def holiday_leak(backend: Backend) -> None:
    """300 L on the holidays, a quiet Sunday in between: a leak only if the holidays count."""
    backend.litres = lambda day: (
        300.0 if day in HOLIDAY_DAYS else 60.0 if day.weekday() == 6 else 5000.0
    )


def test_event_days() -> None:
    days = event_days(
        [
            {"start": "2026-11-16", "end": "2026-11-18"},  # all-day, two days
            {"start": "2026-10-24T22:00:00+02:00", "end": "2026-10-26T09:00:00+01:00"},  # DST
            {"start": "2026-11-12T08:00:00+01:00", "end": "2026-11-13T00:00:00+01:00"},
            {"start": "2026-11-01T23:30:00+00:00", "end": "2026-11-01T23:45:00+00:00"},  # UTC
            {"start": "2026-11-05T10:00:00+01:00", "end": "2026-11-05T10:00:00+01:00"},
        ],
        PARIS,
    )
    assert days == {
        date(2026, 11, 16),
        date(2026, 11, 17),
        date(2026, 10, 24),
        date(2026, 10, 25),
        date(2026, 10, 26),
        date(2026, 11, 12),
        date(2026, 11, 2),  # 00:30 in Paris
        date(2026, 11, 5),
    }


async def test_calendar_events_are_closed_days(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    calendar = await add_calendar(hass, HOLIDAYS)
    holiday_leak(backend)
    config = entry(holiday_calendar=CALENDAR)
    await setup(hass, config)
    baseline = dict(config.runtime_data.data[SUB].baseline)
    assert {d for d in baseline if d.weekday() != 6} == HOLIDAY_DAYS
    assert baseline[date(2026, 11, 15)] == 60.0  # the closed weekdays still count
    assert hass.states.get(LEAK).state == "on"  # the last two closed days: 16 and 17 November
    assert hass.states.get(f"sensor.veolia_{SUB}_closed_day_baseline").attributes["day"] == (
        "2026-11-17"
    )
    # One query, over the refresh window, in the Home Assistant time zone.
    assert calendar.queries == [
        (datetime(2026, 10, 14, tzinfo=PARIS), datetime(2026, 11, 19, tzinfo=PARIS))
    ]
    assert config.runtime_data.calendar_missed is False


async def test_without_a_calendar_only_the_weekdays_count(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    calendar = await add_calendar(hass, HOLIDAYS)  # exists, but is not configured
    holiday_leak(backend)
    config = entry()
    await setup(hass, config)
    assert {d.weekday() for d, _ in config.runtime_data.data[SUB].baseline} == {6}
    assert hass.states.get(LEAK).state == "off"
    assert calendar.queries == []


@pytest.mark.parametrize("case", ["missing", "unavailable", "failing"])
async def test_unreadable_calendar_falls_back_to_the_weekdays(
    hass: HomeAssistant,
    http: FakeHttp,
    backend: Backend,
    caplog: pytest.LogCaptureFixture,
    case: str,
) -> None:
    holiday_leak(backend)
    if case == "unavailable":
        hass.states.async_set(CALENDAR, "unavailable")
    elif case == "failing":
        calendar = await add_calendar(hass, HOLIDAYS)
        calendar.events = None  # type: ignore[assignment]  # the service call fails
    config = entry(holiday_calendar=CALENDAR)
    await setup(hass, config)
    assert config.state is ConfigEntryState.LOADED
    assert config.runtime_data.last_update_success is True
    assert config.runtime_data.calendar_missed is True
    assert {d.weekday() for d, _ in config.runtime_data.data[SUB].baseline} == {6}
    assert hass.states.get(LEAK).state == "off"
    warnings = [r for r in caplog.records if "holiday calendar" in r.getMessage()]
    assert len(warnings) == 1 and warnings[0].levelname == "WARNING"
    assert CALENDAR not in warnings[0].getMessage() and SUB not in warnings[0].getMessage()


async def test_calendar_loaded_after_the_entry_is_read_once_started(
    hass: HomeAssistant, http: FakeHttp, backend: Backend, caplog: pytest.LogCaptureFixture
) -> None:
    holiday_leak(backend)
    hass.set_state(CoreState.starting)
    started = []  # the callback scheduled for "Home Assistant has started", awaited by the test
    with patch(
        "custom_components.veolia_eau.async_at_started",
        side_effect=lambda hass, action: started.append(action) or (lambda: None),
    ):
        config = entry(holiday_calendar=CALENDAR)
        await setup(hass, config)
    assert len(started) == 1
    assert hass.states.get(LEAK).state == "off"
    assert config.runtime_data.calendar_missed is True
    assert not [r for r in caplog.records if r.levelname == "WARNING" and "calendar" in r.message]
    await add_calendar(hass, HOLIDAYS)
    http.calls.clear()
    hass.set_state(CoreState.running)
    await started[0](hass)
    await hass.async_block_till_done()
    assert hass.states.get(LEAK).state == "on"
    assert config.runtime_data.calendar_missed is False
    assert http.calls == []  # nothing is asked from Veolia again
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"possible_leak_{SUB}") is not None


async def test_started_callback_does_nothing_when_the_calendar_was_read(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    calendar = await add_calendar(hass, HOLIDAYS)
    hass.set_state(CoreState.starting)
    started = []
    with patch(
        "custom_components.veolia_eau.async_at_started",
        side_effect=lambda hass, action: started.append(action) or (lambda: None),
    ):
        config = entry(holiday_calendar=CALENDAR)
        await setup(hass, config)
    hass.set_state(CoreState.running)
    await started[0](hass)
    assert len(calendar.queries) == 1  # read at set-up only


# --- Leak Repairs issue -------------------------------------------------------------------


def leaking(backend: Backend, sunday_8: float, sunday_15: float) -> None:
    volumes = {date(2026, 11, 8): sunday_8, date(2026, 11, 15): sunday_15}
    backend.litres = lambda day: volumes.get(day, 60.0 if day.weekday() == 6 else 5000.0)


async def test_leak_issue_is_raised_once_then_deleted(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    issues = ir.async_get(hass)
    leaking(backend, 185.0, 327.5)
    config = entry()
    await setup(hass, config)
    issue = issues.async_get_issue(DOMAIN, f"possible_leak_{SUB}")
    assert issue is not None and issue.is_fixable is False
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == "possible_leak"
    assert issue.translation_placeholders == {
        "subscription": SUB,
        "threshold": "150",
        "days": "2",
        "volumes": "2026-11-08: 185 L, 2026-11-15: 327.5 L",
    }
    leaking(backend, 185.0, 400.0)  # still leaking: the issue is kept as it is
    await config.runtime_data.async_refresh()
    await settle(hass)
    assert issues.async_get_issue(DOMAIN, f"possible_leak_{SUB}") is issue
    leaking(backend, 185.0, 80.0)
    await config.runtime_data.async_refresh()
    await settle(hass)
    assert hass.states.get(LEAK).state == "off"
    assert issues.async_get_issue(DOMAIN, f"possible_leak_{SUB}") is None
    leaking(backend, 185.0, 327.5)  # a new leak is a new issue
    await config.runtime_data.async_refresh()
    await settle(hass)
    assert issues.async_get_issue(DOMAIN, f"possible_leak_{SUB}") is not None
    assert await hass.config_entries.async_unload(config.entry_id)
    assert issues.async_get_issue(DOMAIN, f"possible_leak_{SUB}") is None


async def test_leak_issue_survives_a_failed_refresh(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    leaking(backend, 185.0, 327.5)
    config = entry()
    await setup(hass, config)
    http.backend = lambda c: FakeResponse(500)
    await config.runtime_data.async_refresh()
    assert config.runtime_data.last_update_success is False
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"possible_leak_{SUB}") is not None


async def test_leak_issue_can_be_turned_off(
    hass: HomeAssistant, http: FakeHttp, backend: Backend
) -> None:
    leaking(backend, 185.0, 327.5)
    await setup(hass, entry(leak_repair_issue=False))
    assert hass.states.get(LEAK).state == "on"
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"possible_leak_{SUB}") is None


async def test_diagnostics_do_not_name_the_calendar(hass: HomeAssistant, http: FakeHttp) -> None:
    config = entry(holiday_calendar=CALENDAR)
    await setup(hass, config)
    text = str(await async_get_config_entry_diagnostics(hass, config))
    assert CALENDAR not in text and "'holiday_calendar': 'set'" in text
    assert "'holiday_calendar_missed': True" in text
