from __future__ import annotations

from pathlib import Path

import yaml

from .models import GoldenQuery

_SEARCH_NAMES = ("golden/queries.yaml", "eval/golden/queries.yaml")


def find_golden_file(start: Path | None = None) -> Path:
    current = (start or Path(__file__).resolve()).parent
    for directory in [current, *current.parents]:
        for name in _SEARCH_NAMES:
            candidate = directory / name
            if candidate.exists():
                return candidate
    raise FileNotFoundError("Could not locate golden/queries.yaml")


def load_golden(path: Path | None = None) -> list[GoldenQuery]:
    target = path or find_golden_file()
    document = yaml.safe_load(target.read_text(encoding="utf-8"))
    return [GoldenQuery(**entry) for entry in document["queries"]]
