"""The usage metric: per-caller totals with labels, the 2% comparison (B5), and the query."""

import json
import logging

import httpx
import pytest

from app.gateway import metric

OWNER, DEPLOY, STRANGER = (
    "00000000-0000-0000-0000-000000000001",
    "11111111-1111-1111-1111-111111111111",
    "22222222-2222-2222-2222-222222222222",
)
LABELS = {OWNER: "owner", DEPLOY: "deploy"}


def test_totals_sum_per_label_and_keep_an_unknown_oid_as_other():
    rows = [{"Caller": OWNER, "total": 900}, {"Caller": DEPLOY, "total": 320}, {"Caller": STRANGER, "total": 41}]
    assert metric.totals(rows, LABELS) == {"owner": 900, "deploy": 320, "other": 41}


def test_two_unknown_oids_sum_into_other_and_a_split_caller_sums_into_one_label():
    rows = [{"Caller": OWNER, "total": 100}, {"Caller": OWNER, "total": 50.0},
            {"Caller": STRANGER, "total": 1}, {"Caller": "33333333-3333-3333-3333-333333333333", "total": 2}]
    assert metric.totals(rows, LABELS) == {"owner": 150, "other": 3}


def test_totals_are_integers_and_a_blank_caller_is_other():
    totals = metric.totals([{"Caller": "", "total": 7.0}], LABELS)
    assert totals == {"other": 7} and type(totals["other"]) is int


def test_matches_passes_at_1_9_percent():
    result = metric.matches({"owner": 1000, "deploy": 300}, {"owner": 1019, "deploy": 300})
    assert (result.check, result.passed) == ("B5", True)


def test_matches_fails_at_2_1_percent_and_names_the_caller():
    result = metric.matches({"owner": 1000, "deploy": 300}, {"owner": 1021, "deploy": 300})
    assert result.passed is False and "owner" in result.detail


def test_matches_fails_when_a_caller_is_missing_on_either_side():
    missing_metric = metric.matches({"owner": 1000, "deploy": 300}, {"owner": 1000})
    missing_client = metric.matches({"owner": 1000}, {"owner": 1000, "deploy": 300})
    assert missing_metric.passed is False and "deploy" in missing_metric.detail
    assert missing_client.passed is False and "deploy" in missing_client.detail


def test_matches_fails_when_the_metric_holds_an_other_caller():
    assert metric.matches({"owner": 10}, {"owner": 10, "other": 5}).passed is False


def test_matches_takes_a_tolerance_and_treats_zero_on_both_sides_as_equal():
    assert metric.matches({"a": 100}, {"a": 110}, tolerance=0.10).passed is True
    assert metric.matches({"a": 0}, {"a": 0}).passed is True
    assert metric.matches({"a": 0}, {"a": 1}).passed is False


def test_matches_with_nothing_on_either_side_fails():
    assert metric.matches({}, {}).passed is False


# --- the query --------------------------------------------------------------------------------

SINCE = "2026-10-09T01:30:00Z"
TOKEN = "fake-appinsights-token-not-real"
APP = "33333333-3333-3333-3333-333333333333"


def _http(seen: list[httpx.Request], payload: dict | None = None, status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=payload or {})

    return httpx.Client(transport=httpx.MockTransport(handler))


TABLE = {"tables": [{"name": "PrimaryResult", "columns": [{"name": "Caller", "type": "string"},
                                                          {"name": "total", "type": "real"}],
                     "rows": [[OWNER, 1234.0], [DEPLOY, 321.0]]}]}


def test_query_makes_one_post_with_the_token_and_returns_rows_as_dicts():
    seen: list[httpx.Request] = []
    rows = metric.query(APP, TOKEN, SINCE, _http(seen, TABLE))

    (request,) = seen
    assert request.method == "POST" and request.url.path == f"/v1/apps/{APP}/query"
    assert request.url.host == "api.applicationinsights.io"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert rows == [{"Caller": OWNER, "total": 1234.0}, {"Caller": DEPLOY, "total": 321.0}]


def test_the_kql_names_the_namespace_the_window_and_groups_by_caller():
    seen: list[httpx.Request] = []
    metric.query(APP, TOKEN, SINCE, _http(seen, TABLE))
    body = json.loads(seen[0].content)
    assert "customMetrics" in body["query"]
    assert "releaselens-gateway" in body["query"]
    assert f"datetime({SINCE})" in body["query"]
    assert 'customDimensions["Caller"]' in body["query"] and "by Caller" in body["query"]
    assert body["timespan"].startswith(SINCE)


def test_a_malformed_since_is_refused_before_any_request():
    seen: list[httpx.Request] = []
    for bad in ("yesterday", "2026-10-09T01:30:00Z) | take 1 //", "2026-10-09", ""):
        with pytest.raises(ValueError):
            metric.query(APP, TOKEN, bad, _http(seen, TABLE))
    assert seen == []


def test_no_token_is_logged_and_a_failure_does_not_carry_the_token_or_the_url(caplog):
    seen: list[httpx.Request] = []
    with caplog.at_level(logging.DEBUG):
        metric.query(APP, TOKEN, SINCE, _http(seen, TABLE))
        with pytest.raises(metric.MetricQueryError) as failure:
            metric.query(APP, TOKEN, SINCE, _http(seen, {"error": {"message": TOKEN}}, status=403))
    assert TOKEN not in caplog.text
    assert TOKEN not in str(failure.value) and APP not in str(failure.value) and "403" in str(failure.value)


def test_a_response_without_a_table_is_an_error_not_an_empty_result():
    with pytest.raises(metric.MetricQueryError):
        metric.query(APP, TOKEN, SINCE, _http([], {"unexpected": True}))


def test_labels_come_from_the_environment_and_a_bad_value_is_not_echoed():
    assert metric.labels_from_env({"GATEWAY_CALLER_LABELS": json.dumps(LABELS)}) == LABELS
    for env in ({}, {"GATEWAY_CALLER_LABELS": "not json " + OWNER}, {"GATEWAY_CALLER_LABELS": "[1]"},
                {"GATEWAY_CALLER_LABELS": '{"a": 1}'}):
        with pytest.raises(metric.LabelsError) as failure:
            metric.labels_from_env(env)
        assert OWNER not in str(failure.value)
