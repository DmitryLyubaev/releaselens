"""The fixed workload (spec §7.2): 45 requests, one every 4 seconds, whatever the responses.

Request n is sent at n x spacing on the run's clock, whether or not the earlier ones have
answered, so a slow or failing response never slows the load down.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .client import Record

COUNT = 45
SPACING_S = 4.0


async def run(
    send: Callable[[int], Awaitable[Record]], *, count: int = COUNT, spacing_s: float = SPACING_S,
    clock: Callable[[], float], sleep: Callable[[float], Awaitable[None]],
) -> list[Record]:
    start = clock()
    tasks: list[asyncio.Task[Record]] = []
    try:
        for seq in range(count):
            delay = start + seq * spacing_s - clock()
            if delay > 0:
                await sleep(delay)
            tasks.append(asyncio.create_task(send(seq)))
            # Let the send reach its first await, so it starts at its own time and not after the
            # next wait has been measured.
            await asyncio.sleep(0)
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        raise
