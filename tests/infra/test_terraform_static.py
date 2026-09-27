"""Static guards over both Terraform stacks' source.

CI runs `terraform fmt -check`, so blocks open as `<kind> "<type>" "<name>" {` at the start of a
line and close with `}` in column 0, and attributes sit one to a line. Plain regular expressions
over the text are reliable under that layout. Matches are anchored to the start of a line, so a
comment that mentions a name does not count.
"""

import re
from pathlib import Path

import pytest

BOOTSTRAP_PROVIDERS = [
    "Microsoft.Storage",
    "Microsoft.ManagedIdentity",
    "Microsoft.CognitiveServices",
    "Microsoft.App",
    "Microsoft.DBforPostgreSQL",
    "Microsoft.Consumption",
    "Microsoft.Insights",
]

# Bootstrap owns every one of these; the app stack may neither create nor read them.
APP_STACK_FORBIDDEN = [
    "azurerm_role_assignment",
    "azurerm_user_assigned_identity",
    "azurerm_federated_identity_credential",
    "azurerm_resource_group",
    "azurerm_key_vault",
    "azurerm_log_analytics_workspace",
    "terraform_remote_state",
]


def _stack(repo_root: Path, name: str) -> dict[str, str]:
    paths = sorted((repo_root / "infra" / name).glob("*.tf"))
    assert paths, f"no .tf files in infra/{name}"
    files = {path.name: path.read_text(encoding="utf-8") for path in paths}
    return files


def _block(text: str, header: str) -> str:
    """The body of the top-level block that opens with `header {`."""
    match = re.search(rf"^{re.escape(header)} \{{\n(.*?)^\}}", text, re.MULTILINE | re.DOTALL)
    assert match, f"no block {header}"
    return match.group(1)


def _setting(body: str, name: str) -> re.Match | None:
    return re.search(rf"^\s*{name}\s*=\s*(.*?)\s*$", body, re.MULTILINE)


def _declarations(files: dict[str, str], kind: str, type_: str) -> list[str]:
    """The file name of each `<kind> "<type_>"` declaration, once per declaration."""
    pattern = re.compile(rf'^{kind} "{re.escape(type_)}" "', re.MULTILINE)
    return [name for name, text in files.items() for _ in pattern.finditer(text)]


def test_bootstrap_registers_exactly_the_seven_providers(repo_root):
    provider = _block(_stack(repo_root, "bootstrap")["versions.tf"], 'provider "azurerm"')

    registrations = _setting(provider, "resource_provider_registrations")
    assert registrations and registrations.group(1) == '"none"'
    listed = re.search(r"^\s*resource_providers_to_register\s*=\s*\[(.*?)\]", provider, re.MULTILINE | re.DOTALL)
    assert listed, "resource_providers_to_register is not set"
    lines = [line.strip() for line in listed.group(1).splitlines()]
    entries = [line.rstrip(",") for line in lines if line and not line.startswith("#")]
    # Sorted, not a set, so a duplicate fails too.
    assert sorted(entries) == sorted(f'"{name}"' for name in BOOTSTRAP_PROVIDERS)


def test_app_stack_registers_no_providers(repo_root):
    # CI may not register resource providers; bootstrap registers the ones this stack uses.
    provider = _block(_stack(repo_root, "terraform")["versions.tf"], 'provider "azurerm"')

    registrations = _setting(provider, "resource_provider_registrations")
    assert registrations and registrations.group(1) == '"none"'
    assert _setting(provider, "resource_providers_to_register") is None


def test_state_account_cannot_be_destroyed(repo_root):
    account = _block(_stack(repo_root, "bootstrap")["state.tf"], 'resource "azurerm_storage_account" "state"')

    lifecycle = re.search(r"^  lifecycle \{\n(.*?)^  \}", account, re.MULTILINE | re.DOTALL)
    assert lifecycle, "the state account has no lifecycle block"
    prevent_destroy = _setting(lifecycle.group(1), "prevent_destroy")
    assert prevent_destroy and prevent_destroy.group(1) == "true"


def test_bootstrap_has_the_seven_role_assignments_all_in_roles_tf(repo_root):
    assert _declarations(_stack(repo_root, "bootstrap"), "resource", "azurerm_role_assignment") == ["roles.tf"] * 7


def test_bootstrap_has_one_federated_credential(repo_root):
    declared = _declarations(_stack(repo_root, "bootstrap"), "resource", "azurerm_federated_identity_credential")

    assert len(declared) == 1


@pytest.mark.parametrize("type_", APP_STACK_FORBIDDEN)
@pytest.mark.parametrize("kind", ["resource", "data"])
def test_app_stack_holds_none_of_bootstraps_types(repo_root, kind, type_):
    assert _declarations(_stack(repo_root, "terraform"), kind, type_) == []
