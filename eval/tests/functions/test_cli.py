"""The harness commands: upload, ingest-checks, tool-checks, report.

No network and no real time: every endpoint is one `httpx.MockTransport` router, the token source is
a fake, sleep and the clock are fakes, git is a stub, and truststore is not injected.
"""

import base64
import json
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.functions import __main__ as cli
from app.functions import ingest_checks as ic
from app.retrieval import questions as frozen

TENANT = "00000000-0000-0000-0000-000000000000"
OWNER_OID = "00000000-0000-0000-0000-0000000000a1"
APP_ID = "33333333-3333-3333-3333-333333333333"
GATEWAY_SCOPE = "api://22222222-2222-2222-2222-222222222222/.default"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
BLOB = "https://store.example.com/"
SEARCH = "https://search.example.com"
OPENAI = "https://oai.example.com/openai/v1/"
MCP = "https://gateway.example.com/releaselens-search/mcp"
APP = "https://tool.example.com/runtime/webhooks/mcp"

# One token per scope, so a route can tell which scope a call was made with.
TOKEN_FOR = {GATEWAY_SCOPE: "t-gateway", cli.DIRECT_SCOPE: "t-direct", "https://search.azure.com/.default": "t-search",
             ic.STORAGE_SCOPE: "t-storage", "https://api.applicationinsights.io/.default": "t-insights"}


class _Tokens:
    created: list[tuple[str, str]] = []

    def __init__(self, scope: str, tenant_id: str) -> None:
        _Tokens.created.append((scope, tenant_id))
        self._token = TOKEN_FOR[scope]

    def token(self) -> str:
        return self._token


def _stem(artefact: str) -> str:
    return base64.urlsafe_b64encode(artefact.encode()).rstrip(b"=").decode()


class _Env:
    """The router, the stubs, and what they saw."""

    def __init__(self, tmp_path: Path, clock) -> None:
        self.requests: list[httpx.Request] = []
        self.lock = threading.Lock()
        self.clock = clock
        self.porcelain = ""
        self.ignored = True
        self.out = tmp_path / "out"
        self.files = tmp_path / "artefacts"
        self.files.mkdir()
        self.index: dict[str, list[dict]] = {}
        self.blobs: list[tuple[str, bytes]] = []
        self.on_put = None
        self.doc_count = 1000
        self.poison: list[str] = []
        self.answers: dict[str, list[str]] = {}
        self.gateway_calls = 0
        self.tool_limit = 10_000
        self.metric_rows = [{"Caller": OWNER_OID, "total": 500}]
        self.corpus = tmp_path / "chunks.jsonl"

    def run(self, command, **kwargs):
        if command[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(command, 0, self.porcelain, "")
        if command[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(command, 0, COMMIT + "\n", "")
        return subprocess.CompletedProcess(command, 0 if self.ignored else 1, "", "")

    # -- artefact files and the corpus
    def corpus_of(self, counts: dict[str, int]) -> None:
        lines = []
        for artefact, n in counts.items():
            for i in range(n):
                lines.append(json.dumps({"chunkId": len(lines) + 1, "artefact": artefact,
                                         "entityType": artefact.split(":")[0], "entityKey": artefact.split(":")[1],
                                         "chunkIndex": i, "tokenCount": 5, "content": "text"}))
        self.corpus.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def artefact_file(self, artefact: str) -> Path:
        path = self.files / f"{_stem(artefact)}.json"
        path.write_text(json.dumps({"entityType": artefact.split(":")[0]}), encoding="utf-8")
        return path

    def ingested(self, artefact: str, n: int, text: str = "new text") -> None:
        self.index[artefact] = [{"chunk_id": ic.chunk_key(artefact, i), "content": f"{text} {i}"} for i in range(n)]

    # -- the router
    def handler(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.requests.append(request)
        host, path = request.url.host, request.url.path
        auth = request.headers.get("authorization")
        if host == "store.example.com" and request.method == "PUT":
            name = path.rsplit("/", 1)[1]
            with self.lock:
                self.blobs.append((name, request.content))
            if self.on_put:
                self.on_put(name)
            return httpx.Response(201)
        if host == "store.example.com" and request.method == "GET":
            xml = "".join(f"<QueueMessage><MessageText>{t}</MessageText></QueueMessage>" for t in self.poison)
            return httpx.Response(200, text=f"<QueueMessagesList>{xml}</QueueMessagesList>")
        if host == "search.example.com":
            return self.search(request, path)
        if host == "oai.example.com":
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.5] * 1536}],
                                             "usage": {"prompt_tokens": 3}})
        if host == "gateway.example.com":
            return self.mcp(request, auth, tool=True)
        if host == "tool.example.com":
            return httpx.Response(401, text="Unauthorized")
        if host == "api.applicationinsights.io":
            return httpx.Response(200, json={"tables": [{"columns": [{"name": "Caller"}, {"name": "total"}],
                                                          "rows": [[r["Caller"], r["total"]] for r in self.metric_rows]}]})
        raise AssertionError(f"unexpected request to {host}")

    def search(self, request, path):
        if path.endswith("$count"):
            return httpx.Response(200, text=str(self.doc_count))
        body = json.loads(request.content)
        if "filter" in body:
            artefact = body["filter"].removeprefix("artefact eq '").removesuffix("'").replace("''", "'")
            return httpx.Response(200, json={"value": self.index.get(artefact, [])})
        assert body["top"] == 5 and body["queryType"] == "semantic"
        value = [{"chunk_id": str(i), "artefact": a, "content": "x", "@search.rerankerScore": 2.0}
                 for i, a in enumerate(self.answers[body["search"]])]
        return httpx.Response(200, json={"value": value})

    def mcp(self, request, auth, *, tool):
        if auth != "Bearer t-gateway":
            return httpx.Response(401, text="Unauthorized.")
        message = json.loads(request.content)
        method = message["method"]
        if "id" not in message:
            return httpx.Response(202)
        results = {
            "initialize": {"protocolVersion": "2025-06-18", "capabilities": {}},
            "tools/list": {"tools": [{"name": "search_corpus"}]},
        }
        if method == "tools/call":
            with self.lock:
                self.gateway_calls += 1
                over = self.gateway_calls > self.tool_limit
            if over:
                return httpx.Response(429, headers={"Retry-After": "30"}, text="")
            query = message["params"]["arguments"]["query"]
            hits = [{"artefact": a, "type": a.split(":")[0], "excerpt": "x", "score": 1.0}
                    for a in self.answers.get(query, ["issue:1"])]
            results["tools/call"] = {"content": [{"type": "text", "text": json.dumps(hits)}], "isError": False}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": results[method]})


@pytest.fixture
def env(tmp_path, monkeypatch, clock):
    env = _Env(tmp_path, clock)
    _Tokens.created = []
    monkeypatch.setattr(cli, "TokenSource", _Tokens)
    monkeypatch.setattr(cli, "_run", env.run)
    monkeypatch.setattr(cli, "_now", clock)
    monkeypatch.setattr(cli, "_sleep_sync", clock.sleep)
    monkeypatch.setattr(cli, "_utcnow", lambda: datetime(2026, 10, 10, 3, 30, 15, tzinfo=timezone.utc))
    monkeypatch.setattr(cli, "_sync_client", lambda: httpx.Client(transport=httpx.MockTransport(env.handler)))
    monkeypatch.setattr(cli.truststore, "inject_into_ssl", lambda: None)
    monkeypatch.setenv("GATEWAY_CALLER_LABELS", json.dumps({OWNER_OID: "owner"}))
    return env


def _argv(env: _Env, command: str, *extra: str) -> list[str]:
    return [command, "--out", str(env.out), "--tenant", TENANT, *extra]


def _upload(env: _Env, kind: str, *extra: str) -> int:
    return cli.main(_argv(env, "upload", "--kind", kind, "--dir", str(env.files), "--blob-endpoint", BLOB,
                          "--search-endpoint", SEARCH, "--corpus", str(env.corpus), *extra))


def _manifest(env: _Env) -> dict:
    return json.loads((env.out / "functions-session.json").read_text(encoding="utf-8"))


def _everything_written(env: _Env) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in env.out.iterdir() if p.is_file())


def _files(env: _Env) -> list[str]:
    return sorted(p.name for p in env.out.iterdir()) if env.out.exists() else []


# --- the guards ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("command", ["upload", "ingest-checks", "tool-checks"])
def test_a_measured_command_refuses_a_dirty_tree_before_any_token_request_or_file(env, capsys, command):
    env.porcelain = " M x\n"
    argv = {"upload": ["--kind", "broken", "--blob-endpoint", BLOB, "--search-endpoint", SEARCH],
            "ingest-checks": ["--search-endpoint", SEARCH, "--queue-endpoint", BLOB],
            "tool-checks": ["--mcp-url", MCP, "--app-url", APP, "--scope", GATEWAY_SCOPE]}[command]
    assert cli.main(_argv(env, command, *argv)) == 1
    assert "not clean" in capsys.readouterr().err
    assert env.requests == [] and _Tokens.created == [] and _files(env) == []


@pytest.mark.parametrize("command", ["upload", "ingest-checks", "tool-checks"])
def test_a_measured_command_refuses_an_output_directory_git_would_see(env, capsys, command):
    env.ignored = False
    argv = {"upload": ["--kind", "broken", "--blob-endpoint", BLOB, "--search-endpoint", SEARCH],
            "ingest-checks": ["--search-endpoint", SEARCH, "--queue-endpoint", BLOB],
            "tool-checks": ["--mcp-url", MCP, "--app-url", APP, "--scope", GATEWAY_SCOPE]}[command]
    inside = cli.freeze.REPO_ROOT / "eval" / "functions-out-not-real"
    assert cli.main([command, "--out", str(inside), "--tenant", TENANT, *argv]) == 1
    assert "not git-ignored" in capsys.readouterr().err
    assert env.requests == [] and not inside.exists()


# --- upload --------------------------------------------------------------------------------------------

def test_upload_new_puts_each_file_as_session_and_key_and_records_the_expected_chunks(env, capsys):
    env.corpus_of({"issue:1": 2, "pull_request:7": 3})
    env.artefact_file("issue:1")
    env.artefact_file("pull_request:7")

    assert _upload(env, "new") == 0

    manifest = _manifest(env)
    session = manifest["session"]
    assert len(session) == 8
    assert sorted(name for name, _ in env.blobs) == sorted(f"{session}-{_stem(a)}.json" for a in ("issue:1", "pull_request:7"))
    assert all(json.loads(data) for _, data in env.blobs)
    uploads = {u["artefact"]: u for u in manifest["uploads"]}
    assert {a: (u["kind"], u["expected_chunks"]) for a, u in uploads.items()} == {
        "issue:1": ("new", 2), "pull_request:7": ("new", 3)}
    assert uploads["issue:1"]["blob"] == f"{session}-{_stem('issue:1')}.json" and uploads["issue:1"]["uploaded_at"] > 0
    assert sorted(_Tokens.created) == sorted([(ic.STORAGE_SCOPE, TENANT), ("https://search.azure.com/.default", TENANT)])
    put = next(r for r in env.requests if r.method == "PUT")
    assert put.headers["authorization"] == "Bearer t-storage" and put.url.path.startswith("/artefacts-in/")
    assert "uploaded 2" in capsys.readouterr().out


def test_upload_new_refuses_an_artefact_the_index_already_holds_and_uploads_nothing(env, capsys):
    env.corpus_of({"issue:1": 2, "issue:2": 1})
    env.artefact_file("issue:1")
    env.artefact_file("issue:2")
    env.ingested("issue:2", 1)

    assert _upload(env, "new") == 1

    assert "already" in capsys.readouterr().err and env.blobs == [] and _files(env) == []


def test_upload_new_refuses_an_artefact_the_corpus_does_not_hold_and_a_file_that_is_not_a_key(env, capsys):
    env.corpus_of({"issue:1": 2})
    env.artefact_file("issue:9")
    assert _upload(env, "new") == 1 and "corpus" in capsys.readouterr().err
    (env.files / f"{_stem('issue:9')}.json").unlink()
    (env.files / "readme.json").write_text("{}", encoding="utf-8")
    assert _upload(env, "new") == 1 and "not a base64url artefact key" in capsys.readouterr().err
    assert env.blobs == []


def test_a_second_upload_joins_the_session_and_a_new_session_moves_the_old_manifest_aside(env):
    env.corpus_of({"issue:1": 2, "issue:2": 1})
    env.artefact_file("issue:1")
    assert _upload(env, "new") == 0
    first = _manifest(env)["session"]
    (env.files / f"{_stem('issue:1')}.json").unlink()
    env.artefact_file("issue:2")
    assert _upload(env, "new") == 0
    assert _manifest(env)["session"] == first and len(_manifest(env)["uploads"]) == 2

    (env.files / f"{_stem('issue:2')}.json").unlink()
    env.artefact_file("issue:1")
    env.index.clear()
    assert _upload(env, "new", "--new-session") == 0
    assert _manifest(env)["session"] != first and len(_manifest(env)["uploads"]) == 1
    assert (env.out / f"functions-session-{first}.json").exists()


def test_upload_changed_records_the_old_keys_and_needs_the_expected_count_and_the_gone_text(env, capsys):
    env.artefact_file("issue:1")
    env.index["issue:1"] = [{"chunk_id": str(k), "content": "old"} for k in (9001, 9002, 9003)]

    assert _upload(env, "changed") == 1 and "--expect-chunks" in capsys.readouterr().err and env.blobs == []
    assert _upload(env, "changed", "--expect-chunks", "2", "--gone-text", "the cut tail") == 0

    entry = _manifest(env)["uploads"][0]
    assert (entry["kind"], entry["expected_chunks"], entry["gone_text"]) == ("changed", 2, "the cut tail")
    assert sorted(entry["old_keys"]) == ["9001", "9002", "9003"]


def test_upload_changed_refuses_an_artefact_that_is_not_in_the_index_yet(env, capsys):
    env.artefact_file("issue:1")
    assert _upload(env, "changed", "--expect-chunks", "2", "--gone-text", "tail") == 1
    assert "not in the index" in capsys.readouterr().err and env.blobs == []


def test_upload_changed_refuses_a_gone_text_that_holds_an_identifier_before_uploading(env, capsys):
    env.artefact_file("issue:1")
    env.index["issue:1"] = [{"chunk_id": "1", "content": "old"}]
    assert _upload(env, "changed", "--expect-chunks", "2", "--gone-text", f"see {APP_ID}") == 1
    assert "GUID" in capsys.readouterr().err and env.blobs == []


def test_upload_duplicate_uploads_twice_after_the_first_is_searchable_and_keeps_the_keys_between(env):
    env.corpus_of({"issue:1": 3})
    env.artefact_file("issue:1")
    env.on_put = lambda name: env.ingested("issue:1", 3)

    assert _upload(env, "duplicate") == 0

    assert [name for name, _ in env.blobs] == [env.blobs[0][0]] * 2
    entry = _manifest(env)["uploads"][0]
    assert entry["kind"] == "duplicate" and entry["expected_chunks"] == 3
    assert entry["keys_after_first"] == [ic.chunk_key("issue:1", i) for i in range(3)]
    assert entry["second_uploaded_at"] >= entry["uploaded_at"]


def test_upload_duplicate_stops_without_a_second_upload_when_the_first_never_becomes_searchable(env, capsys):
    env.corpus_of({"issue:1": 3})
    env.artefact_file("issue:1")
    assert _upload(env, "duplicate") == 1
    assert len(env.blobs) == 1 and "first upload" in capsys.readouterr().err


def test_upload_broken_puts_a_malformed_file_and_records_the_document_count(env):
    assert _upload(env, "broken") == 0
    (name, data), = env.blobs
    entry = _manifest(env)["uploads"][0]
    assert name == f"{_manifest(env)['session']}-broken.json" and entry["blob"] == name
    with pytest.raises(ValueError):
        json.loads(data)
    assert (entry["kind"], entry["docs_before"]) == ("broken", 1000)


def test_upload_writes_what_was_uploaded_even_when_a_later_upload_fails(env, capsys):
    env.corpus_of({"issue:1": 2, "issue:2": 1})
    env.artefact_file("issue:1")
    env.artefact_file("issue:2")
    def fail_second(name):
        if len(env.blobs) == 2:
            raise httpx.ConnectError("down")

    env.on_put = fail_second
    assert _upload(env, "new") == 1
    assert [u["artefact"] for u in _manifest(env)["uploads"]] == ["issue:1"]
    assert "ConnectError" in capsys.readouterr().err


# --- ingest-checks ---------------------------------------------------------------------------------------------

def _checks(env: _Env, *extra: str) -> int:
    return cli.main(_argv(env, "ingest-checks", "--search-endpoint", SEARCH, "--queue-endpoint", BLOB, *extra))


def test_ingest_checks_run_i1_on_the_manifest_and_write_only_that_checks_result(env, capsys):
    env.corpus_of({"issue:1": 2})
    env.artefact_file("issue:1")
    env.on_put = lambda name: env.ingested("issue:1", 2)
    env.index.clear()
    assert _upload(env, "new") == 0        # the hook puts the chunks in the index straight away

    assert _checks(env, "--check", "i1") == 0

    assert json.loads((env.out / "check-i1.json").read_text(encoding="utf-8"))["passed"] is True
    assert "I1: pass" in capsys.readouterr().out
    assert [f for f in _files(env) if f.startswith("check-")] == ["check-i1.json"]


def test_ingest_checks_default_to_every_check_the_manifest_has_an_upload_for(env, capsys):
    env.corpus_of({"issue:1": 2})
    env.artefact_file("issue:1")
    assert _upload(env, "new") == 0
    env.ingested("issue:1", 2)

    assert _checks(env) == 0

    assert [f for f in _files(env) if f.startswith("check-")] == ["check-i1.json"]


def test_a_failed_ingest_check_still_writes_its_result_and_exits_1(env, capsys):
    env.corpus_of({"issue:1": 2})
    env.artefact_file("issue:1")
    assert _upload(env, "new") == 0
    assert _checks(env, "--check", "i1") == 1
    assert json.loads((env.out / "check-i1.json").read_text(encoding="utf-8"))["passed"] is False
    assert "I1: fail" in capsys.readouterr().out


def test_ingest_checks_i4_only_peeks_the_poison_queue_and_passes_on_a_matching_message(env, capsys):
    assert _upload(env, "broken") == 0
    blob = _manifest(env)["uploads"][0]["blob"]
    event = {"subject": f"/blobServices/default/containers/artefacts-in/blobs/{blob}"}
    env.poison = [base64.b64encode(json.dumps(event).encode()).decode()]
    before = len(env.requests)

    assert _checks(env, "--check", "i4") == 0

    new = env.requests[before:]
    peeks = [r for r in new if r.url.host == "store.example.com"]
    assert peeks and all(r.method == "GET" and r.url.params["peekonly"] == "true" for r in peeks)
    assert "I4: pass" in capsys.readouterr().out


def test_ingest_checks_refuse_a_check_with_no_upload_for_it_and_a_missing_manifest(env, capsys):
    assert _checks(env) == 1 and "functions-session.json" in capsys.readouterr().err
    assert _upload(env, "broken") == 0
    assert _checks(env, "--check", "i2") == 1 and "no changed upload" in capsys.readouterr().err
    assert not (env.out / "check-i2.json").exists()


def test_ingest_checks_ask_the_index_with_the_search_token_and_the_queue_with_the_storage_token(env):
    env.corpus_of({"issue:1": 2})
    env.artefact_file("issue:1")
    assert _upload(env, "new") == 0
    env.ingested("issue:1", 2)
    _Tokens.created = []
    assert _checks(env, "--check", "i1") == 0
    assert _Tokens.created and all(s == "https://search.azure.com/.default" for s, _ in _Tokens.created)


# --- tool-checks ------------------------------------------------------------------------------------------------

@pytest.fixture
def questions_file(tmp_path, env):
    items = [frozen.Question(f"q{n:03d}", f"Question {n}?", f"issue:{n}", "issue") for n in range(1, 13)]
    path = tmp_path / "questions.jsonl"
    frozen.freeze(items, {"seed": 1}, path)
    for n in range(1, 13):
        env.answers[f"Question {n}?"] = [f"issue:{n}", f"commit:{n}ab", f"pull_request:{n}", f"issue:{n + 50}", f"release:v{n}"]
    return path


def _tool(env: _Env, questions: Path, *extra: str) -> int:
    return cli.main(_argv(env, "tool-checks", "--mcp-url", MCP, "--app-url", APP, "--scope", GATEWAY_SCOPE,
                          "--search-endpoint", SEARCH, "--openai-base-url", OPENAI, "--app-id", APP_ID,
                          "--questions", str(questions), *extra))


def test_tool_checks_t1_compares_the_gateway_calls_with_the_direct_query(env, questions_file, capsys):
    assert _tool(env, questions_file, "--check", "t1") == 0

    assert json.loads((env.out / "check-t1.json").read_text(encoding="utf-8"))["passed"] is True
    assert "T1: pass" in capsys.readouterr().out
    gateway = [r for r in env.requests if r.url.host == "gateway.example.com"]
    assert gateway and all(r.headers["authorization"] == "Bearer t-gateway" for r in gateway)
    calls = [json.loads(r.content)["params"]["arguments"] for r in gateway if json.loads(r.content)["method"] == "tools/call"]
    assert [c["query"] for c in calls] == [f"Question {n}?" for n in range(1, 11)]        # the first ten, in file order
    assert {r.headers["authorization"] for r in env.requests if r.url.host == "oai.example.com"} == {"Bearer t-direct"}


def test_tool_checks_t1_fails_when_the_direct_query_disagrees(env, questions_file):
    env.answers["Question 4?"] = ["issue:4"]
    # the tool and the direct query read the same table in this stub, so make the tool differ:
    original = env.mcp

    def tool_differs(request, auth, *, tool):
        message = json.loads(request.content)
        if message["method"] == "tools/call" and message["params"]["arguments"]["query"] == "Question 4?":
            hits = [{"artefact": "issue:99", "type": "issue", "excerpt": "x", "score": 1.0}]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": {
                "content": [{"type": "text", "text": json.dumps(hits)}], "isError": False}})
        return original(request, auth, tool=tool)

    env.mcp = tool_differs
    assert _tool(env, questions_file, "--check", "t1") == 1
    assert json.loads((env.out / "check-t1.json").read_text(encoding="utf-8"))["passed"] is False


def test_tool_checks_t2_and_t3_pass_on_401s_and_use_the_right_tokens(env, questions_file):
    assert _tool(env, questions_file, "--check", "t2", "--check", "t3") == 0

    t2 = [r for r in env.requests if r.url.host == "gateway.example.com"]
    assert {r.headers.get("authorization") for r in t2} == {None, "Bearer t-direct"}
    t3 = [r for r in env.requests if r.url.host == "tool.example.com"]
    assert {r.headers.get("authorization") for r in t3} == {None, "Bearer t-gateway"}
    assert (env.out / "check-t2.json").exists() and (env.out / "check-t3.json").exists()


def test_tool_checks_t4_sends_thirty_at_once_records_the_burst_and_reads_the_metric_by_label(env, questions_file, capsys):
    env.tool_limit = 20
    env.metric_rows = [{"Caller": OWNER_OID, "total": 500}, {"Caller": "11111111-1111-1111-1111-111111111111", "total": 4}]

    assert _tool(env, questions_file, "--check", "t4") == 0

    assert "T4: pass" in capsys.readouterr().out
    burst = json.loads((env.out / "check-t4-burst.json").read_text(encoding="utf-8"))
    assert burst["sent"] == 30 and burst["refused_with_retry_after"] > 0 and burst["since"].endswith("Z")
    query = next(r for r in env.requests if r.url.host == "api.applicationinsights.io")
    assert "Tool Calls" in json.loads(query.content)["query"] and query.headers["authorization"] == "Bearer t-insights"
    assert not any(OWNER_OID in text for text in (_everything_written(env),))


def test_tool_checks_t4_fails_with_no_429_and_writes_the_result(env, questions_file):
    assert _tool(env, questions_file, "--check", "t4") == 1
    assert json.loads((env.out / "check-t4.json").read_text(encoding="utf-8"))["passed"] is False


def test_tool_checks_t4_metric_reruns_the_metric_alone_and_overwrites_only_its_own_result(env, questions_file):
    env.tool_limit = 20
    env.metric_rows = []                                         # the metric has not arrived
    assert _tool(env, questions_file, "--check", "t4", "--metric-wait", "0") == 1
    burst_before = (env.out / "check-t4-burst.json").read_text(encoding="utf-8")
    (env.out / "check-t1.json").write_text('{"check": "T1", "passed": true, "detail": "kept"}', encoding="utf-8")
    sent = len(env.requests)

    env.metric_rows = [{"Caller": OWNER_OID, "total": 500}]
    assert _tool(env, questions_file, "--check", "t4-metric", "--metric-wait", "0") == 0

    assert json.loads((env.out / "check-t4.json").read_text(encoding="utf-8"))["passed"] is True
    assert (env.out / "check-t4-burst.json").read_text(encoding="utf-8") == burst_before
    assert (env.out / "check-t1.json").read_text(encoding="utf-8") == '{"check": "T1", "passed": true, "detail": "kept"}'
    assert {r.url.host for r in env.requests[sent:]} == {"api.applicationinsights.io"}


def test_tool_checks_t4_metric_needs_the_recorded_burst(env, questions_file, capsys):
    assert _tool(env, questions_file, "--check", "t4-metric") == 1
    assert "check-t4-burst.json" in capsys.readouterr().err


def test_tool_checks_t4_needs_the_label_map_before_any_token_is_asked_for(env, questions_file, capsys, monkeypatch):
    monkeypatch.delenv("GATEWAY_CALLER_LABELS")
    assert _tool(env, questions_file, "--check", "t4") == 1
    assert "GATEWAY_CALLER_LABELS" in capsys.readouterr().err
    assert _Tokens.created == [] and env.requests == []


def test_tool_checks_need_their_own_arguments_and_refuse_before_any_request(env, questions_file, capsys):
    argv = _argv(env, "tool-checks", "--mcp-url", MCP, "--app-url", APP, "--scope", GATEWAY_SCOPE,
                 "--questions", str(questions_file), "--check", "t1")
    assert cli.main(argv) == 1
    assert "--search-endpoint" in capsys.readouterr().err
    assert env.requests == [] and _Tokens.created == []


# --- report ------------------------------------------------------------------------------------------------------

def test_report_renders_the_saved_results_and_the_commit_and_says_which_checks_did_not_run(env, capsys):
    env.out.mkdir()
    (env.out / "check-i1.json").write_text(json.dumps({"check": "I1", "passed": True, "detail": "5 of 5"}), encoding="utf-8")

    assert cli.main(["report", "--dir", str(env.out)]) == 0

    text = (env.out / "report.md").read_text(encoding="utf-8")
    assert "- I1: pass" in text and "- T1: not run" in text and COMMIT in text


def test_report_refuses_to_write_a_result_that_holds_an_identifier(env, capsys):
    env.out.mkdir()
    (env.out / "check-i1.json").write_text(
        json.dumps({"check": "I1", "passed": True, "detail": f"leak {APP_ID}"}), encoding="utf-8")
    assert cli.main(["report", "--dir", str(env.out)]) == 1
    assert "GUID" in capsys.readouterr().err and not (env.out / "report.md").exists()


# --- what is written ---------------------------------------------------------------------------------------------

def test_nothing_written_holds_a_token_a_url_a_hostname_a_guid_or_an_oid(env, questions_file):
    env.tool_limit = 20
    env.corpus_of({"issue:1": 2})
    env.artefact_file("issue:1")
    env.on_put = lambda name: env.ingested("issue:1", 2)
    assert _upload(env, "new") == 0
    assert _checks(env, "--check", "i1") == 0
    assert _tool(env, questions_file) in (0, 1)
    assert cli.main(["report", "--dir", str(env.out)]) == 0

    text = _everything_written(env)
    for forbidden in ("t-gateway", "t-storage", "t-search", "t-direct", "t-insights", "example.com", "https://",
                      TENANT, OWNER_OID, APP_ID, "22222222", "Bearer"):
        assert forbidden not in text
