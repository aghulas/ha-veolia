"""Daily refresh of the subscriptions of one Veolia account."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import VeoliaApiError, VeoliaAuthRefused, VeoliaClient, VeoliaError
from .const import (
    CONF_AUTH_REFUSED,
    CONF_BACKFILL_MONTHS,
    CONF_CLOSED_WEEKDAYS,
    CONF_HOLIDAY_CALENDAR,
    CONF_LEAK_DAYS,
    CONF_LEAK_ISSUE,
    CONF_LEAK_THRESHOLD,
    CONF_SUBSCRIPTIONS,
    DEFAULT_BACKFILL_MONTHS,
    DEFAULT_CLOSED_WEEKDAYS,
    DEFAULT_LEAK_ISSUE,
    DOMAIN,
    ISSUE_AUTH_REFUSED,
    ISSUE_POSSIBLE_LEAK,
    LEAK_ISSUE_VOLUMES,
    REFRESH_WINDOW_DAYS,
    WEEKDAYS,
)
from .leak import DEFAULT_CONSECUTIVE, DEFAULT_THRESHOLD_L, closed_day_baseline, possible_leak
from .models import (
    BilledPeriod,
    DailyReading,
    Invoice,
    SubscriptionInfo,
    last_billed_period,
    last_invoice,
    monthly_as_readings,
    months_between,
    parse_daily,
    parse_monthly,
    parse_subscription_info,
)
from .statistics import async_has_statistics, async_import_daily

_LOGGER = logging.getLogger(__name__)

# Veolia's answers for a period it has nothing for (422 observed on a first backfill).
NO_DATA_STATUSES = frozenset({400, 404, 422})


@dataclass(frozen=True)
class SubscriptionData:
    """State of one subscription after a refresh."""

    info: SubscriptionInfo
    last_day: DailyReading | None
    baseline: list[tuple[date, float]]
    leak: bool
    invoice: Invoice | None
    billed: BilledPeriod | None


def leak_issue_id(sub: str) -> str:
    """Id of the Repairs issue of one subscription."""
    return f"{ISSUE_POSSIBLE_LEAK}_{sub}"


def event_days(events: list[dict[str, Any]], tz: Any) -> set[date]:
    """Every local day covered by the events of a `calendar.get_events` response.

    `start` and `end` are ISO strings: dates for an all-day event, date-times otherwise. The end
    is exclusive in both cases (an event ending at midnight does not cover the day that begins).
    """
    days: set[date] = set()
    for event in events:
        start, end = str(event["start"]), str(event["end"])
        if "T" in start:
            first = datetime.fromisoformat(start).astimezone(tz)
            last = datetime.fromisoformat(end).astimezone(tz)
            day, stop = first.date(), last.date()
            if last.time() != time.min or last <= first:  # the last day is partly covered
                stop += timedelta(days=1)
        else:
            day, stop = date.fromisoformat(start), date.fromisoformat(end)
        while day < stop:
            days.add(day)
            day += timedelta(days=1)
    return days


class VeoliaCoordinator(DataUpdateCoordinator[dict[str, SubscriptionData]]):
    """Fetches once per trigger; the daily trigger is set up in __init__.py (no interval)."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: VeoliaClient) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=None)
        self.client = client
        self.subscriptions: list[str] = list(entry.data[CONF_SUBSCRIPTIONS])
        # True when the holiday calendar is configured but could not be read at the last refresh.
        self.calendar_missed = False
        self._readings: dict[str, list[DailyReading]] = {}  # last window, to recompute the leak
        self._leak_issues: set[str] = set()  # subscriptions whose Repairs issue is raised

    def _option(self, key: str, default: Any) -> Any:
        return self.config_entry.options.get(key, default)

    async def _async_update_data(self) -> dict[str, SubscriptionData]:
        try:
            data = {sub: await self._refresh(sub) for sub in self.subscriptions}
            closed_dates = await self._closed_dates()
            data = {sub: self._with_leak(sub, found, closed_dates) for sub, found in data.items()}
        except VeoliaAuthRefused as err:
            # The session is now locked: nothing more is sent until the user re-authenticates.
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"{ISSUE_AUTH_REFUSED}_{self.config_entry.entry_id}",
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_AUTH_REFUSED,
                translation_placeholders={"code": err.code},
            )
            if self.config_entry.data.get(CONF_AUTH_REFUSED) != err.code:
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data={**self.config_entry.data, CONF_AUTH_REFUSED: err.code}
                )
            raise ConfigEntryAuthFailed(str(err)) from err
        except VeoliaError as err:
            raise UpdateFailed(str(err)) from err
        ir.async_delete_issue(
            self.hass, DOMAIN, f"{ISSUE_AUTH_REFUSED}_{self.config_entry.entry_id}"
        )
        self._sync_leak_issues(data)
        return data

    async def async_recompute_leak(self) -> None:
        """Read the holiday calendar again and redo the leak detection, without calling Veolia.

        Used once Home Assistant has started, when the calendar was not loaded yet at set-up.
        """
        if not self.data:
            return
        closed_dates = await self._closed_dates()
        data = {sub: self._with_leak(sub, found, closed_dates) for sub, found in self.data.items()}
        self._sync_leak_issues(data)
        self.async_set_updated_data(data)

    async def _closed_dates(self) -> set[date]:
        """Days of the refresh window covered by an event of the holiday calendar, if any.

        A calendar that cannot be read never fails the refresh: closed weekdays only.
        """
        self.calendar_missed = False
        entity_id = self._option(CONF_HOLIDAY_CALENDAR, None)
        if not entity_id:
            return set()
        tz = dt_util.get_default_time_zone()
        today = dt_util.now().date()
        start = datetime.combine(today - timedelta(days=REFRESH_WINDOW_DAYS), time.min, tz)
        end = datetime.combine(today + timedelta(days=1), time.min, tz)
        try:
            state = self.hass.states.get(entity_id)
            if state is None or state.state == STATE_UNAVAILABLE:
                raise HomeAssistantError("calendar not available")
            response = await self.hass.services.async_call(
                "calendar",
                "get_events",
                {"entity_id": entity_id, "start_date_time": start, "end_date_time": end},
                blocking=True,
                return_response=True,
            )
            found: Any = response
            return event_days(found[entity_id]["events"], tz)
        except (HomeAssistantError, vol.Invalid, KeyError, TypeError, ValueError) as err:
            self.calendar_missed = True
            # While Home Assistant starts, the calendar may simply not be loaded yet: the leak
            # detection is redone once it has started (see __init__.py), and warns then.
            _LOGGER.log(
                logging.WARNING if self.hass.state is CoreState.running else logging.DEBUG,
                "The holiday calendar cannot be read (%s): only the closed weekdays count",
                type(err).__name__,
            )
            return set()

    def _with_leak(
        self, sub: str, data: SubscriptionData, closed_dates: set[date]
    ) -> SubscriptionData:
        closed = {
            WEEKDAYS.index(d) for d in self._option(CONF_CLOSED_WEEKDAYS, DEFAULT_CLOSED_WEEKDAYS)
        }
        baseline = closed_day_baseline(
            self._readings.get(sub, []), closed_weekdays=closed, closed_dates=closed_dates
        )
        leak = possible_leak(
            baseline,
            float(self._option(CONF_LEAK_THRESHOLD, DEFAULT_THRESHOLD_L)),
            int(self._option(CONF_LEAK_DAYS, DEFAULT_CONSECUTIVE)),
        )
        return replace(data, baseline=baseline, leak=leak)

    def _sync_leak_issues(self, data: dict[str, SubscriptionData]) -> None:
        """One Repairs issue per subscription while a leak is possible, raised once."""
        enabled = bool(self._option(CONF_LEAK_ISSUE, DEFAULT_LEAK_ISSUE))
        for sub, found in data.items():
            if not (enabled and found.leak):
                self._leak_issues.discard(sub)
                ir.async_delete_issue(self.hass, DOMAIN, leak_issue_id(sub))
            elif sub not in self._leak_issues:
                self._leak_issues.add(sub)
                days = int(self._option(CONF_LEAK_DAYS, DEFAULT_CONSECUTIVE))
                threshold = float(self._option(CONF_LEAK_THRESHOLD, DEFAULT_THRESHOLD_L))
                last = found.baseline[-max(days, 1) :][-LEAK_ISSUE_VOLUMES:]
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    leak_issue_id(sub),
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_POSSIBLE_LEAK,
                    translation_placeholders={
                        "subscription": sub,
                        "threshold": f"{threshold:g}",
                        "days": str(days),
                        "volumes": ", ".join(f"{d.isoformat()}: {v:g} L" for d, v in last),
                    },
                )

    async def _refresh(self, sub: str) -> SubscriptionData:
        info = parse_subscription_info(await self.client.facturation(sub))
        if info is None:
            raise UpdateFailed("subscription record without delivery point or start date")
        today = dt_util.now().date()
        first_run = not await async_has_statistics(self.hass, sub)
        if first_run:  # backfill since the subscription started, within the configured depth
            months = int(self._option(CONF_BACKFILL_MONTHS, DEFAULT_BACKFILL_MONTHS))
            since = today - timedelta(days=31 * months)
        else:
            since = today - timedelta(days=REFRESH_WINDOW_DAYS)
        since = max(since, info.start)
        start = info.start.isoformat()

        # Newest month first. Veolia answers HTTP 4xx for a month it has no daily data for:
        # - an old month on a first run: the backfill simply stops there;
        # - a month of the refresh window (e.g. the current month on the 1st, before anything is
        #   published): it counts as empty, unless every month of the window is refused.
        rows: list[dict[str, Any]] = []
        recent = (today - timedelta(days=REFRESH_WINDOW_DAYS)).replace(day=1)
        answered, refusal = False, None
        for year, month in reversed(months_between(since, today)):
            try:
                rows += await self.client.daily(sub, info.pds, start, year, month)
                answered = True
            except VeoliaApiError as err:
                if err.status not in NO_DATA_STATUSES:
                    raise
                refusal = err
                if date(year, month, 1) >= recent:
                    continue
                _LOGGER.debug(
                    "Daily history stops before %04d-%02d (HTTP %s)", year, month, err.status
                )
                break
        if not answered and refusal is not None:
            raise refusal
        readings = [r for r in parse_daily(rows) if since <= r.day <= today]

        older: list[DailyReading] = []
        if first_run:  # months that only exist as monthly volumes (before the daily data)
            first_daily = min((r.day for r in readings if r.litres is not None), default=today)
            monthly: list[dict[str, Any]] = []
            for year in range(first_daily.year, since.year - 1, -1):
                if (year, 1) >= (first_daily.year, first_daily.month):
                    continue
                try:
                    monthly += await self.client.monthly(sub, info.pds, start, year)
                except VeoliaApiError as err:
                    if err.status not in NO_DATA_STATUSES:
                        raise
                    _LOGGER.debug("Monthly history stops before %04d (HTTP %s)", year, err.status)
                    break
            older = monthly_as_readings(parse_monthly(monthly), since, first_daily)
        await async_import_daily(self.hass, sub, f"Veolia {sub}", older, readings)

        self._readings[sub] = readings
        measured = [r for r in readings if r.litres is not None]
        return SubscriptionData(
            info=info,
            last_day=measured[-1] if measured else None,
            baseline=[],  # filled in by _with_leak
            leak=False,
            invoice=last_invoice(await self.client.invoices(sub)),
            billed=last_billed_period(await self.client.billing_readings(sub)),
        )
