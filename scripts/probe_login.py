"""Supervised probe of a real eau.veolia.fr account — run it yourself, once.

    .venv/bin/python scripts/probe_login.py

It asks for the e-mail and the password (not echoed, not stored), makes ONE login attempt
(USER_PASSWORD_AUTH) and two GETs, and prints only the *shape* of the answers: key names, types
and list lengths — never a value, a token or an identifier. Nothing is written to disk.
If the login is refused it stops: do not run it again with the same password.
"""

from __future__ import annotations

import asyncio
import getpass
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.veolia_eau.api import (  # noqa: E402
    CognitoSession,
    Tokens,
    VeoliaClient,
    VeoliaError,
)


def shape(value: Any, depth: int = 0) -> str:
    """Describe a JSON value without any of its content."""
    pad = "  " * depth
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = [f"{pad}  {key}: {shape(value[key], depth + 1)}" for key in sorted(value)]
        return "{\n" + "\n".join(lines) + f"\n{pad}}}"
    if isinstance(value, list):
        if not value:
            return "[] (empty)"
        return f"[{len(value)} × {shape(value[0], depth)}]"
    return type(value).__name__


async def main(username: str, password: str) -> int:
    seen: list[Tokens] = []
    # System resolver: the aiodns/pycares pair pulled in by the dev environment can be broken.
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    async with aiohttp.ClientSession(connector=connector) as http:
        session = CognitoSession(http, username, password, on_tokens=seen.append)
        client = VeoliaClient(http, session)
        try:
            await session.token()
        except VeoliaError as err:
            print(f"LOGIN : échec — {err}. Arrêt, ne pas relancer avec le même mot de passe.")
            return 1
        tokens = seen[-1]
        print("LOGIN : accepté en USER_PASSWORD_AUTH")
        print(f"  refresh token renvoyé : {'oui' if tokens.refresh_token else 'non'}")
        print(f"  durée du jeton d'accès : {tokens.expires_at - time.time():.0f} s")
        for label, call in (
            ("/espace-client/pro", client.espace_client_pro),
            ("/espace-client?type-front=WEB_ORDINATEUR", client.espace_client),
        ):
            try:
                print(f"GET {label} :\n{shape(await call())}")
            except VeoliaError as err:
                print(f"GET {label} : {err}")
    return 0


if __name__ == "__main__":
    _username = input("E-mail du compte Veolia : ").strip()
    _password = getpass.getpass("Mot de passe (non affiché) : ")
    sys.exit(asyncio.run(main(_username, _password)))
