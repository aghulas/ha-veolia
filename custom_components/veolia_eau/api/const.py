"""Constants of the eau.veolia.fr customer API (unofficial, see docs/api.md)."""

from __future__ import annotations

import re

COGNITO_URL = "https://cognito-idp.eu-west-3.amazonaws.com/"
# Public app client of the eau.veolia.fr web app (in its JS bundle): not a secret. Other Veolia
# portals (*.monespace.eau.veolia.fr) use other client ids; out of scope for now.
COGNITO_CLIENT_ID = "3kghade1fg54739kj8pkbova8j"

BACKEND_URL = "https://prd-ael-sirius-backend.istefr.fr"
ORIGIN = "https://www.eau.veolia.fr"

HABILITATION_PATH = "/espace-client/pro"
HABILITATION_ERROR = "ERR_HABILATION_MANQUANTE"  # sic: the backend's spelling

# Read-only allow-list: the client refuses any path that does not match, and only ever sends GET.
ALLOWED_PATHS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/espace-client"),  # residential account description (subscriptions)
    re.compile(r"/espace-client/pro"),  # business account: opens the habilitation server-side
    re.compile(r"/abonnements/\d+/facturation"),
    re.compile(r"/consommations/\d+/(journalieres|mensuelles|horaires)"),
    re.compile(r"/consommations/facturation/\d+"),
    re.compile(r"/documents-archive/abonnement/\d+/factures"),
)

REQUEST_TIMEOUT_S = 30
# Renew the access token this long before its announced expiry.
TOKEN_MARGIN_S = 120
