"""Static guards over the Terraform stacks' source.

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
    "Microsoft.Search",
    "Microsoft.ApiManagement",
    "Microsoft.OperationalInsights",
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


def _code_lines(text: str) -> list[str]:
    """Every line that is not a comment, so a comment that names something does not count."""
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def _declarations(files: dict[str, str], kind: str, type_: str) -> list[str]:
    """The file name of each `<kind> "<type_>"` declaration, once per declaration."""
    pattern = re.compile(rf'^{kind} "{re.escape(type_)}" "', re.MULTILINE)
    return [name for name, text in files.items() for _ in pattern.finditer(text)]


def test_bootstrap_registers_exactly_the_ten_providers(repo_root):
    provider = _block(_stack(repo_root, "bootstrap")["versions.tf"], 'provider "azurerm"')

    registrations = _setting(provider, "resource_provider_registrations")
    assert registrations and registrations.group(1) == '"none"'
    listed = re.search(r"^\s*resource_providers_to_register\s*=\s*\[(.*?)\]", provider, re.MULTILINE | re.DOTALL)
    assert listed, "resource_providers_to_register is not set"
    lines = [line.strip() for line in listed.group(1).splitlines()]
    entries = [line.rstrip(",") for line in lines if line and not line.startswith("#")]
    # Sorted, not a set, so a duplicate fails too.
    assert sorted(entries) == sorted(f'"{name}"' for name in BOOTSTRAP_PROVIDERS)


# The app stack runs as CI, which may not register resource providers. The search stack runs as
# the owner, who may, but bootstrap is the one place registration happens.
@pytest.mark.parametrize("stack", ["terraform", "search"])
def test_stack_registers_no_providers(repo_root, stack):
    provider = _block(_stack(repo_root, stack)["versions.tf"], 'provider "azurerm"')

    registrations = _setting(provider, "resource_provider_registrations")
    assert registrations and registrations.group(1) == '"none"'
    assert _setting(provider, "resource_providers_to_register") is None


def test_state_account_cannot_be_destroyed(repo_root):
    account = _block(_stack(repo_root, "bootstrap")["state.tf"], 'resource "azurerm_storage_account" "state"')

    lifecycle = re.search(r"^  lifecycle \{\n(.*?)^  \}", account, re.MULTILINE | re.DOTALL)
    assert lifecycle, "the state account has no lifecycle block"
    prevent_destroy = _setting(lifecycle.group(1), "prevent_destroy")
    assert prevent_destroy and prevent_destroy.group(1) == "true"


def test_bootstrap_has_the_twelve_role_assignments_all_in_roles_tf(repo_root):
    assert _declarations(_stack(repo_root, "bootstrap"), "resource", "azurerm_role_assignment") == ["roles.tf"] * 12


def test_bootstrap_has_the_three_gateway_invoke_assignments_all_in_roles_tf(repo_root):
    files = _stack(repo_root, "bootstrap")

    assert _declarations(files, "resource", "azuread_app_role_assignment") == ["roles.tf"] * 3
    # The one role, on the gateway's own service principal, for the three principals.
    for name in ["gateway_invoke_owner", "gateway_invoke_app", "gateway_invoke_deploy"]:
        assignment = _block(files["roles.tf"], f'resource "azuread_app_role_assignment" "{name}"')
        resource = _setting(assignment, "resource_object_id")
        assert resource and resource.group(1) == "azuread_service_principal.gateway.object_id"
        role = _setting(assignment, "app_role_id")
        assert role and role.group(1) == "random_uuid.gateway_invoke_role.result"


def test_bootstrap_has_no_app_secret(repo_root):
    files = _stack(repo_root, "bootstrap")

    # No secret and no certificate of any kind, on the application or on its service principal.
    for type_ in [
        "azuread_application_password",
        "azuread_application_certificate",
        "azuread_application_federated_identity_credential",
        "azuread_service_principal_password",
        "azuread_service_principal_certificate",
    ]:
        assert _declarations(files, "resource", type_) == [], type_
    application = _block(files["gateway_app.tf"], 'resource "azuread_application" "gateway"')
    assert not re.search(r"^\s*password\s*\{", application, re.MULTILINE)
    # No line of code may carry a secret out of either provider.
    secrets = re.compile(r"client_secret|password|certificate")
    for name, text in files.items():
        assert not [line for line in _code_lines(text) if secrets.search(line)], name


def test_bootstrap_has_no_literal_client_id_in_the_gateway_app(repo_root):
    files = _stack(repo_root, "bootstrap")
    guid = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

    # The Azure CLI's ID comes from the published-app-IDs data source, and the role's and the
    # scope's IDs are generated, so no GUID appears in the stack's code.
    for name in ["gateway_app.tf", "monitoring.tf", "roles.tf"]:
        assert not [line for line in _code_lines(files[name]) if guid.search(line)], name


def test_gateway_monitoring_keeps_local_authentication_off(repo_root):
    files = _stack(repo_root, "bootstrap")

    for header in [
        'resource "azurerm_log_analytics_workspace" "gateway"',
        'resource "azurerm_application_insights" "gateway"',
    ]:
        local_auth = _setting(_block(files["monitoring.tf"], header), "local_authentication_enabled")
        assert local_auth and local_auth.group(1) == "false", header


def test_failover_account_keeps_keys_off(repo_root):
    account = _block(_stack(repo_root, "bootstrap")["failover.tf"], 'resource "azurerm_cognitive_account" "failover"')

    local_auth = _setting(account, "local_auth_enabled")
    assert local_auth and local_auth.group(1) == "false"
    # The account is AIServices like the first one, and no line of code may carry a key out.
    kind = _setting(account, "kind")
    assert kind and kind.group(1) == '"AIServices"'
    keys = re.compile(r"primary_access_key|secondary_access_key|api_key")
    for name, text in _stack(repo_root, "bootstrap").items():
        assert not [line for line in _code_lines(text) if keys.search(line)], name


def test_bootstrap_has_one_federated_credential(repo_root):
    declared = _declarations(_stack(repo_root, "bootstrap"), "resource", "azurerm_federated_identity_credential")

    assert len(declared) == 1


@pytest.mark.parametrize("type_", APP_STACK_FORBIDDEN)
@pytest.mark.parametrize("kind", ["resource", "data"])
def test_app_stack_holds_none_of_bootstraps_types(repo_root, kind, type_):
    assert _declarations(_stack(repo_root, "terraform"), kind, type_) == []


def test_search_stack_keeps_keys_off(repo_root):
    files = _stack(repo_root, "search")
    service = _block(files["main.tf"], 'resource "azurerm_search_service" "search"')

    local_auth = _setting(service, "local_authentication_enabled")
    assert local_auth and local_auth.group(1) == "false"
    # No output, and no other line of code, may carry the service's admin or query keys out.
    keys = re.compile(r"api_key|primary_key|secondary_key|query_keys")
    for name, text in files.items():
        assert not [line for line in _code_lines(text) if keys.search(line)], name


def test_search_stack_has_only_the_owners_two_role_assignments(repo_root):
    assert len(_declarations(_stack(repo_root, "search"), "resource", "azurerm_role_assignment")) == 2


def test_search_stack_is_not_in_the_app_group(repo_root):
    # In rg-releaselens, the nightly destroy's empty-group check would find the service and fail.
    app_group = re.compile(r"rg-releaselens(?![-\w])")
    for name, text in _stack(repo_root, "search").items():
        assert not [line for line in _code_lines(text) if app_group.search(line)], name

    group = _block(_stack(repo_root, "search")["main.tf"], 'resource "azurerm_resource_group" "search"')
    group_name = _setting(group, "name")
    assert group_name and group_name.group(1) == '"rg-releaselens-search"'
