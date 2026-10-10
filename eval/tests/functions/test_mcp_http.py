"""The MCP client: JSON-RPC over Streamable HTTP, as the harness speaks it."""

import json

import httpx
import pytest

from app.functions import mcp_http
from app.functions.mcp_http import McpHttpError, McpProtocolError, McpRpcError, McpSession, McpToolError

URL = "https://gateway.example.com/releaselens-search/mcp"
TOKEN = "fake-token-not-real"


def _result(request_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


class Server:
    """Records every request; answers by method."""

    def __init__(self, *, sse: bool = False, session_id: str | None = "sess-1") -> None:
        self.requests: list[httpx.Request] = []
        self.sse = sse
        self.session_id = session_id
        self.overrides: dict[str, httpx.Response] = {}

    def body(self, request: httpx.Request) -> dict:
        return json.loads(request.content)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        message = self.body(request)
        method = message["method"]
        if method in self.overrides:
            return self.overrides[method]
        if "id" not in message:
            return httpx.Response(202)
        headers = {"Mcp-Session-Id": self.session_id} if method == "initialize" and self.session_id else {}
        results = {
            "initialize": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "tool"}, "capabilities": {}},
            "tools/list": {"tools": [{"name": "search_corpus"}, {"name": "other"}]},
            "tools/call": {"content": [{"type": "text", "text": "[]"}], "isError": False},
        }
        payload = _result(message["id"], results[method])
        if self.sse:
            text = f"event: message\ndata: {json.dumps(payload)}\n\n"
            return httpx.Response(200, headers={"content-type": "text/event-stream", **headers}, text=text)
        return httpx.Response(200, headers=headers, json=payload)


def _session(server: Server, token: str | None = TOKEN) -> McpSession:
    return McpSession(httpx.Client(transport=httpx.MockTransport(server)), URL, token)


def test_initialize_sends_the_bearer_header_and_both_accept_types_then_the_initialized_notification():
    server = Server()
    info = _session(server).initialize()

    assert info["serverInfo"] == {"name": "tool"}
    first, second = server.requests
    assert first.headers["authorization"] == f"Bearer {TOKEN}"
    assert first.headers["accept"] == "application/json, text/event-stream"
    assert first.headers["content-type"].startswith("application/json")
    assert server.body(first)["method"] == "initialize" and server.body(first)["jsonrpc"] == "2.0"
    assert server.body(second) == {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert str(first.url) == URL


def test_without_a_token_no_authorization_header_is_sent():
    server = Server()
    _session(server, token=None).initialize()
    assert all("authorization" not in r.headers for r in server.requests)


def test_the_session_id_is_carried_once_the_server_sets_it_and_not_before():
    server = Server()
    session = _session(server)
    session.initialize()
    session.list_tools()

    first, notification, listing = server.requests
    assert "mcp-session-id" not in first.headers
    assert notification.headers["mcp-session-id"] == "sess-1"
    assert listing.headers["mcp-session-id"] == "sess-1"
    assert listing.headers["mcp-protocol-version"] == "2025-06-18"


def test_a_server_that_sets_no_session_id_gets_none_back():
    server = Server(session_id=None)
    session = _session(server)
    session.initialize()
    session.list_tools()
    assert all("mcp-session-id" not in r.headers for r in server.requests)


@pytest.mark.parametrize("sse", [False, True])
def test_both_reply_content_types_are_read(sse):
    server = Server(sse=sse)
    session = _session(server)
    session.initialize()
    assert session.list_tools() == ["search_corpus", "other"]
    assert session.call("search_corpus", {"query": "x"})["content"][0]["text"] == "[]"
    call = server.body(server.requests[-1])
    assert call["method"] == "tools/call" and call["params"] == {"name": "search_corpus", "arguments": {"query": "x"}}


def test_an_event_stream_with_a_notification_before_the_answer_is_read_by_id():
    note = {"jsonrpc": "2.0", "method": "notifications/message", "params": {}}

    def respond(request):
        message = json.loads(request.content)
        answer = _result(message["id"], {"tools": [{"name": "a"}]})
        text = f"data: {json.dumps(note)}\n\ndata: {json.dumps(answer)}\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream; charset=utf-8"}, text=text)

    session = McpSession(httpx.Client(transport=httpx.MockTransport(respond)), URL, TOKEN)
    assert session.list_tools() == ["a"]


def test_request_ids_are_distinct():
    server = Server()
    session = _session(server)
    session.initialize()
    session.list_tools()
    session.list_tools()
    ids = [server.body(r).get("id") for r in server.requests if "id" in server.body(r)]
    assert len(ids) == 3 and len(set(ids)) == 3


def test_a_tool_error_is_raised_with_its_text():
    server = Server()
    server.overrides["tools/call"] = httpx.Response(
        200, json=_result(1, {"content": [{"type": "text", "text": "search failed"}], "isError": True}))
    with pytest.raises(McpToolError) as raised:
        _session(server).call("search_corpus", {"query": "x"})
    assert str(raised.value) == "search failed"


def test_a_json_rpc_error_is_raised_with_its_code_and_message():
    server = Server()
    server.overrides["tools/call"] = httpx.Response(
        200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "bad params"}})
    with pytest.raises(McpRpcError) as raised:
        _session(server).call("nope", {})
    assert raised.value.code == -32602 and "bad params" in str(raised.value)


def test_an_http_refusal_carries_its_status_and_retry_after_and_never_the_body_or_url():
    server = Server()
    server.overrides["initialize"] = httpx.Response(429, headers={"Retry-After": "17"}, text="secret body text")
    with pytest.raises(McpHttpError) as raised:
        _session(server).initialize()
    assert raised.value.status == 429 and raised.value.retry_after == "17"
    assert str(raised.value) == "HTTP 429"


def test_a_401_is_an_http_error_with_no_retry_after():
    server = Server()
    server.overrides["initialize"] = httpx.Response(401, text="Unauthorized.")
    with pytest.raises(McpHttpError) as raised:
        _session(server).initialize()
    assert raised.value.status == 401 and raised.value.retry_after is None


def test_a_reply_that_is_neither_json_nor_an_event_stream_is_a_protocol_error():
    server = Server()
    server.overrides["tools/list"] = httpx.Response(200, headers={"content-type": "text/html"}, text="<html/>")
    with pytest.raises(McpProtocolError):
        _session(server).list_tools()


def test_an_event_stream_with_no_answer_is_a_protocol_error():
    server = Server()
    server.overrides["tools/list"] = httpx.Response(
        200, headers={"content-type": "text/event-stream"}, text=": keep-alive\n\n")
    with pytest.raises(McpProtocolError):
        _session(server).list_tools()


def test_a_transport_failure_is_not_swallowed():
    def fail(request):
        raise httpx.ConnectError("down")

    with pytest.raises(httpx.TransportError):
        McpSession(httpx.Client(transport=httpx.MockTransport(fail)), URL, TOKEN).initialize()


def test_the_error_types_share_a_base():
    assert all(issubclass(t, mcp_http.McpError) for t in (McpHttpError, McpRpcError, McpToolError, McpProtocolError))


def test_the_tool_text_is_read_from_the_first_text_content():
    assert mcp_http.text_of({"content": [{"type": "image"}, {"type": "text", "text": "hi"}]}) == "hi"
    with pytest.raises(McpProtocolError):
        mcp_http.text_of({"content": []})
