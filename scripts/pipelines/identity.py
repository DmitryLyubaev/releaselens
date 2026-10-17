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


IDENTITY_CHECKS = frozenset({
    "unclassified", "principal-fields", "account-subscription", "account-tenant",
    "account-state", "account-type", "account-client", "federation-issuer",
    "federation-subject", "federation-audience", "federation-expiry", "access-tenant",
    "access-object", "access-client", "access-expiry", "assertion-missing",
    "jwt-format", "access-format", "secret-present", "cli-account", "cli-access-token",
    "refresh-fields", "refresh-endpoint", "refresh-connection", "refresh-job-token",
    "refresh-request", "refresh-result",
})


class IdentityRefused(DeliveryRefused):
    """A fixed check identifier, never a claim, URL, token or remote error."""
    def __init__(self, check):
        super().__init__("identity")
        self.check = check if isinstance(check, str) and check in IDENTITY_CHECKS else "unclassified"


def _require(condition, check):
    if not condition:
        raise IdentityRefused(check)


def federation_audience(expected: dict) -> str:
    # Entra v2 emits the token-exchange application ID, while the FIC keeps its URI.
    if expected["issuer"] == f'https://login.microsoftonline.com/{expected["tenant_id"]}/v2.0':
        return "fb60f99c-7a34-4190-8149-302f77469936"
    return "api://AzureADTokenExchange"


def verify_principal(account: dict, claims: dict, expected: dict) -> None:
    try:
        federation, access = claims["federation"], claims["access"]
        _require(account["id"] == expected["subscription_id"], "account-subscription")
        _require(account["tenantId"] == expected["tenant_id"], "account-tenant")
        _require(account["state"] == "Enabled", "account-state")
        _require(account["user"]["type"].lower() == "serviceprincipal", "account-type")
        _require(account["user"]["name"].lower() == expected["client_id"].lower(), "account-client")
        _require(federation["iss"] == expected["issuer"], "federation-issuer")
        _require(federation["sub"] == expected["subject"], "federation-subject")
        _require(federation["aud"] == federation_audience(expected), "federation-audience")
        _require(federation["exp"] > time.time()+30, "federation-expiry")
        _require(access["tid"] == expected["tenant_id"], "access-tenant")
        _require(access["oid"] == expected["object_id"], "access-object")
        _require(access.get("appid", access.get("azp")) == expected["client_id"], "access-client")
        _require(access["exp"] > time.time()+30, "access-expiry")
    except (KeyError,TypeError,ValueError):
        raise IdentityRefused("principal-fields") from None


def terraform_environment(expected: dict, assertion: str, inherited: dict | None = None) -> dict:
    if not assertion:
        raise IdentityRefused("assertion-missing")
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
            _require(uri.scheme == "https" and uri.netloc == origin.netloc and not uri.username
                     and not uri.fragment and uri.path.startswith(expected["oidc_path_prefix"]),
                     "refresh-endpoint")
            _require(source["AZURESUBSCRIPTION_SERVICE_CONNECTION_ID"] == expected["service_connection_id"],
                     "refresh-connection")
            _require(bool(source["SYSTEM_ACCESSTOKEN"]), "refresh-job-token")
            env.pop("ARM_OIDC_TOKEN")
            env.update({"ARM_ADO_PIPELINE_SERVICE_CONNECTION_ID":expected["service_connection_id"],
                "ARM_OIDC_AZURE_SERVICE_CONNECTION_ID":expected["service_connection_id"],
                "ARM_OIDC_REQUEST_TOKEN":source["SYSTEM_ACCESSTOKEN"],
                "ARM_OIDC_REQUEST_URL":source["SYSTEM_OIDCREQUESTURI"]})
        except (KeyError,TypeError,ValueError):
            raise IdentityRefused("refresh-fields") from None
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
