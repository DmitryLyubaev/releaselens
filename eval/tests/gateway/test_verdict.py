"""The failover verdict by the rule fixed in advance: thresholds 14 failures and 43 successes."""

from app.gateway.client import Record
from app.gateway.verdict import failover


def _record(seq: int, status: int | None = 200, region: str | None = "primary") -> Record:
    return Record(seq, seq * 4.0, status, 120.0, region if status == 200 else None, status == 200, 0, 300, 20, "owner")


def _before(failures: int, total: int = 45) -> list[Record]:
    return [_record(n, 429 if n < failures else 200) for n in range(total)]


def _after(successes: int, secondary: int, total: int = 45, failure: int | None = 429) -> list[Record]:
    records = [_record(n, 200, "secondary" if n < secondary else "primary") for n in range(successes)]
    records += [_record(n, failure) for n in range(successes, total)]
    return records


def test_13_before_failures_are_inconclusive_even_with_all_45_after_successes():
    result = failover(_before(13), _after(45, 20))
    assert result.outcome == "inconclusive"
    assert "13" in result.reason and "14" in result.reason
    assert result.before_failures == 13 and result.after_successes == 45


def test_14_before_failures_and_43_successes_with_one_secondary_held():
    result = failover(_before(14), _after(43, 1))
    assert result.outcome == "held"
    assert (result.before_total, result.before_failures) == (45, 14)
    assert (result.after_total, result.after_successes, result.after_secondary) == (45, 43, 1)
    assert result.after_failure_statuses == (429, 429)


def test_42_successes_did_not_hold_and_the_reason_names_the_count():
    result = failover(_before(30), _after(42, 10, failure=503))
    assert result.outcome == "did not hold"
    assert "42" in result.reason and "43" in result.reason
    assert result.after_failure_statuses == (503, 503, 503)


def test_45_successes_none_from_the_secondary_did_not_hold_and_the_reason_names_the_region():
    result = failover(_before(30), _after(45, 0))
    assert result.outcome == "did not hold"
    assert "Southeast Asia" in result.reason
    assert result.after_failure_statuses == ()


def test_both_reasons_are_given_when_both_fail():
    result = failover(_before(30), _after(40, 0))
    assert result.outcome == "did not hold"
    assert "40" in result.reason and "Southeast Asia" in result.reason


def test_a_secondary_answer_that_is_not_a_success_does_not_count():
    after = _after(43, 0)
    after[44] = Record(44, 176.0, 500, 90.0, "secondary", True, 0, 0, 0, "owner")
    assert failover(_before(30), after).outcome == "did not hold"


def test_a_missing_status_is_a_failure_and_is_listed():
    after = _after(43, 5)
    after[43] = _record(43, None)
    after[44] = _record(44, 429)
    result = failover(_before(30), after)
    assert result.outcome == "held"
    assert result.after_failure_statuses == (None, 429)


def test_a_short_run_cannot_pass():
    assert failover(_before(14), _after(10, 5, total=10)).outcome == "did not hold"
    assert failover(_before(13, total=20), _after(45, 5)).outcome == "inconclusive"
