"""`python -m app.retrieval`: the runbook's commands, as thin wrappers.

No network: the Anthropic client is a fake, the arms are replaced by recorders, the tokens are a
stub and truststore is not injected into the test process, so nothing here reaches Claude or
Azure or spends anything.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from app.retrieval import __main__ as cli
from app.retrieval import arms, search_index
from app.retrieval.arms import ArmResult, Hit, QueryVector
from app.retrieval.questions import EXCLUDED, MODEL, PROMPT, REWRITE, SEED, Question, freeze, load_frozen

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
    assert cli.main(["write-questions", *paths]) == 1
    # Anything but a refusal keeps its traceback, so a failure is never reduced to one line.
    error = capsys.readouterr().err
    assert "Traceback" in error and "ConnectionError: connection dropped (not real)" in error
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
    # The generation's whole spend: the 100 calls of the failed run and the 200 of the rerun.
    spend = {"calls": 300, "input_tokens": 300_000, "output_tokens": 12_000}
    assert sheet["spend"] == spend
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
    refused = capsys.readouterr().err
    # A refusal is the plain message: it is an answer, not a failure.
    assert "4 of 30" in refused and "Traceback" not in refused
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
    assert manifest["excluded_from_sample"] == EXCLUDED
    assert manifest["thinking"] == "disabled"
    assert manifest["rejections"] == {} and (manifest["rewrites"], manifest["replacements"]) == (0, 0)
    assert manifest["generated_on"] and manifest["frozen_on"]
    assert manifest["why_unique"] == {question.qid: "only this artefact says so" for question in frozen}
    [check] = manifest["spot_check"]
    assert (check["round"], check["seed"], check["fine"], check["not_fine"], check["passes"]) == (0, SEED, 27, 3, True)
    assert check["spend"] == spend
    assert (manifest["calls"], manifest["input_tokens"], manifest["output_tokens"]) == (300, 300_000, 12_000)
    assert check["marks"][sheet["rows"][0]["qid"]] == "ambiguous"
    assert manifest["quotas"] == {"commit": 94, "issue": 113, "pull_request": 84, "release": 9}


def _mark(path, *, wrong: int) -> None:
    sheet = json.loads(path.read_text(encoding="utf-8"))
    for index, row in enumerate(sheet["rows"]):
        row["mark"] = "wrong" if index < wrong else "fine"
    path.write_text(json.dumps(sheet), encoding="utf-8")


def test_a_regenerated_set_carries_every_generations_spend(tmp_path, monkeypatch):
    data, questions_path = tmp_path / "data", tmp_path / "retrieval" / "questions.jsonl"
    _export(data, _sample_corpus())
    paths = ["--data", str(data)]
    monkeypatch.setattr(cli, "_writer", lambda: SimpleNamespace(messages=_Messages(300)))

    assert cli.main(["write-questions", *paths]) == 0
    assert cli.main(["spot-check", "--round", "0", *paths]) == 0
    _mark(data / "spot-check-round-0.json", wrong=5)

    # Regenerated, as the README says: the first generation's files are moved aside and kept.
    first = tmp_path / "generation-0"
    first.mkdir()
    for name in ("questions.checkpoint.jsonl", "questions.spend.jsonl"):
        (data / name).rename(first / name)
    assert cli.main(["write-questions", *paths]) == 0
    assert cli.main(["spot-check", "--round", "1", *paths]) == 0
    _mark(data / "spot-check-round-1.json", wrong=1)

    assert cli.main(["freeze", "--sheet", str(data / "spot-check-round-0.json"),
                     "--sheet", str(data / "spot-check-round-1.json"),
                     "--questions", str(questions_path), *paths]) == 0

    manifest = json.loads(questions_path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    generation = {"calls": 300, "input_tokens": 300_000, "output_tokens": 12_000}
    assert [(c["round"], c["passes"], c["spend"]) for c in manifest["spot_check"]] == [
        (0, False, generation), (1, True, generation)]
    assert manifest["spend_all_rounds"] == {"calls": 600, "input_tokens": 600_000, "output_tokens": 24_000}


def test_freeze_refuses_a_set_of_rounds_that_leaves_one_out(tmp_path, monkeypatch, capsys):
    # A round left out would leave its generation's spend out of spend_all_rounds.
    data, questions_path = tmp_path / "data", tmp_path / "retrieval" / "questions.jsonl"
    _export(data, _sample_corpus())
    paths = ["--data", str(data), "--questions", str(questions_path)]
    monkeypatch.setattr(cli, "_writer", lambda: SimpleNamespace(messages=_Messages(300)))
    assert cli.main(["write-questions", "--data", str(data)]) == 0
    for number in range(3):
        assert cli.main(["spot-check", "--round", str(number), "--data", str(data)]) == 0
        _mark(data / f"spot-check-round-{number}.json", wrong=0)
    sheet = [str(data / f"spot-check-round-{number}.json") for number in range(3)]
    elsewhere = tmp_path / "elsewhere" / "round-0.json"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes((data / "spot-check-round-0.json").read_bytes())
    capsys.readouterr()

    for given in ([sheet[1]], [sheet[0], sheet[2]], [sheet[1], sheet[0]]):
        assert cli.main(["freeze", *[item for path in given for item in ("--sheet", path)], *paths]) == 1
        assert "give every round from 0, oldest first" in capsys.readouterr().err
    # Round 0 given from another file, so the one in --data was not given.
    assert cli.main(["freeze", "--sheet", str(elsewhere), "--sheet", sheet[1], *paths]) == 1
    error = capsys.readouterr().err
    assert "spot-check-round-0.json" in error and "was not given" in error
    assert not questions_path.exists()

    assert cli.main(["freeze", *[item for path in sheet for item in ("--sheet", path)], *paths]) == 0


# --- run-arms ------------------------------------------------------------------------------


class _Tokens:
    made: list[str] = []
    fetched: list[str] = []
    failing: str | None = None

    def __init__(self, scope: str, tenant_id: str) -> None:
        _Tokens.made.append(scope)
        self.scope = scope

    def token(self) -> str:
        _Tokens.fetched.append(self.scope)
        if self.scope == _Tokens.failing:
            raise RuntimeError("az: please run az login (not real)")
        return "fake-token"


# The measurement's corpus: chunk n is the nth artefact, as _export numbers them.
_CORPUS = ["commit:c001", "issue:7", "pull_request:1", "release:dotnet-1.0.0"]


def _hits(question: Question) -> list[Hit]:
    # Hits on the corpus's own chunks, with its artefacts, as a Worker run on the same database gives.
    return [Hit(_CORPUS.index(question.target) + 1, question.target, 0.9), Hit(4, _CORPUS[3], 0.5)]


def _progress(data, deployment: str, done: dict[str, int], *, chunks: int = 4, batch_size: int = 64) -> None:
    (data / f"{deployment}.progress.json").write_text(json.dumps(
        {"deployment": deployment, "batch_size": batch_size, "chunks": chunks, "dimensions": 3, "done": done}),
        encoding="utf-8")


def _measurement(tmp_path, monkeypatch, *, fail: str | None = None, indexed: int = 4):
    data, reports = tmp_path / "data", tmp_path / "reports"
    _export(data, _CORPUS)
    for deployment, tokens in (("releaselens-embed-small", 1_234_567), ("releaselens-embed-large", 1_234_000)):
        np.save(data / f"{deployment}.npy", np.ones((4, 3), dtype=np.float32))
        (data / f"{deployment}.ids.json").write_text("[1, 2, 3, 4]", encoding="utf-8")
        _progress(data, deployment, {"0": tokens})
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

    def document_count(endpoint, tokens, client=None):
        calls.append(("count", None, [], None, tokens, endpoint))
        # The tokens are fetched first, so a sign-in failure is found before the index is asked.
        assert len(set(_Tokens.fetched)) == 2
        return indexed

    monkeypatch.setattr(cli, "run_embedding_arm", embedding_arm)
    monkeypatch.setattr(cli, "run_search_arm", search_arm)
    monkeypatch.setattr(cli, "document_count", document_count)
    monkeypatch.setattr(cli, "TokenSource", _Tokens)
    monkeypatch.setattr(cli, "REPORTS", reports)
    _Tokens.made, _Tokens.fetched, _Tokens.failing = [], [], None

    argv = ["run-arms", "--base-url", "https://example-account.openai.azure.com/openai/v1/",
            "--tenant", "tenant-not-real", "--endpoint", "https://search-not-real.search.windows.net",
            "--data", str(data), "--questions", str(questions_path), "--repeat-first", "2",
            "--run-id", "test-run", *[item for pair in worker.items() for item in pair]]
    return argv, calls, reports / "retrieval-test-run.json"


def test_run_arms_runs_e2_first_and_hands_its_own_vectors_to_s2_and_s3(tmp_path, monkeypatch, capsys):
    argv, calls, saved = _measurement(tmp_path, monkeypatch)

    assert cli.main(argv) == 0

    # The index is counted before any arm runs, at the endpoint the arms search.
    count = calls.pop(0)
    assert (count[0], count[5]) == ("count", "https://search-not-real.search.windows.net")
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
    # What embedding the corpus was billed, read from each deployment's progress file.
    assert run["corpus_tokens"] == {
        "text-embedding-3-small": {"deployment": "releaselens-embed-small", "tokens": 1_234_567},
        "text-embedding-3-large": {"deployment": "releaselens-embed-large", "tokens": 1_234_000},
    }

    capsys.readouterr()
    assert cli.main(["report", "test-run"]) == 0
    page = capsys.readouterr().out
    assert "# Retrieval benchmark" in page
    assert "1,234,567" in page and "one-time" in page


def test_a_token_failure_is_an_arm_failure_not_300_question_errors(tmp_path, monkeypatch, capsys):
    argv, calls, saved = _measurement(tmp_path, monkeypatch, fail="E3")

    assert cli.main(argv) == 1

    # Nothing after the failed arm is run, so no ranker request is spent on a run that cannot publish.
    assert [(kind, arm) for kind, arm, *_ in calls] == [("count", None), ("embed", "E2"), ("embed", "E3")]
    run = json.loads(saved.read_text(encoding="utf-8"))
    assert run["arm_failure"] == {"arm": "E3", "pass": "first",
                                  "error": "RuntimeError: az: token request failed (not real)"}
    assert not [row for row in run["results"] if row["arm"] == "E3"]
    assert run["analysis"] is None
    error = capsys.readouterr().err
    assert "E3 failed on its first pass" in error and "Traceback" in error

    assert cli.main(["report", "test-run"]) == 1
    assert "E3" in capsys.readouterr().err


@pytest.mark.parametrize("scope", ["https://ai.azure.com/.default", "https://search.azure.com/.default"])
def test_a_sign_in_failure_is_found_before_anything_is_paid_for(tmp_path, monkeypatch, capsys, scope):
    argv, calls, saved = _measurement(tmp_path, monkeypatch)
    _Tokens.failing = scope

    assert cli.main(argv) == 1

    assert calls == []
    assert not saved.exists()
    assert "nothing was spent" in capsys.readouterr().err


def test_run_arms_fetches_both_tokens_before_the_first_arm(tmp_path, monkeypatch):
    argv, calls, _ = _measurement(tmp_path, monkeypatch)
    seen_before_first_arm = []
    original = cli.run_embedding_arm

    def first_arm(*args, **kwargs):
        seen_before_first_arm.extend(_Tokens.fetched)
        return original(*args, **kwargs)

    monkeypatch.setattr(cli, "run_embedding_arm", first_arm)

    assert cli.main(argv) == 0
    assert set(seen_before_first_arm[:2]) == {"https://ai.azure.com/.default", "https://search.azure.com/.default"}


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


class _Azure:
    """A fake of everything run-arms calls over HTTP: the embeddings endpoint and a tiny AI
    Search index of the measurement's four chunks. Chunk n's corpus vector is the nth axis, and
    a question's embedding leans towards its target's axis, so every arm should find it first."""

    def __init__(self, questions: list[Question]) -> None:
        self.targets = {question.question: _CORPUS.index(question.target) for question in questions}
        self.requests: list = []

    def vector(self, text: str) -> list[float]:
        vector = [0.1] * 4
        vector[self.targets[text]] = 1.0
        return vector

    def __call__(self, request):
        self.requests.append(request)
        if request.url.path.endswith("/docs/$count"):
            return httpx.Response(200, text=str(len(_CORPUS)))
        body = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={
                "data": [{"index": 0, "embedding": self.vector(body["input"][0])}],
                "usage": {"prompt_tokens": 7, "total_tokens": 7}})
        assert request.url.path.endswith("/docs/search")
        first = self.targets[body["search"]]
        ranked = [first] + [row for row in range(len(_CORPUS)) if row != first]
        return httpx.Response(200, json={"value": [
            {"@search.score": 0.05 - row / 100, "@search.rerankerScore": 3.0 - row,
             "chunk_id": str(chunk + 1), "artefact": _CORPUS[chunk]}
            for row, chunk in enumerate(ranked)]})


def test_run_arms_end_to_end_through_the_real_arms(tmp_path, monkeypatch):
    # The real arms, embedder and search client, wired by the CLI, against a fake service: only
    # the Worker's files, the tokens and the HTTP transport are fakes.
    argv, _, saved = _measurement(tmp_path, monkeypatch)
    data = tmp_path / "data"
    for deployment in ("releaselens-embed-small", "releaselens-embed-large"):
        np.save(data / f"{deployment}.npy", np.eye(4, dtype=np.float32))
    questions = load_frozen(tmp_path / "questions.jsonl")
    azure = _Azure(questions)
    monkeypatch.setattr(cli, "run_embedding_arm", arms.run_embedding_arm)
    monkeypatch.setattr(cli, "run_search_arm", arms.run_search_arm)
    monkeypatch.setattr(cli, "document_count", search_index.document_count)
    monkeypatch.setattr(cli, "new_client", lambda: httpx.Client(transport=httpx.MockTransport(azure)))

    assert cli.main(argv) == 0

    run = json.loads(saved.read_text(encoding="utf-8"))
    assert run["arm_failure"] is None
    assert sorted((row["arm"], row["qid"]) for row in run["results"]) == sorted(
        (arm, question.qid) for arm in ("E1", "E2", "E3", "S1", "S2", "S3") for question in questions)
    assert all(row["error"] is None for row in run["results"] + run["repeat"])
    assert sorted({row["arm"] for row in run["repeat"]}) == ["E1", "E2", "E3", "S1", "S2", "S3"]
    analysis = run["analysis"]
    assert all(analysis["arms"][arm]["top1"] == 1.0 for arm in ("E2", "E3", "S2", "S3"))
    assert [c["n"] for c in analysis["comparisons"]] == [3, 3, 3]
    # S2 and S3 carry E2's embedding tokens; E1 and S1 cost none.
    assert analysis["arms"]["S3"]["query_tokens"] == 7 * 3 and analysis["arms"]["E1"]["query_tokens"] == 0

    paths = [request.url.path for request in azure.requests]
    # One count, E2 and E3 embedding each question on both passes, and S2 and S3 searching.
    assert paths.count("/indexes/releaselens-chunks/docs/$count") == 1
    assert paths.count("/openai/v1/embeddings") == (3 + 2) * 2
    assert paths.count("/indexes/releaselens-chunks/docs/search") == (3 + 2) * 2
    semantic = [json.loads(request.content).get("queryType") for request in azure.requests
                if request.url.path.endswith("/docs/search")]
    assert semantic.count("semantic") == 3 + 2


@pytest.mark.parametrize("bad_hit, named", [
    ({"chunkId": 99, "artefact": "commit:c001", "score": 0.9}, "chunk 99"),
    ({"chunkId": 1, "artefact": "commit:c999", "score": 0.9}, "chunk 1"),
])
def test_run_arms_refuses_worker_hits_from_another_corpus_before_paying(tmp_path, monkeypatch, capsys,
                                                                        bad_hit, named):
    # E1 and S1 searched another database than the one the corpus was exported from: a chunk the
    # export does not hold, or one whose artefact differs. Scored, C1 and C2 would compare arms
    # that did not search the same chunks.
    argv, calls, saved = _measurement(tmp_path, monkeypatch)
    hybrid = Path(argv[argv.index("--worker-hybrid") + 1])
    rows = [json.loads(line) for line in hybrid.read_text(encoding="utf-8").splitlines()]
    rows[1]["hits"].insert(1, bad_hit)
    hybrid.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    assert cli.main(argv) == 1

    assert calls == []
    assert not saved.exists()
    error = capsys.readouterr().err
    assert "worker-hybrid.jsonl" in error and named in error and "RELEASELENS_DB" in error


def test_run_arms_refuses_an_index_that_does_not_hold_the_corpus_before_paying(tmp_path, monkeypatch, capsys):
    # A re-applied stack is a new, empty service; scored, its empty replies would be S2's and
    # S3's misses, and C2 and C3 a confident difference made by the setup.
    argv, calls, saved = _measurement(tmp_path, monkeypatch, indexed=0)

    assert cli.main(argv) == 1

    assert [kind for kind, *_ in calls] == ["count"]
    assert not saved.exists()
    error = capsys.readouterr().err
    assert "holds 0 documents, not 4" in error and "build-index" in error


@pytest.mark.parametrize("progress", ["missing", "unfinished"])
def test_run_arms_refuses_a_corpus_embedding_with_no_finished_record_before_paying(tmp_path, monkeypatch, capsys,
                                                                                    progress):
    argv, calls, saved = _measurement(tmp_path, monkeypatch)
    data = tmp_path / "data"
    if progress == "missing":
        (data / "releaselens-embed-large.progress.json").unlink()
    else:
        # Two batches of two, and only the first recorded.
        _progress(data, "releaselens-embed-large", {"0": 10}, chunks=4, batch_size=2)

    assert cli.main(argv) == 1

    assert calls == []
    assert not saved.exists()
    error = capsys.readouterr().err
    assert "releaselens-embed-large.progress.json" in error and "run embed" in error


# --- build-index ---------------------------------------------------------------------------


class _Index:
    """A fake search service for build-index: it accepts the index and every upload, and counts
    the documents as `counts` says, one reading per request, the last one repeated."""

    def __init__(self, counts: list[int]) -> None:
        self.counts, self.requests = list(counts), []

    def __call__(self, request):
        self.requests.append(request)
        if request.method == "PUT":
            return httpx.Response(201, json={"name": "releaselens-chunks"})
        if request.url.path.endswith("/docs/index"):
            keys = [document["chunk_id"] for document in json.loads(request.content)["value"]]
            return httpx.Response(200, json={"value": [{"key": key, "status": True} for key in keys]})
        count = self.counts.pop(0) if len(self.counts) > 1 else self.counts[0]
        # The service answers $count as plain text, which may begin with a byte-order mark.
        return httpx.Response(200, content=("\ufeff" + str(count)).encode("utf-8"),
                              headers={"Content-Type": "text/plain"})

    def counted(self) -> list:
        return [request for request in self.requests if request.url.path.endswith("/docs/$count")]


def _build_index(tmp_path, monkeypatch, counts: list[int]):
    data = tmp_path / "data"
    _export(data, _CORPUS)
    np.save(data / "releaselens-embed-small.npy", np.ones((4, 1536), dtype=np.float32))
    (data / "releaselens-embed-small.ids.json").write_text("[1, 2, 3, 4]", encoding="utf-8")
    index, slept = _Index(counts), []
    monkeypatch.setattr(cli, "new_client", lambda: httpx.Client(transport=httpx.MockTransport(index)))
    monkeypatch.setattr(cli, "TokenSource", _Tokens)
    monkeypatch.setattr(cli, "_sleep", slept.append)
    _Tokens.made, _Tokens.fetched, _Tokens.failing = [], [], None
    argv = ["build-index", "--endpoint", "https://search-not-real.search.windows.net",
            "--tenant", "tenant-not-real", "--data", str(data)]
    return argv, index, slept


def test_build_index_waits_until_the_index_counts_every_chunk(tmp_path, monkeypatch, capsys):
    argv, index, slept = _build_index(tmp_path, monkeypatch, [0, 3, 4])

    assert cli.main(argv) == 0

    counted = index.counted()
    assert len(counted) == 3 and slept == [5.0, 5.0]
    assert counted[0].method == "GET" and counted[0].headers["Authorization"] == "Bearer fake-token"
    assert counted[0].url.params["api-version"] == "2026-04-01"
    assert "the index holds 4 documents" in capsys.readouterr().out


def test_build_index_fails_when_the_index_never_counts_every_chunk(tmp_path, monkeypatch, capsys):
    argv, index, slept = _build_index(tmp_path, monkeypatch, [3])

    assert cli.main(argv) == 1

    # A bounded wait: 12 readings, 5 seconds apart.
    assert len(index.counted()) == 12 and slept == [5.0] * 11
    assert "holds 3 documents, not 4" in capsys.readouterr().err
