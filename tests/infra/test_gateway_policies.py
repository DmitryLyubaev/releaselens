"""Structural guards over the API Management gateway's two policy documents.

The policies are the gateway's behaviour: who may call, how much they may use, which credential
reaches the model, and where the request goes. A reordered or dropped element changes that
silently (a budget checked before the caller is known, a retry that re-sends the gateway's own
429), and nothing in Terraform would notice because it loads the files as opaque text. So these
tests parse both files with `xml.etree.ElementTree` and pin the structure the design (spec section 4)
depends on. Comments are dropped by the parser, so they never satisfy a check.

Revision 2 must stay revision 1 plus one outbound block, so a later edit to one file cannot drift
from the other without a test failing.

The checks run on each policy as api.tf renders it, with Event Hubs logging (project 10) off and
on: each file's marker comment becomes nothing, or the log statement in log-to-eventhub.xml.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

FILES = ["api-v1.xml", "api-v1-rev2.xml"]
# Each file as api.tf renders it, with the Event Hubs switch off (the default) and on.
RENDERED = [
    pytest.param(name, logging, id=f"{name}-{'on' if logging else 'off'}")
    for name in FILES
    for logging in (False, True)
]

# api.tf replaces this comment with the log statement when event_hub_logging is on, and with
# nothing when it is off.
MARKER = "<!-- p10:log-to-eventhub -->"
LOG_FILE = "log-to-eventhub.xml"
LOGGER_ID = "p10-eventhub"
# The gateway event (project 10, spec 4.1): exactly these fields, in this order.
EVENT_FIELDS = [
    "event_time",
    "request_id",
    "session",
    "api",
    "operation",
    "caller",
    "answered_by",
    "status",
    "refused_by_gateway",
    "tokens",
    "latency_ms",
]

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


# What api.tf takes from log-to-eventhub.xml: the statement alone, from its opening tag to its
# closing tag, so the file's header comment never reaches the live policy.
STATEMENT = re.compile(r"(?s)<log-to-eventhub.*</log-to-eventhub>")


def _render(repo_root: Path, name: str, logging: bool) -> str:
    """The policy as api.tf renders it: the marker becomes the log statement, or nothing."""
    text = _path(repo_root, name).read_text(encoding="utf-8")
    log = STATEMENT.search(_path(repo_root, LOG_FILE).read_text(encoding="utf-8")).group(0) if logging else ""
    return text.replace(MARKER, log)


def _root(repo_root: Path, name: str, logging: bool = False) -> ET.Element:
    return ET.fromstring(_render(repo_root, name, logging))


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


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_sections_and_base(repo_root: Path, name: str, logging: bool) -> None:
    root = _root(repo_root, name, logging)
    assert root.tag == "policies"
    assert [child.tag for child in root] == ["inbound", "backend", "outbound", "on-error"]
    for section in ("inbound", "outbound", "on-error"):
        assert _section(root, section).find("base") is not None, f"{section} lost <base />"


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_inbound_order(repo_root: Path, name: str, logging: bool) -> None:
    inbound = _section(_root(repo_root, name, logging), "inbound")
    assert [step.tag for step in _steps(inbound)] == [
        "validate-azure-ad-token",
        "llm-token-limit",
        "llm-emit-token-metric",
        "authentication-managed-identity",
        "set-backend-service",
    ]


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_token_validation(repo_root: Path, name: str, logging: bool) -> None:
    validate = _only(_root(repo_root, name, logging), "inbound", "validate-azure-ad-token")
    assert validate.get("tenant-id") == "{{tenant-id}}"
    assert validate.get("output-token-variable-name") == "jwt"
    audiences = [audience.text for audience in validate.findall("audiences/audience")]
    assert audiences == ["api://{{gateway-app-client-id}}", "{{gateway-app-client-id}}"]
    claims = validate.findall("required-claims/claim")
    assert len(claims) == 1
    assert claims[0].get("name") == "roles"
    assert [value.text for value in claims[0].findall("value")] == ["Gateway.Invoke"]


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_budget(repo_root: Path, name: str, logging: bool) -> None:
    limit = _only(_root(repo_root, name, logging), "inbound", "llm-token-limit")
    key = limit.get("counter-key") or ""
    assert 'Variables["jwt"]' in key
    assert '"oid"' in key
    assert limit.get("tokens-per-minute") == "{{tokens-per-minute}}"
    assert limit.get("token-quota") == "{{tokens-per-day}}"
    assert limit.get("token-quota-period") == "Daily"
    assert limit.get("estimate-prompt-tokens") == "true"


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_metric(repo_root: Path, name: str, logging: bool) -> None:
    metric = _only(_root(repo_root, name, logging), "inbound", "llm-emit-token-metric")
    assert metric.get("namespace") == "releaselens-gateway"
    dimensions = {dim.get("name"): dim.get("value") for dim in metric.findall("dimension")}
    assert list(dimensions) == ["API ID", "Caller", "Deployment"]
    assert '"oid"' in (dimensions["Caller"] or "")
    assert 'Variables["jwt"]' in (dimensions["Caller"] or "")
    deployment = dimensions["Deployment"] or ""
    assert '"model"' in deployment
    # Without preserveContent the read would consume the body before the backend gets it.
    assert "preserveContent: true" in deployment


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_credential_swap(repo_root: Path, name: str, logging: bool) -> None:
    swap = _only(_root(repo_root, name, logging), "inbound", "authentication-managed-identity")
    assert swap.get("resource") == TOKEN_RESOURCE
    assert swap.get("client-id") == "{{gateway-identity-client-id}}"


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_route(repo_root: Path, name: str, logging: bool) -> None:
    route = _only(_root(repo_root, name, logging), "inbound", "set-backend-service")
    assert route.get("backend-id") == "aoai-pool"


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_retry_only_in_backend_on_a_model_429(repo_root: Path, name: str, logging: bool) -> None:
    root = _root(repo_root, name, logging)
    assert len(list(root.iter("retry"))) == 1
    retry = _only(root, "backend", "retry")
    assert retry.get("condition") == "@(context.Response.StatusCode == 429)"
    assert retry.get("count") == "1"
    assert retry.get("interval") == "0"
    assert retry.get("first-fast-retry") == "true"
    assert [child.tag for child in retry] == ["forward-request"]
    assert retry[0].get("buffer-request-body") == "true"
    assert [child.tag for child in _section(root, "backend")] == ["retry"]


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_backend_label(repo_root: Path, name: str, logging: bool) -> None:
    outbound = _section(_root(repo_root, name, logging), "outbound")
    header = _set_header(outbound, "x-releaselens-backend")
    assert header.get("exists-action") == "override"
    expression = "".join(header.itertext())
    assert "context.Request.Url.Host" in expression
    assert "{{primary-backend-host}}" in expression
    assert re.search(r'\?\s*"primary"\s*:\s*"secondary"', expression)


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_on_error_body_is_fixed(repo_root: Path, name: str, logging: bool) -> None:
    on_error = _section(_root(repo_root, name, logging), "on-error")
    bodies = list(on_error.iter("set-body"))
    assert len(bodies) == 1
    body = "".join(bodies[0].itertext())
    assert "context.Response.StatusCode" in body
    assert "error" in body
    serialized = ET.tostring(on_error, encoding="unicode")
    for forbidden in ("LastError", "Url", "Host", "Backend"):
        assert forbidden not in serialized, f"on-error mentions {forbidden}"


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_on_error_never_leaves_a_success_status_on_the_error_body(repo_root: Path, name: str, logging: bool) -> None:
    # If an error leaves the response on a 2xx or 3xx, the caller would get an error body with a
    # success status, and the measured test would count it as a success. A failure status the
    # error already carries (a 429 with its Retry-After, a 403, a 503) is kept; anything else is a 502.
    on_error = _section(_root(repo_root, name, logging), "on-error")
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


@pytest.mark.parametrize("logging", [False, True], ids=["off", "on"])
def test_rev2_differs_only_by_stripping_headers(repo_root: Path, logging: bool) -> None:
    rev1 = _root(repo_root, "api-v1.xml", logging)
    rev2 = _root(repo_root, "api-v1-rev2.xml", logging)

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


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_no_literal_identifiers(repo_root: Path, name: str, logging: bool) -> None:
    text = _render(repo_root, name, logging)
    assert not GUID.search(text), "a GUID is written into the policy"
    # The one fixed resource identifier is allowed; any other host is not.
    assert text.count(TOKEN_RESOURCE) == 1
    remainder = text.replace(TOKEN_RESOURCE, "")
    for suffix in HOST_SUFFIXES:
        assert suffix not in remainder, f"the policy writes a host ending {suffix}"


# The Event Hubs log statement (project 10). api.tf renders each policy from its file: the marker
# comment becomes the statement in log-to-eventhub.xml when event_hub_logging is on, and nothing
# when it is off, so a session that does not use Event Hubs applies exactly today's policy.


def _with_comments(text: str) -> ET.Element:
    return ET.fromstring(text, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))


def _logs(root: ET.Element) -> list[ET.Element]:
    return list(root.iter("log-to-eventhub"))


def _log_statement(repo_root: Path) -> ET.Element:
    return ET.fromstring(_path(repo_root, LOG_FILE).read_text(encoding="utf-8"))


# The statement only reads the call. Telemetry that wrote to the request or the response would
# change what the backend receives or what the caller gets. Each pattern is one way to do that (or
# to read the request, which the event has no use for), found in the C# of the expression.
FORBIDDEN_IN_STATEMENT = {
    "a read of Request.Headers": re.compile(r"Request\.Headers"),
    "an .Add( call": re.compile(r"\.Add\("),
    "a .Remove( call": re.compile(r"\.Remove\("),
    "a Headers[...] assignment": re.compile(r"Headers\s*\[[^\]]*\]\s*=(?!=)"),
    "an indexer assignment": re.compile(r"\]\s*=(?!=)"),
}


def _assert_read_only(expression: str) -> None:
    found = [what for what, pattern in FORBIDDEN_IN_STATEMENT.items() if pattern.search(expression)]
    assert not found, f"the log statement must not change the request or response: it has {', '.join(found)}"


def _expressions(repo_root: Path, name: str, logging: bool) -> list[str]:
    """The event expression of each rendered log statement (two when the switch is on, none when
    off), and the one in log-to-eventhub.xml itself, so every check also holds in the off state."""
    logs = _logs(_root(repo_root, name, logging))
    assert len(logs) == (2 if logging else 0)
    return ["".join(log.itertext()) for log in [*logs, _log_statement(repo_root)]]


@pytest.mark.parametrize("name", FILES)
def test_marker_ends_outbound_and_on_error(repo_root: Path, name: str) -> None:
    text = _path(repo_root, name).read_text(encoding="utf-8")
    assert text.count(MARKER) == 2
    root = _with_comments(text)
    for section in ("outbound", "on-error"):
        last = list(_section(root, section))[-1]
        assert last.tag is ET.Comment and f"<!--{last.text}-->" == MARKER, f"{section} does not end with the marker"
    # In outbound, after the backend label, so the statement can read it.
    names = [child.get("name") for child in _section(root, "outbound")]
    assert "x-releaselens-backend" in names


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_switch_off_renders_todays_policy(repo_root: Path, name: str, logging: bool) -> None:
    source = _path(repo_root, name).read_text(encoding="utf-8")
    rendered = _render(repo_root, name, logging)
    assert MARKER not in rendered
    if not logging:
        # Today's file without the marker comment, and nothing else.
        assert rendered == source.replace(MARKER, "")
        assert "log-to-eventhub" not in rendered and LOGGER_ID not in rendered
        assert _canonical(ET.fromstring(rendered)) == _canonical(ET.fromstring(source))
        return
    # On, the switch adds the two log statements and nothing else: not even the log file's
    # header comment, so the rendered policy has two comments fewer than its file (the markers).
    assert rendered.count("<!--") == source.count("<!--") - 2
    assert rendered.count("<log-to-eventhub ") == 2 and rendered.count("</log-to-eventhub>") == 2
    root = ET.fromstring(rendered)
    for section in ("outbound", "on-error"):
        for log in _section(root, section).findall("log-to-eventhub"):
            _section(root, section).remove(log)
    assert _canonical(root) == _canonical(ET.fromstring(source))


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_log_statement_in_outbound_and_on_error_when_on(repo_root: Path, name: str, logging: bool) -> None:
    root = _root(repo_root, name, logging)
    if not logging:
        assert _logs(root) == []
        return
    assert len(_logs(root)) == 2
    for section in ("outbound", "on-error"):
        children = list(_section(root, section))
        assert [child.tag for child in children].count("log-to-eventhub") == 1, section
        log = children[-1]
        assert log.tag == "log-to-eventhub", f"the log statement must be the last step of {section}"
        assert log.get("logger-id") == LOGGER_ID
        assert list(log) == [], "the statement holds one expression and no child element"
    # Outbound logs after the backend label is set, so the label is in the response.
    outbound = [child.get("name") or child.tag for child in _steps(_section(root, "outbound"))]
    assert outbound.index("x-releaselens-backend") < outbound.index("log-to-eventhub")


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_event_fields_match_contract(repo_root: Path, name: str, logging: bool) -> None:
    statement = _log_statement(repo_root)
    assert statement.tag == "log-to-eventhub" and statement.get("logger-id") == LOGGER_ID
    for expression in _expressions(repo_root, name, logging):
        assert expression.strip().startswith("@{") and expression.strip().endswith("}")
        # One JObject, built once, with exactly the contract's fields in order.
        assert expression.count("new JObject(") == 1
        assert re.findall(r'new JProperty\("([^"]+)"', expression) == EVENT_FIELDS
        assert "Formatting.None" in expression, "one event is one line of compact JSON"
        # Built in that one constructor call: nothing added to it or set on it afterwards.
        _assert_read_only(expression)


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_no_client_ip_or_body_in_event(repo_root: Path, name: str, logging: bool) -> None:
    for expression in _expressions(repo_root, name, logging):
        for forbidden in ("IpAddress", "Body", "model", "Deployment", "Request.Headers"):
            assert forbidden not in expression, f"the event reads {forbidden}"


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_unknown_caller_is_other(repo_root: Path, name: str, logging: bool) -> None:
    for expression in _expressions(repo_root, name, logging):
        # The label comes from the secret named value, looked up by the caller's object ID. A
        # call with no token looks up an empty ID; an ID that is not in the map is "other".
        assert re.search(r'oid\s*=\s*jwt\s*!=\s*null\s*\?\s*jwt\.Claims\.GetValueOrDefault\("oid", ""\)\s*:\s*""\s*;', expression)
        assert re.search(r'string caller = "other";', expression)
        assert re.search(r'found\s*=\s*\(string\)JObject\.Parse\("\{\{caller-labels\}\}"\)\[oid\]\s*;', expression)
        # Only one of the three known labels replaces "other"; anything else stays "other".
        assert re.search(r'if \(found == "owner" \|\| found == "deploy" \|\| found == "webapp"\) caller = found;', expression)
        assert re.search(r'new JProperty\("caller", caller\)', expression)
        # The raw object ID is never a value in the event.
        assert not re.search(r'new JProperty\("\w+", oid\)', expression)


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_tokens_consumed_variable_named(repo_root: Path, name: str, logging: bool) -> None:
    limit = _only(_root(repo_root, name, logging), "inbound", "llm-token-limit")
    assert limit.get("tokens-consumed-variable-name") == "tokensConsumed"
    for expression in _expressions(repo_root, name, logging):
        assert 'context.Variables.GetValueOrDefault<object>("tokensConsumed")' in expression


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_refusal_and_nulls_follow_contract(repo_root: Path, name: str, logging: bool) -> None:
    for expression in _expressions(repo_root, name, logging):
        # answered_by is the label outbound set, primary or secondary, and null when no backend
        # answered (a refusal, or a backend failure that reached on-error).
        assert 'context.Response.Headers.GetValueOrDefault("x-releaselens-backend", "")' in expression
        assert re.search(r'answered\s*=\s*\(label\s*==\s*"primary"\s*\|\|\s*label\s*==\s*"secondary"\)\s*\?\s*label\s*:\s*null\s*;', expression)
        # A refusal is the gateway's own 401, 403 or 429, with no backend answer.
        assert re.search(r'refused\s*=\s*answered\s*==\s*null\s*&&\s*\(status\s*==\s*401\s*\|\|\s*status\s*==\s*403\s*\|\|\s*status\s*==\s*429\)\s*;', expression)
        # No answer, no tokens: tokens start null, and each assignment needs an answer.
        assert "int? tokens = null;" in expression
        assignments = [line for line in expression.splitlines() if re.search(r"\btokens = (?!null)", line)]
        assert len(assignments) == 2
        assert all(line.strip().startswith("if (answered != null &&") for line in assignments)
        for field, variable in [("answered_by", "answered"), ("status", "status"), ("refused_by_gateway", "refused"), ("tokens", "tokens")]:
            assert f'new JProperty("{field}", {variable})' in expression
        assert 'new JProperty("session", "{{p10-session}}")' in expression
        assert 'new JProperty("request_id", context.RequestId.ToString())' in expression


# Telemetry must never change a call's answer. An exception in the statement at the end of outbound
# sends an answered call to on-error, whose set-status turns it into a 502, and the on-error copy
# throws again, so a refusal loses its 401, 403 or 429. The two steps that could throw are guarded.


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_caller_lookup_cannot_throw(repo_root: Path, name: str, logging: bool) -> None:
    for expression in _expressions(repo_root, name, logging):
        # The one parse of the label map, and the cast of what it finds, are inside a try whose
        # catch leaves "other".
        assert expression.count("JObject.Parse(") == 1
        guarded = re.search(r"try \{(?P<body>.*?)\} catch \{(?P<handler>.*?)\}", expression, re.DOTALL)
        assert guarded, "the caller lookup is not in a try/catch"
        assert 'JObject.Parse("{{caller-labels}}")[oid]' in guarded.group("body")
        assert guarded.group("handler").strip() == 'caller = "other";'


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_tokens_conversion_cannot_throw(repo_root: Path, name: str, logging: bool) -> None:
    for expression in _expressions(repo_root, name, logging):
        # No conversion that throws on an unexpected type or on overflow: an int is taken as it
        # is, a long only when it fits an int, and anything else leaves tokens null.
        assert "Convert." not in expression and "int.Parse" not in expression
        assert "if (answered != null && consumed is int) tokens = (int)consumed;" in expression
        assert (
            "if (answered != null && consumed is long && (long)consumed >= 0 && (long)consumed <= int.MaxValue) "
            "tokens = (int)(long)consumed;"
        ) in expression


@pytest.mark.parametrize(("name", "logging"), RENDERED)
def test_statement_does_not_mutate_request_or_response(repo_root: Path, name: str, logging: bool) -> None:
    for expression in _expressions(repo_root, name, logging):
        _assert_read_only(expression)


# A policy with any one of these in its statement must be rejected by the guard above.
MUTATIONS = [
    pytest.param('var seen = context.Request.Headers.GetValueOrDefault("x-a", "");', id="read-request-headers"),
    pytest.param('context.Response.Headers.Add("x-a", "b");', id="add"),
    pytest.param('context.Response.Headers.Remove("x-a");', id="remove"),
    pytest.param('context.Response.Headers["x-a"] = new string[] { "b" };', id="headers-indexer"),
    pytest.param('label = "x"; found = label; var map = new JObject(); map["k"] = "v";', id="indexer-assignment"),
]


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_a_statement_that_mutates_is_rejected(repo_root: Path, mutation: str) -> None:
    expression = "".join(_log_statement(repo_root).itertext())
    _assert_read_only(expression)
    anchor = "    int status = "
    assert expression.count(anchor) == 1
    mutated = expression.replace(anchor, f"    {mutation}\n{anchor}")
    with pytest.raises(AssertionError, match="must not change the request or response"):
        _assert_read_only(mutated)


def test_reads_and_comparisons_are_not_mistaken_for_mutations() -> None:
    # What the statement does read is allowed: == is not an assignment, a lookup is not an Add.
    _assert_read_only('var a = context.Response.Headers.GetValueOrDefault("x", ""); if (a == "b" || a[0] == "c") { }')
