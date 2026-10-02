"""The benchmark's 300 questions: sampled, written by Claude, checked, spot-checked and frozen.

Spec §3. Each question is written from one artefact, which is its single right answer. The
sample is seeded and stratified by type; Claude Sonnet 5 writes one question per artefact; the
mechanical checks reject what copies, leaks the key or is the wrong length; a rejected artefact
gets one rewrite and is then replaced by the next seeded draw of its type. The owner marks 30,
and the set is frozen with its SHA-256, so measuring refuses a file edited after freezing.

This does not run the arms, and it does not decide whether a question is good: the checks are
mechanical by design, and judging a question is the owner's spot-check.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from .corpus import Chunk

SEED = 20261002
MODEL = "claude-sonnet-5"
MAX_TOKENS = 300
SAMPLE_SIZE = 300
MIN_TOKENS = 40
MAX_TEXT_TOKENS = 1_500
MAX_COPIED = 0.5
MIN_WORDS = 6
MAX_WORDS = 40
SPOT_CHECK_SIZE = 30
MAX_NOT_FINE = 3
MARKS = ("fine", "ambiguous", "wrong")

PROMPT = """You write one question for a retrieval benchmark over a software repository's GitHub \
history. You are shown one artefact from it: a commit, an issue, a pull request or a release.

Write one natural question that a developer might ask, and that this artefact alone answers.

Ask about what is distinctive to this artefact, such as a number, an author or a date, where \
its text shows them.

Do not copy the artefact's distinctive phrasing. Put the question in your own words.

Do not name the artefact's own identifier: not its commit SHA, its #number, its release tag or \
its URL. Keep the question between 6 and 40 words.

Reply with JSON only, with no other text and no code fence:
{"question": "<the question>", "why_unique": "<one sentence on why only this artefact answers it>"}"""

# Sent with the same PROMPT for the one rewrite a rejected artefact gets, so the writer knows
# which check it failed. Frozen with the set alongside PROMPT.
REWRITE = """Your previous reply was rejected by a mechanical check: {reasons}.
Your previous reply was:
{raw}

Write a different question for the same artefact that passes, following the same instructions."""

_TYPE_WORDS = {"commit": "commit", "issue": "issue", "pull_request": "pull request",
               "release": "release"}

# Each type's repo-relative URL, as SearchCommitsTool builds it. A full URL contains it, so
# matching the fragment catches the URL on any host. GitHub serves an issue at `pull/<n>` and
# a pull request at `issues/<n>` too, so both carry both.
_URL_PATHS = {
    "commit": ("commit/{key}",),
    "issue": ("issues/{key}", "pull/{key}"),
    "pull_request": ("pull/{key}", "issues/{key}"),
    "release": ("releases/tag/{key}",),
}

_WORDS = re.compile(r"[^\W\d_]+")
_HEX_RUNS = re.compile(r"(?<![0-9a-f])[0-9a-f]{7,}(?![0-9a-f])")
_STOPWORDS = frozenset(
    line.strip().lower()
    for line in (Path(__file__).with_name("stopwords.txt")).read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.startswith("#")
)


@dataclass(frozen=True)
class Artefact:
    """One artefact as the writer sees it.

    `text` is its chunks in `chunk_index` order, joined by a blank line, keeping whole chunks
    while their `token_count` totals at most 1,500 (no chunk exceeds 320, so a longer artefact
    shows more than 1,180). `token_count` is the total over all its chunks, the figure the
    40-token floor is judged on. `url_hints` are the repo-relative URLs a question must not
    contain.
    """

    artefact: str
    entity_type: str
    text: str
    token_count: int
    url_hints: tuple[str, ...]


@dataclass(frozen=True)
class Draft:
    """One reply from the writer. `question` and `why_unique` are None when it was not the JSON
    asked for; the reply was billed all the same, so it still carries its tokens."""

    question: str | None
    why_unique: str | None
    raw: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Question:
    qid: str
    question: str
    target: str
    entity_type: str


@dataclass(frozen=True)
class BuiltSet:
    """The set in draw order, with each target's Artefact for the spot-check.

    `rejections` counts each failed check by its reason, without the copy ratio, so a reply that
    is both too short and copied counts once under each. `rewrites` is how many artefacts were
    rejected once, and `replacements` how many were rejected twice and replaced.
    """

    questions: list[Question]
    artefacts: dict[str, Artefact]
    rejections: dict[str, int]
    rewrites: int
    replacements: int
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class SpotCheck:
    fine: int
    not_fine: int
    passes: bool


def largest_remainder(counts: Mapping[str, int], n: int) -> dict[str, int]:
    """Split n seats in proportion to counts, exactly, so they total n.

    Each type first gets the floor of its share; the seats left go one each to the largest
    remainders. The arithmetic is in integers, so no float rounding can move a seat; a tie in
    remainder goes to the type whose name sorts first.
    """
    total = sum(counts.values())
    floors = {name: n * count // total for name, count in counts.items()}
    remainders = sorted(counts, key=lambda name: (-(n * counts[name] % total), name))
    for name in remainders[: n - sum(floors.values())]:
        floors[name] += 1
    return floors


def _url_hints(entity_type: str, key: str) -> tuple[str, ...]:
    return tuple(path.format(key=key) for path in _URL_PATHS[entity_type])


def _artefact(artefact: str, chunks: list[Chunk]) -> Artefact:
    ordered = sorted(chunks, key=lambda chunk: chunk.chunk_index)
    shown, tokens = [], 0
    for chunk in ordered:
        if tokens + chunk.token_count > MAX_TEXT_TOKENS:
            break
        shown.append(chunk.content)
        tokens += chunk.token_count
    first = ordered[0]
    return Artefact(
        artefact=artefact,
        entity_type=first.entity_type,
        text="\n\n".join(shown),
        token_count=sum(chunk.token_count for chunk in ordered),
        url_hints=_url_hints(first.entity_type, first.entity_key),
    )


class SampleStream:
    """Each type's artefacts in one seeded order, drawn one at a time, never twice.

    The order of a type is its artefacts sorted by identifier and then shuffled by a fresh
    `random.Random(seed)`, so it depends on the seed and the corpus, not on the export's order.
    `quotas` is each type's share of n, and `schedule` is the order the set's slots are filled
    in: each type repeated by its quota, shuffled by `random.Random(seed)`, so that q001 onwards
    mixes the types instead of running through them one at a time.
    """

    def __init__(self, chunks: list[Chunk], *, n: int, seed: int, min_tokens: int) -> None:
        by_artefact: defaultdict[str, list[Chunk]] = defaultdict(list)
        for chunk in chunks:
            by_artefact[chunk.artefact].append(chunk)
        by_type: defaultdict[str, list[str]] = defaultdict(list)
        for artefact, its_chunks in by_artefact.items():
            by_type[its_chunks[0].entity_type].append(artefact)

        self._chunks = dict(by_artefact)
        self._min_tokens = min_tokens
        self._order: dict[str, list[str]] = {}
        for entity_type, artefacts in sorted(by_type.items()):
            order = sorted(artefacts)
            random.Random(seed).shuffle(order)
            self._order[entity_type] = order
        self._next = dict.fromkeys(self._order, 0)

        self.quotas = largest_remainder({t: len(a) for t, a in self._order.items()}, n)
        schedule = [t for t, quota in sorted(self.quotas.items()) for _ in range(quota)]
        random.Random(seed).shuffle(schedule)
        self.schedule: tuple[str, ...] = tuple(schedule)

    def draw(self, entity_type: str) -> Artefact:
        """The next artefact of this type whose chunks total at least `min_tokens`.

        Raises LookupError when the type has none left, rather than borrowing from another type
        and breaking the per-type counts.
        """
        order = self._order.get(entity_type, [])
        while self._next.get(entity_type, 0) < len(order):
            artefact = order[self._next[entity_type]]
            self._next[entity_type] += 1
            candidate = _artefact(artefact, self._chunks[artefact])
            if candidate.token_count >= self._min_tokens:
                return candidate
        raise LookupError(f"no {entity_type} artefact of {self._min_tokens}+ tokens is left to draw")


def sample_artefacts(
    chunks: list[Chunk], *, n: int = SAMPLE_SIZE, seed: int = SEED, min_tokens: int = MIN_TOKENS
) -> SampleStream:
    """The seeded stream the set is drawn from, with quotas over the distinct artefacts per type.

    The quotas count every distinct artefact, thin or not, as spec §3.2 states the proportion;
    the 40-token floor only decides which artefacts may be drawn.
    """
    return SampleStream(chunks, n=n, seed=seed, min_tokens=min_tokens)


def content_words(text: str) -> set[str]:
    """Lower-cased runs of 4 or more letters that are not in `stopwords.txt`.

    A word is a run of letters, so digits, punctuation and apostrophes split it: "null-step"
    is two words, and "planner's" is "planner" and "s".
    """
    return {
        word for word in _WORDS.findall(text.lower())
        if len(word) >= 4 and word not in _STOPWORDS
    }


def _contains_key(question: str, target: Artefact) -> bool:
    lowered = question.lower()
    key = target.artefact.split(":", 1)[1].lower()
    if target.entity_type == "commit":
        if any(key.startswith(run) for run in _HEX_RUNS.findall(lowered)):
            return True
    elif target.entity_type in ("issue", "pull_request"):
        if re.search(rf"#{re.escape(key)}(?!\d)", lowered):
            return True
    elif target.entity_type == "release":
        if re.search(rf"(?<!\w){re.escape(key)}(?!\w)", lowered):
            return True
    return any(
        re.search(rf"{re.escape(hint.lower())}(?!\w)", lowered) for hint in target.url_hints
    )


def check(question: str, target: Artefact) -> list[str]:
    """Why spec §3.4's checks reject this question; empty when it passes.

    - copying: more than half of its content words appear among the target's text's words
    - the key: a SHA prefix of 7 or more hex characters, `#<number>`, the tag, or the URL
    - length: fewer than 6 or more than 40 whitespace-separated words

    A question with no content words copies nothing. The key is looked for only in the forms
    the spec names, so "issue 14111" without the `#` passes.
    """
    reasons = []
    words = content_words(question)
    if words:
        copied = len(words & content_words(target.text)) / len(words)
        if copied > MAX_COPIED:
            reasons.append(f"copies its target: {copied:.2f} of content words")
    if _contains_key(question, target):
        reasons.append("contains the target's key")
    count = len(question.split())
    if count < MIN_WORDS:
        reasons.append("too short")
    elif count > MAX_WORDS:
        reasons.append("too long")
    return reasons


def _parse(raw: str) -> tuple[str, str] | None:
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    question, why_unique = parsed.get("question"), parsed.get("why_unique")
    if not isinstance(question, str) or not isinstance(why_unique, str) or not question.strip():
        return None
    return question.strip(), why_unique.strip()


def write_question(target: Artefact, client, *, feedback: str | None = None) -> Draft:
    """One call to Claude Sonnet 5 for one question about `target`.

    `client` is a synchronous `anthropic.Anthropic`. The reply must be exactly the JSON object
    asked for: one wrapped in prose or a code fence is not valid JSON, and gives a Draft with no
    question. `feedback` is the REWRITE note for a rejected artefact's one rewrite.
    """
    content = f"This artefact is a {_TYPE_WORDS[target.entity_type]}.\n\n{target.text}"
    if feedback is not None:
        content = f"{content}\n\n---\n\n{feedback}"
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        # Without this, Sonnet 5 thinks adaptively, and its thinking counts against the 300
        # tokens, which could end a reply before its JSON is complete.
        thinking={"type": "disabled"},
        system=PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    raw = "".join(block.text for block in response.content if block.type == "text")
    parsed = _parse(raw.strip())
    question, why_unique = parsed if parsed is not None else (None, None)
    return Draft(question, why_unique, raw, response.usage.input_tokens,
                 response.usage.output_tokens)


def _rejections(draft: Draft, target: Artefact) -> list[str]:
    if draft.question is None:
        return ["not valid JSON"]
    return check(draft.question, target)


def build_set(stream: SampleStream, client) -> BuiltSet:
    """Fill every slot of `stream.schedule` with a question that passes the checks.

    A rejected artefact is asked once more, told why; rejected again, it is replaced by the
    next seeded draw of the same type, which starts afresh. So the set has exactly the quotas'
    counts, or the stream runs dry and LookupError is raised. Questions are numbered q001
    onwards in slot order.
    """
    questions: list[Question] = []
    artefacts: dict[str, Artefact] = {}
    rejections: Counter[str] = Counter()
    rewrites = replacements = input_tokens = output_tokens = 0

    for slot, entity_type in enumerate(stream.schedule, start=1):
        while True:
            target = stream.draw(entity_type)
            draft = write_question(target, client)
            input_tokens, output_tokens = (input_tokens + draft.input_tokens,
                                           output_tokens + draft.output_tokens)
            reasons = _rejections(draft, target)
            if reasons:
                rejections.update(reason.split(":", 1)[0] for reason in reasons)
                rewrites += 1
                draft = write_question(
                    target, client, feedback=REWRITE.format(reasons="; ".join(reasons), raw=draft.raw)
                )
                input_tokens, output_tokens = (input_tokens + draft.input_tokens,
                                               output_tokens + draft.output_tokens)
                reasons = _rejections(draft, target)
            if not reasons:
                break
            rejections.update(reason.split(":", 1)[0] for reason in reasons)
            replacements += 1

        questions.append(Question(f"q{slot:03d}", draft.question, target.artefact, entity_type))
        artefacts[target.artefact] = target

    return BuiltSet(questions, artefacts, dict(rejections), rewrites, replacements,
                    input_tokens, output_tokens)


def spot_check_sheet(
    questions: list[Question], artefacts: Mapping[str, Artefact], *, seed: int = SEED,
    n: int = SPOT_CHECK_SIZE,
) -> list[dict]:
    """n questions drawn with `random.Random(seed)`, in qid order, each with its target's text
    and an empty `mark` for the owner to fill in as fine, ambiguous or wrong."""
    drawn = sorted(random.Random(seed).sample(questions, n), key=lambda question: question.qid)
    return [
        asdict(question) | {"text": artefacts[question.target].text, "mark": ""}
        for question in drawn
    ]


def apply_marks(sheet: list[dict], marks: Mapping[str, str]) -> SpotCheck:
    """Count the owner's marks; the set passes with at most 3 that are not fine (spec §3.5).

    Every question on the sheet must be marked, with one of MARKS, and nothing else may be:
    a partial or mistyped sheet raises ValueError instead of passing on what it left out.
    """
    on_sheet = {row["qid"] for row in sheet}
    if missing := sorted(on_sheet - marks.keys()):
        raise ValueError(f"not marked: {', '.join(missing)}")
    if unknown := sorted(marks.keys() - on_sheet):
        raise ValueError(f"not on the sheet: {', '.join(unknown)}")
    if invalid := sorted(f"{qid}={mark}" for qid, mark in marks.items() if mark not in MARKS):
        raise ValueError(f"marks must be one of {', '.join(MARKS)}: {', '.join(invalid)}")
    fine = sum(1 for mark in marks.values() if mark == "fine")
    not_fine = len(marks) - fine
    return SpotCheck(fine, not_fine, not_fine <= MAX_NOT_FINE)


def _manifest_path(path: Path) -> Path:
    return path.with_suffix(".manifest.json")


def freeze(questions: Iterable[Question], manifest: Mapping, path: Path) -> str:
    """Write the set as UTF-8 JSON lines, and beside it the manifest with the file's SHA-256.

    The bytes are written exactly, with LF line endings on every platform, so the hash is of
    what is committed. The repository's `.gitattributes` keeps text files LF on checkout, so
    a Windows clone hashes the same. Returns the hash.
    """
    data = "".join(json.dumps(asdict(question), ensure_ascii=False) + "\n" for question in questions)
    payload = data.encode("utf-8")
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    frozen = dict(manifest) | {"sha256": digest}
    _manifest_path(path).write_bytes(
        (json.dumps(frozen, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    return digest


def load_frozen(path: Path) -> list[Question]:
    """The frozen set, only if the file is byte for byte what its manifest hashed.

    Raises ValueError naming both hashes otherwise, so no measurement runs on an edited set.
    """
    payload = path.read_bytes()
    actual = hashlib.sha256(payload).hexdigest()
    expected = json.loads(_manifest_path(path).read_text(encoding="utf-8"))["sha256"]
    if actual != expected:
        raise ValueError(
            f"{path.name} has SHA-256 {actual}, but its manifest froze {expected}: "
            "the question set changed after freezing, so it is not measured"
        )
    return [Question(**json.loads(line)) for line in payload.decode("utf-8").splitlines() if line]
