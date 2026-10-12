"""Bounded in-memory HTTP. Refusal and refresh bodies are never read."""
from __future__ import annotations

import json
from contextlib import contextmanager
from html.parser import HTMLParser
from http.cookies import SimpleCookie
from urllib.parse import urlsplit, urljoin
import httpx

from .contracts import AskObservation, Health, SessionSnapshot, VERSION, Probe, FixtureHealth
from .private_az import TransportRefused

BODY_LIMIT = 1024 * 1024
SESSION_KEYS = ("tokenPresent", "tokenParseable", "tenantMatches", "audienceMatches", "ownerMatches", "scopeMatches", "roleMatches", "usable", "expiresAtUnixSeconds")


def _unique(pairs):
    result = {}
    for name, value in pairs:
        if name in result: raise ValueError()
        result[name] = value
    return result


def _body(response: httpx.Response) -> bytes:
    value = bytearray()
    for chunk in response.iter_bytes():
        if len(value) + len(chunk) > BODY_LIMIT: raise TransportRefused("response_limit")
        value.extend(chunk)
    return bytes(value)


def _json(response: httpx.Response):
    try: return json.loads(_body(response), object_pairs_hook=_unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeDecodeError): return None


class _Form(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens = []
    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == "input" and data.get("name") == "__RequestVerificationToken" and data.get("value"):
            self.tokens.append(data["value"])


class _Answer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.nonempty = False
        self.elements = 0
    def handle_starttag(self, tag, attrs):
        if self.depth: self.depth += 1
        elif dict(attrs).get("id") == "answer-text":
            self.depth = 1
            self.elements += 1
    def handle_endtag(self, tag):
        if self.depth: self.depth -= 1
    def handle_data(self, data):
        if self.depth and data.strip(): self.nonempty = True


class WebAppClient:
    def __init__(self, http: httpx.Client, origin: str, cookie: str | None = None):
        parts = urlsplit(origin)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in {"", "/"}:
            raise TransportRefused("invalid_origin")
        self._http = http
        self._origin = origin.rstrip("/")
        self._cookies = httpx.Cookies()
        if cookie:
            try:
                parsed = SimpleCookie()
                parsed.load(cookie)
                if not parsed: raise ValueError()
                for name, item in parsed.items(): self._cookies.set(name, item.value, domain=parts.hostname, path="/")
            except (ValueError, TypeError): raise TransportRefused("invalid_cookie") from None

    def __repr__(self): return "WebAppClient(session in memory)"

    @contextmanager
    def _request(self, method: str, path: str, bearer: str | None = None, *, data=None, timeout=10.0, headers=None):
        try:
            request = self._http.build_request(method, self._origin + path, data=data, timeout=timeout)
            # Never merge a shared httpx client's cookies or credentials into a slot session.
            request.headers.pop("Cookie", None)
            request.headers.pop("Authorization", None)
            self._cookies.set_cookie_header(request)
            if bearer is not None: request.headers["Authorization"] = "Bearer " + bearer
            if headers: request.headers.update(headers)
            response = self._http.send(request, stream=True, follow_redirects=False, auth=None)
            try:
                self._cookies.extract_cookies(response)
                yield response
            finally: response.close()
        except (httpx.HTTPError, OSError): raise TransportRefused("network") from None
        except UnicodeDecodeError: raise TransportRefused("invalid_response") from None

    def health(self, bearer: str | None) -> Health:
        with self._request("GET", "/health", bearer) as response:
            status = response.status_code
            if status != 200: return Health(status)
            data = _json(response)
            if not isinstance(data, dict) or set(data) != {"status", "version", "slot", "configurationValid", "caller"}: return Health(status)
            if any(not isinstance(data[key], str) for key in ("status", "version", "slot", "caller")): return Health(status)
            if data["status"] not in {"ok", "unavailable"} or not VERSION.fullmatch(data["version"]) or data["slot"] not in {"main", "staging"} or type(data["configurationValid"]) is not bool or data["caller"] not in {"owner", "deployment"}: return Health(status)
            return Health(status, data["status"], data["version"], data["slot"], data["configurationValid"], data["caller"])

    def session(self) -> SessionSnapshot:
        with self._request("GET", "/session") as response:
            status = response.status_code
            if status != 200: return SessionSnapshot(http_status=status)
            data = _json(response)
            if not isinstance(data, dict) or set(data) != set(SESSION_KEYS) or any(type(data[key]) is not bool for key in SESSION_KEYS[:-1]): return SessionSnapshot(http_status=status)
            expiry = data["expiresAtUnixSeconds"]
            if expiry is not None and (type(expiry) is not int or not 0 <= expiry <= 253402300799): return SessionSnapshot(http_status=status)
            if data["usable"] and (not all(data[key] for key in SESSION_KEYS[:-1]) or expiry is None): return SessionSnapshot(http_status=status)
            return SessionSnapshot(*(data[key] for key in SESSION_KEYS), http_status=status)

    def ask(self, question: str) -> AskObservation:
        with self._request("GET", "/") as response:
            if response.status_code != 200: return AskObservation(response.status_code, False)
            form = _Form()
            form.feed(_body(response).decode("utf-8", errors="strict"))
            if len(form.tokens) != 1 or len(form.tokens[0]) > 4096: raise TransportRefused("invalid_form")
        with self._request("POST", "/ask", data={"Question": question, "__RequestVerificationToken": form.tokens[0]}, timeout=70.0) as response:
            if response.status_code != 200: return AskObservation(response.status_code, False)
            answer = _Answer()
            answer.feed(_body(response).decode("utf-8", errors="strict"))
            return AskObservation(response.status_code, answer.elements == 1 and answer.nonempty)

    def refresh(self) -> int:
        with self._request("GET", "/.auth/refresh") as response: return response.status_code

    def fixture_health(self, bearer: str, version: str) -> FixtureHealth:
        with self._request('GET','/health',bearer) as response:
            matches=response.status_code==503 and response.headers.get_list('X-ReleaseLens-Unhealthy')==['1'] and response.headers.get_list('X-ReleaseLens-Fixture-Version')==[version]
            return FixtureHealth(response.status_code,matches)

    def probe(self, path: str, bearer: str | None, *, forged=None) -> Probe:
        if path not in {'/', '/health', '/ask', '/session'}: raise TransportRefused('invalid_response')
        with self._request('POST' if path=='/ask' else 'GET',path,bearer,headers=forged) as response:
            location=urlsplit(urljoin(self._origin+'/',response.headers.get('Location','')))
            own=urlsplit(self._origin)
            valid=response.status_code==302 and location.scheme==own.scheme and location.netloc==own.netloc and location.path=='/.auth/login/aad' and not location.username and not location.password and not location.fragment
            return Probe(response.status_code,valid)
