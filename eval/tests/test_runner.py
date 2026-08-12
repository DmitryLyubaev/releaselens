"""What the runner asks the API for, and what it does with the reply.

No network and no judge: the HTTP client is replaced and the sweep runs with judge=False,
so nothing here can reach api.anthropic.com.
"""

import pytest

from app import runner as runner_module
from app.models import RunRequest
from app.runner import citation_ids, cited_evidence, run_eval

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
        "degraded": False,
        "unresolvedCitationMarkers": [],
    },
}


def _without_evidence(body: dict) -> dict:
    """The same reply as an API that was not asked for evidence would send it."""
    return {
        **body,
        "citations": [{k: v for k, v in c.items() if k != "evidence"} for c in body["citations"]],
    }


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    sent: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> bool:
        return False

    async def post(self, url, headers=None, json=None):
        _FakeClient.sent.append(json)
        return _FakeResponse(_BODY)


@pytest.fixture
def captured_requests(monkeypatch) -> list[dict]:
    _FakeClient.sent = []

    # runner.py holds the module, not the class, so patching the attribute on httpx itself
    # is what the runner will see.
    monkeypatch.setattr(runner_module.httpx, "AsyncClient", _FakeClient)
    return _FakeClient.sent


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


async def test_the_run_asks_the_api_for_the_evidence_text(captured_requests):
    report = await run_eval(
        RunRequest(api_key="rl_test", limit=1, judge=False, api_base_url="http://api.invalid")
    )

    assert report.query_count == 1

    sent = captured_requests[0]
    assert sent["includeEvidence"] is True
    assert sent["question"]


async def test_the_run_still_scores_recall_and_precision_over_identifiers(captured_requests):
    report = await run_eval(
        RunRequest(api_key="rl_test", limit=1, judge=False, api_base_url="http://api.invalid")
    )

    outcome = report.outcomes[0]

    # The outcome carries identifiers, exactly as before — the evidence text is held beside
    # the outcomes rather than on them, so reports stay readable.
    assert outcome.citations == ["commit:1a2b3c4d", "issue:14111"]
    assert "evidence" not in outcome.model_dump()
