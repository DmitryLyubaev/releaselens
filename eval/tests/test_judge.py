"""The judge's prompt. No network: the Anthropic client is replaced wholesale.

The judge is the one part of this harness that spends money per call, so nothing here may
reach the real client — every test drives a fake that records what it was asked and answers
from a fixture.
"""

from types import SimpleNamespace

import pytest

from app import judge as judge_module
from app.judge import GroundednessJudge
from app.models import CitedEvidence

_CHUNK_ONE = "[commit 1a2b3c4] fix: planner null reference\nauthor: Alice | committed: 2024-03-02"
_CHUNK_TWO = "the planner dereferenced a null step when the goal was empty; guard added"


class _FakeMessages:
    def __init__(self) -> None:
        self.counted: list[dict] = []
        self.created: list[dict] = []

        # What every reply says it used, as a real reply's `usage` does.
        self.usage = SimpleNamespace(input_tokens=1_000, output_tokens=50)

    async def count_tokens(self, **kwargs):
        self.counted.append(kwargs)
        return SimpleNamespace(input_tokens=len(kwargs["messages"][0]["content"]))

    async def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text='{"score": 1.0, "reason": "supported"}')],
            usage=self.usage,
        )


class _FakeAnthropic:
    def __init__(self, api_key: str | None = None) -> None:
        self.messages = _FakeMessages()


@pytest.fixture
def fake_judge(monkeypatch) -> GroundednessJudge:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setattr(judge_module, "AsyncAnthropic", _FakeAnthropic)
    return GroundednessJudge("claude-sonnet-5")


def _evidence(**overrides) -> CitedEvidence:
    fields = {
        "marker": 2,
        "id": "commit:1a2b3c4d5e",
        "title": "fix: planner null reference",
        "url": "https://github.com/microsoft/semantic-kernel/commit/1a2b3c4d5e",
        "text": [_CHUNK_ONE],
    }
    fields.update(overrides)
    return CitedEvidence(**fields)


def _sent_prompt(fake_judge: GroundednessJudge) -> str:
    return fake_judge._client.messages.created[0]["messages"][0]["content"]


def test_the_module_claims_no_batch_api():
    """Nothing here uses the Batch API, so nothing here may say it does (F7)."""
    assert "Batch" not in judge_module.__doc__


async def test_score_sends_the_evidence_text_not_just_the_identifier(fake_judge):
    judgement = await fake_judge.score("What fixed the planner?", "Fixed in [E2].", [_evidence()])

    assert (judgement.score, judgement.reason) == (1.0, "supported")

    prompt = _sent_prompt(fake_judge)
    assert _CHUNK_ONE in prompt

    # The identifier alone was the defect: the judge could only report that it had been
    # handed a bare reference. It is still present, as a label for the text.
    assert "commit:1a2b3c4d5e" in prompt


async def test_each_artefact_is_labelled_with_the_marker_the_answer_used(fake_judge):
    await fake_judge.score("q", "Fixed in [E2].", [_evidence()])

    # Without the marker the judge has to guess which passage "[E2]" referred to.
    assert "[E2]" in _sent_prompt(fake_judge)


async def test_every_passage_of_a_multi_chunk_artefact_is_shown(fake_judge):
    await fake_judge.score("q", "Fixed in [E2].", [_evidence(text=[_CHUNK_ONE, _CHUNK_TWO])])

    prompt = _sent_prompt(fake_judge)

    # A claim supported by the second chunk reads as unsupported to a judge shown only the
    # first, which is a groundedness failure invented by the harness.
    assert _CHUNK_ONE in prompt
    assert _CHUNK_TWO in prompt


async def test_an_artefact_with_no_text_says_so_rather_than_appearing_blank(fake_judge):
    await fake_judge.score("q", "Fixed in [E2].", [_evidence(text=[])])

    prompt = _sent_prompt(fake_judge)

    assert "no text was available" in prompt
    assert "commit:1a2b3c4d5e" in prompt


async def test_an_answer_citing_nothing_says_so(fake_judge):
    await fake_judge.score("q", "The evidence does not answer this.", [])

    assert "cited no evidence" in _sent_prompt(fake_judge)


async def test_estimate_prices_exactly_the_prompt_that_scoring_sends(fake_judge):
    """The estimate is read before money is spent, so it must not be able to drift.

    Evidence text raises judge input tokens substantially; an estimate that priced a
    cheaper prompt than the run sends would understate the sweep by that whole margin.
    """
    items = [("What fixed the planner?", "Fixed in [E2].", [_evidence(text=[_CHUNK_ONE, _CHUNK_TWO])])]

    total = await fake_judge.estimate_cost_usd(items)
    await fake_judge.score(*items[0])

    counted = fake_judge._client.messages.counted[0]
    created = fake_judge._client.messages.created[0]

    assert counted["model"] == created["model"]
    assert counted["system"] == created["system"]
    assert counted["messages"] == created["messages"]

    # And the count really is over the evidence, not over a stub of it.
    assert total == pytest.approx(len(counted["messages"][0]["content"]) * 3 / 1e6 + 512 * 15 / 1e6)


async def test_estimating_an_artefact_with_text_costs_more_than_one_without(fake_judge):
    """Guards the claim that the estimate reflects the evidence, not just the answer."""
    bare = await fake_judge.estimate_cost_usd([("q", "a", [_evidence(text=[])])])
    full = await fake_judge.estimate_cost_usd([("q", "a", [_evidence(text=[_CHUNK_ONE, _CHUNK_TWO])])])

    assert full > bare


async def test_a_judgement_is_priced_with_output_tokens(fake_judge):
    """Sonnet 5 bills output at five times its input rate, so leaving it out understates (F8)."""
    fake_judge._client.messages.usage = SimpleNamespace(input_tokens=10_000, output_tokens=200)

    judgement = await fake_judge.score("q", "Fixed in [E2].", [_evidence()])

    assert judgement.cost_usd == pytest.approx(10_000 * 3 / 1e6 + 200 * 15 / 1e6)


async def test_the_estimate_includes_the_output_allowance(fake_judge):
    """count_tokens counts input only, so the estimate adds an allowance for what the judge writes."""
    estimate = await fake_judge.estimate_cost_usd([("q", "Fixed in [E2].", [_evidence()])])

    # The fake counts one token per character of the prompt it was asked to count.
    counted = len(fake_judge._client.messages.counted[0]["messages"][0]["content"])
    assert estimate == pytest.approx(counted * 3 / 1e6 + 512 * 15 / 1e6)


async def test_unparseable_judge_output_is_reported_rather_than_scored_zero(fake_judge, monkeypatch):
    async def _garbage(**kwargs):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="I could not decide.")],
            usage=SimpleNamespace(input_tokens=10_000, output_tokens=200),
        )

    monkeypatch.setattr(fake_judge._client.messages, "create", _garbage)

    judgement = await fake_judge.score("q", "a", [_evidence()])

    assert judgement.score == -1.0
    assert "unparseable" in judgement.reason

    # Scoring nothing is not free: the reply was billed whether or not it could be read.
    assert judgement.cost_usd == pytest.approx(10_000 * 3 / 1e6 + 200 * 15 / 1e6)


def _replying(text: str):
    async def _create(**kwargs):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=10_000, output_tokens=200),
        )
    return _create


@pytest.mark.parametrize(
    ("reply", "why"),
    [
        # float(None) raised a TypeError nothing caught, which ended the whole run.
        ('{"score": null, "reason": "no idea"}', "unparseable"),
        ('{"score": "high", "reason": "no idea"}', "unparseable"),
        ('[1.0, "supported"]', "unparseable"),
        # Python's json reads NaN and Infinity, and a NaN poisons every mean it reaches.
        ('{"score": NaN, "reason": "no idea"}', "not a finite number: nan"),
        ('{"score": Infinity, "reason": "no idea"}', "not a finite number: inf"),
        ('{"score": -Infinity, "reason": "no idea"}', "not a finite number: -inf"),
        # On a 0 to 1 scale, a 5 alone could push a delta past the rule's 0.10.
        ('{"score": 5, "reason": "very grounded"}', "outside [0, 1]: 5.0"),
        ('{"score": -0.5, "reason": "very ungrounded"}', "outside [0, 1]: -0.5"),
        ('{"score": 1.0000001, "reason": "grounded"}', "outside [0, 1]: 1.0000001"),
    ],
    ids=["null", "string", "not-an-object", "nan", "infinity", "minus-infinity", "five", "negative", "just-over-one"],
)
async def test_a_score_that_is_not_a_number_in_range_is_no_score(fake_judge, monkeypatch, reply, why):
    monkeypatch.setattr(fake_judge._client.messages, "create", _replying(reply))

    judgement = await fake_judge.score("q", "a", [_evidence()])

    assert judgement.score == -1.0
    assert why in judgement.reason
    assert judgement.cost_usd == pytest.approx(10_000 * 3 / 1e6 + 200 * 15 / 1e6)


@pytest.mark.parametrize("score", [0.0, 0.5, 1.0])
async def test_a_score_in_range_is_kept(fake_judge, monkeypatch, score):
    """The bounds are scores the rubric gives, so they are in range."""
    monkeypatch.setattr(fake_judge._client.messages, "create", _replying(f'{{"score": {score}, "reason": "ok"}}'))

    judgement = await fake_judge.score("q", "a", [_evidence()])

    assert (judgement.score, judgement.reason) == (score, "ok")
