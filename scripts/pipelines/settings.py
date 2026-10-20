"""Delivery settings from the private variable group, and the stage-to-role mapping."""
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from app.delivery_search.contracts import DeliveryRefused

ROLES = ("test", "prod", "verify")
PRINCIPAL_KEYS = ("client_id", "object_id", "issuer", "subject", "service_connection_id",
                  "oidc_request_origin", "oidc_path_prefix")
GUID = r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}"
STAGE_ROLES = {"build":"test", "create-service":"test", "plan-test":"test", "test":"test", "negative":"test",
               "teardown-test":"test", "plan-prod":"prod", "prod":"prod", "promote-negative":"prod",
               "teardown-prod":"prod", "status":"verify", "verify":"verify"}


@dataclass(frozen=True)
class Settings:
    tenant_id: str
    subscription_id: str
    storage_account_name: str
    search_group: str
    search_service_name: str
    release_digest: str
    principals: dict[str, dict]

    @property
    def storage_host(self) -> str:
        return f"{self.storage_account_name}.blob.core.windows.net"

    @property
    def search_host(self) -> str:
        return f"{self.search_service_name}.search.windows.net"


def _value(env: Mapping[str, str], key: str, pattern: str) -> str:
    value = env.get(key)
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise DeliveryRefused("settings")
    return value


def _principals(raw: str, tenant_id: str, subscription_id: str) -> dict[str, dict]:
    try:
        loaded = json.loads(raw)
    except ValueError:
        raise DeliveryRefused("settings") from None
    if not isinstance(loaded, dict):
        raise DeliveryRefused("settings")
    principals = {}
    for role in ROLES:
        entry = loaded.get(role)
        if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) and entry[k] for k in PRINCIPAL_KEYS):
            raise DeliveryRefused("settings")
        # The tenant and subscription are the group's own; a principal cannot carry different ones.
        principals[role] = {k:entry[k] for k in PRINCIPAL_KEYS} | {"tenant_id":tenant_id, "subscription_id":subscription_id}
    return principals


def load_settings(env: Mapping[str, str]) -> Settings:
    tenant_id = _value(env, "P8_TENANT_ID", GUID)
    subscription_id = _value(env, "P8_SUBSCRIPTION_ID", GUID)
    return Settings(tenant_id, subscription_id,
        _value(env, "P8_STORAGE_ACCOUNT", r"[a-z0-9]{3,24}"),
        _value(env, "P8_SEARCH_GROUP", r"rg-[a-z0-9-]{1,60}"),
        _value(env, "P8_SEARCH_SERVICE", r"[a-z0-9][a-z0-9-]{1,58}[a-z0-9]"),
        _value(env, "P8_RELEASE_DIGEST", r"[0-9a-f]{64}"),
        _principals(_value(env, "P8_PRINCIPALS_JSON", r"(?s).+"), tenant_id, subscription_id))


def role_for(stage: str, reason: str) -> str:
    role = STAGE_ROLES.get(stage)
    # The verify identity is read-only and exists for the scheduled run alone.
    if role is None or (role == "verify" and reason != "Schedule"):
        raise DeliveryRefused("settings")
    return role
