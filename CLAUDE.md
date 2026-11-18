# ha-veolia — Home Assistant integration for Veolia water (eau.veolia.fr), including business accounts

Guide for Claude Code sessions on this repo. Goal: **read-only** import of the water consumption of a
smart (télérelevé) Veolia meter into HA long-term statistics and the Energy dashboard (water section),
with **leak detection**, working for **business ("pro") accounts** as well as residential ones.

## Status

Decision made in session 1 (2026-11-18): **new integration** `veolia_eau`, separate from the community
integration Jezza34000/homeassistant_veolia — see `docs/decision.md`. v0.1 is done and in use.

## Non-negotiable rules

1. **Never retry a refused password** (Cognito `NotAuthorizedException`, user not confirmed, MFA…):
   stop polling, Repairs issue, reauth flow. Token refresh failures → one full re-login, then reauth.
2. **Poll little**: once a day after publication (D+1), jitter, re-import the last 7 days.
3. **Statistics with real dates**, idempotent, backfilled (since the smart meter was installed).
4. **Read-only**: allow-list of GET routes; never touch consent / hourly-step activation, payments,
   subscriptions.
5. **Secrets & personal data**: password, tokens, subscription number, meter number, PDS, names,
   addresses, IBAN/BIC (present in `/abonnements/.../facturation`!) — never in logs, diagnostics,
   fixtures or the repo. No data specific to one site or account. `git grep` before pushing.
   Accepted exception: the subscription number in device names, entity ids and the statistic id.

Machine-specific notes (local paths of related private projects) live in `CLAUDE.local.md`,
which is not committed.

## Definition of done (v0.1)

See `docs/requirements.md` §Acceptance.
