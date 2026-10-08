"""`send_one`: the app's 429 rule on a fake clock, the region signal, the usage, no secrets kept."""

import dataclasses
import json

import httpx
import pytest

from app.gateway.client import PROMPT, Record, send_one

URL = "https://gateway.example.com/openai/v1/chat/completions"
BODY = {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 312, "completion_tokens": 28}}


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _ok(region: str | None = "Australia East", **extra) -> httpx.Response:
    headers = {"x-ms-region": region} if region is not None else {}
    return httpx.Response(200, json=BODY, headers={**headers, **extra})


def _throttled(**headers) -> httpx.Response:
    return httpx.Response(429, json={"error": {"code": "429"}}, headers=headers)


async def _send(responses, *, region_signal="x-ms-region", clock=None) -> tuple[Record, list[httpx.Request], FakeClock]:
    clock = clock or FakeClock()
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        record = await send_one(http, URL, "fake-token", "dep-not-real", 7, caller="owner",
                                region_signal=region_signal, clock=clock, sleep=clock.sleep)
    return record, seen, clock


async def test_the_request_is_the_apps_chat_completions_call():
    record, seen, _ = await _send([_ok()])

    (request,) = seen
    assert str(request.url) == URL and request.method == "POST"
    assert request.headers["authorization"] == "Bearer fake-token"
    assert "api-key" not in request.headers
    body = json.loads(request.content)
    assert body == {"model": "dep-not-real", "messages": [{"role": "user", "content": PROMPT}],
                    "max_tokens": 40, "temperature": 0}
    assert (record.seq, record.status, record.caller) == (7, 200, "owner")


async def test_usage_is_read_from_the_body():
    record, _, _ = await _send([_ok()])
    assert (record.prompt_tokens, record.completion_tokens) == (312, 28)


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"choices": []}),
    httpx.Response(200, json={"usage": {"prompt_tokens": "3", "completion_tokens": 1}}),
    httpx.Response(500, json=BODY),
])
async def test_missing_or_unusable_usage_is_zero(response):
    record, _, _ = await _send([response])
    assert (record.prompt_tokens, record.completion_tokens) == (0, 0)


async def test_a_429_advising_1000_ms_waits_once_then_sends_again():
    record, seen, clock = await _send([_throttled(**{"retry-after-ms": "1000"}), _ok()])

    assert len(seen) == 2
    assert clock.sleeps == [1.0]
    assert (record.status, record.waited_ms) == (200, 1000)
    assert record.sent_at == 100.0 and record.latency_ms == 1000.0


async def test_retry_after_seconds_is_the_fallback():
    record, _, clock = await _send([_throttled(**{"retry-after": "2"}), _ok()])
    assert clock.sleeps == [2.0] and record.waited_ms == 2000 and record.status == 200


async def test_the_wait_that_exactly_fills_the_budget_is_taken():
    record, seen, clock = await _send([_throttled(**{"retry-after-ms": "3000"}), _ok()])
    assert clock.sleeps == [3.0] and len(seen) == 2 and record.status == 200


@pytest.mark.parametrize("headers", [
    {"retry-after-ms": "4000"},       # does not fit the budget
    {"retry-after-ms": "3001"},
    {"retry-after": "4"},
    {},                               # nothing advised
    {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"},
])
async def test_a_429_that_cannot_be_waited_out_fails_at_once(headers):
    record, seen, clock = await _send([_throttled(**headers)])
    assert len(seen) == 1 and clock.sleeps == []
    assert (record.status, record.waited_ms) == (429, 0)


async def test_a_second_429_fails_without_a_second_wait():
    record, seen, clock = await _send([_throttled(**{"retry-after-ms": "100"}),
                                       _throttled(**{"retry-after-ms": "100"})])
    assert len(seen) == 2 and clock.sleeps == [0.1]
    assert (record.status, record.waited_ms) == (429, 100)


async def test_each_request_has_its_own_budget():
    clock = FakeClock()
    first, _, _ = await _send([_throttled(**{"retry-after-ms": "2500"}), _ok()], clock=clock)
    second, _, _ = await _send([_throttled(**{"retry-after-ms": "2500"}), _ok()], clock=clock)
    assert first.waited_ms == second.waited_ms == 2500
    assert second.status == 200


@pytest.mark.parametrize(("region", "expected"), [
    ("Southeast Asia", "secondary"), ("Australia East", "primary"), ("Mars Central", None), ("", None),
])
async def test_the_region_is_read_from_x_ms_region(region, expected):
    record, _, _ = await _send([_ok(region)])
    assert record.region == expected
    assert record.model_called is True


async def test_no_x_ms_region_means_no_model_was_called():
    record, _, _ = await _send([_ok(None)])
    assert record.model_called is False and record.region is None


@pytest.mark.parametrize(("value", "expected"), [("primary", "primary"), ("secondary", "secondary"), ("other", None)])
async def test_the_region_is_read_from_the_backend_label_when_that_is_the_frozen_signal(value, expected):
    record, _, _ = await _send([_ok("Australia East", **{"x-releaselens-backend": value})],
                               region_signal="x-releaselens-backend")
    assert record.region == expected
    assert record.model_called is True    # x-ms-region is present whichever signal is frozen


async def test_the_frozen_signal_is_the_only_one_read():
    record, _, _ = await _send([_ok("Southeast Asia", **{"x-releaselens-backend": "primary"})])
    assert record.region == "secondary"
    record, _, _ = await _send([_ok("Southeast Asia")], region_signal="x-releaselens-backend")
    assert record.region is None


async def test_an_unknown_signal_is_a_bug_not_a_guess():
    with pytest.raises(ValueError):
        await _send([_ok()], region_signal="x-other")


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("slow"), httpx.ConnectError("refused")])
async def test_a_timeout_or_dropped_connection_is_status_none(failure):
    record, _, _ = await _send([failure])
    assert record.status is None and record.model_called is False and record.region is None


async def test_a_timeout_on_the_retry_still_keeps_what_was_waited():
    record, _, _ = await _send([_throttled(**{"retry-after-ms": "500"}), httpx.ReadTimeout("slow")])
    assert (record.status, record.waited_ms) == (None, 500)


async def test_a_record_holds_no_token_oid_url_or_hostname():
    record, _, _ = await _send([_ok(**{"x-ms-request-id": "11111111-1111-1111-1111-111111111111"})])
    text = json.dumps(dataclasses.asdict(record))
    for secret in ("fake-token", "example.com", "gateway", "11111111", URL):
        assert secret not in text
