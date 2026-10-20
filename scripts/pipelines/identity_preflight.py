"""One authentication diagnostic on the delivery settings, with no delivery or ARM mutations."""
import argparse
from contextlib import suppress
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import httpx

from app.delivery_search.contracts import DeliveryRefused, exclusive_json
from .identity import IDENTITY_CHECKS, IdentityRefused, verify_principal
from .run import authenticate, jwt_claims
from .settings import ROLES, Settings, check_principals, load_settings

CHECKS = {"principal_verified": True, "refresh_environment_verified": True,
          "refresh_request_verified": True, "refreshed_principal_verified": True}


def require_context(env: dict) -> tuple[Settings, int]:
    """A manual run on main with valid settings; it proves all three roles, so no role_for."""
    try:
        if env["BUILD_REASON"] != "Manual" or env["BUILD_SOURCEBRANCH"] != "refs/heads/main":
            raise DeliveryRefused("authority")
        run_id = int(env["BUILD_BUILDID"])
        if run_id < 1: raise DeliveryRefused("authority")
    except (KeyError, TypeError, ValueError):
        raise DeliveryRefused("authority") from None
    settings = load_settings(env)
    check_principals(settings.principals)
    return settings, run_id


def check_identity(principal: dict, client: httpx.Client) -> dict:
    account, claims, env = authenticate(principal)
    # authenticate already validates this exact job's refresh origin/path and
    # connection. Exercise the token request too, without retaining either token.
    try:
        response = client.post(env["ARM_OIDC_REQUEST_URL"],
            params={"serviceConnectionId":principal["service_connection_id"],"api-version":"7.1-preview.1"},
            headers={"Authorization":"Bearer "+env["ARM_OIDC_REQUEST_TOKEN"]},json={})
        if response.status_code != 200: raise IdentityRefused("refresh-request")
        token = response.json().get("oidcToken")
        if not isinstance(token,str) or not token: raise IdentityRefused("refresh-result")
        refreshed = jwt_claims(token)
        verify_principal(account,{"federation":refreshed,"access":claims["access"]},principal)
    except (httpx.HTTPError,ValueError,TypeError,AttributeError):
        raise IdentityRefused("refresh-result") from None
    return dict(CHECKS)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=(*ROLES, "all"))
    parser.add_argument("--receipt-dir", type=Path, required=True)
    parser.add_argument("--guard-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        settings, run_id = require_context(os.environ)
        if args.guard_only:
            print("status=pass code=source-guard")
            return 0
        proof = {"kind": "identity-preflight-result", "run_id": run_id}
        if args.role == "all":
            for role in ROLES:
                receipt = json.loads((args.receipt_dir/(role+"-identity.json")).read_bytes())
                if (not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in proof.items())
                    or receipt.get("role") != role or receipt.get("checks") != CHECKS
                    or receipt.get("verified") is not True):
                    raise DeliveryRefused("identity")
            print("status=pass code=identity-preflight-all")
            return 0
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            checks = check_identity(settings.principals[args.role], client)
        args.receipt_dir.mkdir(parents=True, exist_ok=True)
        exclusive_json(args.receipt_dir/(args.role+"-identity.json"), proof | {
            "role": args.role, "at": datetime.now(timezone.utc).isoformat(), "checks": checks, "verified": True})
        print("status=pass code=identity-preflight")
        return 0
    except Exception as exc:
        code = DeliveryRefused(exc.code).code if isinstance(exc, DeliveryRefused) else "internal"
        check = exc.check if isinstance(exc, IdentityRefused) and exc.check in IDENTITY_CHECKS else "unclassified"
        with suppress(OSError):
            args.receipt_dir.mkdir(parents=True, exist_ok=True)
            exclusive_json(args.receipt_dir/(args.role+"-failure.json"), {
                "kind": "identity-preflight-failure", "role": args.role, "code": code, "check": check})
        print("status=refused code="+code)
        return 2


if __name__ == "__main__": raise SystemExit(main())
