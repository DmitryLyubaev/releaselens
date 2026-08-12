"""LLM-as-judge for groundedness.

Every sweep prices itself with count_tokens (free) before spending anything, and
uses the Batch API where a sweep is large — eval is the textbook non-latency-sensitive
workload and the Batch API is half price.
"""

from __future__ import annotations

import json
import os

from anthropic import AsyncAnthropic

from .models import CitedEvidence

_JUDGE_SYSTEM = """You score whether an answer is grounded in the evidence it cites.

You are given a question, an answer, and the evidence the answer cites. This is what the
answer rests on, not everything the system retrieved — so a claim resting on something the
answer did not cite is unsupported here, which is the point.

Each piece of evidence is given as its marker exactly as the answer writes it ([E1], [E2],
…), its identifier, its title, and then the full text the system showed. An artefact may
have several passages; they are all shown, so treat them together as that artefact's
evidence. An artefact whose text says "(no text was available for this artefact)" cannot
support anything — score claims resting only on it as unsupported.

Score groundedness from 0.0 to 1.0:
- 1.0 — every factual claim in the answer is supported by the evidence shown.
- 0.5 — the answer is broadly supported but contains at least one claim the evidence
  does not establish.
- 0.0 — the answer asserts things the evidence does not support, or contradicts it.

An answer that correctly states the evidence does not contain what was asked scores 1.0.
Declining to answer is grounded behaviour, not a failure.

One kind of claim carries no marker by design. The system has tools that COMPUTE over the
whole corpus — a count, or an enumeration — rather than retrieving passages from it. Their
results are not artefacts, so they get no citation, and the answering model is explicitly
instructed not to attach one. Such a figure is stated together with the predicate that
produced it: the entity counted, the date field, and the window. Do not mark a computed
figure unsupported merely because no evidence backs it — that is the system working as
designed, and penalising it would reward retrieving toward an answer over calculating one.

You are not being asked whether such a number is arithmetically right; you cannot check
that and neither can the evidence. Judge only whether the answer represents it honestly:
stating the predicate, not silently widening it, and saying so when the tool reported that
the corpus does not cover the whole window asked about. An answer that quietly presents a
count over a partial corpus as a count of the repository IS ungrounded, and that is the
failure worth catching here.

Reply with JSON only: {"score": <float>, "reason": "<one sentence>"}"""


class GroundednessJudge:
    def __init__(self, model: str = "claude-sonnet-5") -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set; the judge cannot run.")
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    @staticmethod
    def _render(evidence: list[CitedEvidence]) -> str:
        """The cited artefacts, each with its marker, identifier and full text.

        The marker is printed because it is how the answer refers to the artefact: without
        it the judge has to guess which of five passages "[E3]" meant. Absent text is
        stated explicitly rather than rendered as a blank — a judge shown an empty space
        cannot tell "this artefact says nothing relevant" from "nobody sent me the text",
        and that confusion is exactly what scored two real answers 0.0.
        """
        if not evidence:
            return "(the answer cited no evidence)"

        blocks = []
        for item in evidence:
            body = "\n\n".join(item.text) if item.text else "(no text was available for this artefact)"
            blocks.append(f"[E{item.marker}] {item.id} — {item.title}\n{body}")

        return "\n\n---\n\n".join(blocks)

    def _prompt(self, question: str, answer: str, evidence: list[CitedEvidence]) -> str:
        return (
            f"Question:\n{question}\n\n"
            f"Answer:\n{answer}\n\n"
            f"Evidence cited:\n{self._render(evidence)}"
        )

    async def estimate_cost(self, items: list[tuple[str, str, list[CitedEvidence]]]) -> int:
        """Total input tokens for the whole sweep. count_tokens is free — call it first.

        Counts the same system prompt and the same _prompt() the scoring call sends, so the
        estimate moves with the evidence text automatically. Its whole purpose is to be
        trusted before money is spent, so the two must not be able to drift apart: any
        cheaper approximation here would have gone stale the moment evidence text was added.
        """
        total = 0
        for question, answer, evidence in items:
            counted = await self._client.messages.count_tokens(
                model=self._model,
                system=_JUDGE_SYSTEM,
                messages=[{"role": "user", "content": self._prompt(question, answer, evidence)}],
            )
            total += counted.input_tokens
        return total

    async def score(
        self, question: str, answer: str, evidence: list[CitedEvidence]
    ) -> tuple[float, str]:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=256,
            system=_JUDGE_SYSTEM,
            messages=[{"role": "user", "content": self._prompt(question, answer, evidence)}],
        )

        text = "".join(block.text for block in response.content if block.type == "text")

        try:
            parsed = json.loads(text[text.index("{") : text.rindex("}") + 1])
            return float(parsed["score"]), str(parsed["reason"])
        except (ValueError, KeyError) as exc:
            # A judge that cannot be parsed must not silently score zero — that would
            # look like a groundedness failure in the report when it is a harness bug.
            return -1.0, f"judge output unparseable: {exc}"
