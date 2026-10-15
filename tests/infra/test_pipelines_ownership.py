"""Computed IDs fill immutable approved slots; they cannot enlarge cleanup ownership."""
import copy
import hashlib
import importlib
import json

import pytest


@pytest.fixture
def ownership(repo_root,monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root/"eval"))
    return importlib.import_module("scripts.pipelines.ownership")


@pytest.fixture
def binding_case(tmp_path):
    scope="/subscriptions/s/resourceGroups/rg-p8-1016x-search/providers/Microsoft.Search/searchServices/owned"
    role="00000000-0000-0000-0000-000000000001"
    principal="00000000-0000-0000-0000-000000000002"
    address="azurerm_role_assignment.delivery"
    plan=tmp_path/"plans"; plan.mkdir()
    binary=plan/"foundation.tfplan"; binary.write_bytes(b"approved saved plan")
    shown=plan/"foundation.json"
    shown.write_text(json.dumps({"resource_changes":[{"address":address,"mode":"managed","change":{"actions":["create"]}}]}))
    component={"name":"foundation","directory":"tf/foundation","resources":[{"address":address,"id":None,"kind":"arm",
        "resolve":{"kind":"role-assignment","scope":scope,"role_definition_id":role,"principal_id":principal}}],
        "create_plan":{"kind":"owner","binary":"plans/foundation.tfplan","binary_sha256":hashlib.sha256(binary.read_bytes()).hexdigest(),
            "shown":"plans/foundation.json","shown_sha256":hashlib.sha256(shown.read_bytes()).hexdigest()}}
    manifest={"components":[component]}
    data=json.dumps(manifest).encode(); (tmp_path/"cleanup-manifest.json").write_bytes(data)
    raw={"session":"p8-1016x","source_sha":"a"*40,"tree_sha":"b"*40,"release_digest":"c"*64,
        "manifest_sha256":hashlib.sha256(data).hexdigest()}
    intents=tmp_path/"apply-intents"; intents.mkdir()
    (intents/"foundation.json").write_text(json.dumps({"session":raw["session"],"source_sha":raw["source_sha"],
        "component":"foundation","manifest_sha256":raw["manifest_sha256"],"plan_sha256":component["create_plan"]["binary_sha256"]}))
    resource={"address":address,"mode":"managed","type":"azurerm_role_assignment","values":{
        "id":scope+"/providers/Microsoft.Authorization/roleAssignments/11111111-1111-1111-1111-111111111111",
        "scope":scope,"role_definition_id":"/subscriptions/s/providers/Microsoft.Authorization/roleDefinitions/"+role,
        "principal_id":principal}}
    state={"values":{"root_module":{"resources":[resource]}}}
    return tmp_path,raw,manifest,component,state


def test_computed_id_binding_keeps_manifest_bytes_and_original_identity(ownership,binding_case):
    directory,raw,manifest,component,state=binding_case
    original=(directory/"cleanup-manifest.json").read_bytes()
    ownership.bind_state(directory,raw,component,state)
    expected=state["values"]["root_module"]["resources"][0]["values"]["id"]
    assert component["resources"][0]["id"]==expected
    assert (directory/"cleanup-manifest.json").read_bytes()==original
    resolved=ownership.resolve_existing(directory,raw,manifest)
    assert resolved["components"][0]["resources"][0]["id"]==expected
    changed=copy.deepcopy(state); changed["values"]["root_module"]["resources"][0]["values"]["id"]=expected[:-1]+"2"
    with pytest.raises(Exception): ownership.bind_state(directory,raw,component,changed)


@pytest.mark.parametrize("change",["extra-address","principal","scope","plan","no-intent"])
def test_binding_refuses_unapproved_resolution(ownership,binding_case,change):
    directory,raw,manifest,component,state=binding_case
    row=state["values"]["root_module"]["resources"][0]
    if change=="extra-address": state["values"]["root_module"]["resources"].append(dict(row,address="azurerm_role_assignment.extra"))
    if change=="principal": row["values"]["principal_id"]="00000000-0000-0000-0000-000000000003"
    if change=="scope": row["values"]["scope"]="unowned"
    if change=="plan": (directory/"plans/foundation.tfplan").write_bytes(b"different plan")
    if change=="no-intent": (directory/"apply-intents/foundation.json").unlink()
    with pytest.raises(Exception): ownership.bind_state(directory,raw,component,state)


def test_owner_state_resolves_only_approved_slots_before_delete(ownership,binding_case):
    owner=importlib.import_module("scripts.pipelines.owner")
    directory,raw,manifest,component,state=binding_case
    driver=object.__new__(owner.OwnerDriver); driver.directory=directory; driver.a=raw
    driver.component_dir=lambda component:directory
    address=component["resources"][0]["address"]
    driver.process=lambda args,cwd:json.dumps(state) if "show" in args else address+"\n"
    rows=driver.state(component)
    assert component["resources"][0]["id"]==rows[0]["id"]


def test_computed_slot_policy_is_narrow_and_create_plan_bound(ownership,binding_case):
    directory,raw,manifest,component,state=binding_case
    raw["owned_group_ids"]=[state["values"]["root_module"]["resources"][0]["values"]["scope"].split("/providers/")[0]]
    ownership.validate_policy(component,component["resources"][0],raw,directory)
    for changed in ({"kind":"arbitrary"},{"kind":"role-assignment","scope":"/unowned"}):
        with pytest.raises(Exception): ownership.validate_policy(component,dict(component["resources"][0],resolve=changed),raw,directory)
    with pytest.raises(Exception): ownership.validate_policy(dict(component,create_plan={"kind":"anything"}),component["resources"][0],raw,directory)


def test_index_computed_id_cannot_redirect_cleanup(ownership):
    spec={"address":"azapi_data_plane_resource.index","id":None,"kind":"index","host":"owned.search.windows.net",
        "name":"p8-test","resolve":{"kind":"index"}}
    row={"type":"azapi_data_plane_resource","values":{"id":"evil.search.windows.net/indexes('p8-test')",
        "parent_id":spec["host"],"name":spec["name"],"type":"Microsoft.Search/searchServices/indexes@2024-07-01"}}
    with pytest.raises(Exception): ownership.validate_slot(spec,row,{}, {})
