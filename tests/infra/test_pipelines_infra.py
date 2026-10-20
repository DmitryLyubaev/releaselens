"""A reviewed create-only Terraform plan cannot acquire unexpected resources, and the stacks declare only theirs."""
import importlib
import re

import pytest

DECLARED = re.compile(r'^(resource|data) "([^"]+)" "([^"]+)"',re.M)
PLATFORM = ["azurerm_federated_identity_credential.delivery","azurerm_resource_group.platform","azurerm_resource_group.search",
    "azurerm_role_assignment.delivery","azurerm_role_assignment.owner_storage","azurerm_storage_account.platform",
    "azurerm_storage_container.private","azurerm_user_assigned_identity.delivery","random_string.suffix"]


def declared(stack):
    return sorted(("data." if kind == "data" else "")+rtype+"."+name
        for path in sorted(stack.glob("*.tf")) for kind,rtype,name in DECLARED.findall(path.read_text()))


@pytest.fixture
def plans(repo_root,monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "eval"))
    return importlib.import_module("scripts.pipelines.plans")


def test_schema_target_contract(plans):
    plan = {"resource_changes":[{"address":"azapi_data_plane_resource.index","mode":"managed",
        "type":"azapi_data_plane_resource","change":{"actions":["create"],"after":{
        "type":"Microsoft.Search/searchServices/indexes@2024-07-01","parent_id":"owned.search.windows.net",
        "name":"p8-test"}}}]}
    plans.validate_index_plan(plan,"owned.search.windows.net","test")
    plan["resource_changes"][0]["change"]["after"]["name"] = "p8-demo-prod"
    with pytest.raises(Exception): plans.validate_index_plan(plan,"owned.search.windows.net","test")


@pytest.mark.parametrize("bad",["update","delete","unknown_resource","bootstrap"])
def test_no_bootstrap_reads(plans,bad):
    row = {"address":"azapi_data_plane_resource.index","mode":"managed","type":"azapi_data_plane_resource",
           "change":{"actions":["create"],"after":{"type":"Microsoft.Search/searchServices/indexes@2024-07-01",
           "parent_id":"owned.search.windows.net","name":"p8-test"}}}
    plan = {"resource_changes":[row]}
    if bad in ("update","delete"): row["change"]["actions"] = [bad]
    if bad == "unknown_resource": plan["resource_changes"].append(row | {"address":"azurerm_resource_group.other"})
    if bad == "bootstrap": plan["resource_changes"].append({"mode":"data","type":"terraform_remote_state","address":"data.terraform_remote_state.bootstrap","change":{"actions":["read"]}})
    with pytest.raises(Exception): plans.validate_index_plan(plan,"owned.search.windows.net","test")


def test_copied_stacks_hold_every_reviewed_file(repo_root,plans):
    run = importlib.import_module("scripts.pipelines.run")
    for stack in ("service","index"):
        assert all((repo_root/"infra/pipelines"/stack/name).is_file() for name in run.STACK_FILES)


def test_service_stack_is_one_search_service(repo_root,plans):
    service = importlib.import_module("scripts.pipelines.service")
    storage = importlib.import_module("scripts.pipelines.storage")
    root = repo_root/"infra/pipelines/service"
    # No role assignment, no storage: the run's only resource is the service its plan check expects.
    assert declared(root) == [service.SERVICE_ADDRESS]
    backend = (root/"backend.tf").read_text()
    assert f'key              = "{storage.STATES["service"][1]}"' in backend and "use_oidc         = true" in backend
    assert 'output "search_host"' in (root/"outputs.tf").read_text()


def test_platform_stack_holds_no_search_service(repo_root):
    root = repo_root/"infra/pipelines/platform"
    # Every role assignment is one the contract test enumerates; the search service is per run.
    assert declared(root) == PLATFORM
    assert 'key              = "p8-platform.tfstate"' in (root/"backend.tf").read_text()
