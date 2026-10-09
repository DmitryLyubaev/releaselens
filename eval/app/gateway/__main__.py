"""`python -m app.gateway <command>`, run from eval/: the gateway test's measured commands.

`failover` runs the fixed workload (45 requests, one every 4 seconds) once, either straight to the
primary account (`--mode direct`) or through the gateway (`--mode gateway`), and writes one record
per request to `<out>/failover-<mode>.jsonl`. The verdict is Task 7's report, from the two files.
A run that crashes or is interrupted still writes the records of the requests that finished, and
raises the error that ended it afterwards.

`smoke` (one call direct, one through the gateway, one at revision 2), `access` (B4), `minute-budget`
(B1), `day-budget` (B2), `metric-totals` (B5) and `report --b3 passed|failed` are the rest of the
harness. `minute-budget` and `day-budget` spend the owner's budget and are measured commands, like
`failover`; `smoke` runs before the freeze, `access` and `metric-totals` spend nothing, and `report`
only reads. Every file they write goes through `report.write`, which refuses a GUID, an Azure
hostname or an `oid` from `GATEWAY_CALLER_LABELS`; that variable stays in the owner's shell.

A measured command refuses to start unless the region signal is frozen in `freeze.json` and the
git tree is clean, and it does so before any token is asked for or any request is sent. It also
refuses an output directory git would see, so a run's records cannot dirty the tree for the next
measured command; `eval/reports/` is ignored, and is the default.

Azure and the gateway are called as the owner, through the Azure CLI, with no key. No token, `oid`,
URL or hostname is printed or recorded.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
import truststore

from app.retrieval.azure_auth import TokenSource

from . import checks, client, freeze, metric, report, workload
from .verdict import failover as failover_verdict

EVAL = Path(__file__).resolve().parents[2]
REPORTS = EVAL / "reports"

DIRECT_SCOPE = "https://ai.azure.com/.default"
DEPLOYMENT = "releaselens-chat-failover-test"
CALLER = "owner"

FREEZE_FILE = freeze.FREEZE_FILE
# Looked up when a command runs, so a test can replace them.
_run = subprocess.run
_now = time.monotonic
_sleep = asyncio.sleep
_sleep_sync = time.sleep


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


BUDGET_DEPLOYMENT = "releaselens-chat"
BUDGET_RECORDS = {"minute-budget": "budget-minute.jsonl", "day-budget": "budget-day.jsonl"}
CHECK_FILES = {"B1": "check-b1.json", "B2": "check-b2.json", "B4": "check-b4.json", "B5": "check-b5.json"}
SMOKE_RECORDS = "smoke.jsonl"
# The owner's calls the gateway answered, found by prefix so that a file renamed in place (the
# runbook's advice after a crash: keep it, give it another name) still counts. B5 sums them all for
# the owner's client totals; B2 counts all but its own run's file as spent before its run. Direct
# runs (`failover-direct*`) are not among them: the gateway never saw those.
GATEWAY_RECORD_PATTERNS = ("failover-gateway*.jsonl", "budget-*.jsonl", "smoke*.jsonl")


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=client.TIMEOUT_S)


def _sync_client() -> httpx.Client:
    return httpx.Client(timeout=client.TIMEOUT_S)


class Refused(Exception):
    """A command that cannot start, with the reason, printed without a traceback."""


def failover(args) -> int:
    """Run the fixed workload once, in one mode, and write its records."""
    # The guard first: nothing is asked of Azure, and nothing is written, until it passes.
    frozen = freeze.require_measurable(_run, FREEZE_FILE)
    freeze.require_output_ignored(args.out, _run)
    if args.mode == "gateway" and not args.scope:
        raise Refused("--mode gateway needs --scope: the gateway app's api://<client id>/.default")
    destination = args.out / f"failover-{args.mode}.jsonl"
    if destination.exists():
        raise Refused(f"{destination.name} already exists in the output directory: a measured run is never "
                      "overwritten; rename it in place, keeping the start of its name, so the tokens it "
                      "records are still counted")

    scope = args.scope or DIRECT_SCOPE
    tokens = TokenSource(scope, args.tenant)
    url = args.base_url.rstrip("/") + "/chat/completions"
    tokens.token()      # fetched before the first request, so az's start-up is not in its latency
    origin = _now()

    def clock() -> float:
        return _now() - origin

    finished: list[client.Record] = []

    async def go() -> list[client.Record]:
        async with _http_client() as http:
            async def send(seq: int) -> client.Record:
                # A fresh read each time: the source refreshes a token that is about to lapse.
                record = await client.send_one(http, url, tokens.token(), args.deployment, seq, caller=CALLER,
                                               region_signal=frozen.region_signal, clock=clock, sleep=_sleep)
                finished.append(record)
                return record

            return await workload.run(send, clock=clock, sleep=_sleep)

    try:
        records = asyncio.run(go())
    except BaseException:
        # A crash or Ctrl+C mid-run still keeps what the gateway already counted, as `_budget` does:
        # B2's floor and B5's totals read these records. The error that ended the run is raised
        # again after the save; a run that finished no request writes nothing.
        if finished:
            _save(args, destination, _lines(sorted(finished, key=lambda r: r.seq)))
        raise
    _save(args, destination, _lines(records))
    statuses = Counter("none" if r.status is None else r.status for r in records)
    print(f"{destination.name}: {len(records)} records; statuses {dict(sorted(statuses.items(), key=str))}")
    return 0


def _lines(records: list[client.Record]) -> str:
    return "".join(json.dumps(asdict(r)) + "\n" for r in records)


# --- the rest of the harness ------------------------------------------------------------------

def _forbidden(args) -> list[str]:
    """Strings no written file may hold: what this command was given, and the labels' oids."""
    items = [getattr(args, name, None) for name in ("tenant", "app_id", "scope")]
    for name in ("base_url", "direct_url", "gateway_url"):
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


def _announce(result: checks.CheckResult) -> None:
    print(f"{result.check}: {'pass' if result.passed else 'fail'} - {result.detail}")


def _sender(http: httpx.Client, url: str, token: Callable[[], str] | None, deployment: str,
            log: list[dict] | None = None) -> checks.Send:
    """One call as the owner (or with no token): the reply, and one line in `log` that holds numbers only."""
    lock = threading.Lock()     # B1's burst sends from many threads at once: one line, one seq, each

    def send() -> checks.Reply:
        headers = {} if token is None else {"Authorization": f"Bearer {token()}"}
        try:
            response = http.post(url, json=client.chat_body(deployment), headers=headers)
        except httpx.TransportError:
            reply = checks.Reply(None)
        else:
            prompt, completion = client._usage(response)
            reply = checks.Reply(response.status_code, dict(response.headers), response.text, prompt, completion)
        if log is not None:
            with lock:
                log.append({"seq": len(log), "status": reply.status, "model_called": reply.model_called,
                            "retry_after": reply.header("retry-after"), "prompt_tokens": reply.prompt_tokens,
                            "completion_tokens": reply.completion_tokens, "caller": CALLER})
        return reply

    return send


def _url(base_url: str) -> str:
    return base_url.rstrip("/") + "/chat/completions"


def _finish(args, results: list[checks.CheckResult], filename: str, as_list: bool = False) -> int:
    """Write the results, then show them: nothing is printed that the writer did not pass."""
    saved = [asdict(r) for r in results] if as_list else asdict(results[0])
    _save(args, args.out / filename, json.dumps(saved, indent=2) + "\n")
    for result in results:
        _announce(result)
    return 0 if all(r.passed for r in results) else 1


def _budget(args, command: str, check_file: str, run_check: Callable[[checks.Send], checks.CheckResult]) -> int:
    # The guard first, as for `failover`: nothing is asked of Azure, and nothing is written, until it passes.
    freeze.require_measurable(_run, FREEZE_FILE)
    freeze.require_output_ignored(args.out, _run)
    records_path = args.out / BUDGET_RECORDS[command]
    for path in (records_path, args.out / check_file):
        if path.exists():
            raise Refused(f"{path.name} already exists in the output directory: a measured run is never "
                          "overwritten; rename it in place, keeping the start of its name, so the tokens "
                          "it records are still counted")
    tokens = TokenSource(args.scope, args.tenant)
    tokens.token()      # one token before any send: a burst's threads would otherwise each start `az`
    log: list[dict] = []
    try:
        with _sync_client() as http:
            result = run_check(_sender(http, _url(args.base_url), tokens.token, args.deployment, log))
    finally:
        # A crash or Ctrl+C mid-run still keeps what was paid for: B2 cannot be re-run until 00:00 UTC.
        if log:
            _save(args, records_path, "".join(json.dumps(line) + "\n" for line in log))
    return _finish(args, [result], check_file)


def run_minute_budget(args) -> int:
    """B1: one burst of requests at once through the gateway, which the minute budget must refuse in part."""
    return _budget(args, "minute-budget", CHECK_FILES["B1"], checks.minute_budget)


def _gateway_records(out: Path, *, leave_out: str | None = None) -> list[Path]:
    """The records of the owner's gateway calls in `out`, by prefix, each file once."""
    found = {path for pattern in GATEWAY_RECORD_PATTERNS for path in out.glob(pattern) if path.is_file()}
    return sorted(path for path in found if path.name != leave_out)


def _recorded_today(out: Path) -> int:
    """Tokens in the owner's gateway 200s recorded in this directory since 00:00 UTC today.

    Not the day-budget run's own file: its 200s are counted as the run's own, and a run that finds
    it already there refuses to start.
    """
    midnight = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    total = 0
    for path in _gateway_records(out, leave_out=BUDGET_RECORDS["day-budget"]):
        if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < midnight:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row["status"] == 200:
                total += row["prompt_tokens"] + row["completion_tokens"]
    return total


def run_day_budget(args) -> int:
    """B2: requests through the gateway, past the minute budget's resets, until the day's tokens are spent."""
    return _budget(args, "day-budget", CHECK_FILES["B2"], lambda send: checks.day_budget(
        send, sleep=_sleep_sync, recorded_tokens=_recorded_today(args.out)))


def run_access(args) -> int:
    """B4: no token, and the owner's token for another audience."""
    freeze.require_output_ignored(args.out, _run)
    tokens = TokenSource(DIRECT_SCOPE, args.tenant)
    url = _url(args.base_url)
    with _sync_client() as http:
        result = checks.access(_sender(http, url, None, args.deployment),
                               _sender(http, url, tokens.token, args.deployment))
    return _finish(args, [result], CHECK_FILES["B4"])


def run_smoke(args) -> int:
    """One call direct, one through the gateway, one at revision 2: before the freeze, so no guard."""
    freeze.require_output_ignored(args.out, _run)
    direct_tokens = TokenSource(DIRECT_SCOPE, args.tenant)
    gateway_tokens = TokenSource(args.scope, args.tenant)
    log: list[dict] = []        # the two calls the gateway answered: their usage is in the metric (B5)
    try:
        with _sync_client() as http:
            results = checks.smoke(
                _sender(http, _url(args.direct_url), direct_tokens.token, args.deployment),
                _sender(http, _url(args.gateway_url), gateway_tokens.token, args.deployment, log),
                _sender(http, checks.rev2_url(args.gateway_url), gateway_tokens.token, args.deployment, log),
            )
    finally:
        if log:
            _save(args, args.out / SMOKE_RECORDS, "".join(json.dumps(line) + "\n" for line in log))
    return _finish(args, results, "check-smoke.json", as_list=True)


def _client_totals(paths: list[Path], extra: list[str]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            totals[row["caller"]] = totals.get(row["caller"], 0) + row["prompt_tokens"] + row["completion_tokens"]
    entered: set[str] = set()
    for item in extra:
        label, _, number = item.partition("=")
        if not label or not number.isdigit():
            raise Refused("--client-total takes LABEL=N, N a whole number of tokens")
        if label in totals or label in entered:
            raise Refused(f"--client-total {label}: that caller already has recorded totals (or was entered "
                          "twice); a figure entered by hand is only for a caller with no records")
        totals[label] = int(number)
        entered.add(label)
    return totals


def run_metric_totals(args) -> int:
    """B5: the metric's tokens per caller against what the clients recorded."""
    freeze.require_output_ignored(args.out, _run)
    try:
        metric.check_since(args.since)
    except ValueError as error:
        raise Refused(str(error)) from None
    labels = metric.labels_from_env(os.environ)
    paths = _gateway_records(args.out)
    recorded = _client_totals(paths, args.client_total)
    if not recorded:
        raise Refused("no client records: run the gateway failover and the budget checks first, "
                      "or give --client-total LABEL=N")
    token = TokenSource(metric.SCOPE, args.tenant).token()
    with _sync_client() as http:
        measured = metric.totals(metric.query(args.app_id, token, args.since, http), labels)
    result = metric.matches(recorded, measured)
    if args.client_total:
        by_hand = ", ".join(sorted(item.partition("=")[0] for item in args.client_total))
        result = replace(result, detail=f"{result.detail}; {by_hand}: from the workflow log, entered by hand")
    _save(args, args.out / CHECK_FILES["B5"], json.dumps(asdict(result), indent=2) + "\n")
    print(f"clients' totals: {dict(sorted(recorded.items()))}")
    print(f"metric totals:   {dict(sorted(measured.items()))}")
    _announce(result)
    return 0 if result.passed else 1


def _load_run(path: Path) -> list[client.Record]:
    if not path.exists():
        raise Refused(f"{path.name} is missing: run `failover` in both modes first")
    try:
        return [client.Record(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]
    except (ValueError, TypeError):
        raise Refused(f"{path.name} is not a failover run's records") from None


def _load_check(path: Path) -> checks.CheckResult | None:
    if not path.exists():
        return None
    try:
        return checks.CheckResult(**json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, TypeError):
        raise Refused(f"{path.name} is not a check result") from None


def _head() -> str:
    done = _run(["git", "rev-parse", "HEAD"], cwd=freeze.REPO_ROOT, capture_output=True, text=True)
    commit = done.stdout.strip()
    if done.returncode != 0 or len(commit) != 40:
        raise Refused("cannot tell which commit this is: git rev-parse HEAD failed")
    return commit


def run_report(args) -> int:
    """The report, from both runs' records and the checks' results. It reads; it sends nothing."""
    frozen = freeze.load(FREEZE_FILE)
    before = _load_run(args.dir / "failover-direct.jsonl")
    after = _load_run(args.dir / "failover-gateway.jsonl")
    results = [r for name in CHECK_FILES.values() if (r := _load_check(args.dir / name)) is not None]
    text = report.render(failover_verdict(before, after), before, after, results, b3=args.b3, commit=_head(),
                         region_signal=frozen.region_signal)
    destination = args.report_out or args.dir / "report.md"
    _save(args, destination, text)
    print(f"{destination.name}: written")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.gateway", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    sub = commands.add_parser("failover", help="run the failover workload once, direct or through the gateway")
    sub.set_defaults(handler=failover)
    sub.add_argument("--mode", choices=("direct", "gateway"), required=True)
    sub.add_argument("--out", type=Path, default=REPORTS, help="where to write failover-<mode>.jsonl")
    sub.add_argument("--tenant", required=True, help="the Entra tenant to take tokens from")
    sub.add_argument("--base-url", required=True,
                     help="the account's OpenAI v1 URL (direct) or the gateway's (gateway), ending in /openai/v1/")
    sub.add_argument("--deployment", default=DEPLOYMENT)
    sub.add_argument("--scope", default=None,
                     help=f"the token scope; direct mode defaults to {DIRECT_SCOPE}, gateway mode needs it")

    def checked(name: str, handler, help: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help)
        sub.set_defaults(handler=handler)
        sub.add_argument("--out", type=Path, default=REPORTS, help="where records and results are written")
        sub.add_argument("--tenant", required=True, help="the Entra tenant to take tokens from")
        return sub

    for name, handler, help in (
        ("minute-budget", run_minute_budget, "B1: one burst at once, which the gateway's minute budget must refuse in part"),
        ("day-budget", run_day_budget, "B2: requests until the gateway's daily budget answers 403"),
    ):
        sub = checked(name, handler, help)
        sub.add_argument("--base-url", required=True, help="the gateway's URL, ending in /openai/v1/")
        sub.add_argument("--scope", required=True, help="the gateway app's api://<client id>/.default")
        sub.add_argument("--deployment", default=BUDGET_DEPLOYMENT)

    sub = checked("access", run_access, "B4: a call with no token, and one with a token for another audience")
    sub.add_argument("--base-url", required=True, help="the gateway's URL, ending in /openai/v1/")
    sub.add_argument("--deployment", default=BUDGET_DEPLOYMENT)

    sub = checked("smoke", run_smoke, "one call direct, one through the gateway, one at revision 2")
    sub.add_argument("--direct-url", required=True, help="the primary account's OpenAI v1 URL")
    sub.add_argument("--gateway-url", required=True, help="the gateway's URL, ending in /openai/v1/")
    sub.add_argument("--scope", required=True, help="the gateway app's api://<client id>/.default")
    sub.add_argument("--deployment", default=BUDGET_DEPLOYMENT)

    sub = checked("metric-totals", run_metric_totals, "B5: the usage metric against the clients' own totals")
    sub.add_argument("--app-id", required=True, help="the Application Insights app id")
    sub.add_argument("--since", required=True, help="the session's start, UTC: 2026-10-09T01:30:00Z")
    sub.add_argument("--client-total", action="append", default=[], metavar="LABEL=N",
                     help="tokens a caller's client spent but did not record to a file, entered by hand")

    sub = commands.add_parser("report", help="the report, from both runs and the checks' results")
    sub.set_defaults(handler=run_report)
    sub.add_argument("--b3", choices=("passed", "failed"), required=True,
                     help="the workflow run's outcome, entered by hand: this harness does not measure it")
    sub.add_argument("--dir", type=Path, default=REPORTS, help="where the runs' records and results are")
    sub.add_argument("--report-out", type=Path, default=None, help="the report file (default <dir>/report.md)")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Before any HTTPS client exists: Python verifies TLS against certifi's bundle, which a
    # TLS-inspecting proxy's certificate is not in. As app.retrieval does; a no-op elsewhere.
    truststore.inject_into_ssl()
    args = _parser().parse_args(argv)
    try:
        return args.handler(args)
    except (Refused, freeze.MeasuredRunRefused, report.IdentifierError, metric.MetricQueryError,
            metric.LabelsError) as error:
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
