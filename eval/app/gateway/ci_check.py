"""`python -m app.gateway.ci_check`: the gateway-check workflow's one call (spec §7.3, B3).

Runs in GitHub Actions as `id-releaselens-deploy`: it takes GitHub's OIDC token for the audience
`api://AzureADTokenExchange`, exchanges it through `ClientAssertionCredential` for a token for the
gateway's scope, makes one chat-completions call, and prints `status=<code>` and nothing else.
No URL, scope, token or exception message is ever printed, because the log is public: on any
failure it prints `status=error`.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from urllib.parse import quote

import httpx

from .client import TIMEOUT_S, chat_body

AUDIENCE = "api://AzureADTokenExchange"
DEPLOYMENT = "releaselens-chat"


def _assertion(env: Mapping[str, str], http: httpx.Client) -> str:
    url = env["ACTIONS_ID_TOKEN_REQUEST_URL"]
    # Appended, not passed as params: httpx replaces a URL's own query with `params`.
    url += ("&" if "?" in url else "?") + "audience=" + quote(AUDIENCE, safe="")
    response = http.get(url, headers={"Authorization": f"Bearer {env['ACTIONS_ID_TOKEN_REQUEST_TOKEN']}"})
    response.raise_for_status()
    return response.json()["value"]


def main(env: Mapping[str, str], http: httpx.Client, credential_factory: Callable[..., object]) -> int:
    try:
        credential = credential_factory(env["AZURE_TENANT_ID"], env["AZURE_CLIENT_ID"], lambda: _assertion(env, http))
        token = credential.get_token(env["GATEWAY_SCOPE"]).token      # type: ignore[attr-defined]
        response = http.post(
            env["GATEWAY_BASE_URL"].rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT_S,
            json=chat_body(env.get("GATEWAY_DEPLOYMENT") or DEPLOYMENT),
        )
    except Exception:
        # Not even its type: an exception's text can carry the URL, the scope or a token.
        print("status=error")
        return 1
    print(f"status={response.status_code}")
    return 0 if response.status_code == 200 else 1


if __name__ == "__main__":
    try:
        from azure.identity import ClientAssertionCredential
    except Exception:
        print("status=error")
        sys.exit(1)

    with httpx.Client(timeout=TIMEOUT_S) as client:
        sys.exit(main(os.environ, client, ClientAssertionCredential))
