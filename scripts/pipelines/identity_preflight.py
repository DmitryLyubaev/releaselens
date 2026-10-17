"""One source-bound authentication diagnostic, with no delivery or ARM mutations."""
import argparse
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re

import httpx

from app.delivery_search.contracts import DeliveryRefused, canonical, exclusive_json
from .authority_transport import decode_authority
from .identity import IDENTITY_CHECKS, IdentityRefused, verify_principal
from .run import authenticate, jwt_claims
from .session import verify_source

ROLES = ("test", "prod", "verify")
CHECKS = {"principal_verified": True, "refresh_environment_verified": True,
          "refresh_request_verified": True, "refreshed_principal_verified": True}


def instant(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None: raise ValueError("timezone")
    return result


def require_context(authority: dict, env: dict, now: datetime) -> int:
    """A diagnostic packet cannot substitute for a paid execution authority."""
    try:
        if (authority["kind"] != "identity-preflight" or authority["pipeline_id"] != 2
            or not authority["approval_ref"] or not re.fullmatch(r"[a-z][a-z0-9-]{3,11}",authority["session"])
            or any(not re.fullmatch(r"[0-9a-f]{40}",authority[k]) for k in ("source_sha","tree_sha"))
            or env["BUILD_REASON"] != "Manual" or env["BUILD_SOURCEBRANCH"] != "refs/heads/main"
            or env["BUILD_SOURCEVERSION"] != authority["source_sha"]
            or env["P8_PREFLIGHT_SOURCE_SHA"] != authority["source_sha"]
            or env["SYSTEM_DEFINITIONID"] != "2" or env["SYSTEM_JOBATTEMPT"] != "1"
            or env["SYSTEM_STAGEATTEMPT"] != "1"
            or hashlib.sha256(canonical(authority)).hexdigest() != env["P8_PREFLIGHT_SHA256"]
            or not instant(authority["issued_at"]) <= now < instant(authority["expires_at"])
            or instant(authority["expires_at"])-instant(authority["issued_at"]) > timedelta(minutes=30)
            or set(authority["principals"]) != set(ROLES)):
            raise DeliveryRefused("authority")
        required = ("subscription_id","tenant_id","client_id","object_id","issuer","subject",
                    "service_connection_id","oidc_request_origin","oidc_path_prefix")
        for principal in authority["principals"].values():
            if (any(not isinstance(principal.get(k),str) or not principal[k] for k in required)
                or principal["oidc_request_origin"] != "https://dev.azure.com"
                or not principal["oidc_path_prefix"].startswith("/")):
                raise DeliveryRefused("identity")
        for key in ("client_id","object_id","service_connection_id"):
            if len({v[key] for v in authority["principals"].values()}) != 3:
                raise DeliveryRefused("identity")
        run_id = int(env["BUILD_BUILDID"])
        if run_id < 1: raise DeliveryRefused("authority")
        return run_id
    except (KeyError,TypeError,ValueError):
        raise DeliveryRefused("authority") from None


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
    parser.add_argument("role", choices=(*ROLES,"all"))
    parser.add_argument("--receipt-dir",type=Path,required=True)
    parser.add_argument("--guard-only",action="store_true")
    args = parser.parse_args(argv)
    try:
        authority = decode_authority(os.environ["P8_PREFLIGHT_JSON"].encode())
        run_id = require_context(authority,os.environ,datetime.now(timezone.utc))
        verify_source(authority)
        if args.guard_only:
            print("status=pass code=source-guard")
            return 0
        proof = {"kind":"identity-preflight-result","session":authority["session"],
                 "source_sha":authority["source_sha"],"tree_sha":authority["tree_sha"],
                 "run_id":run_id,"authority_sha256":hashlib.sha256(canonical(authority)).hexdigest()}
        if args.role == "all":
            for role in ROLES:
                receipt = json.loads((args.receipt_dir/(role+"-identity.json")).read_bytes())
                if (any(receipt.get(k) != v for k,v in proof.items()) or receipt.get("role") != role
                    or receipt.get("checks") != CHECKS or receipt.get("verified") is not True):
                    raise DeliveryRefused("identity")
            print("status=pass code=identity-preflight-all")
            return 0
        with httpx.Client(timeout=20,follow_redirects=False) as client:
            checks = check_identity(authority["principals"][args.role],client)
        args.receipt_dir.mkdir(parents=True,exist_ok=True)
        exclusive_json(args.receipt_dir/(args.role+"-identity.json"),proof | {
            "role":args.role,"at":datetime.now(timezone.utc).isoformat(),"checks":checks,"verified":True})
        print("status=pass code=identity-preflight")
        return 0
    except Exception as exc:
        code = DeliveryRefused(exc.code).code if isinstance(exc,DeliveryRefused) else "internal"
        check = exc.check if isinstance(exc,IdentityRefused) and exc.check in IDENTITY_CHECKS else "unclassified"
        with suppress(OSError):
            args.receipt_dir.mkdir(parents=True,exist_ok=True)
            exclusive_json(args.receipt_dir/(args.role+"-failure.json"),{
                "kind":"identity-preflight-failure","role":args.role,"code":code,"check":check})
        print("status=refused code="+code)
        return 2


if __name__ == "__main__": raise SystemExit(main())
