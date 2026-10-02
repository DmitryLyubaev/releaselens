"""What the runner asks the API for, and what it does with the reply.

No network and no real judge: the HTTP client is replaced, every sweep that judges uses a
fake judge or the real one over a fake Anthropic client, and the rest run with judge=False, so
nothing here can reach api.anthropic.com.
"""

import re
from datetime import datetime
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app import judge as judge_module
from app import runner as runner_module
from app.analysis import QUALITY_METRICS
from app.golden import load_golden
from app.judge import JUDGE_OUTPUT_ALLOWANCE_TOKENS, Judgement
from app.models import Arm, RunRequest
from app.runner import _unscored, arm_order, citation_ids, cited_evidence, provider_error, run_eval

_BODY = {
    "answer": "The planner defect arrived in [E1] and was fixed by [E3].",
    "citations": [
        {
            "marker": 1,
            "type": "commit",
            "key": "1a2b3c4d",
            "title": "fix: planner null reference",
            "url": "https://github.com/microsoft/semantic-kernel/commit/1a2b3c4d",
            "evidence": ["[commit 1a2b3c4] fix: planner null reference", "guard added for empty goals"],
        },
        {
            "marker": 3,
            "type": "issue",
            "key": "14111",
            "title": "Planner throws on empty goal",
            "url": "https://github.com/microsoft/semantic-kernel/issues/14111",
            "evidence": ["[issue #14111 closed] Planner throws on empty goal"],
        },
    ],
    "metadata": {
        "costUsd": 0.0123,
        "tokensIn": 4000,
        "tokensOut": 300,
        "cacheReadInputTokens": 0,
        "provider": "anthropic",
        "providers": ["anthropic"],
        "model": "claude-sonnet-5",
        "degraded": False,
        "unresolvedCitationMarkers": [],
    },
}


_ARM = Arm(name="A", base_url="http://api.invalid", expected_provider="anthropic")

_ARMS = [
    Arm(name="A", base_url="http://arm-a.invalid", expected_provider="anthropic"),
    Arm(name="Z", base_url="http://arm-z.invalid", expected_provider="azure-openai"),
    Arm(name="O", base_url="http://arm-o.invalid", expected_provider="openai"),
]

_MODELS = {"anthropic": "claude-sonnet-5", "azure-openai": "gpt-4.1-mini-2025-04-14", "openai": "gpt-4.1-mini"}


def _reply(provider: str, /, **metadata) -> dict:
    """_BODY as the given provider would answer it, with any metadata field overridden."""
    return {
        **_BODY,
        "metadata": {
            **_BODY["metadata"],
            "provider": provider,
            "providers": [provider],
            "model": _MODELS[provider],
            **metadata,
        },
    }


def _each_arm_answers_as_itself() -> dict:
    return {arm.base_url: _reply(arm.expected_provider) for arm in _ARMS}


def _without_evidence(body: dict) -> dict:
    """The same reply as an API that was not asked for evidence would send it."""
    return {
        **body,
        "citations": [{k: v for k, v in c.items() if k != "evidence"} for c in body["citations"]],
    }


class _FakeClient:
    """Answers each arm from `replies`, keyed by its base URL, and records what was sent.

    A reply is a body, an HTTP status code, an exception to raise, or a function of the
    request's JSON that returns one of those, for an arm that answers some queries and not
    others.
    """

    replies: dict[str, object] = {}
    sent: list[tuple[str, dict]] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> bool:
        return False

    async def post(self, url, headers=None, json=None):
        base_url = url.removesuffix("/query")
        _FakeClient.sent.append((base_url, json))

        reply = _FakeClient.replies[base_url]
        if callable(reply):
            reply = reply(json)
        if isinstance(reply, Exception):
            raise reply

        request = httpx.Request("POST", url)
        if isinstance(reply, int):
            return httpx.Response(reply, request=request)
        return httpx.Response(200, json=reply, request=request)


@pytest.fixture
def fake_api(monkeypatch) -> type[_FakeClient]:
    _FakeClient.sent = []
    _FakeClient.replies = {_ARM.base_url: _BODY}

    # runner.py holds the module, not the class, so patching the attribute on httpx itself
    # is what the runner will see.
    monkeypatch.setattr(runner_module.httpx, "AsyncClient", _FakeClient)
    return _FakeClient


class _FakeJudge:
    """Stands in for GroundednessJudge: estimates each item at a fixed price, and records how many
    items each estimate was over and every answer it scores with the evidence it was given.

    The nth judgement costs n tenths of a cent, so a cost on the wrong outcome, or a sum that is
    really a count, shows. The judgements numbered in `unparseable` come back unscored.
    """

    usd_per_item = 0.0345
    estimated: list[int] = []
    scored: list[tuple[str, list]] = []
    unparseable: set[int] = set()
    raises: dict[int, Exception] = {}

    def __init__(self, model: str) -> None:
        pass

    async def estimate_cost_usd(self, items) -> float:
        _FakeJudge.estimated.append(len(items))
        return self.usd_per_item * len(items)

    async def score(self, question, answer, evidence) -> Judgement:
        _FakeJudge.scored.append((answer, evidence))
        n = len(_FakeJudge.scored)
        if n in _FakeJudge.raises:
            raise _FakeJudge.raises[n]
        if n in _FakeJudge.unparseable:
            return Judgement(score=-1.0, reason="judge output unparseable", cost_usd=0.001 * n)
        return Judgement(score=1.0, reason="supported", cost_usd=0.001 * n)


@pytest.fixture
def fake_judge(monkeypatch) -> type[_FakeJudge]:
    _FakeJudge.estimated = []
    _FakeJudge.scored = []
    _FakeJudge.unparseable = set()
    _FakeJudge.raises = {}
    monkeypatch.setattr(runner_module, "GroundednessJudge", _FakeJudge)
    return _FakeJudge


class _RecordingMessages:
    """Stands in for the Anthropic client's `messages` under the real GroundednessJudge, and keeps
    every request the judge sends, the free counts as well as the paid judgements."""

    sent: list[dict] = []
    reply_text = '{"score": 1.0, "reason": "supported"}'

    async def count_tokens(self, **kwargs):
        _RecordingMessages.sent.append(kwargs)
        return SimpleNamespace(input_tokens=1_000)

    async def create(self, **kwargs):
        _RecordingMessages.sent.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=_RecordingMessages.reply_text)],
            usage=SimpleNamespace(input_tokens=1_000, output_tokens=50),
        )


class _RecordingAnthropic:
    def __init__(self, api_key: str | None = None) -> None:
        self.messages = _RecordingMessages()


@pytest.fixture
def recording_anthropic(monkeypatch) -> type[_RecordingMessages]:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    _RecordingMessages.sent = []
    _RecordingMessages.reply_text = '{"score": 1.0, "reason": "supported"}'
    monkeypatch.setattr(judge_module, "AsyncAnthropic", _RecordingAnthropic)
    return _RecordingMessages


def _strings(value) -> list[str]:
    """Every string in a request, however deeply it is nested."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    return []


def test_citation_ids_are_the_type_key_identifiers_the_metrics_score():
    assert citation_ids(_BODY) == ["commit:1a2b3c4d", "issue:14111"]


def test_citation_ids_are_unaffected_by_the_evidence_field():
    """citation_recall and citation_precision must not move because of this change.

    The identifiers are read from the same two fields as before; whether the API also sent
    the artefacts' text is invisible to them.
    """
    assert citation_ids(_BODY) == citation_ids(_without_evidence(_BODY))


def test_cited_evidence_carries_the_marker_and_every_passage():
    evidence = cited_evidence(_BODY)

    assert [e.marker for e in evidence] == [1, 3]
    assert [e.id for e in evidence] == ["commit:1a2b3c4d", "issue:14111"]

    # Both passages of the multi-chunk artefact, not just the first.
    assert len(evidence[0].text) == 2
    assert "guard added for empty goals" in evidence[0].text


def test_cited_evidence_when_the_api_returned_none():
    """An API that was not asked yields empty text, never a KeyError or a fabricated blank."""
    evidence = cited_evidence(_without_evidence(_BODY))

    assert [e.id for e in evidence] == ["commit:1a2b3c4d", "issue:14111"]
    assert all(e.text == [] for e in evidence)


async def test_the_run_asks_the_api_for_the_evidence_text(fake_api):
    report = await run_eval(
        RunRequest(api_key="rl_test", limit=1, judge=False, arms=[_ARM])
    )

    assert len(report.outcomes) == 1
    assert report.query_ids == [load_golden()[0].id]

    _, sent = fake_api.sent[0]
    assert sent["includeEvidence"] is True
    assert sent["question"]


async def test_the_run_still_scores_recall_and_precision_over_identifiers(fake_api):
    report = await run_eval(
        RunRequest(api_key="rl_test", limit=1, judge=False, arms=[_ARM])
    )

    outcome = report.outcomes[0]

    # The outcome carries identifiers, exactly as before — the evidence text is held beside
    # the outcomes rather than on them, so reports stay readable.
    assert outcome.citations == ["commit:1a2b3c4d", "issue:14111"]
    assert "evidence" not in outcome.model_dump()


def test_arm_order_rotates_by_pass():
    def order(arms: list[Arm], pass_index: int) -> str:
        return "".join(arm.name for arm in arm_order(arms, pass_index))

    assert [order(_ARMS, p) for p in (0, 1, 2)] == ["AZO", "ZOA", "OAZ"]
    assert order(_ARMS, 3) == "AZO"
    assert [order([_ARM], p) for p in range(4)] == ["A"] * 4


async def test_requests_follow_pass_then_query_then_arm(fake_api):
    fake_api.replies = _each_arm_answers_as_itself()

    await run_eval(RunRequest(api_key="rl_test", limit=2, passes=2, judge=False, arms=_ARMS))

    a, z, o = (arm.base_url for arm in _ARMS)
    q1, q2 = (query.question for query in load_golden()[:2])

    assert [(url, body["question"]) for url, body in fake_api.sent] == [
        (a, q1), (z, q1), (o, q1), (a, q2), (z, q2), (o, q2),
        (z, q1), (o, q1), (a, q1), (z, q2), (o, q2), (a, q2),
    ]


async def test_each_outcome_is_tagged(fake_api):
    fake_api.replies = _each_arm_answers_as_itself()

    report = await run_eval(RunRequest(api_key="rl_test", limit=1, passes=2, judge=False, arms=_ARMS))

    tags = {
        (o.arm, o.pass_index): (o.provider, o.providers, o.model, o.filtered_stage, o.error)
        for o in report.outcomes
    }
    assert tags == {
        (arm.name, pass_index): (
            arm.expected_provider, [arm.expected_provider], _MODELS[arm.expected_provider], None, None,
        )
        for arm in _ARMS
        for pass_index in (0, 1)
    }


@pytest.mark.parametrize(
    ("metadata", "named", "filtered_stage"),
    [
        ({"provider": "anthropic", "providers": ["anthropic"]}, ["Z", "azure-openai", "anthropic"], None),
        ({"provider": "none", "providers": []}, ["Z", "azure-openai", "none"], None),
        (
            {"provider": "openai", "providers": ["azure-openai", "openai"]},
            ["Z", "azure-openai", "azure-openai, openai"],
            None,
        ),
        (
            {"providers": [], "filtered": {"stage": "prompt", "provider": "azure-openai"}},
            None,
            "prompt",
        ),
    ],
    ids=["another-provider", "no-provider", "a-second-provider", "filtered-by-its-own-provider"],
)
async def test_wrong_provider_rule(fake_api, metadata, named, filtered_stage):
    arm = _ARMS[1]
    fake_api.replies = {arm.base_url: _reply("azure-openai", **metadata)}

    error = provider_error(arm, _reply("azure-openai", **metadata)["metadata"])
    report = await run_eval(RunRequest(api_key="rl_test", limit=1, judge=False, arms=[arm]))
    outcome = report.outcomes[0]

    assert outcome.error == error
    assert outcome.filtered_stage == filtered_stage

    if named is None:
        assert error is None
        assert outcome.citations == ["commit:1a2b3c4d", "issue:14111"]
        return

    # The message says which arm, what it should have been, and what answered instead, so the
    # operator can tell a misconfigured process from a provider that never answered.
    for name in named:
        assert name in error

    # Not the arm's answer, so it carries no score; who did answer is kept for the record.
    assert outcome.citations == []
    assert outcome.citation_recall == 0.0
    assert outcome.groundedness is None
    assert outcome.providers == metadata["providers"]


@pytest.mark.parametrize(
    ("failure", "complaint"),
    [
        (503, "503"),
        (httpx.ConnectError("connection refused"), "connection refused"),
        # httpx's timeouts carry no message, so the exception's text alone is "", an error
        # that reads as no error at all.
        (httpx.ReadTimeout(""), "ReadTimeout"),
        # The provider answered once and then went down: the reply still names the arm's own
        # provider, so only its `degraded` flag says this is not a synthesised answer.
        (
            _reply(
                "azure-openai", degraded=True,
                degradedReason="All providers unavailable: azure-openai. "
                               "Returning retrieved evidence without synthesis.",
            ),
            "degraded",
        ),
    ],
    ids=["status-503", "connection-refused", "read-timeout", "degraded"],
)
async def test_a_failed_arm_query_is_an_error_not_a_score(fake_api, failure, complaint):
    z = _ARMS[1]
    failing_question = load_golden()[1].question

    fake_api.replies = {
        **_each_arm_answers_as_itself(),
        z.base_url: lambda sent: failure if sent["question"] == failing_question else _reply("azure-openai"),
    }

    report = await run_eval(RunRequest(api_key="rl_test", limit=2, passes=2, judge=False, arms=_ARMS))

    errored = [o for o in report.outcomes if o.error is not None]

    # Every pass of that query on that arm, each still tagged with its arm and pass.
    assert [(o.id, o.arm, o.pass_index) for o in errored] == [("gq-002", "Z", 0), ("gq-002", "Z", 1)]
    for outcome in errored:
        assert complaint in outcome.error
        assert outcome.groundedness is None
        assert outcome.citations == []

    # The other arms' answers to that query, and Z's answer to the other query, are untouched.
    for outcome in report.outcomes:
        if outcome.error is None:
            assert outcome.citations == ["commit:1a2b3c4d", "issue:14111"]
    assert len(report.outcomes) - len(errored) == 10


@pytest.mark.parametrize(
    ("failure", "no_reply"),
    [
        (httpx.ReadTimeout(""), True),
        (httpx.ConnectError("connection refused"), True),
        (503, False),
        (_without_evidence({**_BODY, "metadata": None}), False),
    ],
    ids=["read-timeout", "connection-refused", "status-503", "malformed"],
)
async def test_a_request_that_got_no_reply_is_counted(fake_api, failure, no_reply):
    """It records $0, though the arm may have gone on answering and billed. Counted, the write-up
    can say its total leaves that spend out."""
    z = _ARMS[1]
    failing_question = load_golden()[1].question
    fake_api.replies = {
        **_each_arm_answers_as_itself(),
        z.base_url: lambda sent: failure if sent["question"] == failing_question else _reply("azure-openai"),
    }

    report = await run_eval(RunRequest(api_key="rl_test", limit=2, passes=2, judge=False, arms=_ARMS))

    errored = [o for o in report.outcomes if o.error is not None]
    assert len(errored) == 2
    assert all(o.no_reply is no_reply for o in errored)
    assert not any(o.no_reply for o in report.outcomes if o.error is None)

    assert [(s.arm, s.no_reply_count) for s in report.arm_summaries] == [
        ("A", 0), ("Z", 2 if no_reply else 0), ("O", 0),
    ]


def test_an_unscored_outcome_must_say_why():
    """An empty error is falsy, and would let an outcome with no answer pass for one."""
    query = load_golden()[0]

    with pytest.raises(ValueError, match="needs an error"):
        _unscored(query, _ARMS[1], 0, 12.0, "")


@pytest.mark.parametrize(
    ("reply", "spend"),
    [
        (_reply("anthropic", costUsd=0.02, cacheReadInputTokens=1000), (0.02, 4000, 300, 1000)),
        (
            _reply(
                "azure-openai", costUsd=0.02, cacheReadInputTokens=1000, degraded=True,
                degradedReason="All providers unavailable: azure-openai.",
            ),
            (0.02, 4000, 300, 1000),
        ),
        # No reply, so nothing reports a cost to keep.
        (503, (0.0, 0, 0, 0)),
    ],
    ids=["wrong-provider", "degraded", "no-reply"],
)
async def test_a_rejected_reply_keeps_what_it_cost(fake_api, reply, spend):
    """Not scored is not free: the reply's tokens were billed whether or not they count."""
    arm = _ARMS[1]
    fake_api.replies = {arm.base_url: reply}

    report = await run_eval(RunRequest(api_key="rl_test", limit=1, judge=False, arms=[arm]))
    outcome = report.outcomes[0]

    assert outcome.error is not None
    assert (
        outcome.cost_usd, outcome.tokens_in, outcome.tokens_out, outcome.cache_read_input_tokens,
    ) == spend
    assert report.answering_cost_usd == pytest.approx(spend[0])
    assert report.total_cost_usd == pytest.approx(spend[0])

    # The arm's total counts it, as money spent. Its cost per query has no answer to be over.
    (summary,) = report.arm_summaries
    assert summary.total_cost_usd == pytest.approx(spend[0])
    assert summary.mean_cost_usd_per_query is None

    # Only the spend is kept. The quality metrics are still those of an outcome with no answer.
    assert outcome.citations == []
    assert outcome.citation_recall == 0.0
    assert outcome.groundedness is None


async def test_each_answer_is_judged_against_its_own_evidence(fake_api, fake_judge, monkeypatch):
    """Two passes of one query on one arm are two answers, each with the evidence it cited."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    passes_seen = []

    def answer_differently_each_pass(sent):
        passes_seen.append(sent)
        reply = _reply("anthropic")
        return {
            **reply,
            "answer": f"answer from pass {len(passes_seen) - 1} [E1]",
            "citations": [{**reply["citations"][0], "evidence": [f"evidence from pass {len(passes_seen) - 1}"]}],
        }

    fake_api.replies = {_ARM.base_url: answer_differently_each_pass}

    await run_eval(RunRequest(api_key="rl_test", limit=1, passes=2, judge=True, arms=[_ARM]))

    assert [(answer, [e.text for e in evidence]) for answer, evidence in fake_judge.scored] == [
        ("answer from pass 0 [E1]", [["evidence from pass 0"]]),
        ("answer from pass 1 [E1]", [["evidence from pass 1"]]),
    ]


async def test_dry_run_prices_the_run_it_precedes(fake_api):
    fake_api.replies = {
        arm.base_url: _reply(arm.expected_provider, costUsd=0.01) for arm in _ARMS
    }

    report = await run_eval(
        RunRequest(api_key="rl_test", per_category=2, passes=3, judge=False, dry_run=True, arms=_ARMS)
    )

    categories = {query.question: query.category for query in load_golden()}
    every_category = set(categories.values())

    # One query per category per arm, once: the sample, not the study.
    assert len(fake_api.sent) == len(every_category) * len(_ARMS)
    for arm in _ARMS:
        asked = [categories[body["question"]] for url, body in fake_api.sent if url == arm.base_url]
        assert sorted(asked) == sorted(every_category)
    assert {o.pass_index for o in report.outcomes} == {0}

    # 10 queries, 3 passes, 3 arms: the run the operator is about to authorise.
    assert report.estimated_cost_usd_before_run == pytest.approx(0.01 * 10 * 3 * 3)

    # Unjudged, so the estimate leaves judging out, which would understate a judged run.
    assert report.estimate_note == "The estimate excludes judging."

    # The report's header is the run it prices, not the sample.
    assert report.query_ids == [q.id for q in runner_module._stratify(load_golden(), 2)]
    assert report.passes == 3


async def test_a_dry_run_publishes_no_comparisons(fake_api):
    """A dry run's sample is one query per category on one pass, under a header giving the priced
    run's queries and passes. A verdict on it could be read as the study's."""
    fake_api.replies = _each_arm_answers_as_itself()

    report = await run_eval(RunRequest(
        api_key="rl_test", per_category=2, passes=3, judge=False, dry_run=True, arms=_ARMS,
        comparisons=[("Z", "O")],
    ))

    assert report.dry_run is True
    assert report.comparisons == []


@pytest.mark.parametrize(
    ("failing", "note"),
    [
        ({"Z": "all"}, "no estimate: arm Z had 5 of 5 errors. The estimate excludes judging."),
        ({"O": "gq-014"}, "no estimate: arm O had 1 of 5 errors. The estimate excludes judging."),
        (
            {"Z": "all", "O": "gq-014"},
            "no estimate: arm Z had 5 of 5 errors; arm O had 1 of 5 errors. The estimate excludes judging.",
        ),
    ],
    ids=["one-arm-fails-its-whole-sample", "one-arm-fails-one-query", "two-arms-fail"],
)
async def test_a_dry_run_with_errors_refuses_to_estimate(fake_api, failing, note):
    """A sample with a hole in it cannot price the run.

    Cost varies enormously by category, so a mean over the categories that did answer is not
    the arm's cost, and an arm that answered nothing has no cost to scale up at all.
    """
    questions = {query.id: query.question for query in load_golden()}

    def failing_on(arm: Arm, which: str):
        answer = _reply(arm.expected_provider, costUsd=0.01)
        return lambda sent: 503 if which == "all" or sent["question"] == questions[which] else answer

    fake_api.replies = {
        arm.base_url: (
            failing_on(arm, failing[arm.name]) if arm.name in failing
            else _reply(arm.expected_provider, costUsd=0.01)
        )
        for arm in _ARMS
    }

    report = await run_eval(
        RunRequest(api_key="rl_test", per_category=2, passes=3, judge=False, dry_run=True, arms=_ARMS)
    )

    assert report.estimated_cost_usd_before_run is None
    assert report.estimate_note == note


async def test_dry_run_estimate_adds_the_judge_for_every_answer_of_the_run(fake_api, fake_judge, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    fake_api.replies = {
        arm.base_url: _reply(arm.expected_provider, costUsd=0.01) for arm in _ARMS
    }

    report = await run_eval(
        RunRequest(api_key="rl_test", per_category=2, passes=3, judge=True, dry_run=True, arms=_ARMS)
    )

    # A dry run estimates the judge but never calls it.
    assert fake_judge.estimated == [5 * 3]
    assert fake_judge.scored == []
    assert report.judge_cost_usd == 0.0
    assert report.estimated_cost_usd_before_run == pytest.approx(
        0.01 * 10 * 3 * 3 + fake_judge.usd_per_item * 10 * 3 * 3
    )


_ALLOWANCE = (
    f"The judge's output share is an allowance of {JUDGE_OUTPUT_ALLOWANCE_TOKENS} tokens per "
    "judgement, not a measurement."
)


@pytest.mark.parametrize(
    ("z_fails", "note"),
    [
        (False, _ALLOWANCE),
        (True, "no estimate: arm Z had 5 of 5 errors. " + _ALLOWANCE),
    ],
    ids=["estimated", "refused"],
)
async def test_a_judged_dry_run_says_the_judge_output_is_an_allowance(
    fake_api, fake_judge, monkeypatch, z_fails, note,
):
    """count_tokens counts the judge's input only. What it will write is allowed for, not known."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    fake_api.replies = {
        arm.base_url: 503 if z_fails and arm.name == "Z" else _reply(arm.expected_provider, costUsd=0.01)
        for arm in _ARMS
    }

    report = await run_eval(
        RunRequest(api_key="rl_test", per_category=2, passes=3, judge=True, dry_run=True, arms=_ARMS)
    )

    assert report.estimate_note == note
    assert (report.estimated_cost_usd_before_run is None) == z_fails


async def test_the_judge_is_blinded_to_the_arm(fake_api, recording_anthropic):
    """The judge is given the question, the answer and the evidence it cited, and nothing that
    says which arm, provider or model wrote the answer (spec §8). A judge that knew could favour
    one, and the difference it found would be its own."""
    arms = [arm for arm in _ARMS if arm.expected_provider in ("azure-openai", "openai")]
    fake_api.replies = {arm.base_url: _reply(arm.expected_provider) for arm in arms}

    report = await run_eval(
        RunRequest(api_key="rl_test", per_category=2, passes=3, judge=True, arms=arms)
    )

    # Every answer was judged, so the search below is over every prompt the run sent.
    judgements = [sent for sent in recording_anthropic.sent if "max_tokens" in sent]
    assert len(judgements) == len(report.outcomes) == 10 * 3 * len(arms)
    assert all(_BODY["answer"] in "\n".join(_strings(sent["messages"])) for sent in judgements)

    leaks = ["azure-openai", "openai", "anthropic", *_MODELS.values()]
    for sent in recording_anthropic.sent:
        # The model the request is addressed to is the judge's own, and is not part of the prompt.
        assert sent["model"] == "claude-sonnet-5"
        prompt = "\n".join(_strings({k: v for k, v in sent.items() if k != "model"}))

        for leak in leaks:
            assert leak not in prompt.lower()
        for arm in arms:
            assert re.search(rf"\b{re.escape(arm.name)}\b", prompt) is None


async def test_judge_cost_lands_on_the_outcome(fake_api, fake_judge, monkeypatch):
    """What each judgement cost is kept on the outcome it judged, and the report sums them.

    Kept apart from cost_usd, which is what the arm reported for answering: judging is the
    harness's cost, not the arm's.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    z = _ARMS[1]
    failing_question = load_golden()[1].question
    fake_api.replies = {
        **_each_arm_answers_as_itself(),
        z.base_url: lambda sent: 503 if sent["question"] == failing_question else _reply("azure-openai"),
    }
    fake_judge.unparseable = {3}

    report = await run_eval(RunRequest(api_key="rl_test", limit=2, passes=2, judge=True, arms=_ARMS))

    judged = [o for o in report.outcomes if o.error is None]

    # Judged in run order, each judgement's cost on the outcome it judged.
    assert len(judged) == len(fake_judge.scored) == 10
    assert [o.judge_cost_usd for o in judged] == pytest.approx([0.001 * n for n in range(1, 11)])

    # An unparseable judgement scores nothing and was still billed.
    assert judged[2].groundedness is None
    assert judged[2].judge_cost_usd == pytest.approx(0.003)

    # An outcome with an error is not judged, so it costs the judge nothing.
    assert [o.judge_cost_usd for o in report.outcomes if o.error is not None] == [0.0, 0.0]

    assert report.judge_cost_usd == pytest.approx(sum(0.001 * n for n in range(1, 11)))
    assert report.answering_cost_usd == pytest.approx(sum(o.cost_usd for o in report.outcomes))
    assert report.total_cost_usd == pytest.approx(report.answering_cost_usd + report.judge_cost_usd)

    # Pricing the run is the dry run's job. A real run neither counts its judge's tokens after the
    # answering has been paid for nor reports a judge-only figure as what the run would cost.
    assert fake_judge.estimated == []
    assert report.estimated_cost_usd_before_run is None
    assert report.estimate_note is None


async def test_report_carries_summaries_and_the_requested_comparisons(fake_api, fake_judge, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    fake_api.replies = _each_arm_answers_as_itself()

    report = await run_eval(RunRequest(
        api_key="rl_test", per_category=2, passes=3, judge=True, arms=_ARMS,
        comparisons=[("Z", "O"), ("Z", "A")],
    ))

    # 4 metrics × 2 comparisons, each comparison as requested and the metrics in their fixed order.
    assert [(c.x, c.y, c.metric) for c in report.comparisons] == [
        (x, y, metric) for x, y in [("Z", "O"), ("Z", "A")] for metric in QUALITY_METRICS
    ]

    # Each metric over the queries it applies to (spec §8): 10, the 8 answerable, and the 6 with
    # something to contain, every pass of each paired.
    assert [(c.k, c.pairs, c.passes) for c in report.comparisons[:4]] == [
        (10, 30, 3), (8, 24, 3), (8, 24, 3), (6, 18, 3),
    ]
    assert list(report.comparisons[3].per_query_delta) == ["gq-001", "gq-002", "gq-022", "gq-023", "gq-029", "gq-030"]

    # The pre-registered selection's nominal sizes, which every comparison carries, short or not.
    assert [(c.nominal_k, c.nominal_pairs) for c in report.comparisons] == 2 * [
        (10, 30), (8, 24), (8, 24), (6, 18),
    ]

    # One summary per arm, in the request's arm order.
    assert [(s.arm, s.outcome_count, s.models_seen) for s in report.arm_summaries] == [
        (arm.name, 30, [_MODELS[arm.expected_provider]]) for arm in _ARMS
    ]

    assert report.passes == 3
    assert report.arms == _ARMS
    assert len(report.query_ids) == 10
    assert report.dry_run is False


async def test_started_at_is_read_before_the_first_query_is_sent(fake_api, monkeypatch):
    """A sweep runs for many minutes; read at the end, the published date could be the next day."""
    requests_sent_when_read = []

    class _Clock:
        @staticmethod
        def now(tz=None):
            requests_sent_when_read.append(len(fake_api.sent))
            return datetime(2026, 10, 2, 23, 59, tzinfo=tz)

    monkeypatch.setattr(runner_module, "datetime", _Clock)

    report = await run_eval(RunRequest(api_key="rl_test", limit=1, judge=False, arms=[_ARM]))

    assert requests_sent_when_read == [0]
    assert report.started_at == "2026-10-02T23:59:00+00:00"


# --- A run that has started answering must finish, whatever the replies and the judge do. ---


@pytest.mark.parametrize("dry_run", [False, True], ids=["run", "dry-run"])
async def test_a_judged_run_without_a_judge_key_fails_before_any_query_is_sent(fake_api, monkeypatch, dry_run):
    """Built only after answering, the judge's key check failed once every answer was paid for."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(judge_module, "AsyncAnthropic", _RecordingAnthropic)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        await run_eval(RunRequest(api_key="rl_test", limit=1, judge=True, dry_run=dry_run, arms=[_ARM]))

    assert fake_api.sent == []


def _without(reply: dict, field: str) -> dict:
    """`reply` with one field the runner reads left out: a top-level field, or a metadata key."""
    if field in reply:
        return {k: v for k, v in reply.items() if k != field}
    return {**reply, "metadata": {k: v for k, v in reply["metadata"].items() if k != field}}


_WRONG_PROVIDER = _reply("azure-openai", provider="anthropic", providers=["anthropic"])


@pytest.mark.parametrize(
    ("malformed", "complaint", "spend"),
    [
        (_without(_reply("azure-openai"), "answer"), "'answer'", (0.0123, 4000, 300, 0)),
        (_without(_reply("azure-openai"), "citations"), "'citations'", (0.0123, 4000, 300, 0)),
        (_without(_reply("azure-openai"), "metadata"), "'metadata'", (0.0, 0, 0, 0)),
        ({**_reply("azure-openai"), "metadata": None}, "'NoneType'", (0.0, 0, 0, 0)),
        (_without(_reply("azure-openai"), "degraded"), "'degraded'", (0.0123, 4000, 300, 0)),
        (
            _without(_reply("azure-openai"), "unresolvedCitationMarkers"),
            "'unresolvedCitationMarkers'", (0.0123, 4000, 300, 0),
        ),
        (_without(_reply("azure-openai"), "costUsd"), "'costUsd'", (0.0, 4000, 300, 0)),
        (_without(_reply("azure-openai"), "tokensIn"), "'tokensIn'", (0.0123, 0, 300, 0)),
        (_without(_reply("azure-openai"), "tokensOut"), "'tokensOut'", (0.0123, 4000, 0, 0)),
        (
            _without(_reply("azure-openai", cacheReadInputTokens=7), "cacheReadInputTokens"),
            "'cacheReadInputTokens'", (0.0123, 4000, 300, 0),
        ),
        (
            {**_reply("azure-openai"), "citations": [{"marker": 1, "key": "1a2b3c4d"}]},
            "'type'", (0.0123, 4000, 300, 0),
        ),
        # A reply already rejected, as another provider's, and missing a cost key besides.
        (_without(_WRONG_PROVIDER, "costUsd"), "'costUsd'", (0.0, 4000, 300, 0)),
    ],
    ids=[
        "no-answer", "no-citations", "no-metadata", "null-metadata", "no-degraded",
        "no-unresolved-markers", "no-cost", "no-tokens-in", "no-tokens-out", "no-cache-read",
        "citation-without-type", "wrong-provider-without-cost",
    ],
)
async def test_a_malformed_reply_is_an_error_and_the_run_goes_on(fake_api, malformed, complaint, spend):
    """A 200 missing a field the runner reads is that arm's error on that query and pass.

    Raised out of the sweep, it ended the run and lost every answer already paid for. Any cost the
    reply did report is kept, because it was billed whether or not the reply can be read.
    """
    z = _ARMS[1]
    failing_question = load_golden()[1].question
    fake_api.replies = {
        **_each_arm_answers_as_itself(),
        z.base_url: lambda sent: malformed if sent["question"] == failing_question else _reply("azure-openai"),
    }

    report = await run_eval(RunRequest(api_key="rl_test", limit=2, passes=2, judge=False, arms=_ARMS))

    errored = [o for o in report.outcomes if o.error is not None]
    assert [(o.id, o.arm, o.pass_index) for o in errored] == [("gq-002", "Z", 0), ("gq-002", "Z", 1)]
    for outcome in errored:
        assert outcome.error.startswith("arm Z sent a malformed reply: ")
        assert complaint in outcome.error
        assert outcome.citations == []
        assert outcome.groundedness is None
        assert (
            outcome.cost_usd, outcome.tokens_in, outcome.tokens_out, outcome.cache_read_input_tokens,
        ) == spend

    # Every other answer of the run is untouched, and the money the reply reported is counted.
    assert len(report.outcomes) - len(errored) == 10
    assert report.answering_cost_usd == pytest.approx(10 * 0.0123 + 2 * spend[0])


@pytest.mark.parametrize(
    "failure",
    [
        anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.invalid")),
        TypeError("float() argument must be a string or a real number, not 'NoneType'"),
        RuntimeError("anything at all"),
    ],
    ids=["anthropic-api-error", "type-error", "any-exception"],
)
async def test_a_judge_that_raises_leaves_that_answer_unjudged_and_the_run_goes_on(
    fake_api, fake_judge, monkeypatch, failure,
):
    """One failed judgement drops that answer from groundedness, and nothing else."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    fake_api.replies = _each_arm_answers_as_itself()
    fake_judge.raises = {2: failure}

    report = await run_eval(RunRequest(api_key="rl_test", limit=2, passes=2, judge=True, arms=_ARMS))

    # Every answer was put to the judge, the one after the failure included.
    assert len(fake_judge.scored) == len(report.outcomes) == 12

    failed = report.outcomes[1]
    assert failed.error is None
    assert failed.groundedness is None
    assert failed.groundedness_reason.startswith("judge failed: ")
    assert type(failure).__name__ in failed.groundedness_reason

    # No reply, so no usage to price.
    assert failed.judge_cost_usd == 0.0

    others = [o for o in report.outcomes if o is not failed]
    assert all(o.groundedness == 1.0 for o in others)
    assert all(o.citations == ["commit:1a2b3c4d", "issue:14111"] for o in report.outcomes)
    assert report.judge_cost_usd == pytest.approx(sum(0.001 * n for n in range(1, 13) if n != 2))

    z = next(s for s in report.arm_summaries if s.arm == "Z")
    assert (z.groundedness_count, z.judge_failure_count) == (3, 1)
    assert all(s.judge_failure_count == 0 for s in report.arm_summaries if s.arm != "Z")


@pytest.mark.parametrize(
    ("reply", "why"),
    [
        ('{"score": null, "reason": "no idea"}', "unparseable"),
        ('{"score": NaN, "reason": "no idea"}', "not a finite number"),
        ('{"score": Infinity, "reason": "no idea"}', "not a finite number"),
        ('{"score": 5, "reason": "very grounded"}', "outside [0, 1]"),
        ('{"score": -0.5, "reason": "very ungrounded"}', "outside [0, 1]"),
    ],
    ids=["null", "nan", "infinity", "above-one", "below-zero"],
)
async def test_a_judge_score_that_is_not_in_range_is_no_score(fake_api, recording_anthropic, reply, why):
    """A score of 5 could push a groundedness delta past the rule's 0.10, and NaN poisons every mean.

    Each is handled as a reply that cannot be read: no score, a reason saying why, and the cost of
    the reply kept, since it was billed.
    """
    recording_anthropic.reply_text = reply
    fake_api.replies = {_ARM.base_url: _reply("anthropic")}

    report = await run_eval(RunRequest(api_key="rl_test", limit=1, passes=2, judge=True, arms=[_ARM]))

    for outcome in report.outcomes:
        assert outcome.error is None
        assert outcome.groundedness is None
        assert why in outcome.groundedness_reason
        assert outcome.judge_cost_usd == pytest.approx(1_000 * 3 / 1e6 + 50 * 15 / 1e6)

    (summary,) = report.arm_summaries
    assert (summary.mean_groundedness, summary.groundedness_count, summary.judge_failure_count) == (None, 0, 2)
