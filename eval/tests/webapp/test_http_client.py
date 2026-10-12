import json
from urllib.parse import parse_qs
import httpx
import pytest
from app.webapp.http_client import WebAppClient
from app.webapp.private_az import TransportRefused

STAMP = "a" * 40 + ".12.1.stage"
ORIGIN = "https://main.example.test"
SNAPSHOT = dict(tokenPresent=True, tokenParseable=True, tenantMatches=True, audienceMatches=True,
                ownerMatches=True, scopeMatches=True, roleMatches=True, usable=True, expiresAtUnixSeconds=1791766800)


class Unreadable(httpx.SyncByteStream):
    def __init__(self): self.reads = 0
    def __iter__(self):
        self.reads += 1
        raise AssertionError("Refusal body was read")
        yield b""


@pytest.mark.parametrize("status", [401, 302, 429, 503])
def test_health_refusal_body_is_never_read_or_followed(status):
    body = Unreadable()
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(status, headers={"location": "https://signin.example.test"}, stream=body)
    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True) as http:
        health = WebAppClient(http, ORIGIN).health("private-test-token")
    assert health.http_status == status
    assert health.configuration_valid is False
    assert len(requests) == 1
    assert body.reads == 0

@pytest.mark.parametrize('headers,expected',[({},False),({'X-ReleaseLens-Unhealthy':'1','X-ReleaseLens-Fixture-Version':STAMP},True),({'X-ReleaseLens-Unhealthy':'1','X-ReleaseLens-Fixture-Version':'b'*40+'.12.1.fixture'},False)])
def test_fixture_503_requires_exact_safe_marker_without_reading_body(headers,expected):
    body=Unreadable()
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(503,headers=headers,stream=body))) as http:
        result=WebAppClient(http,ORIGIN).fixture_health('private-test-token',STAMP)
    assert result.status==503 and result.version_matches is expected and body.reads==0


def test_health_requires_200_and_exact_safe_schema():
    payload = dict(status="ok", version=STAMP, slot="main", configurationValid=True, caller="deployment")
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as http:
        result = WebAppClient(http, ORIGIN).health("private-test-token")
    assert result.version == STAMP
    assert result.caller == "deployment"
    assert result.configuration_valid


@pytest.mark.parametrize("field,value", [("status", []), ("slot", {}), ("caller", []), ("configurationValid", 1), ("version", "private-host.example.test")])
def test_malformed_health_fields_fail_closed(field, value):
    payload = dict(status="ok", version=STAMP, slot="main", configurationValid=True, caller="deployment")
    payload[field] = value
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as http:
        assert not WebAppClient(http, ORIGIN).health(None).configuration_valid


def test_invalid_html_encoding_has_a_fixed_error():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"\xffprivate"))) as http:
        with pytest.raises(TransportRefused, match="invalid_response"):
            WebAppClient(http, ORIGIN).ask("question")


@pytest.mark.parametrize("payload", [{}, dict(SNAPSHOT, usable="true"), dict(SNAPSHOT, extra="private"), dict(SNAPSHOT, ownerMatches=False), dict(SNAPSHOT, expiresAtUnixSeconds=True)])
def test_malformed_diagnostics_fail_closed(payload):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as http:
        assert WebAppClient(http, ORIGIN).session().usable is False


@pytest.mark.parametrize("status", [200, 204, 401, 302])
def test_refresh_never_reads_body_and_updates_chunked_cookies(status):
    body = Unreadable()
    requests = []
    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(status, headers=[("set-cookie", "AppServiceAuthSession=chunks-2; Path=/; Secure"), ("set-cookie", "AppServiceAuthSessionC1=fresh-one; Path=/; Secure"), ("set-cookie", "AppServiceAuthSessionC2=fresh-two; Path=/; Secure")], stream=body)
        return httpx.Response(200, json=SNAPSHOT)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = WebAppClient(http, ORIGIN, "AppServiceAuthSession=chunks-2; AppServiceAuthSessionC1=old-one; AppServiceAuthSessionC2=old-two")
        assert client.refresh() == status
        assert client.session().usable
    assert body.reads == 0
    cookies = requests[-1].headers["cookie"]
    assert "fresh-one" in cookies and "fresh-two" in cookies and "old-one" not in cookies


def test_ask_uses_fresh_antiforgery_and_chunked_cookie_jar():
    requests = []
    def handle(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, headers={"set-cookie": "Antiforgery=fresh-cookie; Path=/; Secure"}, text='<form><input name="__RequestVerificationToken" value="fresh-form-token" type="hidden"></form>')
        form = parse_qs(request.content.decode())
        assert form == {"Question": ["question"], "__RequestVerificationToken": ["fresh-form-token"]}
        assert "Antiforgery=fresh-cookie" in request.headers["cookie"]
        assert "AppServiceAuthSessionC1=chunk-one" in request.headers["cookie"]
        assert "AppServiceAuthSessionC2=chunk-two" in request.headers["cookie"]
        return httpx.Response(200, text='<div id="answer-text">&lt;script&gt;encoded answer&lt;/script&gt;</div>')
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = WebAppClient(http, ORIGIN, "AppServiceAuthSession=chunks-2; AppServiceAuthSessionC1=chunk-one; AppServiceAuthSessionC2=chunk-two").ask("question")
    assert result.status == 200 and result.answer_nonempty
    assert [r.method for r in requests] == ["GET", "POST"]
    assert not hasattr(result, "answer")


@pytest.mark.parametrize("html", ['<p>non-answer text</p>', '<div id="answer-text"> </div>', '<div id="answer-text"></div><p>answer elsewhere</p>'])
def test_only_the_answer_element_counts(html):
    def handle(request):
        return httpx.Response(200, text='<input name="__RequestVerificationToken" value="csrf">' if request.method == "GET" else html)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        assert not WebAppClient(http, ORIGIN).ask("question").answer_nonempty


def test_slot_clients_never_share_cookies_even_when_http_client_is_shared():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, headers={"set-cookie": "platform=new-main; Domain=.example.test; Path=/; Secure"}, json=SNAPSHOT)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        main = WebAppClient(http, ORIGIN, "owner=main-private")
        staging = WebAppClient(http, "https://staging.example.test", "owner=staging-private")
        main.session()
        staging.session()
    assert "main-private" in requests[0].headers["cookie"]
    assert "main-private" not in requests[1].headers["cookie"]
    assert "new-main" not in requests[1].headers["cookie"]
    assert "staging-private" in requests[1].headers["cookie"]


@pytest.mark.parametrize("route", ["health", "session", "ask"])
def test_success_json_and_html_are_limited_to_1mib(route):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * (1024 * 1024 + 1)))) as http:
        client = WebAppClient(http, ORIGIN)
        with pytest.raises(TransportRefused, match="response_limit"):
            getattr(client, route)(*({"health": (None,), "ask": ("question",)}.get(route, ())))


def test_network_error_and_repr_do_not_echo_host_cookie_or_token(capsys):
    def handle(request): raise httpx.ConnectError("private-token https://main.example.test private-cookie", request=request)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = WebAppClient(http, ORIGIN, "owner=private-cookie")
        with pytest.raises(TransportRefused) as error: client.health("private-token")
        assert str(error.value) == "network"
        assert error.value.__cause__ is None
        assert "private" not in repr(client)
        assert "example.test" not in repr(client)
    assert capsys.readouterr().out == ""
