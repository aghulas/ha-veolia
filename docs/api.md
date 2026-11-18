# Veolia customer API (eau.veolia.fr) — as reverse-engineered for the MCP connector (Oct. 2026)

Unofficial, undocumented, may change. Source of truth: `~/dev/veolia-mcp/veolia_mcp/`.

## Authentication — AWS Cognito

- User pool region `eu-west-3`; public app client of the web app (id in the site's JS bundle and in
  `veolia_mcp/auth.py`; it is not a secret, but keep it in one constant).
- Endpoint `https://cognito-idp.eu-west-3.amazonaws.com/`, header
  `X-Amz-Target: AWSCognitoIdentityProviderService.InitiateAuth`, `Content-Type: application/x-amz-json-1.1`.
- Web app login = Amplify default **USER_SRP_AUTH** (no captcha at login as of Oct. 2026; a
  FriendlyCaptcha key exists in the bundle for other forms). HA must implement SRP (e.g. a maintained
  library) — **not yet tested programmatically**: the MCP connector captures the tokens from a real
  browser login. Validate once, supervised, and document the exact flow and error codes here.
- Access token ≈ 1 h; renew with `REFRESH_TOKEN_AUTH` (`AuthParameters.REFRESH_TOKEN`, plus `DEVICE_KEY`
  if device tracking ever appears). Observed once: refresh refused ≈ 40 h after login → full re-login.
- The web app stores tokens encrypted in localStorage (react-secure-storage) — irrelevant for HA.

## Backend — GET only, `Authorization: Bearer <access token>`, `Origin: https://www.eau.veolia.fr`

Base: `https://prd-ael-sirius-backend.istefr.fr`

| Path | Params | Use |
|---|---|---|
| `/espace-client/pro` | — | **opens the business habilitation** server-side; without it subscription routes answer `403 {"message":"ERR_HABILATION_MANQUANTE"}` (time-limited) |
| `/abonnements/<id>/facturation` | — | subscription: `numero_pds`, `date_debut_abonnement`, `mode_releve` (`TELERELEVE`), `dernier_index_releve`, `date_index_releve`, `solde`, `mode_paiement`, meter number… **also holds IBAN/BIC → never log** |
| `/consommations/<id>/journalieres` | `mois`, `annee`, `numero-pds`, `date-debut-abonnement` | daily rows |
| `/consommations/<id>/mensuelles` | `annee`, `numero-pds`, `date-debut-abonnement` | monthly rows |
| `/consommations/<id>/horaires` | `jour=YYYY-MM-DD`, `numero-pds`, `date-debut-abonnement` | hourly rows (partial unless hourly step consented) |
| `/consommations/facturation/<id>` | — | billing readings |
| `/documents-archive/abonnement/<id>/factures` | — | invoices list (`cle_document`, `code_document`, `date_emission`, `montant_facture_ttc`) |
| `/documents-archive/<cle>` | `is_professionnel=true` | `{ "url": <signed S3 URL> }` → PDF (never log the URL) |

Row fields: daily — `date_releve`, `consommation.litre`, `index.m3`, `fiabilite_index` (M measured,
E estimated, C corrected, R read); monthly — `annee`, `mois`, `consommation.m3`, `index.m3`,
`fiabilite_conso`; hourly — `heure_debut_releve`, `heure_fin_releve`, `consommation.litre`.

Do **not** use `solde_facture_ttc` from documents (unreliable: equals the amount even for old paid
invoices); the amount due is `solde` from `/facturation`.

Out of scope (writes, never call): hourly-step consent (`.../mettre-a-jour-pas-horaire`), payments,
direct debit, contact changes.
