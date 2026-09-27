"""Both Terraform stacks' local state, plans, tfvars, overrides and crash logs must
never reach git, whichever stack they live under."""

import subprocess

import pytest

IGNORED = [
    "infra/bootstrap/terraform.tfstate",
    "infra/bootstrap/terraform.tfstate.backup",
    "infra/bootstrap/.terraform/x",
    "infra/bootstrap/terraform.tfvars",
    "infra/bootstrap/backend_override.tf",
    "infra/bootstrap/tfplan",
    "infra/terraform/terraform.tfstate",
    "infra/terraform/app.tfplan",
    "infra/terraform/terraform.tfvars",
    "infra/bootstrap/override.tf",
    "infra/terraform/override.tf.json",
    "infra/terraform/backend_override.tf.json",
    "infra/bootstrap/terraform.tfvars.json",
    "infra/terraform/crash.log",
    "infra/bootstrap/crash.1790000000.log",
]

NOT_IGNORED = [
    "infra/bootstrap/terraform.tfvars.example",
    "infra/bootstrap/.terraform.lock.hcl",
    "infra/bootstrap/tests/state.tftest.hcl",
    "infra/terraform/main.tf",
]


def _check_ignore(repo_root, path):
    return subprocess.run(
        ["git", "check-ignore", "-q", path],
        cwd=repo_root,
        check=False,
    ).returncode


@pytest.mark.parametrize("path", IGNORED)
def test_ignored(repo_root, path):
    assert _check_ignore(repo_root, path) == 0


@pytest.mark.parametrize("path", NOT_IGNORED)
def test_not_ignored(repo_root, path):
    assert _check_ignore(repo_root, path) == 1
