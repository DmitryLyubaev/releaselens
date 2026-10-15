"""Pin original, owner-reviewed resource checks; YAML cannot supply its own approval."""
import hashlib

from app.delivery_search.contracts import DeliveryRefused, canonical


def verify_checks(snapshot: dict, expected: dict) -> None:
    try:
        if not expected.get("approval_ref") or not all(snapshot.get(k) is True for k in (
            "pipeline_authorized","pool_authorized","private_project","main_protected","secrets_masked")):
            raise DeliveryRefused("checks")
        required = {"approval","branch","required-template","exclusive-lock"}
        if {item["kind"] for item in expected["checks"]} != required:
            raise DeliveryRefused("checks")
        actual = {row["id"]:row for row in snapshot["checks"]}
        if len(actual) != len(snapshot["checks"]) or set(actual) != {row["id"] for row in expected["checks"]}:
            raise DeliveryRefused("checks")
        for item in expected["checks"]:
            row = actual[item["id"]]
            if (row.get("isDisabled",False) is not False or row["type"]["id"] != item["type_id"]
                or row["version"] != item["version"] or row["resource"] != item["resource"]
                or hashlib.sha256(canonical(row["settings"])).hexdigest() != item["settings_sha256"]):
                raise DeliveryRefused("checks")
    except (KeyError,TypeError,ValueError):
        raise DeliveryRefused("checks") from None
