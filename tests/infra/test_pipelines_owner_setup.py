"""The owner's setup helpers: the delivery variable group's values, written only where git ignores them and
never printed, and the uploaded archive's read-back check."""
import hashlib
import importlib
import json
import re

import pytest

ROLES = ("test","prod","verify")
OUTPUTS = {"tenant_id":"00000000-0000-0000-0000-000000000001","subscription_id":"00000000-0000-0000-0000-000000000002",
    "storage_account_name":"p8a1b2c3","search_group":"rg-p8-search","search_service_name":"p8-search-a1b2c3",
    "principals":{r:{"client_id":f"00000000-0000-0000-0000-00000000002{n}","object_id":f"00000000-0000-0000-0000-00000000001{n}"}
                  for n,r in enumerate(ROLES)}}
CONNECTIONS = {"oidc_path_prefix":"/example/00000000-0000-0000-0000-000000000009/_apis/distributedtask/hubs/build/"} | {
    r:{"service_connection_id":f"00000000-0000-0000-0000-00000000003{n}","issuer":"https://issuer.example/current",
       "subject":f"sc://example/project/ReleaseLens-P8-{r.title()}"} for n,r in enumerate(ROLES)}
DIGEST = "a"*64


@pytest.fixture
def mod(repo_root,monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root/"eval"))
    return importlib.import_module("scripts.pipelines.owner_setup")


@pytest.fixture
def inputs(tmp_path):
    # `terraform output -json` wraps each output; the helper reads its value.
    outputs = tmp_path/"platform-outputs.json"
    outputs.write_text(json.dumps({k:{"sensitive":True,"type":"string","value":v} for k,v in OUTPUTS.items()}))
    connections = tmp_path/"connections.json"
    connections.write_text(json.dumps(CONNECTIONS))
    return outputs,connections


def args(inputs,out,digest=DIGEST):
    return ["variables","--platform-outputs",str(inputs[0]),"--connections",str(inputs[1]),"--release-digest",digest,"--out",str(out)]


def test_variables_have_exactly_the_settings_keys(mod,inputs,tmp_path,repo_root):
    out = tmp_path/"private"/"variables.json"
    assert mod.main(args(inputs,out)) == 0
    written = json.loads(out.read_text())
    # The keys every pipeline stage reads from the variable group, and nothing else.
    stage_keys = set(re.findall(r"^\s+(P8_[A-Z_]+):",(repo_root/"pipelines/templates/stage-steps.yml").read_text(),re.M))
    assert set(written) == stage_keys == {"P8_TENANT_ID","P8_SUBSCRIPTION_ID","P8_STORAGE_ACCOUNT","P8_SEARCH_GROUP",
        "P8_SEARCH_SERVICE","P8_RELEASE_DIGEST","P8_PRINCIPALS_JSON"}
    assert all(isinstance(v,str) and "\n" not in v and " " not in v for v in written.values())
    settings = importlib.import_module("scripts.pipelines.settings").load_settings(written)
    assert settings.search_service_name == "p8-search-a1b2c3" and settings.release_digest == DIGEST
    test = settings.principals["test"]
    assert test["client_id"] == OUTPUTS["principals"]["test"]["client_id"] and test["oidc_request_origin"] == "https://dev.azure.com"
    assert test["subject"] == CONNECTIONS["test"]["subject"] and test["oidc_path_prefix"] == CONNECTIONS["oidc_path_prefix"]


def test_variables_output_refuses_tracked_path(mod,inputs,repo_root,capsys):
    tracked = repo_root/"scripts"/"pipelines"/"p8-variables.json"
    assert mod.main(args(inputs,tracked)) == 2
    assert not tracked.exists()
    assert capsys.readouterr().out.strip() == "refused: target"
    # An ignored file in a tracked directory is still refused; an ignored directory is not.
    assert not mod.allowed_output(repo_root/"infra"/"pipelines"/"platform"/"terraform.tfvars.json")
    assert not mod.allowed_output(repo_root/"p8-variables.json")
    assert mod.allowed_output(repo_root/".local"/"project8"/"setup"/"variables.json")


def test_variables_refuses_bad_inputs_and_an_existing_file(mod,inputs,tmp_path,capsys):
    assert mod.main(args(inputs,tmp_path/"a.json",digest="abc")) == 2
    duplicate = json.loads(inputs[1].read_text())
    duplicate["prod"]["service_connection_id"] = duplicate["test"]["service_connection_id"]
    inputs[1].write_text(json.dumps(duplicate))
    assert mod.main(args(inputs,tmp_path/"b.json")) == 2
    assert not (tmp_path/"a.json").exists() and not (tmp_path/"b.json").exists()
    existing = tmp_path/"c.json"
    existing.write_text("{}")
    inputs[1].write_text(json.dumps(CONNECTIONS))
    assert mod.main(args(inputs,existing)) == 2
    assert existing.read_text() == "{}"
    assert capsys.readouterr().out.split() == ["refused:","settings","refused:","settings","refused:","reserved"]


def test_variables_prints_no_values(mod,inputs,tmp_path,capsys):
    assert mod.main(args(inputs,tmp_path/"variables.json")) == 0
    printed = capsys.readouterr()
    values = [v for v in OUTPUTS.values() if isinstance(v,str)]+[DIGEST]
    values += [v for p in OUTPUTS["principals"].values() for v in p.values()]
    values += [v for c in CONNECTIONS.values() if isinstance(c,dict) for v in c.values()]+[CONNECTIONS["oidc_path_prefix"]]
    assert not any(v in printed.out or v in printed.err for v in values)
    assert re.search(r"\bwritten\b",printed.out) and "P8_PRINCIPALS_JSON" in printed.out
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}",printed.out)


def test_verify_upload_match_and_mismatch(mod,tmp_path,capsys):
    archive = tmp_path/"readback.tar.gz"
    archive.write_bytes(b"frozen release bytes")
    local = hashlib.sha256(b"frozen release bytes").hexdigest()
    assert mod.main(["verify-upload","--archive",str(archive),"--digest",local]) == 0
    # Get-FileHash prints upper case; the same digest still matches.
    assert mod.main(["verify-upload","--archive",str(archive),"--digest",local.upper()]) == 0
    assert mod.main(["verify-upload","--archive",str(archive),"--digest","0"*64]) == 1
    assert mod.main(["verify-upload","--archive",str(tmp_path/"missing.tar.gz"),"--digest",local]) == 1
    assert capsys.readouterr().out.split() == ["match","match","mismatch","mismatch"]
