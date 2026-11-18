# ha-veolia — Home Assistant integration for Veolia water (eau.veolia.fr), including business accounts

Guide for Claude Code sessions on this repo. Goal: **read-only** import of the water consumption of a
smart (télérelevé) Veolia meter into HA long-term statistics and the Energy dashboard (water section),
with **leak detection**, working for **business ("pro") accounts** as well as residential ones.

## First decision: contribute upstream or build our own

A community integration exists: **Jezza34000/homeassistant_veolia** (HACS, v2.x, eau.veolia.fr, imports
history into native statistics). It targets residential accounts. Session 1 must:

1. Clone it into `~/dev/upstream/homeassistant_veolia` (outside this repo) and read it: auth flow
   (Cognito), endpoints, statistics import, tests, license, maintenance activity.
2. Check what breaks for a **pro account** (see `docs/api.md`: habilitation call `/espace-client/pro`,
   `ERR_HABILATION_MANQUANTE`, subscription selection, `is_professionnel` flags).
3. Write the decision in `docs/decision.md` with the user:
   - **A. Contribute** (preferred if upstream is active and the changes are contained): this repo then
     holds our fork/branch work and notes; PRs go upstream.
   - **B. New integration** `veolia_eau` (domain distinct from upstream's) if upstream is inactive or
     the changes are structural.

## Non-negotiable rules

1. **Never retry a refused password** (Cognito `NotAuthorizedException`, user not confirmed, MFA…):
   stop polling, Repairs issue, reauth flow. Token refresh failures → one full re-login, then reauth.
2. **Poll little**: once a day after publication (D+1), jitter, re-import the last 7 days.
3. **Statistics with real dates**, idempotent, backfilled (since the smart meter was installed).
4. **Read-only**: allow-list of GET routes; never touch consent / hourly-step activation, payments,
   subscriptions.
5. **Secrets & personal data**: password, tokens, subscription number, meter number, PDS, names,
   addresses, IBAN/BIC (present in `/abonnements/.../facturation`!) — never in logs, diagnostics,
   fixtures or the repo. No data specific to the school. `git grep` before pushing.

## Reuse (do not rewrite)

- `~/dev/veolia-mcp/veolia_mcp/` — the MCP connector (private): `client.py` (allow-list, habilitation
  retry), `auth.py` (Cognito token refresh), `modeles.py` (response shapes, leak-relevant fields).
  The connector obtains its first tokens by observing a real browser login — HA must log in itself
  (Cognito SRP with username/password), see `docs/api.md`.
- `~/dev/veolia-mcp/skills/consommation-eau-veolia/SKILL.md` — semantics and the Sunday-baseline leak
  method.
- `~/dev/ha-danfoss-ally` — layout, CI, conventions.

## Definition of done (v0.1)

See `docs/requirements.md` §Acceptance.
