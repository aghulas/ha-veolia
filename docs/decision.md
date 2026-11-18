# Decision: contribute upstream or build our own

**Status: decided (session 1, 2026-11-18) — option B, new integration `veolia_eau`, confirmed by the user.**

## What was read

Clones in `~/dev/upstream/` (outside this repo), both MIT:

- `Jezza34000/homeassistant_veolia` — the HACS integration, domain `veolia`, `main` at `1c7dd90`
  (2026-09-29), ~1 150 lines, **no tests**. Latest GitHub release v2.0.2 (2026-01-06); tag v2.1.0.
- `Jezza34000/veolia-api` — the PyPI library that holds all the HTTP/auth logic (`veolia_api==2.1.1`),
  `main` at `2ef8eff` (2026-11-03), one 555-line module.

Nothing below was run against a real account: the pro-account analysis is from reading the code and
from what the MCP connector observed, not from a live test of upstream.

## Upstream: how it works

| Topic | Upstream behaviour |
|---|---|
| Login | Cognito `InitiateAuth` with **`USER_PASSWORD_AUTH`** (plain username/password over TLS, no SRP), same public client id as our MCP connector. Only the access token is kept. |
| Session | No refresh token. When the access token has expired (≈ 1 h, i.e. at every poll) it **logs in again with the password**. |
| Retries | `tenacity`: 5 attempts with backoff on network errors and HTTP 429, login request included. A Cognito refusal is not retried, but… |
| Errors | …any `VeoliaAPIError` (HTTP 403/500 on a data route, missing field) is raised as `ConfigEntryAuthFailed`, and the config flow has **no reauth step**. |
| Subscription | `GET /espace-client?type-front=WEB_ORDINATEUR`, then hard-coded `contacts[0].tiers[0].abonnements[0]`. One subscription per account. |
| Data | `/abonnements/<id>/facturation`, `/consommations/<id>/journalieres` and `/mensuelles`; also `/alertes/<pds>` and `/facturation/mensualisation/plan` at every refresh. |
| Polling | Every 6 h; 1 year at start-up, then the last 2 months. |
| Statistics | Imported under the **sensor entity ids** (`source="recorder"`), at midnight UTC, with the sum restarting from 0 at each import window; only when the entity is added. Known consequences upstream: date shift (#83), negative consumption (#95), history only refreshed on reload (#158). |
| Writes | `switch` and `text` platforms **POST `/alertes/<pds>`** (consumption alert thresholds, SMS/e-mail). |
| Logging | Debug logs contain the request headers (**`Authorization: Bearer …`**), the alert payload (contact, tiers, meter ids) and the raw daily data. No diagnostics platform. |
| Portals | `main` of the integration imports `veolia_api.portals`, which exists only in the v2.1.1 tag: multi-portal was reverted on the library's `main` (2026-04-20) and its restoration (veolia-api#38) has been open since July. |

## Upstream: activity

- The maintainer merges dependency bumps every few weeks and merged one contributor refactor in a
  week (#136, August 2026).
- He stated on 2025-10-22 (issue #30) that he has moved and **no longer has a Veolia account**; the
  README asks for developers to take the project forward.
- Functional PRs wait: veolia-api#33 (a correct 1-bug fix) was closed unmerged after 5 months;
  veolia-api#38 open for 4 months; multi-subscription (#30) open for 14 months; #157 (external
  statistics dated on the real day — the same design as our rule 3) opened 2026-11-14, no review yet.
- No issue or PR mentions business accounts, `/espace-client/pro` or `ERR_HABILATION_MANQUANTE`.

## What breaks for a business account (expected, not tested)

1. **Subscription discovery.** Upstream reads the residential `/espace-client` payload and takes the
   first subscription. The web app uses `/espace-client/pro` for a business account; whether the
   residential route answers for a pro login, and with which shape, is unknown. If the shape differs,
   the config flow ends in "unknown error".
2. **Habilitation.** Nothing calls `/espace-client/pro`, so `/abonnements/<id>/facturation` would
   answer `403 ERR_HABILATION_MANQUANTE` during `login()`; there is no retry for it.
3. **Side calls.** `/alertes` and `mensualisation/plan` are fetched at every refresh; a non-200/204
   on `/alertes` fails the whole update and is reported as an authentication failure.
4. **Several subscriptions** per account are not supported (also needed by residential users).

## Against our non-negotiable rules

| Rule | Upstream | Gap |
|---|---|---|
| 1. Never retry a refused password; refresh → one re-login → reauth | Password re-sent at every poll, no refresh token, no reauth flow, data errors mislabelled as auth failures | Rewrite of auth and error handling (library) and of the config flow |
| 2. Poll once a day, jitter, re-import 7 days | Every 6 h, 2 months | Rewrite of the coordinator |
| 3. Real-date, idempotent, backfilled statistics | See above | Replacement (what #157 proposes) |
| 4. Read-only, GET allow-list | POST `/alertes`, no allow-list | Removal of two platforms that existing users rely on |
| 5. No secrets in logs/diagnostics | Bearer token in debug logs | Library change + diagnostics to add |

## Options

**A. Contribute.** Benefits: existing user base, one integration instead of two. Costs: changes span
two repositories and need a library release on PyPI by a maintainer who cannot test; they touch
nearly every file (auth, discovery, coordinator, statistics, config flow); rule 4 conflicts with
features upstream ships; no test suite to protect existing users from our changes; review latency of
months on functional PRs.

**B. New integration `veolia_eau`** (this repo, already scaffolded for it). Benefits: our rules by
construction, tests from day one, client code in the integration (no separate library to release),
no dependency on upstream review. Costs: a second Veolia integration in the ecosystem; we maintain it.

## Recommendation: B

Upstream is alive but not in a position to review or test structural work, and the changes are
structural and partly contrary to its feature set. What we take from it (MIT, with attribution in
the README):

- `USER_PASSWORD_AUTH` is accepted by the web client id → probably **no SRP needed**. To validate
  once, supervised, together with the refresh token it should return (`docs/api.md` updated).
- The residential discovery route and shape (`contacts[].tiers[].abonnements[]` with
  `id_abonnement`, `numero_compteur`).
- Per-portal client ids exist (other `*.monespace.eau.veolia.fr` portals): keep the client id in one
  constant, out of scope for v0.1.

Giving back, once ours works: an issue upstream documenting the pro habilitation call and
multi-subscription listing, and a note on the bearer token in debug logs.

## Open questions before coding (session 2, supervised login)

1. Does `USER_PASSWORD_AUTH` work for the business account, and does it return a refresh token?
   One attempt only (rule 1).
2. How does a business account list its subscriptions — body of `/espace-client/pro` (the MCP
   connector ignores it and takes the subscription id from its configuration), or does
   `/espace-client?type-front=WEB_ORDINATEUR` also answer?
3. Exact Cognito error codes to map to reauth (`NotAuthorizedException`, `UserNotFoundException`,
   `UserNotConfirmedException`, `PasswordResetRequiredException`, MFA challenges).
