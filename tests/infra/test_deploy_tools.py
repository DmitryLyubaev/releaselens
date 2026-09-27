"""deploy_tools: the smoke test, the empty-group check and the OIDC exchange, run against a
fake HTTP fetch and a fake price command so nothing reaches Azure or GitHub."""

import base64
import json
import subprocess
import sys
import urllib.error
import urllib.parse
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import deploy_tools as dt  # noqa: E402

URL = "https://releaselens.example.com"
IDENTITY = ("gpt-4.1-mini", "2025-04-14", "GlobalStandard")
PRICE_CMD = ["dotnet", "run", "--project", "src/ReleaseLens.Worker", "--", "price"]
ZERO_GUID = "00000000-0000-0000-0000-000000000000"
SUBJECT = "repo:DmitryLyubaev/releaselens:environment:azure"
OIDC_ENV = {
    "ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.actions.example.com/idtoken?api-version=2.0",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "SENTINEL-REQUEST-TOKEN",
}
ARM_LIST_URL = (
    f"https://management.azure.com/subscriptions/{ZERO_GUID}"
    "/resourceGroups/rg-releaselens/resources?api-version=2021-04-01"
)

KEY = "SENTINEL-KEY-123"
CONNECTION_STRING = "Host=db.example.com;Username=admin;Password=SENTINEL-CONN-456"
SECRETS = (KEY, "SENTINEL-CONN-456")
SMOKE_ARGV = [
    "smoke",
    "--url", URL,
    "--model", "gpt-4.1-mini",
    "--model-version", "2025-04-14",
    "--deployment-type", "GlobalStandard",
    "--price-cmd", "dotnet run --no-build -c Release --project src/ReleaseLens.Worker -- price",
]


def metadata(**overrides):
    # Task 8's price command gives 0.0061252 for exactly these three token counts.
    fields = {
        "tokensIn": 12345,
        "tokensOut": 678,
        "cacheReadInputTokens": 1024,
        "costUsd": 0.0061252,
        "provider": "azure-openai",
        "providers": ["azure-openai"],
        "model": "gpt-4.1-mini-2025-04-14",
        "degraded": False,
    }
    fields.update(overrides)
    return fields


def answer(**overrides):
    return {"answer": "Release 1.2 added X [E1].", "citations": [], "metadata": metadata(**overrides)}


def response(status, body, headers=None):
    return status, headers or {"Content-Type": "application/json"}, json.dumps(body).encode()


def make_jwt(claims):
    def segment(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{segment({'alg': 'RS256', 'typ': 'JWT'})}.{segment(claims)}.c2lnbmF0dXJl"


class FakeFetch:
    """Returns (or raises) each outcome in turn, then repeats the last one."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class Router:
    """A fake fetch that answers by host, one FakeFetch per host."""

    def __init__(self, **by_host):
        self.by_host = by_host
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.by_host[urllib.parse.urlsplit(request.full_url).hostname](request)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def price_run(cost="0.0061252", returncode=0):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, returncode, stdout=f"{cost}\n", stderr="")

    run.calls = calls
    return run


def smoke(fetch, run=None, deadline_s=300):
    clock = FakeClock()
    code = dt.run_smoke(
        URL, "test-key", IDENTITY, PRICE_CMD, deadline_s=deadline_s,
        fetch=fetch, sleep=clock.sleep, clock=clock.clock, run=run or price_run(),
    )
    return code, clock


def oidc_router(entra, arm=None):
    by_host = {
        "token.actions.example.com": FakeFetch(response(200, {"value": make_jwt({"sub": SUBJECT})})),
        "login.microsoftonline.com": FakeFetch(entra),
    }
    if arm is not None:
        by_host["management.azure.com"] = FakeFetch(*arm)
    return Router(**by_host)


# --- smoke -------------------------------------------------------------------------------------


def test_smoke_passes_on_a_priced_azure_answer(capsys):
    assert dt.evaluate_smoke(answer(), Decimal("0.0061252")) == []

    fetch = FakeFetch(response(200, answer()))
    code, _ = smoke(fetch)

    assert code == 0
    request = fetch.requests[0]
    assert request.full_url == f"{URL}/query"
    assert request.get_method() == "POST"
    assert request.get_header("X-api-key") == "test-key"
    assert json.loads(request.data) == {"question": "What changed in the latest release?"}
    out = capsys.readouterr().out
    assert "costUsd: 0.0061252" in out
    assert out.rstrip().endswith("smoke: pass")


def test_wrong_provider_fails():
    body = answer(provider="anthropic")

    assert dt.evaluate_smoke(body, Decimal("0.0061252")) == [
        'metadata.provider is "anthropic", expected "azure-openai"'
    ]
    code, _ = smoke(FakeFetch(response(200, body)))
    assert code == 1


@pytest.mark.parametrize("providers", [["azure-openai", "openai"], [], None])
def test_providers_list_must_be_exactly_azure(providers):
    failures = dt.evaluate_smoke(answer(providers=providers), Decimal("0.0061252"))

    assert failures == [f'metadata.providers is {json.dumps(providers)}, expected ["azure-openai"]']


@pytest.mark.parametrize("degraded", [True, None])
def test_degraded_fails(degraded):
    failures = dt.evaluate_smoke(answer(degraded=degraded), Decimal("0.0061252"))

    assert failures == [f"metadata.degraded is {json.dumps(degraded)}, expected false"]


@pytest.mark.parametrize("cost", [0, 0.0])
def test_zero_cost_fails(cost):
    # Recomputed at zero too, so the only failure is the one that says a call must cost something.
    failures = dt.evaluate_smoke(answer(costUsd=cost), Decimal("0"))

    assert failures == [f"metadata.costUsd is {cost}, expected more than 0"]


def test_cost_mismatch_at_six_places_fails():
    failures = dt.evaluate_smoke(answer(costUsd=0.0061252), Decimal("0.0061262"))

    assert failures == [
        "metadata.costUsd 0.006125 does not equal the recomputed 0.006126 at 6 decimal places"
    ]
    # A difference past the sixth place is not a mismatch.
    assert dt.evaluate_smoke(answer(costUsd=0.0061252), Decimal("0.00612524")) == []


def test_recompute_passes_cached_tokens_to_price():
    run = price_run("0.0061252")

    cost = dt.recompute_cost(metadata(), IDENTITY, PRICE_CMD, run=run)

    assert cost == Decimal("0.0061252")
    # price takes <input> <cached> <output>: uncached tokensIn, then cacheReadInputTokens.
    assert run.calls == [
        PRICE_CMD + ["azure-openai", "gpt-4.1-mini", "2025-04-14", "GlobalStandard", "12345", "1024", "678"]
    ]


def test_retries_503_then_passes():
    fetch = FakeFetch(
        urllib.error.URLError("connection refused"),
        TimeoutError("timed out"),
        response(503, {}),
        response(200, answer()),
    )

    code, clock = smoke(fetch)

    assert code == 0
    assert len(fetch.requests) == 4
    assert len(clock.sleeps) == 3


def test_401_fails_without_retry():
    run = price_run()
    fetch = FakeFetch(response(401, {"error": "invalid key"}))

    code, clock = smoke(fetch, run=run)

    assert code == 1
    assert len(fetch.requests) == 1
    assert clock.sleeps == []
    assert run.calls == []


def test_deadline_stops_retrying(capsys):
    fetch = FakeFetch(response(503, {}))

    code, clock = smoke(fetch, deadline_s=60)

    assert code == 1
    assert clock.now <= 60
    assert len(fetch.requests) == len(clock.sleeps) + 1 > 1
    assert "FAIL: no answer within 60s" in capsys.readouterr().out


def _echo(status):
    # A response that quotes the key and the connection string back, in its body and a header.
    body = {"error": f"bad key {KEY}", "db": CONNECTION_STRING}
    return status, {"X-Echo": f"{KEY} {CONNECTION_STRING}"}, json.dumps(body).encode()


def _answer_quoting_secrets():
    body = answer()
    body["answer"] = f"{KEY} {CONNECTION_STRING}"
    return 200, {"X-Echo": KEY}, json.dumps(body).encode()


def _raising_run(argv, **kwargs):
    raise RuntimeError(f"price failed for {argv} with {KEY} and {CONNECTION_STRING}")


def _leaking_run(argv, **kwargs):
    return subprocess.CompletedProcess(
        argv, 1, stdout=f"{KEY}\n", stderr=f"Unhandled exception: {CONNECTION_STRING} {KEY}"
    )


LEAK_SCENARIOS = {
    "401-echoing-both": ([_echo(401)], price_run()),
    "503-until-the-deadline": ([_echo(503)], price_run()),
    "connection-error-quoting-both": ([urllib.error.URLError(f"{KEY} {CONNECTION_STRING}")], price_run()),
    "200-then-price-run-raises": ([_answer_quoting_secrets()], _raising_run),
    "200-then-price-exits-1-quoting-both": ([_answer_quoting_secrets()], _leaking_run),
    "unexpected-error-quoting-both": ([ValueError(f"{KEY} {CONNECTION_STRING}")], price_run()),
}


@pytest.mark.parametrize("responses, run", LEAK_SCENARIOS.values(), ids=list(LEAK_SCENARIOS))
def test_key_and_connection_string_never_printed(capsys, responses, run):
    clock = FakeClock()
    fetch = FakeFetch(*responses)

    code = dt.main(
        SMOKE_ARGV,
        env={"SMOKE_API_KEY": KEY, "RELEASELENS_DB": CONNECTION_STRING},
        fetch=fetch, run=run, sleep=clock.sleep, clock=clock.clock,
    )

    assert code == 1
    # The key was sent, so its absence from the output is not vacuous.
    assert fetch.requests[0].get_header("X-api-key") == KEY
    captured = capsys.readouterr()
    for secret in SECRETS:
        assert secret not in captured.out
        assert secret not in captured.err


def test_missing_key_is_a_usage_error(capsys):
    assert dt.main(SMOKE_ARGV, env={}, fetch=FakeFetch(response(200, answer()))) == 2
    assert "SMOKE_API_KEY" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exit_info:
        dt.main(["smoke", "--url", URL])
    assert exit_info.value.code == 2


# --- empty resource group ----------------------------------------------------------------------


def test_check_empty_names_each_leftover():
    resources = [
        {"type": "Microsoft.App/containerApps", "name": "ca-releaselens-api"},
        {"type": "Microsoft.Storage/storageAccounts", "name": "stcreatedbyhand"},
    ]

    assert dt.check_empty(resources) == [
        "Microsoft.App/containerApps ca-releaselens-api",
        "Microsoft.Storage/storageAccounts stcreatedbyhand",
    ]
    assert dt.check_empty([]) == []


def test_list_follows_next_link():
    next_link = ARM_LIST_URL + "&%24skiptoken=page2"
    fetch = FakeFetch(
        response(200, {"value": [{"type": "A/b", "name": "one"}], "nextLink": next_link}),
        response(200, {"value": [{"type": "C/d", "name": "two"}]}),
    )

    resources = dt.list_group_resources(ZERO_GUID, "rg-releaselens", "arm-token", fetch)

    assert [r["name"] for r in resources] == ["one", "two"]
    assert [r.full_url for r in fetch.requests] == [ARM_LIST_URL, next_link]
    assert all(r.get_header("Authorization") == "Bearer arm-token" for r in fetch.requests)


def test_next_link_off_arm_is_refused():
    fetch = FakeFetch(response(200, {"value": [], "nextLink": "https://management.azure.com.example.com/x"}))

    with pytest.raises(dt.ToolError):
        dt.list_group_resources(ZERO_GUID, "rg-releaselens", "arm-token", fetch)
    assert len(fetch.requests) == 1


def test_check_empty_cli_fails_on_leftovers_and_passes_when_empty(capsys):
    argv = [
        "check-empty", "--subscription", ZERO_GUID, "--resource-group", "rg-releaselens",
        "--client-id", ZERO_GUID, "--tenant-id", ZERO_GUID,
    ]
    entra = response(200, {"token_type": "Bearer", "access_token": "SENTINEL-ARM-TOKEN"})
    leftover = {"value": [{"type": "Microsoft.Storage/storageAccounts", "name": "stcreatedbyhand"}]}

    router = oidc_router(entra, arm=[response(200, leftover)])
    assert dt.main(argv, env=OIDC_ENV, fetch=router) == 1
    arm_requests = [r for r in router.requests if "management.azure.com" in r.full_url]
    assert arm_requests[0].get_header("Authorization") == "Bearer SENTINEL-ARM-TOKEN"
    out = capsys.readouterr().out
    assert "Microsoft.Storage/storageAccounts stcreatedbyhand" in out
    assert "SENTINEL-ARM-TOKEN" not in out

    assert dt.main(argv, env=OIDC_ENV, fetch=oidc_router(entra, arm=[response(200, {"value": []})])) == 0


# --- OIDC --------------------------------------------------------------------------------------


def test_exchange_denied_returns_aadsts_code():
    fetch = FakeFetch(response(400, {
        "error": "invalid_client",
        "error_description": "AADSTS700213: No matching federated identity record found.",
        "error_codes": [700213],
    }))

    assert dt.exchange(ZERO_GUID, ZERO_GUID, "the-assertion", fetch=fetch) == (False, "AADSTS700213")
    request = fetch.requests[0]
    assert request.full_url == f"https://login.microsoftonline.com/{ZERO_GUID}/oauth2/v2.0/token"
    assert request.get_method() == "POST"
    assert urllib.parse.parse_qs(request.data.decode()) == {
        "client_id": [ZERO_GUID],
        "grant_type": ["client_credentials"],
        "scope": ["https://management.azure.com/.default"],
        "client_assertion_type": ["urn:ietf:params:oauth:client-assertion-type:jwt-bearer"],
        "client_assertion": ["the-assertion"],
    }


def test_exchange_ok_never_exposes_token(capsys):
    entra = response(200, {"token_type": "Bearer", "expires_in": 3599, "access_token": "SENTINEL-ACCESS-TOKEN"})

    result = dt.exchange(ZERO_GUID, ZERO_GUID, "the-assertion", fetch=FakeFetch(entra))

    assert result == (True, None)
    argv = ["oidc-exchange", "--client-id", ZERO_GUID, "--tenant-id", ZERO_GUID, "--expect", "ok"]
    assert dt.main(argv, env=OIDC_ENV, fetch=oidc_router(entra)) == 0
    captured = capsys.readouterr()
    assert captured.out == "exchange: ok\n"
    assert "SENTINEL-ACCESS-TOKEN" not in captured.err


@pytest.mark.parametrize("status, expect, code, line", [
    (400, "denied", 0, "exchange: denied (AADSTS700213)"),
    (400, "ok", 1, "exchange: denied (AADSTS700213)"),
    (200, "denied", 1, "exchange: ok"),
])
def test_oidc_exchange_cli_compares_with_expect(capsys, status, expect, code, line):
    body = {"access_token": "t"} if status == 200 else {"error_codes": [700213]}
    argv = ["oidc-exchange", "--client-id", ZERO_GUID, "--tenant-id", ZERO_GUID, "--expect", expect]

    assert dt.main(argv, env=OIDC_ENV, fetch=oidc_router(response(status, body))) == code
    assert capsys.readouterr().out.splitlines()[0] == line


def test_decode_subject():
    jwt = make_jwt({"sub": SUBJECT})
    assert len(jwt.split(".")[1]) % 4 != 0  # so the missing base64 padding is exercised

    assert dt.decode_subject(jwt) == SUBJECT


def test_oidc_subject_cli_prints_only_the_subject(capsys):
    jwt = make_jwt({"sub": SUBJECT})
    fetch = FakeFetch(response(200, {"value": jwt}))

    assert dt.main(["oidc-subject"], env=OIDC_ENV, fetch=fetch) == 0
    request = fetch.requests[0]
    assert request.full_url == OIDC_ENV["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=api://AzureADTokenExchange"
    assert request.get_header("Authorization") == "Bearer SENTINEL-REQUEST-TOKEN"
    captured = capsys.readouterr()
    assert captured.out == f"{SUBJECT}\n"
    assert "SENTINEL-REQUEST-TOKEN" not in captured.out + captured.err
    assert jwt not in captured.out + captured.err
