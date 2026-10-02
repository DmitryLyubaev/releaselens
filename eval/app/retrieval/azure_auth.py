"""Keyless Azure tokens, as the owner, through the Azure CLI.

No API key is used or read anywhere in the benchmark: every Azure call carries a bearer token
for its scope. This does not sign in; `az login` is the owner's to run.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from azure.identity import AzureCliCredential

# A token is replaced this long before it expires, so no request, nor its retries after a
# throttle, goes out on a token about to lapse. Embedding the corpus takes about half an hour,
# so a run outlives the token it started with.
REFRESH_MARGIN_SECONDS = 300


class TokenSource:
    """A bearer token for one scope, cached until 5 minutes before it expires."""

    def __init__(self, scope: str, tenant_id: str, *, clock: Callable[[], float] = time.time) -> None:
        self._scope = scope
        self._credential = AzureCliCredential(tenant_id=tenant_id)
        self._clock = clock
        self._token: str | None = None
        self._expires_on = 0.0

    def token(self) -> str:
        if self._token is None or self._clock() >= self._expires_on - REFRESH_MARGIN_SECONDS:
            access = self._credential.get_token(self._scope)
            self._token, self._expires_on = access.token, float(access.expires_on)
        return self._token
