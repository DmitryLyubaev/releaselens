"""The client's 429 rule against the app's own cases (spec §2, §7.2).

Every row is a case from `tests/ReleaseLens.Llm.Tests/AzureOpenAiRateLimitTests.cs`, or a case
the app's `ReadAdvisedWait` / `TryReadPlainInteger` decides the same way (named in the comment).
"""

import httpx
import pytest

from app.gateway.rule import WAIT_BUDGET_MS, advised_wait_ms

INT_MAX = 2**31 - 1

# (headers, expected advised wait in ms or None)
CASES = [
    # RetryAfterMs_WithinBudget_...: 1200 ms
    ({"retry-after-ms": "1200"}, 1200),
    # RetryAfterSeconds_WithoutRetryAfterMs_IsHonoured: whole seconds, as milliseconds
    ({"retry-after": "2"}, 2000),
    # BothHeaders_RetryAfterMsWins
    ({"retry-after-ms": "400", "retry-after": "2"}, 400),
    # WaitLongerThanWhatRemains_...: 1500 ms is a usable wait; whether it fits is the budget's call
    ({"retry-after-ms": "1500"}, 1500),
    # NoUsableRetryHeader_...: no header at all
    ({}, None),
    # NoUsableRetryHeader_...: an HTTP-date is ignored
    ({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, None),
    # NoUsableRetryHeader_...: a decimal, a sign
    ({"retry-after": "1.5"}, None),
    ({"retry-after": "-1"}, None),
    # MalformedRetryAfterMs_IsTreatedAsAbsent, one row per InlineData (no retry-after to fall back to)
    ({"retry-after-ms": "1.5"}, None),
    ({"retry-after-ms": "-5"}, None),
    ({"retry-after-ms": "99999999999"}, None),      # past int max
    ({"retry-after-ms": " 200 "}, None),
    ({"retry-after-ms": "+200"}, None),
    ({"retry-after-ms": "200ms"}, None),
    ({"retry-after-ms": ""}, None),
    # MalformedRetryAfterMs_FallsBackToRetryAfter
    ({"retry-after-ms": "1.5", "retry-after": "1"}, 1000),
    # The same decisions TryReadPlainInteger makes for retry-after-ms and for retry-after
    ({"retry-after": "+2"}, None),
    ({"retry-after": " 2 "}, None),
    ({"retry-after": "2s"}, None),
    ({"retry-after": ""}, None),
    ({"retry-after": "99999999999"}, None),
    ({"retry-after-ms": "99999999999", "retry-after": "1"}, 1000),   # malformed ms falls back
    ({"retry-after-ms": "-5", "retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, None),
    # int.TryParse with NumberStyles.None: digits only, up to int.MaxValue, leading zeros fine
    ({"retry-after-ms": str(INT_MAX)}, INT_MAX),
    ({"retry-after-ms": str(INT_MAX + 1)}, None),
    ({"retry-after": str(INT_MAX)}, INT_MAX * 1000),
    ({"retry-after": str(INT_MAX + 1)}, None),
    ({"retry-after-ms": "0"}, 0),
    ({"retry-after-ms": "0200"}, 200),
    # Not plain ASCII digits: another script's digit, a trailing newline
    ({"retry-after-ms": "٣"}, None),
    ({"retry-after-ms": "200\n"}, None),
    # A list is not one value (the app requires exactly one)
    ({"retry-after-ms": "100, 200"}, None),
    ({"retry-after": "1, 2"}, None),
    ({"retry-after-ms": "100,200", "retry-after": "3"}, 3000),
    # Header names are case-insensitive
    ({"Retry-After-Ms": "250"}, 250),
    ({"RETRY-AFTER": "1"}, 1000),
]


@pytest.mark.parametrize(("headers", "expected"), CASES, ids=[f"{h}->{e}" for h, e in CASES])
def test_rule_matches_the_apps_cases(headers, expected):
    assert advised_wait_ms(headers) == expected


def test_a_header_sent_twice_is_not_one_value():
    # httpx joins repeated headers with ", ", which is a list, which the app rejects.
    twice = httpx.Headers([("retry-after-ms", "100"), ("retry-after-ms", "200")])
    assert advised_wait_ms(twice) is None
    assert advised_wait_ms(httpx.Headers([("retry-after-ms", "100"), ("retry-after", "7")])) == 100


def test_the_budget_is_the_apps_three_seconds():
    assert WAIT_BUDGET_MS == 3000
