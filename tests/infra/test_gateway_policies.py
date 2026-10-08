"""Structural guards over the API Management gateway's two policy documents.

The policies are the gateway's behaviour: who may call, how much they may use, which credential
reaches the model, and where the request goes. A reordered or dropped element changes that
silently (a budget checked before the caller is known, a retry that re-sends the gateway's own
429), and nothing in Terraform would notice because it loads the files as opaque text. So these
tests parse both files with `xml.etree.ElementTree` and pin the structure the design (spec section 4)
depends on. Comments are dropped by the parser, so they never satisfy a check.

Revision 2 must stay revision 1 plus one outbound block, so a later edit to one file cannot drift
from the other without a test failing.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

FILES = ["api-v1.xml", "api-v1-rev2.xml"]

STRIPPED_HEADERS = [
    "azureml-model-session",
    "x-ms-rai-invoked",
    "x-envoy-upstream-service-time",
    "x-ms-deployment-name",
    "apim-request-id",
]
KEPT_HEADERS = ["x-ms-region", "retry-after", "retry-after-ms", "x-releaselens-backend"]

GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
HOST_SUFFIXES = [".azure.com", ".azure-api.net", ".openai.azure.com"]
# The token audience for Azure OpenAI is a fixed public resource identifier, not a host of ours.
TOKEN_RESOURCE = "https://cognitiveservices.azure.com"


def _path(repo_root: Path, name: str) -> Path:
    return repo_root / "infra" / "gateway" / "policies" / name


def _root(repo_root: Path, name: str) -> ET.Element:
    return ET.parse(_path(repo_root, name)).getroot()


def _section(root: ET.Element, name: str) -> ET.Element:
    section = root.find(name)
    assert section is not None, f"no <{name}> section"
    return section


def _steps(section: ET.Element) -> list[ET.Element]:
    return [child for child in section if child.tag != "base"]


def _only(root: ET.Element, section: str, tag: str) -> ET.Element:
    found = _section(root, section).findall(tag)
    assert len(found) == 1, f"expected one <{tag}> in {section}, found {len(found)}"
    return found[0]


def _set_header(outbound: ET.Element, name: str) -> ET.Element:
    for header in outbound.iter("set-header"):
        if header.get("name") == name:
            return header
    raise AssertionError(f"no set-header for {name}")


def _canonical(element: ET.Element) -> str:
    return ET.canonicalize(ET.tostring(element, encoding="unicode"), strip_text=True)


@pytest.mark.parametrize("name", FILES)
def test_sections_and_base(repo_root: Path, name: str) -> None:
    root = _root(repo_root, name)
    assert root.tag == "policies"
    assert [child.tag for child in root] == ["inbound", "backend", "outbound", "on-error"]
    for section in ("inbound", "outbound", "on-error"):
        assert _section(root, section).find("base") is not None, f"{section} lost <base />"


@pytest.mark.parametrize("name", FILES)
def test_inbound_order(repo_root: Path, name: str) -> None:
    inbound = _section(_root(repo_root, name), "inbound")
    assert [step.tag for step in _steps(inbound)] == [
        "validate-azure-ad-token",
        "llm-token-limit",
        "llm-emit-token-metric",
        "authentication-managed-identity",
        "set-backend-service",
    ]


@pytest.mark.parametrize("name", FILES)
def test_token_validation(repo_root: Path, name: str) -> None:
    validate = _only(_root(repo_root, name), "inbound", "validate-azure-ad-token")
    assert validate.get("tenant-id") == "{{tenant-id}}"
    assert validate.get("output-token-variable-name") == "jwt"
    audiences = [audience.text for audience in validate.findall("audiences/audience")]
    assert audiences == ["api://{{gateway-app-client-id}}", "{{gateway-app-client-id}}"]
    claims = validate.findall("required-claims/claim")
    assert len(claims) == 1
    assert claims[0].get("name") == "roles"
    assert [value.text for value in claims[0].findall("value")] == ["Gateway.Invoke"]


@pytest.mark.parametrize("name", FILES)
def test_budget(repo_root: Path, name: str) -> None:
    limit = _only(_root(repo_root, name), "inbound", "llm-token-limit")
    key = limit.get("counter-key") or ""
    assert 'Variables["jwt"]' in key
    assert '"oid"' in key
    assert limit.get("tokens-per-minute") == "{{tokens-per-minute}}"
    assert limit.get("token-quota") == "{{tokens-per-day}}"
    assert limit.get("token-quota-period") == "Daily"
    assert limit.get("estimate-prompt-tokens") == "true"


@pytest.mark.parametrize("name", FILES)
def test_metric(repo_root: Path, name: str) -> None:
    metric = _only(_root(repo_root, name), "inbound", "llm-emit-token-metric")
    assert metric.get("namespace") == "releaselens-gateway"
    dimensions = {dim.get("name"): dim.get("value") for dim in metric.findall("dimension")}
    assert list(dimensions) == ["API ID", "Caller", "Deployment"]
    assert '"oid"' in (dimensions["Caller"] or "")
    assert 'Variables["jwt"]' in (dimensions["Caller"] or "")
    deployment = dimensions["Deployment"] or ""
    assert '"model"' in deployment
    # Without preserveContent the read would consume the body before the backend gets it.
    assert "preserveContent: true" in deployment


@pytest.mark.parametrize("name", FILES)
def test_credential_swap(repo_root: Path, name: str) -> None:
    swap = _only(_root(repo_root, name), "inbound", "authentication-managed-identity")
    assert swap.get("resource") == TOKEN_RESOURCE
    assert swap.get("client-id") == "{{gateway-identity-client-id}}"


@pytest.mark.parametrize("name", FILES)
def test_route(repo_root: Path, name: str) -> None:
    route = _only(_root(repo_root, name), "inbound", "set-backend-service")
    assert route.get("backend-id") == "aoai-pool"


@pytest.mark.parametrize("name", FILES)
def test_retry_only_in_backend_on_a_model_429(repo_root: Path, name: str) -> None:
    root = _root(repo_root, name)
    assert len(list(root.iter("retry"))) == 1
    retry = _only(root, "backend", "retry")
    assert retry.get("condition") == "@(context.Response.StatusCode == 429)"
    assert retry.get("count") == "1"
    assert retry.get("interval") == "0"
    assert retry.get("first-fast-retry") == "true"
    assert [child.tag for child in retry] == ["forward-request"]
    assert retry[0].get("buffer-request-body") == "true"
    assert [child.tag for child in _section(root, "backend")] == ["retry"]


@pytest.mark.parametrize("name", FILES)
def test_backend_label(repo_root: Path, name: str) -> None:
    outbound = _section(_root(repo_root, name), "outbound")
    header = _set_header(outbound, "x-releaselens-backend")
    assert header.get("exists-action") == "override"
    expression = "".join(header.itertext())
    assert "context.Request.Url.Host" in expression
    assert "{{primary-backend-host}}" in expression
    assert re.search(r'\?\s*"primary"\s*:\s*"secondary"', expression)


@pytest.mark.parametrize("name", FILES)
def test_on_error_body_is_fixed(repo_root: Path, name: str) -> None:
    on_error = _section(_root(repo_root, name), "on-error")
    bodies = list(on_error.iter("set-body"))
    assert len(bodies) == 1
    body = "".join(bodies[0].itertext())
    assert "context.Response.StatusCode" in body
    assert "error" in body
    serialized = ET.tostring(on_error, encoding="unicode")
    for forbidden in ("LastError", "Url", "Host", "Backend"):
        assert forbidden not in serialized, f"on-error mentions {forbidden}"


@pytest.mark.parametrize("name", FILES)
def test_on_error_never_leaves_a_success_status_on_the_error_body(repo_root: Path, name: str) -> None:
    # If an error leaves the response on a 2xx or 3xx, the caller would get an error body with a
    # success status, and the measured test would count it as a success. A failure status the
    # error already carries (a 429 with its Retry-After, a 403, a 503) is kept; anything else is a 502.
    on_error = _section(_root(repo_root, name), "on-error")
    steps = [child.tag for child in on_error]
    assert steps.count("set-status") == 1
    assert steps.index("set-status") < steps.index("set-body")
    status = on_error.find("set-status")
    assert status is not None
    code = status.get("code") or ""
    assert code.startswith("@(")
    assert "context.Response.StatusCode >= 400" in code
    assert code.count("context.Response.StatusCode") == 2
    assert re.search(r"\?\s*context\.Response\.StatusCode\s*:\s*502\s*\)$", code), code
    # The reason is fixed: no host or error detail can reach it.
    reason = status.get("reason") or ""
    assert reason and "@(" not in reason and "{{" not in reason


def test_rev2_differs_only_by_stripping_headers(repo_root: Path) -> None:
    rev1 = _root(repo_root, "api-v1.xml")
    rev2 = _root(repo_root, "api-v1-rev2.xml")

    outbound = _section(rev2, "outbound")
    deleted = [
        header.get("name")
        for header in outbound.findall("set-header")
        if header.get("exists-action") == "delete"
    ]
    assert sorted(deleted) == sorted(STRIPPED_HEADERS)
    for kept in KEPT_HEADERS:
        assert kept not in deleted
    assert _set_header(outbound, "x-releaselens-backend").get("exists-action") == "override"

    for header in [h for h in outbound.findall("set-header") if h.get("exists-action") == "delete"]:
        outbound.remove(header)
    assert _canonical(rev2) == _canonical(rev1)


@pytest.mark.parametrize("name", FILES)
def test_no_literal_identifiers(repo_root: Path, name: str) -> None:
    text = _path(repo_root, name).read_text(encoding="utf-8")
    assert not GUID.search(text), "a GUID is written into the policy"
    # The one fixed resource identifier is allowed; any other host is not.
    assert text.count(TOKEN_RESOURCE) == 1
    remainder = text.replace(TOKEN_RESOURCE, "")
    for suffix in HOST_SUFFIXES:
        assert suffix not in remainder, f"the policy writes a host ending {suffix}"
