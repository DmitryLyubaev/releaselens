"""Public projection rejects missing provenance and preserves late physical cleanup."""
import importlib
import json

import pytest


@pytest.fixture
def evidence(repo_root,monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root/"eval"))
    return importlib.import_module("scripts.pipelines.evidence")


def test_provenance_and_timing_grades(evidence,tmp_path):
    result = evidence.grade(tmp_path)
    assert set(result) == {"P8-"+str(i) for i in range(1,9)}
    assert all(v["status"] == "NOT RUN" for v in result.values())
    assert not evidence.timing_pass({"cleanup_started_at":"2026-10-16T09:21:00+00:00",
        "verified_at":"2026-10-16T10:01:00+00:00"},
        {"cleanup_start_by":"2026-10-16T09:20:00+00:00","closeout_by":"2026-10-16T10:00:00+00:00"})
    projection = evidence.public_projection({"source_sha":"a"*40,"release_digest":"b"*64,
        "session":"p8-1016x","token":"NEVER-PUBLIC","tenant_id":"PRIVATE","cost_actual":None},result)
    text = json.dumps(projection)
    assert "NEVER-PUBLIC" not in text and "PRIVATE" not in text
    assert projection["cost_actual"] is None and projection["source_sha"] == "a"*40


def test_public_projection_validates_values_not_just_key_names(evidence):
    projection=evidence.public_projection({"session":"PRIVATE COOKIE","source_sha":"PRIVATE TOKEN"},
        {"P8-1":{"status":"PRIVATE","code":"PRIVATE","correct":"PRIVATE"}})
    assert "PRIVATE" not in json.dumps(projection)


def test_canonical_absence_requires_every_exact_group(evidence):
    rows=[{"id":"group-1","status":404},{"id":"group-2","status":404}]
    evidence.require_group_absence(rows,["group-1","group-2"])
    for bad in (rows[:1],rows+[rows[0]],[rows[0],dict(rows[1],status=403)]):
        with pytest.raises(Exception): evidence.require_group_absence(bad,["group-1","group-2"])


def test_empty_state_requires_successful_original_command(evidence):
    command={"pid":123,"args":["terraform","state","list"],"cwd":"exact"}
    result={"pid":123,"returncode":0,"stdout":"","stderr":""}
    evidence.require_empty_state(command,result,"exact")
    for bad in (dict(result,returncode=1),dict(result,stdout="owned.resource"),dict(result,pid=456)):
        with pytest.raises(Exception): evidence.require_empty_state(command,bad,"exact")


def test_approved_check_from_an_old_plan_is_not_current_approval(evidence):
    check={"id":7,"resource":{"id":"2","type":"environment"},"type_id":"approval-type"}
    suites=[{"context":{"PlanId":"current"},"checkRuns":[{"id":"run","status":"approved",
        "checkConfigurationRef":{"id":7,"resource":check["resource"],"type":{"id":"approval-type"}}}]}]
    evidence.require_evaluations(suites,[check],"current")
    with pytest.raises(Exception): evidence.require_evaluations(suites,[check],"different-plan")
