"""Immutable release gate. Runtime configuration and tokens stay in memory."""
from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable

from .contracts import Observation, VERSION, utc_time
from .private_az import TransportRefused

WORKFLOW = ".github/workflows/webapp-release.yml"
RECEIPT_KEYS = {"schema", "repository", "workflow_path", "source_sha", "run_id", "run_attempt", "version", "package_sha256", "kind", "created_at", "baseline_version", "checks_passed"}


def ci_passed(metadata: dict, repository: str, source_sha: str) -> bool:
    try:
        jobs = [job for job in metadata["jobs"] if job.get("name") == "build-and-test"]
        return (metadata["repository"]["full_name"] == repository and metadata["path"] == ".github/workflows/ci.yml"
                and metadata["head_sha"] == source_sha and metadata["head_branch"] == "main" and metadata["event"] == "push"
                and metadata["status"] == "completed" and metadata["conclusion"] == "success"
                and len(jobs) == 1 and jobs[0]["status"] == "completed" and jobs[0]["conclusion"] == "success")
    except (KeyError, TypeError, AttributeError): return False


def validate_source(run_metadata: dict, receipt: dict, expected_repository: str, ci_metadata: dict) -> bool:
    try:
        if set(receipt) != RECEIPT_KEYS or type(receipt["schema"]) is not int or receipt["schema"] != 1: return False
        if type(receipt["run_id"]) is not int or type(receipt["run_attempt"]) is not int or min(receipt["run_id"], receipt["run_attempt"]) <= 0: return False
        if not re.fullmatch(r"[a-f0-9]{40}", receipt["source_sha"]) or not re.fullmatch(r"[a-f0-9]{64}", receipt["package_sha256"]): return False
        if not isinstance(receipt["baseline_version"], str) or not VERSION.fullmatch(receipt["baseline_version"]) or receipt["baseline_version"].endswith(".fixture"): return False
        expected = f"{receipt['source_sha']}.{receipt['run_id']}.{receipt['run_attempt']}.stage"
        if receipt["version"] != expected or receipt["kind"] != "candidate" or receipt["checks_passed"] is not True: return False
        if receipt["repository"] != expected_repository or receipt["workflow_path"] != WORKFLOW: return False
        run = run_metadata
        release_jobs = [job for job in run.get("jobs", []) if job.get("name") == "release"]
        if len(release_jobs) != 1 or release_jobs[0].get("status") != "completed" or release_jobs[0].get("conclusion") != "success": return False
        if (run["repository"]["full_name"] != expected_repository or run["head_repository"]["full_name"] != expected_repository
            or run["path"] != WORKFLOW or run["head_sha"] != receipt["source_sha"] or run["head_branch"] != "main"
            or run["event"] != "workflow_dispatch" or run["status"] != "completed" or run["conclusion"] != "success"
            or run["id"] != receipt["run_id"] or run["run_attempt"] != receipt["run_attempt"]
            or run["display_title"] != f"webapp-stage-candidate-{receipt['source_sha']}-{receipt['run_attempt']}"): return False
        if not all(utc_time(value) for value in (run["created_at"], run["updated_at"], receipt["created_at"])): return False
        parse = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not parse(run["created_at"]) <= parse(receipt["created_at"]) <= parse(run["updated_at"]): return False
        return ci_passed(ci_metadata, expected_repository, receipt["source_sha"])
    except (KeyError, TypeError, ValueError, AttributeError): return False


def validate_package(value: bytes, expected_sha256: str) -> None:
    # The helper is loaded only from this trusted checkout, never from an artifact.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    from package_webapp import safe_name, REQUIRED
    try:
        if len(value) > 200 * 1024 * 1024 or hashlib.sha256(value).hexdigest() != expected_sha256: raise ValueError()
        with zipfile.ZipFile(io.BytesIO(value)) as archive:
            items = archive.infolist()
            names = [item.filename for item in items if not item.is_dir()]
            if len(items) > 10000 or sum(item.file_size for item in items) > 200 * 1024 * 1024: raise ValueError()
            if len({name.casefold() for name in names}) != len(names) or not REQUIRED <= set(names): raise ValueError()
            if any(not safe_name(item.filename) or stat.S_ISLNK(item.external_attr >> 16) for item in items): raise ValueError()
            if archive.testzip() is not None: raise ValueError()
    except (ValueError, OSError, zipfile.BadZipFile, RuntimeError): raise TransportRefused("release_refused") from None


def _time(value: datetime) -> str: return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def smoke(client, bearer: str, expected_version: str, expected_slot: str, clock: Callable, sleep: Callable) -> Observation:
    start = clock(); status = None; attempts = 0; healthy = False
    key = "baseline_healthy" if expected_slot == "main" else "staging_healthy"
    for _ in range(30):
        if (clock() - start).total_seconds() >= 300: break
        attempts += 1
        try:
            health = client.health(bearer); status = health.http_status
            if status == 200:
                healthy = health.status == "ok" and health.configuration_valid is True and health.version == expected_version and health.slot == expected_slot and health.caller == "deployment"
                break
            if status not in {502, 503, 504}: break
        except TransportRefused as error:
            if str(error) != "network": break
        if attempts < 30: sleep(min(10, max(0, 300 - (clock() - start).total_seconds())))
    return Observation("W4", {"health_main" if expected_slot == "main" else "health_staging": status}, {key: healthy}, {"attempts": attempts}, {"started_at": _time(start), "ended_at": _time(clock())})


def fixture_smoke(client,bearer,version,digest,selected_digest,clock,sleep)->Observation:
    start=clock(); matched=False; status=None; attempts=0
    try:
        if selected_digest()!=digest: raise TransportRefused('release_refused')
        for _ in range(30):
            if (clock()-start).total_seconds()>=300: break
            attempts+=1
            try:
                health=client.fixture_health(bearer,version); status=health.status
                if health.version_matches: matched=True; break
                if status not in {502,503,504}: break
            except TransportRefused as error:
                if str(error)!='network': break
            if attempts<30: sleep(min(10,max(0,300-(clock()-start).total_seconds())))
    except TransportRefused: pass
    return Observation('W5',{'fixture_health':status},{'fixture_health_503':matched}, {'attempts':attempts,'gateway_calls':0,'swap_calls':0},{'started_at':_time(start),'ended_at':_time(clock())})


@dataclass(repr=False)
class ReleaseContext:
    az: object
    main: object
    staging: object
    frontend_bearer: Callable
    resource_id: str
    expected_repository: str
    ci_metadata: dict
    intended_configuration: dict
    configuration: Callable
    selected_package_sha256: Callable
    latest_source: Callable
    persist: Callable
    clock: Callable
    sleep: Callable

    def __repr__(self): return "ReleaseContext(runtime values in memory)"


def promote(context: ReleaseContext, receipt: dict, source_metadata: dict) -> tuple[Observation, Observation]:
    ctx = context
    started = ctx.clock(); statuses = {}; swaps = 0; recorded = False
    matches = {key: False for key in ("baseline_healthy", "staging_healthy", "approval_recorded", "swap_succeeded", "main_candidate", "staging_baseline", "slot_labels_retained")}
    before_matches = False; settings_retained = False
    def records():
        nonlocal recorded
        w4 = Observation("W4", statuses, matches, {"swap_calls": swaps, "gateway_calls": 0}, {"started_at": _time(started), "ended_at": _time(ctx.clock())})
        settings = Observation("W6a", matches={"intended_settings_before": before_matches, "settings_retained": settings_retained})
        if not recorded:
            ctx.persist(w4); ctx.persist(settings)
            recorded = True
        return w4, settings
    try:
        latest, latest_ci = ctx.latest_source()
        if not validate_source(source_metadata, receipt, ctx.expected_repository, ctx.ci_metadata) or not validate_source(latest, receipt, ctx.expected_repository, latest_ci): raise TransportRefused("release_refused")
        before = ctx.configuration()
        before_matches = before == ctx.intended_configuration
        if not before_matches: raise TransportRefused("release_refused")
        token = ctx.frontend_bearer()
        main = smoke(ctx.main, token, receipt["baseline_version"], "main", ctx.clock, ctx.sleep)
        stage = smoke(ctx.staging, token, receipt["version"], "staging", ctx.clock, ctx.sleep)
        statuses.update(main.statuses); statuses.update(stage.statuses)
        matches["baseline_healthy"] = main.matches["baseline_healthy"]
        matches["staging_healthy"] = stage.matches["staging_healthy"]
        if not matches["baseline_healthy"] or not matches["staging_healthy"]: raise TransportRefused("release_refused")
        if ctx.selected_package_sha256("staging") != receipt["package_sha256"]: raise TransportRefused("release_refused")
        # The caller enters only inside the protected environment job, after approval.
        matches["approval_recorded"] = True
        swaps = 1
        ctx.az.call(["rest", "--method", "post", "--url", f"https://management.azure.com{ctx.resource_id}/slots/staging/slotsswap?api-version=2024-11-01", "--body", json.dumps({"targetSlot": "production", "preserveVnet": True})])
        matches["swap_succeeded"] = True
        main = smoke(ctx.main, token, receipt["version"], "main", ctx.clock, ctx.sleep)
        stage = smoke(ctx.staging, token, receipt["baseline_version"], "staging", ctx.clock, ctx.sleep)
        statuses.update(main.statuses); statuses.update(stage.statuses)
        matches["main_candidate"] = main.matches["baseline_healthy"]
        matches["staging_baseline"] = stage.matches["staging_healthy"]
        matches["slot_labels_retained"] = matches["main_candidate"] and matches["staging_baseline"]
        settings_retained = ctx.configuration() == before == ctx.intended_configuration
        result = records()
        if not all(matches.values()) or not settings_retained: raise TransportRefused("release_refused")
        return result
    except Exception:
        records()
        raise TransportRefused("release_refused") from None


if __name__ == "__main__":
    from .release_cli import main
    sys.exit(main())
