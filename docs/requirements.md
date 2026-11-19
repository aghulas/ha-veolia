# Requirements — ha-veolia

## Use cases

- Water consumption of a smart Veolia meter in HA's Energy dashboard (water) and statistics, per day.
- **Leak detection** for a building that is empty at known times (weekends, holidays): a continuous flow
  shows up as a non-zero baseline when nobody is there.
- Invoices: last invoice amount and date, amount still due (business accounts pay by TIP: unpaid
  invoices lead to reminders).

## Configuration

- Config flow: username, password → login → list subscriptions (residential and pro) → pick one or
  several. Options: backfill depth, fetch hour, leak thresholds, "closed days" definition
  (default: Sunday; optionally a calendar entity for holidays).
- Reauth flow; never a silent retry of a refused password.

## Data and entities

| Data | Source | In HA |
|---|---|---|
| Daily volume (L) | `consommations/<id>/journalieres` (per month) | external statistic `<domain>:<sub>_water` (sum, L or m³), backfilled |
| Monthly volume (m³) | `consommations/<id>/mensuelles` (per year) | used for backfill before daily data exists |
| Hourly volume (L) | `consommations/<id>/horaires` (per day) | opt-in external statistic `<domain>:<sub>_water_hourly` (sum, L), the hours Veolia publishes |
| Billing readings | `consommations/facturation/<id>` | sensor "last billed period" |
| Last invoice amount/date, amount due | `documents-archive/abonnement/<id>/factures`, `abonnements/<id>/facturation` (`solde`) | sensors |
| Meter index, last reading date, reading mode | `abonnements/<id>/facturation` | diagnostic sensors |
| **Closed-day baseline** (L) | computed from daily data on closed days | sensor + statistic |
| **Possible leak** | baseline above threshold on N consecutive closed days (default 150 L, N = 2) | binary sensor (problem) + optional Repairs/notification |

Decisions for v0.1 (2026-11-18): the subscription number is accepted in device names, entity ids and
the statistic id (an exception to "no subscription number", agreed with the user, so that several
subscriptions stay apart); the hourly statistic is dropped, since it needs the hourly step enabled,
which is a write on the Veolia account.

Decision of 2026-11-19 (reverses the last point): **the hourly statistic is added, opt-in, default
off**. One read-only look at a real account without the hourly-step consent (approved by the
account owner) showed that `/horaires` still answers, every day, with the same three night hours
(02:00–05:00 local time), published with the daily data (D+1). That is the minimum night flow, a
useful leak signal on every night, not only on closed days. The integration imports whatever hours
Veolia publishes and still never asks for, offers or activates the consent; an account owner who
turns it on in Veolia's web app gets the full curve with no change here. Bounds: hourly data is
fetched during the existing daily fetch only, one GET per day, the last 3 days (7 the first time).
Hours Veolia does not publish get no row (unknown, not zero).

## Behaviour

- One fetch per day after publication with jitter; manual refresh button.
- On `403 ERR_HABILATION_MANQUANTE`: call `/espace-client/pro` once, retry once (business accounts).
- On 401: refresh the access token once (Cognito `REFRESH_TOKEN_AUTH`); if refused, one full login;
  if refused → stop + reauth.

## Non-functional

- aiohttp through HA; no blocking calls (Cognito SRP maths offloaded or async-safe library).
- Diagnostics redact tokens, e-mail, subscription/meter/PDS numbers, names, addresses, IBAN/BIC.
- Translations `en`, `fr`. Tests with pytest-homeassistant-custom-component, synthetic fixtures.

## Acceptance (v0.1)

1. A **business** account and a residential account (fixture) both configure and import history.
2. Daily statistics match the MCP connector's `veolia_consommations` for the same days.
3. The leak binary sensor turns on for a synthetic series with 2 Sundays above 150 L, off otherwise.
4. A refused password → one attempt, Repairs, reauth, no further calls (test).
5. CI green (hassfest, HACS, ruff, mypy, pytest).
