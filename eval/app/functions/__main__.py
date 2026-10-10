"""`python -m app.functions <command>`, run from eval/: the Functions harness (spec §7).

`upload` puts artefact files in the `artefacts-in` container as the owner, one `new`, `changed` or
`duplicate` kind at a time, or one malformed file (`broken`), and records each in
`<out>/functions-session.json`: the session id its blob names carry, when each was uploaded, and
what the checks need to judge it. `ingest-checks` reads that record and runs I1 to I4 against the
index and the poison queue. `tool-checks` runs T1 to T4 against the gateway, the tool app and
Application Insights. `report` renders the saved results.

Each check writes its own `check-<id>.json`, so a re-run overwrites only that check's result. T4's
metric lags: `tool-checks --check t4` sends the burst once and records it in `check-t4-burst.json`;
`--check t4-metric` reads the metric again later, from that record, and sends nothing.

A command that sends or measures refuses to start on a tree with uncommitted changes, and on an
output directory git would see, before any token is asked for, any request is sent or any file is
written. Every file goes through `report.write`, which refuses a GUID, an Azure hostname, or any
string this command was given as an identifier (the tenant, the app id, the scope, each URL's host)
or `GATEWAY_CALLER_LABELS`' object IDs; that variable stays in the owner's shell. Azure is called as
the owner through the Azure CLI, with no key, and no token, `oid`, URL or hostname is printed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
import truststore

from app.gateway import checks, freeze, metric
from app.retrieval import corpus, search_index
from app.retrieval.azure_auth import TokenSource

from . import ingest_checks as ic
from . import report
from . import tool_checks as tc
from .mcp_http import McpError, McpSession

EVAL = Path(__file__).resolve().parents[2]
REPORTS = EVAL / "reports"
DEFAULT_CORPUS = EVAL / "retrieval-data" / "chunks.jsonl"
DEFAULT_QUESTIONS = EVAL / "retrieval" / "questions.jsonl"

MANIFEST = "functions-session.json"
BURST_FILE = "check-t4-burst.json"
DIRECT_SCOPE = tc.DIRECT_SCOPE
CALLER = "owner"                       # the label the owner's `oid` has in GATEWAY_CALLER_LABELS
EMBEDDING_DEPLOYMENT = "releaselens-embed-small"
HTTP_TIMEOUT_S = 60.0
BURST_QUERY = "what changed in the latest release?"
SINCE_MARGIN = timedelta(seconds=60)
BROKEN_BYTES = b'{"entityType": "issue", "number": '       # cut off: not JSON, so every try fails

# Looked up when a command runs, so a test can replace them.
_run = subprocess.run
_now = time.time
_sleep_sync = time.sleep


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sync_client() -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT_S)


class Refused(Exception):
    """A command that cannot start or go on, with the reason, printed without a traceback."""


# --- shared --------------------------------------------------------------------------------------------

def _forbidden(args) -> list[str]:
    """Strings no written file may hold: what this command was given, and the labels' object IDs."""
    items = [getattr(args, name, None) for name in ("tenant", "app_id", "scope")]
    for name in ("blob_endpoint", "queue_endpoint", "search_endpoint", "mcp_url", "app_url", "openai_base_url"):
        url = getattr(args, name, None)
        items.append(urlparse(url).hostname if url else None)
    try:
        labels = metric.labels_from_env(os.environ)
    except metric.LabelsError:
        labels = {}
    for oid in labels:
        items += [oid, oid.replace("-", "")]
    return [item for item in items if item]


def _save(args, path: Path, text: str) -> None:
    report.write(path, text, _forbidden(args))


def _require_clean_text(args, text: str) -> None:
    """Raise `IdentifierError` if `text` could not be written to a file: used before anything is uploaded."""
    with tempfile.TemporaryDirectory() as directory:
        report.write(Path(directory) / "probe", text, _forbidden(args))


def _guard(args) -> None:
    """Before any token, request or file: an ignored output directory, and a clean tree."""
    freeze.require_output_ignored(args.out, _run)
    freeze.require_clean_tree(_run)


def _need(args, *flags: str) -> None:
    missing = [flag for flag in flags if not getattr(args, flag.lstrip("-").replace("-", "_"), None)]
    if missing:
        raise Refused(f"this needs {', '.join(missing)}")


def _announce(result: checks.CheckResult) -> None:
    print(f"{result.check}: {'pass' if result.passed else 'fail'} - {result.detail}")


def _finish(args, result: checks.CheckResult) -> None:
    """Save one check's own result file, then show it: nothing is printed that the writer did not pass."""
    _save(args, args.out / report.check_file(result.check), json.dumps(asdict(result), indent=2) + "\n")
    _announce(result)


def _stored(directory: Path) -> tuple[str, list[ic.Upload]] | None:
    path = directory / MANIFEST
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["session"], [ic.Upload(**item) for item in data["uploads"]]
    except (ValueError, KeyError, TypeError):
        raise Refused(f"{MANIFEST} is not a session record") from None


def _store(args, session: str, uploads: list[ic.Upload]) -> None:
    text = json.dumps({"session": session, "uploads": [asdict(u) for u in uploads]}, indent=2) + "\n"
    _save(args, args.out / MANIFEST, text)


def _reader(http: httpx.Client, endpoint: str, tokens: TokenSource) -> ic.ReadChunks:
    return lambda artefact: ic.chunks_for_artefact(http, endpoint, tokens.token, artefact)


# --- upload --------------------------------------------------------------------------------------------

def run_upload(args) -> int:
    _guard(args)
    kind = args.kind
    if kind != "broken" and args.dir is None:
        raise Refused("--dir is needed for this kind of upload")
    if kind == "changed":
        if not args.expect_chunks or not args.gone_text:
            raise Refused("--kind changed needs --expect-chunks (how many chunks the shortened file yields) and "
                          "--gone-text (a phrase only the cut-away text holds)")
    elif args.expect_chunks or args.gone_text:
        raise Refused("--expect-chunks and --gone-text are only for --kind changed")
    _require_clean_text(args, args.gone_text or "")

    files: list[tuple[Path, str]] = []
    if kind != "broken":
        for path in sorted(args.dir.glob("*.json"), key=lambda p: p.name):
            try:
                files.append((path, ic.artefact_of_stem(path.stem)))
            except ValueError:
                raise Refused(f"{path.name} is not a base64url artefact key (a file the Worker's export-artefacts "
                              "wrote is named so)") from None
        if not files:
            raise Refused(f"{args.dir} holds no <base64url key>.json file")
        if kind == "changed" and len(files) != 1:
            raise Refused("--kind changed uploads one file")

    expected: dict[str, int] = {}
    if kind in ("new", "duplicate"):
        counts = Counter(chunk.artefact for chunk in corpus.load_chunks(args.corpus))
        for _, artefact in files:
            if artefact not in counts:
                raise Refused(f"{artefact} is not in the corpus file: its expected chunk count is unknown")
            expected[artefact] = counts[artefact]

    stored = None if args.new_session else _stored(args.out)
    previous = _stored(args.out) if args.new_session else None
    session, uploads = stored if stored else (ic.new_session_id(), [])

    storage = TokenSource(ic.STORAGE_SCOPE, args.tenant)
    search = TokenSource(search_index.SCOPE, args.tenant)
    done: list[ic.Upload] = []
    try:
        with _sync_client() as http:
            read = _reader(http, args.search_endpoint, search)

            def put(name: str, data: bytes) -> float:
                ic.upload_blob(http, args.blob_endpoint, storage.token, name, data)
                return _now()

            if kind == "broken":
                before = search_index.document_count(args.search_endpoint, search, http)
                name = ic.blob_name(session, "broken")
                done.append(ic.Upload("broken", "", name, put(name, BROKEN_BYTES), docs_before=before))
            else:
                snapshots = {artefact: read(artefact) for _, artefact in files}
                for path, artefact in files:
                    if kind == "new" and snapshots[artefact]:
                        raise Refused(f"{artefact} is already in the index: I1 needs an artefact held back from "
                                      "the bulk load")
                    if kind == "changed" and not snapshots[artefact]:
                        raise Refused(f"{artefact} is not in the index: I2 needs an artefact from the bulk-loaded corpus")
                for path, artefact in files:
                    name = ic.blob_name(session, path.stem)
                    data = path.read_bytes()
                    if kind == "new":
                        done.append(ic.Upload("new", artefact, name, put(name, data), expected_chunks=expected[artefact]))
                    elif kind == "changed":
                        done.append(ic.Upload("changed", artefact, name, put(name, data),
                                              expected_chunks=args.expect_chunks, gone_text=args.gone_text,
                                              old_keys=[c["chunk_id"] for c in snapshots[artefact]]))
                    else:
                        first = ic.Upload("duplicate", artefact, name, put(name, data), expected_chunks=expected[artefact])
                        landed = ic.i1_new_searchable([first], read, clock=_now, sleep=_sleep_sync)
                        if not landed.passed:
                            raise Refused("the first upload did not become searchable, so it was not uploaded again: "
                                          + landed.detail)
                        keys = [c["chunk_id"] for c in read(artefact)]
                        done.append(ic.Upload("duplicate", artefact, name, first.uploaded_at,
                                              expected_chunks=expected[artefact], keys_after_first=keys,
                                              second_uploaded_at=put(name, data)))
    finally:
        # What was uploaded stays on record even if a later upload failed: the checks read it.
        if done:
            if previous is not None:
                (args.out / MANIFEST).rename(args.out / f"functions-session-{previous[0]}.json")
            _store(args, session, [*uploads, *done])
    print(f"uploaded {len(done)} {kind} artefact(s) in session {session}")
    return 0


# --- ingest-checks ---------------------------------------------------------------------------------------

_KINDS = {"i1": "new", "i2": "changed", "i3": "duplicate", "i4": "broken"}


def _combined(check: str, results: list[checks.CheckResult]) -> checks.CheckResult:
    if len(results) == 1:
        return results[0]
    return checks.CheckResult(check, all(r.passed for r in results), " | ".join(r.detail for r in results))


def run_ingest_checks(args) -> int:
    _guard(args)
    _need(args, "--search-endpoint")
    stored = _stored(args.out)
    if stored is None:
        raise Refused(f"{MANIFEST} is missing from the output directory: run `upload` first")
    _, uploads = stored
    by_kind = {kind: [u for u in uploads if u.kind == kind] for kind in _KINDS.values()}
    if args.check:
        wanted = list(dict.fromkeys(args.check))
        for check in wanted:
            if not by_kind[_KINDS[check]]:
                raise Refused(f"no {_KINDS[check]} upload in the session for {check.upper()}: "
                              f"run `upload --kind {_KINDS[check]}` first")
    else:
        wanted = [check for check, kind in _KINDS.items() if by_kind[kind]]
        if not wanted:
            raise Refused("the session holds no upload")
    if "i4" in wanted:
        _need(args, "--queue-endpoint")

    search = TokenSource(search_index.SCOPE, args.tenant)
    results: list[checks.CheckResult] = []
    with _sync_client() as http:
        read = _reader(http, args.search_endpoint, search)
        for check in wanted:
            clock = {"clock": _now, "sleep": _sleep_sync}
            if check == "i1":
                result = ic.i1_new_searchable(by_kind["new"], read, **clock)
            elif check == "i2":
                result = _combined("I2", [ic.i2_changed_replaces(u, read, **clock) for u in by_kind["changed"]])
            elif check == "i3":
                result = _combined("I3", [ic.i3_duplicate_harmless(u, read, **clock) for u in by_kind["duplicate"]])
            else:
                storage = TokenSource(ic.STORAGE_SCOPE, args.tenant)
                result = _combined("I4", [
                    ic.i4_broken_to_poison(
                        u, lambda: ic.peek_poison(http, args.queue_endpoint, storage.token),
                        lambda: search_index.document_count(args.search_endpoint, search, http), **clock)
                    for u in by_kind["broken"]])
            _finish(args, result)
            results.append(result)
    return 0 if all(r.passed for r in results) else 1


# --- tool-checks ---------------------------------------------------------------------------------------------

def _burst_record(burst: tc.Burst, since: str) -> str:
    return json.dumps({**asdict(burst), "since": since}, indent=2) + "\n"


def _load_burst(directory: Path) -> tuple[tc.Burst, str]:
    path = directory / BURST_FILE
    if not path.exists():
        raise Refused(f"{BURST_FILE} is missing: run `tool-checks --check t4` first, which sends the burst")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return tc.Burst(**{f.name: data[f.name] for f in fields(tc.Burst)}), data["since"]
    except (ValueError, KeyError, TypeError):
        raise Refused(f"{BURST_FILE} is not a burst record") from None


def run_tool_checks(args) -> int:
    _guard(args)
    wanted = list(dict.fromkeys(args.check or ["t1", "t2", "t3", "t4"]))
    if "t1" in wanted:
        _need(args, "--mcp-url", "--scope", "--search-endpoint", "--openai-base-url")
    if "t2" in wanted:
        _need(args, "--mcp-url")
    if "t3" in wanted:
        _need(args, "--app-url", "--scope")
    if "t4" in wanted:
        _need(args, "--mcp-url", "--scope", "--app-id")
    if "t4-metric" in wanted:
        _need(args, "--app-id")
    questions: list[tuple[str, str]] = []
    if "t1" in wanted:
        try:
            questions = tc.first_ten_questions(args.questions)
        except (ValueError, OSError) as error:
            raise Refused(f"the question set cannot be used: {type(error).__name__}") from None
    labels: dict[str, str] = {}
    burst_record: tuple[tc.Burst, str] | None = None
    if "t4" in wanted or "t4-metric" in wanted:
        labels = metric.labels_from_env(os.environ)           # before any token: a missing map is the owner's to set
    if "t4-metric" in wanted:
        burst_record = _load_burst(args.out)

    cache: dict[str, TokenSource] = {}

    def tokens(scope: str) -> TokenSource:
        return cache.setdefault(scope, TokenSource(scope, args.tenant))

    results: list[checks.CheckResult] = []
    with _sync_client() as http:
        def finish(result: checks.CheckResult) -> None:
            _finish(args, result)
            results.append(result)

        def read_totals(since: str) -> Callable[[], dict[str, int]]:
            return lambda: metric.totals(tc.tool_call_rows(args.app_id, tokens(metric.SCOPE).token(), since, http), labels)

        for check in wanted:
            if check == "t1":
                direct = tc.DirectSearch(http, args.openai_base_url, args.search_endpoint, args.deployment,
                                         tokens(DIRECT_SCOPE), tokens(search_index.SCOPE), sleep=_sleep_sync)
                session = McpSession(http, args.mcp_url, tokens(args.scope).token())
                finish(tc.t1_same_search(session, direct, questions))
            elif check == "t2":
                finish(tc.t2_gateway_refuses(McpSession(http, args.mcp_url, None),
                                             McpSession(http, args.mcp_url, tokens(DIRECT_SCOPE).token())))
            elif check == "t3":
                finish(tc.t3_no_bypass(McpSession(http, args.app_url, None),
                                       McpSession(http, args.app_url, tokens(args.scope).token())))
            elif check == "t4":
                since = (_utcnow() - SINCE_MARGIN).strftime("%Y-%m-%dT%H:%M:%SZ")
                session = McpSession(http, args.mcp_url, tokens(args.scope).token())
                try:
                    session.initialize()
                except McpError as error:
                    finish(checks.CheckResult("T4", False, f"the handshake failed before the burst: {error}"))
                    continue
                except httpx.TransportError:
                    finish(checks.CheckResult("T4", False, "the handshake before the burst got no response"))
                    continue
                burst = tc.run_burst(tc.mcp_send(session, BURST_QUERY))
                _save(args, args.out / BURST_FILE, _burst_record(burst, since))
                finish(tc.t4_rate_limit(burst, read_totals(since), label=CALLER, clock=_now, sleep=_sleep_sync,
                                        wait_s=args.metric_wait))
            else:
                assert burst_record is not None
                burst, since = burst_record
                finish(tc.t4_rate_limit(burst, read_totals(since), label=CALLER, clock=_now, sleep=_sleep_sync,
                                        wait_s=args.metric_wait))
    return 0 if all(r.passed for r in results) else 1


# --- report --------------------------------------------------------------------------------------------------

def _head() -> str:
    done = _run(["git", "rev-parse", "HEAD"], cwd=freeze.REPO_ROOT, capture_output=True, text=True)
    commit = done.stdout.strip()
    if done.returncode != 0 or len(commit) != 40:
        raise Refused("cannot tell which commit this is: git rev-parse HEAD failed")
    return commit


def run_report(args) -> int:
    """The report, from the saved results. It reads; it sends nothing."""
    try:
        results = report.load_results(args.dir)
    except ValueError as error:
        raise Refused(str(error)) from None
    destination = args.report_out or args.dir / "report.md"
    _save(args, destination, report.render(results, commit=_head()))
    print(f"{destination.name}: written")
    return 0


# --- the parser ----------------------------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.functions", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    def measured(name: str, handler, help: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help)
        sub.set_defaults(handler=handler)
        sub.add_argument("--out", type=Path, default=REPORTS, help="where the session record and results are written")
        sub.add_argument("--tenant", required=True, help="the Entra tenant to take tokens from")
        return sub

    sub = measured("upload", run_upload, "put artefact files in artefacts-in, and record them for the checks")
    sub.add_argument("--kind", choices=("new", "changed", "duplicate", "broken"), required=True,
                     help="new: I1; changed: I2; duplicate: I3 (each file twice); broken: I4 (one malformed file)")
    sub.add_argument("--dir", type=Path, default=None, help="the folder of <base64url key>.json files (not for broken)")
    sub.add_argument("--blob-endpoint", required=True, help="the ingestion account's blob endpoint")
    sub.add_argument("--search-endpoint", required=True, help="the search service's endpoint")
    sub.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS,
                     help="the corpus's chunks.jsonl: the expected chunk count of a new or duplicate artefact")
    sub.add_argument("--expect-chunks", type=int, default=None, help="changed: the chunks the shortened file yields")
    sub.add_argument("--gone-text", default=None, help="changed: a phrase only the cut-away text holds")
    sub.add_argument("--new-session", action="store_true",
                     help="start a new session; the old record is kept as functions-session-<id>.json")

    sub = measured("ingest-checks", run_ingest_checks, "I1 to I4, from the session record")
    sub.add_argument("--check", action="append", choices=tuple(_KINDS), default=[],
                     help="a check to run (repeat for several); default: each one the record has an upload for")
    sub.add_argument("--search-endpoint", required=True)
    sub.add_argument("--queue-endpoint", default=None, help="the ingestion account's queue endpoint (I4)")

    sub = measured("tool-checks", run_tool_checks, "T1 to T4 against the gateway, the tool app and Application Insights")
    sub.add_argument("--check", action="append", choices=("t1", "t2", "t3", "t4", "t4-metric"), default=[],
                     help="a check to run (repeat for several); default t1 t2 t3 t4. t4-metric reads T4's metric "
                          "again, from the recorded burst")
    sub.add_argument("--mcp-url", default=None, help="the gateway's MCP URL for the tool")
    sub.add_argument("--app-url", default=None, help="the tool app's own /runtime/webhooks/mcp URL (T3)")
    sub.add_argument("--scope", default=None, help="the gateway app's api://<client id>/.default")
    sub.add_argument("--search-endpoint", default=None, help="the search service's endpoint (T1's direct query)")
    sub.add_argument("--openai-base-url", default=None, help="the account's OpenAI v1 URL (T1's direct query)")
    sub.add_argument("--deployment", default=EMBEDDING_DEPLOYMENT)
    sub.add_argument("--app-id", default=None, help="the Application Insights app id (T4)")
    sub.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    sub.add_argument("--metric-wait", type=float, default=tc.T4_METRIC_WAIT_S,
                     help="seconds to wait for the metric to show the calls (T4)")

    sub = commands.add_parser("report", help="the report, from the saved check results")
    sub.set_defaults(handler=run_report)
    sub.add_argument("--dir", type=Path, default=REPORTS, help="where the results are")
    sub.add_argument("--report-out", type=Path, default=None, help="the report file (default <dir>/report.md)")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Before any HTTPS client exists: Python verifies TLS against certifi's bundle, which a
    # TLS-inspecting proxy's certificate is not in. As app.gateway does; a no-op elsewhere.
    truststore.inject_into_ssl()
    args = _parser().parse_args(argv)
    try:
        return args.handler(args)
    except (Refused, freeze.MeasuredRunRefused, report.IdentifierError, metric.MetricQueryError, metric.LabelsError,
            ic.IndexReadError, ic.UploadError, ic.QueueReadError, tc.DirectSearchError) as error:
        print(error, file=sys.stderr)
        return 1
    except Exception as error:
        # Anything else is a failure, not an answer: keep the frames, but not the message, which
        # can carry a URL or a hostname.
        traceback.print_tb(error.__traceback__)
        print(f"{type(error).__name__}: the message is withheld; it can carry a URL", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
