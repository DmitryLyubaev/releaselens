"""CI's keyless ARM/SCM adapter. No Azure login or credential file is created."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import time
from urllib.parse import urlsplit, urljoin
import httpx

from .private_az import TransportRefused

ARM_SCOPE = "https://management.azure.com/.default"
API = "2024-11-01"
_ARM_ID_CASE = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def owned_deployment_status_url(origin, hint):
    """Untrusted hints never select a different origin or a non-status endpoint."""
    fallback = origin + '/api/deployments/latest'
    if not hint: return fallback, 'missing'
    try:
        candidate = urljoin(origin, hint)
        parsed = urlsplit(candidate)
        if (parsed.scheme == 'https' and parsed.netloc == urlsplit(origin).netloc
                and not parsed.username and not parsed.password and not parsed.fragment
                and re.fullmatch(r'/api/deployments/[A-Za-z0-9_-]+', parsed.path)):
            return candidate, None
    except Exception:
        return fallback, 'malformed'
    return fallback, 'untrusted'


class AzureReleaseTransport:
    def __init__(self, http, token, tenant, subscription, client_id, principal_id, resource_id, main_origin, staging_origin, *, clock=time.monotonic, sleep=time.sleep):
        self._http, self._token = http, token
        self._tenant, self._subscription, self._client, self._principal = tenant, subscription, client_id, principal_id
        self._resource = resource_id
        self._origins = {"main": main_origin, "staging": staging_origin}
        self._clock, self._sleep = clock, sleep
        if not resource_id.startswith(f"/subscriptions/{subscription}/resourceGroups/") or "/providers/Microsoft.Web/sites/" not in resource_id:
            raise TransportRefused("release_refused")

    def __repr__(self): return "AzureReleaseTransport(credentials in memory)"

    def _bearer(self):
        try:
            value = self._token(ARM_SCOPE)
            if len(value) > 32768: raise ValueError()
            part = value.split('.')[1]
            claims = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
            if claims.get("tid") != self._tenant or claims.get("oid") != self._principal or claims.get("appid", claims.get("azp")) != self._client: raise ValueError()
            return value
        except Exception: raise TransportRefused("account_refused") from None

    def _scm(self, slot):
        parts = urlsplit(self._origins[slot])
        if parts.scheme != "https" or not parts.hostname or not parts.hostname.endswith(".azurewebsites.net") or parts.username or parts.query or parts.fragment or parts.port: raise TransportRefused("release_refused")
        return "https://" + parts.hostname.removesuffix(".azurewebsites.net") + ".scm.azurewebsites.net"

    def _read(self, method, url, *, body=None, binary=False, limit=1024 * 1024, allow_async=False):
        # Only owned, non-mutating ARM configuration reads may be replayed.
        try: parsed = urlsplit(url)
        except Exception: raise TransportRefused('release_refused') from None
        lists = {self._resource + '/config/appsettings/list', self._resource + '/slots/staging/config/appsettings/list'}
        read = (body is None and parsed.scheme == 'https' and parsed.netloc == 'management.azure.com'
                and (method == 'GET' and (parsed.path == self._resource or parsed.path.startswith(self._resource + '/'))
                     or method == 'POST' and parsed.path in lists))
        for attempt in range(2 if read else 1):
            try:
                return self._read_once(method, url, body=body, binary=binary, limit=limit, allow_async=allow_async,
                                       timeout=30 if read else 120 if isinstance(body, bytes) else 10)
            except TransportRefused as error:
                if not read or attempt or str(error) != 'release_refused' or not isinstance(error.__context__, httpx.TimeoutException):
                    raise
                self._sleep(1)

    def _read_once(self, method, url, *, body, binary, limit, allow_async, timeout):
        try:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment: raise ValueError()
            own_scm = {urlsplit(self._scm(slot)).hostname for slot in ("main", "staging")}
            if parsed.hostname != "management.azure.com" and parsed.hostname not in own_scm: raise ValueError()
            if parsed.hostname == "management.azure.com" and not parsed.path.startswith(f"/subscriptions/{self._subscription}/"): raise ValueError()
            kwargs = {"content": body} if isinstance(body, bytes) else {"json": body} if body is not None else {}
            with self._http.stream(method, url, headers={"Authorization": "Bearer " + self._bearer()}, timeout=timeout, follow_redirects=False, **kwargs) as response:
                headers = dict(response.headers)
                if response.status_code == 202 and allow_async: return None, headers
                if response.status_code not in {200, 201, 204}: raise TransportRefused("release_refused")
                if response.status_code == 204: return (b"" if binary else {}), headers
                value = bytearray()
                for chunk in response.iter_bytes():
                    if len(value) + len(chunk) > limit: raise TransportRefused("response_limit")
                    value.extend(chunk)
                if binary: return bytes(value), headers
                result = json.loads(value) if value else {}
                if not isinstance(result, dict): raise ValueError()
                return result, headers
        except TransportRefused: raise
        except Exception: raise TransportRefused("release_refused") from None

    def _arm(self, method, url, body=None):
        if not url.startswith("https://management.azure.com" + self._resource): raise TransportRefused("release_refused")
        result, headers = self._read(method, url, body=body, allow_async=True)
        if result is not None: return result
        location = headers.get("azure-asyncoperation") or headers.get("location")
        if not location: raise TransportRefused("release_refused")
        started = self._clock()
        for _ in range(30):
            if self._clock() - started >= 300: break
            state, _ = self._read("GET", location)
            if state.get("status") == "Succeeded": return {}
            if state.get("status") not in {"InProgress", "Running", "Accepted"}: raise TransportRefused("release_refused")
            self._sleep(10)
        raise TransportRefused("release_refused")

    def run(self, args, **kwargs):
        try:
            if args[1:3] == ["account", "show"]:
                self._bearer()
                result = {"tenantId": self._tenant, "id": self._subscription, "user": {"name": self._client, "type": "servicePrincipal"}}
            elif args[1] == "rest":
                def option(name, default=None): return args[args.index(name) + 1] if name in args else default
                result = self._arm(option("--method", "GET"), option("--url"), json.loads(option("--body")) if "--body" in args else None)
            else: raise ValueError()
            return subprocess.CompletedProcess(args, 0, json.dumps(result), "")
        except Exception: return subprocess.CompletedProcess(args, 1, "", "")

    def selected_package_sha256(self, slot):
        origin = self._scm(slot)
        selected, _ = self._read("GET", origin + "/api/vfs/data/SitePackages/packagename.txt", binary=True, limit=4096)
        try: name = selected.decode("utf-8").strip()
        except Exception: raise TransportRefused("release_refused") from None
        if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*\.zip", name) or ".." in name: raise TransportRefused("release_refused")
        value, _ = self._read("GET", origin + "/api/vfs/data/SitePackages/" + name, binary=True, limit=200 * 1024 * 1024)
        return hashlib.sha256(value).hexdigest()

    def deploy(self, slot, package):
        origin = self._scm(slot)
        result, headers = self._read("POST", origin + "/api/zipdeploy?isAsync=true", body=package, allow_async=True)
        if result is not None: return
        location, _ = owned_deployment_status_url(origin, headers.get('location', ''))
        started = self._clock()
        for _ in range(30):
            if self._clock() - started >= 300: break
            # Kudu can report pending deployment with HTTP 202 and an unread body.
            state, _ = self._read("GET", location, allow_async=True)
            if state is not None and state.get("status") == 4: return
            if state is not None and state.get("status") not in {0, 1, 2}: raise TransportRefused("release_refused")
            self._sleep(10)
        raise TransportRefused("release_refused")

    def configuration(self, intended):
        def pick(actual, expected):
            if isinstance(expected, dict):
                if not isinstance(actual, dict): raise TransportRefused("release_refused")
                return {key: pick(actual.get(key), value) for key, value in expected.items()}
            if expected == [] and actual is None: return []
            if type(actual) is not type(expected): raise TransportRefused("release_refused")
            return actual
        result = {}
        for slot in ("main", "staging"):
            base = "https://management.azure.com" + self._resource + ("/slots/staging" if slot == "staging" else "")
            get = lambda path: self._arm("GET", base + path + "?api-version=" + API)
            site = get(""); identity = site.get("identity", {})
            config = get("/config/web").get("properties", {})
            settings = self._arm("POST", base + "/config/appsettings/list?api-version=" + API).get("properties", {})
            auth = get("/config/authsettingsV2").get("properties", {})
            providers = auth.get("identityProviders", {})
            principals = providers.get("azureActiveDirectory", {}).get("validation", {}).get("defaultAuthorizationPolicy", {}).get("allowedPrincipals", {})
            if principals.get("groups") is not None: raise TransportRefused("release_refused")
            if any(isinstance(value, dict) and value.get("enabled") is True for key, value in providers.items() if key != "azureActiveDirectory"): raise TransportRefused("release_refused")
            custom = providers.get("customOpenIdConnectProviders")
            if custom not in (None, {}): raise TransportRefused("release_refused")
            # A missing/null map also means no custom providers on the platform.
            if custom is None: providers["customOpenIdConnectProviders"] = {}
            registration = providers.get("azureActiveDirectory", {}).get("registration", {})
            if any(registration.get(key) for key in ("clientSecretCertificateThumbprint", "clientSecretCertificateIssuer", "clientSecretCertificateSubjectAlternativeName")): raise TransportRefused("release_refused")
            policies = {kind: get("/basicPublishingCredentialsPolicies/" + kind).get("properties", {}).get("allow") for kind in ("scm", "ftp")}
            # ARM can change ASCII casing in these project-generated resource IDs.
            # Keep all other characters exact, including Unicode substitutions.
            declared_ids = {value.translate(_ARM_ID_CASE): value for value in intended[slot]["identity"]["identity_ids"]}
            actual_ids = [declared_ids.get(value.translate(_ARM_ID_CASE), value) for value in identity.get("userAssignedIdentities", {})]
            result[slot] = {"identity": {"type": identity.get("type"), "identity_ids": sorted(actual_ids)},
                            "siteconfig": pick(config, intended[slot]["siteconfig"]), "appsettings": settings,
                            "auth": pick(auth, intended[slot]["auth"]), "publishing": policies}
        sticky, _ = self._read("GET", "https://management.azure.com" + self._resource + "/config/slotConfigNames?api-version=" + API)
        result["sticky"] = pick(sticky.get("properties", {}), intended["sticky"])
        return result


class ReadOnlyAzureReleaseTransport(AzureReleaseTransport):
    """The diagnostic action cannot reach ARM writes, SCM uploads or swaps."""
    def _read(self, method, url, **kwargs):
        parsed = urlsplit(url)
        lists = {self._resource + '/config/appsettings/list', self._resource + '/slots/staging/config/appsettings/list'}
        if method != 'GET' and not (method == 'POST' and parsed.hostname == 'management.azure.com' and parsed.path in lists and kwargs.get('body') is None):
            raise TransportRefused('release_refused')
        return super()._read(method, url, **kwargs)

    def probe_scm(self, slot):
        try:
            self._read('GET', self._scm(slot) + '/api/deployments/latest')
            return True
        except TransportRefused:
            return False
