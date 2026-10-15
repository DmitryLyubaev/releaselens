from dataclasses import replace
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import httpx
import numpy as np
import pytest

from app.delivery_search.contracts import (DeliveryRefused, ReleaseManifest, VerifiedBundle, SearchTarget)
from app.delivery_search.gate import grade_rows, run_gate, write_gate
from app.retrieval.arms import ArmResult, Hit
from .conftest import questions_at


@pytest.fixture
def gate_bundle(tmp_path):
    rows = questions_at(tmp_path)
    (tmp_path / "links.jsonl").write_text("")
    np.save(tmp_path / "queries.npy", np.ones((229, 1536), dtype="float32"))
    configs = {"s2.json": {"top": 50, "k": 50, "filter": None}, "negative.json": {
        "top": 50, "k": 50, "filter": "artefact eq '__p8_no_match__'"}}
    for name, data in configs.items(): (tmp_path / name).write_text(json.dumps(data))
    manifest = ReleaseManifest(1, "a"*40, (), "b"*64, "text-embedding-3-small", "1", 1536,
                               229, 41825, 120, "c"*64)
    return VerifiedBundle(tmp_path, manifest, "d"*64)


def results(correct=120):
    return [ArmResult(f"q{i:03}", "S2", [Hit(i, f"a{i}" if i < correct else "wrong", 1.0)],
                      1.0, None) for i in range(229)]


@pytest.mark.parametrize("correct,passed", [(120, True), (119, False), (229, True), (0, False)])
def test_exact_s2_and_floor(gate_bundle, correct, passed):
    result = grade_rows(gate_bundle, results(correct))
    assert (result.expected, result.evaluated, result.correct, result.passed) == (229, 229, correct, passed)


@pytest.mark.parametrize("case", ["missing", "error", "skipped", "duplicate", "unknown"])
def test_missing_error_skipped_never_pass(gate_bundle, tmp_path, case):
    rows = results(229)
    if case == "missing": rows.pop()
    if case == "error": rows[0] = replace(rows[0], error="request")
    if case == "skipped": rows[0] = replace(rows[0], error="skipped")
    if case == "duplicate": rows[0] = rows[1]
    if case == "unknown": rows[0] = replace(rows[0], qid="unknown")
    result = grade_rows(gate_bundle, rows)
    assert not result.passed
    write_gate(result, rows, tmp_path / "result")
    suite = ET.parse(tmp_path / "result/results.xml").getroot()
    assert int(suite.attrib["tests"]) == 230 and int(suite.attrib["failures"]) >= 1
    assert len(suite.findall("testcase")) == 230


def test_junit_wrong_answers_are_aggregate_properties(gate_bundle, tmp_path):
    result = grade_rows(gate_bundle, results())
    write_gate(result, results(), tmp_path / "out")
    suite = ET.parse(tmp_path / "out/results.xml").getroot()
    assert suite.attrib["failures"] == "0"
    assert len(suite.findall("testcase")) == 230
    assert suite.find("testcase[@name='q228']/properties/property[@name='top1']").attrib["value"] == "0"


class Tokens:
    def token(self): return "synthetic-token"


def test_negative_has_no_matches(gate_bundle):
    queries = []
    def server(request):
        if request.method == "GET": return httpx.Response(200, text="41825")
        body = json.loads(request.content)
        queries.append(body)
        assert body["filter"] == "artefact eq '__p8_no_match__'"
        assert "queryType" not in body and body["vectorQueries"][0]["k"] == 50
        return httpx.Response(200, json={"value": []})
    target = SearchTarget("https://owned.search.windows.net", "p8-test", "owned.search.windows.net")
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        result, rows = run_gate(gate_bundle, target, json.loads((gate_bundle.root / "negative.json").read_text()), Tokens(), client)
    assert len(queries) == 229 and result.correct == 0 and result.evaluated == 229
    assert not result.passed and all(row.error is None for row in rows)


def test_negative_refuses_prod(gate_bundle):
    target = SearchTarget("https://owned.search.windows.net", "p8-demo-prod", "owned.search.windows.net")
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("no request"))) as client:
        with pytest.raises(DeliveryRefused):
            run_gate(gate_bundle, target, json.loads((gate_bundle.root / "negative.json").read_text()), Tokens(), client)


def test_safe_errors(gate_bundle):
    target = SearchTarget("https://owned.search.windows.net", "p8-test", "owned.search.windows.net")
    calls = []
    def server(request):
        if request.method == "GET": return httpx.Response(200, text="41825")
        calls.append(request)
        return httpx.Response(401, text="PRIVATE TOKEN COOKIE ACCOUNT")
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        result, rows = run_gate(gate_bundle, target, {"top":50,"k":50,"filter":None}, Tokens(), client)
    assert not result.passed and result.errors == 229 and len(calls) == 229
    assert set(row.error for row in rows) == {"request"}


def test_run_gate_scores_real_search_replies(gate_bundle):
    def server(request):
        if request.method == "GET": return httpx.Response(200, text="41825")
        body = json.loads(request.content)
        i = int(body["search"].split()[-1])
        return httpx.Response(200, json={"value": [{"chunk_id":str(i),
            "artefact":f"a{i}" if i < 120 else "wrong", "@search.score":1.0}]})
    target = SearchTarget("https://owned.search.windows.net", "p8-test", "owned.search.windows.net")
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        result, rows = run_gate(gate_bundle, target, {"top":50,"k":50,"filter":None}, Tokens(), client)
    assert result.passed and result.correct == 120 and result.errors == 0 and len(rows) == 229


def test_gate_never_queries_before_expected_count(gate_bundle, monkeypatch):
    from app.delivery_search import client as module
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    requests = []
    def server(request):
        requests.append(request)
        return httpx.Response(200, text="41824")
    target = SearchTarget("https://owned.search.windows.net", "p8-test", "owned.search.windows.net")
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(DeliveryRefused):
            run_gate(gate_bundle,target,{"top":50,"k":50,"filter":None},Tokens(),client)
    assert len(requests) == 24 and all(r.method == "GET" for r in requests)


def test_cli_repeated_bad_authority_never_exposes_private_error(tmp_path, capsys):
    from app.delivery_search.__main__ import main
    authority = tmp_path / "authority.json"
    authority.write_text('{"PRIVATE TOKEN COOKIE ACCOUNT":')
    args = ["validate","--authority",str(authority),"--bundle","absent", "--digest","a"*64,
            "--target","test","--output",str(tmp_path / "out")]
    assert main(args) == 2 and main(args) == 2
    public = capsys.readouterr()
    assert public.out == "status=refused code=internal\n"*2 and public.err == ""
