"""T1 to T4 on fixtures, and the direct query T1 compares with."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.functions import tool_checks as tc
from app.functions.mcp_http import McpHttpError, McpToolError
from app.gateway.checks import Reply
from app.gateway.metric import MetricQueryError

QUESTIONS = [(f"q{n:03d}", f"question number {n}?") for n in range(1, 11)]


def _hits(*artefacts: str) -> dict:
    hits = [{"artefact": a, "type": a.split(":")[0], "excerpt": "text", "score": 3.0 - i / 10}
            for i, a in enumerate(artefacts)]
    return {"content": [{"type": "text", "text": json.dumps(hits)}], "isError": False}


class FakeSession:
    def __init__(self, tools=("search_corpus",), answers=None, fail_on=None, init_error=None) -> None:
        self.tools, self.answers = list(tools), answers or {}
        self.fail_on, self.init_error = fail_on or {}, init_error
        self.calls: list[tuple[str, dict]] = []
        self.initialised = 0

    def initialize(self):
        self.initialised += 1
        if self.init_error:
            raise self.init_error
        return {}

    def list_tools(self):
        return self.tools

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        if arguments["query"] in self.fail_on:
            raise self.fail_on[arguments["query"]]
        return self.answers[arguments["query"]]


def _agreeing() -> tuple[FakeSession, dict]:
    direct = {text: [f"issue:{n}", f"pull_request:{n}", f"commit:{n}ab", f"issue:{n + 100}", f"release:v{n}"]
              for n, (_, text) in enumerate(QUESTIONS, 1)}
    return FakeSession(answers={q: _hits(*a) for q, a in direct.items()}), direct


# --- T1 ------------------------------------------------------------------------------------------------

def test_t1_passes_when_the_tool_is_listed_and_all_ten_top_fives_match_in_order():
    session, direct = _agreeing()
    result = tc.t1_same_search(session, lambda q: direct[q], QUESTIONS)

    assert (result.check, result.passed) == ("T1", True)
    assert "10 of 10" in result.detail
    assert session.initialised == 1 and len(session.calls) == 10
    assert all(name == "search_corpus" and args["top"] == 5 for name, args in session.calls)
    assert [args["query"] for _, args in session.calls] == [q for _, q in QUESTIONS]


def test_t1_fails_when_two_artefacts_swap_places_and_names_the_question():
    session, direct = _agreeing()
    wrong = dict(direct)
    wrong[QUESTIONS[2][1]] = [direct[QUESTIONS[2][1]][1], direct[QUESTIONS[2][1]][0], *direct[QUESTIONS[2][1]][2:]]
    result = tc.t1_same_search(session, lambda q: wrong[q], QUESTIONS)
    assert result.passed is False and "q003" in result.detail and "9 of 10" in result.detail


def test_t1_fails_when_the_tool_returns_fewer_than_the_direct_query():
    session, direct = _agreeing()
    session.answers[QUESTIONS[0][1]] = _hits(*direct[QUESTIONS[0][1]][:4])
    result = tc.t1_same_search(session, lambda q: direct[q], QUESTIONS)
    assert result.passed is False and "q001" in result.detail


def test_t1_counts_a_repeated_artefact_as_a_position_of_its_own():
    answers = {text: _hits("issue:1", "issue:1", "issue:2", "issue:3", "issue:4") for _, text in QUESTIONS}
    same = tc.t1_same_search(FakeSession(answers=answers), lambda q: ["issue:1", "issue:1", "issue:2", "issue:3", "issue:4"], QUESTIONS)
    different = tc.t1_same_search(FakeSession(answers=answers), lambda q: ["issue:1", "issue:2", "issue:3", "issue:4", "issue:5"], QUESTIONS)
    assert same.passed is True and different.passed is False


def test_t1_fails_when_search_corpus_is_not_in_the_tool_list_and_asks_no_question():
    session, direct = _agreeing()
    session.tools = ["something_else"]
    result = tc.t1_same_search(session, lambda q: direct[q], QUESTIONS)
    assert result.passed is False and "tool list" in result.detail and session.calls == []


def test_t1_fails_on_a_tool_error_and_says_only_the_error_not_the_question_text():
    session, direct = _agreeing()
    session.fail_on[QUESTIONS[4][1]] = McpToolError("search failed")
    result = tc.t1_same_search(session, lambda q: direct[q], QUESTIONS)
    assert result.passed is False and "q005" in result.detail and "search failed" in result.detail
    assert QUESTIONS[4][1] not in result.detail


def test_t1_fails_when_the_handshake_is_refused():
    result = tc.t1_same_search(FakeSession(init_error=McpHttpError(401)), lambda q: [], QUESTIONS)
    assert result.passed is False and "HTTP 401" in result.detail


def test_t1_needs_exactly_ten_questions():
    session, direct = _agreeing()
    with pytest.raises(ValueError):
        tc.t1_same_search(session, lambda q: direct[q], QUESTIONS[:9])


def test_t1_fails_on_a_reply_that_is_not_a_list_of_hits():
    session, direct = _agreeing()
    session.answers[QUESTIONS[0][1]] = {"content": [{"type": "text", "text": "{\"not\": \"a list\"}"}]}
    assert tc.t1_same_search(session, lambda q: direct[q], QUESTIONS).passed is False


# --- T2 and T3 -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("check,function", [("T2", tc.t2_gateway_refuses), ("T3", tc.t3_no_bypass)])
def test_t2_and_t3_pass_when_both_calls_are_401(check, function):
    result = function(FakeSession(init_error=McpHttpError(401)), FakeSession(init_error=McpHttpError(401)))
    assert (result.check, result.passed) == (check, True)


@pytest.mark.parametrize("function", [tc.t2_gateway_refuses, tc.t3_no_bypass])
def test_t2_and_t3_fail_when_either_call_is_answered_or_refused_with_another_status(function):
    refused, answered, forbidden = (FakeSession(init_error=McpHttpError(401)), FakeSession(),
                                    FakeSession(init_error=McpHttpError(403)))
    assert function(refused, answered).passed is False
    assert function(answered, refused).passed is False
    failed = function(refused, forbidden)
    assert failed.passed is False and "403" in failed.detail


@pytest.mark.parametrize("function", [tc.t2_gateway_refuses, tc.t3_no_bypass])
def test_t2_and_t3_fail_on_no_response(function):
    down = FakeSession(init_error=httpx.ConnectError("down"))
    result = function(down, FakeSession(init_error=McpHttpError(401)))
    assert result.passed is False and "no response" in result.detail


# --- T4 ------------------------------------------------------------------------------------------------------

def _burst_send(statuses: list[int | None], retry_after: str | None = "12"):
    state = {"n": 0}

    def send() -> Reply:
        n = state["n"]
        state["n"] += 1
        status = statuses[n % len(statuses)]
        headers = {"Retry-After": retry_after} if status == 429 and retry_after else {}
        return Reply(status, headers)

    return send


def test_run_burst_sends_thirty_at_once_and_counts_each_kind():
    burst = tc.run_burst(_burst_send([200] * 20 + [429] * 10))
    assert (burst.sent, burst.accepted, burst.refused, burst.refused_with_retry_after) == (30, 20, 10, 10)


def test_run_burst_counts_a_429_without_retry_after_and_an_answer_that_never_came():
    burst = tc.run_burst(_burst_send([200, 429, None], retry_after=None), n=30)
    assert (burst.accepted, burst.refused, burst.refused_with_retry_after, burst.other) == (10, 10, 0, 10)


def test_t4_passes_when_a_429_has_retry_after_and_the_metric_shows_the_owner_s_calls(clock):
    burst = tc.Burst(sent=30, accepted=20, refused=10, refused_with_retry_after=10, other=0)
    result = tc.t4_rate_limit(burst, lambda: {"owner": 23}, label="owner", clock=clock, sleep=clock.sleep)
    assert (result.check, result.passed) == ("T4", True)
    assert "10 of 30" in result.detail and "owner" in result.detail


def test_t4_waits_for_a_metric_that_lags_and_passes_when_it_arrives(clock):
    burst = tc.Burst(30, 20, 10, 10, 0)
    reads = []

    def totals():
        reads.append(clock.now)
        return {"owner": 22} if len(reads) >= 4 else {}

    result = tc.t4_rate_limit(burst, totals, label="owner", clock=clock, sleep=clock.sleep, wait_s=300, interval_s=30)
    assert result.passed is True and len(reads) == 4 and sum(clock.sleeps) == 90


def test_t4_fails_when_the_metric_never_shows_the_owner_in_the_wait(clock):
    burst = tc.Burst(30, 20, 10, 10, 0)
    result = tc.t4_rate_limit(burst, lambda: {"other": 25}, label="owner", clock=clock, sleep=clock.sleep,
                              wait_s=120, interval_s=30)
    assert result.passed is False and "owner" in result.detail and "re-run" in result.detail
    assert sum(clock.sleeps) >= 120


def test_t4_fails_when_the_metric_holds_fewer_calls_than_the_gateway_let_through(clock):
    burst = tc.Burst(30, 20, 10, 10, 0)
    result = tc.t4_rate_limit(burst, lambda: {"owner": 7}, label="owner", clock=clock, sleep=clock.sleep, wait_s=0)
    assert result.passed is False and "7" in result.detail


def test_t4_needs_the_burst_s_accepted_calls_plus_the_handshake_s_requests(clock):
    burst = tc.Burst(30, 20, 10, 10, 0)
    assert tc.HANDSHAKE_REQUESTS == 2
    short = tc.t4_rate_limit(burst, lambda: {"owner": 21}, label="owner", clock=clock, sleep=clock.sleep, wait_s=0)
    enough = tc.t4_rate_limit(burst, lambda: {"owner": 22}, label="owner", clock=clock, sleep=clock.sleep, wait_s=0)
    assert short.passed is False and "22" in short.detail and enough.passed is True


def test_t4_fails_when_no_burst_call_was_accepted_and_never_passes_on_other_rows(clock):
    burst = tc.Burst(30, 0, 30, 30, 0)
    result = tc.t4_rate_limit(burst, lambda: {"owner": 500}, label="owner", clock=clock, sleep=clock.sleep, wait_s=0)
    assert result.passed is False and "no call" in result.detail and "got past the gateway" in result.detail


def test_t4_fails_without_a_429_with_retry_after_and_does_not_wait_for_the_metric(clock):
    for burst in (tc.Burst(30, 30, 0, 0, 0), tc.Burst(30, 20, 10, 0, 0)):
        result = tc.t4_rate_limit(burst, lambda: pytest.fail("the metric is not read"), label="owner",
                                  clock=clock, sleep=clock.sleep)
        assert result.passed is False and "Retry-After" in result.detail
    assert clock.sleeps == []


def test_t4_fails_with_the_status_when_the_metric_query_fails(clock):
    def totals():
        raise MetricQueryError("the query API answered 403")

    result = tc.t4_rate_limit(tc.Burst(30, 20, 10, 10, 0), totals, label="owner", clock=clock, sleep=clock.sleep, wait_s=0)
    assert result.passed is False and "403" in result.detail


# --- the adapter, the direct query, the question set -------------------------------------------------------

def test_mcp_send_maps_a_call_to_a_reply():
    class Session:
        def __init__(self, outcome):
            self.outcome = outcome

        def call(self, name, arguments):
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return {}

    assert tc.mcp_send(Session(None), "q")().status == 200
    assert tc.mcp_send(Session(McpToolError("search failed")), "q")().status == 200      # still an HTTP 200
    limited = tc.mcp_send(Session(McpHttpError(429, "9")), "q")()
    assert (limited.status, limited.header("retry-after")) == (429, "9")
    assert tc.mcp_send(Session(McpHttpError(401)), "q")().status == 401
    assert tc.mcp_send(Session(httpx.ReadTimeout("slow")), "q")().status is None


class _Tokens:
    def __init__(self, token: str) -> None:
        self._token = token

    def token(self) -> str:
        return self._token


def test_direct_search_sends_the_tools_own_query_and_returns_the_artefacts_in_order():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.25] * 1536}], "usage": {"prompt_tokens": 5}})
        reply = {"value": [{"chunk_id": "a-0", "artefact": "issue:2", "@search.rerankerScore": 3.1},
                           {"chunk_id": "a-1", "artefact": "issue:2", "@search.rerankerScore": 2.9},
                           {"chunk_id": "b-0", "artefact": "commit:abc", "@search.rerankerScore": 2.0}]}
        return httpx.Response(200, json=reply)

    search = tc.DirectSearch(
        httpx.Client(transport=httpx.MockTransport(handler)), openai_base_url="https://oai.example.com/openai/v1/",
        search_endpoint="https://search.example.com", deployment="releaselens-embed-small",
        openai_tokens=_Tokens("oai-token"), search_tokens=_Tokens("search-token"), sleep=lambda s: None)

    assert search("  What changed?  ") == ["issue:2", "issue:2", "commit:abc"]

    embed, query = seen
    assert embed.url.path == "/openai/v1/embeddings" and embed.headers["authorization"] == "Bearer oai-token"
    assert json.loads(embed.content) == {"model": "releaselens-embed-small", "input": ["What changed?"]}
    body = json.loads(query.content)
    assert query.url.path == "/indexes/releaselens-chunks/docs/search" and query.headers["authorization"] == "Bearer search-token"
    assert body["search"] == "What changed?" and body["top"] == 5
    assert body["vectorQueries"] == [{"kind": "vector", "vector": [0.25] * 1536, "fields": "vector", "k": 5}]
    assert (body["queryType"], body["semanticConfiguration"]) == ("semantic", "default")
    assert body["select"] == "chunk_id,artefact,content"


def test_direct_search_raises_when_a_hit_was_not_reranked():
    def handler(request):
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0] * 1536}], "usage": {"prompt_tokens": 1}})
        return httpx.Response(200, json={"value": [{"chunk_id": "a", "artefact": "issue:1"}]})

    search = tc.DirectSearch(httpx.Client(transport=httpx.MockTransport(handler)), "https://oai.example.com/openai/v1/",
                             "https://search.example.com", "releaselens-embed-small", _Tokens("a"), _Tokens("b"),
                             sleep=lambda s: None)
    with pytest.raises(tc.DirectSearchError):
        search("q")


def test_the_first_ten_questions_are_read_in_file_order_from_the_frozen_set(tmp_path):
    from app.retrieval import questions as q

    path = tmp_path / "questions.jsonl"
    items = [q.Question(f"q{n:03d}", f"Question {n}?", f"issue:{n}", "issue") for n in range(1, 16)]
    q.freeze(items, {"seed": 1}, path)

    first = tc.first_ten_questions(path)

    assert first == [(f"q{n:03d}", f"Question {n}?") for n in range(1, 11)]


def test_the_first_ten_questions_refuse_a_set_changed_after_freezing(tmp_path):
    from app.retrieval import questions as q

    path = tmp_path / "questions.jsonl"
    q.freeze([q.Question(f"q{n:03d}", f"Question {n}?", f"issue:{n}", "issue") for n in range(1, 16)], {}, path)
    path.write_bytes(path.read_bytes().replace(b"Question 3?", b"Question THREE?"))
    with pytest.raises(ValueError):
        tc.first_ten_questions(path)


def test_the_real_frozen_set_has_at_least_ten_questions_and_loads():
    from app.retrieval import questions as q

    first = tc.first_ten_questions(Path(__file__).resolve().parents[2] / "retrieval" / "questions.jsonl")
    assert len(first) == 10 and first[0][0] == "q001"


def test_tool_call_rows_query_the_tool_calls_metric_by_caller_within_the_burst_window():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"tables": [{"columns": [{"name": "Caller"}, {"name": "total"}],
                                                      "rows": [["00000000-0000-0000-0000-0000000000a1", 23.0]]}]})

    rows = tc.tool_call_rows("app-id-1", "tok", "2026-10-10T01:30:00Z", "2026-10-10T01:31:10Z",
                             httpx.Client(transport=httpx.MockTransport(handler)))

    assert rows == [{"Caller": "00000000-0000-0000-0000-0000000000a1", "total": 23.0}]
    body = json.loads(seen[0].content)
    assert 'name == "Tool Calls"' in body["query"]
    assert "between (datetime(2026-10-10T01:30:00Z) .. datetime(2026-10-10T01:31:10Z))" in body["query"]
    assert seen[0].headers["authorization"] == "Bearer tok" and body["timespan"] == "2026-10-10T01:30:00Z/2026-10-10T01:31:10Z"


def test_tool_call_rows_refuse_a_time_that_is_not_utc_and_raise_the_status_only():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403, text="secret")))
    with pytest.raises(ValueError):
        tc.tool_call_rows("a", "t", '2026" | take 1', "2026-10-10T01:31:10Z", client)
    with pytest.raises(ValueError):
        tc.tool_call_rows("a", "t", "2026-10-10T01:31:10Z", "tomorrow", client)
    with pytest.raises(MetricQueryError) as raised:
        tc.tool_call_rows("a", "t", "2026-10-10T01:30:00Z", "2026-10-10T01:31:10Z", client)
    assert str(raised.value) == "the query API answered 403"


def _at(second: float) -> datetime:
    whole = int(second)
    return datetime(2026, 10, 10, 3, 30, whole, int((second - whole) * 1_000_000), tzinfo=timezone.utc)


@pytest.mark.parametrize("second,wait", [(0, 75), (14, 61), (15, 60), (59, 16), (14.5, 60.5)])
def test_the_wait_ends_15_s_past_the_next_whole_minute(second, wait):
    assert tc.seconds_until_burst(_at(second)) == wait
    after = _at(second).timestamp() + wait
    assert after % 60 == 15 and after > _at(second).timestamp()


def test_the_wait_is_at_most_75_seconds():
    assert max(tc.seconds_until_burst(_at(s)) for s in range(60)) == 75


def test_the_floor_is_the_whole_minute():
    assert tc.floor_to_minute(_at(30.7)) == datetime(2026, 10, 10, 3, 30, tzinfo=timezone.utc)
    assert tc.floor_to_minute(_at(0)) == datetime(2026, 10, 10, 3, 30, tzinfo=timezone.utc)
    assert tc.floor_to_minute(_at(59.999)).second == 0
