"""Closed diagnostic facts. Never serialize exception text, URLs or credentials."""
import re
from urllib.parse import urlsplit
import httpx
from .private_az import TransportRefused
from .release_transport import owned_deployment_status_url

PHASES = {'source_ci', 'bundle_validation', 'credential_setup', 'arm_token', 'frontend_token', 'configuration_before', 'main_baseline', 'scm_deploy', 'package_presence', 'readiness', 'package_digest', 'configuration_after', 'access_probes', 'receipt', 'promotion', 'diagnostic_health', 'diagnostic_scm', 'complete'}


def _error_facts(error):
    category, aad, refusal, timeout = None, None, None, None
    if error is not None:
        category = 'other'
        current = error
        for _ in range(4):
            if isinstance(current, httpx.TimeoutException):
                category = 'timeout'
                timeout = next((label for kind, label in (
                    (httpx.ConnectTimeout, 'connect'), (httpx.ReadTimeout, 'read'),
                    (httpx.WriteTimeout, 'write'), (httpx.PoolTimeout, 'pool'))
                    if isinstance(current, kind)), 'other')
            elif isinstance(current, (httpx.NetworkError, OSError)) and category != 'timeout': category = 'network'
            elif isinstance(current, httpx.ProtocolError) and category != 'timeout': category = 'protocol'
            elif type(current).__name__ == 'ClientAuthenticationError' and category in {'other', 'transport_refused'}: category = 'client_authentication'
            elif isinstance(current, TransportRefused) and category == 'other': category = 'transport_refused'
            elif isinstance(current, ValueError) and category in {'other', 'transport_refused'}: category = 'invalid_response'
            try:
                if isinstance(current, TransportRefused):
                    code = str(current)
                    if code in {'account_refused', 'cli_refused', 'invalid_response', 'network', 'response_limit', 'invalid_origin', 'invalid_cookie', 'invalid_form', 'release_refused'}:
                        refusal = code
                match = re.search(r'\bAADSTS([0-9]{5,8})\b', str(current)[:32768])
                if match and aad is None: aad = int(match[1])
            except Exception:
                pass
            current = getattr(current, '__context__', None)
            if current is None: break
    return dict(error_category=category, timeout_kind=timeout, refusal_code=refusal, aad_error_code=aad)


class ReleaseDiagnostics:
    def __init__(self, sha, run, attempt, mode):
        self.binding = dict(schema=1, source_sha=sha, run_id=run, run_attempt=attempt, mode=mode)
        self.phase = 'source_ci'
        self.responses = []
        self.hosts = {}
        self.deployments = 0
        self.swaps = 0
        self.scm_polling = None
        self.probes = []
        self._active_probe = None

    def collect_probe(self, operation, slot, read):
        if (operation not in {'health', 'anonymous_health', 'deployment_status'}
                or slot not in {'main', 'staging'} or self._active_probe is not None
                or len(self.probes) >= 6):
            raise ValueError()
        record = dict(operation=operation, slot=slot, status=None) | _error_facts(None)
        self.probes.append(record)
        self._active_probe = record
        try:
            result = read()
            status = result if type(result) is int else getattr(result, 'http_status', getattr(result, 'status', None))
            if record['status'] is None and type(status) is int and 100 <= status <= 599:
                record['status'] = status
            return result
        except Exception as error:
            record.update(_error_facts(error))
            if not isinstance(error, TransportRefused): raise
            return None
        finally:
            self._active_probe = None

    def observe_request(self, request):
        parsed = urlsplit(str(request.url))
        if request.method == 'POST' and parsed.hostname == 'management.azure.com' and parsed.path.endswith('/slotsswap'):
            self.swaps += 1

    def observe(self, response):
        try:
            parsed = urlsplit(str(response.request.url))
            host, path = parsed.hostname, parsed.path
            slot = self.hosts.get(host)
            if host == 'management.azure.com':
                operation = next((label for suffix, label in (('/config/appsettings/list', 'appsettings'), ('/config/authsettingsV2', 'auth'), ('/config/web', 'siteconfig'), ('/config/slotConfigNames', 'sticky'), ('/slotsswap', 'swap')) if path.endswith(suffix)), 'arm_read')
            elif slot and '.scm.azurewebsites.net' in host:
                operation = 'zipdeploy' if path.startswith('/api/zipdeploy') else 'deployment_status' if path == '/api/deployments' or path.startswith('/api/deployments/') else 'selected_manifest' if path.endswith('/packagename.txt') else 'selected_package'
            elif slot:
                operation = 'health' if path == '/health' else 'access_probe'
            else:
                return
            status = response.status_code
            if type(status) is not int or not 100 <= status <= 599:
                return
            active = self._active_probe
            if active is not None and active['slot'] == slot and (
                    active['operation'] == operation
                    or active['operation'] == 'anonymous_health' and operation == 'health'):
                active['status'] = status
            if operation == 'zipdeploy' and status == 202:
                _, reason = owned_deployment_status_url(parsed.scheme + '://' + parsed.netloc, response.headers.get('location', ''))
                self.scm_polling = dict(source='owned_latest' if reason else 'location', fallback_reason=reason)
            self.responses.append(dict(operation=operation, slot=slot or 'arm', status=status))
            self.responses = self.responses[-40:]
        except Exception:
            return

    def result(self, outcome, error=None):
        if self.phase not in PHASES or outcome not in {'collected', 'refused'}: raise ValueError()
        return self.binding | _error_facts(error) | dict(outcome=outcome, phase=self.phase, responses=self.responses, probes=self.probes, deployment_calls=self.deployments, swap_calls=self.swaps, model_calls=0, scm_polling=self.scm_polling)
