"""Public contracts and small, deterministic validation primitives."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


class DeliveryRefused(Exception):
    """Only stable codes cross the public CLI boundary, never remote response bodies."""
    def __init__(self, code: str):
        self.code = code if code in {"inputs", "bundle", "transport", "authority", "reserved",
            "prepare", "target", "request", "upload", "count", "gate", "plan", "identity",
            "deadline", "cleanup", "checks", "evidence", "settings", "internal"} else "internal"
        super().__init__(self.code)


REQUEST_CHECKS = frozenset({
    "metadata-budget-http", "metadata-budget-transport", "blob-create-http", "blob-create-transport",
    "search-count-http", "search-count-transport", "search-documents-http", "search-documents-transport",
})


class RequestRefused(DeliveryRefused):
    """Only a fixed operation label and numeric status cross the stage boundary."""
    def __init__(self, check, http_status=None):
        self.check = check if isinstance(check,str) and check in REQUEST_CHECKS else "unclassified"
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        super().__init__("request")


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


@dataclass(frozen=True)
class SearchTarget:
    endpoint: str
    index: str
    allowed_host: str

    def __post_init__(self):
        parsed = urlsplit(self.endpoint)
        if (not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,58}[a-z0-9]\.search\.windows\.net", self.allowed_host)
            or parsed.scheme != "https" or parsed.netloc != self.allowed_host
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or self.index not in ("p8-test", "p8-demo-prod")):
            raise DeliveryRefused("target")


@dataclass(frozen=True)
class GateCase:
    qid: str
    top1: int
    error: str | None


@dataclass(frozen=True)
class GateResult:
    expected: int
    evaluated: int
    correct: int
    errors: int
    missing: int
    skipped: int
    passed: bool
    cases: tuple[GateCase, ...] = ()


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
