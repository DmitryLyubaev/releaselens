"""The budget and access checks (B1, B2, B4) and the smoke check (spec §7.3, §7.4, §10).

Each check is given a `send` that makes one call and returns a `Reply`; the checks know nothing of
URLs, tokens or HTTP, so they run on fixtures. A `Reply` holds the status, the headers and the
body of one response, and the checks put none of the headers' or body's values into a result's
`detail`, except the two region signals, and those only when they carry no hostname.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from .rule import advised_wait_ms

MAX_MINUTE_REQUESTS = 60     # about 330 tokens each against 10,000 a minute: a 429 comes by the 31st
MAX_DAY_REQUESTS = 400       # about 330 tokens each against 50,000 a day: a 403 comes by the 152nd
DAY_BUDGET_TOKENS = 50_000   # the policy's daily quota (spec §4.1)
# A 403 counts as the daily budget's only once this much has been recorded: 90% of the quota, which
# allows for the policy's estimate of each prompt being more than the model's own count.
DAY_FLOOR_TOKENS = 45_000
DEFAULT_WAIT_S = 60          # a 429 that does not say how long: one minute window

REGION_HEADER = "x-ms-region"
BACKEND_HEADER = "x-releaselens-backend"

# A hostname, for the smoke check and the report's scan: any host under the three domains the
# gateway, the accounts and storage can reveal themselves in.
HOSTNAME = re.compile(r"\.(?:azure\.com|azure-api\.net|windows\.net)(?![A-Za-z0-9-])", re.IGNORECASE)

REV2_SEGMENT = ";rev=2"


@dataclass(frozen=True)
class CheckResult:
    check: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Reply:
    """One response: `status` is None when there was none (a timeout, a dropped connection)."""

    status: int | None
    headers: Mapping[str, str] = field(default_factory=dict)
    body: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def header(self, name: str) -> str | None:
        for key, value in self.headers.items():
            if key.lower() == name:
                return value
        return None

    @property
    def model_called(self) -> bool:
        """A model answered: the response carries x-ms-region (as the client records it)."""
        return self.header(REGION_HEADER) is not None


Send = Callable[[], Reply]


def _describe(reply: Reply) -> str:
    return "no response" if reply.status is None else f"status {reply.status}"


def minute_budget(send: Send, *, max_requests: int = MAX_MINUTE_REQUESTS) -> CheckResult:
    """B1: send until one is refused; pass on a 429 with Retry-After and no model call."""
    for sent in range(1, max_requests + 1):
        reply = send()
        if reply.status == 200:
            continue
        if reply.status != 429:
            return CheckResult("B1", False, f"request {sent} got {_describe(reply)}, not a 429")
        if reply.model_called:
            return CheckResult("B1", False, f"request {sent} got a 429 from a model (x-ms-region present), "
                                            "not from the gateway's budget")
        if reply.header("retry-after") is None:
            return CheckResult("B1", False, f"request {sent} got a 429 without Retry-After")
        return CheckResult("B1", True, f"request {sent} was refused with a 429 that has Retry-After and no model "
                                       "call (the client side only: the Application Insights request record was "
                                       "not read by the harness)")
    return CheckResult("B1", False, f"{max_requests} requests were sent and none was refused with a 429")


def day_budget(send: Send, *, sleep: Callable[[float], None] = time.sleep, recorded_tokens: int = 0,
               max_requests: int = MAX_DAY_REQUESTS) -> CheckResult:
    """B2: send until a 403 arrives, waiting out each minute budget's 429.

    Passes only if the 403 comes after at least 45,000 tokens were recorded today: `recorded_tokens`
    (the owner's earlier gateway answers since 00:00 UTC) plus this run's own 200s. A 403 sooner
    than that is not the daily budget's.
    """
    waits = 0
    spent = 0
    for sent in range(1, max_requests + 1):
        reply = send()
        if reply.status == 403:
            total = recorded_tokens + spent
            figure = f"{total} tokens recorded today before the 403 ({recorded_tokens} earlier, {spent} in this run)"
            if total >= DAY_FLOOR_TOKENS:
                return CheckResult("B2", True, f"request {sent} got a 403 after {waits} minute-budget waits; {figure}")
            return CheckResult("B2", False, f"request {sent} got a 403, but only {figure}: at least "
                                            f"{DAY_FLOOR_TOKENS} are needed to take it for the daily budget's")
        if reply.status == 429:
            advised = advised_wait_ms(reply.headers)
            sleep(DEFAULT_WAIT_S if advised is None else advised / 1000)
            waits += 1
            continue
        if reply.status != 200:
            return CheckResult("B2", False, f"request {sent} got {_describe(reply)}, not a 200, 429 or 403")
        spent += reply.prompt_tokens + reply.completion_tokens
    return CheckResult("B2", False, f"{max_requests} requests were sent and none got a 403")


def access(post_without_token: Send, post_with_wrong_audience: Send) -> CheckResult:
    """B4: a call with no token, and one with a token for another audience, both get 401."""
    problems = []
    for name, send in (("no-token", post_without_token), ("wrong-audience", post_with_wrong_audience)):
        reply = send()
        if reply.status != 401:
            problems.append(f"the {name} call got {_describe(reply)}, not 401")
    if problems:
        return CheckResult("B4", False, "; ".join(problems))
    return CheckResult("B4", True, "the no-token call and the wrong-audience call both got 401")


def rev2_url(gateway_base_url: str) -> str:
    """Revision 2's chat-completions URL: the segment `;rev=2` after `v1` (constraints.md rulings)."""
    return gateway_base_url.rstrip("/") + REV2_SEGMENT + "/chat/completions"


def _holds_a_hostname(reply: Reply) -> bool:
    return HOSTNAME.search(reply.body) is not None or any(HOSTNAME.search(v) for v in reply.headers.values())


def _signal(reply: Reply, name: str) -> str:
    value = reply.header(name)
    return "absent" if value is None else value


def _smoke_one(check: str, reply: Reply) -> CheckResult:
    if _holds_a_hostname(reply):
        return CheckResult(check, False, f"{_describe(reply)}; a body or header value contains a hostname "
                                         "(withheld); the response must carry labels only")
    names = ", ".join(sorted(key.lower() for key in reply.headers))
    detail = (f"{_describe(reply)}; {REGION_HEADER} {_signal(reply, REGION_HEADER)}; "
              f"{BACKEND_HEADER} {_signal(reply, BACKEND_HEADER)}; header names: {names or 'none'}")
    return CheckResult(check, reply.status == 200, detail)


def smoke(direct: Send, gateway: Send, rev2: Send) -> list[CheckResult]:
    """One call direct, one through the gateway, one at revision 2: what comes back, and nothing leaked."""
    return [_smoke_one("smoke-direct", direct()), _smoke_one("smoke-gateway", gateway()),
            _smoke_one("smoke-rev2", rev2())]
