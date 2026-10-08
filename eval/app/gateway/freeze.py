"""What is fixed before a measured run, and the guard that holds it (spec §7.4).

The region signal the rule reads is chosen once, by the smoke test, and committed in
`freeze.json`. A measured command (`failover`, `minute-budget`, `day-budget`) refuses to run
while it is null, and refuses to run on a tree with uncommitted changes, so that what the report
cites as the commit is what actually ran.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

FREEZE_FILE = Path(__file__).with_name("freeze.json")
REPO_ROOT = Path(__file__).resolve().parents[3]
SIGNALS = ("x-ms-region", "x-releaselens-backend")

RegionSignal = Literal["x-ms-region", "x-releaselens-backend"]
Run = Callable[..., subprocess.CompletedProcess]


class MeasuredRunRefused(Exception):
    """A measured command must not start; the message says why."""


class FreezeError(MeasuredRunRefused):
    """The region signal is not frozen, or `freeze.json` cannot be read."""


class DirtyTreeError(MeasuredRunRefused):
    """The working tree has changes that no commit holds."""


@dataclass(frozen=True)
class Freeze:
    region_signal: RegionSignal


def load(path: Path = FREEZE_FILE) -> Freeze:
    name = path.name
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FreezeError(f"{name} does not exist: the region signal is not frozen") from None
    except (OSError, ValueError) as error:
        raise FreezeError(f"{name} cannot be read ({type(error).__name__}): the region signal is not frozen") from None
    signal = data.get("region_signal") if isinstance(data, dict) else None
    if signal is None:
        raise FreezeError(f"{name} has no region signal (it is null): freeze the choice the smoke test made, "
                          "commit it, then run")
    if signal not in SIGNALS:
        raise FreezeError(f"{name} names an unknown region signal; it must be one of {', '.join(SIGNALS)}")
    return Freeze(cast(RegionSignal, signal))


def require_clean_tree(run: Run = subprocess.run, root: Path = REPO_ROOT) -> None:
    """Raise unless `git status --porcelain` is empty, from the repository root."""
    try:
        done = run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True)
    except OSError as error:
        raise DirtyTreeError(f"cannot tell whether the tree is clean: git did not run ({type(error).__name__})") from None
    if done.returncode != 0:
        raise DirtyTreeError(f"cannot tell whether the tree is clean: git status exited {done.returncode}")
    changed = [line for line in done.stdout.splitlines() if line.strip()]
    if changed:
        raise DirtyTreeError(f"the working tree is not clean ({len(changed)} changed or untracked paths): "
                             "commit or stash them, so the report can cite the commit that ran")


def require_output_ignored(out: Path, run: Run = subprocess.run, root: Path = REPO_ROOT) -> None:
    """Raise unless `out` is git-ignored, so writing a run's records cannot dirty the tree.

    `eval/reports/` is ignored, so the default passes. A directory outside the repository cannot
    dirty it either.
    """
    out = out.resolve()
    if not out.is_relative_to(root.resolve()):
        return
    done = run(["git", "check-ignore", "-q", "--", str(out / "probe")], cwd=root, capture_output=True, text=True)
    if done.returncode != 0:
        raise DirtyTreeError("the output directory is not git-ignored: writing a run's records there would "
                             "dirty the tree for the next measured command; use eval/reports/")


def require_measurable(run: Run = subprocess.run, path: Path = FREEZE_FILE, root: Path = REPO_ROOT) -> Freeze:
    """Everything a measured command needs before its first request: the frozen signal, a clean tree."""
    frozen = load(path)
    require_clean_tree(run, root)
    return frozen
