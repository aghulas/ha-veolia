"""Constants of the Veolia Eau integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "veolia_eau"
PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]

CONF_SUBSCRIPTIONS = "subscriptions"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_AUTH_REFUSED = "auth_refused"  # Cognito code of a refusal, kept across restarts

# Options
CONF_FETCH_HOUR = "fetch_hour"
CONF_BACKFILL_MONTHS = "backfill_months"
CONF_LEAK_THRESHOLD = "leak_threshold_l"
CONF_LEAK_DAYS = "leak_consecutive_days"
CONF_CLOSED_WEEKDAYS = "closed_weekdays"
CONF_HOLIDAY_CALENDAR = "holiday_calendar"  # optional calendar entity: its events are closed days
CONF_LEAK_ISSUE = "leak_repair_issue"  # raise a Repairs issue while a leak is possible
CONF_HOURLY = "hourly_statistic"  # opt-in: import the hourly volumes Veolia publishes

DEFAULT_FETCH_HOUR = 10  # the previous day is published the next morning
DEFAULT_BACKFILL_MONTHS = 36
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DEFAULT_CLOSED_WEEKDAYS = ["sun"]
DEFAULT_LEAK_ISSUE = True
DEFAULT_HOURLY = False

# Every refresh re-reads this many days (statistics are re-imported, idempotently). It covers the
# 7 days of corrections Veolia may publish and enough closed days for the leak baseline.
REFRESH_WINDOW_DAYS = 35

# Hourly volumes are fetched one day per GET. At the daily fetch, the last HOURLY_REFRESH_DAYS days
# (yesterday first) are re-read; the first time, HOURLY_BACKFILL_DAYS. Never more GETs than that.
HOURLY_REFRESH_DAYS = 3
HOURLY_BACKFILL_DAYS = 7
# Veolia's hourly timestamps carry no offset: they are French local time.
VEOLIA_TIME_ZONE = "Europe/Paris"

ISSUE_AUTH_REFUSED = "auth_refused"
ISSUE_POSSIBLE_LEAK = "possible_leak"
LEAK_ISSUE_VOLUMES = 5  # closed days quoted in the Repairs issue, at most

# After a failed first fetch (other than a refused login): one retry, this long after start-up.
STARTUP_RETRY_S = 30 * 60
