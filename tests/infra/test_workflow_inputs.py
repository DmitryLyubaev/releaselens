"""The Terraform jobs of deploy.yml and destroy.yml give the app stack every input it needs, each
read from its place in the GitHub environment `azure`.

A required variable missing from destroy.yml would fail every nightly destroy under -input=false
while Postgres kept billing, and CI would stay green, because `terraform test` supplies its own
variables. A secret read as `vars.*` resolves to an empty string, and one stored back as a
variable would be printed unmasked in the public run log."""

import re

import pytest
import yaml

TERRAFORM_JOBS = {"deploy.yml": "deploy", "destroy.yml": "destroy"}

# Spec §4.12, amended 2026-09-30 and 2026-10-01.
SECRETS = {"AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID", "TFSTATE_STORAGE_ACCOUNT", "AZURE_OPENAI_BASE_URL"}
SOURCES = {
    "ARM_CLIENT_ID": "vars.AZURE_CLIENT_ID",
    "ARM_TENANT_ID": "secrets.AZURE_TENANT_ID",
    "ARM_SUBSCRIPTION_ID": "secrets.AZURE_SUBSCRIPTION_ID",
    "TF_VAR_subscription_id": "secrets.AZURE_SUBSCRIPTION_ID",
    "TF_VAR_app_identity_id": "vars.APP_IDENTITY_ID",
    "TF_VAR_app_identity_client_id": "vars.APP_IDENTITY_CLIENT_ID",
    "TF_VAR_azure_openai_base_url": "secrets.AZURE_OPENAI_BASE_URL",
    "TF_VAR_azure_openai_deployment": "vars.AZURE_OPENAI_DEPLOYMENT",
    "TFSTATE_STORAGE_ACCOUNT": "secrets.TFSTATE_STORAGE_ACCOUNT",
}


def _blank(pattern, text):
    # Same length, so that offsets into the blanked text are offsets into the original.
    return re.sub(pattern, lambda match: " " * len(match.group()), text)


def required_variables(hcl):
    """Names of the variables declared with no `default`, which Terraform must be given."""
    # Strings and comments can hold braces (the image regex has {64}), so they are blanked before
    # the braces are matched.
    code = _blank(r'#[^\n]*|//[^\n]*', _blank(r'"(?:\\.|[^"\\\n])*"', hcl))
    required = []
    for declaration in re.finditer(r'^variable\s+"([^"]+)"\s*\{', hcl, re.M):
        depth, end = 1, declaration.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(code[end], 0)
            end += 1
        body = code[declaration.end():end - 1]
        # Nested blocks such as validation are dropped, so only the variable's own arguments stay.
        while re.search(r"\{[^{}]*\}", body):
            body = re.sub(r"\{[^{}]*\}", "", body)
        if not re.search(r"^\s*default\s*=", body, re.M):
            required.append(declaration.group(1))
    return required


def _job_env(repo_root, name):
    workflow = yaml.safe_load((repo_root / ".github" / "workflows" / name).read_bytes())
    return workflow["jobs"][TERRAFORM_JOBS[name]].get("env") or {}


def test_the_parser_finds_required_and_optional_variables():
    hcl = (
        'variable "needed" {\n  type = string\n  validation {\n    condition = can(regex("^[a-f]{64}$", var.needed))\n'
        '    error_message = "must be 64 hex characters; a default is not allowed"\n  }\n}\n\n'
        '# variable "commented" {}\n'
        'variable "optional" {\n  type    = string\n  default = ""\n}\n'
        'variable "nullable" {\n  default = null\n}\n'
    )
    assert required_variables(hcl) == ["needed"]


@pytest.mark.parametrize("name", sorted(TERRAFORM_JOBS))
def test_the_terraform_job_sets_every_required_app_stack_variable(repo_root, name):
    required = required_variables((repo_root / "infra" / "terraform" / "variables.tf").read_text(encoding="utf-8"))
    assert "subscription_id" in required, "the parser found none of the declarations"
    env = _job_env(repo_root, name)
    assert sorted(variable for variable in required if f"TF_VAR_{variable}" not in env) == []


def test_deploy_sets_the_image_the_preflight_resolved(repo_root):
    # image has a placeholder default, which destroy relies on and no deploy may use.
    assert _job_env(repo_root, "deploy.yml")["TF_VAR_image"] == "${{ needs.preflight.outputs.image }}"


@pytest.mark.parametrize("name", sorted(TERRAFORM_JOBS))
def test_the_terraform_job_reads_each_value_from_its_place_in_the_environment(repo_root, name):
    env = _job_env(repo_root, name)
    assert {key: env.get(key) for key in SOURCES} == {
        key: f"${{{{ {source} }}}}" for key, source in SOURCES.items()
    }


@pytest.mark.parametrize("name", sorted(TERRAFORM_JOBS))
def test_no_secret_is_read_as_a_variable(repo_root, name):
    text = (repo_root / ".github" / "workflows" / name).read_text(encoding="utf-8")
    assert [secret for secret in sorted(SECRETS) if re.search(rf"\bvars\.{secret}\b", text)] == []
