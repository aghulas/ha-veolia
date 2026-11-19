# Veolia Eau (pro) — Home Assistant integration

Daily water consumption of a Veolia smart meter (télérelevé, [eau.veolia.fr](https://www.eau.veolia.fr))
in Home Assistant's statistics and Energy dashboard, for **business ("pro") accounts** as well as
residential ones, with **leak detection** based on the water used on days when the building is empty.
Read-only. License: MIT.

Unofficial, not affiliated with Veolia. This is a separate integration (domain `veolia_eau`) from the
community integration [Jezza34000/homeassistant_veolia](https://github.com/Jezza34000/homeassistant_veolia)
(MIT), whose work on the eau.veolia.fr API it builds on; `docs/decision.md` says why.

**Status: v0.1.** Running on a real Home Assistant (2026.11) with a business account since
2026-11-18: two years of daily volumes imported, identical day for day to an independent reading of
the same account. Tested with Home Assistant 2025.1 and 2026.10.

## Installation

### Marketplace (Home Assistant 2026.11 and later)

Home Assistant 2026.11 replaced HACS with the built-in **Marketplace**.

1. Settings → **Marketplace**. On first use, read and accept the warning about community content,
   then connect a GitHub account (⋮ → Reconfigure): adding a repository by link needs it.
2. **Add from link** (top right) → `https://github.com/aghulas/ha-veolia`, type **Integration**.
3. Open **Veolia Eau (pro)** in the list, install it, then restart Home Assistant (a Repairs issue
   offers to do it). Updates then appear under Settings → Updates.

### HACS (Home Assistant before 2026.11)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/aghulas/ha-veolia`, type
   **Integration**.
2. Install **Veolia Eau (pro)**, then restart Home Assistant.

### Manual

1. Copy the folder `custom_components/veolia_eau` of this repository into the `custom_components`
   folder of your Home Assistant configuration (create it if needed), so that you have
   `<config>/custom_components/veolia_eau/manifest.json`.
2. Restart Home Assistant.

## Set-up

Settings → Devices & services → **Add integration** → **Veolia Eau (pro)**.

1. Enter the e-mail and password of your eau.veolia.fr account.
2. **Verification code.** On a first login from a new place, Veolia e-mails a code to the account's
   address. The integration then asks for it: enter it within a few minutes. A wrong code can be
   typed again; an expired one sends you back to the login form.
3. If the account has several subscriptions, pick the ones to follow (all by default). Each one
   becomes a device.

At the first start the integration imports the history: every daily volume since the smart meter
was installed (within the backfill depth, 36 months by default), and the monthly volumes of the
months before the daily data. After that it fetches **once a day**, at the configured hour (10:00
by default) plus a random minute, and re-reads the last 35 days, because Veolia publishes each day
the next morning and may correct the last few days.

## Where the data goes

The consumption is an **external statistic** named `veolia_eau:<subscription>_water`, in **litres**,
one value per day at local midnight of the real day (not the day it was fetched). Re-importing is
idempotent: the same day is overwritten, never counted twice.

To add it to the Energy dashboard: Settings → Dashboards → **Energy** → **Water consumption** →
**Add water source** → choose *Veolia &lt;subscription&gt;*. Daily data only: the dashboard shows no
hourly profile for this source.

You can also show it with a **Statistics graph** card (statistic `veolia_eau:<subscription>_water`,
period *day*, stat type *change*).

### Hourly volumes (optional)

Turn on **Import the hourly volumes** in the options to get a second statistic,
`veolia_eau:<subscription>_water_hourly`, in litres, one value per hour Veolia publishes, at the
real start of that hour.

- **Without hourly reading turned on in your Veolia customer area, Veolia publishes only three
  night hours a day (02:00–05:00).** That is the night flow: on a building that is empty at night,
  a few litres per hour that never drop to zero point to a running flush or a leak, and you see it
  every night, not only on closed days. Show it with a Statistics graph card (period *hour*, stat
  type *change*).
- Turning hourly reading on is a change to your Veolia account (a consent). This integration never
  does it and never will; if you do it yourself on eau.veolia.fr, the full hourly curve is imported
  with nothing to change here.
- Hours Veolia does not publish have no value (not zero). Like the daily data, the hours of a day
  arrive the next morning.
- Cost: at the daily fetch, one request per day for the last 3 days (7 the first time). No other
  polling.
- Do not add it to the Energy dashboard next to the daily statistic: it would count the same water
  twice (and without the consent it covers three hours a day only).

## Entities

One device per subscription, with:

| Entity | Content |
|---|---|
| `sensor.veolia_<subscription>_last_daily_volume` | Volume of the last published day (L), with its date |
| `sensor.veolia_<subscription>_closed_day_baseline` | Volume of the last closed day (L), the leak indicator |
| `binary_sensor.veolia_<subscription>_possible_leak` | *Problem* when a leak is possible (see below) |
| `sensor.veolia_<subscription>_amount_due` | Amount still due on the account (€) |
| `sensor.veolia_<subscription>_last_invoice_amount`, `_last_invoice_date` | Last water invoice |
| `sensor.veolia_<subscription>_last_billed_volume` | Volume of the last billed period (m³), with its dates |
| `sensor.veolia_<subscription>_meter_index`, `_last_reading_date`, `_reading_mode` | Diagnostic |
| `button.veolia_<subscription>_refresh` | Fetch now (several presses in a row are one fetch) |

Entities become *unavailable* when a fetch fails; the statistic keeps everything already imported.

## Leak detection

On a day when the building is empty, whatever flows is a continuous flow: a running flush, a
dripping tap, a leak. The integration takes the volume of each **closed day** (the *closed-day
baseline*) and reports a **possible leak** when the last *N* closed days are **all strictly above the
threshold**.

When the possible leak turns on, a **Repairs issue** appears (Settings → System → Repairs) with the
threshold, the number of closed days and their volumes. It is raised once, stays while the leak is
possible and disappears by itself once a closed day is back under the threshold. To be notified
elsewhere (phone, e-mail), write an automation on the `possible_leak` binary sensor turning on.

Tune it in the integration's **options** (Settings → Devices & services → Veolia Eau (pro) →
Configure):

| Option | Default | Meaning |
|---|---|---|
| Days when the building is empty | Sunday | Closed weekdays |
| Holiday calendar | none | Any `calendar` entity (local calendar, CalDAV, Google…): every day covered by one of its events also counts as a closed day. Use all-day events. |
| Leak threshold on a closed day | 150 L | Volume above which a closed day is suspicious |
| Closed days in a row above the threshold | 2 | *N* |
| Raise a Repairs issue | on | Turn off to keep only the binary sensor |
| Hour of the daily fetch | 10 | Veolia publishes the previous day in the morning |
| History to import at first start | 36 months | Backfill depth |
| Import the hourly volumes | off | Hourly statistic (see *Hourly volumes* above) |

Pick a threshold above the normal baseline of your building (look at the *closed-day baseline*
sensor's history over a few weeks): fridges, a boiler filling up or a cleaner on a Sunday all use
some water.

Limits, said plainly:

- **The data is daily and published the next day.** A leak that starts on a closed day is seen at
  the earliest the morning after the *N*-th closed day above the threshold. With the defaults
  (Sundays only, *N* = 2) that is up to two weeks. This is a slow safety net, not an alarm: a burst
  pipe needs a sensor on site.
- A leak on working days only is invisible: open days are not looked at.
- A day Veolia has not published (or withdrew) is skipped; it neither raises nor ends a leak.
- If the holiday calendar cannot be read (removed, unavailable), the refresh still runs with the
  closed weekdays only and one warning is logged.

## Re-authentication

The integration logs in by itself and keeps the session's refresh token. If Veolia **refuses the
password** (changed, account locked, new verification required…), it makes **one attempt only**:
it stops polling at once, raises a Repairs issue and asks you to re-authenticate (Settings →
Devices & services → Veolia Eau (pro) → Reconfigure / Re-authenticate). Nothing is sent to Veolia
again until you enter a password it accepts, even after a restart. This avoids locking the account
by retrying a wrong password.

Other failures (Veolia down, network) are not authentication failures: the entities become
unavailable, one retry is made 30 minutes after a start-up, then the next daily fetch.

## Privacy

- **Read-only**: the integration only sends GET requests from a fixed list of Veolia routes. It never
  changes anything on the account (no consent, no hourly-step activation, no payment, no
  subscription change).
- The password and the refresh token are stored in Home Assistant's configuration entry (like any
  integration that logs in) and never logged.
- **Diagnostics** contain only the options (the holiday calendar as "set", not its name), counts,
  dates and flags (reading mode, last day published, number of closed days, possible leak, number
  of hours published for the last day when the hourly option is on, whether the login was refused). No e-mail, password, token, subscription, meter or delivery-point number,
  name, address or bank detail.
- The subscription number appears in device names, entity ids and the statistic id, so that several
  subscriptions stay apart.

## Development

`docs/` holds the API notes, the requirements and the design decision. Tests run against a synthetic
backend:

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check . && .venv/bin/mypy custom_components/veolia_eau && .venv/bin/pytest
```

---

*FR* — Consommation d'eau Veolia (compteur télérelevé, eau.veolia.fr) dans Home Assistant, y compris
pour les comptes professionnels, avec détection de fuite par le talon des jours de fermeture
(jours de la semaine choisis et calendrier des congés). Lecture seule. Installation par HACS
(dépôt personnalisé) ou en copiant `custom_components/veolia_eau`, puis configuration depuis
Paramètres → Appareils et services ; l'interface est traduite en français.
