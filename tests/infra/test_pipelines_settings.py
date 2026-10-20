"""Delivery settings from the private variable group, and the stage-to-role mapping."""
import importlib
import json

import pytest

PRINCIPAL = {"client_id":"00000000-0000-0000-0000-000000000004","object_id":"00000000-0000-0000-0000-000000000005",
             "issuer":"https://<ado-issuer>/00000000-0000-0000-0000-000000000003",
             "subject":"sc://example/ReleaseLens/ReleaseLens-P8-Test",
             "service_connection_id":"00000000-0000-0000-0000-000000000006",
             "oidc_request_origin":"https://example.vstoken.visualstudio.com","oidc_path_prefix":"/"}
ROLES = ("test","prod","verify")
VALID = {"P8_TENANT_ID":"00000000-0000-0000-0000-000000000001","P8_SUBSCRIPTION_ID":"00000000-0000-0000-0000-000000000002",
         "P8_STORAGE_ACCOUNT":"p8a1b2c3","P8_SEARCH_GROUP":"rg-p8-search","P8_SEARCH_SERVICE":"p8-search-a1b2c3",
         "P8_RELEASE_DIGEST":"0"*64,"P8_PRINCIPALS_JSON":json.dumps({r:PRINCIPAL for r in ROLES})}


@pytest.fixture
def mod(repo_root,monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "eval"))
    return importlib.import_module("scripts.pipelines.settings")


@pytest.fixture
def refused(repo_root,monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "eval"))
    return importlib.import_module("app.delivery_search.contracts").DeliveryRefused


def test_load_settings_reads_every_field_and_derives_hosts(mod):
    s = mod.load_settings(VALID)
    assert s.storage_host == "p8a1b2c3.blob.core.windows.net" and s.search_host == "p8-search-a1b2c3.search.windows.net"
    assert (s.tenant_id,s.subscription_id) == (VALID["P8_TENANT_ID"],VALID["P8_SUBSCRIPTION_ID"])
    assert (s.storage_account_name,s.search_group,s.search_service_name,s.release_digest) == (
        "p8a1b2c3","rg-p8-search","p8-search-a1b2c3","0"*64)
    assert tuple(s.principals) == ROLES == mod.ROLES


@pytest.mark.parametrize("role",ROLES)
def test_every_principal_has_every_key_identity_reads(mod,role):
    p = mod.load_settings(VALID).principals[role]
    assert p == PRINCIPAL | {"tenant_id":VALID["P8_TENANT_ID"],"subscription_id":VALID["P8_SUBSCRIPTION_ID"]}


def test_principal_tenant_and_subscription_come_from_the_top_level_not_the_json(mod):
    forged = {r:PRINCIPAL | {"tenant_id":"other","subscription_id":"other"} for r in ROLES}
    p = mod.load_settings(VALID | {"P8_PRINCIPALS_JSON":json.dumps(forged)}).principals["test"]
    assert (p["tenant_id"],p["subscription_id"]) == (VALID["P8_TENANT_ID"],VALID["P8_SUBSCRIPTION_ID"])


def test_settings_are_frozen(mod):
    with pytest.raises(Exception): mod.load_settings(VALID).tenant_id = "x"


@pytest.mark.parametrize("key,value",[("P8_TENANT_ID","not-a-guid"),("P8_SUBSCRIPTION_ID","0"*36),
    ("P8_TENANT_ID","00000000-0000-0000-0000-00000000000A"),("P8_STORAGE_ACCOUNT","Has_Upper"),
    ("P8_STORAGE_ACCOUNT","ab"),("P8_SEARCH_SERVICE","x"),("P8_SEARCH_SERVICE","-bad-start"),
    ("P8_SEARCH_GROUP","search"),("P8_SEARCH_GROUP","rg-"),("P8_RELEASE_DIGEST","abc"),("P8_RELEASE_DIGEST","G"*64),
    ("P8_TENANT_ID",""),("P8_PRINCIPALS_JSON",json.dumps({"test":{}})),("P8_PRINCIPALS_JSON","not json"),
    ("P8_PRINCIPALS_JSON","[]")])
def test_load_settings_refuses_malformed(mod,refused,key,value):
    with pytest.raises(refused) as e: mod.load_settings(VALID | {key:value})
    assert e.value.code == "settings"


@pytest.mark.parametrize("key",sorted(VALID))
def test_load_settings_refuses_missing_key(mod,refused,key):
    env = {k:v for k,v in VALID.items() if k != key}
    with pytest.raises(refused) as e: mod.load_settings(env)
    assert e.value.code == "settings"


def test_load_settings_refuses_missing_role(mod,refused):
    with pytest.raises(refused): mod.load_settings(VALID | {"P8_PRINCIPALS_JSON":json.dumps({"test":PRINCIPAL})})


@pytest.mark.parametrize("field",sorted(PRINCIPAL))
@pytest.mark.parametrize("bad",["missing","empty","not-a-string"])
def test_load_settings_refuses_incomplete_principal(mod,refused,field,bad):
    p = dict(PRINCIPAL)
    if bad == "missing": del p[field]
    else: p[field] = "" if bad == "empty" else 7
    with pytest.raises(refused): mod.load_settings(VALID | {"P8_PRINCIPALS_JSON":json.dumps({r:p for r in ROLES})})


def test_refusal_never_echoes_the_value(mod,refused):
    with pytest.raises(refused) as e: mod.load_settings(VALID | {"P8_TENANT_ID":"secret-looking-value"})
    assert "secret-looking-value" not in str(e.value) and e.value.__cause__ is None


@pytest.mark.parametrize("stage,reason,role",[("build","Manual","test"),("create-service","Manual","test"),
    ("plan-test","Manual","test"),("test","Manual","test"),
    ("plan-prod","Manual","prod"),("prod","Manual","prod"),("negative","Manual","test"),
    ("promote-negative","Manual","prod"),
    ("status","Schedule","verify"),("verify","Schedule","verify"),("teardown-prod","Schedule","prod"),
    ("teardown-test","Schedule","test"),("teardown-prod","Manual","prod")])
def test_role_for(mod,stage,reason,role):
    assert mod.role_for(stage,reason) == role


@pytest.mark.parametrize("stage",["verify","status"])
def test_role_for_refuses_verify_outside_schedule(mod,refused,stage):
    with pytest.raises(refused) as e: mod.role_for(stage,"Manual")
    assert e.value.code == "settings"


def test_role_for_refuses_unknown_stage(mod,refused):
    with pytest.raises(refused) as e: mod.role_for("deploy","Manual")
    assert e.value.code == "settings"
