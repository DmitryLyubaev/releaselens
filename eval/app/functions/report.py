"""The report: the eight checks' outcomes as markdown, and the one way the harness writes a file.

`write` is `app.gateway.report.write`, the gateway harness's writer: it refuses text that holds a
GUID, an Azure hostname or any string the caller names, and says which kind, never the thing. Every
file this harness writes goes through it. `render` puts the checks' results, and nothing else the
runs saw, into markdown: labels, counts, statuses and artefact IDs.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from app.gateway.checks import CheckResult
from app.gateway.report import IdentifierError, write

__all__ = ["CHECKS", "IdentifierError", "check_file", "load_results", "render", "write"]

CHECKS = ("I1", "I2", "I3", "I4", "T1", "T2", "T3", "T4")
_TITLES = {
    "I1": "a new artefact becomes searchable",
    "I2": "a changed artefact replaces itself",
    "I3": "duplicates are harmless",
    "I4": "broken input goes to the poison queue",
    "T1": "the tool runs the same search as project 2",
    "T2": "the gateway refuses strangers",
    "T3": "no way round the gateway",
    "T4": "the per-caller limit holds",
}

T3_NOTE = (
    "T3 sends the call straight to the tool app twice: with no token, and with the owner's gateway token. "
    "The owner is not assigned to `releaselens-search-tool`, so the owner cannot get a token for the tool "
    "app's own audience; a call carrying a token for that audience was not tested."
)


def check_file(check: str) -> str:
    return f"check-{check.lower()}.json"


def load_results(directory: Path) -> list[CheckResult]:
    """The results saved in `directory`, one `check-<id>.json` each, in the checks' order."""
    results = []
    for check in CHECKS:
        path = directory / check_file(check)
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            results.append(CheckResult(**data))
        except (ValueError, TypeError):
            raise ValueError(f"{path.name} is not a check result") from None
    return results


def render(results: Sequence[CheckResult], *, commit: str) -> str:
    by_name = {r.check: r for r in results}
    passed = sum(1 for name in CHECKS if name in by_name and by_name[name].passed)
    lines = [
        "# Azure Functions: ingestion and a search tool: measured checks",
        "",
        f"Commit `{commit}`. {passed} of {len(CHECKS)} checks passed.",
        "",
        "Each pass rule was fixed in the spec (section 7) before any live run. A failed check is a result, "
        "and is reported as one.",
        "",
        "## Ingestion",
        "",
    ]
    for name in CHECKS:
        if name == "T1":
            lines += ["", "## The tool", ""]
        if name in by_name:
            lines.append(f"- {name}: {'pass' if by_name[name].passed else 'fail'} ({_TITLES[name]}): {by_name[name].detail}")
        else:
            lines.append(f"- {name}: not run ({_TITLES[name]})")
    lines += ["", T3_NOTE, ""]
    return "\n".join(lines)
