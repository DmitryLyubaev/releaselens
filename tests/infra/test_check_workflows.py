"""check_workflows: the rules that keep the GitHub environment `azure` to the allowlisted
workflows, run over fixture workflow sets. Each bad-* set breaks exactly one rule."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import check_workflows as cw  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "workflows"
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_workflows.py"

BAD = {
    "bad-env-in-ci":
        "ci.yml: job 'terraform-plan': environment 'azure' is reserved for the workflows in "
        "ALLOWED_AZURE",
    "bad-env-mapping-uppercase":
        "preview.yml: job 'preview': environment 'Azure' is reserved for the workflows in "
        "ALLOWED_AZURE",
    "bad-env-expression":
        "release.yml: job 'release': environment '${{ vars.RELEASE_ENVIRONMENT }}' is an "
        "expression and could resolve to azure",
    "bad-env-yaml-extension":
        "deploy.yaml: job 'deploy': environment 'azure' is reserved for the workflows in "
        "ALLOWED_AZURE",
    "bad-external-reusable-workflow":
        "preview.yml: job 'plan': reusable workflow "
        "'someone/shared/.github/workflows/terraform.yml@0123456789abcdef0123456789abcdef01234567' "
        "is external and could run with environment azure",
    "bad-deploy-on-push":
        "deploy.yml: triggers must be exactly workflow_dispatch (found: push, workflow_dispatch)",
    "bad-destroy-wrong-cron":
        "destroy.yml: schedule must include cron '0 14 * * *' (found: '0 13 * * *')",
    "bad-missing-top-permissions":
        "deploy.yml: top-level permissions must be {} (found: absent)",
    "bad-extra-job-permission":
        "deploy.yml: job 'deploy': permissions must be "
        '{"id-token": "write", "contents": "read"} '
        '(found: {"id-token": "write", "contents": "read", "packages": "write"})',
    "bad-preflight-permissions":
        "deploy.yml: job 'preflight': permissions must be "
        '{"actions": "read"} (found: {"actions": "read", "contents": "read"})',
    "bad-other-job-id-token":
        "destroy.yml: job 'report': only the job with environment azure may have id-token "
        '(found: {"id-token": "write", "contents": "read"})',
    "bad-other-job-write-all":
        "deploy.yml: job 'notify': only the job with environment azure may have id-token "
        '(found: "write-all")',
    "bad-concurrency-cancel-true":
        "destroy.yml: top-level concurrency must be "
        '{"group": "releaselens-azure", "cancel-in-progress": false, "queue": "max"} '
        '(found: {"group": "releaselens-azure", "cancel-in-progress": true, "queue": "max"})',
    "bad-unpinned-action":
        "deploy.yml: job 'deploy': 'hashicorp/setup-terraform@v3' must be pinned to a 40-hex "
        "commit SHA",
    "bad-publish-image-unpinned":
        "ci.yml: job 'publish-image': 'docker/build-push-action@v6' must be pinned to a 40-hex "
        "commit SHA",
}


def _cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_good_fixtures_pass():
    assert cw.check(FIXTURES / "good") == []


@pytest.mark.parametrize("fixture", sorted(BAD))
def test_bad_fixture_fails_with_its_violation(fixture):
    assert cw.check(FIXTURES / fixture) == [BAD[fixture]]


def test_every_bad_fixture_has_a_case():
    assert sorted(path.name for path in FIXTURES.glob("bad-*")) == sorted(BAD)


def test_local_actions_are_exempt_from_pinning(tmp_path):
    (tmp_path / "oidc-probe.yml").write_text(
        "on: workflow_dispatch\n"
        "permissions: {}\n"
        "jobs:\n"
        "  probe:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: ./.github/actions/probe\n",
        encoding="utf-8",
    )
    assert cw.check(tmp_path) == []


def test_padded_environment_name_is_still_azure(tmp_path):
    (tmp_path / "ci.yml").write_text(
        "on: pull_request\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    environment: ' AZURE '\n"
        "    steps:\n"
        "      - run: echo build\n",
        encoding="utf-8",
    )
    assert cw.check(tmp_path) == [
        "ci.yml: job 'build': environment ' AZURE ' is reserved for the workflows in ALLOWED_AZURE"
    ]


def test_invalid_yaml_is_a_violation(tmp_path):
    (tmp_path / "broken.yml").write_text("jobs:\n  build: [unclosed\n", encoding="utf-8")
    assert cw.check(tmp_path) == ["broken.yml: not valid YAML at line 3"]


def test_cli_prints_each_violation_and_exits_1(tmp_path):
    shutil.copy(FIXTURES / "bad-env-in-ci" / "ci.yml", tmp_path)
    shutil.copy(FIXTURES / "bad-env-mapping-uppercase" / "preview.yml", tmp_path)
    result = _cli(tmp_path)
    assert result.returncode == 1
    assert result.stdout.splitlines() == [BAD["bad-env-in-ci"], BAD["bad-env-mapping-uppercase"]]


def test_cli_exits_0_on_good_fixtures():
    result = _cli(FIXTURES / "good")
    assert result.returncode == 0, result.stdout


def test_cli_rejects_a_missing_directory(tmp_path):
    assert _cli(tmp_path / "missing").returncode == 2


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="ci.yml's publish-image actions are tag-pinned until Task 11 pins them to SHAs",
)
def test_repository_workflows_pass(repo_root):
    assert cw.check(repo_root / ".github" / "workflows") == []
