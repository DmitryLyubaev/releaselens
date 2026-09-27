"""Delivery checks for the deploy and destroy workflows and the OIDC probe.

Subcommands:
  smoke          POST /query with the key in SMOKE_API_KEY, then check the answer came from
                 Azure OpenAI, undegraded, and priced above $0 at the cost the Worker's `price`
                 command recomputes from the answer's own token counts.
  check-empty    Fail, naming each one, if any resource is left in the resource group.
  oidc-subject   Print the `sub` claim of this job's GitHub OIDC token.
  oidc-exchange  Exchange that token with Entra and compare the outcome with --expect.

Exit codes: 0 pass, 1 check failed, 2 usage.

Standard library only. Nothing here prints the API key, a token, a request header or a response
body. The smoke test prints the metadata fields it checks; every other failure is reported by
HTTP status, AADSTS or ARM error code, or exception type.
"""

import argparse
import base64
import http.client
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation

AZURE = "azure-openai"
SMOKE_QUESTION = "What changed in the latest release?"
RETRY_INTERVAL_S = 10
HTTP_TIMEOUT_S = 120
PRICE_TIMEOUT_S = 300
SIX_PLACES = Decimal("0.000001")
SHOWN_FIELDS = (
    "provider", "providers", "model", "degraded", "costUsd",
    "tokensIn", "tokensOut", "cacheReadInputTokens",
)
PRICE_EXIT_MEANINGS = {1: "no rate for this pricing identity", 2: "usage error"}

GITHUB_AUDIENCE = "api://AzureADTokenExchange"
ARM_HOST = "management.azure.com"
ARM_SCOPE = f"https://{ARM_HOST}/.default"


class ToolError(Exception):
    """A failed check or call. Its message never quotes a response body, a header, a token or
    another exception's message, so it is safe to print."""


class UsageError(ToolError):
    """A required input is missing."""


class PriceError(ToolError):
    """The expected cost could not be recomputed."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # urllib forwards X-Api-Key and Authorization on a redirect, to whatever host the response
    # names. Refusing redirects keeps them on the host the caller chose; a 3xx comes back as a status.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _urlopen_fetch(request):
    try:
        with _OPENER.open(request, timeout=HTTP_TIMEOUT_S) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        # An error status is an answer, not a failure to connect, so it is returned like a 200.
        with error:
            return error.code, dict(error.headers), error.read()


def _json_or_none(raw, **kwargs):
    try:
        return json.loads(raw, **kwargs)
    except ValueError:
        return None


def _show(value):
    # JSON-encoded, so a value from a response cannot put a newline, and with it a workflow
    # command, into the log.
    return str(value) if isinstance(value, Decimal) else json.dumps(value, default=str)


def _as_cost(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    cost = Decimal(str(value))
    return cost if cost.is_finite() else None


# --- smoke -------------------------------------------------------------------------------------


def evaluate_smoke(body, expected_cost):
    """Return the smoke test's failure messages; an empty list is a pass."""
    metadata = body.get("metadata") if isinstance(body, dict) else None
    if not isinstance(metadata, dict):
        return ["the response has no metadata object"]

    failures = []
    if metadata.get("provider") != AZURE:
        failures.append(f"metadata.provider is {_show(metadata.get('provider'))}, expected {_show(AZURE)}")
    if metadata.get("providers") != [AZURE]:
        failures.append(f"metadata.providers is {_show(metadata.get('providers'))}, expected {_show([AZURE])}")
    if metadata.get("degraded") is not False:
        failures.append(f"metadata.degraded is {_show(metadata.get('degraded'))}, expected false")

    cost = _as_cost(metadata.get("costUsd"))
    if cost is None:
        failures.append(f"metadata.costUsd is {_show(metadata.get('costUsd'))}, expected a number")
        return failures
    if not cost > 0:
        failures.append(f"metadata.costUsd is {cost}, expected more than 0")
    actual, expected = cost.quantize(SIX_PLACES), expected_cost.quantize(SIX_PLACES)
    if actual != expected:
        failures.append(
            f"metadata.costUsd {actual} does not equal the recomputed {expected} at 6 decimal places"
        )
    return failures


def recompute_cost(metadata, identity, price_cmd, run=subprocess.run):
    """Price the answer's token counts with the Worker's `price` command, at the app's own rates.

    `tokensIn` is uncached input, so `cacheReadInputTokens` goes in the cached position and is
    priced at the cached rate on top of it.
    """
    counts = []
    for field in ("tokensIn", "cacheReadInputTokens", "tokensOut"):
        value = metadata.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PriceError(f"metadata.{field} is {_show(value)}, expected a token count")
        counts.append(str(value))

    model, version, deployment_type = identity
    argv = [*price_cmd, AZURE, model, version, deployment_type, *counts]
    try:
        result = run(argv, capture_output=True, text=True, check=False, timeout=PRICE_TIMEOUT_S)
    except Exception as error:
        # The exception's message can quote its command or environment; only its type is shown.
        raise PriceError(f"the price command could not run ({type(error).__name__})") from None

    # Its stderr and stdout are never shown: the command inherits the job's environment.
    if result.returncode != 0:
        meaning = PRICE_EXIT_MEANINGS.get(result.returncode, "failed")
        raise PriceError(
            f"the price command exited {result.returncode} ({meaning}) for "
            f"{AZURE} {model} {version} {deployment_type}"
        )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    try:
        cost = Decimal(lines[-1])
    except (IndexError, InvalidOperation):
        raise PriceError("the price command did not print a decimal cost") from None
    if not cost.is_finite():
        raise PriceError("the price command did not print a decimal cost")
    return cost


def run_smoke(url, api_key, identity, price_cmd, deadline_s=300, fetch=_urlopen_fetch,
              sleep=time.sleep, clock=time.monotonic, run=subprocess.run):
    """Ask /query one question, retrying a cold start until the deadline, and check the answer."""
    request = urllib.request.Request(
        url.rstrip("/") + "/query",
        data=json.dumps({"question": SMOKE_QUESTION}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "X-Api-Key": api_key},
    )
    deadline = clock() + deadline_s
    attempt = 0
    while True:
        attempt += 1
        try:
            status, _headers, raw = fetch(request)
        except (OSError, http.client.HTTPException) as error:
            # URLError, timeouts and dropped connections, all OSError or HTTPException.
            outcome = f"connection error ({type(error).__name__})"
        else:
            if status < 500:
                break
            outcome = f"HTTP {status}"
        remaining = deadline - clock()
        if remaining <= 0:
            print(f"FAIL: no answer within {deadline_s}s after {attempt} attempts; last: {outcome}")
            return 1
        print(f"smoke: attempt {attempt}: {outcome}; retrying")
        sleep(min(RETRY_INTERVAL_S, remaining))

    print(f"smoke: HTTP {status}")
    if status != 200:
        print(f"FAIL: /query returned HTTP {status}, which is not retried")
        return 1

    # Decimal, so costUsd is compared as the digits the API wrote rather than a float near them.
    body = _json_or_none(raw, parse_float=Decimal)
    metadata = body.get("metadata") if isinstance(body, dict) else None
    if not isinstance(metadata, dict):
        print("FAIL: the response has no metadata object")
        return 1
    for field in SHOWN_FIELDS:
        print(f"{field}: {_show(metadata.get(field))}")

    try:
        expected_cost = recompute_cost(metadata, identity, price_cmd, run=run)
    except PriceError as error:
        print(f"FAIL: {error}")
        return 1
    failures = evaluate_smoke(body, expected_cost)
    for failure in failures:
        print(f"FAIL: {failure}")
    print("smoke: fail" if failures else "smoke: pass")
    return 1 if failures else 0


# --- empty resource group ----------------------------------------------------------------------


def _arm_error_code(page):
    error = page.get("error") if isinstance(page, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return f" ({code})" if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9.]{1,100}", code) else ""


def list_group_resources(subscription_id, resource_group, arm_token, fetch=_urlopen_fetch):
    """Every resource in the group, across all of ARM's `nextLink` pages."""
    url = (
        f"https://{ARM_HOST}/subscriptions/{urllib.parse.quote(subscription_id, safe='')}"
        f"/resourceGroups/{urllib.parse.quote(resource_group, safe='')}"
        "/resources?api-version=2021-04-01"
    )
    resources = []
    while url:
        # The bearer token goes only to ARM, whatever host a nextLink names.
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or parts.netloc != ARM_HOST:
            raise ToolError(f"ARM returned a nextLink outside {ARM_HOST}; the token was not sent there")
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {arm_token}"})
        status, _headers, raw = fetch(request)
        page = _json_or_none(raw)
        if status != 200 or not isinstance(page, dict):
            raise ToolError(f"listing {resource_group} returned HTTP {status}{_arm_error_code(page)}")
        resources.extend(page.get("value") or [])
        url = page.get("nextLink")
    return resources


def check_empty(resources):
    """One `<type> <name>` line per resource left behind."""
    return [f"{resource.get('type')} {resource.get('name')}" for resource in resources]


# --- OIDC --------------------------------------------------------------------------------------


def github_oidc_token(audience=GITHUB_AUDIENCE, fetch=_urlopen_fetch, env=os.environ):
    """This job's GitHub OIDC token for the audience. The caller must not print it."""
    url = env.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not request_token:
        raise UsageError(
            "ACTIONS_ID_TOKEN_REQUEST_URL and ACTIONS_ID_TOKEN_REQUEST_TOKEN are not set; "
            "the job needs permissions: id-token: write"
        )
    request = urllib.request.Request(
        f"{url}&audience={urllib.parse.quote(audience, safe=':/')}",
        headers={"Authorization": f"Bearer {request_token}"},
    )
    status, _headers, raw = fetch(request)
    body = _json_or_none(raw)
    token = body.get("value") if isinstance(body, dict) else None
    if status != 200 or not isinstance(token, str) or not token:
        raise ToolError(f"the GitHub OIDC token request returned HTTP {status} without a token")
    return token


def decode_subject(jwt):
    """The token's `sub` claim. The signature is not checked: the subject is only displayed."""
    parts = jwt.split(".")
    if len(parts) != 3:
        raise ToolError("the OIDC token is not a JWT")
    payload = parts[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        raise ToolError("the OIDC token's payload is not base64url JSON") from None
    subject = claims.get("sub") if isinstance(claims, dict) else None
    if not isinstance(subject, str) or not subject:
        raise ToolError("the OIDC token has no sub claim")
    return subject


def _request_token(client_id, tenant_id, assertion, scope, fetch):
    """(access token or None, AADSTS code or None). The caller must not print the token."""
    request = urllib.request.Request(
        f"https://login.microsoftonline.com/{urllib.parse.quote(tenant_id, safe='')}/oauth2/v2.0/token",
        data=urllib.parse.urlencode({
            "client_id": client_id,
            "grant_type": "client_credentials",
            "scope": scope,
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": assertion,
        }).encode(),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    status, _headers, raw = fetch(request)
    body = _json_or_none(raw)
    if not isinstance(body, dict):
        body = {}
    if status == 200:
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise ToolError("the token endpoint returned HTTP 200 without an access token")
        return token, None
    if 400 <= status < 500:
        codes = body.get("error_codes")
        first = codes[0] if isinstance(codes, list) and codes else None
        valid = isinstance(first, int) and not isinstance(first, bool)
        return None, (f"AADSTS{first}" if valid else None)
    # A 5xx says nothing about whether the credential is trusted, so it is not a denial.
    raise ToolError(f"the token endpoint returned HTTP {status}")


def exchange(client_id, tenant_id, assertion, scope=ARM_SCOPE, fetch=_urlopen_fetch):
    """(ok, AADSTS code or None) for exchanging the assertion. The token itself is dropped here."""
    token, code = _request_token(client_id, tenant_id, assertion, scope, fetch)
    return token is not None, code


# --- CLI ---------------------------------------------------------------------------------------


def _smoke(args, env, fetch, run, sleep, clock):
    api_key = env.get("SMOKE_API_KEY")
    if not api_key:
        raise UsageError("SMOKE_API_KEY is not set")
    identity = (args.model, args.model_version, args.deployment_type)
    return run_smoke(args.url, api_key, identity, shlex.split(args.price_cmd),
                     fetch=fetch, sleep=sleep, clock=clock, run=run)


def _check_empty(args, env, fetch, **_):
    assertion = github_oidc_token(fetch=fetch, env=env)
    # Not exchange(): that drops the token, and this check needs it for ARM.
    arm_token, code = _request_token(args.client_id, args.tenant_id, assertion, ARM_SCOPE, fetch)
    if arm_token is None:
        raise ToolError(f"the Entra token exchange was denied ({code or 'no AADSTS code'})")
    leftovers = check_empty(list_group_resources(args.subscription, args.resource_group, arm_token, fetch))
    if leftovers:
        print(f"FAIL: {len(leftovers)} resource(s) left in {args.resource_group}:")
        for line in leftovers:
            print(f"  {line}")
        return 1
    print(f"check-empty: {args.resource_group} is empty")
    return 0


def _oidc_subject(args, env, fetch, **_):
    print(decode_subject(github_oidc_token(fetch=fetch, env=env)))
    return 0


def _oidc_exchange(args, env, fetch, **_):
    assertion = github_oidc_token(fetch=fetch, env=env)
    ok, code = exchange(args.client_id, args.tenant_id, assertion, fetch=fetch)
    print("exchange: ok" if ok else f"exchange: denied ({code or 'no AADSTS code'})")
    if ok != (args.expect == "ok"):
        print(f"FAIL: expected {args.expect}")
        return 1
    return 0


def _parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    smoke = commands.add_parser("smoke", help="POST /query and check the answer; the key is in SMOKE_API_KEY")
    smoke.add_argument("--url", required=True)
    smoke.add_argument("--model", required=True)
    smoke.add_argument("--model-version", required=True)
    smoke.add_argument("--deployment-type", required=True)
    smoke.add_argument("--price-cmd", required=True, help="the Worker price command, as one string")
    smoke.set_defaults(handler=_smoke)

    empty = commands.add_parser("check-empty", help="fail if the resource group holds anything")
    empty.add_argument("--subscription", required=True)
    empty.add_argument("--resource-group", required=True)
    empty.add_argument("--client-id", required=True)
    empty.add_argument("--tenant-id", required=True)
    empty.set_defaults(handler=_check_empty)

    subject = commands.add_parser("oidc-subject", help="print this job's OIDC sub claim")
    subject.set_defaults(handler=_oidc_subject)

    probe = commands.add_parser("oidc-exchange", help="exchange this job's OIDC token with Entra")
    probe.add_argument("--client-id", required=True)
    probe.add_argument("--tenant-id", required=True)
    probe.add_argument("--expect", required=True, choices=["ok", "denied"])
    probe.set_defaults(handler=_oidc_exchange)
    return parser


def main(argv=None, *, env=os.environ, fetch=_urlopen_fetch, run=subprocess.run,
         sleep=time.sleep, clock=time.monotonic):
    args = _parser().parse_args(argv)
    try:
        return args.handler(args, env=env, fetch=fetch, run=run, sleep=sleep, clock=clock)
    except UsageError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except ToolError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        # A traceback or an exception's message can quote a response body, a header or a token.
        print(f"error: failed with {type(error).__name__}; details withheld", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
