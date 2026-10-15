"""A reviewed create-only Terraform plan cannot acquire unexpected resources."""
import importlib

import pytest


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
