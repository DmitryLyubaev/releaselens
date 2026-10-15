"""Actual service principal, assertion and exact container/service scope boundaries."""
import importlib
import time

import pytest


@pytest.fixture
def identity(repo_root,monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "eval"))
    return importlib.import_module("scripts.pipelines.identity")


@pytest.fixture
def principal():
    expected = {"tenant_id":"tenant","subscription_id":"sub","client_id":"client","object_id":"object",
                "issuer":"https://issuer.example/tenant","subject":"exact-fresh-subject"}
    account = {"id":"sub","tenantId":"tenant","state":"Enabled","user":{"type":"servicePrincipal","name":"client"}}
    claims = {"federation":{"iss":expected["issuer"],"sub":expected["subject"],
              "aud":"api://AzureADTokenExchange","exp":time.time()+300},
              "access":{"tid":"tenant","oid":"object","appid":"client","exp":time.time()+300}}
    return account,claims,expected


@pytest.mark.parametrize("change",[None,"owner_email","tenant","subscription","object","issuer","subject","expired","secret"])
def test_principal_not_owner_email(identity,principal,change):
    account,claims,expected = principal
    if change == "owner_email": account["user"] = {"type":"user","name":"owner@example.com"}
    if change == "tenant": account["tenantId"] = "other"
    if change == "subscription": account["id"] = "other"
    if change == "object": claims["access"]["oid"] = "other"
    if change in ("issuer","subject"): claims["federation"]["iss" if change == "issuer" else "sub"] = "other"
    if change == "expired": claims["federation"]["exp"] = 0
    if change == "secret": claims["federation"] = {}
    if change:
        with pytest.raises(Exception,match="identity"): identity.verify_principal(account,claims,expected)
    else:
        assert identity.verify_principal(account,claims,expected) is None


def test_role_matrix(identity):
    scopes = {"service":"/s/fresh-search","inputs":"/s/fresh-store/containers/inputs",
              "test-state":"/s/fresh-store/containers/test-state","prod-state":"/s/fresh-store/containers/prod-state",
              "evidence":"/s/fresh-store/containers/evidence"}
    matrix = identity.role_matrix(scopes)
    assert matrix["verify"] == [("Search Index Data Reader","/s/fresh-search"),
        ("Storage Blob Data Reader","/s/fresh-store/containers/inputs"),
        ("Storage Blob Data Contributor","/s/fresh-store/containers/evidence")]
    assert ("Storage Blob Data Contributor","/s/fresh-store/containers/test-state") in matrix["test"]
    assert ("Storage Blob Data Contributor","/s/fresh-store/containers/prod-state") not in matrix["test"]
    assert ("Storage Blob Data Contributor","/s/fresh-store/containers/prod-state") in matrix["prod"]
    assert all("Owner" != role and "User Access Administrator" != role for grants in matrix.values() for role,_ in grants)


def test_oidc_environment_has_no_client_secret(identity,principal):
    account,claims,expected = principal
    env = identity.terraform_environment(expected,"fresh-assertion",{"PATH":"test","ARM_CLIENT_SECRET":"must-drop"})
    assert env["ARM_USE_OIDC"] == "true" and env["ARM_USE_AZUREAD"] == "true"
    assert env["ARM_OIDC_TOKEN"] == "fresh-assertion" and "ARM_CLIENT_SECRET" not in env
    assert env["ARM_CLIENT_ID"] == "client" and env["ARM_TENANT_ID"] == "tenant"


def test_ado_provider_requests_fresh_assertions(identity,principal):
    _,_,expected = principal
    expected |= {"service_connection_id":"connection","oidc_request_origin":"https://dev.azure.com",
                 "oidc_path_prefix":"/example/"}
    inherited = {"SYSTEM_ACCESSTOKEN":"synthetic-job-token", "SYSTEM_OIDCREQUESTURI":"https://dev.azure.com/example/oidctoken",
                 "AZURESUBSCRIPTION_SERVICE_CONNECTION_ID":"connection","PATH":"test"}
    env = identity.terraform_environment(expected,"initial-assertion",inherited)
    assert "ARM_OIDC_TOKEN" not in env
    assert env["ARM_OIDC_REQUEST_TOKEN"] == "synthetic-job-token"
    assert env["ARM_OIDC_REQUEST_URL"] == "https://dev.azure.com/example/oidctoken"
    assert env["ARM_ADO_PIPELINE_SERVICE_CONNECTION_ID"] == "connection"
    inherited["SYSTEM_OIDCREQUESTURI"] = "https://evil.example/oidctoken"
    with pytest.raises(Exception,match="identity"): identity.terraform_environment(expected,"initial-assertion",inherited)


def test_terraform_never_inherits_debug_logging(identity,principal):
    env = identity.terraform_environment(principal[2],"assertion",{
        "TF_LOG":"TRACE","TF_LOG_PATH":"public.log","TF_LOG_PROVIDER":"TRACE","PATH":"safe"})
    assert not any(key.startswith("TF_LOG") for key in env)
