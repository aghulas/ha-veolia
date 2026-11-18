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

DEFAULT_FETCH_HOUR = 10  # the previous day is published the next morning
DEFAULT_BACKFILL_MONTHS = 36
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DEFAULT_CLOSED_WEEKDAYS = ["sun"]

# Every refresh re-reads this many days (statistics are re-imported, idempotently). It covers the
# 7 days of corrections Veolia may publish and enough closed days for the leak baseline.
REFRESH_WINDOW_DAYS = 35

ISSUE_AUTH_REFUSED = "auth_refused"

# After a failed first fetch (other than a refused login): one retry, this long after start-up.
STARTUP_RETRY_S = 30 * 60
