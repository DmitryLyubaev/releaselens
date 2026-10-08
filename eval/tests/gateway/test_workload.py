"""The fixed workload: send times on a fake clock, and a request that times out is not a crash."""

import asyncio
import json

import httpx
import pytest

from app.gateway import client, workload
from app.gateway.client import Record


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _record(seq: int, sent_at: float) -> Record:
    return Record(seq, sent_at, 200, 10.0, None, False, 0, 1, 1, "owner")


def test_the_prompt_and_limits_are_the_specs():
    # About 300 tokens: a word is about 1.3 tokens in English prose, so 200 to 260 words.
    assert 200 <= len(client.PROMPT.split()) <= 260
    assert (client.MAX_TOKENS, client.TEMPERATURE, client.TIMEOUT_S) == (40, 0, 30.0)
    assert (workload.COUNT, workload.SPACING_S) == (45, 4.0)


async def test_requests_go_out_every_four_seconds_whatever_each_response_takes():
    clock = FakeClock()
    started: list[tuple[int, float]] = []
    gate = asyncio.Event()

    async def send(seq: int) -> Record:
        started.append((seq, clock()))
        if seq == 44:
            gate.set()
        elif seq % 2 == 0:
            # A response that has not come back by the time the next request is due, nor by
            # the time the last is sent, must not slow the sends down.
            await gate.wait()
        return _record(seq, clock())

    records = await workload.run(send, clock=clock, sleep=clock.sleep)

    assert len(records) == 45
    assert [r.seq for r in records] == list(range(45))
    assert started == [(n, 4.0 * n) for n in range(45)]


async def test_spacing_and_count_are_parameters():
    clock = FakeClock()
    seen = []

    async def send(seq: int) -> Record:
        seen.append(clock())
        return _record(seq, clock())

    records = await workload.run(send, count=3, spacing_s=1.5, clock=clock, sleep=clock.sleep)
    assert len(records) == 3
    assert seen == [0.0, 1.5, 3.0]


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("slow"), httpx.ConnectError("refused")])
async def test_a_timeout_is_a_failed_request_not_a_crash(failure):
    clock = FakeClock()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise failure

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async def send(seq: int) -> Record:
            return await client.send_one(http, "https://example.com/openai/v1/chat/completions", "tok", "dep", seq,
                                         caller="owner", region_signal="x-ms-region",
                                         clock=clock, sleep=clock.sleep)

        records = await workload.run(send, clock=clock, sleep=clock.sleep)

    assert len(records) == 45
    assert len(calls) == 45
    assert all(r.status is None and not r.model_called and r.region is None for r in records)
    # Nothing identifying is kept about the failure.
    assert "example.com" not in json.dumps([r.__dict__ for r in records])


async def test_an_unexpected_error_lets_the_other_requests_finish_then_propagates():
    # The gateway has counted every request that was sent, so each one's record must be able to
    # reach the caller: a bug in one request does not cancel the others.
    clock = FakeClock()
    finished: list[int] = []

    async def send(seq: int) -> Record:
        if seq == 2:
            raise RuntimeError("a bug")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        finished.append(seq)
        return _record(seq, clock())

    with pytest.raises(RuntimeError, match="a bug"):
        await workload.run(send, count=5, clock=clock, sleep=clock.sleep)

    assert sorted(finished) == [0, 1, 3, 4]


class _Stop(BaseException):
    """Stands for Ctrl+C, which would end the test's own event loop."""


async def test_an_interrupt_between_sends_cancels_what_is_still_in_flight():
    clock = FakeClock()
    cancelled: list[int] = []

    async def send(seq: int) -> Record:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(seq)
            raise
        return _record(seq, clock())

    async def interrupted(seconds: float) -> None:
        if clock() >= 2.0:
            raise _Stop
        await clock.sleep(seconds)

    with pytest.raises(_Stop):
        await workload.run(send, count=5, spacing_s=1.0, clock=clock, sleep=interrupted)
    await asyncio.sleep(0)
    assert sorted(cancelled) == [0, 1, 2]
