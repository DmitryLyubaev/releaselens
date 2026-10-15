"""Workload-federation identity and the explicit service/container grant matrix."""
import os
import time
from urllib.parse import urlsplit

from app.delivery_search.contracts import DeliveryRefused

ROLE_IDS = {
    "Search Service Contributor":"7ca78c08-252a-4471-8644-bb5ff32d4ba0",
    "Search Index Data Contributor":"8ebe5a00-799e-43f5-93ac-243d3dce84a7",
    "Search Index Data Reader":"1407120a-92aa-4202-b7e9-c0e197c71c8f",
    "Storage Blob Data Reader":"2a2b9908-6ea1-4ae2-8e65-a410df84e7d1",
    "Storage Blob Data Contributor":"ba92f5b4-2d11-453d-a403-e96b0029c9fe",
}


def verify_principal(account: dict, claims: dict, expected: dict) -> None:
    try:
        federation, access = claims["federation"], claims["access"]
        if (account["id"] != expected["subscription_id"] or account["tenantId"] != expected["tenant_id"]
            or account["state"] != "Enabled" or account["user"]["type"].lower() != "serviceprincipal"
            or account["user"]["name"].lower() != expected["client_id"].lower()
            or federation["iss"] != expected["issuer"] or federation["sub"] != expected["subject"]
            or federation["aud"] != "api://AzureADTokenExchange" or federation["exp"] <= time.time()+30
            or access["tid"] != expected["tenant_id"] or access["oid"] != expected["object_id"]
            or access.get("appid",access.get("azp")) != expected["client_id"]
            or access["exp"] <= time.time()+30):
            raise DeliveryRefused("identity")
    except (KeyError,TypeError,ValueError):
        raise DeliveryRefused("identity") from None


def terraform_environment(expected: dict, assertion: str, inherited: dict | None = None) -> dict:
    if not assertion:
        raise DeliveryRefused("identity")
    # Never inherit alternate ARM authentication or developer credentials into this subprocess.
    env = {k:v for k,v in (os.environ if inherited is None else inherited).items()
           if not k.startswith(("ARM_","AZURE_","TF_"))}
    env.update({"ARM_USE_OIDC":"true","ARM_USE_AZUREAD":"true","ARM_USE_CLI":"false",
        "ARM_CLIENT_ID":expected["client_id"],"ARM_TENANT_ID":expected["tenant_id"],
        "ARM_SUBSCRIPTION_ID":expected["subscription_id"],"ARM_OIDC_TOKEN":assertion,
        "TF_IN_AUTOMATION":"true","TF_INPUT":"false"})
    if expected.get("service_connection_id"):
        source = os.environ if inherited is None else inherited
        try:
            uri = urlsplit(source["SYSTEM_OIDCREQUESTURI"])
            origin = urlsplit(expected["oidc_request_origin"])
            if (uri.scheme != "https" or uri.netloc != origin.netloc or uri.username or uri.fragment
                or not uri.path.startswith(expected["oidc_path_prefix"])
                or source["AZURESUBSCRIPTION_SERVICE_CONNECTION_ID"] != expected["service_connection_id"]
                or not source["SYSTEM_ACCESSTOKEN"]):
                raise DeliveryRefused("identity")
            env.pop("ARM_OIDC_TOKEN")
            env.update({"ARM_ADO_PIPELINE_SERVICE_CONNECTION_ID":expected["service_connection_id"],
                "ARM_OIDC_AZURE_SERVICE_CONNECTION_ID":expected["service_connection_id"],
                "ARM_OIDC_REQUEST_TOKEN":source["SYSTEM_ACCESSTOKEN"],
                "ARM_OIDC_REQUEST_URL":source["SYSTEM_OIDCREQUESTURI"]})
        except (KeyError,TypeError,ValueError):
            raise DeliveryRefused("identity") from None
    return env


def role_matrix(scopes: dict) -> dict[str,list[tuple[str,str]]]:
    return {
        name:[("Search Service Contributor",scopes["service"]),
              ("Search Index Data Contributor",scopes["service"]),
              ("Storage Blob Data Reader",scopes["inputs"]),
              ("Storage Blob Data Contributor",scopes["test-state" if name == "test" else "prod-state"]),
              ("Storage Blob Data Contributor",scopes["evidence"])]
        for name in ("test","prod")
    } | {"verify":[("Search Index Data Reader",scopes["service"]),
                  ("Storage Blob Data Reader",scopes["inputs"]),
                  ("Storage Blob Data Contributor",scopes["evidence"])]}
