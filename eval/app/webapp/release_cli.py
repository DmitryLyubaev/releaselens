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
from .release import ReleaseContext, WORKFLOW, ci_passed, validate_source, validate_package, smoke, promote
from .release_transport import AzureReleaseTransport

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
    try:
        # No environment/credential acquisition or output write before these guards.
        (clean_guard or freeze.require_clean_tree)()
        guard = output_guard or freeze.require_output_ignored
        guard(bundle); guard(records)
        values = os.environ if env is None else env
        if len(argv) != 1 or argv[0] not in {"preflight", "verify-bundle", "seed", "stage", "promote"}: raise ValueError()
        command = argv[0]
        mode = values.get("WEBAPP_MODE", command)
        fixture = values.get("WEBAPP_FIXTURE", "false") == "true"
        if mode not in {"seed", "stage", "promote"} or fixture and mode != "stage": raise ValueError()
        if command in {"seed", "stage", "promote"} and command != mode: raise ValueError()
        if values.get("GITHUB_ACTIONS") != "true" or values.get("GITHUB_REF") != "refs/heads/main" or values.get("GITHUB_EVENT_NAME") != "workflow_dispatch": raise ValueError()
        repository = values["GITHUB_REPOSITORY"]; sha = values["GITHUB_SHA"]
        run = int(values["GITHUB_RUN_ID"]); attempt = int(values["GITHUB_RUN_ATTEMPT"])
        if min(run, attempt) <= 0 or not re.fullmatch(r"[a-f0-9]{40}", sha): raise ValueError()
        http = http or httpx.Client(timeout=10, follow_redirects=False)
        if command == "preflight":
            api = GitHubMetadata(http, repository, values.get("GH_TOKEN"))
            manifest = {"mode": mode, "fixture": fixture, "current_sha": sha, "repository": repository, "execution_ci": api.ci(sha)}
            if mode == "promote":
                source = api.source(int(values["SOURCE_RUN_ID"]))
                if source["run_attempt"] != int(values["SOURCE_RUN_ATTEMPT"]) or source["path"] != WORKFLOW or source["head_branch"] != "main" or source["event"] != "workflow_dispatch" or source["conclusion"] != "success" or source["status"] != "completed" or source["display_title"] != f"webapp-stage-candidate-{source['head_sha']}-{source['run_attempt']}": raise ValueError()
                manifest.update(source=source, ci=api.ci(source["head_sha"]))
            else:
                manifest.update(version=f"{sha}.{run}.{attempt}.{'fixture' if fixture else mode}", source_sha=sha, run_id=run, run_attempt=attempt, ci=manifest["execution_ci"])
            _save(bundle / "verified.json", manifest)
        elif command == "verify-bundle":
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
            credential = credential_factory(values["AZURE_TENANT_ID"], values["AZURE_CLIENT_ID"], lambda: _assertion(values, http))
            token = lambda scope: credential.get_token(scope).token
            audience = values["WEBAPP_FRONTEND_AUDIENCE"]
            frontend = lambda: token(audience.rstrip("/") + "/.default")
            cloud = AzureReleaseTransport(http, token, values["AZURE_TENANT_ID"], values["AZURE_SUBSCRIPTION_ID"], values["AZURE_CLIENT_ID"], values["WEBAPP_DEPLOY_OBJECT_ID"], values["WEBAPP_RESOURCE_ID"], values["WEBAPP_MAIN_URL"], values["WEBAPP_STAGING_URL"])
            intended = _canonical(json.loads(values["WEBAPP_INTENDED_CONFIG"]))
            config = lambda: _canonical(cloud.configuration(intended))
            if config() != intended: raise ValueError()
            clients = {slot: WebAppClient(http, values["WEBAPP_MAIN_URL"] if slot == "main" else values["WEBAPP_STAGING_URL"]) for slot in ("main", "staging")}
            persist = lambda observation: _save(records / f"observation-{observation.check}.json", observation.to_dict())
            now = lambda: datetime.now(timezone.utc)
            if mode == "promote":
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
                    before = smoke(clients["main"], frontend(), baseline, "main", now, time.sleep)
                    persist(before)
                    if not before.matches["baseline_healthy"]: raise ValueError()
                slot = "main" if mode == "seed" else "staging"
                cloud.deploy(slot, package)
                if fixture:
                    fixture_health = clients["staging"].health(frontend())
                    after = clients["main"].health(frontend())
                    from .contracts import Observation
                    persist(Observation("W5", {"fixture_health": fixture_health.http_status, "main_after": after.http_status}, {"fixture_health_503": fixture_health.http_status == 503, "stage_failed": True, "no_swap": True, "main_unchanged": after.http_status == 200 and after.version == baseline and after.slot == "main" and after.caller == "deployment"}, {"swap_calls": 0, "gateway_calls": 0}))
                    raise ValueError()
                observed = smoke(clients[slot], frontend(), version, slot, now, time.sleep)
                persist(observed)
                if not all(observed.matches.values()) or cloud.selected_package_sha256(slot) != manifest["package_sha256"] or config() != intended: raise ValueError()
                from .observations import deployment_access
                access = deployment_access(clients['main'],frontend())
                persist(access)
                if not access.matches['deployment_health_only']: raise ValueError()
                receipt = dict(schema=1, repository=repository, workflow_path=WORKFLOW, source_sha=sha, run_id=run, run_attempt=attempt, version=version, package_sha256=manifest["package_sha256"], kind="candidate" if mode == "stage" else "seed", created_at=now().isoformat().replace("+00:00", "Z"), baseline_version=baseline if mode == "stage" else version, checks_passed=True)
                _save(records / "receipt.json", receipt)
                (records / "package.zip").write_bytes(package)
        print("status=ok")
        return 0
    except Exception:
        print("status=refused")
        return 1
    finally:
        if made_http and http is not None: http.close()
        logging.lastResort=logging_before[1]; logging.disable(logging_before[0])
