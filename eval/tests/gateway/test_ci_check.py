"""`python -m app.gateway.ci_check`: one call as the workflow's identity, and nothing printed but a status."""

import json

import httpx

from app.gateway import ci_check

OIDC_URL = "https://pipelines.example.com/oidc?api-version=2.0"
OIDC_TOKEN = "fake-request-token-not-real"
GITHUB_JWT = "fake-github-jwt-not-real"
ACCESS = "fake-access-token-not-real"
TENANT = "00000000-0000-0000-0000-000000000000"
CLIENT = "11111111-1111-1111-1111-111111111111"
SCOPE = "api://22222222-2222-2222-2222-222222222222/.default"
BASE = "https://gateway.example.com/openai/v1/"

ENV = {
    "ACTIONS_ID_TOKEN_REQUEST_URL": OIDC_URL, "ACTIONS_ID_TOKEN_REQUEST_TOKEN": OIDC_TOKEN,
    "AZURE_TENANT_ID": TENANT, "AZURE_CLIENT_ID": CLIENT, "GATEWAY_SCOPE": SCOPE, "GATEWAY_BASE_URL": BASE,
}
SECRETS = (OIDC_URL, OIDC_TOKEN, GITHUB_JWT, ACCESS, TENANT, CLIENT, SCOPE, BASE, "example.com", "22222222")


def _run(gateway_status: int, capsys, env=None, raises: Exception | None = None):
    seen: list[httpx.Request] = []
    record: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "pipelines.example.com":
            return httpx.Response(200, json={"value": GITHUB_JWT})
        if raises:
            raise raises
        return httpx.Response(gateway_status, json={})

    class Credential:
        def __init__(self, tenant_id, client_id, assertion) -> None:
            record["built"] = (tenant_id, client_id)
            self._assertion = assertion

        def get_token(self, *scopes):
            record["scopes"] = scopes
            record["assertion_value"] = self._assertion()
            return type("AccessToken", (), {"token": ACCESS})()

    code = ci_check.main(ENV if env is None else env, httpx.Client(transport=httpx.MockTransport(handler)), Credential)
    return code, capsys.readouterr(), seen, record


def test_a_200_prints_status_200_and_returns_0(capsys):
    code, out, _, _ = _run(200, capsys)
    assert (code, out.out, out.err) == (0, "status=200\n", "")


def test_a_403_prints_status_403_and_returns_1(capsys):
    code, out, _, _ = _run(403, capsys)
    assert (code, out.out, out.err) == (1, "status=403\n", "")


def test_it_asks_github_for_the_entra_audience_and_uses_the_answer_as_the_assertion(capsys):
    _, _, seen, record = _run(200, capsys)
    oidc = seen[0]
    assert oidc.url.params["audience"] == "api://AzureADTokenExchange" and oidc.url.params["api-version"] == "2.0"
    assert oidc.headers["authorization"] == f"Bearer {OIDC_TOKEN}"
    assert record["built"] == (TENANT, CLIENT) and record["assertion_value"] == GITHUB_JWT
    assert record["scopes"] == (SCOPE,)


def test_it_makes_exactly_one_gateway_call_with_the_access_token(capsys):
    _, _, seen, _ = _run(200, capsys)
    (call,) = [r for r in seen if r.url.host == "gateway.example.com"]
    assert str(call.url) == BASE + "chat/completions" and call.headers["authorization"] == f"Bearer {ACCESS}"
    assert json.loads(call.content)["model"] == "releaselens-chat"


def test_nothing_secret_is_printed_on_success_or_failure(capsys):
    for status in (200, 403, 500):
        _, out, _, _ = _run(status, capsys)
        for secret in SECRETS:
            assert secret not in out.out + out.err


def test_any_exception_prints_status_error_and_nothing_else(capsys):
    code, out, _, _ = _run(200, capsys, raises=httpx.ConnectError(f"cannot reach {BASE} with {ACCESS}"))
    assert (code, out.out, out.err) == (1, "status=error\n", "")


def test_a_missing_variable_prints_status_error(capsys):
    env = {k: v for k, v in ENV.items() if k != "GATEWAY_SCOPE"}
    code, out, _, _ = _run(200, capsys, env=env)
    assert (code, out.out, out.err) == (1, "status=error\n", "")
