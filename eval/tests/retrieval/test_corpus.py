"""Reading the Worker's `export-corpus` files: one camelCase JSON object per line."""

import json
from pathlib import Path

from app.retrieval.corpus import Chunk, load_chunks, load_links


def _write_lines(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_load_chunks_reads_the_worker_export(tmp_path):
    path = _write_lines(
        tmp_path / "chunks.jsonl",
        [
            {
                "chunkId": 7,
                "artefact": "commit:1a2b3c4d5e",
                "entityType": "commit",
                "entityKey": "1a2b3c4d5e",
                "chunkIndex": 0,
                "tokenCount": 42,
                "content": "fix: planner null reference — guard añadido",
            },
            {
                "chunkId": 9,
                "artefact": "pull_request:101",
                "entityType": "pull_request",
                "entityKey": "101",
                "chunkIndex": 1,
                "tokenCount": 12,
                "content": "second chunk",
            },
        ],
    )

    chunks = load_chunks(path)

    assert chunks == [
        Chunk(7, "commit:1a2b3c4d5e", "commit", "1a2b3c4d5e", 0, 42,
              "fix: planner null reference — guard añadido"),
        Chunk(9, "pull_request:101", "pull_request", "101", 1, 12, "second chunk"),
    ]


def test_load_links_is_symmetric(tmp_path):
    path = _write_lines(
        tmp_path / "links.jsonl",
        [
            {"pullRequest": "pull_request:101", "commit": "commit:aaaaaaa"},
            {"pullRequest": "pull_request:102", "commit": "commit:bbbbbbb"},
            # A commit two pull requests both name: each side still maps to the other.
            {"pullRequest": "pull_request:103", "commit": "commit:bbbbbbb"},
        ],
    )

    links = load_links(path)

    assert links == {
        "pull_request:101": frozenset({"commit:aaaaaaa"}),
        "commit:aaaaaaa": frozenset({"pull_request:101"}),
        "pull_request:102": frozenset({"commit:bbbbbbb"}),
        "pull_request:103": frozenset({"commit:bbbbbbb"}),
        "commit:bbbbbbb": frozenset({"pull_request:102", "pull_request:103"}),
    }
    for source, targets in links.items():
        for target in targets:
            assert source in links[target]
