"""`python -m app.gateway <command>`, run from eval/: the gateway test's measured commands.

`failover` runs the fixed workload (45 requests, one every 4 seconds) once, either straight to the
primary account (`--mode direct`) or through the gateway (`--mode gateway`), and writes one record
per request to `<out>/failover-<mode>.jsonl`. The verdict is Task 7's report, from the two files.

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
import subprocess
import sys
import time
import traceback
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import httpx
import truststore

from app.retrieval.azure_auth import TokenSource

from . import client, freeze, workload

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


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=client.TIMEOUT_S)


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
                      "overwritten; move it away first")

    scope = args.scope or DIRECT_SCOPE
    tokens = TokenSource(scope, args.tenant)
    url = args.base_url.rstrip("/") + "/chat/completions"
    tokens.token()      # fetched before the first request, so az's start-up is not in its latency
    origin = _now()

    def clock() -> float:
        return _now() - origin

    async def go() -> list[client.Record]:
        async with _http_client() as http:
            async def send(seq: int) -> client.Record:
                # A fresh read each time: the source refreshes a token that is about to lapse.
                return await client.send_one(http, url, tokens.token(), args.deployment, seq, caller=CALLER,
                                             region_signal=frozen.region_signal, clock=clock, sleep=_sleep)

            return await workload.run(send, clock=clock, sleep=_sleep)

    records = asyncio.run(go())

    args.out.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(json.dumps(asdict(r)) + "\n" for r in records), encoding="utf-8")
    statuses = Counter("none" if r.status is None else r.status for r in records)
    print(f"{destination.name}: {len(records)} records; statuses {dict(sorted(statuses.items(), key=str))}")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    # Before any HTTPS client exists: Python verifies TLS against certifi's bundle, which a
    # TLS-inspecting proxy's certificate is not in. As app.retrieval does; a no-op elsewhere.
    truststore.inject_into_ssl()
    args = _parser().parse_args(argv)
    try:
        return args.handler(args)
    except (Refused, freeze.MeasuredRunRefused) as error:
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
