"""Validate frozen payloads and transfer private, content-addressed release archives."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
from urllib.parse import urlsplit

import httpx
import numpy as np

from .contracts import (DeliveryRefused, FileDigest, ReleaseManifest, VerifiedBundle,
                        canonical, sha256_file)
from ..retrieval.corpus import load_chunks, load_links
from ..retrieval.questions import load_frozen

MAX_COMPRESSED = 512 * 1024 * 1024
MAX_EXTRACTED = 1024 * 1024 * 1024
MAX_FILES = 32
REQUIRED = frozenset({"chunks.jsonl", "links.jsonl", "corpus.npy", "corpus.ids.json",
    "questions.jsonl", "questions.manifest.json", "queries.npy", "queries.json", "schema.json",
    "s2.json", "negative.json", "scorer.py", "requirements.lock", "provider.lock.hcl"})
SHA = re.compile(r"[0-9a-f]{64}\Z")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_queries(root: Path) -> None:
    questions = load_frozen(root / "questions.jsonl")
    qids = [q.qid for q in questions]
    if len(qids) != 229 or len(set(qids)) != 229:
        raise DeliveryRefused("inputs")
    meta = read_json(root / "queries.json")
    expected = [{"qid": q.qid, "question_sha256": hashlib.sha256(q.question.encode()).hexdigest()}
                for q in questions]
    if (meta.get("model"), meta.get("version"), meta.get("dimensions"), meta.get("questions")) != (
            "text-embedding-3-small", "1", 1536, expected):
        raise DeliveryRefused("inputs")
    vectors = np.load(root / "queries.npy", allow_pickle=False, mmap_mode="r")
    if vectors.shape != (229, 1536) or vectors.dtype != np.dtype("float32"):
        raise DeliveryRefused("inputs")
    if not np.isfinite(vectors).all() or not vectors.any(axis=1).all():
        raise DeliveryRefused("inputs")


def validate_inputs(root: Path) -> None:
    try:
        names = {p.name for p in root.iterdir()}
        if names != REQUIRED or any(p.is_symlink() or not p.is_file() for p in root.iterdir()):
            raise DeliveryRefused("inputs")
        if sum(p.stat().st_size for p in root.iterdir()) > MAX_EXTRACTED:
            raise DeliveryRefused("inputs")
        validate_queries(root)
        chunks = load_chunks(root / "chunks.jsonl")
        ids = read_json(root / "corpus.ids.json")
        chunk_ids = [c.chunk_id for c in chunks]
        if (len(chunks) != 41825 or len(set(chunk_ids)) != 41825 or len(ids) != 41825
                or len(set(ids)) != 41825 or set(ids) != set(chunk_ids)):
            raise DeliveryRefused("inputs")
        artefacts = {c.artefact for c in chunks}
        if "__p8_no_match__" in artefacts or any(q.target not in artefacts for q in load_frozen(root / "questions.jsonl")):
            raise DeliveryRefused("inputs")
        load_links(root / "links.jsonl")
        vectors = np.load(root / "corpus.npy", allow_pickle=False, mmap_mode="r")
        if vectors.shape != (41825, 1536) or vectors.dtype != np.dtype("float32"):
            raise DeliveryRefused("inputs")
        for start in range(0, len(vectors), 1000):
            block = vectors[start:start + 1000]
            if not np.isfinite(block).all() or not block.any(axis=1).all():
                raise DeliveryRefused("inputs")
        from ..retrieval.search_index import SCHEMA
        if read_json(root / "schema.json") != SCHEMA:
            raise DeliveryRefused("inputs")
        if read_json(root / "s2.json") != {"top": 50, "k": 50, "filter": None}:
            raise DeliveryRefused("inputs")
        if read_json(root / "negative.json") != {"top": 50, "k": 50, "filter": "artefact eq '__p8_no_match__'"}:
            raise DeliveryRefused("inputs")
        if any(not (root / name).stat().st_size for name in ["scorer.py", "requirements.lock", "provider.lock.hcl"]):
            raise DeliveryRefused("inputs")
    except DeliveryRefused:
        raise
    except (ValueError, OSError, KeyError, TypeError, UnicodeError):
        raise DeliveryRefused("inputs") from None


def build_bundle(inputs: Path, source_sha: str, output: Path) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise DeliveryRefused("bundle")
    validate_inputs(inputs)
    manifest = ReleaseManifest(1, source_sha, tuple(FileDigest(name, sha256_file(inputs / name),
        (inputs / name).stat().st_size) for name in sorted(REQUIRED)),
        sha256_file(inputs / "questions.jsonl"), "text-embedding-3-small", "1", 1536, 229, 41825,
        120, sha256_file(inputs / "s2.json"))
    data = canonical(asdict(manifest))
    digest = hashlib.sha256(data).hexdigest()
    import io
    with output.open("xb") as file:
        with tarfile.open(fileobj=file, mode="w:gz", compresslevel=1) as archive:
            entry = tarfile.TarInfo("manifest.json")
            entry.size, entry.mtime = len(data), 0
            archive.addfile(entry, io.BytesIO(data))
            for item in manifest.files:
                entry = tarfile.TarInfo(item.path)
                entry.size, entry.mtime = item.size, 0
                with (inputs / item.path).open("rb") as source:
                    archive.addfile(entry, source)
    if output.stat().st_size > MAX_COMPRESSED:
        raise DeliveryRefused("bundle")
    return digest


def verify_bundle(archive: Path, expected_digest: str, destination: Path) -> VerifiedBundle:
    if not SHA.fullmatch(expected_digest) or archive.stat().st_size > MAX_COMPRESSED or destination.exists():
        raise DeliveryRefused("bundle")
    destination.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="p8-verify-", dir=destination.parent))
    try:
        names, size = set(), 0
        with tarfile.open(archive, "r:gz") as incoming:
            for member in incoming:
                name = member.name
                pure = PurePosixPath(name)
                size += member.size
                if (not member.isfile() or pure.is_absolute() or len(pure.parts) != 1
                    or ":" in name or "\\" in name or name not in REQUIRED | {"manifest.json"}
                    or name.casefold() in names or member.size < 0 or size > MAX_EXTRACTED
                    or len(names) >= MAX_FILES):
                    raise DeliveryRefused("bundle")
                names.add(name.casefold())
                if name == "manifest.json" and member.size > 65536:
                    raise DeliveryRefused("bundle")
                with incoming.extractfile(member) as source, (scratch / name).open("xb") as out:
                    shutil.copyfileobj(source, out, length=1024 * 1024)
        if names != REQUIRED | {"manifest.json"}:
            raise DeliveryRefused("bundle")
        raw = read_json(scratch / "manifest.json")
        digest = hashlib.sha256(canonical(raw)).hexdigest()
        if digest != expected_digest:
            raise DeliveryRefused("bundle")
        raw["files"] = tuple(FileDigest(**row) for row in raw["files"])
        manifest = ReleaseManifest(**raw)
        if (manifest.version, manifest.query_model, manifest.query_model_version,
            manifest.questions, manifest.chunks, manifest.dimensions, manifest.minimum_top1) != (
                1, "text-embedding-3-small", "1", 229, 41825, 1536, 120):
            raise DeliveryRefused("bundle")
        if not re.fullmatch(r"[0-9a-f]{40}", manifest.source_sha):
            raise DeliveryRefused("bundle")
        if {x.path for x in manifest.files} != REQUIRED or len(manifest.files) != len(REQUIRED):
            raise DeliveryRefused("bundle")
        for entry in manifest.files:
            path = scratch / entry.path
            if not SHA.fullmatch(entry.sha256) or path.stat().st_size != entry.size or sha256_file(path) != entry.sha256:
                raise DeliveryRefused("bundle")
        if manifest.question_set_sha256 != sha256_file(scratch / "questions.jsonl") or manifest.query_config_sha256 != sha256_file(scratch / "s2.json"):
            raise DeliveryRefused("bundle")
        (scratch / "manifest.json").unlink()
        validate_inputs(scratch)
        # Recheck the claimed source/digest at every consumer; archive code is never executed.
        os.replace(scratch, destination)
        return VerifiedBundle(destination, manifest, digest)
    except DeliveryRefused:
        raise
    except (tarfile.TarError, ValueError, OSError, KeyError, TypeError, UnicodeError):
        raise DeliveryRefused("bundle") from None
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)


def fetch_bundle(storage_url: str, expected_digest: str, destination: Path, tokens,
                 client: httpx.Client, *, allowed_host: str) -> VerifiedBundle:
    url = urlsplit(storage_url)
    if (not re.fullmatch(r"[a-z0-9]{3,24}\.blob\.core\.windows\.net", allowed_host)
        or url.scheme != "https" or url.netloc != allowed_host or url.query or url.fragment
        or url.path != f"/inputs/releases/{expected_digest}/release.tar.gz" or not SHA.fullmatch(expected_digest)):
        raise DeliveryRefused("transport")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="p8-fetch-", dir=destination.parent)
    os.close(fd)
    downloaded = Path(path)
    try:
        with client.stream("GET", storage_url, headers={"Authorization": f"Bearer {tokens.token()}",
                           "x-ms-version": "2023-11-03"}, follow_redirects=False) as response:
            if response.status_code != 200:
                raise DeliveryRefused("transport")
            size = 0
            with downloaded.open("wb") as out:
                for block in response.iter_bytes(1024 * 1024):
                    size += len(block)
                    if size > MAX_COMPRESSED:
                        raise DeliveryRefused("transport")
                    out.write(block)
        return verify_bundle(downloaded, expected_digest, destination)
    except httpx.HTTPError:
        raise DeliveryRefused("transport") from None
    finally:
        downloaded.unlink(missing_ok=True)
