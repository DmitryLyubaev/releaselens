"""Closed evidence schema. Untrusted text never becomes a report field."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import re
from types import MappingProxyType
from collections.abc import Mapping

CHECKS = frozenset({"W1", "W2", "W3", "W4", "W5", "W6a", "W6b"})
REQUIRED_MATCHES = {
    "W1": ("token_usable", "ask_succeeded", "answer_nonempty", "owner_metric_match"),
    "W2": ("anonymous_main_refused", "anonymous_staging_refused", "forged_headers_refused", "deployment_health_only", "offline_edges_passed"),
    "W3": ("initial_lifetime_elapsed", "expiry_advanced", "owner_audience_match", "ask_succeeded", "answer_nonempty"),
    "W4": ("baseline_healthy", "staging_healthy", "approval_recorded", "swap_succeeded", "main_candidate", "staging_baseline", "slot_labels_retained"),
    "W5": ("fixture_health_503", "stage_failed", "promotion_rejected", "no_swap", "main_unchanged"),
    "W6a": ("intended_settings_before", "settings_retained", "main_owner_signin", "staging_owner_signin"),
    "W6b": ("webapp_group_absent", "gateway_group_absent", "plan_site_slot_absent", "deleted_apim_absent", "bootstrap_intact"),
}
STATUS_KEYS = frozenset({"health_main", "health_staging", "ask", "anonymous_main", "anonymous_staging", "forged_headers", "deployment_health", "deployment_index", "deployment_ask", "deployment_session", "refresh", "session_initial", "session_refreshed", "fixture_health", "main_before", "main_after"})
COUNT_KEYS = frozenset({"gateway_calls", "owner_metric_rows", "requests", "attempts", "swap_calls"})
TIME_KEYS = frozenset({"started_at", "ended_at", "initial_expiry", "refreshed_expiry", "measured_at", "approval_at", "created_at"})
VERSION = re.compile(r"(?:[a-f0-9]{40}\.[1-9][0-9]*\.[1-9][0-9]*\.(?:seed|stage|fixture)|[0-9]+\.[0-9]+\.[0-9]+(?:\+[a-f0-9]{40})?)\Z")


class ContractRefused(ValueError):
    def __init__(self): super().__init__("invalid_contract")


def utc_time(value: str) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value): return False
    try: return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset().total_seconds() == 0
    except (ValueError, AttributeError): return False


@dataclass(frozen=True, repr=False)
class Health:
    http_status: int
    status: str | None = None
    version: str | None = None
    slot: str | None = None
    configuration_valid: bool = False
    caller: str | None = None

    def __repr__(self): return f"Health(http_status={self.http_status})"


@dataclass(frozen=True)
class SessionSnapshot:
    token_present: bool = False
    token_parseable: bool = False
    tenant_matches: bool = False
    audience_matches: bool = False
    owner_matches: bool = False
    scope_matches: bool = False
    role_matches: bool = False
    usable: bool = False
    expires_at_unix_seconds: int | None = None
    http_status: int | None = None


@dataclass(frozen=True)
class AskObservation:
    status: int
    answer_nonempty: bool


@dataclass(frozen=True)
class Probe:
    status: int
    signin_redirect: bool


@dataclass(frozen=True)
class Observation:
    check: str
    statuses: Mapping[str, int | None] = field(default_factory=dict)
    matches: Mapping[str, bool] = field(default_factory=dict)
    counts: Mapping[str, int | float] = field(default_factory=dict)
    times: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if self.check not in CHECKS: raise ContractRefused()
        groups = (self.statuses, self.matches, self.counts, self.times)
        if not all(isinstance(group, Mapping) for group in groups): raise ContractRefused()
        if set(self.statuses) - STATUS_KEYS or set(self.matches) - set(REQUIRED_MATCHES[self.check]) or set(self.counts) - COUNT_KEYS or set(self.times) - TIME_KEYS: raise ContractRefused()
        if any(value is not None and (type(value) is not int or not 100 <= value <= 599) for value in self.statuses.values()): raise ContractRefused()
        if any(type(value) is not bool for value in self.matches.values()): raise ContractRefused()
        if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in self.counts.values()): raise ContractRefused()
        if any(not utc_time(value) for value in self.times.values()): raise ContractRefused()
        for name in ("statuses", "matches", "counts", "times"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    def to_dict(self):
        return {"check": self.check, **{name: dict(getattr(self, name)) for name in ("statuses", "matches", "counts", "times")}}


@dataclass(frozen=True)
class CheckResult:
    check: str
    verdict: str
    evidence: str
    observations: tuple[Observation, ...]
    commit: str
    measured_at: str

    def __post_init__(self):
        if self.check not in CHECKS or self.verdict not in {"pass", "fail", "not run"} or self.evidence not in {"live", "offline", "mixed", "workflow", "none"}: raise ContractRefused()
        if not isinstance(self.commit, str) or not re.fullmatch(r"[a-f0-9]{40}", self.commit) or not utc_time(self.measured_at): raise ContractRefused()
        if not isinstance(self.observations, tuple) or any(not isinstance(item, Observation) or item.check != self.check for item in self.observations): raise ContractRefused()
        if self.verdict == "not run" and self.observations: raise ContractRefused()

    def to_dict(self):
        return {"check": self.check, "verdict": self.verdict, "evidence": self.evidence, "observations": [item.to_dict() for item in self.observations], "commit": self.commit, "measured_at": self.measured_at}
