"""Closed diagnostic facts. Never serialize exception text, URLs or credentials."""
import re
from urllib.parse import urlsplit
import httpx
from .private_az import TransportRefused

PHASES = {'source_ci', 'bundle_validation', 'credential_setup', 'arm_token', 'frontend_token', 'configuration_before', 'main_baseline', 'scm_deploy', 'readiness', 'package_digest', 'configuration_after', 'access_probes', 'receipt', 'promotion', 'diagnostic_health', 'diagnostic_scm', 'complete'}


class ReleaseDiagnostics:
    def __init__(self, sha, run, attempt, mode):
        self.binding = dict(schema=1, source_sha=sha, run_id=run, run_attempt=attempt, mode=mode)
        self.phase = 'source_ci'
        self.responses = []
        self.hosts = {}
        self.deployments = 0
        self.swaps = 0

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
                operation = 'zipdeploy' if path.startswith('/api/zipdeploy') else 'deployment_status' if path.startswith('/api/deployments/') else 'selected_manifest' if path.endswith('/packagename.txt') else 'selected_package'
            elif slot:
                operation = 'health' if path == '/health' else 'access_probe'
            else:
                return
            status = response.status_code
            if type(status) is not int or not 100 <= status <= 599:
                return
            self.responses.append(dict(operation=operation, slot=slot or 'arm', status=status))
            self.responses = self.responses[-40:]
        except Exception:
            return

    def result(self, outcome, error=None):
        category, aad, refusal = None, None, None
        if error is not None:
            category = 'other'
            current = error
            for _ in range(4):
                if isinstance(current, httpx.TimeoutException): category = 'timeout'
                elif type(current).__name__ == 'ClientAuthenticationError': category = 'client_authentication'
                elif type(current).__name__ == 'TransportRefused' and category == 'other': category = 'transport_refused'
                elif isinstance(current, ValueError) and category == 'other': category = 'invalid_response'
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
        if self.phase not in PHASES or outcome not in {'collected', 'refused'}: raise ValueError()
        return self.binding | dict(outcome=outcome, phase=self.phase, error_category=category, refusal_code=refusal, aad_error_code=aad, responses=self.responses, deployment_calls=self.deployments, swap_calls=self.swaps, model_calls=0)
