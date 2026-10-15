"""Exact saved plans cannot be replayed, retargeted or applied to changed state."""
from dataclasses import replace
import importlib
import json
from types import SimpleNamespace

import pytest


@pytest.fixture
def plans(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "eval"))
    return importlib.import_module("scripts.pipelines.plans")


@pytest.fixture
def files(tmp_path):
    result = {}
    for key in ("plan", "inputs", "locks"):
        path = tmp_path / key
        path.write_bytes(b"synthetic-" + key.encode())
        result[key] = path
    result["bundle"] = SimpleNamespace(digest="b"*64, manifest=SimpleNamespace(source_sha="a"*40))
    result["state"] = {"version":4,"lineage":"00000000-0000-0000-0000-000000000001","serial":2,"resources":[]}
    return result


@pytest.mark.parametrize("change", ["plan","locks","inputs","target","source","digest","lineage","serial","state_content"])
def test_target_state_and_artifact_binding(plans, files, change):
    f = files
    binding = plans.bind_plan(f["plan"], f["bundle"], "test", f["state"], f["inputs"], f["locks"])
    target = "test"
    if change in ("plan","locks","inputs"): f[change].write_bytes(b"changed")
    if change == "target": target = "demo-prod"
    if change == "source": f["bundle"].manifest.source_sha = "e"*40
    if change == "digest": f["bundle"].digest = "f"*64
    if change == "lineage": f["state"]["lineage"] = "00000000-0000-0000-0000-000000000002"
    if change == "serial": f["state"]["serial"] = 3
    if change == "state_content": f["state"]["resources"] = [{"type":"unexpected"}]
    with pytest.raises(Exception, match="plan"):
        plans.verify_plan(binding,f["plan"],f["bundle"],target,f["state"],f["inputs"],f["locks"])


def test_initial_absence_cannot_be_replaced(plans, files):
    f = files
    binding = plans.bind_plan(f["plan"],f["bundle"],"test",None,f["inputs"],f["locks"])
    assert binding.state_lineage is None and binding.state_serial == 0
    with pytest.raises(Exception, match="plan"):
        plans.verify_plan(binding,f["plan"],f["bundle"],"test",f["state"],f["inputs"],f["locks"])


def test_apply_is_exact_once(plans,files,tmp_path):
    f = files
    binding = plans.bind_plan(f["plan"],f["bundle"],"test",f["state"],f["inputs"],f["locks"])
    calls = []
    def runner(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0,stdout=json.dumps(f["state"]) if "pull" in args else "private output",stderr="")
    reservation = tmp_path / "intent.json"
    plans.apply_exact(binding,f["plan"],f["bundle"],"test",f["inputs"],f["locks"],
                      reservation=reservation,cwd=tmp_path,env={},runner=runner)
    assert calls == [["terraform","state","pull"],["terraform","apply","-input=false",str(f["plan"].resolve())]]
    original=json.loads(reservation.with_suffix(".result.json").read_bytes())
    assert original["binding"]==plans.asdict(binding)
    calls.clear()
    with pytest.raises(Exception):
        plans.apply_exact(binding,f["plan"],f["bundle"],"test",f["inputs"],f["locks"],
                          reservation=reservation,cwd=tmp_path,env={},runner=runner)
    assert not calls


def test_stale_state_never_invokes_apply(plans,files,tmp_path):
    f = files
    binding = plans.bind_plan(f["plan"],f["bundle"],"test",f["state"],f["inputs"],f["locks"])
    calls = []
    def runner(args,**kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0,stdout=json.dumps(f["state"] | {"serial":3}),stderr="")
    with pytest.raises(Exception, match="plan"):
        plans.apply_exact(binding,f["plan"],f["bundle"],"test",f["inputs"],f["locks"],
                          reservation=tmp_path/"intent",cwd=tmp_path,env={},runner=runner)
    assert calls == [["terraform","state","pull"]]
