"""Original-result and TRX mutations must never become false acceptance passes."""
import copy
import hashlib
import importlib.util
import json

import pytest


@pytest.fixture
def evidence(repo_root):
    spec = importlib.util.spec_from_file_location("agents_evidence", repo_root / "scripts/agents/evidence.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def observed(tmp_path):
    source, image, session = "a" * 40, "ghcr.io/example/releaselens-agent@sha256:" + "b" * 64, "demo-test"
    group = "/subscriptions/private-sub/resourceGroups/rg-p7-demo-test"
    owned = [group] + [group + "/providers/Example/type/" + str(i) for i in range(8)]
    runs, paths = [], []
    for i in range(3):
        folder = tmp_path / str(i)
        agent = f"p7-{session}-worker-nonce{i}"
        hashes = {}
        for name in ("ReleaseLens.Core.Tests", "ReleaseLens.Web.Tests"):
            path = folder / name / f"{name}.trx"
            path.parent.mkdir(parents=True)
            path.write_text('''<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">
              <Results><UnitTestResult executionId="test-1" outcome="Passed" duration="00:00:00.250" /></Results>
              <ResultSummary outcome="Completed"><Counters total="1" executed="1" passed="1" failed="0" notExecuted="0" /></ResultSummary>
            </TestRun>''')
            hashes[path.relative_to(folder).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
            paths.append(path)
        timeline = [{"type": "Task", "name": name, "result": "succeeded"} for name in
                    ("Require approved source and fresh workspace", "Build and test Core and Web")]
        timeline[1].update(startTime=f"2026-10-15T00:{i}0:35Z", finishTime=f"2026-10-15T00:{i}0:55Z")
        if i == 2:
            timeline.append({"type": "Task", "name": "Intentional negative pipeline step", "result": "failed"})
        timeline.extend({"type": "Task", "name": name, "result": "succeeded"} for name in
                        ("Publish Project 7 test results", "Publish Project 7 workload artifact"))
        runs.append({"build": {"id": i + 1, "status": "completed", "result": "failed" if i == 2 else "succeeded",
                               "sourceVersion": source, "sourceBranch": "refs/heads/main",
                               "queueTime": f"2026-10-15T00:{i}0:00Z", "startTime": f"2026-10-15T00:{i}0:30Z",
                               "finishTime": f"2026-10-15T00:{i}1:00Z"},
                     "timeline": {"records": timeline}, "artifact_dir": str(folder),
                     "execution": {"name": f"exec-private-{i}", "properties": {"status": "Succeeded",
                                   "startTime": f"2026-10-15T00:{i}0:10Z", "endTime": f"2026-10-15T00:{i}1:10Z",
                                   "template": {"containers": [{"image": image}]}}},
                     "workload": {"schema_version": 1, "build_id": str(i + 1), "source": source, "agent": agent,
                                  "session": session, "workspace_fresh": True, "workspace_id": f"workspace-{i}", "trx": hashes},
                     "pool_snapshot": {"value": [{"name": agent, "status": "online"}]},
                     "activity": {"value": []},
                     "worker_samples": [{"time": f"2026-10-15T00:{i}0:30Z", "executions": [f"exec-private-{i}"]}],
                     "idle_samples": [{"time": f"2026-10-15T00:{i}2:{sec}Z", "workers": [], "online_agents": [], "queued": 0}
                                      for sec in ("00", "30")] +
                                     [{"time": f"2026-10-15T00:{i}3:00Z", "workers": [], "online_agents": [], "queued": 0}]})
    obs = {"schema_version": 1, "source": source, "image_digest": image, "session": session,
           "initial_idle": {"time": "2026-10-14T23:59:59Z", "workers": [], "online_agents": [], "queued": 0},
           "window": {"apply_started_at": "2026-10-14T23:59:00Z", "queue_cutoff_at": "2026-10-15T00:29:00Z",
                      "cleanup_started_at": "2026-10-15T00:30:00Z", "cleanup_deadline_at": "2026-10-15T00:59:00Z",
                      "closeout_at": "2026-10-15T00:40:00Z"},
           "worker_definition": {"properties": {"configuration": {"triggerType": "Event"}}},
           "placeholder": {"name": f"p7-{session}-placeholder", "status": "offline"}, "runs": runs,
           "owned_group_id": group, "owned_ids": owned,
           "closeout": {"group_exists": False, "state_read_ok": True, "terraform_addresses": [],
                        "destroyed_ids": owned, "operations_active": [], "cleanup_lock_exclusive": True,
                        "queue_disabled": True, "queued": 0, "pat_revoked": True,
                        "pat_revocation_confirmed_at": "2026-10-15T00:40:00Z",
                        "retained_before": {"private-bootstrap-group": ["private-retained-resource"]},
                        "retained_after": {"private-bootstrap-group": ["private-retained-resource"]},
                        "inventories_complete": True}}
    return obs, paths


def test_actual_positive_and_expected_negative_grade(evidence, observed):
    obs, paths = observed
    result = evidence.grade(obs, paths)
    assert {k: v["status"] for k, v in result["checks"].items()} == {f"A{i}": "pass" for i in range(1, 7)}
    assert result["runs"][0]["executed_tests"] == 2
    assert result["runs"][2]["pipeline_result"] == "failed"
    assert result["runs"][0]["queue_to_agent_seconds"] == 30
    assert result["runs"][0]["build_test_seconds"] == 20
    assert "exec-private" not in json.dumps(result)
    assert "private-sub" not in json.dumps(result)
    assert "artifact_dir" not in json.dumps(result)


def test_early_cleanup_does_not_require_waiting_for_scheduled_queue_cutoff(evidence, observed):
    obs, paths = observed
    # All three runs and idle checks ended by 00:23; cleanup may begin immediately.
    obs["window"].update(cleanup_started_at="2026-10-15T00:24:00Z", closeout_at="2026-10-15T00:27:00Z")
    obs["closeout"]["pat_revocation_confirmed_at"] = "2026-10-15T00:26:00Z"
    assert evidence.grade(obs, paths)["checks"]["A6"]["status"] == "pass"


@pytest.fixture
def approved_ninety(observed):
    obs, paths = observed
    obs["source_tree"] = "c" * 40
    obs["timing_approval"] = {
        "profile": "owner-approved-90", "approved": True,
        "source": obs["source"], "session": obs["session"],
        "reviewed_tree": obs["source_tree"],
        "owner_approval_reference": "chat:synthetic-test-only",
        "recorded_at": "2026-10-14T23:58:00Z",
    }
    obs["window"].update(queue_cutoff_at="2026-10-15T00:44:00Z",
                         cleanup_started_at="2026-10-15T00:49:00Z",
                         cleanup_deadline_at="2026-10-15T01:29:00Z",
                         closeout_at="2026-10-15T01:20:00Z")
    obs["closeout"]["pat_revocation_confirmed_at"] = "2026-10-15T01:19:00Z"
    return obs, paths


def test_prospectively_approved_fixed_ninety_minute_window(evidence, approved_ninety):
    obs, paths = approved_ninety
    result = evidence.grade(obs, paths)
    assert all(c["status"] == "pass" for c in result["checks"].values())
    assert "synthetic-test-only" not in json.dumps(result)


@pytest.mark.parametrize("mutation", [
    "absent", "null", "false", "truthy", "profile", "source", "session", "tree",
    "missing_tree", "reference", "missing_reference", "late", "invalid_time", "naive_time",
])
def test_extended_window_requires_bound_pre_apply_approval(evidence, approved_ninety, mutation):
    obs, paths = approved_ninety
    approval = obs["timing_approval"]
    if mutation == "absent": del obs["timing_approval"]
    if mutation == "null": obs["timing_approval"] = None
    if mutation == "false": approval["approved"] = False
    if mutation == "truthy": approval["approved"] = "yes"
    if mutation == "profile": approval["profile"] = "owner-approved-120"
    if mutation == "source": approval["source"] = "d" * 40
    if mutation == "session": approval["session"] = "other-session"
    if mutation == "tree": approval["reviewed_tree"] = "e" * 40
    if mutation == "missing_tree": del obs["source_tree"]
    if mutation == "reference": approval["owner_approval_reference"] = " "
    if mutation == "missing_reference": del approval["owner_approval_reference"]
    if mutation == "late": approval["recorded_at"] = "2026-10-14T23:59:01Z"
    if mutation == "invalid_time": approval["recorded_at"] = "unknown"
    if mutation == "naive_time": approval["recorded_at"] = "2026-10-14T23:58:00"
    assert evidence.grade(obs, paths)["checks"]["A6"]["status"] == "fail"


@pytest.mark.parametrize("field,value", [
    ("queue_cutoff_at", "2026-10-15T00:44:01Z"),
    ("cleanup_started_at", "2026-10-15T00:49:01Z"),
    ("cleanup_deadline_at", "2026-10-15T01:29:01Z"),
    ("closeout_at", "2026-10-15T01:29:01Z"),
])
def test_approved_window_still_enforces_each_fixed_bound(evidence, approved_ninety, field, value):
    obs, paths = approved_ninety
    obs["window"][field] = value
    assert evidence.grade(obs, paths)["checks"]["A6"]["status"] == "fail"


def test_old_late_cleanup_is_not_regraded_by_new_profile(evidence, observed):
    obs, paths = observed
    obs["window"].update(cleanup_started_at="2026-10-15T00:45:45Z",
                         closeout_at="2026-10-15T01:12:00Z")
    obs["closeout"]["pat_revocation_confirmed_at"] = "2026-10-15T00:46:00Z"
    assert evidence.grade(obs, paths)["checks"]["A6"]["status"] == "fail"


@pytest.mark.parametrize("cleanup,queued", [
    ("2026-10-15T00:24:00Z", "2026-10-15T00:24:01Z"),
    ("2026-10-15T00:30:00Z", "2026-10-15T00:29:01Z"),
])
def test_no_run_can_be_queued_after_cleanup_or_cutoff(evidence, observed, cleanup, queued):
    obs, paths = observed
    obs["window"]["cleanup_started_at"] = cleanup
    obs["runs"][2]["build"]["queueTime"] = queued
    assert evidence.grade(obs, paths)["checks"]["A6"]["status"] == "fail"


@pytest.mark.parametrize("mutation,check", [
    ("manual", "A1"), ("source", "A2"), ("image", "A1"), ("duplicates", "A1"),
    ("agent_reuse", "A3"), ("workspace_reuse", "A3"), ("publication", "A2"),
    ("negative_success", "A4"), ("negative_other_failure", "A4"), ("timestamp", "A1"),
    ("idle", "A5"), ("inventory", "A6"), ("pat", "A6"), ("apply_active", "A6"),
    ("ownership", "A6"), ("state_unknown", "A6"),
])
def test_inconsistent_originals_fail(evidence, observed, mutation, check):
    obs, paths = observed
    first = obs["runs"][0]
    if mutation == "manual": first["activity"]["value"] = [{"operationName": {"value": "Microsoft.App/jobs/start/action"}}]
    if mutation == "source": first["build"]["sourceVersion"] = "c" * 40
    if mutation == "image": first["execution"]["properties"]["template"]["containers"][0]["image"] = "wrong"
    if mutation == "duplicates": first["worker_samples"][0]["executions"].append("unexpected")
    if mutation == "agent_reuse": obs["runs"][1]["workload"]["agent"] = first["workload"]["agent"]
    if mutation == "workspace_reuse": obs["runs"][1]["workload"]["workspace_id"] = first["workload"]["workspace_id"]
    if mutation == "publication": first["workload"]["trx"] = {}
    if mutation == "negative_success": obs["runs"][2]["build"]["result"] = "succeeded"
    if mutation == "negative_other_failure": obs["runs"][2]["timeline"]["records"][1]["result"] = "failed"
    if mutation == "timestamp": first["build"]["startTime"] = "not-a-time"
    if mutation == "idle": first["idle_samples"][1]["workers"] = ["still-running"]
    if mutation == "inventory": obs["closeout"]["retained_after"] = {}
    if mutation == "pat": obs["closeout"]["pat_revoked"] = False
    if mutation == "apply_active": obs["closeout"]["operations_active"] = ["terraform"]
    if mutation == "ownership": obs["closeout"]["destroyed_ids"].append("unrelated")
    if mutation == "state_unknown": obs["closeout"]["state_read_ok"] = False
    assert evidence.grade(obs, paths)["checks"][check]["status"] == "fail"


@pytest.mark.parametrize("change", ["missing", "empty", "skipped", "failed", "hash", "counter_only"])
def test_invalid_trx_never_passes(evidence, observed, change):
    obs, paths = observed
    path = paths[0]
    if change == "missing": path.unlink()
    else:
        text = path.read_text()
        if change == "empty": text = text.replace('total="1" executed="1" passed="1"', 'total="0" executed="0" passed="0"').replace('<UnitTestResult executionId="test-1" outcome="Passed" duration="00:00:00.250" />', '')
        if change == "skipped": text = text.replace('outcome="Passed"', 'outcome="NotExecuted"')
        if change == "failed": text = text.replace('outcome="Passed"', 'outcome="Failed"')
        if change == "counter_only": text = text.replace('executed="1"', 'executed="2"')
        if change == "hash": text += ' '
        path.write_text(text)
        if change != "hash": obs["runs"][0]["workload"]["trx"][path.relative_to(path.parents[1]).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert evidence.grade(obs, paths)["checks"]["A2"]["status"] == "fail"


def test_missing_results_stay_not_run_and_output_sanitized(evidence):
    result = evidence.grade({"source": "private-secret", "image_digest": "private-token"}, [])
    assert all(c["status"] == "not-run" for c in result["checks"].values())
    assert "private-" not in json.dumps(result)


def test_nonfinite_or_negative_time_is_rejected(evidence, observed):
    obs, paths = observed
    obs["runs"][0]["build"]["startTime"] = "2020-01-01T00:00:00Z"
    assert evidence.grade(obs, paths)["checks"]["A1"]["status"] == "fail"


@pytest.mark.parametrize("change,check", [("baseline", "A1"), ("overlap", "A3"), ("deadline", "A6"), ("queue_late", "A6")])
def test_session_origin_and_time_bounds(evidence, observed, change, check):
    obs, paths = observed
    if change == "baseline": obs["initial_idle"]["workers"] = ["existing"]
    if change == "overlap": obs["runs"][1]["build"]["queueTime"] = obs["runs"][0]["build"]["queueTime"]
    if change == "deadline": obs["window"]["closeout_at"] = "2026-10-15T01:10:00Z"
    if change == "queue_late": obs["window"]["queue_cutoff_at"] = "2026-10-15T00:10:00Z"
    assert evidence.grade(obs, paths)["checks"][check]["status"] == "fail"


@pytest.mark.parametrize("name", ["Publish Project 7 test results", "Publish Project 7 workload artifact"])
@pytest.mark.parametrize("status", ["missing", "skipped", "failed"])
def test_failed_or_missing_negative_publishers_are_not_credited(evidence, observed, name, status):
    obs, paths = observed
    records = obs["runs"][2]["timeline"]["records"]
    if status == "missing":
        records[:] = [r for r in records if r["name"] != name]
    else:
        next(r for r in records if r["name"] == name)["result"] = status
    result = evidence.grade(obs, paths)
    assert result["checks"]["A2"]["status"] == "fail"
    assert result["checks"]["A4"]["status"] == "fail"


@pytest.mark.parametrize("change", ["missing", "malformed", "stale", "late", "unordered", "wrong_execution"])
def test_worker_observations_are_current_and_ordered(evidence, observed, change):
    obs, paths = observed
    run = obs["runs"][0]
    sample = run["worker_samples"][0]
    if change == "missing": del sample["time"]
    if change == "malformed": sample["time"] = "unknown"
    if change == "stale": sample["time"] = "2000-01-01T00:00:00Z"
    if change == "late": sample["time"] = "2026-10-15T01:00:00Z"
    if change == "unordered":
        run["worker_samples"].append({**sample, "time": "2026-10-15T00:00:20Z"})
    if change == "wrong_execution":
        run["worker_samples"].append({"time": "2026-10-15T00:00:40Z", "executions": ["other-worker"]})
    assert evidence.grade(obs, paths)["checks"]["A1"]["status"] == "fail"


@pytest.mark.parametrize("identity", ["agent", "execution", "workspace_id", "build"])
def test_negative_run_cannot_reuse_one_job_identity(evidence, observed, identity):
    obs, paths = observed
    previous, negative = obs["runs"][1:]
    if identity == "agent":
        negative["workload"]["agent"] = previous["workload"]["agent"]
        negative["pool_snapshot"]["value"][0]["name"] = previous["workload"]["agent"]
    if identity == "execution":
        negative["execution"]["name"] = previous["execution"]["name"]
        negative["worker_samples"][0]["executions"] = [previous["execution"]["name"]]
    if identity == "workspace_id": negative["workload"]["workspace_id"] = previous["workload"]["workspace_id"]
    if identity == "build":
        negative["build"]["id"] = previous["build"]["id"]
        negative["workload"]["build_id"] = previous["workload"]["build_id"]
    assert evidence.grade(obs, paths)["checks"]["A3"]["status"] == "fail"
