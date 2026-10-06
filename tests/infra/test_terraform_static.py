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


# The app stack runs as CI, which may not register resource providers. The search and gateway
# stacks run as the owner, who may, but bootstrap is the one place registration happens.
@pytest.mark.parametrize("stack", ["terraform", "search", "gateway"])
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


def test_gateway_stack_has_no_role_assignments(repo_root):
    # Role assignments live in infra/bootstrap/roles.tf only: the gateway identity's roles on the
    # models and the Gateway.Invoke assignments are all made there.
    files = _stack(repo_root, "gateway")

    for type_ in ["azurerm_role_assignment", "azuread_app_role_assignment"]:
        assert _declarations(files, "resource", type_) == [], type_
    # Nor through azapi.
    for name, text in files.items():
        assert not [line for line in _code_lines(text) if "Microsoft.Authorization/roleAssignments" in line], name


def test_gateway_stack_keeps_keys_off(repo_root):
    files = _stack(repo_root, "gateway")

    # No product and no subscription, so no subscription key exists to be leaked or required.
    keyed = re.compile(r'^resource "azurerm_api_management_(product|subscription)', re.MULTILINE)
    for name, text in files.items():
        assert not keyed.search(text), name
    # Nor through azapi, and no line of code may carry a key out.
    keys = re.compile(r"subscription_key|primary_key|secondary_key|/products|/subscriptions@")
    for name, text in files.items():
        assert not [line for line in _code_lines(text) if keys.search(line)], name
    # Every revision is called with an Entra token alone.
    for header in [
        'resource "azurerm_api_management_api" "v1"',
        'resource "azurerm_api_management_api" "v1_rev2"',
    ]:
        required = _setting(_block(files["api.tf"], header), "subscription_required")
        assert required and required.group(1) == "false", header


def test_gateway_stack_is_not_in_the_app_group(repo_root):
    # In rg-releaselens, the nightly destroy's empty-group check would find the service and fail.
    app_group = re.compile(r"rg-releaselens(?![-\w])")
    for name, text in _stack(repo_root, "gateway").items():
        assert not [line for line in _code_lines(text) if app_group.search(line)], name

    group = _block(_stack(repo_root, "gateway")["main.tf"], 'resource "azurerm_resource_group" "gateway"')
    group_name = _setting(group, "name")
    assert group_name and group_name.group(1) == '"rg-releaselens-gateway"'


def test_gateway_stack_purges_and_never_recovers(repo_root):
    files = _stack(repo_root, "gateway")
    provider = _block(files["versions.tf"], 'provider "azurerm"')

    # A destroy purges the soft-deleted service, and a create never restores an old one.
    features = re.search(r"^  features \{\n(.*?)^  \}", provider, re.MULTILINE | re.DOTALL)
    assert features, "the azurerm provider has no features block"
    api_management = re.search(r"^    api_management \{\n(.*?)^    \}", features.group(1), re.MULTILINE | re.DOTALL)
    assert api_management, "the features block has no api_management block"
    purge = _setting(api_management.group(1), "purge_soft_delete_on_destroy")
    assert purge and purge.group(1) == "true"
    recover = _setting(api_management.group(1), "recover_soft_deleted")
    assert recover and recover.group(1) == "false"
    # The stack is meant to be destroyed at the end of every session.
    for name, text in files.items():
        assert not [line for line in _code_lines(text) if "prevent_destroy" in line], name


def test_gateway_policies_are_pinned_to_their_revisions(repo_root):
    # azurerm 5.7's api policy resource reads and deletes by the API name with ";rev=n" stripped,
    # so on a revision it deletes the current revision's policy; the stack uses azapi instead.
    files = _stack(repo_root, "gateway")

    assert _declarations(files, "resource", "azurerm_api_management_api_policy") == []
    for name, parent in [
        ("policy_v1", "azurerm_api_management_api.v1.id"),
        ("policy_v1_rev2", "azurerm_api_management_api.v1_rev2.id"),
    ]:
        policy = _block(files["api.tf"], f'resource "azapi_resource" "{name}"')
        parent_id = _setting(policy, "parent_id")
        assert parent_id and parent_id.group(1) == parent, name


def test_gateway_revision_2_is_copied_before_any_policy(repo_root):
    # A revision is a copy of its source when it is created: revision 2 must be copied after the
    # operation, the diagnostic and its metrics switch exist, and before either policy does.
    api = _stack(repo_root, "gateway")["api.tf"]

    def depends_on(header: str) -> str:
        found = re.search(r"^  depends_on = \[(.*?)\]", _block(api, header), re.MULTILINE | re.DOTALL)
        assert found, f"{header} has no depends_on"
        return found.group(1)

    revision_2 = depends_on('resource "azurerm_api_management_api" "v1_rev2"')
    for needed in [
        "azurerm_api_management_api_operation.chat_completions",
        "azurerm_api_management_api_diagnostic.appi",
        "azapi_update_resource.diagnostic_metrics",
    ]:
        assert needed in revision_2, needed
    assert "azurerm_api_management_api.v1_rev2" in depends_on('resource "azapi_resource" "policy_v1"')
    assert "azapi_resource.policy_v1_rev2" in depends_on('resource "azurerm_api_management_api_release" "revision_2"')


def test_gateway_policies_are_saved_after_what_they_refer_to(repo_root):
    # API Management checks a policy's {{name}} references and its backend-id when the policy is
    # saved, so the order must not rest on timing.
    files = _stack(repo_root, "gateway")

    for name in ["policy_v1", "policy_v1_rev2"]:
        found = re.search(
            r"^  depends_on = \[(.*?)\]",
            _block(files["api.tf"], f'resource "azapi_resource" "{name}"'),
            re.MULTILINE | re.DOTALL,
        )
        assert found, f"{name} has no depends_on"
        for needed in ["azurerm_api_management_named_value.this", "azapi_resource.pool"]:
            assert needed in found.group(1), (name, needed)
    # The pool, in turn, refers to both backends, so they exist before it.
    pool = _block(files["backends.tf"], 'resource "azapi_resource" "pool"')
    for backend in ["aoai-primary", "aoai-secondary"]:
        assert f'azapi_resource.backend["{backend}"].id' in pool, backend
