"""LLM-as-judge for groundedness.

Every sweep prices itself with count_tokens (free) before spending anything, and
uses the Batch API where a sweep is large — eval is the textbook non-latency-sensitive
workload and the Batch API is half price.
"""

from __future__ import annotations

import json
import os

from anthropic import AsyncAnthropic

_JUDGE_SYSTEM = """You score whether an answer is grounded in the evidence it cites.

You are given a question, an answer, and the evidence the answer cites. This is what the
answer rests on, not everything the system retrieved — so a claim resting on something the
answer did not cite is unsupported here, which is the point.

Score groundedness from 0.0 to 1.0:
- 1.0 — every factual claim in the answer is supported by the evidence shown.
- 0.5 — the answer is broadly supported but contains at least one claim the evidence
  does not establish.
- 0.0 — the answer asserts things the evidence does not support, or contradicts it.

An answer that correctly states the evidence does not contain what was asked scores 1.0.
Declining to answer is grounded behaviour, not a failure.

Reply with JSON only: {"score": <float>, "reason": "<one sentence>"}"""


class GroundednessJudge:
    def __init__(self, model: str = "claude-sonnet-5") -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set; the judge cannot run.")
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    def _prompt(self, question: str, answer: str, evidence: list[str]) -> str:
        joined = "\n".join(f"- {item}" for item in evidence) or "(no evidence was cited)"
        return f"Question:\n{question}\n\nAnswer:\n{answer}\n\nEvidence cited:\n{joined}"

    async def estimate_cost(self, items: list[tuple[str, str, list[str]]]) -> int:
        """Total input tokens for the whole sweep. count_tokens is free — call it first."""
        total = 0
        for question, answer, evidence in items:
            counted = await self._client.messages.count_tokens(
                model=self._model,
                system=_JUDGE_SYSTEM,
                messages=[{"role": "user", "content": self._prompt(question, answer, evidence)}],
            )
            total += counted.input_tokens
        return total

    async def score(self, question: str, answer: str, evidence: list[str]) -> tuple[float, str]:
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
