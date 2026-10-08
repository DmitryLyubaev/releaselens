"""The report (spec §7.1, §7.3, §7.4), and the one way the harness writes a file.

`render` puts the verdict, both runs' counts and latencies, B1 to B5 and the frozen choice into
markdown, using labels only. `write` is the harness's only writer: it refuses text that holds a
GUID, an Azure hostname or any string the caller names, and says which kind it found, never the
thing itself. The report, the check results and every JSONL file go through it.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from .checks import HOSTNAME, CheckResult
from .client import Record, signals_disagree
from .verdict import Verdict

_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)

CHECK_ORDER = ("B1", "B2", "B3", "B4", "B5")


class IdentifierError(Exception):
    """A file's text holds an identifier; the message names the kind, never the identifier."""


def write(path: Path, text: str, forbidden: Sequence[str] = ()) -> None:
    """Write `text` to `path`, unless it holds a GUID, an Azure hostname or a `forbidden` string."""
    if _GUID.search(text):
        raise IdentifierError(f"{path.name} was not written: the text holds a GUID")
    if HOSTNAME.search(text):
        raise IdentifierError(f"{path.name} was not written: the text holds an Azure hostname")
    lowered = text.lower()
    if any(item and item.lower() in lowered for item in forbidden):
        raise IdentifierError(f"{path.name} was not written: the text holds a string on the forbidden list")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _percentile(sorted_values: list[float], percent: int) -> str:
    """Nearest rank, in whole milliseconds; `n/a` when there is nothing to rank."""
    if not sorted_values:
        return "n/a"
    rank = max(1, math.ceil(percent / 100 * len(sorted_values)))
    return f"{sorted_values[rank - 1]:.0f} ms"


def _run_lines(name: str, records: list[Record], *, compare_signals: bool = False) -> list[str]:
    failed = [r for r in records if r.status != 200]
    statuses = Counter("none" if r.status is None else str(r.status) for r in failed)
    shown = ", ".join(f"{status} x{count}" for status, count in sorted(statuses.items())) or "none"
    latencies = sorted(r.latency_ms for r in records)
    secondary = sum(r.status == 200 and r.region == "secondary" for r in records)
    primary = sum(r.status == 200 and r.region == "primary" for r in records)
    signals = []
    if compare_signals:
        # Only a run through the gateway has both signals: the label is the gateway's own header.
        carrying = [r for r in records if r.x_ms_region is not None or r.backend_label is not None]
        signals = [f"- the two region signals (`x-ms-region` and `x-releaselens-backend`) disagreed on "
                   f"{sum(signals_disagree(r) for r in carrying)} of {len(carrying)} responses that carried either"]
    return [
        f"**{name} run**",
        f"- succeeded {len(records) - len(failed)} of {len(records)}; failed {len(failed)} of {len(records)}",
        f"- failure statuses: {shown}",
        f"- answered by the primary: {primary}; by Southeast Asia: {secondary}",
        *signals,
        f"- latency, all {len(records)} requests including failures: p50 {_percentile(latencies, 50)}, "
        f"p95 {_percentile(latencies, 95)}",
        "",
    ]


NOT_TESTED_LIVE = (
    "A valid token for the gateway that lacks `Gateway.Invoke` was not tested live: there is no second "
    "user to test it with, and none was created. With assignment required on the Entra app, such a token "
    "cannot be issued at all."
)
GLOBAL_STANDARD = (
    "\"Answered by Southeast Asia\" means the Southeast Asia account served the request, not that the "
    "prompt was processed there: a Global Standard deployment may process a prompt in any Azure region."
)


def render(
    verdict: Verdict, before: list[Record], after: list[Record], checks: Sequence[CheckResult], *,
    b3: str, commit: str, region_signal: str,
) -> str:
    """The report as markdown. `b3` is `passed` or `failed`, entered by the owner from the workflow run."""
    by_name = {c.check: c for c in checks}
    lines = [
        "# API Management AI gateway: measured test",
        "",
        "## Test 1: failover",
        "",
        f"Verdict: {verdict.outcome}",
        "",
        verdict.reason[:1].upper() + verdict.reason[1:] + ".",
        "",
        *_run_lines("Before (direct to the primary)", before),
        *_run_lines("After (through the gateway)", after, compare_signals=True),
    ]
    if verdict.after_failure_statuses:
        shown = ", ".join("none" if s is None else str(s) for s in verdict.after_failure_statuses)
        lines += [f"After-run failure statuses, in request order: {shown}.", ""]
    lines += [
        "Latencies are descriptive only; they decide nothing.",
        "",
        GLOBAL_STANDARD,
        "",
        "## Test 2: budgets and access",
        "",
    ]
    for name in CHECK_ORDER:
        if name == "B3":
            outcome = "pass" if b3 == "passed" else "fail"
            lines.append(f"- B3: {outcome} (entered by hand from the workflow run; this harness did not measure it)")
        elif name in by_name:
            lines.append(f"- {name}: {'pass' if by_name[name].passed else 'fail'} ({by_name[name].detail})")
        else:
            lines.append(f"- {name}: not run")
    lines += [
        "",
        "**Not tested live.** " + NOT_TESTED_LIVE,
        "",
        "## What the run used",
        "",
        f"- The region signal frozen before the first measured request (freeze.json): `{region_signal}`.",
        f"- The commit: `{commit}`, HEAD when this report was made. Measured commands refuse a tree with "
        "uncommitted changes, so it is the commit that ran unless one was made since.",
        "- Callers appear as labels. No identifier, hostname or account name is in this report.",
        "",
    ]
    return "\n".join(lines)
