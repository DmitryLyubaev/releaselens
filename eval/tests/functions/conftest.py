"""Shared fakes for the Functions harness tests: no network, no real time."""

import pytest


class FakeClock:
    """A clock that only `sleep` moves, so a poll that waits 120 s takes no time."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
