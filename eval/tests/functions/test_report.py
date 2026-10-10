"""The report: what it says, and that its writer is the gateway harness's identifier-refusing one."""

import json

import pytest

from app.functions import report
from app.gateway import report as gateway_report
from app.gateway.checks import CheckResult

COMMIT = "0123456789abcdef0123456789abcdef01234567"

ALL = [CheckResult("I1", True, "5 of 5 searchable within 120 s: median 30 s, maximum 50 s"),
       CheckResult("I2", True, "exactly 2 chunks"), CheckResult("I3", True, "3 chunks, same keys"),
       CheckResult("I4", False, "no poison message within 10 minutes"),
       CheckResult("T1", True, "10 of 10"), CheckResult("T2", True, "both 401"),
       CheckResult("T3", True, "both 401"), CheckResult("T4", True, "10 of 30 got a 429")]


def test_the_writer_is_the_gateway_harnesss_one():
    assert report.write is gateway_report.write
    assert report.IdentifierError is gateway_report.IdentifierError


def test_the_report_lists_every_check_in_order_with_its_outcome_and_detail():
    text = report.render(ALL, commit=COMMIT)

    positions = [text.index(f"- {name}:") for name in ("I1", "I2", "I3", "I4", "T1", "T2", "T3", "T4")]
    assert positions == sorted(positions)
    assert "- I1: pass" in text and "- I4: fail" in text and "median 30 s" in text
    assert "7 of 8 checks passed" in text and COMMIT in text


def test_a_check_that_was_not_run_says_so_and_does_not_count_as_a_pass():
    text = report.render([c for c in ALL if c.check != "T4"], commit=COMMIT)
    assert "- T4: not run" in text and "6 of 8 checks passed" in text


def test_a_failed_check_is_reported_as_a_result_not_hidden():
    text = report.render(ALL, commit=COMMIT)
    assert "A failed check is a result" in text


def test_the_report_says_what_t3_did_and_did_not_test():
    text = report.render(ALL, commit=COMMIT)
    assert "releaselens-search-tool" in text and "cannot get a token" in text


def test_the_report_holds_no_guid_and_no_azure_hostname_so_the_writer_accepts_it(tmp_path):
    path = tmp_path / "report.md"
    report.write(path, report.render(ALL, commit=COMMIT))
    assert path.exists()


def test_load_results_reads_each_checks_own_file_and_ignores_the_rest(tmp_path):
    for name, passed in (("i1", True), ("t4", False)):
        (tmp_path / f"check-{name}.json").write_text(json.dumps(
            {"check": name.upper(), "passed": passed, "detail": "d"}), encoding="utf-8")
    (tmp_path / "check-t4-burst.json").write_text("{}", encoding="utf-8")
    (tmp_path / "other.json").write_text("{}", encoding="utf-8")

    assert report.load_results(tmp_path) == [CheckResult("I1", True, "d"), CheckResult("T4", False, "d")]


def test_load_results_refuses_a_file_that_is_not_a_check_result(tmp_path):
    (tmp_path / "check-i1.json").write_text('{"nope": 1}', encoding="utf-8")
    with pytest.raises(ValueError):
        report.load_results(tmp_path)
