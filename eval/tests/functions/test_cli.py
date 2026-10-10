"""The harness commands: upload, ingest-checks, tool-checks, report.

No network and no real time: every endpoint is one `httpx.MockTransport` router, the token source is
a fake, sleep and the clock are fakes, git is a stub, and truststore is not injected.
"""

import base64
import json
import subprocess
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from email.utils import formatdate
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
        self.poison: list[tuple[str, float]] = []        # (message text, insertion time)
        self.poison_count: int | None = None
        self.put_headers: list[httpx.Headers] = []
        self.conflicts: set[str] = set()
        self.answers: dict[str, list[str]] = {}
        self.gateway_calls = 0
        self.tool_limit = 10_000
        self.metric_rows = [{"Caller": OWNER_OID, "total": 500}]

    def run(self, command, **kwargs):
        if command[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(command, 0, self.porcelain, "")
        if command[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(command, 0, COMMIT + "\n", "")
        return subprocess.CompletedProcess(command, 0 if self.ignored else 1, "", "")

    # -- artefact files and the export manifest
    def artefact_file(self, artefact: str, chunks: int | None = None) -> Path:
        """A file as `export-artefacts` writes it; with `chunks`, its entry in manifest.json."""
        path = self.files / f"{_stem(artefact)}.json"
        path.write_text(json.dumps({"entityType": artefact.split(":")[0]}), encoding="utf-8")
        if chunks is not None:
            manifest_path = self.files / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
            manifest[path.name] = chunks
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def autoingest(self, counts: dict[str, int]) -> None:
        """What the Function does: once a blob is put, its artefact's chunks are in the index."""
        def hook(name: str) -> None:
            artefact = ic.artefact_of_stem(name.split("-", 1)[1].removesuffix(".json"))
            if artefact in counts:
                self.ingested(artefact, counts[artefact])

        self.on_put = hook

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
            if name in self.conflicts and "if-none-match" in request.headers:
                return httpx.Response(409, text="BlobAlreadyExists")
            with self.lock:
                self.blobs.append((name, request.content))
                self.put_headers.append(request.headers)
            if self.on_put:
                self.on_put(name)
            return httpx.Response(201)
        if host == "store.example.com" and request.method == "GET":
            if request.url.params.get("comp") == "metadata":
                count = len(self.poison) if self.poison_count is None else self.poison_count
                return httpx.Response(200, headers={"x-ms-approximate-messages-count": str(count)})
            xml = "".join(f"<QueueMessage><InsertionTime>{formatdate(at, usegmt=True)}</InsertionTime>"
                          f"<MessageText>{t}</MessageText></QueueMessage>" for t, at in self.poison)
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
    # The wall clock follows the fake clock, so a wait moves it: it starts at 03:30:45 UTC.
    clock.now = datetime(2026, 10, 10, 3, 30, 45, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(cli, "_utcnow", lambda: datetime.fromtimestamp(clock.now, timezone.utc))
    monkeypatch.setattr(cli, "_sync_client", lambda: httpx.Client(transport=httpx.MockTransport(env.handler)))
    monkeypatch.setattr(cli.truststore, "inject_into_ssl", lambda: None)
    monkeypatch.setenv("GATEWAY_CALLER_LABELS", json.dumps({OWNER_OID: "owner"}))
    return env


def _argv(env: _Env, command: str, *extra: str) -> list[str]:
    return [command, "--out", str(env.out), "--tenant", TENANT, *extra]


def _upload(env: _Env, kind: str, *extra: str) -> int:
    return cli.main(_argv(env, "upload", "--kind", kind, "--dir", str(env.files), "--blob-endpoint", BLOB,
                          "--search-endpoint", SEARCH, *extra))


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

def test_upload_new_puts_each_file_as_session_and_key_with_if_none_match_and_watches_its_window(env, capsys):
    env.artefact_file("issue:1", 2)
    env.artefact_file("pull_request:7", 3)
    env.autoingest({"issue:1": 2, "pull_request:7": 3})

    assert _upload(env, "new") == 0

    manifest = _manifest(env)
    session = manifest["session"]
    assert len(session) == 8
    assert sorted(name for name, _ in env.blobs) == sorted(f"{session}-{_stem(a)}.json" for a in ("issue:1", "pull_request:7"))
    assert all(headers.get("if-none-match") == "*" for headers in env.put_headers)
    uploads = {u["artefact"]: u for u in manifest["uploads"]}
    assert {a: (u["kind"], u["expected_chunks"], u["watched"], u["problem"]) for a, u in uploads.items()} == {
        "issue:1": ("new", 2, True, None), "pull_request:7": ("new", 3, True, None)}
    assert all(u["observed_after"] is not None and u["observed_after"] <= 120 for u in uploads.values())
    assert uploads["issue:1"]["blob"] == f"{session}-{_stem('issue:1')}.json" and uploads["issue:1"]["uploaded_at"] > 0
    assert sorted(_Tokens.created) == sorted([(ic.STORAGE_SCOPE, TENANT), ("https://search.azure.com/.default", TENANT)])
    put = next(r for r in env.requests if r.method == "PUT")
    assert put.headers["authorization"] == "Bearer t-storage" and put.url.path.startswith("/artefacts-in/")
    assert "uploaded 2 new" in capsys.readouterr().out


def test_upload_never_uploads_the_export_manifest(env):
    env.artefact_file("issue:1", 2)
    env.autoingest({"issue:1": 2})
    assert _upload(env, "new") == 0
    assert [name for name, _ in env.blobs] == [f"{_manifest(env)['session']}-{_stem('issue:1')}.json"]


def test_upload_new_watches_the_whole_window_and_records_a_miss_without_failing_the_upload(env, capsys):
    env.artefact_file("issue:1", 2)                    # nothing ingests it
    assert _upload(env, "new") == 0
    entry = _manifest(env)["uploads"][0]
    assert entry["watched"] is True and entry["observed_after"] is None and "0 of 2" in entry["problem"]
    assert "not searchable" in capsys.readouterr().out
    assert env.clock.now >= entry["uploaded_at"] + 120


def _index_fails(env: _Env, statuses: dict[int, int] | None = None, *, from_read: int | None = None) -> None:
    """The index answers a filtered read with the given status: on the reads `statuses` names (1-based), or on
    every read from `from_read` on. Read 1 is `upload`'s snapshot before the upload."""
    original, reads = env.search, [0]

    def failing(request, path):
        if "filter" in json.loads(request.content or b"{}"):
            reads[0] += 1
            status = (statuses or {}).get(reads[0]) or (503 if from_read and reads[0] >= from_read else None)
            if status:
                return httpx.Response(status, text="busy")
        return original(request, path)

    env.search = failing


def test_upload_new_rides_out_transient_index_errors_and_records_how_many_polls_failed(env, capsys):
    env.artefact_file("issue:1", 2)
    env.autoingest({"issue:1": 2})
    _index_fails(env, {2: 503, 3: 429})

    assert _upload(env, "new") == 0

    entry = _manifest(env)["uploads"][0]
    assert entry["watched"] is True and entry["observed_after"] is not None and entry["problem"] is None
    assert (entry["failed_polls"], entry["polls"]) == (2, 3)
    assert "2 of 3 polls" in capsys.readouterr().out
    assert _checks(env, "--check", "i1") == 0
    detail = json.loads((env.out / "check-i1.json").read_text(encoding="utf-8"))["detail"]
    assert "2 of 3 polls failed" in detail


def test_upload_new_stops_at_once_on_a_403_from_the_index(env, capsys):
    env.artefact_file("issue:1", 2)
    _index_fails(env, {2: 403})
    assert _upload(env, "new") == 1
    assert "the index answered 403" in capsys.readouterr().err
    assert _manifest(env)["uploads"][0]["watched"] is False          # on record, never judged
    assert env.clock.now < _manifest(env)["uploads"][0]["uploaded_at"] + 120


def test_an_upload_whose_every_poll_failed_is_refused_by_ingest_checks_with_no_result(env, capsys):
    env.artefact_file("issue:1", 2)
    env.autoingest({"issue:1": 2})
    _index_fails(env, from_read=2)

    assert _upload(env, "new") == 0
    entry = _manifest(env)["uploads"][0]
    assert entry["watched"] is False and entry["failed_polls"] == entry["polls"] > 0

    assert _checks(env, "--check", "i1") == 1
    assert "not watched" in capsys.readouterr().err and not (env.out / "check-i1.json").exists()


def test_upload_new_refuses_a_missing_export_manifest_and_a_file_it_does_not_list(env, capsys):
    env.artefact_file("issue:1")                       # a file, but no manifest.json entry
    assert _upload(env, "new") == 1 and "manifest.json" in capsys.readouterr().err
    assert env.blobs == [] and _files(env) == []
    env.artefact_file("issue:2", 3)
    assert _upload(env, "new") == 1 and "issue:1" in capsys.readouterr().err
    assert env.blobs == []


def test_upload_new_refuses_an_artefact_the_index_already_holds_and_uploads_nothing(env, capsys):
    env.artefact_file("issue:1", 2)
    env.artefact_file("issue:2", 1)
    env.ingested("issue:2", 1)

    assert _upload(env, "new") == 1

    assert "already" in capsys.readouterr().err and env.blobs == [] and _files(env) == []


def test_upload_refuses_a_file_that_is_not_a_key(env, capsys):
    env.artefact_file("issue:1", 2)
    (env.files / "readme.json").write_text("{}", encoding="utf-8")
    assert _upload(env, "new") == 1 and "not a base64url artefact key" in capsys.readouterr().err
    assert env.blobs == []


def test_a_repeated_upload_of_the_same_name_is_refused_plainly_on_a_409(env, capsys):
    env.artefact_file("issue:1", 2)
    env.autoingest({"issue:1": 2})
    assert _upload(env, "new") == 0
    env.index.clear()
    env.conflicts = {f"{_manifest(env)['session']}-{_stem('issue:1')}.json"}
    assert _upload(env, "new") == 1
    assert "already exists" in capsys.readouterr().err and len(_manifest(env)["uploads"]) == 1


def test_a_second_upload_joins_the_session_and_a_new_session_moves_the_old_manifest_aside(env):
    env.artefact_file("issue:1", 2)
    env.autoingest({"issue:1": 2, "issue:2": 1})
    assert _upload(env, "new") == 0
    first = _manifest(env)["session"]
    (env.files / f"{_stem('issue:1')}.json").unlink()
    env.artefact_file("issue:2", 1)
    assert _upload(env, "new") == 0
    assert _manifest(env)["session"] == first and len(_manifest(env)["uploads"]) == 2

    (env.files / f"{_stem('issue:2')}.json").unlink()
    env.artefact_file("issue:1", 2)
    env.index.clear()
    assert _upload(env, "new", "--new-session") == 0
    assert _manifest(env)["session"] != first and len(_manifest(env)["uploads"]) == 1
    assert (env.out / f"functions-session-{first}.json").exists()


OLD_TEXTS = ["old head", "old middle", "the cut tail", "old end"]


def _old_numeric(env: _Env, artefact: str = "issue:1") -> None:
    env.index[artefact] = [{"chunk_id": str(9001 + i), "content": t} for i, t in enumerate(OLD_TEXTS)]


def _changed_file(env: _Env, body: str = "a shortened body") -> None:
    (env.files / f"{_stem('issue:1')}.json").write_text(json.dumps({"entityType": "issue", "body": body}), encoding="utf-8")


def test_upload_changed_snapshots_the_old_chunks_then_watches_the_replacement_with_no_count_typed_in(env):
    _old_numeric(env)
    _changed_file(env)
    env.on_put = lambda name: env.ingested("issue:1", 2)

    assert _upload(env, "changed", "--gone-text", "the cut tail") == 0

    entry = _manifest(env)["uploads"][0]
    assert (entry["kind"], entry["gone_text"], entry["watched"], entry["problem"]) == ("changed", "the cut tail", True, None)
    assert sorted(entry["old_keys"]) == ["9001", "9002", "9003", "9004"] and entry["new_count"] == 2
    assert entry["old_hashes"] == [ic.content_hash(t) for t in OLD_TEXTS]
    assert env.put_headers[0].get("if-none-match") == "*"


def test_upload_changed_no_longer_takes_a_chunk_count(env):
    _old_numeric(env)
    _changed_file(env)
    with pytest.raises(SystemExit):
        _upload(env, "changed", "--gone-text", "the cut tail", "--expect-chunks", "2")


def test_upload_changed_needs_a_gone_text(env, capsys):
    _old_numeric(env)
    _changed_file(env)
    assert _upload(env, "changed") == 1 and "--gone-text" in capsys.readouterr().err and env.blobs == []


def test_upload_changed_refuses_unless_every_snapshotted_key_is_numeric(env, capsys):
    """A second `changed` upload would snapshot the base64 keys of the first: spec §4.4 and §7 mean the bulk-loaded keys."""
    env.index["issue:1"] = [{"chunk_id": "9001", "content": "the cut tail"}, {"chunk_id": ic.chunk_key("issue:1", 0), "content": "x"}]
    _changed_file(env)
    assert _upload(env, "changed", "--gone-text", "the cut tail") == 1
    assert "numeric" in capsys.readouterr().err and env.blobs == []


def test_upload_changed_refuses_a_gone_text_not_in_the_old_content(env, capsys):
    _old_numeric(env)
    _changed_file(env)
    assert _upload(env, "changed", "--gone-text", "a phrase nobody wrote") == 1
    assert "old content" in capsys.readouterr().err and env.blobs == []


def test_upload_changed_refuses_a_gone_text_the_shortened_file_still_holds(env, capsys):
    _old_numeric(env)
    _changed_file(env, body="this body still has the cut tail in it")
    assert _upload(env, "changed", "--gone-text", "the cut tail") == 1
    assert "shortened file" in capsys.readouterr().err and env.blobs == []


def test_upload_changed_refuses_an_artefact_that_is_not_in_the_index_yet(env, capsys):
    _changed_file(env)
    assert _upload(env, "changed", "--gone-text", "tail") == 1
    assert "not in the index" in capsys.readouterr().err and env.blobs == []


def test_upload_changed_refuses_a_gone_text_that_holds_an_identifier_before_uploading(env, capsys):
    _old_numeric(env)
    _changed_file(env)
    assert _upload(env, "changed", "--gone-text", f"see {APP_ID}") == 1
    assert "GUID" in capsys.readouterr().err and env.blobs == []


def test_upload_duplicate_uploads_twice_overwriting_after_the_first_is_searchable_and_keeps_the_keys_between(env):
    env.artefact_file("issue:1", 3)
    env.on_put = lambda name: env.ingested("issue:1", 3)

    assert _upload(env, "duplicate") == 0

    assert [name for name, _ in env.blobs] == [env.blobs[0][0]] * 2
    assert all("if-none-match" not in headers for headers in env.put_headers)       # a duplicate must overwrite
    entry = _manifest(env)["uploads"][0]
    assert entry["kind"] == "duplicate" and entry["expected_chunks"] == 3
    assert entry["keys_after_first"] == [ic.chunk_key("issue:1", i) for i in range(3)]
    assert entry["second_uploaded_at"] >= entry["uploaded_at"]


def test_upload_duplicate_stops_without_a_second_upload_when_the_first_never_becomes_searchable(env, capsys):
    env.artefact_file("issue:1", 3)
    assert _upload(env, "duplicate") == 1
    assert len(env.blobs) == 1 and "first upload" in capsys.readouterr().err


def test_upload_broken_puts_a_malformed_file_with_if_none_match_and_records_the_document_count(env):
    assert _upload(env, "broken") == 0
    (name, data), = env.blobs
    entry = _manifest(env)["uploads"][0]
    assert name.startswith(f"{_manifest(env)['session']}-broken-") and name.endswith(".json") and entry["blob"] == name
    with pytest.raises(ValueError):
        json.loads(data)
    assert (entry["kind"], entry["docs_before"]) == ("broken", 1000)
    assert env.put_headers[0].get("if-none-match") == "*"


def test_every_broken_upload_has_its_own_name(env):
    assert _upload(env, "broken") == 0
    assert _upload(env, "broken") == 0
    names = [name for name, _ in env.blobs]
    assert len(set(names)) == 2
    assert [u["blob"] for u in _manifest(env)["uploads"]] == names


def test_upload_writes_what_was_uploaded_even_when_a_later_upload_fails(env, capsys):
    env.artefact_file("issue:1", 2)
    env.artefact_file("issue:2", 1)

    def fail_second(name):
        if len(env.blobs) == 2:
            raise httpx.ConnectError("down")

    env.on_put = fail_second
    assert _upload(env, "new") == 1
    entries = _manifest(env)["uploads"]
    assert [u["artefact"] for u in entries] == ["issue:1"] and entries[0]["watched"] is False
    assert "ConnectError" in capsys.readouterr().err


# --- ingest-checks ---------------------------------------------------------------------------------------------

def _checks(env: _Env, *extra: str) -> int:
    return cli.main(_argv(env, "ingest-checks", "--search-endpoint", SEARCH, "--queue-endpoint", BLOB, *extra))


def _write_session(env: _Env, *uploads: ic.Upload) -> None:
    env.out.mkdir(exist_ok=True)
    (env.out / "functions-session.json").write_text(
        json.dumps({"session": "ab12cd34", "uploads": [asdict(u) for u in uploads]}), encoding="utf-8")


def test_ingest_checks_judge_i1_from_what_upload_watched_without_reading_the_index(env, capsys):
    env.artefact_file("issue:1", 2)
    env.autoingest({"issue:1": 2})
    assert _upload(env, "new") == 0
    env.index.clear()                       # the index is no longer what it was: the verdict is from the watch
    before, created = len(env.requests), list(_Tokens.created)

    assert _checks(env, "--check", "i1") == 0

    assert json.loads((env.out / "check-i1.json").read_text(encoding="utf-8"))["passed"] is True
    assert "I1: pass" in capsys.readouterr().out
    assert [f for f in _files(env) if f.startswith("check-")] == ["check-i1.json"]
    assert len(env.requests) == before and _Tokens.created == created


def test_a_late_ingest_check_does_not_rejudge_an_earlier_batch_by_the_clock(env, capsys):
    env.artefact_file("issue:1", 2)
    env.autoingest({"issue:1": 2, "issue:2": 1})
    assert _upload(env, "new") == 0
    (env.files / f"{_stem('issue:1')}.json").unlink()
    env.artefact_file("issue:2", 1)
    assert _upload(env, "new") == 0
    env.clock.now += 86_400

    assert _checks(env, "--check", "i1") == 0
    assert "2 of 2" in capsys.readouterr().out


def test_ingest_checks_refuse_an_upload_that_was_not_watched_and_write_no_result(env, capsys):
    _write_session(env, ic.Upload("new", "issue:1", "ab12cd34-x.json", 1.0, expected_chunks=2))
    assert _checks(env, "--check", "i1") == 1
    err = capsys.readouterr().err
    assert "not watched" in err and "issue:1" in err
    assert not (env.out / "check-i1.json").exists()


def test_ingest_checks_refuse_i2_too_when_it_was_not_watched(env, capsys):
    _write_session(env, ic.Upload("changed", "issue:1", "ab12cd34-x.json", 1.0, old_keys=["1"], gone_text="x"))
    assert _checks(env, "--check", "i2") == 1 and "not watched" in capsys.readouterr().err
    assert not (env.out / "check-i2.json").exists()


def test_ingest_checks_default_to_every_check_the_session_has_an_upload_for(env):
    env.artefact_file("issue:1", 2)
    env.autoingest({"issue:1": 2})
    assert _upload(env, "new") == 0
    assert _checks(env) == 0
    assert [f for f in _files(env) if f.startswith("check-")] == ["check-i1.json"]


def test_a_failed_ingest_check_still_writes_its_result_and_exits_1(env, capsys):
    env.artefact_file("issue:1", 2)
    assert _upload(env, "new") == 0                           # nothing ingested it
    assert _checks(env, "--check", "i1") == 1
    assert json.loads((env.out / "check-i1.json").read_text(encoding="utf-8"))["passed"] is False
    assert "I1: fail" in capsys.readouterr().out


def test_ingest_checks_i2_passes_from_the_watch_with_no_count_given(env, capsys):
    _old_numeric(env)
    _changed_file(env)
    env.on_put = lambda name: env.ingested("issue:1", 1)
    assert _upload(env, "changed", "--gone-text", "the cut tail") == 0
    assert _checks(env, "--check", "i2") == 0
    assert "I2: pass" in capsys.readouterr().out


def _inserted_after(env: _Env, name_of_blob: str, seconds: float) -> None:
    event = {"subject": f"/blobServices/default/containers/artefacts-in/blobs/{name_of_blob}"}
    env.poison = [(base64.b64encode(json.dumps(event).encode()).decode(), env.clock.now + seconds)]


def test_ingest_checks_i4_only_peeks_the_poison_queue_and_passes_on_a_matching_message(env, capsys):
    assert _upload(env, "broken") == 0
    _inserted_after(env, _manifest(env)["uploads"][0]["blob"], 120)
    before = len(env.requests)

    assert _checks(env, "--check", "i4") == 0

    store = [r for r in env.requests[before:] if r.url.host == "store.example.com"]
    assert store and all(r.method == "GET" for r in store)
    assert any(r.url.params.get("peekonly") == "true" for r in store)
    assert "I4: pass" in capsys.readouterr().out


def test_ingest_checks_i4_run_hours_later_is_judged_by_the_messages_insertion_time(env, capsys):
    assert _upload(env, "broken") == 0
    _inserted_after(env, _manifest(env)["uploads"][0]["blob"], 120)
    env.clock.now += 5 * 3600
    assert _checks(env, "--check", "i4") == 0
    assert "I4: pass" in capsys.readouterr().out


def test_ingest_checks_i4_refuses_a_poison_queue_over_32_messages_and_writes_no_result(env, capsys):
    assert _upload(env, "broken") == 0
    env.poison_count = 33
    assert _checks(env, "--check", "i4") == 1
    assert "33" in capsys.readouterr().err and not (env.out / "check-i4.json").exists()


def test_the_second_broken_upload_is_not_matched_by_the_first_ones_message(env, capsys):
    assert _upload(env, "broken") == 0
    first = _manifest(env)["uploads"][0]["blob"]
    assert _upload(env, "broken") == 0
    _inserted_after(env, first, 30)
    session = _manifest(env)
    # judge only the second upload: drop the first from the record, as a session with one broken upload
    session["uploads"] = session["uploads"][1:]
    (env.out / "functions-session.json").write_text(json.dumps(session), encoding="utf-8")
    assert _checks(env, "--check", "i4") == 1
    assert "I4: fail" in capsys.readouterr().out


def test_ingest_checks_refuse_a_check_with_no_upload_for_it_and_a_missing_manifest(env, capsys):
    assert _checks(env) == 1 and "functions-session.json" in capsys.readouterr().err
    assert _upload(env, "broken") == 0
    assert _checks(env, "--check", "i2") == 1 and "no changed upload" in capsys.readouterr().err
    assert not (env.out / "check-i2.json").exists()


def test_ingest_checks_i3_asks_the_index_with_the_search_token(env):
    env.artefact_file("issue:1", 3)
    env.on_put = lambda name: env.ingested("issue:1", 3)
    assert _upload(env, "duplicate") == 0
    _Tokens.created = []
    assert _checks(env, "--check", "i3") == 0
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
    assert burst["sent"] == 30 and burst["refused_with_retry_after"] > 0
    # 45 s past the minute: wait 30 s to 03:31:15, so the handshake is in the minute that opens the window,
    # and the window closes 10 s after the burst
    assert env.clock.sleeps[:1] == [30.0]
    assert burst["start"] == "2026-10-10T03:31:00Z" and burst["until"] == "2026-10-10T03:31:25Z"
    query = next(r for r in env.requests if r.url.host == "api.applicationinsights.io")
    sent = json.loads(query.content)
    assert "Tool Calls" in sent["query"] and query.headers["authorization"] == "Bearer t-insights"
    assert "between (datetime(2026-10-10T03:31:00Z) .. datetime(2026-10-10T03:31:25Z))" in sent["query"]
    assert not any(OWNER_OID in text for text in (_everything_written(env),))


def test_tool_checks_t4_waits_before_its_handshake_and_says_so(env, questions_file, capsys, monkeypatch):
    env.tool_limit = 20
    at_sleep = []
    monkeypatch.setattr(cli, "_sleep_sync", lambda s: (at_sleep.append((s, len(env.requests))), env.clock.sleep(s)))

    assert _tool(env, questions_file, "--check", "t4") == 0

    assert at_sleep[0] == (30.0, 0)                  # nothing was sent before the wait
    out = capsys.readouterr().out
    assert "T4: waiting 30 s" in out and "whole minute" in out


def test_tool_checks_t4_start_is_the_floor_of_the_handshake_minute_whatever_second_it_lands_on(env, questions_file):
    env.tool_limit = 20
    env.clock.now = datetime(2026, 10, 10, 3, 30, 0, tzinfo=timezone.utc).timestamp()      # waits the full 75 s
    assert _tool(env, questions_file, "--check", "t4") == 0
    burst = json.loads((env.out / "check-t4-burst.json").read_text(encoding="utf-8"))
    assert env.clock.sleeps[0] == 75.0 and burst["start"] == "2026-10-10T03:31:00Z"


def test_tool_checks_t1_records_when_it_ended(env, questions_file):
    assert _tool(env, questions_file, "--check", "t1") == 0
    ended = json.loads((env.out / "check-t1-ended.json").read_text(encoding="utf-8"))
    assert ended == {"ended": "2026-10-10T03:30:45Z"}
    assert "check-t1-ended.json" not in [r.check for r in cli.report.load_results(env.out)]


def test_tool_checks_t4_refuses_when_t1_ended_inside_its_window_and_sends_no_burst(env, questions_file, capsys):
    env.out.mkdir()
    (env.out / "check-t1-ended.json").write_text('{"ended": "2026-10-10T03:31:05Z"}', encoding="utf-8")

    assert _tool(env, questions_file, "--check", "t4") == 1

    assert "T1" in capsys.readouterr().err
    assert not any(json.loads(r.content).get("method") == "tools/call" for r in env.requests if r.url.host == "gateway.example.com")
    assert not (env.out / "check-t4-burst.json").exists()


def test_tool_checks_t4_accepts_a_t1_that_ended_before_the_window_opened(env, questions_file):
    env.tool_limit = 20
    env.out.mkdir()
    (env.out / "check-t1-ended.json").write_text('{"ended": "2026-10-10T03:30:59Z"}', encoding="utf-8")
    assert _tool(env, questions_file, "--check", "t4") == 0


def test_tool_checks_t4_refuses_a_t1_end_time_it_cannot_read(env, questions_file, capsys):
    env.out.mkdir()
    (env.out / "check-t1-ended.json").write_text("not json", encoding="utf-8")
    assert _tool(env, questions_file, "--check", "t4") == 1 and "check-t1-ended.json" in capsys.readouterr().err


def test_tool_checks_t4_fails_clearly_when_no_burst_call_got_past_the_gateway(env, questions_file, capsys):
    env.tool_limit = 0                                  # every tools/call is refused with a 429
    env.metric_rows = [{"Caller": OWNER_OID, "total": 500}]
    assert _tool(env, questions_file, "--check", "t4") == 1
    assert "got past the gateway" in capsys.readouterr().out
    assert not any(r.url.host == "api.applicationinsights.io" for r in env.requests)


def test_tool_checks_t4_fails_with_no_429_and_writes_the_result(env, questions_file):
    assert _tool(env, questions_file, "--check", "t4") == 1
    assert json.loads((env.out / "check-t4.json").read_text(encoding="utf-8"))["passed"] is False


def _rpc_error_on(env: _Env, method: str) -> None:
    """The server answers `method` with a JSON-RPC error whose message names an Azure host."""
    original = env.mcp

    def erring(request, auth, *, tool):
        message = json.loads(request.content)
        if message.get("method") == method and "id" in message:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "error": {
                "code": -32603, "message": "upstream apim-x.azure-api.net refused the call"}})
        return original(request, auth, tool=tool)

    env.mcp = erring


@pytest.mark.parametrize("check,method", [("t1", "tools/call"), ("t1", "initialize"), ("t4", "initialize")])
def test_a_server_error_message_with_a_hostname_still_writes_the_check_s_result(env, questions_file, capsys,
                                                                                check, method):
    _rpc_error_on(env, method)
    assert _tool(env, questions_file, "--check", check) == 1

    saved = json.loads((env.out / f"check-{check}.json").read_text(encoding="utf-8"))
    assert saved["passed"] is False and "McpRpcError" in saved["detail"] and "-32603" in saved["detail"]
    assert "azure-api.net" not in saved["detail"] and "upstream" not in saved["detail"]
    assert "not written" not in capsys.readouterr().err


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
    env.artefact_file("issue:1", 2)
    env.autoingest({"issue:1": 2})
    assert _upload(env, "new") == 0
    assert _checks(env, "--check", "i1") == 0
    assert _tool(env, questions_file) in (0, 1)
    assert cli.main(["report", "--dir", str(env.out)]) == 0

    text = _everything_written(env)
    for forbidden in ("t-gateway", "t-storage", "t-search", "t-direct", "t-insights", "example.com", "https://",
                      TENANT, OWNER_OID, APP_ID, "22222222", "Bearer"):
        assert forbidden not in text
