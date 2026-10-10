"""The tool checks T1 to T4 (spec §7), and the direct query T1 compares with.

As in `ingest_checks`, each check is given what it measures as a function or a session, and a clock
and a sleep, so it runs on fixtures. A result's detail holds statuses, counts, labels, question IDs
and artefact IDs: never a token, a URL, a hostname, a reply body or a question's text.

T1 compares with the query the tool itself sends (`SearchIndexClient.HybridSemanticAsync`): keyword
plus vector with `k = top = 5`, the semantic ranker, configuration `default`, the query embedded by
`releaselens-embed-small` through the account's v1 `embeddings` endpoint. Not project 2's K of 50.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx
import numpy as np

from app.gateway.checks import CheckResult, Reply, concurrently
from app.gateway.metric import QUERY_URL, MetricQueryError, check_since
from app.retrieval import embed, search_index
from app.retrieval import questions as frozen_questions

from .mcp_http import McpError, McpHttpError, McpSession, text_of

TOOL = "search_corpus"
TOP = 5                     # the tool's default, and the k and top of the direct query
QUESTION_COUNT = 10         # spec §7: ten fixed questions, the first ten of the frozen set
BURST_CALLS = 30            # spec §7: a burst of 30 calls at once
TOOL_CALLS_METRIC = "Tool Calls"
T4_METRIC_WAIT_S = 300      # Application Insights lags; a few minutes is usual
T4_POLL_S = 30
# `initialize` and `notifications/initialized` each pass the gateway policy, so each is one `Tool Calls`.
HANDSHAKE_REQUESTS = 2
DIRECT_SCOPE = "https://ai.azure.com/.default"      # the account's v1 API, and the wrong audience for T2

Clock = Callable[[], float]
Sleep = Callable[[float], None]


class DirectSearchError(Exception):
    """The direct query failed; the message holds a status or a reason, never a URL or a body."""


# --- the questions and the direct query --------------------------------------------------------------

def first_ten_questions(path: Path) -> list[tuple[str, str]]:
    """`(qid, question)` of the first ten questions of the frozen set, in file order.

    The set is read only if the file is byte for byte what its manifest froze.
    """
    questions = frozen_questions.load_frozen(path)
    if len(questions) < QUESTION_COUNT:
        raise ValueError(f"the frozen set holds {len(questions)} questions; T1 needs {QUESTION_COUNT}")
    return [(q.qid, q.question) for q in questions[:QUESTION_COUNT]]


class DirectSearch:
    """The tool's search, run straight against the index: `question -> artefact IDs, best first`."""

    def __init__(self, http: httpx.Client, openai_base_url: str, search_endpoint: str, deployment: str,
                 openai_tokens, search_tokens, *, sleep: Callable[[float], None]) -> None:
        self._http = http
        self._base_url = openai_base_url.rstrip("/") + "/"
        self._endpoint = search_endpoint.rstrip("/")
        self._deployment = deployment
        self._openai_tokens = openai_tokens
        self._search_tokens = search_tokens
        self._sleep = sleep

    def __call__(self, question: str) -> list[str]:
        text = question.strip()
        vectors, _ = embed._post_embeddings(
            self._http, base_url=self._base_url, deployment=self._deployment, inputs=[text],
            tokens=self._openai_tokens, sleep=self._sleep)
        body = {
            "search": text,
            "vectorQueries": [{"kind": "vector", "vector": np.asarray(vectors[0], dtype=np.float32).tolist(),
                               "fields": "vector", "k": TOP}],
            "top": TOP,
            "select": "chunk_id,artefact,content",
            "queryType": "semantic",
            "semanticConfiguration": search_index.SEMANTIC_CONFIGURATION,
        }
        response = self._http.post(
            f"{self._endpoint}/indexes/{search_index.INDEX_NAME}/docs/search",
            params={"api-version": search_index.API_VERSION},
            headers={"Authorization": f"Bearer {self._search_tokens.token()}"}, json=body)
        if not response.is_success:
            raise DirectSearchError(f"the index answered {response.status_code}")
        documents = response.json().get("value", [])
        if any(d.get("@search.rerankerScore") is None for d in documents):
            raise DirectSearchError("a hit has no reranker score: the semantic ranker did not rank this query")
        return [d["artefact"] for d in documents]


# --- T1 ------------------------------------------------------------------------------------------------

def _artefacts_of(result: dict) -> list[str]:
    hits = json.loads(text_of(result))
    if not isinstance(hits, list) or not all(isinstance(h, dict) and isinstance(h.get("artefact"), str) for h in hits):
        raise ValueError("not a list of hits")
    return [h["artefact"] for h in hits]


def t1_same_search(session: McpSession, direct: Callable[[str], list[str]],
                   questions: list[tuple[str, str]]) -> CheckResult:
    """T1: `search_corpus` is listed, and for each of ten questions its top 5 artefacts match the direct query's, in order."""
    if len(questions) != QUESTION_COUNT:
        raise ValueError(f"T1 takes exactly {QUESTION_COUNT} questions, not {len(questions)}")
    try:
        session.initialize()
        if TOOL not in session.list_tools():
            return CheckResult("T1", False, f"{TOOL} is not in the tool list")
    except McpError as error:
        return CheckResult("T1", False, f"the handshake or the tool list failed: {error}")
    except httpx.TransportError:
        return CheckResult("T1", False, "the handshake or the tool list got no response")

    matched, problems = 0, []
    for qid, text in questions:
        try:
            from_tool = _artefacts_of(session.call(TOOL, {"query": text, "top": TOP}))
        except McpError as error:
            problems.append(f"{qid}: the tool call failed ({error})")
            continue
        except httpx.TransportError:
            problems.append(f"{qid}: the tool call got no response")
            continue
        except ValueError:
            problems.append(f"{qid}: the tool's reply is not a list of hits")
            continue
        try:
            from_index = direct(text)
        except (DirectSearchError, httpx.HTTPError) as error:
            problems.append(f"{qid}: the direct query failed ({type(error).__name__})")
            continue
        if from_tool == from_index:
            matched += 1
        else:
            problems.append(f"{qid}: the tool gave {from_tool}, the direct query {from_index}")
    if matched == len(questions):
        return CheckResult("T1", True, f"{TOOL} is in the tool list; {matched} of {len(questions)} questions "
                                       f"returned the same top {TOP} artefacts, in order, as a direct query")
    return CheckResult("T1", False, f"{matched} of {len(questions)} questions matched; " + "; ".join(problems))


# --- T2 and T3 -------------------------------------------------------------------------------------------

def _refusal(session: McpSession) -> tuple[bool, str]:
    """Whether the call was refused with a 401, and what it got."""
    try:
        session.initialize()
    except McpHttpError as error:
        return error.status == 401, f"status {error.status}"
    except McpError:
        return False, "an answer that is not a refusal"
    except httpx.TransportError:
        return False, "no response"
    return False, "an answer (not refused)"


def _both_401(check: str, first: tuple[str, McpSession], second: tuple[str, McpSession], what: str) -> CheckResult:
    outcomes = [(label, *_refusal(session)) for label, session in (first, second)]
    if all(refused for _, refused, _ in outcomes):
        return CheckResult(check, True, f"{what}: " + " and ".join(f"{label} got 401" for label, _, _ in outcomes))
    return CheckResult(check, False, f"{what}, not both 401: " + "; ".join(f"{label} got {got}" for label, _, got in outcomes))


def t2_gateway_refuses(no_token: McpSession, wrong_audience: McpSession) -> CheckResult:
    """T2: a call with no token and a call with a wrong-audience token both get 401 from the gateway."""
    return _both_401("T2", ("the call with no token", no_token), ("the call with a wrong-audience token", wrong_audience),
                     "the gateway refused strangers")


def t3_no_bypass(without_token: McpSession, with_gateway_token: McpSession) -> CheckResult:
    """T3: a call straight to the tool app is refused (401) with no token and with the owner's gateway token."""
    return _both_401("T3", ("the direct call with no token", without_token),
                     ("the direct call with the gateway token", with_gateway_token),
                     "the tool app refused direct calls")


def floor_to_minute(moment: datetime) -> datetime:
    """The whole minute `moment` falls in."""
    return moment.replace(second=0, microsecond=0)


MINUTE_SETTLE_S = 15


def seconds_until_burst(now: datetime) -> float:
    """How long T4 waits before its handshake: until MINUTE_SETTLE_S past the next whole minute (16 to 75 s).

    The handshake then lands in a minute that opens after everything before it, so the whole-minute window
    that starts there holds only T4's own calls.
    """
    return (60 - (now.second + now.microsecond / 1_000_000)) + MINUTE_SETTLE_S


# --- T4 ------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Burst:
    """What 30 calls at once got: counts only. `accepted` are the calls that got past the gateway's policy."""

    sent: int
    accepted: int                   # answered, by the tool app or by an error of its own: not a 401, not a 429
    refused: int                    # the gateway's 429
    refused_with_retry_after: int
    other: int                      # no response, or a 401


def mcp_send(session: McpSession, query: str) -> Callable[[], Reply]:
    """One `search_corpus` call as a `Reply`: its HTTP status, and `Retry-After` when there is one.

    A tool error is still an HTTP 200: it got past the gateway.
    """
    def send() -> Reply:
        try:
            session.call(TOOL, {"query": query, "top": 1})
        except McpHttpError as error:
            return Reply(error.status, {} if error.retry_after is None else {"Retry-After": error.retry_after})
        except McpError:
            return Reply(200)
        except httpx.TransportError:
            return Reply(None)
        return Reply(200)

    return send


def run_burst(send: Callable[[], Reply], n: int = BURST_CALLS) -> Burst:
    replies = concurrently(send, n)
    refused = [r for r in replies if r.status == 429]
    return Burst(
        sent=len(replies),
        accepted=sum(r.status is not None and r.status not in (401, 429) for r in replies),
        refused=len(refused),
        refused_with_retry_after=sum(r.header("retry-after") not in (None, "") for r in refused),
        other=sum(r.status is None or r.status == 401 for r in replies),
    )


def t4_rate_limit(burst: Burst, read_totals: Callable[[], dict[str, int]], *, label: str, clock: Clock,
                  sleep: Sleep, wait_s: float = T4_METRIC_WAIT_S, interval_s: float = T4_POLL_S) -> CheckResult:
    """T4: the burst met a gateway 429 with `Retry-After`, and the `Tool Calls` metric shows the calls under `label`.

    `read_totals` is bound to the burst's own window (it starts before the handshake and ends after the
    burst). The metric lags, so it is read until it holds at least the calls the gateway let through plus
    the handshake's requests (the 429s are refused before `emit-metric`), or until `wait_s` has passed. A
    burst in which no call got past the gateway fails: no metric row can then show anything.
    """
    if burst.refused_with_retry_after < 1:
        return CheckResult("T4", False, f"none of the {burst.sent} calls sent at once got a 429 with Retry-After "
                                        f"({burst.refused} got a 429, {burst.accepted} were answered)")
    if burst.accepted < 1:
        return CheckResult("T4", False, f"no call of the {burst.sent} got past the gateway: nothing could show in "
                                        "the metric, so the rate limit is not shown to leave a caller's calls through")
    required = burst.accepted + HANDSHAKE_REQUESTS
    start, seen, error = clock(), None, None
    while True:
        try:
            seen = read_totals().get(label)
            error = None
        except MetricQueryError as failure:
            error = str(failure)
        if seen is not None and seen >= required:
            return CheckResult("T4", True, f"{burst.refused_with_retry_after} of {burst.sent} calls at once got a 429 "
                                           f"with Retry-After; the {TOOL_CALLS_METRIC} metric shows {seen} calls under "
                                           f"{label} in the burst's window, at least the {required} expected "
                                           f"({burst.accepted} let through and {HANDSHAKE_REQUESTS} for the handshake)")
        if clock() - start >= wait_s:
            break
        sleep(interval_s)
    shown = error or (f"{seen} calls under {label}" if seen is not None else f"no calls under {label}")
    return CheckResult("T4", False, f"{burst.refused_with_retry_after} of {burst.sent} calls got a 429 with Retry-After, "
                                    f"but the {TOOL_CALLS_METRIC} metric shows {shown} in the burst's window, "
                                    f"and {required} were expected: the metric lags, so re-run the metric check later")


# --- the metric query -------------------------------------------------------------------------------------------

def tool_call_rows(app_id: str, token: str, start: str, until: str, http: httpx.Client) -> list[dict]:
    """`Tool Calls` per `Caller` between `start` and `until` (UTC, to the second), from Application Insights."""
    check_since(start)
    check_since(until)
    kql = (
        "customMetrics\n"
        f"| where timestamp between (datetime({start}) .. datetime({until}))\n"
        f'| where name == "{TOOL_CALLS_METRIC}"\n'
        '| extend Caller = tostring(customDimensions["Caller"])\n'
        "| summarize total = sum(valueSum) by Caller"
    )
    response = http.post(QUERY_URL.format(app_id=app_id), headers={"Authorization": f"Bearer {token}"},
                         json={"query": kql, "timespan": f"{start}/{until}"})
    if response.status_code != 200:
        raise MetricQueryError(f"the query API answered {response.status_code}")
    try:
        table = response.json()["tables"][0]
        names = [column["name"] for column in table["columns"]]
        return [dict(zip(names, row, strict=True)) for row in table["rows"]]
    except (ValueError, KeyError, IndexError, TypeError):
        raise MetricQueryError("the query API's answer has no result table") from None
