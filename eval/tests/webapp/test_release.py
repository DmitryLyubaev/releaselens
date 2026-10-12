import copy
import hashlib
import io
import stat
import sys
import zipfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
import pytest
from app.webapp.contracts import Health
from app.webapp.private_az import TransportRefused
from app.webapp import release as r

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import package_webapp as p

SHA = "a" * 40
VERSION = SHA + ".123.1.stage"
BASELINE = SHA + ".122.1.seed"
NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
REPO = "portfolio/releaselens"


def receipt():
    return dict(schema=1, repository=REPO, workflow_path=".github/workflows/webapp-release.yml", source_sha=SHA,
                run_id=123, run_attempt=1, version=VERSION, package_sha256="b" * 64, kind="candidate",
                created_at="2026-10-12T00:01:00Z", baseline_version=BASELINE, checks_passed=True)


def source():
    return dict(id=123, run_attempt=1, repository={"full_name": REPO}, head_repository={"full_name": REPO},
                path=".github/workflows/webapp-release.yml", head_sha=SHA, head_branch="main", event="workflow_dispatch",
                status="completed", conclusion="success", display_title=f"webapp-stage-candidate-{SHA}-1",
                created_at="2026-10-12T00:00:00Z", updated_at="2026-10-12T00:02:00Z", jobs=[{"name": "release", "status": "completed", "conclusion": "success"}])


def ci():
    return dict(repository={"full_name": REPO}, path=".github/workflows/ci.yml", head_sha=SHA, head_branch="main",
                event="push", status="completed", conclusion="success", jobs=[{"name": "build-and-test", "status": "completed", "conclusion": "success"}])


def test_only_successful_trusted_main_stage_and_required_ci_are_eligible():
    assert r.validate_source(source(), receipt(), REPO, ci())


@pytest.mark.parametrize("target,key,value", [
    ("source", "conclusion", "failure"), ("source", "run_attempt", 2), ("source", "id", 124),
    ("source", "head_branch", "feature"), ("source", "event", "pull_request_target"),
    ("source", "path", ".github/workflows/other.yml"), ("source", "head_sha", "c" * 40),
    ("source", "repository", {"full_name": "foreign/releaselens"}),
    ("source", "display_title", f"webapp-stage-fixture-{SHA}-1"),
    ("source", "jobs", [{"name": "release", "status": "completed", "conclusion": "skipped"}]),
    ("receipt", "kind", "fixture"), ("receipt", "version", SHA + ".123.1.fixture"),
    ("receipt", "checks_passed", False), ("receipt", "run_attempt", True),
    ("receipt", "created_at", "2026-10-13T00:00:00Z"), ("receipt", "runtime_host", "secret.example.test"),
    ("ci", "conclusion", "failure"), ("ci", "head_sha", "d" * 40),
    ("ci", "jobs", []), ("ci", "jobs", [{"name": "build-and-test", "status": "completed", "conclusion": "skipped"}]),
])
def test_failed_foreign_fixture_stale_or_untrusted_source_is_ineligible(target, key, value):
    values = {"source": source(), "receipt": receipt(), "ci": ci()}
    values[target][key] = value
    assert not r.validate_source(values["source"], values["receipt"], REPO, values["ci"])


def test_missing_receipt_is_ineligible():
    assert not r.validate_source(source(), {}, REPO, ci())


def publish(directory):
    directory.mkdir()
    for name in ("ReleaseLens.Web.dll", "ReleaseLens.Web.deps.json", "ReleaseLens.Web.runtimeconfig.json"):
        (directory / name).write_bytes(b"test published content")


def test_package_hash_binds_deterministic_bytes_and_startup_layout(tmp_path):
    folder = tmp_path / "publish"; publish(folder)
    first = tmp_path / "first.zip"; second = tmp_path / "second.zip"
    digest = p.create_zip(folder, first)
    assert digest == hashlib.sha256(first.read_bytes()).hexdigest()
    assert p.create_zip(folder, second) == digest
    r.validate_package(first.read_bytes(), digest)
    with zipfile.ZipFile(first) as archive:
        assert "ReleaseLens.Web.dll" in archive.namelist()
    with pytest.raises(TransportRefused): r.validate_package(first.read_bytes(), "c" * 64)


def test_package_omits_debug_symbols_that_can_hold_local_source_paths(tmp_path):
    folder = tmp_path / "publish"; publish(folder)
    (folder / "ReleaseLens.Web.pdb").write_bytes(b"local source path marker")
    output = tmp_path / "package.zip"
    p.create_zip(folder, output)
    with zipfile.ZipFile(output) as archive:
        assert not any(name.endswith(".pdb") for name in archive.namelist())


@pytest.mark.parametrize("name", [".env", ".env.local", "appsettings.Production.json", "terraform.tfvars", "local.tfstate", "credentials.json"])
def test_package_refuses_settings_or_identity_files_without_reading_them(tmp_path, name):
    folder = tmp_path / "publish"; publish(folder)
    (folder / name).write_text("private marker")
    with pytest.raises(ValueError, match="package_refused"): p.create_zip(folder, tmp_path / "package.zip")
    assert not (tmp_path / "package.zip").exists()


@pytest.mark.parametrize("name,mode", [("../escape.dll", 0), ("/absolute.dll", 0), ("C:\\escape.dll", 0), ("link.dll", stat.S_IFLNK)])
def test_artifact_zip_cannot_escape_or_install_symlinks(name, mode):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for item in ("ReleaseLens.Web.dll", "ReleaseLens.Web.deps.json", "ReleaseLens.Web.runtimeconfig.json"): archive.writestr(item, "content")
        info = zipfile.ZipInfo(name); info.external_attr = (mode | 0o777) << 16
        archive.writestr(info, "target")
    value = data.getvalue()
    with pytest.raises(TransportRefused): r.validate_package(value, hashlib.sha256(value).hexdigest())


class Clock:
    def __init__(self): self.value = NOW
    def now(self): return self.value
    def sleep(self, seconds): self.value += timedelta(seconds=seconds)


class Client:
    def __init__(self, version, slot): self.value = Health(200, "ok", version, slot, True, "deployment"); self.calls = 0
    def health(self, bearer): self.calls += 1; return self.value


@pytest.mark.parametrize("field,value", [("version", BASELINE), ("slot", "main"), ("caller", "owner"), ("http_status", 302), ("configuration_valid", False)])
def test_smoke_denies_identity_version_slot_auth_or_configuration_mismatch_immediately(field, value):
    from dataclasses import replace
    clock = Clock(); client = Client(VERSION, "staging")
    client.value = replace(client.value, **{field: value})
    result = r.smoke(client, "private bearer", VERSION, "staging", clock.now, clock.sleep)
    assert result.matches["staging_healthy"] is False
    assert client.calls == 1


def test_readiness_stops_at_30_attempts_within_five_minutes():
    clock = Clock(); client = Client(VERSION, "staging"); client.value = Health(503)
    result = r.smoke(client, "private bearer", VERSION, "staging", clock.now, clock.sleep)
    assert not result.matches["staging_healthy"]
    assert client.calls == 30
    assert clock.now() - NOW <= timedelta(minutes=5)


class Az:
    def __init__(self, main, staging): self.calls = []; self.main = main; self.staging = staging; self.fail_after = False
    def call(self, args):
        self.calls.append(args)
        self.main.value, self.staging.value = Health(200, "ok", VERSION, "main", True, "deployment"), Health(200, "ok", BASELINE, "staging", True, "deployment")
        if self.fail_after: self.main.value = Health(503)
        return {}


def context():
    clock = Clock(); main = Client(BASELINE, "main"); staging = Client(VERSION, "staging"); az = Az(main, staging)
    saved = []
    cfg = {"main": {"protected": True}, "staging": {"protected": True}}
    return r.ReleaseContext(az=az, main=main, staging=staging, frontend_bearer=lambda: "private bearer",
                            resource_id="/subscriptions/mock/resourceGroups/mock/providers/Microsoft.Web/sites/mock",
                            expected_repository=REPO, ci_metadata=ci(), intended_configuration=copy.deepcopy(cfg),
                            configuration=lambda: copy.deepcopy(cfg), selected_package_sha256=lambda slot: "b" * 64,
                            latest_source=lambda: (source(), ci()), persist=saved.append, clock=clock.now, sleep=clock.sleep), saved


@pytest.mark.parametrize("changed", ["staging", "main", "digest", "configuration", "source_attempt"])
def test_changed_candidate_or_baseline_blocks_swap_and_persists_failure(changed):
    ctx, saved = context()
    if changed == "staging": ctx.staging.value = Health(200, "ok", BASELINE, "staging", True, "deployment")
    elif changed == "main": ctx.main.value = Health(200, "ok", VERSION, "main", True, "deployment")
    elif changed == "digest": ctx.selected_package_sha256 = lambda _: "c" * 64
    elif changed == "configuration": ctx.configuration = lambda: {"changed": True}
    else: ctx.latest_source = lambda: (source() | {"run_attempt": 2}, ci())
    with pytest.raises(TransportRefused): r.promote(ctx, receipt(), source())
    assert ctx.az.calls == []
    assert saved and not saved[0].matches["swap_succeeded"]


def test_successful_swap_checks_both_versions_and_settings():
    ctx, saved = context()
    w4, config = r.promote(ctx, receipt(), source())
    assert all(w4.matches.values())
    assert config.matches["settings_retained"]
    assert len(ctx.az.calls) == 1
    assert "slotsswap" in " ".join(ctx.az.calls[0])
    assert "preview" not in " ".join(ctx.az.calls[0])
    assert len(saved) == 2


def test_post_swap_failure_is_recorded_without_automatic_rollback():
    ctx, saved = context(); ctx.az.fail_after = True
    with pytest.raises(TransportRefused): r.promote(ctx, receipt(), source())
    assert len(ctx.az.calls) == 1
    assert saved[0].matches["swap_succeeded"]
    assert not saved[0].matches["main_candidate"]


def test_post_swap_transport_failure_still_records_the_swap():
    ctx, saved = context()
    calls = []
    def configuration():
        calls.append(True)
        if len(calls) == 2: raise TransportRefused("network")
        return ctx.intended_configuration
    ctx.configuration = configuration
    with pytest.raises(TransportRefused): r.promote(ctx, receipt(), source())
    assert saved and saved[0].matches["swap_succeeded"]
    assert saved[0].counts["swap_calls"] == 1


def test_failed_swap_request_is_counted_as_an_attempt():
    ctx, saved = context()
    def failed(args): raise TransportRefused("network")
    ctx.az.call = failed
    with pytest.raises(TransportRefused): r.promote(ctx, receipt(), source())
    assert saved[0].counts["swap_calls"] == 1
    assert not saved[0].matches["swap_succeeded"]
