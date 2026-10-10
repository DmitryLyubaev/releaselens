"""A small MCP client over Streamable HTTP, with httpx directly (no SDK).

It speaks what the checks need and nothing more: `initialize` (then the `initialized`
notification), `tools/list` and `tools/call`, as JSON-RPC 2.0 POSTs. A reply is `application/json`
or a single `text/event-stream` message, and both are read. The server may set `Mcp-Session-Id` on
the `initialize` reply; once it does, every later request carries it. A token, when there is one, is
sent as a bearer header, and nothing here records, logs or raises a token, a URL or a reply body: an
HTTP refusal is its status (and `Retry-After`) and nothing else.
"""

from __future__ import annotations

import itertools
import json
import threading

import httpx

PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "releaselens-functions-harness"


class McpError(Exception):
    """Base of every error this client raises on purpose."""


class McpHttpError(McpError):
    """The server (or the gateway in front of it) answered with an HTTP status that is not a success."""

    def __init__(self, status: int, retry_after: str | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after


class McpRpcError(McpError):
    """A JSON-RPC error object in the reply."""

    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(f"JSON-RPC error {code}: {message}")
        self.code = code


class McpToolError(McpError):
    """A tool ran and reported an error (`isError`); the message is the tool's own text."""


class McpProtocolError(McpError):
    """A reply that is not the shape the protocol says."""


def describe(error: McpError) -> str:
    """What a check's result may say about `error`: its class, and its HTTP status or JSON-RPC code.

    Never its message: a JSON-RPC error's message and a tool's error text come from the server, and may
    name a host, which would make the report's writer refuse the whole result.
    """
    name = type(error).__name__
    if isinstance(error, McpHttpError):
        return f"{name} (HTTP {error.status})"
    if isinstance(error, McpRpcError) and isinstance(error.code, int) and not isinstance(error.code, bool):
        return f"{name} (JSON-RPC code {error.code})"
    return name


def text_of(result: dict) -> str:
    """The text of a tool result's first text content."""
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
            return item["text"]
    raise McpProtocolError("the tool result has no text content")


def _event_stream_messages(text: str) -> list[dict]:
    """The JSON messages in a `text/event-stream` body: each event's `data` lines, joined."""
    messages, data = [], []

    def flush() -> None:
        if data:
            try:
                value = json.loads("\n".join(data))
            except ValueError:
                value = None
            if isinstance(value, dict):
                messages.append(value)
            data.clear()

    for line in text.splitlines():
        if not line:
            flush()
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    flush()
    return messages


class McpSession:
    """One MCP session. Safe to call from several threads once `initialize` has returned."""

    def __init__(self, http: httpx.Client, url: str, token: str | None) -> None:
        self._http = http
        self._url = url
        self._token = token
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._session_id: str | None = None
        self._protocol_version: str | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        if self._protocol_version is not None:
            headers["MCP-Protocol-Version"] = self._protocol_version
        return headers

    def _post(self, message: dict) -> httpx.Response:
        response = self._http.post(self._url, headers=self._headers(), content=json.dumps(message))
        if not response.is_success:
            raise McpHttpError(response.status_code, response.headers.get("retry-after"))
        return response

    def _request(self, method: str, params: dict | None = None) -> tuple[dict, httpx.Response]:
        request_id = next(self._ids)
        message: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        response = self._post(message)
        return self._answer(response, request_id), response

    @staticmethod
    def _answer(response: httpx.Response, request_id: int) -> dict:
        kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if kind == "application/json":
            try:
                body = response.json()
            except ValueError:
                raise McpProtocolError("the reply is not valid JSON") from None
            messages = body if isinstance(body, list) else [body]
        elif kind == "text/event-stream":
            messages = _event_stream_messages(response.text)
        else:
            raise McpProtocolError("the reply is neither application/json nor text/event-stream")
        answers = [m for m in messages if isinstance(m, dict) and m.get("id") == request_id and "method" not in m]
        if not answers:
            raise McpProtocolError("the reply holds no answer to the request")
        answer = answers[0]
        if "error" in answer:
            error = answer["error"] if isinstance(answer["error"], dict) else {}
            raise McpRpcError(error.get("code"), str(error.get("message", "")))
        result = answer.get("result")
        if not isinstance(result, dict):
            raise McpProtocolError("the answer has no result object")
        return result

    def initialize(self) -> dict:
        """The handshake: `initialize`, then `notifications/initialized`. Returns the server's `initialize` result."""
        result, response = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": CLIENT_NAME, "version": "1.0.0"}})
        with self._lock:
            self._session_id = response.headers.get("mcp-session-id") or self._session_id
            version = result.get("protocolVersion")
            self._protocol_version = version if isinstance(version, str) else PROTOCOL_VERSION
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def list_tools(self) -> list[str]:
        result, _ = self._request("tools/list")
        return [tool["name"] for tool in result.get("tools", []) if isinstance(tool, dict) and "name" in tool]

    def call(self, name: str, arguments: dict) -> dict:
        """Call a tool. A tool that reports an error raises `McpToolError` with its text."""
        result, _ = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            try:
                message = text_of(result)
            except McpProtocolError:
                message = "the tool reported an error with no text"
            raise McpToolError(message)
        return result
