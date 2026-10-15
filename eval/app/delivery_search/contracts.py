"""Public contracts and small, deterministic validation primitives."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path


class DeliveryRefused(Exception):
    """Only stable codes cross the public CLI boundary, never remote response bodies."""
    def __init__(self, code: str):
        self.code = code if code in {"inputs", "bundle", "transport", "authority", "reserved",
            "prepare", "target", "request", "upload", "count", "gate", "plan", "identity",
            "deadline", "cleanup", "checks", "evidence", "internal"} else "internal"
        super().__init__(self.code)


@dataclass(frozen=True)
class FileDigest:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ReleaseManifest:
    version: int
    source_sha: str
    files: tuple[FileDigest, ...]
    question_set_sha256: str
    query_model: str
    query_model_version: str
    dimensions: int
    questions: int
    chunks: int
    minimum_top1: int
    query_config_sha256: str


@dataclass(frozen=True)
class VerifiedBundle:
    root: Path
    manifest: ReleaseManifest
    digest: str


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sha256_file(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def exclusive_json(path: Path, value) -> None:
    """Fail closed across crashes: create-only and flush before a reserved side effect."""
    import os
    try:
        with path.open("xb") as out:
            out.write(canonical(value) + b"\n")
            out.flush()
            os.fsync(out.fileno())
    except FileExistsError:
        raise DeliveryRefused("reserved") from None
