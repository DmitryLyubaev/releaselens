"""Workflow driver. Preflight has no Azure credentials; release is environment-gated."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
import httpx

from app.gateway import freeze
from app.gateway.report import write
from app.gateway.ci_check import _assertion
from .contracts import VERSION
from .http_client import WebAppClient
from .private_az import PrivateAz, TransportRefused
from .release import ReleaseContext, WORKFLOW, ci_passed, validate_source, validate_package, smoke, promote, fixture_smoke
from .release_transport import AzureReleaseTransport, ReadOnlyAzureReleaseTransport, ARM_SCOPE
from .diagnostics import ReleaseDiagnostics

ROOT = Path(__file__).resolve().parents[3]


class GitHubMetadata:
    def __init__(self, http, repository, token=None):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository): raise TransportRefused("release_refused")
        self.http, self.repository, self.token = http, repository, token
    def __repr__(self): return "GitHubMetadata(public release metadata)"
    def _get(self, path):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token: headers["Authorization"] = "Bearer " + self.token
        try:
            with self.http.stream("GET", f"https://api.github.com/repos/{self.repository}/" + path, headers=headers, timeout=10, follow_redirects=False) as response:
                if response.status_code != 200: raise ValueError()
                value = bytearray()
                for chunk in response.iter_bytes():
                    if len(value) + len(chunk) > 1024 * 1024: raise ValueError()
                    value.extend(chunk)
                data = json.loads(value)
                if not isinstance(data, dict): raise ValueError()
                return data
        except Exception: raise TransportRefused("release_refused") from None
    def _project(self, raw):
        keys = ("id", "run_attempt", "head_sha", "head_branch", "event", "status", "conclusion", "display_title", "created_at", "updated_at")
        result = {key: raw[key] for key in keys if key in raw}
        for name in ("repository", "head_repository"):
            result[name] = {"full_name": raw.get(name, {}).get("full_name")}
        path = raw.get("path", "")
        result["path"] = path.removesuffix("@refs/heads/main").removesuffix("@main")
        return result
    def _jobs(self, run, attempt):
        if type(run) is not int or type(attempt) is not int or min(run, attempt) <= 0: raise TransportRefused("release_refused")
        data = self._get(f"actions/runs/{run}/attempts/{attempt}/jobs?per_page=100")
        return [{key: job.get(key) for key in ("name", "status", "conclusion")} for job in data.get("jobs", [])]
    def source(self, run):
        if type(run) is not int or run <= 0: raise TransportRefused("release_refused")
        result = self._project(self._get(f"actions/runs/{run}"))
        result["jobs"] = self._jobs(result["id"], result["run_attempt"])
        return result
    def ci(self, sha):
        if not re.fullmatch(r"[a-f0-9]{40}", sha): raise TransportRefused("release_refused")
        query = urlencode({"branch": "main", "event": "push", "head_sha": sha, "per_page": 10})
        runs = self._get("actions/workflows/ci.yml/runs?" + query).get("workflow_runs", [])
        for raw in runs:
            if raw.get("status") != "completed" or raw.get("conclusion") != "success": continue
            candidate = self._project(raw)
            candidate["jobs"] = self._jobs(candidate["id"], candidate["run_attempt"])
            if ci_passed(candidate, self.repository, sha): return candidate
        raise TransportRefused("release_refused")


def _load(path):
    if path.stat().st_size > 1024 * 1024: raise TransportRefused("release_refused")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise TransportRefused("release_refused")
    return value


def _save(path, value): write(path, json.dumps(value, sort_keys=True, indent=2) + "\n")


def _canonical(value):
    if isinstance(value, dict): return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, list): return sorted((_canonical(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    return value


def main(argv=None, *, env=None, http=None, clean_guard=None, output_guard=None, root=ROOT, credential_factory=None):
    logging_before = (logging.root.manager.disable,logging.lastResort)
    logging.disable(logging.CRITICAL); logging.lastResort = None
    argv = sys.argv[1:] if argv is None else argv
    bundle = root / "artifacts/webapp-verified"
    records = root / "artifacts/webapp-release"
    made_http = http is None
    diagnostics = None
    hook = None
    request_hook = None
    try:
        # No environment/credential acquisition or output write before these guards.
        (clean_guard or freeze.require_clean_tree)()
        guard = output_guard or freeze.require_output_ignored
        guard(bundle); guard(records)
        values = os.environ if env is None else env
        if len(argv) != 1 or argv[0] not in {"preflight", "verify-bundle", "seed", "stage", "promote", "diagnose"}: raise ValueError()
        command = argv[0]
        mode = values.get("WEBAPP_MODE", command)
        fixture = values.get("WEBAPP_FIXTURE", "false") == "true"
        if mode not in {"seed", "stage", "promote", "diagnose"} or fixture and mode != "stage": raise ValueError()
        if command in {"seed", "stage", "promote", "diagnose"} and command != mode: raise ValueError()
        if values.get("GITHUB_ACTIONS") != "true" or values.get("GITHUB_REF") != "refs/heads/main" or values.get("GITHUB_EVENT_NAME") != "workflow_dispatch": raise ValueError()
        repository = values["GITHUB_REPOSITORY"]; sha = values["GITHUB_SHA"]
        run = int(values["GITHUB_RUN_ID"]); attempt = int(values["GITHUB_RUN_ATTEMPT"])
        if min(run, attempt) <= 0 or not re.fullmatch(r"[a-f0-9]{40}", sha): raise ValueError()
        http = http or httpx.Client(timeout=10, follow_redirects=False)
        diagnostics = ReleaseDiagnostics(sha, run, attempt, mode)
        hook = diagnostics.observe
        http.event_hooks['response'].append(hook)
        request_hook = diagnostics.observe_request
        http.event_hooks['request'].append(request_hook)
        if command == "preflight":
            api = GitHubMetadata(http, repository, values.get("GH_TOKEN"))
            manifest = {"mode": mode, "fixture": fixture, "current_sha": sha, "repository": repository, "execution_ci": api.ci(sha)}
            if mode == "promote":
                source = api.source(int(values["SOURCE_RUN_ID"]))
                # Only this positively identified failed fixture earns an eligibility rejection.
                # Network/input/CI failures never produce this record.
                if (source.get('id')==int(values['SOURCE_RUN_ID']) and source.get('run_attempt')==int(values['SOURCE_RUN_ATTEMPT'])
                    and source.get('path')==WORKFLOW and source.get('head_branch')=='main' and source.get('event')=='workflow_dispatch'
                    and source.get('repository',{}).get('full_name')==repository and source.get('head_repository',{}).get('full_name')==repository
                    and source.get('status')=='completed' and source.get('conclusion')=='failure'
                    and source.get('display_title')==f"webapp-stage-fixture-{source.get('head_sha')}-{source.get('run_attempt')}"
                    and any(job.get('name')=='release' and job.get('status')=='completed' and job.get('conclusion')=='failure' for job in source.get('jobs',[]))):
                    _save(records/'rejection.json',dict(schema=1,repository=repository,source_sha=sha,run_id=run,run_attempt=attempt,mode='promote',measured_at=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),source_run_id=source['id'],source_run_attempt=source['run_attempt'],source_sha_selected=source['head_sha'],reason='fixture_ineligible'))
                    raise ValueError()
                if source["run_attempt"] != int(values["SOURCE_RUN_ATTEMPT"]) or source["path"] != WORKFLOW or source["head_branch"] != "main" or source["event"] != "workflow_dispatch" or source["conclusion"] != "success" or source["status"] != "completed" or source["display_title"] != f"webapp-stage-candidate-{source['head_sha']}-{source['run_attempt']}": raise ValueError()
                manifest.update(source=source, ci=api.ci(source["head_sha"]))
            else:
                manifest.update(version=f"{sha}.{run}.{attempt}.{'fixture' if fixture else mode}", source_sha=sha, run_id=run, run_attempt=attempt, ci=manifest["execution_ci"])
            _save(bundle / "verified.json", manifest)
        elif command == "verify-bundle":
            diagnostics.phase = 'bundle_validation'
            manifest = _load(bundle / "verified.json")
            if manifest["current_sha"] != sha or manifest["repository"] != repository or manifest["mode"] != mode or manifest["fixture"] is not fixture or not ci_passed(manifest["execution_ci"], repository, sha): raise ValueError()
            if mode == "promote":
                old = root / "artifacts/webapp-source"
                receipt = _load(old / "receipt.json"); package = (old / "package.zip").read_bytes()
                if not validate_source(manifest["source"], receipt, repository, manifest["ci"]): raise ValueError()
                validate_package(package, receipt["package_sha256"])
                _save(bundle / "receipt.json", receipt)
                (bundle / "package.zip").write_bytes(package)
                manifest["package_sha256"] = receipt["package_sha256"]
            else:
                import hashlib
                package = (bundle / "package.zip").read_bytes()
                manifest["package_sha256"] = hashlib.sha256(package).hexdigest()
                validate_package(package, manifest["package_sha256"])
            _save(bundle / "verified.json", manifest)
        else:
            diagnostics.phase = 'bundle_validation'
            manifest = _load(bundle / "verified.json")
            if manifest["current_sha"] != sha or manifest["repository"] != repository or manifest["mode"] != mode or manifest["fixture"] is not fixture or not ci_passed(manifest["execution_ci"], repository, sha): raise ValueError()
            package = (bundle / "package.zip").read_bytes()
            validate_package(package, manifest["package_sha256"])
            if mode == "promote":
                receipt = _load(bundle / "receipt.json")
                if not validate_source(manifest["source"], receipt, repository, manifest["ci"]): raise ValueError()
                validate_package(package, receipt["package_sha256"])
            else:
                if manifest["version"] != f"{sha}.{run}.{attempt}.{'fixture' if fixture else mode}": raise ValueError()
                if mode == "stage":
                    baseline = values.get("WEBAPP_BASELINE_VERSION", "")
                    if not VERSION.fullmatch(baseline) or baseline.endswith(".fixture"): raise ValueError()
            if credential_factory is None:
                from azure.identity import ClientAssertionCredential
                credential_factory = ClientAssertionCredential
            diagnostics.phase = 'credential_setup'
            credential = credential_factory(values["AZURE_TENANT_ID"], values["AZURE_CLIENT_ID"], lambda: _assertion(values, http))
            def token(scope):
                previous = diagnostics.phase
                diagnostics.phase = 'arm_token' if scope == ARM_SCOPE else 'frontend_token'
                value = credential.get_token(scope).token
                diagnostics.phase = previous
                return value
            audience = values["WEBAPP_FRONTEND_AUDIENCE"]
            frontend = lambda: token(audience.rstrip("/") + "/.default")
            transport = ReadOnlyAzureReleaseTransport if mode == 'diagnose' else AzureReleaseTransport
            cloud = transport(http, token, values["AZURE_TENANT_ID"], values["AZURE_SUBSCRIPTION_ID"], values["AZURE_CLIENT_ID"], values["WEBAPP_DEPLOY_OBJECT_ID"], values["WEBAPP_RESOURCE_ID"], values["WEBAPP_MAIN_URL"], values["WEBAPP_STAGING_URL"])
            from urllib.parse import urlsplit
            for slot, key in (('main', 'WEBAPP_MAIN_URL'), ('staging', 'WEBAPP_STAGING_URL')):
                hostname = urlsplit(values[key]).hostname
                diagnostics.hosts[hostname] = slot
                if hostname and hostname.endswith('.azurewebsites.net'):
                    diagnostics.hosts[hostname.removesuffix('.azurewebsites.net') + '.scm.azurewebsites.net'] = slot
            intended = _canonical(json.loads(values["WEBAPP_INTENDED_CONFIG"]))
            config = lambda: _canonical(cloud.configuration(intended))
            diagnostics.phase = 'configuration_before'
            if config() != intended: raise ValueError()
            clients = {slot: WebAppClient(http, values["WEBAPP_MAIN_URL"] if slot == "main" else values["WEBAPP_STAGING_URL"]) for slot in ("main", "staging")}
            from .evidence import emit
            persist = lambda observation: _save(records / f"observation-{observation.check}.json", emit(observation,repository=repository,sha=sha,run_id=run,attempt=attempt,mode=mode,fixture=fixture,measured_at=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),source_run_id=receipt['run_id'] if mode=='promote' else None,source_run_attempt=receipt['run_attempt'] if mode=='promote' else None))
            now = lambda: datetime.now(timezone.utc)
            if mode == 'diagnose':
                bearer = frontend()
                diagnostics.phase = 'diagnostic_health'
                statuses = {}
                for slot, client in clients.items():
                    health = diagnostics.collect_probe('health', slot, lambda: client.health(bearer))
                    statuses[slot] = health.http_status if health is not None else None
                anonymous = {}
                for slot, key in (('main', 'WEBAPP_MAIN_URL'), ('staging', 'WEBAPP_STAGING_URL')):
                    # A fresh client prevents any health-response session cookie being reused.
                    client = WebAppClient(http, values[key])
                    anonymous[slot] = diagnostics.collect_probe('anonymous_health', slot, client.anonymous_health_status)
                diagnostics.phase = 'diagnostic_scm'
                scm = {slot: diagnostics.collect_probe('deployment_status', slot, lambda: cloud.probe_scm(slot)) is True for slot in clients}
                diagnostics.phase = 'complete'
                _save(records / 'diagnostics.json', diagnostics.result('collected') | dict(read_only=True, configuration_matches=True, health_statuses=statuses, anonymous_health_statuses=anonymous, scm_readable=scm))
            elif mode == "promote":
                diagnostics.phase = 'promotion'
                receipt = _load(bundle / "receipt.json")
                api = GitHubMetadata(http, repository)  # Public metadata, no actions permission in the protected job.
                ctx = ReleaseContext(PrivateAz(values["AZURE_TENANT_ID"], values["AZURE_SUBSCRIPTION_ID"], values["AZURE_CLIENT_ID"], run=cloud.run), clients["main"], clients["staging"], frontend, values["WEBAPP_RESOURCE_ID"], repository, manifest["ci"], intended, config, cloud.selected_package_sha256, lambda: (api.source(receipt["run_id"]), api.ci(receipt["source_sha"])), persist, now, time.sleep)
                promote(ctx, receipt, manifest["source"])
            else:
                version = manifest["version"]
                if version != f"{sha}.{run}.{attempt}.{'fixture' if fixture else mode}": raise ValueError()
                baseline = values.get("WEBAPP_BASELINE_VERSION", "")
                if mode == "stage":
                    if not VERSION.fullmatch(baseline) or baseline.endswith(".fixture"): raise ValueError()
                    diagnostics.phase = 'main_baseline'
                    before = smoke(clients["main"], frontend(), baseline, "main", now, time.sleep)
                    persist(before)
                    if not before.matches["baseline_healthy"]: raise ValueError()
                slot = "main" if mode == "seed" else "staging"
                diagnostics.phase = 'scm_deploy'
                diagnostics.deployments = 1
                cloud.deploy(slot, package)
                if fixture:
                    fixture_health = fixture_smoke(clients['staging'],frontend(),version,manifest['package_sha256'],lambda:cloud.selected_package_sha256('staging'),now,time.sleep)
                    after = clients["main"].health(frontend())
                    from .contracts import Observation
                    persist(Observation("W5", {**fixture_health.statuses, "main_after": after.http_status}, {**fixture_health.matches, "stage_failed": True, "no_swap": True, "main_unchanged": after.http_status == 200 and after.status=='ok' and after.configuration_valid and after.version == baseline and after.slot == "main" and after.caller == "deployment"}, fixture_health.counts,fixture_health.times))
                    raise ValueError()
                diagnostics.phase = 'readiness'
                observed = smoke(clients[slot], frontend(), version, slot, now, time.sleep)
                persist(observed)
                if not all(observed.matches.values()): raise ValueError()
                diagnostics.phase = 'package_digest'
                if cloud.selected_package_sha256(slot) != manifest["package_sha256"]: raise ValueError()
                diagnostics.phase = 'configuration_after'
                if config() != intended: raise ValueError()
                from .observations import deployment_access
                diagnostics.phase = 'access_probes'
                access = deployment_access(clients['main'],frontend())
                persist(access)
                if not access.matches['deployment_health_only']: raise ValueError()
                receipt = dict(schema=1, repository=repository, workflow_path=WORKFLOW, source_sha=sha, run_id=run, run_attempt=attempt, version=version, package_sha256=manifest["package_sha256"], kind="candidate" if mode == "stage" else "seed", created_at=now().isoformat().replace("+00:00", "Z"), baseline_version=baseline if mode == "stage" else version, checks_passed=True)
                diagnostics.phase = 'receipt'
                _save(records / "receipt.json", receipt)
                (records / "package.zip").write_bytes(package)
        print("status=ok")
        return 0
    except Exception as error:
        if diagnostics is not None:
            try: _save(records / 'diagnostics.json', diagnostics.result('refused', error) | dict(read_only=mode == 'diagnose'))
            except Exception: pass
        print("status=refused")
        return 1
    finally:
        if hook is not None and http is not None and hook in http.event_hooks['response']:
            http.event_hooks['response'].remove(hook)
        if request_hook is not None and http is not None and request_hook in http.event_hooks['request']:
            http.event_hooks['request'].remove(request_hook)
        if made_http and http is not None: http.close()
        logging.lastResort=logging_before[1]; logging.disable(logging_before[0])
