from test_pipelines_metadata_policy import POLICY
"""The complete authority must cross the platform limit without dropping bindings."""
import base64
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import importlib
import json
import os
import random
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def run(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "eval"))
    return importlib.import_module("scripts.pipelines.run")


def authority():
    return {
        "approval_ref": "actual-owner-approval", "metadata_policy":dict(POLICY),
        "owner_ado_id": "11111111-2222-4333-8444-555555555555",
        "session": "p8-example",
        "source_sha": "a" * 40,
        "tree_sha": "b" * 40,
        "pipeline_id": 2,
        "queue_cutoff_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "apply_started_at": datetime.now(timezone.utc).isoformat(),
        "principals": {role: {
            "service_connection_id": role + "-connection",
            "oidc_request_origin": "https://dev.azure.com/example",
            "oidc_path_prefix": "/example/_apis/distributedtask/hubs/build/",
        } for role in ("test", "prod", "verify")},
        "checks_expected": {str(i): {
            "resource": "exact-reviewed-protected-resource-" + str(i),
            "settings_sha256": hashlib.sha256(str(i).encode()).hexdigest(),
            "required_template": "pipelines/templates/search-delivery.yml",
            "protected_branch": "refs/heads/main",
        } for i in range(30)},
    }


def packed(raw):
    return json.dumps({"encoding": "gzip+base64-v1", "data":
        base64.b64encode(gzip.compress(raw, mtime=0)).decode("ascii")}, separators=(",", ":")).encode()


def context_env(run, monkeypatch, value, original):
    for key, val in {
        "P8_AUTHORITY_JSON": value.decode(),
        "P8_AUTHORITY_SHA256": hashlib.sha256(run.canonical(original)).hexdigest(),
        "P8_SOURCE_SHA": original["source_sha"],
        "BUILD_REASON": "Manual", "BUILD_SOURCEBRANCH": "refs/heads/main",
        "BUILD_SOURCEVERSION": original["source_sha"], "SYSTEM_DEFINITIONID": "2",
        "SYSTEM_JOBATTEMPT": "1", "SYSTEM_STAGEATTEMPT": "1",
    }.items():
        monkeypatch.setenv(key, val)
    commands = []
    def git_only(argv, **kwargs):
        assert argv[0] == "git"
        commands.append(argv)
        return SimpleNamespace(returncode=0, stdout=(original["tree_sha"] if argv[-1] == "HEAD^{tree}"
            else original["source_sha"]) + "\n")
    monkeypatch.setattr(run.subprocess, "run", git_only)
    return commands


def test_context_accepts_complete_packed_authority_before_login(run, monkeypatch, tmp_path):
    original = authority()
    raw = run.canonical(original)
    value = packed(raw)
    assert len(raw) > 4096 and len(value) <= 4096
    commands = context_env(run, monkeypatch, value, original)
    actual, principal = run.context(tmp_path / "absent.json", "build")
    assert actual == original and principal == original["principals"]["test"]
    assert len(commands) == 3


@pytest.mark.parametrize("owner", [None, "", "not-an-owner-id", "00000000-0000-0000-0000-000000000000"])
def test_missing_or_invalid_owner_id_refuses_before_git_or_login(run, monkeypatch, tmp_path, owner):
    original = authority()
    if owner is None:
        original.pop("owner_ado_id")
    else:
        original["owner_ado_id"] = owner
    commands = context_env(run, monkeypatch, packed(run.canonical(original)), original)
    with pytest.raises(run.DeliveryRefused, match="^authority$"):
        run.context(tmp_path / "absent.json", "build")
    assert commands == []


@pytest.fixture
def transport(run):
    return importlib.import_module("scripts.pipelines.authority_transport")


def test_pack_preserves_full_canonical_hash_and_fields(transport, run):
    original = authority()
    value = transport.encode_authority(original)
    assert len(value) <= 4096
    assert transport.decode_authority(value) == original
    assert hashlib.sha256(run.canonical(transport.decode_authority(value))).digest() == hashlib.sha256(run.canonical(original)).digest()


@pytest.mark.parametrize("from_file", [False, True])
def test_legacy_plain_authority_still_passes_context(run, monkeypatch, tmp_path, from_file):
    original = authority()
    value = run.canonical(original)
    context_env(run, monkeypatch, value, original)
    path = tmp_path / "authority.json"
    if from_file:
        path.write_bytes(value)
    assert run.context(path, "build")[0] == original


def test_decoded_hash_mismatch_refuses_before_git_or_login(run, monkeypatch, tmp_path):
    original = authority()
    commands = context_env(run, monkeypatch, packed(run.canonical(original)), original)
    monkeypatch.setenv("P8_AUTHORITY_SHA256", "0" * 64)
    with pytest.raises(run.DeliveryRefused, match="^authority$"):
        run.context(tmp_path / "absent.json", "build")
    assert commands == []


@pytest.mark.parametrize("value", [
    b'{"encoding":"unknown","data":"anything"}',
    b'{"encoding":"gzip+base64-v1","data":"@@@"}',
    b'{"encoding":"gzip+base64-v1","data":12}',
    b'{"encoding":"gzip+base64-v1","data":"","extra":1}',
    b'{"encoding":"gzip+base64-v1"}',
    b'[]', b'null', b'{"x":1,"x":2}', b'{"x":NaN}',
    b'{"x":Infinity}', b'\xff', b'{',
])
def test_malformed_authority_refuses_without_disclosing_value(transport, run, value):
    with pytest.raises(run.DeliveryRefused, match="^authority$"):
        transport.decode_authority(value)


@pytest.mark.parametrize("change", ["truncated", "bad-crc", "trailing", "second-member", "expanded-limit"])
def test_compressed_corruption_or_expansion_refuses(transport, run, change):
    compressed = gzip.compress(run.canonical(authority()), mtime=0)
    if change == "truncated": compressed = compressed[:-1]
    if change == "bad-crc": compressed = compressed[:-8] + b'\x00' * 8
    if change == "trailing": compressed += b'unreviewed'
    if change == "second-member": compressed += gzip.compress(b'{}')
    if change == "expanded-limit": compressed = gzip.compress(b'{"x":"' + b'x' * 32768 + b'"}')
    value = json.dumps({"encoding":"gzip+base64-v1", "data":base64.b64encode(compressed).decode()}).encode()
    with pytest.raises(run.DeliveryRefused, match="^authority$"):
        transport.decode_authority(value)


def test_pack_refuses_value_that_cannot_fit_actual_platform_limit(transport, run):
    value = {"entropy": base64.b64encode(random.Random(42).randbytes(5000)).decode()}
    with pytest.raises(run.DeliveryRefused, match="^authority$"):
        transport.encode_authority(value)


def test_transport_and_plain_sizes_are_bounded(transport, run):
    for value in (b' ' * 32769, b'{"encoding":"gzip+base64-v1","data":"' + b'A' * 4096 + b'"}'):
        with pytest.raises(run.DeliveryRefused, match="^authority$"):
            transport.decode_authority(value)
    with pytest.raises(run.DeliveryRefused, match="^authority$"):
        transport.encode_authority({"x": "a" * 32768})


def test_private_pack_command_checks_reviewed_hash_and_writes_once(transport, run, tmp_path, capsys):
    original = authority()
    source = tmp_path / "original.private.json"
    target = tmp_path / "transport.private.json"
    source.write_bytes(run.canonical(original))
    digest = hashlib.sha256(run.canonical(original)).hexdigest()
    args = ["--input", str(source), "--output", str(target), "--sha256", digest]
    assert transport.main(args[:-1] + ["0" * 64]) == 2
    assert not target.exists()
    assert transport.main(args) == 0
    saved = target.read_bytes()
    assert transport.decode_authority(saved) == original
    assert transport.main(args) == 2
    assert target.read_bytes() == saved
    stdout = capsys.readouterr().out
    assert "actual-owner-approval" not in stdout and "checks_expected" not in stdout


def test_authority_file_read_is_bounded(transport, run, tmp_path):
    path = tmp_path / "oversized.private.json"
    path.write_bytes(b' ' * 32769)
    with pytest.raises(run.DeliveryRefused, match="^authority$"):
        transport.read_authority(path)


def test_documented_module_command_works_in_fresh_process_without_pythonpath(repo_root, run, tmp_path):
    source = tmp_path / "reviewed.private.json"
    output = tmp_path / "packed.private.json"
    original = authority()
    source.write_bytes(run.canonical(original))
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run([sys.executable, "-B", "-m", "scripts.pipelines.authority_transport",
        "--input", str(source), "--output", str(output), "--sha256",
        hashlib.sha256(run.canonical(original)).hexdigest()], cwd=repo_root, env=env,
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert output.exists() and "status=pass" in result.stdout
    assert "actual-owner-approval" not in result.stdout


@pytest.mark.parametrize("raw", [b'{"x":1e999}', b'{"x":"\\ud800"}'])
@pytest.mark.parametrize("compressed", [False, True])
def test_noncanonicalizable_values_refuse_cleanly(transport, run, raw, compressed, tmp_path, capsys):
    value = packed(raw) if compressed else raw
    with pytest.raises(run.DeliveryRefused, match="^authority$"):
        transport.decode_authority(value)
    source = tmp_path / "invalid.private.json"
    output = tmp_path / "output.private.json"
    source.write_bytes(value)
    assert transport.main(["--input", str(source), "--output", str(output), "--sha256", "0" * 64]) == 2
    assert not output.exists()
    assert capsys.readouterr().out == "status=refused code=authority\n"
