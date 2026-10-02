"""`python -m app.retrieval`: the runbook's commands, as thin wrappers.

No network: the Anthropic client is a fake, the arms are replaced by recorders, the tokens are a
stub and truststore is not injected into the test process, so nothing here reaches Claude or
Azure or spends anything.
"""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from app.retrieval import __main__ as cli
from app.retrieval.arms import ArmResult, Hit, QueryVector
from app.retrieval.questions import MODEL, PROMPT, REWRITE, SEED, Question, freeze, load_frozen

_PLANNER = "the planner dereferenced a null step when the goal was empty; guard added"
_GOOD = json.dumps({"question": "Which change stopped the orchestrator crashing when it was asked to do nothing?",
                    "why_unique": "only this artefact says so"})


@pytest.fixture(autouse=True)
def no_truststore(monkeypatch):
    injected = []
    monkeypatch.setattr(cli.truststore, "inject_into_ssl", lambda: injected.append(True))
    return injected


class _Messages:
    def __init__(self, replies: int, *, fail_after: int | None = None) -> None:
        self.replies, self.fail_after, self.created = replies, fail_after, []

    def create(self, **kwargs):
        if self.fail_after is not None and len(self.created) == self.fail_after:
            raise ConnectionError("connection dropped (not real)")
        self.created.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=_GOOD)],
                               usage=SimpleNamespace(input_tokens=1_000, output_tokens=40))


def _corpus_line(chunk_id: int, artefact: str) -> str:
    entity_type, key = artefact.split(":", 1)
    return json.dumps({"chunkId": chunk_id, "artefact": artefact, "entityType": entity_type,
                       "entityKey": key, "chunkIndex": 0, "tokenCount": 100,
                       "content": f"[{entity_type} {key}] {_PLANNER}"})


def _export(data, artefacts: list[str]) -> None:
    data.mkdir(parents=True, exist_ok=True)
    (data / "chunks.jsonl").write_text(
        "".join(_corpus_line(n, artefact) + "\n" for n, artefact in enumerate(artefacts, start=1)),
        encoding="utf-8")
    (data / "links.jsonl").write_text(
        json.dumps({"pullRequest": "pull_request:1", "commit": "commit:c001"}) + "\n", encoding="utf-8")


def _sample_corpus() -> list[str]:
    return ([f"commit:c{n:03d}" for n in range(100)] + [f"issue:{n}" for n in range(120)]
            + [f"pull_request:{n}" for n in range(90)] + [f"release:dotnet-1.{n}.0" for n in range(10)])


def test_main_injects_truststore_before_anything_else(no_truststore, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "REPORTS", tmp_path)

    assert cli.main(["report", "missing"]) == 1
    assert no_truststore == [True]
    assert "missing" in capsys.readouterr().err


def test_the_questions_are_written_resumed_spot_checked_and_frozen(tmp_path, monkeypatch, capsys):
    data, questions_path = tmp_path / "data", tmp_path / "retrieval" / "questions.jsonl"
    _export(data, _sample_corpus())
    paths = ["--data", str(data)]

    failing = SimpleNamespace(messages=_Messages(300, fail_after=100))
    monkeypatch.setattr(cli, "_writer", lambda: failing)
    with pytest.raises(ConnectionError):
        cli.main(["write-questions", *paths])
    checkpoint = data / "questions.checkpoint.jsonl"
    assert len(checkpoint.read_text(encoding="utf-8").splitlines()) == 100

    resumed = SimpleNamespace(messages=_Messages(300))
    monkeypatch.setattr(cli, "_writer", lambda: resumed)
    assert cli.main(["write-questions", *paths]) == 0
    # The 100 already written are not paid for again.
    assert len(resumed.messages.created) == 200

    assert cli.main(["spot-check", "--round", "0", *paths]) == 0
    sheet_path = data / "spot-check-round-0.json"
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    assert sheet["round"] == 0 and sheet["seed"] == SEED
    assert len(sheet["rows"]) == 30
    assert all(row["why_unique"] == "only this artefact says so" and row["mark"] == "" for row in sheet["rows"])
    # A sheet the owner may already be marking is never overwritten.
    assert cli.main(["spot-check", "--round", "0", *paths]) == 1

    for row in sheet["rows"][:4]:
        row["mark"] = "wrong"
    for row in sheet["rows"][4:]:
        row["mark"] = "fine"
    sheet_path.write_text(json.dumps(sheet), encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["freeze", "--sheet", str(sheet_path), "--questions", str(questions_path), *paths]) == 1
    assert "4 of 30" in capsys.readouterr().err
    assert not questions_path.exists()

    sheet["rows"][0]["mark"] = "ambiguous"
    sheet["rows"][1]["mark"] = "fine"
    sheet_path.write_text(json.dumps(sheet), encoding="utf-8")
    assert cli.main(["freeze", "--sheet", str(sheet_path), "--questions", str(questions_path), *paths]) == 0

    frozen = load_frozen(questions_path)
    manifest = json.loads(questions_path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    assert len(frozen) == 300 and frozen[0].qid == "q001"
    assert (manifest["seed"], manifest["model"]) == (SEED, MODEL)
    assert (manifest["prompt"], manifest["rewrite"]) == (PROMPT, REWRITE)
    assert manifest["thinking"] == "disabled"
    assert manifest["rejections"] == {} and (manifest["rewrites"], manifest["replacements"]) == (0, 0)
    assert manifest["generated_on"] and manifest["frozen_on"]
    assert manifest["why_unique"] == {question.qid: "only this artefact says so" for question in frozen}
    [check] = manifest["spot_check"]
    assert (check["round"], check["seed"], check["fine"], check["not_fine"], check["passes"]) == (0, SEED, 27, 3, True)
    assert check["marks"][sheet["rows"][0]["qid"]] == "ambiguous"
    assert manifest["quotas"] == {"commit": 94, "issue": 113, "pull_request": 84, "release": 9}


# --- run-arms ------------------------------------------------------------------------------


class _Tokens:
    made: list[str] = []

    def __init__(self, scope: str, tenant_id: str) -> None:
        _Tokens.made.append(scope)
        self.scope = scope

    def token(self) -> str:
        return "fake-token"


def _hits(question: Question) -> list[Hit]:
    return [Hit(1, question.target, 0.9), Hit(2, "issue:999", 0.5)]


def _measurement(tmp_path, monkeypatch, *, fail: str | None = None):
    data, reports = tmp_path / "data", tmp_path / "reports"
    _export(data, ["commit:c001", "issue:7", "pull_request:1", "release:dotnet-1.0.0"])
    for deployment in ("releaselens-embed-small", "releaselens-embed-large"):
        np.save(data / f"{deployment}.npy", np.ones((4, 3), dtype=np.float32))
        (data / f"{deployment}.ids.json").write_text("[1, 2, 3, 4]", encoding="utf-8")
    questions = [Question("q001", "Which change fixed the planner?", "commit:c001", "commit"),
                 Question("q002", "Which issue reported the crash?", "issue:7", "issue"),
                 Question("q003", "Which pull request added the guard?", "pull_request:1", "pull_request")]
    questions_path = tmp_path / "questions.jsonl"
    freeze(questions, {"seed": SEED}, questions_path)

    worker = {}
    for mode, arm in (("hybrid", "S1"), ("bge", "E1")):
        for suffix in ("", "-repeat"):
            path = data / f"worker-{mode}{suffix}.jsonl"
            path.write_text("".join(json.dumps({
                "qid": q.qid, "arm": arm, "ms": 12.5, "error": None,
                "hits": [{"chunkId": h.chunk_id, "artefact": h.artefact, "score": h.score} for h in _hits(q)],
            }) + "\n" for q in questions), encoding="utf-8")
            worker[f"--worker-{mode}{suffix}"] = str(path)

    calls = []

    def embedding_arm(arm, questions, *, vectors_path, ids_path, artefacts, embed, query_vectors=None,
                      tokens=None):
        calls.append(("embed", arm, [q.qid for q in questions], query_vectors, tokens, vectors_path.name))
        if arm == fail:
            raise RuntimeError("az: token request failed (not real)")
        results = []
        for question in questions:
            if query_vectors is not None:
                query_vectors[question.qid] = QueryVector(np.ones(3, dtype=np.float32), 30.0, 9)
            results.append(ArmResult(question.qid, arm, _hits(question), 40.0, None, 9))
        return results

    def search_arm(arm, questions, *, query_vectors, endpoint, tokens, client=None):
        calls.append(("search", arm, [q.qid for q in questions], query_vectors, tokens, endpoint))
        return [ArmResult(q.qid, arm, _hits(q), 60.0, None, query_vectors[q.qid].tokens) for q in questions]

    monkeypatch.setattr(cli, "run_embedding_arm", embedding_arm)
    monkeypatch.setattr(cli, "run_search_arm", search_arm)
    monkeypatch.setattr(cli, "TokenSource", _Tokens)
    monkeypatch.setattr(cli, "REPORTS", reports)
    _Tokens.made = []

    argv = ["run-arms", "--base-url", "https://example-account.openai.azure.com/openai/v1/",
            "--tenant", "tenant-not-real", "--endpoint", "https://search-not-real.search.windows.net",
            "--data", str(data), "--questions", str(questions_path), "--repeat-first", "2",
            "--run-id", "test-run", *[item for pair in worker.items() for item in pair]]
    return argv, calls, reports / "retrieval-test-run.json"


def test_run_arms_runs_e2_first_and_hands_its_own_vectors_to_s2_and_s3(tmp_path, monkeypatch, capsys):
    argv, calls, saved = _measurement(tmp_path, monkeypatch)

    assert cli.main(argv) == 0

    assert [(kind, arm, len(qids)) for kind, arm, qids, *_ in calls] == [
        ("embed", "E2", 3), ("embed", "E3", 3), ("search", "S2", 3), ("search", "S3", 3),
        ("embed", "E2", 2), ("embed", "E3", 2), ("search", "S2", 2), ("search", "S3", 2),
    ]
    e2_vectors = calls[0][3]
    assert set(e2_vectors) == {"q001", "q002", "q003"}
    # S2 and S3 search with E2's first-pass vectors, on both passes, so the repeat embeds nothing new.
    assert all(call[3] is e2_vectors for call in calls if call[0] == "search")
    # E3 is never handed a dict, and E2's repeat is not handed the first pass's, which it would overwrite.
    assert calls[1][3] is None and calls[5][3] is None
    assert calls[4][3] is not e2_vectors
    assert calls[0][5] == "releaselens-embed-small.npy" and calls[1][5] == "releaselens-embed-large.npy"
    assert all(call[4] is not None for call in calls)
    assert sorted(set(_Tokens.made)) == ["https://ai.azure.com/.default", "https://search.azure.com/.default"]

    run = json.loads(saved.read_text(encoding="utf-8"))
    assert run["arm_failure"] is None
    assert run["questions"]["count"] == 3
    assert {row["arm"] for row in run["results"]} == {"E1", "E2", "E3", "S1", "S2", "S3"}
    # The Worker's repeat covers the whole file; only the first 2 are compared.
    assert sorted((row["arm"], row["qid"]) for row in run["repeat"] if row["arm"] in ("E1", "S1")) == [
        ("E1", "q001"), ("E1", "q002"), ("S1", "q001"), ("S1", "q002")]
    assert run["analysis"]["arms"]["S3"]["top1"] == 1.0

    capsys.readouterr()
    assert cli.main(["report", "test-run"]) == 0
    assert "# Retrieval benchmark" in capsys.readouterr().out


def test_a_token_failure_is_an_arm_failure_not_300_question_errors(tmp_path, monkeypatch, capsys):
    argv, calls, saved = _measurement(tmp_path, monkeypatch, fail="E3")

    assert cli.main(argv) == 1

    # Nothing after the failed arm is run, so no ranker request is spent on a run that cannot publish.
    assert [(kind, arm) for kind, arm, *_ in calls] == [("embed", "E2"), ("embed", "E3")]
    run = json.loads(saved.read_text(encoding="utf-8"))
    assert run["arm_failure"] == {"arm": "E3", "pass": "first",
                                  "error": "RuntimeError: az: token request failed (not real)"}
    assert not [row for row in run["results"] if row["arm"] == "E3"]
    assert run["analysis"] is None
    assert "E3" in capsys.readouterr().err

    assert cli.main(["report", "test-run"]) == 1
    assert "E3" in capsys.readouterr().err


def test_run_arms_refuses_worker_output_for_another_question_file_before_paying(tmp_path, monkeypatch, capsys):
    argv, calls, saved = _measurement(tmp_path, monkeypatch)
    repeat = argv[argv.index("--worker-bge-repeat") + 1]
    lines = open(repeat, encoding="utf-8").read().splitlines()
    with open(repeat, "w", encoding="utf-8") as rewritten:
        rewritten.write("\n".join(lines[:2]) + "\n")

    assert cli.main(argv) == 1

    assert calls == []
    assert not saved.exists()
    assert "worker-bge-repeat.jsonl" in capsys.readouterr().err
