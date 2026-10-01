"""What the runner asks the API for, and what it does with the reply.

No network and no real judge: the HTTP client is replaced, every sweep that judges uses a
fake judge, and the rest run with judge=False, so nothing here can reach api.anthropic.com.
"""

import httpx
import pytest

from app import runner as runner_module
from app.golden import load_golden
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
    """Stands in for GroundednessJudge: prices each item at a fixed token count, and records
    every answer it scores with the evidence it was given."""

    tokens_per_item = 10_000
    scored: list[tuple[str, list]] = []

    def __init__(self, model: str) -> None:
        pass

    async def estimate_cost(self, items) -> int:
        return self.tokens_per_item * len(items)

    async def score(self, question, answer, evidence):
        _FakeJudge.scored.append((answer, evidence))
        return 1.0, "supported"


@pytest.fixture
def fake_judge(monkeypatch) -> type[_FakeJudge]:
    _FakeJudge.scored = []
    monkeypatch.setattr(runner_module, "GroundednessJudge", _FakeJudge)
    return _FakeJudge


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

    assert report.query_count == 1

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
    assert report.total_cost_usd == pytest.approx(spend[0])

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
    assert report.estimate_note is None


@pytest.mark.parametrize(
    ("failing", "note"),
    [
        ({"Z": "all"}, "no estimate: arm Z had 5 of 5 errors"),
        ({"O": "gq-014"}, "no estimate: arm O had 1 of 5 errors"),
        ({"Z": "all", "O": "gq-014"}, "no estimate: arm Z had 5 of 5 errors; arm O had 1 of 5 errors"),
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

    judge_usd_per_answer = fake_judge.tokens_per_item / 1_000_000 * runner_module._JUDGE_INPUT_USD_PER_MTOK

    # A dry run estimates the judge but never calls it.
    assert fake_judge.scored == []
    assert report.estimated_cost_usd_before_run == pytest.approx(
        0.01 * 10 * 3 * 3 + judge_usd_per_answer * 10 * 3 * 3
    )
