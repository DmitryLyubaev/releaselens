"""Strict completeness and the original S2 top-1 scoring, with cached query vectors."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import httpx
import numpy as np

from .bundle import read_json
from .client import search_once, wait_for_count
from .contracts import (DeliveryRefused, GateCase, GateResult, SearchTarget, VerifiedBundle,
                        canonical, exclusive_json)
from ..retrieval.arms import ArmResult
from ..retrieval.corpus import load_links
from ..retrieval.questions import load_frozen
from ..retrieval.score import score


def grade_rows(bundle: VerifiedBundle, rows: list[ArmResult]) -> GateResult:
    questions = load_frozen(bundle.root / "questions.jsonl")
    if len(questions) != 229 or len({q.qid for q in questions}) != 229:
        raise DeliveryRefused("inputs")
    links = load_links(bundle.root / "links.jsonl")
    counts = Counter(row.qid for row in rows)
    by_qid = {row.qid: row for row in rows}
    known = {q.qid for q in questions}
    invalid = sum(1 for row in rows if row.qid not in known)
    cases = []
    for q in questions:
        row = by_qid.get(q.qid)
        error = "missing" if row is None else "duplicate" if counts[q.qid] != 1 else None
        if row is not None and error is None:
            error = row.error or ("arm" if row.arm != "S2" else None)
        value = None if error else score(row, q.target, links)
        cases.append(GateCase(q.qid, 0 if value is None else value.top1,
                              error if error else "score" if value is None else None))
    missing = sum(c.error == "missing" for c in cases)
    skipped = sum(c.error == "skipped" for c in cases)
    errors = invalid + sum(c.error is not None and c.error not in ("missing", "skipped") for c in cases)
    evaluated = sum(c.error is None for c in cases)
    correct = sum(c.top1 for c in cases)
    return GateResult(229, evaluated, correct, errors, missing, skipped,
        evaluated == 229 and errors == missing == skipped == 0 and correct >= 120, tuple(cases))


def run_gate(bundle: VerifiedBundle, target: SearchTarget, config: dict, tokens,
             client: httpx.Client) -> tuple[GateResult, list[ArmResult]]:
    positive = read_json(bundle.root / "s2.json")
    negative = read_json(bundle.root / "negative.json")
    if config not in (positive, negative) or positive != {"top":50,"k":50,"filter":None}:
        raise DeliveryRefused("inputs")
    if negative != {"top":50,"k":50,"filter":"artefact eq '__p8_no_match__'"}:
        raise DeliveryRefused("inputs")
    if config == negative and target.index != "p8-test":
        raise DeliveryRefused("target")
    questions = load_frozen(bundle.root / "questions.jsonl")
    vectors = np.load(bundle.root / "queries.npy", allow_pickle=False)
    if len(questions) != 229 or vectors.shape != (229,1536) or not np.isfinite(vectors).all() or not vectors.any(axis=1).all():
        raise DeliveryRefused("inputs")
    tokens.token()  # Populate the RAM cache before worker threads; no four concurrent CLI logins.
    wait_for_count(target, tokens, client)
    started = time.monotonic()
    def measure(pair):
        i, q = pair
        begin = time.monotonic()
        if begin - started >= 240:
            return ArmResult(q.qid, "S2", [], 0, "deadline")
        try:
            hits = search_once(target, q.question, vectors[i], config, tokens, client)
            return ArmResult(q.qid, "S2", hits, (time.monotonic()-begin)*1000, None)
        except Exception:
            return ArmResult(q.qid, "S2", [], (time.monotonic()-begin)*1000, "request")
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(measure, enumerate(questions)))
    return grade_rows(bundle, rows), rows


def write_gate(result: GateResult, rows: list[ArmResult], output: Path) -> None:
    if len(result.cases) != 229 or len({c.qid for c in result.cases}) != 229:
        raise DeliveryRefused("gate")
    output.mkdir(parents=True, exist_ok=True)
    exclusive_json(output / "results.json", {"gate": asdict(result), "rows": [asdict(r) for r in rows],
                                             "latency_scope":"search-only; cached vectors"})
    failures = sum(c.error is not None for c in result.cases) + (not result.passed)
    suite = ET.Element("testsuite", name="S2 frozen retrieval", tests="230", failures=str(failures),
                       errors="0", skipped="0")
    for c in result.cases:
        case = ET.SubElement(suite,"testcase", classname="search.S2",name=c.qid)
        props = ET.SubElement(case,"properties")
        ET.SubElement(props,"property",name="top1",value=str(c.top1))
        if c.error:
            ET.SubElement(case,"failure",type="incomplete",message="qid did not produce a valid measurement")
    aggregate = ET.SubElement(suite,"testcase",classname="search.S2",name="aggregate-120-of-229")
    if not result.passed:
        ET.SubElement(aggregate,"failure",type="quality-gate",message="complete 229 measurements and 120 correct required")
    with (output / "results.xml").open("xb") as file:
        file.write(ET.tostring(suite,encoding="utf-8",xml_declaration=True))
