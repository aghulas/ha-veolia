"""Daily refresh of the subscriptions of one Veolia account."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import VeoliaApiError, VeoliaAuthRefused, VeoliaClient, VeoliaError
from .const import (
    CONF_BACKFILL_MONTHS,
    CONF_CLOSED_WEEKDAYS,
    CONF_LEAK_DAYS,
    CONF_LEAK_THRESHOLD,
    CONF_SUBSCRIPTIONS,
    DEFAULT_BACKFILL_MONTHS,
    DEFAULT_CLOSED_WEEKDAYS,
    DOMAIN,
    ISSUE_AUTH_REFUSED,
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


class VeoliaCoordinator(DataUpdateCoordinator[dict[str, SubscriptionData]]):
    """Fetches once per trigger; the daily trigger is set up in __init__.py (no interval)."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: VeoliaClient) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=None)
        self.client = client
        self.subscriptions: list[str] = list(entry.data[CONF_SUBSCRIPTIONS])

    def _option(self, key: str, default: Any) -> Any:
        return self.config_entry.options.get(key, default)

    async def _async_update_data(self) -> dict[str, SubscriptionData]:
        try:
            data = {sub: await self._refresh(sub) for sub in self.subscriptions}
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
            raise ConfigEntryAuthFailed(str(err)) from err
        except VeoliaError as err:
            raise UpdateFailed(str(err)) from err
        ir.async_delete_issue(
            self.hass, DOMAIN, f"{ISSUE_AUTH_REFUSED}_{self.config_entry.entry_id}"
        )
        return data

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

        # Newest month first: on a first run Veolia refuses (HTTP 4xx) months it has no daily data
        # for — the backfill then simply stops there. A refusal of a recent month is a real error.
        rows: list[dict[str, Any]] = []
        recent = (today - timedelta(days=REFRESH_WINDOW_DAYS)).replace(day=1)
        for year, month in reversed(months_between(since, today)):
            try:
                rows += await self.client.daily(sub, info.pds, start, year, month)
            except VeoliaApiError as err:
                if err.status not in NO_DATA_STATUSES or date(year, month, 1) >= recent:
                    raise
                _LOGGER.debug(
                    "Daily history stops before %04d-%02d (HTTP %s)", year, month, err.status
                )
                break
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
        await async_import_daily(self.hass, sub, f"Veolia {sub}", older + readings)

        closed = {
            WEEKDAYS.index(d) for d in self._option(CONF_CLOSED_WEEKDAYS, DEFAULT_CLOSED_WEEKDAYS)
        }
        baseline = closed_day_baseline(readings, closed_weekdays=closed)
        leak = possible_leak(
            baseline,
            float(self._option(CONF_LEAK_THRESHOLD, DEFAULT_THRESHOLD_L)),
            int(self._option(CONF_LEAK_DAYS, DEFAULT_CONSECUTIVE)),
        )
        measured = [r for r in readings if r.litres is not None]
        return SubscriptionData(
            info=info,
            last_day=measured[-1] if measured else None,
            baseline=baseline,
            leak=leak,
            invoice=last_invoice(await self.client.invoices(sub)),
            billed=last_billed_period(await self.client.billing_readings(sub)),
        )
