"""`python -m app.retrieval <command>`, run from eval/: the benchmark runbook's steps.

Each command is a thin wrapper over the package's functions, in the runbook's order:
`write-questions`, `spot-check` and `freeze` make the frozen set; `embed` embeds the corpus with
one deployment; `build-index` loads the AI Search index; `run-arms` runs all six arms and the
determinism repeat, and saves the run as reports/retrieval-<run_id>.json; `report` prints that
run's write-up.

Azure is called as the owner, through the Azure CLI, with no key; Claude is called with
ANTHROPIC_API_KEY. Nothing here decides whether a step should be paid for: that is the owner's
call at each step of the runbook.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import traceback
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import anthropic
import truststore

from .arms import ArmResult, QueryVector, read_worker_output, run_embedding_arm, run_search_arm
from .azure_auth import TokenSource
from .corpus import load_chunks, load_links
from .embed import embed_corpus, embed_query
from .questions import (
    EXCLUDED,
    MARKS,
    MAX_TEXT_TOKENS,
    MAX_TOKENS,
    MIN_TOKENS,
    MODEL,
    PROMPT,
    REWRITE,
    SEED,
    BuiltSet,
    Question,
    SampleStream,
    apply_marks,
    build_set,
    freeze,
    load_frozen,
    load_manifest,
    sample_artefacts,
    spot_check_sheet,
)
from .report import render
from .score import ARM_MODELS, analyse
from .search_index import API_VERSION, INDEX_NAME, create_index, document_count, new_client, upload
from .search_index import SCOPE as SEARCH_SCOPE

EVAL = Path(__file__).resolve().parents[2]
DATA = EVAL / "retrieval-data"
QUESTIONS = EVAL / "retrieval" / "questions.jsonl"
REPORTS = EVAL / "reports"

EMBEDDING_SCOPE = "https://ai.azure.com/.default"
SMALL = "releaselens-embed-small"
LARGE = "releaselens-embed-large"
CHECKPOINT = "questions.checkpoint.jsonl"
REPEAT_FIRST = 30
# An audit that keeps fewer questions than this is taken for a broken file, not a verdict on the set.
MIN_KEPT = 200
# The index counts an uploaded document only once it has indexed it, so build-index reads the
# count up to this many times, this many seconds apart, before it gives up.
COUNT_READINGS = 12
COUNT_WAIT_SECONDS = 5.0
_sleep = time.sleep


class Refused(Exception):
    """A step that will not run as asked. Its message says why, and what to do instead."""


class _CheckpointOnly:
    """Stands in for the writer when the set is read back from its checkpoint, and refuses to
    write: a set with questions still to write is not spot-checked or frozen."""

    def __init__(self, checkpoint: Path) -> None:
        self.messages = self
        self._checkpoint = checkpoint

    def create(self, **_):
        raise Refused(f"{self._checkpoint} does not hold every question yet; run write-questions to finish it")


def _writer() -> anthropic.Anthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise Refused("ANTHROPIC_API_KEY is not set, so the questions cannot be written")
    return anthropic.Anthropic()


def _write_json(path: Path, value, *, indent: int | None = 2) -> None:
    # Written whole and then swapped in, so a crash never leaves half a file.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes((json.dumps(value, ensure_ascii=False, indent=indent) + "\n").encode("utf-8"))
    os.replace(temporary, path)


def _checkpoint(args) -> Path:
    return args.checkpoint or args.data / CHECKPOINT


def _built(args, client) -> tuple[SampleStream, BuiltSet]:
    stream = sample_artefacts(load_chunks(args.data / "chunks.jsonl"))
    return stream, build_set(stream, client, checkpoint=_checkpoint(args))


def _spend(built: BuiltSet) -> dict:
    return {"calls": built.calls, "input_tokens": built.input_tokens, "output_tokens": built.output_tokens}


def write_questions(args) -> int:
    """Write the 300 questions, checkpointing each, so a rerun pays only for those not saved."""
    _, built = _built(args, _writer())
    print(f"{len(built.questions)} questions in {_checkpoint(args)}: {built.rewrites} rewritten, "
          f"{built.replacements} replaced, rejections {built.rejections}; {built.calls} calls paid for, "
          f"{built.input_tokens:,} input and {built.output_tokens:,} output tokens")
    return 0


def spot_check(args) -> int:
    """Write round N's sheet of 30 for the owner to mark, from the finished checkpoint."""
    out = args.data / f"spot-check-round-{args.round_}.json"
    if out.exists():
        raise Refused(f"{out} exists, and may already hold marks; move it aside to draw it again")
    _, built = _built(args, _CheckpointOnly(_checkpoint(args)))
    sheet = spot_check_sheet(built.questions, built.artefacts, round_=args.round_, why_unique=built.why_unique)
    # The generation's spend goes on its sheet, so a set later regenerated keeps it on record.
    _write_json(out, {"round": args.round_, "seed": SEED + args.round_, "spend": _spend(built), "rows": sheet})
    print(f"{len(sheet)} questions to mark in {out}: set each mark to fine, ambiguous or wrong")
    return 0


def _round(path: Path) -> dict:
    sheet = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(sheet.get("spend"), dict):
        raise Refused(f"{path} records no spend for its generation; write its sheet with spot-check")
    marks = {row["qid"]: row["mark"] for row in sheet["rows"]}
    result = apply_marks(sheet["rows"], marks)
    return {"round": sheet["round"], "seed": sheet["seed"], "sheet": sheet["rows"], "marks": marks,
            "fine": result.fine, "not_fine": result.not_fine, "passes": result.passes,
            "spend": sheet["spend"]}


def _audit(path: Path, built: BuiltSet, last: dict) -> tuple[list[Question], dict]:
    """The questions an independent reviewer's audit of the whole set marks fine, in qid order,
    and the manifest's record of the audit, with every question it drops.

    The audit extends the last spot-check round rather than overruling it, so it must give each
    question on that round's sheet the sheet's own mark. It must also mark every question of
    this set exactly once, and nothing else, with one of MARKS, and say who marked it. An audit
    that keeps fewer than MIN_KEPT is refused as a broken file. It does not judge any question
    itself: the marks are the reviewer's.
    """
    try:
        audit = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refused(f"cannot read the audit at {path}: {error}") from error
    rows = audit.get("rows") if isinstance(audit, dict) else None
    if not isinstance(rows, list) or not all(
            isinstance(row, dict) and all(isinstance(row.get(key), str) for key in ("qid", "mark", "note"))
            for row in rows):
        raise Refused(f"the audit at {path} has no rows, or a row that is not a qid, a mark and a note, "
                      "all strings")
    marked_by = audit.get("marked_by")
    if not isinstance(marked_by, str) or not marked_by.strip():
        raise Refused(f"the audit at {path} does not say who marked it: marked_by must be a non-empty string")

    in_set = [question.qid for question in built.questions]
    counted = Counter(row["qid"] for row in rows)
    if missing := [qid for qid in in_set if qid not in counted]:
        raise Refused(f"the audit at {path} leaves out {', '.join(missing)}; it must mark every question of "
                      "the set once")
    if extra := sorted(counted.keys() - set(in_set)):
        raise Refused(f"the audit at {path} marks {', '.join(extra)}, which are not in this set")
    if repeated := sorted(qid for qid, count in counted.items() if count > 1):
        raise Refused(f"the audit at {path} marks {', '.join(repeated)} more than once")
    if invalid := [f"{row['qid']}={row['mark']}" for row in rows if row["mark"] not in MARKS]:
        raise Refused(f"the audit at {path} has marks that are not one of {', '.join(MARKS)}: {', '.join(invalid)}")
    marks = {row["qid"]: row for row in rows}
    if disagree := [f"{qid} (audit {marks[qid]['mark']}, sheet {mark})" for qid, mark in sorted(last["marks"].items())
                    if marks[qid]["mark"] != mark]:
        raise Refused(f"the audit at {path} and round {last['round']}'s sheet mark {', '.join(disagree)} "
                      "differently; the audit must agree with the round it extends")

    kept = [question for question in built.questions if marks[question.qid]["mark"] == "fine"]
    if len(kept) < MIN_KEPT:
        raise Refused(f"the audit at {path} keeps {len(kept)} questions, fewer than {MIN_KEPT}, so it is taken for "
                      "a broken file and nothing is frozen; check it")
    return kept, {
        "marked_by": marked_by,
        "questions_audited": len(built.questions),
        "kept": len(kept),
        "kept_by_type": {entity_type: sum(1 for question in kept if question.entity_type == entity_type)
                         for entity_type in sorted({question.entity_type for question in built.questions})},
        "dropped": [{"qid": question.qid, "target": question.target, "entity_type": question.entity_type,
                     "mark": marks[question.qid]["mark"], "note": marks[question.qid]["note"]}
                    for question in built.questions if marks[question.qid]["mark"] != "fine"],
    }


def freeze_set(args) -> int:
    """Freeze the set with its manifest, only if the last spot-check passed on this very set,
    or, with --audit, only the questions an independent audit of the whole set marks fine.

    Each sheet given is one round, every round from 0, oldest first; the earlier ones are the
    rounds that failed and sent the set back to be regenerated, and are frozen with it as the
    record. Each round is one generation, and carries what writing it cost, so
    `spend_all_rounds` is every question-writing call the set took, the generations thrown away
    included. So a round left out is refused, as is an earlier round's sheet in --data that was
    not the one given.

    With --audit, the last round may have failed: the audit (see _audit) takes the place of its
    pass mark, and the round is still frozen as the record, with its marks. The questions it
    drops leave gaps in the qids, which stay as they were written.
    """
    stream, built = _built(args, _CheckpointOnly(_checkpoint(args)))
    rounds = [_round(path) for path in args.sheet]
    numbers = [checked["round"] for checked in rounds]
    if numbers != list(range(len(numbers))):
        raise Refused(f"the sheets are rounds {numbers}; give every round from 0, oldest first")
    given = {path.resolve() for path in args.sheet}
    for path in sorted(args.data.glob("spot-check-round-*.json")):
        number = re.fullmatch(r"spot-check-round-(\d+)\.json", path.name)
        if number and int(number[1]) < numbers[-1] and path.resolve() not in given:
            raise Refused(f"{path} is round {number[1]}'s sheet, and was not given; give every round "
                          "from 0, oldest first")

    last = rounds[-1]
    by_qid = {question.qid: question for question in built.questions}
    for row in last["sheet"]:
        question = by_qid.get(row["qid"])
        if question is None or (question.question, question.target) != (row["question"], row["target"]):
            raise Refused(f"round {last['round']}'s sheet shows {row['qid']} as it is not in this set; "
                          "the last sheet must be of the set being frozen")
    if last["spend"] != _spend(built):
        raise Refused(f"round {last['round']}'s sheet records a spend of {last['spend']}, but this set's "
                      f"spend record holds {_spend(built)}; the last sheet must be of the set being frozen")
    if args.audit is not None:
        kept, audit = _audit(args.audit, built, last)
    elif not last["passes"]:
        raise Refused(f"round {last['round']}'s spot-check failed: {last['not_fine']} of {len(last['marks'])} "
                      "are not fine, more than 3, so the set is regenerated, not frozen (spec §3.5)")
    else:
        kept, audit = built.questions, None

    manifest = {
        "seed": SEED,
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "thinking": "disabled",
        "prompt": PROMPT,
        "rewrite": REWRITE,
        "excluded_from_sample": EXCLUDED,
        "generated_on": list(built.written_on),
        "frozen_on": datetime.now(UTC).date().isoformat(),
        "sample_size": len(built.questions),
        "min_tokens": MIN_TOKENS,
        "max_text_tokens": MAX_TEXT_TOKENS,
        "quotas": stream.quotas,
        "rejections": built.rejections,
        "rewrites": built.rewrites,
        "replacements": built.replacements,
        "calls": built.calls,
        "input_tokens": built.input_tokens,
        "output_tokens": built.output_tokens,
        "spot_check": [{key: value for key, value in checked.items() if key != "sheet"} for checked in rounds],
        "spend_all_rounds": {key: sum(checked["spend"][key] for checked in rounds)
                             for key in ("calls", "input_tokens", "output_tokens")},
        "why_unique": built.why_unique,
    }
    if audit is not None:
        manifest["audit"] = audit
    args.questions.parent.mkdir(parents=True, exist_ok=True)
    digest = freeze(kept, manifest, args.questions)
    print(f"froze {len(kept)} of the {len(built.questions)} questions in {args.questions}, SHA-256 {digest}")
    return 0


def embed(args) -> int:
    """Embed the whole corpus with one deployment, resuming any earlier run into the same files."""
    run = embed_corpus(load_chunks(args.data / "chunks.jsonl"), base_url=args.base_url,
                       deployment=args.deployment, tokens=TokenSource(EMBEDDING_SCOPE, args.tenant),
                       out_dir=args.data)
    print(f"{run.deployment}: {run.batches} batches saved in {run.vectors_path}; "
          f"tokens_billed {run.tokens_billed:,}")
    return 0


def _settled_count(endpoint: str, tokens: TokenSource, client, expected: int) -> int:
    """The index's document count once it is `expected`, or else the last one read, after
    COUNT_READINGS readings COUNT_WAIT_SECONDS apart."""
    for reading in range(COUNT_READINGS):
        if reading:
            _sleep(COUNT_WAIT_SECONDS)
        indexed = document_count(endpoint, tokens, client)
        if indexed == expected:
            break
    return indexed


def build_index(args) -> int:
    """Create the index, upload every chunk with its saved `-small` vector, and wait until the
    index counts every chunk."""
    tokens = TokenSource(SEARCH_SCOPE, args.tenant)
    chunks = load_chunks(args.data / "chunks.jsonl")
    with new_client() as client:
        create_index(args.endpoint, tokens, client)
        sent = upload(chunks, args.data / f"{args.deployment}.npy", args.data / f"{args.deployment}.ids.json",
                      args.endpoint, tokens, client)
        indexed = _settled_count(args.endpoint, tokens, client, len(chunks))
    if indexed != len(chunks):
        raise Refused(f"the index at {args.endpoint} holds {indexed:,} documents, not {len(chunks):,}, after "
                      f"{COUNT_READINGS} readings {COUNT_WAIT_SECONDS:g} s apart; run build-index again")
    # upload raises on any document the service refused, so every one sent was accepted.
    print(f"index {INDEX_NAME}: {sent:,} documents uploaded and accepted; the index holds {indexed:,} documents")
    return 0


def _worker(path: Path, arm: str, qids: list[str], artefacts: Mapping[int, str]) -> list[ArmResult]:
    """The Worker's output, only if it is one arm's answer to every frozen question, in order,
    over the exported corpus: every hit a chunk in `artefacts`, with that chunk's artefact.

    Scoring reads the Worker's own artefacts, so hits from another database would otherwise be
    scored as if E1 and S1 had searched the same chunks as the other arms.
    """
    rows = read_worker_output(path)
    if any(row.arm != arm for row in rows):
        raise Refused(f"{path} is not {arm}'s output")
    if [row.qid for row in rows] != qids:
        raise Refused(f"{path} answers {len(rows)} questions, not the {len(qids)} of the frozen file in "
                      "order; run the Worker on the frozen file")
    for row in rows:
        for hit in row.hits:
            if artefacts.get(hit.chunk_id) != hit.artefact:
                corpus = f"is {artefacts[hit.chunk_id]} in" if hit.chunk_id in artefacts else "is not in"
                raise Refused(f"{path} gives {row.qid} chunk {hit.chunk_id} as {hit.artefact}, but that chunk "
                              f"{corpus} the exported corpus; run the Worker with RELEASELENS_DB set to the "
                              "database the corpus was exported from, releaselens_eval")
    if all(row.error is not None for row in rows):
        raise Refused(f"every question errored in {path}; fix the Worker run first")
    return rows


def _corpus_tokens(data: Path, deployment: str, chunks: int) -> int:
    """The tokens embedding the corpus with `deployment` was billed, from its progress record,
    which must record every batch over these chunks as saved."""
    path = data / f"{deployment}.progress.json"
    if not path.exists():
        raise Refused(f"no {path}: the corpus has not been embedded with {deployment}; run embed first")
    progress = json.loads(path.read_text(encoding="utf-8"))
    batches = math.ceil(chunks / progress["batch_size"])
    if progress["chunks"] != chunks or len(progress["done"]) != batches:
        raise Refused(f"{path} records {len(progress['done'])} saved batches over {progress['chunks']:,} chunks, "
                      f"not all {batches} over these {chunks:,}; run embed to finish it")
    return sum(progress["done"].values())


def _shown(path: Path) -> str:
    """The path as the write-up names it: relative to eval/ when it is under it."""
    try:
        return path.resolve().relative_to(EVAL).as_posix()
    except ValueError:
        return path.name


def run_arms(args) -> int:
    """All six arms over the frozen set, then the determinism repeat, saved after each arm.

    Everything free is checked before anything is paid for: the frozen file; the Worker's four
    files, down to each hit being a chunk of the exported corpus; the saved vectors, and the
    record that each corpus embedding finished; a token for each scope, so a sign-in failure
    costs nothing; and the index's document count, so an empty or wrong index is refused rather
    than scored as S2's and S3's misses. E2 runs before S2 and S3, which search with its vectors,
    on both passes, so the repeat embeds nothing for them. An arm that raises (say, a token that
    lapses and cannot be renewed, or a search arm stopped by the same error again and again) is
    the run's arm failure: the run stops there, so no ranker request is spent on a run that
    cannot be published, and is saved with what it had. A run has no resume: run it again in
    full.
    """
    questions = load_frozen(args.questions)
    audit = load_manifest(args.questions).get("audit")
    qids = [question.qid for question in questions]
    if not 1 <= args.repeat_first <= len(questions):
        raise Refused(f"--repeat-first must be between 1 and {len(questions)}")
    chunks = load_chunks(args.data / "chunks.jsonl")
    links = load_links(args.data / "links.jsonl")
    artefacts = {chunk.chunk_id: chunk.artefact for chunk in chunks}

    first_qids = set(qids[:args.repeat_first])
    worker_first = (_worker(args.worker_bge, "E1", qids, artefacts)
                    + _worker(args.worker_hybrid, "S1", qids, artefacts))
    worker_repeat = [row for row in _worker(args.worker_bge_repeat, "E1", qids, artefacts)
                     + _worker(args.worker_hybrid_repeat, "S1", qids, artefacts) if row.qid in first_qids]
    saved = {deployment: (args.data / f"{deployment}.npy", args.data / f"{deployment}.ids.json")
             for deployment in (args.small_deployment, args.large_deployment)}
    missing = [str(path) for paths in saved.values() for path in paths if not path.exists()]
    if missing:
        raise Refused(f"no saved vectors at {', '.join(missing)}; run embed first")
    corpus_tokens = {
        ARM_MODELS[arm]: {"deployment": deployment, "tokens": _corpus_tokens(args.data, deployment, len(chunks))}
        for arm, deployment in (("E2", args.small_deployment), ("E3", args.large_deployment))
    }

    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = REPORTS / f"retrieval-{run_id}.json"
    if out.exists():
        raise Refused(f"{out} exists; a run is never overwritten")

    embed_tokens = TokenSource(EMBEDDING_SCOPE, args.tenant)
    search_tokens = TokenSource(SEARCH_SCOPE, args.tenant)
    for tokens, scope in ((embed_tokens, EMBEDDING_SCOPE), (search_tokens, SEARCH_SCOPE)):
        try:
            tokens.token()
        except Exception as error:
            raise Refused(f"no token for {scope}, so nothing was spent: {type(error).__name__}: {error}. "
                          "Check az account show, and REQUESTS_CA_BUNDLE behind TLS inspection") from error
    with new_client() as http:
        try:
            indexed = document_count(args.endpoint, search_tokens, http)
        except Exception as error:
            raise Refused(f"could not count the index's documents at {args.endpoint}, so nothing was spent: "
                          f"{type(error).__name__}: {error}. Run build-index against this endpoint") from error
    if indexed != len(chunks):
        raise Refused(f"the index at {args.endpoint} holds {indexed:,} documents, not {len(chunks):,}, so "
                      "nothing was spent; run build-index against this endpoint")

    run = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "finished_at": None,
        "questions": {"file": _shown(args.questions),
                      "sha256": hashlib.sha256(args.questions.read_bytes()).hexdigest(), "count": len(questions),
                      # What the write-up states of the audit; the dropped questions stay in the manifest.
                      "audit": None if audit is None else {key: value for key, value in audit.items()
                                                           if key != "dropped"}},
        "chunks": len(chunks),
        # One-time costs, not per query: the tokens embedding the corpus was billed, per model.
        "corpus_tokens": corpus_tokens,
        "deployments": {"E2": args.small_deployment, "E3": args.large_deployment,
                        "S2": args.small_deployment, "S3": args.small_deployment},
        "search": {"index": INDEX_NAME, "api_version": API_VERSION, "endpoint": args.endpoint},
        "repeat_first": args.repeat_first,
        "results": [asdict(row) for row in worker_first],
        "repeat": [asdict(row) for row in worker_repeat],
        "arm_failure": None,
        "analysis": None,
    }
    results, repeat = list(worker_first), list(worker_repeat)
    _write_json(out, run, indent=None)

    repeated = questions[:args.repeat_first]
    # E2's own vectors, for S2 and S3. Never E3's: run_embedding_arm fills any dict it is given.
    e2_vectors: dict[str, QueryVector] = {}

    with new_client() as http:
        def embedding(arm: str, deployment: str, asked, vectors: dict | None) -> Callable[[], list[ArmResult]]:
            vectors_path, ids_path = saved[deployment]
            return lambda: run_embedding_arm(
                arm, asked, vectors_path=vectors_path, ids_path=ids_path, artefacts=artefacts,
                embed=lambda text: embed_query(text, base_url=args.base_url, deployment=deployment,
                                               tokens=embed_tokens, client=http),
                query_vectors=vectors, tokens=embed_tokens)

        def searching(arm: str, asked) -> Callable[[], list[ArmResult]]:
            return lambda: run_search_arm(arm, asked, query_vectors=e2_vectors, endpoint=args.endpoint,
                                          tokens=search_tokens, client=http)

        steps = [
            ("E2", "first", embedding("E2", args.small_deployment, questions, e2_vectors)),
            ("E3", "first", embedding("E3", args.large_deployment, questions, None)),
            ("S2", "first", searching("S2", questions)),
            ("S3", "first", searching("S3", questions)),
            ("E2", "repeat", embedding("E2", args.small_deployment, repeated, None)),
            ("E3", "repeat", embedding("E3", args.large_deployment, repeated, None)),
            ("S2", "repeat", searching("S2", repeated)),
            ("S3", "repeat", searching("S3", repeated)),
        ]
        for arm, which, step in steps:
            try:
                rows = step()
            except Exception as error:
                run["arm_failure"] = {"arm": arm, "pass": which, "error": f"{type(error).__name__}: {error}"}
                _write_json(out, run, indent=None)
                # The failure's own traceback, since the refusal below prints only its message.
                traceback.print_exception(error)
                raise Refused(f"{arm} failed on its {which} pass, so the run stopped and is not analysed: "
                              f"{run['arm_failure']['error']}. Saved what it had in {out}") from error
            (results if which == "first" else repeat).extend(rows)
            run["results" if which == "first" else "repeat"].extend(asdict(row) for row in rows)
            _write_json(out, run, indent=None)
            errors = sum(1 for row in rows if row.error is not None)
            print(f"{arm} {which} pass: {len(rows)} questions, {errors} errored")

    run["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        run["analysis"] = analyse(questions, results, repeat, links)
    except ValueError as error:
        run["analysis_error"] = str(error)
        _write_json(out, run, indent=None)
        raise Refused(f"the run finished but could not be analysed: {error}. Saved in {out}") from error
    _write_json(out, run, indent=None)
    print(f"saved {out}; render it with: python -m app.retrieval report {run_id}")
    return 0


def report(args) -> int:
    """Print a saved run's write-up."""
    path = REPORTS / f"retrieval-{args.run_id}.json"
    if not path.exists():
        raise Refused(f"no run {args.run_id}: {path} does not exist")
    sys.stdout.write(render(json.loads(path.read_text(encoding="utf-8"))))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.retrieval", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, handler, help_: str, *, data: bool = True, tenant: bool = False):
        sub = commands.add_parser(name, help=help_)
        sub.set_defaults(handler=handler)
        if data:
            sub.add_argument("--data", type=Path, default=DATA,
                             help="the export-corpus directory, which also holds vectors and sheets")
        if tenant:
            sub.add_argument("--tenant", required=True, help="the Entra tenant to take tokens from")
        return sub

    for name, handler, help_ in (
        ("write-questions", write_questions, "write the questions, checkpointing each one"),
        ("spot-check", spot_check, "write a spot-check sheet for the owner to mark"),
        ("freeze", freeze_set, "freeze the set and its manifest, if the last spot-check passed"),
    ):
        sub = command(name, handler, help_)
        sub.add_argument("--checkpoint", type=Path, default=None,
                         help=f"the checkpoint file (default: <data>/{CHECKPOINT})")
        if name == "spot-check":
            sub.add_argument("--round", dest="round_", type=int, default=0,
                             help="0 first, then 1, 2... after regenerating")
        if name == "freeze":
            sub.add_argument("--sheet", type=Path, action="append", required=True,
                             help="a marked sheet; repeat it for each round, oldest first")
            sub.add_argument("--questions", type=Path, default=QUESTIONS, help="where to freeze the set")
            sub.add_argument("--audit", type=Path, default=None,
                             help="an independent audit of every question: freeze only those it marks fine")

    sub = command("embed", embed, "embed the corpus with one deployment, resumably", tenant=True)
    sub.add_argument("--deployment", required=True)
    sub.add_argument("--base-url", required=True, help="the account's OpenAI v1 URL, ending in /openai/v1/")

    sub = command("build-index", build_index, "create the AI Search index and upload the corpus", tenant=True)
    sub.add_argument("--endpoint", required=True, help="the search service's URL")
    sub.add_argument("--deployment", default=SMALL, help="whose saved vectors to upload")

    sub = command("run-arms", run_arms, "run the six arms and the determinism repeat", tenant=True)
    sub.add_argument("--base-url", required=True)
    sub.add_argument("--endpoint", required=True)
    sub.add_argument("--questions", type=Path, default=QUESTIONS)
    sub.add_argument("--small-deployment", default=SMALL)
    sub.add_argument("--large-deployment", default=LARGE)
    sub.add_argument("--repeat-first", type=int, default=REPEAT_FIRST)
    sub.add_argument("--run-id", default=None, help="default: the UTC start time")
    for mode in ("hybrid", "bge"):
        sub.add_argument(f"--worker-{mode}", type=Path, required=True,
                         help=f"the Worker's retrieve {'hybrid' if mode == 'hybrid' else 'bge-exact'} output")
        sub.add_argument(f"--worker-{mode}-repeat", type=Path, required=True,
                         help="the second, separate run of the same mode")

    sub = command("report", report, "print a saved run's write-up", data=False)
    sub.add_argument("run_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Before any HTTPS client exists: Python verifies TLS against certifi's bundle, which a
    # TLS-inspecting proxy's certificate is not in, so every call to Azure would fail
    # verification. As app/main.py does; a no-op on a machine that does not intercept TLS.
    truststore.inject_into_ssl()
    args = _parser().parse_args(argv)
    try:
        return args.handler(args)
    except Refused as error:
        print(error, file=sys.stderr)
        return 1
    except Exception as error:
        # Anything else is a failure, not an answer: keep the whole traceback.
        traceback.print_exception(error)
        return 1


if __name__ == "__main__":
    # The write-up goes into UTF-8 markdown. Redirected on Windows, stdout would otherwise use
    # the ANSI code page, which cannot encode the − and ≤ it contains.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
