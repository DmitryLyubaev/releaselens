"""The report: what it says, and the write that refuses identifiers."""

import pytest

from app.gateway import report
from app.gateway.checks import CheckResult
from app.gateway.client import Record
from app.gateway.verdict import failover

COMMIT = "78d96621111111111111111111111111deadbee0"


def _record(seq: int, status: int | None, region: str | None, latency_ms: float, caller: str = "owner") -> Record:
    return Record(seq, seq * 4.0, status, latency_ms, region, status == 200, 0, 300, 20, caller)


def _before(failures: int = 20) -> list[Record]:
    return [_record(n, 429 if n < failures else 200, None if n < failures else "primary", 100 + n)
            for n in range(45)]


def _after(failures: int = 1) -> list[Record]:
    return [_record(n, 503 if n < failures else 200, None if n < failures else ("secondary" if n % 2 else "primary"),
                    200 + n) for n in range(45)]


CHECKS = [CheckResult("B1", True, "refused after 29 requests"), CheckResult("B2", False, "cap reached"),
          CheckResult("B4", True, "both 401"), CheckResult("B5", True, "within 2%")]


def _render(before=None, after=None, checks=CHECKS, b3="passed") -> str:
    before = _before() if before is None else before
    after = _after() if after is None else after
    return report.render(failover(before, after), before, after, checks, b3=b3, commit=COMMIT,
                         region_signal="x-ms-region")


# --- write ------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "owner 11111111-1111-1111-1111-111111111111 called",
    "ID 0A1B2C3D-0000-4000-8000-ABCDEF012345",
    "host aoai-releaselens-sea-a1b2c3.openai.azure.com",
    "host apim-releaselens-x.azure-api.net",
    "scope https://ai.azure.com/.default",
])
def test_report_refuses_text_with_a_guid_or_hostname(tmp_path, text):
    target = tmp_path / "report.md"
    with pytest.raises(report.IdentifierError):
        report.write(target, f"# Report\n\n{text}\n")
    assert not target.exists()


def test_report_refuses_a_forbidden_string_case_insensitively(tmp_path):
    target = tmp_path / "report.md"
    with pytest.raises(report.IdentifierError) as failure:
        report.write(target, "the account Releaselens-Sea-Acct held it", forbidden=["releaselens-sea-acct"])
    assert "acct" not in str(failure.value).lower()
    assert not target.exists()


def test_the_refusal_does_not_repeat_the_identifier(tmp_path):
    guid = "11111111-1111-1111-1111-111111111111"
    with pytest.raises(report.IdentifierError) as failure:
        report.write(tmp_path / "r.md", guid)
    assert guid not in str(failure.value)


def test_write_creates_missing_directories_and_writes_clean_text(tmp_path):
    target = tmp_path / "nested" / "report.md"
    report.write(target, "owner and deploy, labels only\n", forbidden=["", "zzz"])
    assert target.read_text(encoding="utf-8") == "owner and deploy, labels only\n"


# --- render -----------------------------------------------------------------------------------

def test_render_words_the_verdict_exactly_as_the_outcome():
    for before, after, outcome in ((_before(), _after(), "held"), (_before(), _after(5), "did not hold"),
                                   (_before(3), _after(), "inconclusive")):
        text = _render(before, after)
        assert f"Verdict: {outcome}\n" in text


def test_render_gives_both_runs_counts_and_the_failure_statuses():
    text = _render()
    assert "45" in text and "before" in text.lower() and "after" in text.lower()
    assert "20 of 45" in text          # before-run failures
    assert "44 of 45" in text          # after-run successes
    assert "503" in text               # the after-run failure's status


def test_render_gives_p50_and_p95_for_both_runs():
    text = _render()
    # before latencies 100..144: nearest rank p50 = 23rd = 122, p95 = 43rd = 142
    assert "p50 122 ms" in text and "p95 142 ms" in text
    # after latencies 200..244: p50 = 222, p95 = 242
    assert "p50 222 ms" in text and "p95 242 ms" in text


def test_render_gives_b1_to_b5_with_pass_or_fail_and_b3_as_entered_by_hand():
    text = _render(b3="failed")
    assert "B1: pass" in text and "B2: fail" in text and "B4: pass" in text and "B5: pass" in text
    assert "B3: fail" in text and "entered by hand" in text and "workflow run" in text


def test_render_says_not_run_for_a_check_with_no_result():
    assert "B2: not run" in _render(checks=[c for c in CHECKS if c.check != "B2"])


def test_render_cites_the_freeze_the_signal_and_the_commit():
    text = _render()
    assert COMMIT in text and "x-ms-region" in text and "freeze" in text.lower()


def test_render_carries_the_not_tested_live_paragraph():
    text = _render()
    assert "Gateway.Invoke" in text and "not tested live" in text.lower()
    assert "no second user" in text


def test_render_says_what_answered_by_southeast_asia_means():
    assert "not that the prompt was processed there" in _render()


def test_rendered_text_passes_the_writers_scan(tmp_path):
    report.write(tmp_path / "report.md", _render())


def test_render_handles_a_run_with_no_responses_and_an_empty_run():
    timeouts = [_record(n, None, None, 30000.0) for n in range(45)]
    text = _render(before=timeouts, after=timeouts)
    assert "none" in text.lower()
    assert "n/a" in report.render(failover([], []), [], [], [], b3="passed", commit=COMMIT,
                                  region_signal="x-ms-region")


def _signalled(seq: int, region: str | None, label: str | None) -> Record:
    return Record(seq, seq * 4.0, 200, 100.0, None, region is not None, 0, 300, 20, "owner", region, label)


def test_render_counts_the_responses_where_the_two_region_signals_disagree():
    after = ([_signalled(n, "Australia East", "primary") for n in range(30)]
             + [_signalled(30 + n, "Southeast Asia", "primary") for n in range(5)]     # processing region
             + [_signalled(35 + n, "Southeast Asia", "secondary") for n in range(10)])
    text = _render(after=after)
    assert "disagreed on 5 of 45 responses" in text


def test_render_says_zero_disagreements_and_none_for_records_that_carry_no_signals():
    assert "disagreed on 0 of 0 responses" in _render()        # the helpers' records hold no raw values
