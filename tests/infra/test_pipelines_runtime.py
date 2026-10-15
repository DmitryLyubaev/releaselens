"""Behavior at the release boundary; external mutations are recorded fakes."""
import importlib
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def run(repo_root,monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root/"eval"))
    return importlib.import_module("scripts.pipelines.run")


def test_prod_does_not_apply_when_original_gate_refuses(run):
    calls = []
    runtime = object.__new__(run.Runtime)
    runtime.stage = "prod"
    def refuse(stage):
        calls.append(("gate",stage))
        raise run.DeliveryRefused("gate")
    runtime.prior_gate = refuse
    runtime.apply_and_load = lambda target: calls.append(("apply",target))
    with pytest.raises(run.DeliveryRefused): runtime.execute()
    assert calls == [("gate","test")]


def test_verify_cannot_reach_apply_or_load(run):
    calls = []
    runtime = object.__new__(run.Runtime)
    runtime.stage = "verify"
    runtime.proof = {"source_sha":"a"*40}
    runtime.bundle = SimpleNamespace(digest="b"*64)
    runtime.prior_gate = lambda stage: calls.append(("prior",stage))
    runtime.store = SimpleNamespace(json=lambda *args:{"source_sha":"a"*40,"release_digest":"b"*64,
        "expected_negative":True,"acceptance_pass":True})
    runtime.apply_and_load = lambda target: pytest.fail("verify mutated index")
    runtime.plan = lambda target: pytest.fail("verify planned mutation")
    runtime.measure = lambda target,**kw: calls.append(("measure",target,kw)) or True
    assert runtime.execute() is True
    assert calls == [("prior","prod"),("measure","demo-prod",{"negative":False})]


def test_index_workspace_copies_only_reviewed_configuration(run,tmp_path,monkeypatch):
    root = tmp_path/"repo"
    source = root/"infra/pipelines/index"
    source.mkdir(parents=True)
    for filename in ("main.tf","variables.tf","versions.tf","outputs.tf","backend.tf",".terraform.lock.hcl"):
        (source/filename).write_text("reviewed")
    (source/"surprise.tf").write_text("unreviewed")
    runtime = object.__new__(run.Runtime)
    runtime.work = tmp_path/"work"
    runtime.work.mkdir()
    runtime.a = {"storage_account_name":"owned"}
    runtime.command = lambda *args: None
    monkeypatch.setattr(run,"ROOT",root)
    directory = runtime.terraform("test")
    assert {p.name for p in directory.iterdir()} == {
        "main.tf","variables.tf","versions.tf","outputs.tf","backend.tf",".terraform.lock.hcl"}


def test_stage_budget_stops_before_another_request_or_process(run):
    now = datetime.now(timezone.utc)
    ticks = [100.0]
    budget = run.StageBudget("test",now+timedelta(hours=1),clock=lambda:ticks[0])
    assert budget.remaining(120) == 120
    ticks[0] += 721
    with pytest.raises(run.DeliveryRefused,match="deadline"): budget.remaining(120)
    with pytest.raises(run.DeliveryRefused,match="deadline"): budget.before_request(None)
