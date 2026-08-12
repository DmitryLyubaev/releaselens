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

    async def count_tokens(self, **kwargs):
        self.counted.append(kwargs)
        return SimpleNamespace(input_tokens=len(kwargs["messages"][0]["content"]))

    async def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text='{"score": 1.0, "reason": "supported"}')]
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


async def test_score_sends_the_evidence_text_not_just_the_identifier(fake_judge):
    score, reason = await fake_judge.score("What fixed the planner?", "Fixed in [E2].", [_evidence()])

    assert (score, reason) == (1.0, "supported")

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

    total = await fake_judge.estimate_cost(items)
    await fake_judge.score(*items[0])

    counted = fake_judge._client.messages.counted[0]
    created = fake_judge._client.messages.created[0]

    assert counted["model"] == created["model"]
    assert counted["system"] == created["system"]
    assert counted["messages"] == created["messages"]

    # And the count really is over the evidence, not over a stub of it.
    assert total == len(counted["messages"][0]["content"])


async def test_estimating_an_artefact_with_text_costs_more_than_one_without(fake_judge):
    """Guards the claim that the estimate reflects the evidence, not just the answer."""
    bare = await fake_judge.estimate_cost([("q", "a", [_evidence(text=[])])])
    full = await fake_judge.estimate_cost([("q", "a", [_evidence(text=[_CHUNK_ONE, _CHUNK_TWO])])])

    assert full > bare


async def test_unparseable_judge_output_is_reported_rather_than_scored_zero(fake_judge, monkeypatch):
    async def _garbage(**kwargs):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="I could not decide.")])

    monkeypatch.setattr(fake_judge._client.messages, "create", _garbage)

    score, reason = await fake_judge.score("q", "a", [_evidence()])

    assert score == -1.0
    assert "unparseable" in reason
