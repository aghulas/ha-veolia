# Changelog

## 0.2.1 — 2026-11-27

No change in behaviour; this release prepares the listing in the default repositories of HACS and
of the Home Assistant Marketplace.

- **Icon and logo**, shipped in the integration's `brand/` folder (Home Assistant 2026.3 and later
  show them). Original artwork — a water drop over daily bars — not the logo of Veolia.
- The HACS validation now runs all its checks, the brands check included.
- **Minimum Home Assistant version: 2025.1** (was announced as 2024.12). 2025.1 is the oldest
  version the test suite runs against; nothing older was ever tested.
- This changelog.

## 0.2.0 — 2026-11-19

- **Optional hourly statistic.** New option *Import the hourly volumes* (off by default): a second
  external statistic, `veolia_eau:<subscription>_water_hourly`, in litres, one value per hour Veolia
  publishes, at its real hour.
- Without hourly reading turned on in the Veolia customer area, Veolia publishes only three night
  hours (02:00–05:00) a day: enough to follow the night flow, a good leak indicator. If you turn
  hourly reading on yourself on eau.veolia.fr, the full curve is imported with no change. The
  integration never turns it on (read-only).
- Fetched during the existing once-a-day fetch, one request per day for the last 3 days;
  diagnostics show how many hours the last day had.
- Installation through the Home Assistant Marketplace documented in the README.

## 0.1.0 — 2026-11-18

First release: a read-only integration for Veolia smart water meters (eau.veolia.fr), business and
residential accounts.

- Login with the e-mailed verification code Veolia asks for on a first login from a new place; one
  attempt only when a password is refused, then re-authentication.
- Daily water consumption as an external statistic (`veolia_eau:<subscription>_water`, litres),
  dated on the real day, idempotent, backfilled since the smart meter was installed; usable in the
  Energy dashboard.
- Leak detection from the volume of closed days (weekdays and an optional holiday calendar), with a
  Repairs issue when a leak is possible.
- Sensors for the last daily volume, closed-day baseline, meter index, amount due, last invoice and
  last billed volume; manual refresh button; diagnostics without identifiers.
