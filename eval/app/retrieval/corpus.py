"""The corpus the Worker's `export-corpus` writes: its chunks, and its pull request links.

Both files are UTF-8 JSON lines with camelCase fields. This reads them and nothing more: it
does not check the export against the database it came from.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Chunk:
    chunk_id: int
    artefact: str
    entity_type: str
    entity_key: str
    chunk_index: int
    token_count: int
    content: str


def _json_lines(path: Path):
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            if line.strip():
                yield json.loads(line)


def load_chunks(path: Path) -> list[Chunk]:
    """Every chunk in `chunks.jsonl`, in the file's order."""
    return [
        Chunk(
            chunk_id=row["chunkId"],
            artefact=row["artefact"],
            entity_type=row["entityType"],
            entity_key=row["entityKey"],
            chunk_index=row["chunkIndex"],
            token_count=row["tokenCount"],
            content=row["content"],
        )
        for row in _json_lines(path)
    ]


def load_links(path: Path) -> dict[str, frozenset[str]]:
    """Each pull request's merge commits, and each commit's pull requests, from `links.jsonl`.

    Symmetric, so lenient scoring can look up either side. An artefact with no link is absent,
    not mapped to an empty set.
    """
    linked: defaultdict[str, set[str]] = defaultdict(set)
    for row in _json_lines(path):
        pull_request, commit = row["pullRequest"], row["commit"]
        linked[pull_request].add(commit)
        linked[commit].add(pull_request)
    return {artefact: frozenset(others) for artefact, others in linked.items()}
