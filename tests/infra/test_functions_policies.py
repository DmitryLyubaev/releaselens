"""Structural guards over the search tool's MCP API policy (spec section 5.2).

The policy is the tool's only front door: who may call, how often, and which credential reaches the
tool app. A reordered or dropped element changes that silently (a rate limit counted before the
caller is known, the caller's own token forwarded to the app), and nothing in Terraform would
notice because it loads the file as opaque text. So the file is parsed with `xml.etree.ElementTree`
and the structure the design depends on is pinned. Comments are dropped by the parser, so they
never satisfy a check.

The policy sits in front of an MCP server over Streamable HTTP, so it must never read a body:
reading the response body buffers it and breaks the stream.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
HOST_SUFFIXES = [".azure.com", ".azure-api.net", ".azurewebsites.net", ".windows.net"]
OID = '@(((Jwt)context.Variables["jwt"]).Claims.GetValueOrDefault("oid", "unknown"))'


def _path(repo_root: Path) -> Path:
    return repo_root / "infra" / "functions" / "policies" / "mcp-api.xml"


def _root(repo_root: Path) -> ET.Element:
    return ET.parse(_path(repo_root)).getroot()


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


def test_sections_and_base(repo_root: Path) -> None:
    root = _root(repo_root)
    assert root.tag == "policies"
    assert [child.tag for child in root] == ["inbound", "backend", "outbound", "on-error"]
    for section in ("inbound", "backend", "outbound", "on-error"):
        assert _section(root, section).find("base") is not None, f"{section} lost <base />"


def test_inbound_order(repo_root: Path) -> None:
    inbound = _section(_root(repo_root), "inbound")
    assert [step.tag for step in _steps(inbound)] == [
        "validate-azure-ad-token",
        "rate-limit-by-key",
        "emit-metric",
        "authentication-managed-identity",
    ]


def test_token_validation(repo_root: Path) -> None:
    validate = _only(_root(repo_root), "inbound", "validate-azure-ad-token")
    assert validate.get("tenant-id") == "{{tool-tenant-id}}"
    assert validate.get("output-token-variable-name") == "jwt"
    audiences = [audience.text for audience in validate.findall("audiences/audience")]
    assert audiences == ["api://{{tool-gateway-app-client-id}}", "{{tool-gateway-app-client-id}}"]
    claims = validate.findall("required-claims/claim")
    assert len(claims) == 1
    assert claims[0].get("name") == "roles"
    assert claims[0].get("match") == "any"
    assert [value.text for value in claims[0].findall("value")] == ["Gateway.Invoke"]


def test_rate_limit(repo_root: Path) -> None:
    limit = _only(_root(repo_root), "inbound", "rate-limit-by-key")
    assert limit.get("calls") == "20"
    assert limit.get("renewal-period") == "60"
    assert limit.get("counter-key") == OID


def test_metric(repo_root: Path) -> None:
    metric = _only(_root(repo_root), "inbound", "emit-metric")
    assert metric.get("name") == "Tool Calls"
    dimensions = metric.findall("dimension")
    assert [dim.get("name") for dim in dimensions] == ["Caller"]
    assert dimensions[0].get("value") == OID


def test_backend_credential(repo_root: Path) -> None:
    swap = _only(_root(repo_root), "inbound", "authentication-managed-identity")
    assert swap.get("resource") == "{{tool-app-audience}}"
    assert swap.get("client-id") == "{{tool-gateway-identity-client-id}}"


def test_the_named_values_are_the_four_task_8_creates(repo_root: Path) -> None:
    text = _path(repo_root).read_text(encoding="utf-8")
    assert set(re.findall(r"\{\{([^}]+)\}\}", text)) == {
        "tool-tenant-id",
        "tool-gateway-app-client-id",
        "tool-app-audience",
        "tool-gateway-identity-client-id",
    }


def test_no_retry_and_no_model_policy(repo_root: Path) -> None:
    # An MCP call is not retried, and the model-API policies do not apply to MCP traffic.
    root = _root(repo_root)
    assert not list(root.iter("retry"))
    assert not list(root.iter("forward-request"))
    assert [child.tag for child in _section(root, "backend")] == ["base"]
    assert not [element.tag for element in root.iter() if element.tag.startswith("llm-")]


def test_no_body_is_read(repo_root: Path) -> None:
    # Reading either body buffers it, which breaks Streamable HTTP. Checked on the raw text so a
    # reference in an attribute, an expression or a comment all fail.
    text = _path(repo_root).read_text(encoding="utf-8")
    assert "context.Request.Body" not in text
    assert "context.Response.Body" not in text
    # Only the error path sets a body, and that one is fixed text (see the on-error test).
    root = _root(repo_root)
    for section in ("inbound", "backend", "outbound"):
        assert not list(_section(root, section).iter("set-body")), f"{section} rewrites a body"


def test_on_error_body_is_fixed(repo_root: Path) -> None:
    on_error = _section(_root(repo_root), "on-error")
    bodies = list(on_error.iter("set-body"))
    assert len(bodies) == 1
    body = "".join(bodies[0].itertext())
    assert "context.Response.StatusCode" in body
    assert "error" in body
    serialized = ET.tostring(on_error, encoding="unicode")
    for forbidden in ("LastError", "Url", "Host", "Backend"):
        assert forbidden not in serialized, f"on-error mentions {forbidden}"
    # An error body must never go out with a success status; a failure status already set (a 401, a
    # 429 with its Retry-After) is kept, and anything else becomes a 502.
    steps = [child.tag for child in on_error]
    assert steps.count("set-status") == 1
    assert steps.index("set-status") < steps.index("set-body")
    status = on_error.find("set-status")
    assert status is not None
    code = status.get("code") or ""
    assert code.startswith("@(")
    assert "context.Response.StatusCode >= 400" in code
    assert re.search(r"\?\s*context\.Response\.StatusCode\s*:\s*502\s*\)$", code), code
    reason = status.get("reason") or ""
    assert reason and "@(" not in reason and "{{" not in reason


def test_no_literal_identifiers(repo_root: Path) -> None:
    text = _path(repo_root).read_text(encoding="utf-8")
    assert not GUID.search(text), "a GUID is written into the policy"
    # Every tenant, client and audience value is a named value; the credential's resource too.
    assert "https://" not in text, "the policy writes a URL"
    for suffix in HOST_SUFFIXES:
        assert suffix not in text, f"the policy writes a host ending {suffix}"
