"""One request, sent the way the app sends it, and what is recorded about it (spec §7.1).

The client follows the app's rule (`rule.py`): on a 429, one wait if the advised wait fits a
3,000 ms budget, then one more send; otherwise the request fails. A timeout or a dropped
connection is a failed request, not a crash. A `Record` holds no token, no `oid`, no URL and no
hostname, and nothing here logs any of them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from .rule import WAIT_BUDGET_MS, advised_wait_ms

MAX_TOKENS = 40
TEMPERATURE = 0
TIMEOUT_S = 30.0

REGION_HEADER = "x-ms-region"
BACKEND_HEADER = "x-releaselens-backend"
# What the x-ms-region header says when the account in each role answered (spec §3, §4.4).
_REGIONS = {"Australia East": "primary", "Southeast Asia": "secondary"}
_BACKENDS = ("primary", "secondary")

# A made-up paragraph of about 300 tokens, the same in every request of every run, so the token
# count a request spends is the same too. Nothing in it is real.
PROMPT = (
    "Summarise the following release note for a reader who has not seen the project, in two "
    "sentences. Release 7.4 of the Marlowe Harbour scheduling service, a made-up product used only "
    "for this test, changes how its planner orders the day's tasks. Previously the planner sorted "
    "tasks by their deadline alone, so a short task due at noon could be queued behind a long task "
    "due at one o'clock, and the long task then ran past its slot. The planner now estimates each "
    "task's duration from the last twelve runs of the same kind of task, subtracts the estimate "
    "from the deadline to find the latest safe start, and orders tasks by that start. Tasks with "
    "fewer than three earlier runs keep the old ordering, because the estimate is not yet "
    "trustworthy. The release also fixes a crash that happened when two tasks shared an identical "
    "latest start: the comparison function returned an inconsistent result, and the sort aborted "
    "with an exception that cleared the whole queue. Ties are now broken by the task's creation "
    "time, then by its identifier, so the order is the same on every run. The retry setting for "
    "failed tasks is unchanged, but its documentation now says plainly that retries are counted "
    "per task, not per day. Operators upgrading from 7.3 should clear the duration cache once, "
    "using the maintenance command, because the cache format gained a field for the sample count. "
    "No configuration keys were added or removed, and the public interface of the scheduler is "
    "exactly as it was in 7.3."
)


@dataclass(frozen=True)
class Record:
    seq: int
    sent_at: float            # seconds on the run's clock, at the first send
    status: int | None        # None: no response (timeout, connection failure)
    latency_ms: float         # first send to final response, including any wait
    region: str | None        # "primary", "secondary" or None
    model_called: bool        # the response carried x-ms-region: a model answered
    waited_ms: int
    prompt_tokens: int
    completion_tokens: int
    caller: str


def region_of(headers: httpx.Headers, signal: str) -> str | None:
    """The role of the account that answered, from the frozen signal, or None when it is unclear."""
    if signal == REGION_HEADER:
        return _REGIONS.get(headers.get(REGION_HEADER, ""))
    if signal == BACKEND_HEADER:
        value = headers.get(BACKEND_HEADER)
        return value if value in _BACKENDS else None
    raise ValueError(f"unknown region signal {signal!r}")


def _usage(response: httpx.Response) -> tuple[int, int]:
    if response.status_code != 200:
        return 0, 0
    try:
        usage = response.json()["usage"]
        prompt, completion = usage["prompt_tokens"], usage["completion_tokens"]
    except (ValueError, KeyError, TypeError):
        return 0, 0
    if type(prompt) is not int or type(completion) is not int:
        return 0, 0
    return prompt, completion


async def send_one(
    http: httpx.AsyncClient, url: str, token: str, deployment: str, seq: int, *,
    caller: str, region_signal: str, clock: Callable[[], float], sleep: Callable[[float], Awaitable[None]],
) -> Record:
    body = {
        "model": deployment,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
    }
    headers = {"Authorization": f"Bearer {token}"}
    sent_at = clock()
    waited_ms = 0
    response: httpx.Response | None = None
    try:
        response = await http.post(url, json=body, headers=headers, timeout=TIMEOUT_S)
        if response.status_code == 429:
            wait = advised_wait_ms(response.headers)
            # The app's budget is per query: one wait at most, and only if it fits.
            if wait is not None and wait <= WAIT_BUDGET_MS:
                await sleep(wait / 1000)
                waited_ms = wait
                response = await http.post(url, json=body, headers=headers, timeout=TIMEOUT_S)
    except httpx.TransportError:
        # A timeout or a dropped connection, even after a wait, ends the request as failed.
        response = None
    latency_ms = (clock() - sent_at) * 1000

    if response is None:
        return Record(seq, sent_at, None, latency_ms, None, False, waited_ms, 0, 0, caller)
    prompt_tokens, completion_tokens = _usage(response)
    return Record(
        seq, sent_at, response.status_code, latency_ms,
        region_of(response.headers, region_signal), REGION_HEADER in response.headers,
        waited_ms, prompt_tokens, completion_tokens, caller,
    )
