"""The question set: sampling, the writer, the mechanical checks, the spot-check and freezing.

No network: the Anthropic client is a fake that answers from a queue and records every call,
as `tests/test_judge.py` does, so nothing here can reach Claude or spend anything.
"""

import hashlib
import json
from types import SimpleNamespace

import pytest

from app.retrieval.corpus import Chunk
from app.retrieval.questions import (
    MODEL,
    PROMPT,
    REWRITE,
    Artefact,
    Question,
    SpotCheck,
    apply_marks,
    build_set,
    check,
    content_words,
    freeze,
    is_routine_bump,
    load_frozen,
    sample_artefacts,
    spot_check_sheet,
    write_question,
)

_SHA = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d"
_PLANNER = "the planner dereferenced a null step when the goal was empty; guard added"


class _FakeMessages:
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.created: list[dict] = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self._replies.pop(0))],
            usage=SimpleNamespace(input_tokens=1_000, output_tokens=40),
        )


class _FakeAnthropic:
    def __init__(self, replies: list[str]) -> None:
        self.messages = _FakeMessages(replies)


def _reply(question: str) -> str:
    return json.dumps({"question": question, "why_unique": "only this artefact says so"})


def _sent(call: dict) -> str:
    return call["messages"][0]["content"]


def _chunk(artefact: str, index: int = 0, tokens: int = 100, content: str = _PLANNER) -> Chunk:
    entity_type, key = artefact.split(":", 1)
    return Chunk(len(content) + index, artefact, entity_type, key, index, tokens, content)


def _artefact(artefact: str, text: str = _PLANNER) -> Artefact:
    entity_type = artefact.split(":", 1)[0]
    return Artefact(artefact, entity_type, text, 100, ())


# --- sampling -----------------------------------------------------------------------------


def test_quotas_use_largest_remainder_and_total_300():
    counts = {"commit": 2_921, "issue": 3_805, "pull_request": 7_121, "release": 276}
    chunks = [
        _chunk(f"{entity_type}:{number}")
        for entity_type, count in counts.items()
        for number in range(count)
    ]

    stream = sample_artefacts(chunks)

    # Floors 62, 80, 151 and 5 leave 2 seats, which go to the largest remainders: releases
    # (0.863) and issues (0.825), not commits (0.048) or pull requests (0.264).
    assert stream.quotas == {"commit": 62, "issue": 81, "pull_request": 151, "release": 6}
    assert sum(stream.quotas.values()) == 300
    assert sorted(stream.schedule) == sorted(
        entity_type for entity_type, quota in stream.quotas.items() for _ in range(quota)
    )


def test_quotas_count_distinct_artefacts_not_chunks():
    chunks = [_chunk("commit:a", 0), _chunk("commit:a", 1), _chunk("commit:a", 2),
              _chunk("issue:1"), _chunk("issue:2"), _chunk("issue:3")]

    assert sample_artefacts(chunks, n=2).quotas == {"commit": 1, "issue": 1}


def test_sampling_is_reproducible_and_skips_thin_artefacts():
    chunks = [_chunk(f"commit:{sha}", tokens=100) for sha in ("aa", "bb", "cc", "dd")]
    # 39 tokens in all, across two chunks: thin, whichever chunk is looked at.
    chunks += [_chunk("commit:thin", 0, tokens=20), _chunk("commit:thin", 1, tokens=19)]
    # Exactly 40 clears the bar.
    chunks += [_chunk("commit:edge", tokens=40)]

    def all_draws(seed: int) -> list[str]:
        stream = sample_artefacts(chunks, n=1, seed=seed)
        drawn = [stream.draw("commit").artefact for _ in range(5)]
        with pytest.raises(LookupError):
            stream.draw("commit")
        return drawn

    first = all_draws(20261002)

    assert first == all_draws(20261002)
    assert "commit:thin" not in first
    assert sorted(first) == ["commit:aa", "commit:bb", "commit:cc", "commit:dd", "commit:edge"]
    # The order is the seed's, not the file's: the same chunks in reverse draw the same.
    assert [sample_artefacts(chunks[::-1], n=1).draw("commit").artefact] == first[:1]


def test_artefact_text_is_its_chunks_in_order_up_to_1500_tokens():
    tokens = [250, 300, 300, 300, 300, 300]
    chunks = [_chunk("issue:7", index, tokens=count, content=f"part {index}")
              for index, count in enumerate(tokens)]

    target = sample_artefacts(chunks[::-1], n=1).draw("issue")

    # Five whole chunks are 1,450 tokens; the sixth would take them to 1,750, so it is left out.
    assert target.text == "\n\n".join(f"part {index}" for index in range(5))
    assert target.token_count == 1_750
    assert target.entity_type == "issue"
    assert target.url_hints == ("issues/7", "pull/7")


def test_a_first_chunk_over_1500_tokens_is_cut_not_dropped():
    target = sample_artefacts([_chunk("release:big", tokens=2_000, content="x" * 4_000)],
                              n=1).draw("release")

    # 1,500 of its 2,000 tokens' worth, in proportion: three quarters of its characters.
    assert target.text == "x" * 3_000
    assert target.token_count == 2_000


# --- routine bumps --------------------------------------------------------------------------


def _pull_request(title: str, opener: str = "someone") -> str:
    return (f"[pull request #12641 merged] {title}\n"
            f"feature/branch -> main | opened by {opener} on 2025-06-01, merged 2025-06-02 as a2aa88a\n"
            "Motivation and Context")


def _commit(title: str, author: str = "Someone") -> str:
    return f"[commit a2aa88a] {title}\nauthor: {author} | committed: 2025-06-02\nMotivation and Context"


_BUMPS = [
    ".Net: Update nuget-package.props for 1.58.0",
    ".Net: Bump to version 1.44.0 (#11209)",
    "Python: Bump azure-core from 1.30.1 to 1.30.2 in /python",
    "Java: [maven-release-plugin] prepare release java-0.2.13-alpha",
    ".Net: Update to Yaml.DotNet 16.3.0",
    "Bump js-yaml from 4.3.0 to 4.3.1 in /dotnet/samples/Demos/ProcessWithCloudEvents/ProcessWithCloudEvents.Client",
    "Python: Update autogen-agentchat requirement from <0.4,>=0.2 to >=0.2,<0.6 in /python",
]

_NOT_BUMPS = [
    "Updating Streamlit Python Sample to support newest version of SK",
    "Java: Add ability to use different OpenAI models for semantic functions",
]


@pytest.mark.parametrize("title", _BUMPS)
def test_routine_bump_titles_are_bumps_as_pull_requests_and_commits(title):
    assert is_routine_bump("pull_request", _pull_request(title))
    assert is_routine_bump("commit", _commit(title))


@pytest.mark.parametrize("title", _NOT_BUMPS)
def test_other_titles_are_not_bumps(title):
    assert not is_routine_bump("pull_request", _pull_request(title))
    assert not is_routine_bump("commit", _commit(title))


def test_a_pull_request_opened_by_a_dependency_bot_is_a_bump_whatever_its_title():
    title = "Java: Add ability to use different OpenAI models for semantic functions"
    assert is_routine_bump("pull_request", _pull_request(title, opener="dependabot[bot]"))
    assert is_routine_bump("pull_request", _pull_request(title, opener="renovate[bot]"))
    assert is_routine_bump("commit", _commit(title, author="dependabot[bot]"))


def test_only_the_first_two_lines_are_read():
    # A body that mentions a bump, or a bot, does not make a feature a bump.
    text = (_pull_request("Java: Add ability to use different OpenAI models for semantic functions")
            + "\nBump azure-core from 1.30.1 to 1.30.2, as dependabot suggested")
    assert not is_routine_bump("pull_request", text)


def test_issues_and_releases_are_never_bumps():
    assert not is_routine_bump("issue", "[issue #77 open] Bump X to 1.2\nopened by dependabot[bot] on 2025-06-01")
    assert not is_routine_bump("release", "[release dotnet-1.44.0] dotnet-1.44.0\npublished 2025-06-01\n"
                                          "* Bump to version 1.44.0")


def test_draw_skips_a_routine_bump_and_draws_the_next_artefact():
    feature = _commit("Python: Add retries to the HTTP connector")

    def stream(first: str):
        # Only the content of the artefact the seed draws first differs between the two.
        order = sample_artefacts([_chunk("commit:aa", content=feature), _chunk("commit:bb", content=feature)],
                                 n=1)
        head = order.draw("commit").artefact
        other = "commit:bb" if head == "commit:aa" else "commit:aa"
        chunks = [_chunk(head, content=first), _chunk(other, content=feature)]
        return head, other, sample_artefacts(chunks, n=1)

    head, _, plain = stream(feature)
    assert plain.draw("commit").artefact == head

    head, other, bumped = stream(_commit(".Net: Bump to version 1.44.0 (#11209)"))
    assert bumped.draw("commit").artefact == other
    with pytest.raises(LookupError):
        bumped.draw("commit")
    # The quotas still count the bump, as they count every distinct artefact.
    assert bumped.quotas == {"commit": 1}


# --- the checks ---------------------------------------------------------------------------


def test_content_words_are_long_lowercased_and_not_stop_words():
    assert content_words("Which PR fixed the Planner's null-step crash, and when?") == {
        "fixed", "planner", "null", "step", "crash",
    }


def test_check_rejects_copied_wording():
    target = _artefact("commit:" + _SHA)

    # planner, null, step, goal and empty appear in the target; change and guarded do not.
    copied = "Which change guarded the planner against a null step when the goal was empty?"
    paraphrase = "Which change stopped the orchestrator crashing when it was asked to do nothing?"
    # planner and null appear, crash and input do not: exactly half is not more than half.
    half = "Why did the planner crash on a null input?"

    assert check(copied, target) == ["copies its target: 0.71 of content words"]
    assert check(paraphrase, target) == []
    assert check(half, target) == []


def test_check_rejects_keys():
    commit = _artefact("commit:" + _SHA, "[commit 1a2b3c4] fix: planner null reference")
    issue = _artefact("issue:14111", "[issue #14111 closed] connectors time out")
    release = _artefact("release:dotnet-1.79.0", "[release dotnet-1.79.0] streaming agents")
    preview = _artefact("release:dotnet-1.79.0-preview.1", "[release dotnet-1.79.0-preview.1] streaming agents")
    pull = Artefact("pull_request:14111", "pull_request", "[pull request #14111 merged] moves",
                    100, ("pull/14111", "issues/14111"))

    leaks = [
        ("What did commit 1a2b3c4 change in the orchestration code?", commit),
        ("What did commit 1A2B3C4D5E change in the orchestration code?", commit),
        ("Who reported #14111 about the connectors timing out?", issue),
        ("Who reported issue 14111 about the connectors timing out?", issue),
        ("Which adapters stalled according to issue 14111.", issue),
        ("Does PR 14111 move the connectors elsewhere?", pull),
        ("What did pull request number 14111 move?", pull),
        ("What shipped in dotnet-1.79.0 for the agent framework?", release),
        ("What shipped in version 1.79.0 for the agent framework?", release),
        ("What shipped in v1.79.0 for the agent framework?", release),
        # A suffixed tag's version is everything from its first part that starts with a digit,
        # and its bare x.y.z is the key too.
        ("What shipped in 1.79.0-preview.1 for the agent framework?", preview),
        ("What shipped in version 1.79.0 for the agent framework?", preview),
        ("What shipped in v1.79.0 for the agent framework?", preview),
        ("Is https://github.com/example-owner/example-repo/pull/14111 the change that moved them?",
         pull),
    ]
    for question, target in leaks:
        assert check(question, target) == ["contains the target's key"], question

    # Neighbours of the key are not the key.
    near_misses = [
        ("What did commit 1a2b3c5 change in the orchestration code?", commit),
        ("Who reported #141110 about the connectors timing out?", issue),
        ("Who reported #1411 about the connectors timing out?", issue),
        ("Who reported issue 141110 about the connectors timing out?", issue),
        ("Who reported version 1.14111 about the connectors timing out?", issue),
        ("Who reported version 14111.2 about the connectors timing out?", issue),
        ("What shipped in dotnet-1.79.01 for the agent framework?", release),
        ("What shipped in version 1.79.01 for the agent framework?", release),
        ("What shipped in version 1.79.01 for the agent framework?", preview),
        ("What shipped in version 1.7 for the agent framework?", preview),
    ]
    for question, target in near_misses:
        assert check(question, target) == [], question


def test_check_rejects_length():
    target = _artefact("issue:9")

    assert check("Which commit fixed the orchestrator?", target) == ["too short"]
    assert check(" ".join(["orchestrator"] * 41), target) == ["too long"]
    assert check("Which commit fixed the orchestrator crash?", target) == []
    assert check(" ".join(["orchestrator"] * 40), target) == []


# --- the writer and the set ---------------------------------------------------------------


def test_write_question_asks_sonnet_5_once_with_the_artefact():
    target = _artefact("pull_request:12", "[pull request #12 merged] adds retries")
    client = _FakeAnthropic([_reply("Which change added retrying to the HTTP connector?")])

    draft = write_question(target, client)

    assert (draft.question, draft.why_unique) == (
        "Which change added retrying to the HTTP connector?", "only this artefact says so")
    assert (draft.input_tokens, draft.output_tokens) == (1_000, 40)
    [call] = client.messages.created
    assert (call["model"], call["max_tokens"]) == (MODEL, 300) == ("claude-sonnet-5", 300)
    assert call["system"] == PROMPT
    assert call["thinking"] == {"type": "disabled"}
    assert "[pull request #12 merged] adds retries" in _sent(call)


def test_the_prompt_asks_for_stated_facts_standalone_questions_and_specifics():
    # Round 0's spot-check failed on questions about who merged, on "this PR", and on bumps
    # that a similar change also answered.
    assert "never says who merged or approved a pull request" in PROMPT
    assert all(phrase in PROMPT for phrase in
               ('"this pull request"', '"this commit"', '"this issue"', '"this release"'))
    assert "no similar change in the same repository would also answer it" in PROMPT


def test_rejected_artefact_is_rewritten_once_then_replaced():
    # Four-character keys: too short to be a SHA prefix the key check would look for.
    chunks = [_chunk(f"commit:{sha}", content=f"[commit {sha}] {_PLANNER}")
              for sha in ("aaaa", "bbbb", "cccc", "dddd")]
    copied = _reply("Which change guarded the planner against a null step when the goal was empty?")
    good = _reply("Which change stopped the orchestrator crashing when it was asked to do nothing?")
    client = _FakeAnthropic([copied, copied, good, good])
    order = sample_artefacts(chunks, n=2)
    expected = [order.draw("commit").artefact for _ in range(3)]

    built = build_set(sample_artefacts(chunks, n=2), client)

    calls = client.messages.created
    assert len(calls) == 4
    # The first artefact is asked twice: once, then one rewrite that says why.
    first_header = f"[{expected[0].replace(':', ' ')}]"
    assert first_header in _sent(calls[0]) and first_header in _sent(calls[1])
    assert "rejected" not in _sent(calls[0])
    assert calls[1]["system"] == PROMPT
    assert calls[1]["thinking"] == {"type": "disabled"}
    assert _sent(calls[1]).endswith(REWRITE.format(
        reasons="copies its target: 0.71 of content words", raw=copied))
    # Then the next seeded draw of the same type takes its place, under the same qid.
    assert f"[{expected[1].replace(':', ' ')}]" in _sent(calls[2])
    assert [question.target for question in built.questions] == expected[1:]
    assert [question.qid for question in built.questions] == ["q001", "q002"]
    assert {question.entity_type for question in built.questions} == {"commit"}
    assert expected[0] not in built.artefacts
    assert sorted(built.artefacts) == sorted(expected[1:])
    assert built.rejections == {"copies its target": 2}
    assert (built.rewrites, built.replacements) == (1, 1)
    assert (built.input_tokens, built.output_tokens) == (4_000, 160)
    assert built.why_unique == {"q001": "only this artefact says so",
                                "q002": "only this artefact says so"}


def test_invalid_json_counts_as_a_rejection():
    chunks = [_chunk("issue:1"), _chunk("issue:2")]
    good = _reply("Which change stopped the orchestrator crashing when it was asked to do nothing?")
    client = _FakeAnthropic(["Here is a question: what broke?", good])

    built = build_set(sample_artefacts(chunks, n=1), client)

    assert built.rejections == {"not valid JSON": 1}
    assert (built.rewrites, built.replacements) == (1, 0)
    assert len(client.messages.created) == 2
    assert len(built.questions) == 1


@pytest.mark.parametrize("raw", [
    "not json at all",
    '```json\n{"question": "q", "why_unique": "w"}\n```',
    '["a list"]',
    '{"question": 5, "why_unique": "w"}',
    '{"question": "Which change fixed it?"}',
    '{"question": "   ", "why_unique": "w"}',
])
def test_a_reply_that_is_not_the_asked_json_gives_no_question(raw):
    draft = write_question(_artefact("issue:1"), _FakeAnthropic([raw]))

    assert (draft.question, draft.why_unique, draft.raw) == (None, None, raw)
    assert (draft.input_tokens, draft.output_tokens) == (1_000, 40)


def test_build_set_interleaves_types_in_a_seeded_schedule():
    chunks = [_chunk(f"commit:{n}") for n in range(10)] + [_chunk(f"issue:{n}") for n in range(10)]
    good = _reply("Which change stopped the orchestrator crashing when it was asked to do nothing?")
    stream = sample_artefacts(chunks, n=10)

    built = build_set(stream, _FakeAnthropic([good] * 10))

    assert [question.entity_type for question in built.questions] == list(stream.schedule)
    assert sorted(stream.schedule) == ["commit"] * 5 + ["issue"] * 5
    # Mixed, so the first questions (the ones the determinism check repeats) are not one type.
    assert list(stream.schedule) != sorted(stream.schedule)


class _FailingAnthropic(_FakeAnthropic):
    """Answers from its queue, then fails as a dropped connection would."""

    def __init__(self, replies: list[str]) -> None:
        super().__init__(replies)
        create = self.messages.create

        def create_or_fail(**kwargs):
            if not self.messages._replies:
                raise ConnectionError("connection dropped (not real)")
            return create(**kwargs)

        self.messages.create = create_or_fail


def _resumable_corpus() -> list[Chunk]:
    return [_chunk(f"commit:{sha}", content=f"[commit {sha}] {_PLANNER}")
            for sha in ("aaaa", "bbbb", "cccc", "dddd", "eeee", "ffff")]


_COPIED = _reply("Which change guarded the planner against a null step when the goal was empty?")
_GOOD = _reply("Which change stopped the orchestrator crashing when it was asked to do nothing?")


def test_build_set_checkpoints_and_a_rerun_pays_for_nothing_twice(tmp_path):
    checkpoint = tmp_path / "questions.checkpoint.jsonl"
    reference = build_set(sample_artefacts(_resumable_corpus(), n=4),
                          _FakeAnthropic([_COPIED, _GOOD, _GOOD, _GOOD, _GOOD]))

    # q001 needs a rewrite, q002 is accepted, and the connection drops while q003 is written.
    failing = _FailingAnthropic([_COPIED, _GOOD, _GOOD])
    with pytest.raises(ConnectionError):
        build_set(sample_artefacts(_resumable_corpus(), n=4), failing, checkpoint=checkpoint)

    records = [json.loads(line) for line in checkpoint.read_text(encoding="utf-8").splitlines()]
    assert [record["question"]["qid"] for record in records] == ["q001", "q002"]
    assert records[0]["draft"]["raw"] == _GOOD
    assert records[0]["draft"]["why_unique"] == "only this artefact says so"
    assert records[0]["rejections"] == {"copies its target": 1}

    resumed_client = _FakeAnthropic([_GOOD, _GOOD])
    resumed = build_set(sample_artefacts(_resumable_corpus(), n=4), resumed_client, checkpoint=checkpoint)

    # Only q003 and q004 are written; q001 and q002 come from the checkpoint.
    assert len(resumed_client.messages.created) == 2
    assert resumed.questions == reference.questions
    assert resumed.why_unique == reference.why_unique
    assert sorted(resumed.artefacts) == sorted(reference.artefacts)
    assert resumed.rejections == reference.rejections == {"copies its target": 1}
    assert (resumed.rewrites, resumed.replacements) == (reference.rewrites, reference.replacements)
    assert (resumed.input_tokens, resumed.output_tokens) == (reference.input_tokens, reference.output_tokens)
    # The call that raised returned nothing, so it was not billed and is not counted.
    assert resumed.calls == reference.calls == 5
    assert len(resumed.written_on) == 1

    # A finished checkpoint replays the whole set without a single call.
    replayed_client = _FakeAnthropic([])
    replayed = build_set(sample_artefacts(_resumable_corpus(), n=4), replayed_client, checkpoint=checkpoint)
    assert replayed.questions == reference.questions
    assert replayed_client.messages.created == []


def test_a_slot_that_fails_partway_keeps_its_spend(tmp_path):
    """Every call is recorded as it returns, so a slot that dies on its rewrite still counts the
    first draft it paid for, and the accepted slot before it is never paid twice."""
    checkpoint = tmp_path / "questions.checkpoint.jsonl"
    spend = tmp_path / "questions.spend.jsonl"

    # q001 is accepted; q002's first draft is rejected, and the connection drops on its rewrite.
    failing = _FailingAnthropic([_GOOD, _COPIED])
    with pytest.raises(ConnectionError):
        build_set(sample_artefacts(_resumable_corpus(), n=3), failing, checkpoint=checkpoint)

    assert len(checkpoint.read_text(encoding="utf-8").splitlines()) == 1
    recorded = [json.loads(line) for line in spend.read_text(encoding="utf-8").splitlines()]
    assert [(r["qid"], r["attempt"], r["kind"]) for r in recorded] == [("q001", 1, "first"), ("q002", 1, "first")]
    assert all((r["input_tokens"], r["output_tokens"]) == (1_000, 40) for r in recorded)

    resumed_client = _FakeAnthropic([_COPIED, _GOOD, _GOOD])
    resumed = build_set(sample_artefacts(_resumable_corpus(), n=3), resumed_client, checkpoint=checkpoint)

    # q002 is written again from its first draw, and q003 once: q001 is not paid for again.
    assert len(resumed_client.messages.created) == 3
    recorded = [json.loads(line) for line in spend.read_text(encoding="utf-8").splitlines()]
    assert [r["qid"] for r in recorded] == ["q001", "q002", "q002", "q002", "q003"]
    # The totals are every paid call, the failed slot's first draft included.
    assert (resumed.calls, resumed.input_tokens, resumed.output_tokens) == (5, 5_000, 200)


def test_a_checkpoint_without_its_spend_record_is_refused(tmp_path):
    checkpoint = tmp_path / "questions.checkpoint.jsonl"
    build_set(sample_artefacts(_resumable_corpus(), n=2), _FakeAnthropic([_GOOD, _GOOD]), checkpoint=checkpoint)
    (tmp_path / "questions.spend.jsonl").unlink()

    with pytest.raises(ValueError, match="questions.spend.jsonl"):
        build_set(sample_artefacts(_resumable_corpus(), n=2), _FakeAnthropic([]), checkpoint=checkpoint)


def test_a_checkpoint_line_cut_off_by_a_crash_is_written_again(tmp_path):
    checkpoint = tmp_path / "questions.checkpoint.jsonl"
    build_set(sample_artefacts(_resumable_corpus(), n=2), _FakeAnthropic([_GOOD, _GOOD]), checkpoint=checkpoint)
    whole = checkpoint.read_text(encoding="utf-8")
    checkpoint.write_text(whole[: whole.index("\n") + 20], encoding="utf-8")

    client = _FakeAnthropic([_GOOD])
    built = build_set(sample_artefacts(_resumable_corpus(), n=2), client, checkpoint=checkpoint)

    assert len(client.messages.created) == 1
    assert [question.qid for question in built.questions] == ["q001", "q002"]
    assert checkpoint.read_text(encoding="utf-8") == whole


def test_a_checkpoint_from_another_prompt_or_corpus_is_refused(tmp_path):
    checkpoint = tmp_path / "questions.checkpoint.jsonl"
    build_set(sample_artefacts(_resumable_corpus(), n=2), _FakeAnthropic([_GOOD, _GOOD]), checkpoint=checkpoint)
    lines = checkpoint.read_text(encoding="utf-8").splitlines()

    spend = (tmp_path / "questions.spend.jsonl").read_text(encoding="utf-8")

    other_prompt = json.loads(lines[0]) | {"prompts_sha256": "0" * 64}
    stale = tmp_path / "stale.jsonl"
    stale.write_text(json.dumps(other_prompt) + "\n", encoding="utf-8")
    (tmp_path / "stale.spend.jsonl").write_text(spend, encoding="utf-8")
    with pytest.raises(ValueError, match="another PROMPT or REWRITE"):
        build_set(sample_artefacts(_resumable_corpus(), n=2), _FakeAnthropic([]), checkpoint=stale)

    record = json.loads(lines[0])
    record["question"]["target"] = "commit:zzzz"
    foreign = tmp_path / "foreign.jsonl"
    foreign.write_text(json.dumps(record) + "\n", encoding="utf-8")
    (tmp_path / "foreign.spend.jsonl").write_text(spend, encoding="utf-8")
    with pytest.raises(ValueError, match="commit:zzzz"):
        build_set(sample_artefacts(_resumable_corpus(), n=2), _FakeAnthropic([]), checkpoint=foreign)


# --- the spot-check -----------------------------------------------------------------------


def _questions(count: int) -> list[Question]:
    return [Question(f"q{n:03d}", f"question {n}", f"issue:{n}", "issue") for n in range(1, count + 1)]


def test_spot_check_stop_rule():
    questions = _questions(40)
    artefacts = {question.target: _artefact(question.target, f"text {question.target}")
                 for question in questions}

    why_unique = {question.qid: f"why {question.qid}" for question in questions}

    sheet = spot_check_sheet(questions, artefacts, why_unique=why_unique)

    assert len(sheet) == 30
    assert sheet == spot_check_sheet(questions, artefacts, why_unique=why_unique)
    assert all(row["text"] == f"text {row['target']}" for row in sheet)
    assert all(row["why_unique"] == f"why {row['qid']}" for row in sheet)

    qids = [row["qid"] for row in sheet]
    three = {qid: "fine" for qid in qids} | {qids[0]: "ambiguous", qids[1]: "wrong", qids[2]: "wrong"}
    four = three | {qids[3]: "ambiguous"}

    assert apply_marks(sheet, three) == SpotCheck(fine=27, not_fine=3, passes=True)
    assert apply_marks(sheet, four) == SpotCheck(fine=26, not_fine=4, passes=False)


def test_each_spot_check_round_draws_a_fresh_reproducible_sample():
    questions = _questions(300)
    artefacts = {question.target: _artefact(question.target) for question in questions}

    def qids(**round_):
        return [row["qid"] for row in spot_check_sheet(questions, artefacts, **round_)]

    assert qids() == qids(round_=0) == qids(round_=0)
    assert qids(round_=1) == qids(round_=1)
    assert set(qids(round_=0)) != set(qids(round_=1))
    assert qids(round_=1) == qids(seed=20261003)


def test_spot_check_refuses_incomplete_or_unknown_marks():
    questions = _questions(30)
    artefacts = {question.target: _artefact(question.target) for question in questions}
    sheet = spot_check_sheet(questions, artefacts)
    marks = {row["qid"]: "fine" for row in sheet}

    with pytest.raises(ValueError, match="q001"):
        apply_marks(sheet, {qid: mark for qid, mark in marks.items() if qid != "q001"})
    with pytest.raises(ValueError, match="maybe"):
        apply_marks(sheet, marks | {"q001": "maybe"})
    with pytest.raises(ValueError, match="q999"):
        apply_marks(sheet, marks | {"q999": "fine"})


# --- freezing -----------------------------------------------------------------------------


def test_load_frozen_refuses_an_edited_file(tmp_path):
    questions = _questions(3) + [Question("q004", "Qué cambió en el planificador?", "commit:ab", "commit")]
    path = tmp_path / "questions.jsonl"

    digest = freeze(questions, {"seed": 20261002, "model": MODEL}, path)

    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = json.loads(path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    assert manifest == {"seed": 20261002, "model": MODEL, "sha256": digest}
    assert load_frozen(path) == questions

    edited = path.read_bytes().replace(b"question 2", b"question 3", 1)
    path.write_bytes(edited)
    edited_digest = hashlib.sha256(edited).hexdigest()

    with pytest.raises(ValueError) as refused:
        load_frozen(path)

    assert digest in str(refused.value)
    assert edited_digest in str(refused.value)
