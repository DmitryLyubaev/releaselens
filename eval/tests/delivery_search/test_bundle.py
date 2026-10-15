import hashlib
import io
import json
import shutil
import tarfile

import httpx
import numpy as np
import pytest

from app.delivery_search.bundle import build_bundle, verify_bundle, fetch_bundle
from app.delivery_search.contracts import DeliveryRefused

SOURCE = "a" * 40


def test_complete_bundle(complete_inputs, tmp_path):
    archive = tmp_path / "release.tar.gz"
    digest = build_bundle(complete_inputs, SOURCE, archive)
    bundle = verify_bundle(archive, digest, tmp_path / "verified")
    assert (bundle.manifest.questions, bundle.manifest.chunks, bundle.manifest.dimensions,
            bundle.manifest.minimum_top1) == (229, 41825, 1536, 120)
    assert bundle.manifest.source_sha == SOURCE and bundle.digest == digest
    assert (bundle.root / "corpus.npy").stat().st_size == 256972928


@pytest.mark.parametrize("mutation", ["duplicate", "changed", "missing", "extra", "model", "nan", "zero", "dimension"])
def test_question_vector_binding(complete_inputs, tmp_path, mutation):
    root = tmp_path / "input"
    root.mkdir()
    # Large immutable inputs are hard-linked; only mutated small files are copied.
    for path in complete_inputs.iterdir():
        if path.name in ("queries.json", "queries.npy"):
            shutil.copyfile(path, root / path.name)
        else:
            (root / path.name).hardlink_to(path)
    meta = json.loads((root / "queries.json").read_text())
    if mutation == "duplicate": meta["questions"][1] = meta["questions"][0]
    if mutation == "changed": meta["questions"][0]["question_sha256"] = "b" * 64
    if mutation == "missing": meta["questions"].pop()
    if mutation == "extra": meta["questions"].append(dict(qid="unknown", question_sha256="c" * 64))
    if mutation == "model": meta["model"] = "other-model"
    (root / "queries.json").write_text(json.dumps(meta))
    if mutation in ("nan", "zero", "dimension"):
        data = np.load(root / "queries.npy")
        if mutation == "nan": data[0, 0] = float("nan")
        if mutation == "zero": data[0] = 0
        if mutation == "dimension": data = data[:, :3]
        np.save(root / "queries.npy", data)
    with pytest.raises(DeliveryRefused):
        build_bundle(root, SOURCE, tmp_path / "bad.tar.gz")


@pytest.mark.parametrize("name,kind", [("../escape", "file"), ("/absolute", "file"),
    ("C:/escape", "file"), ("link", "symlink"), ("duplicate", "duplicate"), ("many", "many")])
def test_archive_boundaries(tmp_path, name, kind):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as out:
        for i in range(33 if kind == "many" else 2 if kind == "duplicate" else 1):
            member = tarfile.TarInfo(str(i) if kind == "many" else name)
            member.size = 1
            if kind == "symlink": member.type, member.linkname = tarfile.SYMTYPE, "../escape"
            out.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(DeliveryRefused):
        verify_bundle(archive, "0" * 64, tmp_path / "out")
    assert not (tmp_path / "escape").exists()


def test_archive_size_limits(tmp_path, monkeypatch):
    from app.delivery_search import bundle as module
    archive = tmp_path / "large.tar.gz"
    archive.write_bytes(b"x" * 11)
    monkeypatch.setattr(module, "MAX_COMPRESSED", 10)
    with pytest.raises(DeliveryRefused): verify_bundle(archive, "a" * 64, tmp_path / "out")
    monkeypatch.setattr(module, "MAX_COMPRESSED", 10000)
    monkeypatch.setattr(module, "MAX_EXTRACTED", 10)
    with tarfile.open(archive, "w:gz") as out:
        member = tarfile.TarInfo("payload")
        member.size = 11
        out.addfile(member, io.BytesIO(b"x" * 11))
    with pytest.raises(DeliveryRefused): verify_bundle(archive, "a" * 64, tmp_path / "out")


def test_missing_scorer_and_lock_hashes(complete_inputs, tmp_path):
    for missing in ["scorer.py", "provider.lock.hcl", "requirements.lock"]:
        root = tmp_path / missing.replace(".", "-")
        root.mkdir()
        for p in complete_inputs.iterdir():
            if p.name != missing: (root / p.name).hardlink_to(p)
        with pytest.raises(DeliveryRefused): build_bundle(root, SOURCE, tmp_path / "bad.gz")


def test_tampered_archive_digest(complete_inputs, tmp_path):
    archive = tmp_path / "release.gz"
    build_bundle(complete_inputs, SOURCE, archive)
    with pytest.raises(DeliveryRefused): verify_bundle(archive, "f" * 64, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("url", ["https://evil.example/file", "http://owned.blob.core.windows.net/file",
    "https://owned.blob.core.windows.net.evil.example/file", "https://owned.blob.core.windows.net/file?sig=secret"])
def test_private_transport(url, tmp_path):
    class Tokens:
        def token(self): raise AssertionError("untrusted URL must not request a credential")
    def network(request): raise AssertionError("no request allowed")
    with httpx.Client(transport=httpx.MockTransport(network)) as client:
        with pytest.raises(DeliveryRefused):
            fetch_bundle(url, "a" * 64, tmp_path / "out", Tokens(), client,
                         allowed_host="owned.blob.core.windows.net")


def test_redirect_not_followed(tmp_path):
    requests = []
    class Tokens:
        def token(self): return "synthetic"
    def network(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://evil.example/file"})
    with httpx.Client(transport=httpx.MockTransport(network), follow_redirects=True) as client:
        with pytest.raises(DeliveryRefused):
            fetch_bundle("https://owned.blob.core.windows.net/inputs/releases/"+"a"*64+"/release.tar.gz",
                         "a"*64, tmp_path / "out", Tokens(), client, allowed_host="owned.blob.core.windows.net")
    assert len(requests) == 1 and requests[0].url.host == "owned.blob.core.windows.net"
