"""Captured Azure CLI boundary. Login remains the owner's responsibility."""
from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable


class TransportRefused(RuntimeError):
    def __init__(self, code: str):
        allowed = {"account_refused", "cli_refused", "invalid_response", "network", "response_limit", "invalid_origin", "invalid_cookie", "invalid_form", "release_refused"}
        super().__init__(code if code in allowed else "cli_refused")


class PrivateAz:
    def __init__(self, expected_tenant: str, expected_subscription: str, expected_principal: str, *, run: Callable = subprocess.run):
        self._tenant = expected_tenant
        self._subscription = expected_subscription
        self._principal = expected_principal
        self._run = run

    def __repr__(self): return "PrivateAz(captured account)"

    def _invoke(self, args: list[str]) -> dict:
        try:
            if not args or any(not isinstance(arg, str) or any(ord(c) < 32 for c in arg) for arg in args): raise ValueError()
            result = self._run(["az", *args, "--only-show-errors", "--output", "json"], capture_output=True, text=True, check=False, timeout=120, encoding="utf-8", errors="replace")
            if result.returncode != 0 or len(result.stdout) > 2 * 1024 * 1024: raise ValueError()
            value = json.loads(result.stdout) if result.stdout.strip() else {}
            if not isinstance(value, dict): raise ValueError()
            return value
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
            raise TransportRefused("cli_refused") from None

    def call(self, args: list[str]) -> dict:
        account = self._invoke(["account", "show"])
        user = account.get("user", {})
        principal = user.get("name") if isinstance(user, dict) else None
        kind = user.get("type") if isinstance(user, dict) else None
        match = all((self._tenant, self._subscription, self._principal)) and account.get("tenantId") == self._tenant and account.get("id") == self._subscription and isinstance(principal, str) and principal.casefold() == self._principal.casefold()
        if kind == "user":
            work = os.environ.get("WORK_DOMAIN", "")
            match = match and bool(work) and work.casefold() not in principal.casefold()
        elif kind != "servicePrincipal": match = False
        if not match: raise TransportRefused("account_refused")
        return self._invoke(args)
