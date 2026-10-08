"""The failover verdict, by the rule fixed before any run (spec §7.2).

- Validity: fewer than 14 of the 45 before-run requests failing (under 30%) means the test did not
  stress the primary, and the verdict is inconclusive, whatever the after run shows.
- Held: at least 43 of the 45 after-run requests succeed and at least one of them was answered by
  Southeast Asia.
- Did not hold: otherwise, with each failure's status.

A request succeeds when its status is 200. The thresholds are absolute counts, so a run of fewer
than 45 requests can only be inconclusive or fail to hold, never pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .client import Record

MIN_BEFORE_FAILURES = 14
MIN_AFTER_SUCCESSES = 43

Outcome = Literal["inconclusive", "held", "did not hold"]


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    reason: str
    before_total: int
    before_failures: int
    after_total: int
    after_successes: int
    after_secondary: int                            # successes answered by the secondary
    after_failure_statuses: tuple[int | None, ...]  # each after-run failure's status, in sequence order


def _ok(record: Record) -> bool:
    return record.status == 200


def failover(before: list[Record], after: list[Record]) -> Verdict:
    before_failures = sum(not _ok(r) for r in before)
    successes = [r for r in after if _ok(r)]
    secondary = sum(r.region == "secondary" for r in successes)
    statuses = tuple(r.status for r in sorted(after, key=lambda r: r.seq) if not _ok(r))

    def verdict(outcome: Outcome, reason: str) -> Verdict:
        return Verdict(outcome, reason, len(before), before_failures, len(after), len(successes), secondary, statuses)

    if before_failures < MIN_BEFORE_FAILURES:
        return verdict("inconclusive",
                       f"only {before_failures} of {len(before)} before-run requests failed; at least "
                       f"{MIN_BEFORE_FAILURES} are needed to show the primary was stressed")
    problems = []
    if len(successes) < MIN_AFTER_SUCCESSES:
        problems.append(f"{len(successes)} of {len(after)} after-run requests succeeded; at least "
                        f"{MIN_AFTER_SUCCESSES} are needed")
    if secondary < 1:
        problems.append("no after-run request was answered by the Southeast Asia region")
    if problems:
        return verdict("did not hold", "; ".join(problems))
    return verdict("held",
                   f"{len(successes)} of {len(after)} after-run requests succeeded, {secondary} of them "
                   "answered by the Southeast Asia region")
