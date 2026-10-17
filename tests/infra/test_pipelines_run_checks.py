"""Release and Schedule originals must prove the checks for their own run."""
from datetime import datetime, timezone
from copy import deepcopy
from hashlib import sha256
import importlib
import json
from types import SimpleNamespace

import pytest


@pytest.fixture
def run_originals(repo_root, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "eval"))
    evidence = importlib.import_module("scripts.pipelines.evidence")
    source = "a" * 40
    authority = SimpleNamespace(source_sha=source, session="p8-unit", release_digest="b" * 64)
    principals = {role: {"service_connection_id": "connection-" + role}
                  for role in ("test", "prod", "verify")}
    # These literals model the two disjoint compiled branches, not a selector
    # computed by the code under test: Manual uses six checks; Schedule uses two.
    definitions = [
        (101, "branch", "endpoint", "connection-test"),
        (102, "required-template", "endpoint", "connection-test"),
        (201, "branch", "endpoint", "connection-prod"),
        (202, "required-template", "endpoint", "connection-prod"),
        (301, "branch", "endpoint", "connection-verify"),
        (302, "required-template", "endpoint", "connection-verify"),
        (401, "approval", "environment", "2"),
        (402, "exclusive-lock", "environment", "2"),
    ]
    expected, actual = [], []
    for ident, kind, resource_type, resource_id in definitions:
        resource = {"type": resource_type, "id": resource_id}
        type_id = "type-" + kind
        expected.append({"id": ident, "kind": kind, "type_id": type_id,
                         "resource": resource, "version": 1,
                         "settings_sha256": sha256(b"{}").hexdigest()})
        actual.append({"id": ident, "type": {"id": type_id}, "resource": resource,
                       "version": 1, "settings": {}, "isDisabled": False})
    raw = {"pipeline_id": 2, "principals": principals, "owner_ado_id": "owner",
           "checks_expected": {"approval_ref": "unit-owner-approval", "checks": expected}}
    monkeypatch.setattr(evidence, "load", lambda directory: (authority, raw, {}))
    # Corpus/scorer and authority parsing have their own tests. Keep the original
    # registry, hashes, build provenance, snapshots, check suites and approval real.
    monkeypatch.setattr(evidence, "verify_bundle", lambda *args:
                        SimpleNamespace(manifest=SimpleNamespace(source_sha=source)))
    monkeypatch.setattr(evidence.Originals, "gate", lambda *args, **kwargs:
                        {"evaluated": 229, "correct": 120, "errors": 0, "missing": 0, "skipped": 0})
    (tmp_path / "authority.json").write_text("{}")
    registry = {"kind": "actual", "session": authority.session, "source_sha": source, "files": {}}

    def save(name, value):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(value))
        registry["files"][name] = {
            "kind": "actual", "session": authority.session, "source_sha": source,
            "path": path.name, "sha256": sha256(path.read_bytes()).hexdigest(),
            "channel": "runtime", "captured_at": datetime.now(timezone.utc).isoformat(),
        }
        (tmp_path / "originals.json").write_text(json.dumps(registry))

    def suites(plan, ids):
        rows = []
        for ident in ids:
            check = next(row for row in expected if row["id"] == ident)
            rows.append({"id": "run-" + str(ident), "status": "approved",
                         "checkConfigurationRef": {"id": ident, "type": {"id": check["type_id"]},
                                                   "resource": check["resource"]}})
        return [{"context": {"PlanId": plan}, "checkRuns": rows}]

    save("bundle", {})
    for name, ident, reason, plan in [("release", 11, "manual", "release-plan"),
                                      ("scheduled", 12, "schedule", "schedule-plan")]:
        save(name + "-build", {"id": ident, "definition": {"id": 2}, "sourceVersion": source,
             "sourceBranch": "refs/heads/main", "status": "completed", "result": "succeeded",
             "reason": reason, "orchestrationPlan": {"planId": plan}})
    save("checks-snapshot", {"checks": actual, "pipeline_authorized": True, "pool_authorized": True,
                            "private_project": True, "main_protected": True, "secrets_masked": True})
    save("checks-evaluations", suites("release-plan", [101, 102, 201, 202, 401, 402]))
    save("scheduled-checks-evaluations", suites("schedule-plan", [301, 302]))
    save("prod-approval", {"id": "approval", "status": "approved",
                          "steps": [{"status": "approved", "actualApprover": {"id": "owner"}}]})
    save("prod-approval-map", {"build_id": 11, "approval_id": "approval", "check_run_id": "run-401"})
    save("definition-disabled", {"id": 2, "queueStatus": "disabled"})
    save("queue-after", {"count": 0, "value": []})
    return evidence, tmp_path, raw, registry, save, suites


def test_manual_release_passes_with_its_six_checks_without_schedule_checks(run_originals):
    evidence, directory, *_ = run_originals
    assert evidence.grade(directory)["P8-5"]["status"] == "PASS"


@pytest.mark.parametrize("missing", [101, 102, 201, 202, 401, 402])
def test_manual_release_still_requires_each_of_its_six_checks(run_originals, missing):
    evidence, directory, _, _, save, suites = run_originals
    save("checks-evaluations", suites("release-plan", [x for x in [101, 102, 201, 202, 401, 402] if x != missing]))
    assert evidence.grade(directory)["P8-5"] == {"status": "FAIL", "code": "checks"}


@pytest.mark.parametrize("ids", [[], [301], [302]])
def test_schedule_requires_both_verify_checks_from_its_own_plan(run_originals, ids):
    evidence, directory, _, _, save, suites = run_originals
    save("scheduled-checks-evaluations", suites("schedule-plan", ids))
    assert evidence.grade(directory)["P8-7"] == {"status": "FAIL", "code": "checks"}


def test_schedule_accepts_its_two_current_verify_checks(run_originals):
    evidence, directory, *_ = run_originals
    assert evidence.grade(directory)["P8-7"]["status"] == "PASS"


def test_schedule_cannot_borrow_verify_checks_from_another_plan(run_originals):
    evidence, directory, _, _, save, suites = run_originals
    save("scheduled-checks-evaluations", suites("foreign-plan", [301, 302]))
    assert evidence.grade(directory)["P8-7"] == {"status": "FAIL", "code": "checks"}


def test_missing_schedule_check_original_is_not_run(run_originals):
    evidence, directory, _, registry, *_ = run_originals
    registry["files"].pop("scheduled-checks-evaluations")
    (directory / "originals.json").write_text(json.dumps(registry))
    assert evidence.grade(directory)["P8-7"]["status"] == "NOT RUN"


def test_cleanup_without_an_actual_result_is_not_run(run_originals):
    evidence, directory, *_ = run_originals
    assert evidence.grade(directory)["P8-8"]["status"] == "NOT RUN"


def test_failed_cleanup_attempt_without_result_stays_failed(run_originals):
    evidence, directory, *_ = run_originals
    (directory / "cleanup-attempt-1.json").write_text(json.dumps({"status": "failed", "physical_pass": False}))
    assert evidence.grade(directory)["P8-8"]["status"] == "FAIL"


@pytest.mark.parametrize("change", ["missing-verify", "foreign-verify", "duplicate-endpoint",
                                    "wrong-endpoint-kind", "split-environment", "duplicate-check"])
def test_selecting_release_cannot_discard_a_broken_unused_verify_contract(run_originals, change):
    evidence, _, raw, *_ = run_originals
    from app.delivery_search.contracts import DeliveryRefused
    checks = deepcopy(raw["checks_expected"]["checks"])
    principals = deepcopy(raw["principals"])
    if change == "missing-verify": checks.pop(4)
    if change == "foreign-verify": checks[4]["resource"]["id"] = "unapproved-connection"
    if change == "duplicate-endpoint": principals["verify"]["service_connection_id"] = "connection-test"
    if change == "wrong-endpoint-kind": checks[4]["kind"] = "approval"
    if change == "split-environment": checks[7]["resource"]["id"] = "unapproved-environment"
    if change == "duplicate-check": checks[4]["id"] = 101
    with pytest.raises(DeliveryRefused, match="checks"):
        evidence.checks_for_run(checks, principals)


def test_schedule_cannot_substitute_test_connection_checks(run_originals):
    evidence, directory, _, _, save, suites = run_originals
    save("scheduled-checks-evaluations", suites("schedule-plan", [101, 102]))
    assert evidence.grade(directory)["P8-7"] == {"status": "FAIL", "code": "checks"}


def test_schedule_rejects_a_mixed_plan_suite_collection(run_originals):
    evidence, directory, _, _, save, suites = run_originals
    save("scheduled-checks-evaluations", suites("schedule-plan", [301, 302]) + suites("foreign-plan", [101, 102]))
    assert evidence.grade(directory)["P8-7"] == {"status": "FAIL", "code": "checks"}
