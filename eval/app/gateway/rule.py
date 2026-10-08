"""The app's 429 rule, copied from `AzureOpenAiChatProvider` (spec §2, §7.2).

On a 429 the client reads the wait the service advises: `retry-after-ms` in milliseconds when it
is usable, otherwise `retry-after` in whole seconds, otherwise nothing. The wait is spent from a
per-query budget of 3,000 ms: one wait at most, then one more send. `tests/gateway/test_rule.py`
pins this against every case in `AzureOpenAiRateLimitTests`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

WAIT_BUDGET_MS = 3000

# A usable value is a plain non-negative integer that fits an int: digits only (no sign, decimal
# point, whitespace, unit or list) and no more than int.MaxValue, as `int.TryParse` with
# `NumberStyles.None` accepts. [0-9] and fullmatch, not isdigit, which accepts other scripts'
# digits and, with `$`, a trailing newline.
_PLAIN_INTEGER = re.compile(r"[0-9]+")
_INT_MAX = 2**31 - 1


def _plain_integer(headers: Mapping[str, str], name: str) -> int | None:
    # Header names are case-insensitive. A header sent twice reaches here joined by ", " (as
    # httpx does), which is not digits, so it is rejected just as the app rejects a count of two.
    values = [value for key, value in headers.items() if key.lower() == name]
    if len(values) != 1 or _PLAIN_INTEGER.fullmatch(values[0]) is None:
        return None
    number = int(values[0])
    return number if number <= _INT_MAX else None


def advised_wait_ms(headers: Mapping[str, str]) -> int | None:
    """The advised wait in milliseconds, or None when neither header is usable."""
    milliseconds = _plain_integer(headers, "retry-after-ms")
    if milliseconds is not None:
        return milliseconds
    seconds = _plain_integer(headers, "retry-after")
    return None if seconds is None else seconds * 1000
