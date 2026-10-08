"""`python -m app.gateway.ci_check`: the gateway-check workflow's one call (spec §7.3, B3).

Runs in GitHub Actions as `id-releaselens-deploy`: it takes GitHub's OIDC token for the audience
`api://AzureADTokenExchange`, exchanges it through `ClientAssertionCredential` for a token for the
gateway's scope, makes one chat-completions call, and prints `status=<code>`, with
` total_tokens=<n>` after it on a 200 (the figure B5 compares, which the owner enters by hand).
Nothing else is printed: no URL, scope, token or exception message, because the log is public. On
any failure it prints `status=error`. The libraries' own logging is switched off first, since
azure-identity logs a failed token request's exception text, and with no handler configured
Python's last-resort handler writes it to stderr.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable, Mapping
from urllib.parse import quote

import httpx

from .client import TIMEOUT_S, chat_body

AUDIENCE = "api://AzureADTokenExchange"
DEPLOYMENT = "releaselens-chat"


def silence_logging() -> None:
    """No library may log in this process: the workflow's log is public."""
    logging.disable(logging.CRITICAL)
    logging.lastResort = None


def _total_tokens(response: httpx.Response) -> int | None:
    try:
        usage = response.json()["usage"]
        prompt, completion = usage["prompt_tokens"], usage["completion_tokens"]
    except (ValueError, KeyError, TypeError):
        return None
    return prompt + completion if type(prompt) is int and type(completion) is int else None


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
    tokens = _total_tokens(response) if response.status_code == 200 else None
    print(f"status={response.status_code}" + ("" if tokens is None else f" total_tokens={tokens}"))
    return 0 if response.status_code == 200 else 1


if __name__ == "__main__":
    silence_logging()       # before anything else, the imports included
    try:
        from azure.identity import ClientAssertionCredential
    except Exception:
        print("status=error")
        sys.exit(1)

    with httpx.Client(timeout=TIMEOUT_S) as client:
        sys.exit(main(os.environ, client, ClientAssertionCredential))
