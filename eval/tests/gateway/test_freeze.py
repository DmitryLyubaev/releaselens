"""The freeze file and the guard that every measured command calls before its first request."""

import json
import subprocess
from pathlib import Path

import pytest

from app.gateway import freeze


def _file(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "freeze.json"
    path.write_text(content, encoding="utf-8")
    return path


def _git(stdout: str = "", returncode: int = 0):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, returncode, stdout, "")

    run.calls = calls
    return run


def test_the_committed_file_holds_the_signal_the_session_froze():
    # Frozen on 2026-10-09 (commit 344ed07) before the first measured request, and cited by the
    # published report. Nothing about the rule changes after that, so a later edit fails here.
    assert json.loads(freeze.FREEZE_FILE.read_text(encoding="utf-8")) == {"region_signal": "x-ms-region"}
    assert freeze.load().region_signal == "x-ms-region"


@pytest.mark.parametrize("signal", ["x-ms-region", "x-releaselens-backend"])
def test_a_frozen_signal_loads(tmp_path, signal):
    assert freeze.load(_file(tmp_path, json.dumps({"region_signal": signal}))) == freeze.Freeze(signal)


@pytest.mark.parametrize(("content", "why"), [
    ('{"region_signal": null}', "null"),
    ("{}", "null"),
    ("[]", "null"),
    ('{"region_signal": "x-other"}', "unknown"),
    ("not json", "cannot be read"),
])
def test_an_unfrozen_or_unreadable_file_raises_naming_the_file_and_why(tmp_path, content, why):
    with pytest.raises(freeze.FreezeError) as raised:
        freeze.load(_file(tmp_path, content))
    assert "freeze.json" in str(raised.value) and why in str(raised.value)


def test_a_missing_file_raises(tmp_path):
    with pytest.raises(freeze.FreezeError, match="does not exist"):
        freeze.load(tmp_path / "freeze.json")


def test_a_clean_tree_passes_and_git_is_asked_from_the_repo_root(tmp_path):
    run = _git("")
    freeze.require_clean_tree(run, tmp_path)
    ((command, kwargs),) = run.calls
    assert command == ["git", "status", "--porcelain"]
    assert kwargs["cwd"] == tmp_path


def test_a_dirty_tree_raises_with_the_reason_and_no_file_names():
    with pytest.raises(freeze.DirtyTreeError, match="not clean") as raised:
        freeze.require_clean_tree(_git(" M eval/app/gateway/rule.py\n?? scratch.txt\n"))
    assert "2" in str(raised.value) and "rule.py" not in str(raised.value)


def test_a_git_that_fails_is_not_taken_for_clean():
    with pytest.raises(freeze.DirtyTreeError, match="cannot tell"):
        freeze.require_clean_tree(_git("", returncode=128))

    def missing(command, **kwargs):
        raise FileNotFoundError("git")

    with pytest.raises(freeze.DirtyTreeError, match="cannot tell"):
        freeze.require_clean_tree(missing)


def test_require_measurable_checks_the_signal_then_the_tree(tmp_path):
    path = _file(tmp_path, '{"region_signal": "x-ms-region"}')
    assert freeze.require_measurable(_git(""), path, tmp_path) == freeze.Freeze("x-ms-region")

    with pytest.raises(freeze.DirtyTreeError):
        freeze.require_measurable(_git(" M x\n"), path, tmp_path)

    run = _git("")
    with pytest.raises(freeze.FreezeError):
        freeze.require_measurable(run, _file(tmp_path, '{"region_signal": null}'), tmp_path)
    assert run.calls == []     # nothing about the tree is asked while the signal is unfrozen


def test_both_guard_errors_share_one_base_for_the_cli():
    assert issubclass(freeze.FreezeError, freeze.MeasuredRunRefused)
    assert issubclass(freeze.DirtyTreeError, freeze.MeasuredRunRefused)


def test_the_reports_directory_is_git_ignored_for_real():
    """Writing a run's records under eval/reports/ cannot dirty the tree for the next measured command."""
    freeze.require_output_ignored(freeze.REPO_ROOT / "eval" / "reports")


def test_an_output_directory_that_git_does_not_ignore_is_refused(tmp_path):
    root = tmp_path / "repo"
    (root / "somewhere").mkdir(parents=True)
    with pytest.raises(freeze.DirtyTreeError, match="not git-ignored"):
        freeze.require_output_ignored(root / "somewhere", _git("", returncode=1), root)
    freeze.require_output_ignored(root / "somewhere", _git("", returncode=0), root)


def test_an_output_directory_outside_the_repository_is_left_alone(tmp_path):
    run = _git("", returncode=1)
    freeze.require_output_ignored(tmp_path / "elsewhere", run, tmp_path / "repo")
    assert run.calls == []
