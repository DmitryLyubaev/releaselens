"""The harness commands: smoke, minute-budget, day-budget, access, metric-totals, report.

No network and no real time: the transport is an `httpx.MockTransport`, the token source is a
fake, sleep is a fake, git is a stub, and truststore is not injected.
"""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest

from app.gateway import __main__ as cli
from app.gateway import freeze

BASE = "https://gateway.example.com/openai/v1/"
DIRECT = "https://primary.example.com/openai/v1/"
SCOPE = "api://22222222-2222-2222-2222-222222222222/.default"
TENANT = "00000000-0000-0000-0000-000000000000"
OWNER_OID = "00000000-0000-0000-0000-0000000000a1"
DEPLOY_OID = "11111111-1111-1111-1111-1111111111b2"
APP = "33333333-3333-3333-3333-333333333333"
TOKEN = "fake-token-not-real"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
LABELS = json.dumps({OWNER_OID: "owner", DEPLOY_OID: "deploy"})
USAGE = {"usage": {"prompt_tokens": 300, "completion_tokens": 20}}


class _Tokens:
    created: list[tuple[str, str]] = []

    def __init__(self, scope: str, tenant_id: str) -> None:
        _Tokens.created.append((scope, tenant_id))

    def token(self) -> str:
        return TOKEN


class _Env:
    def __init__(self, tmp_path: Path) -> None:
        self.requests: list[httpx.Request] = []
        self.lock = threading.Lock()
        self.sleeps: list[float] = []
        self.porcelain = ""
        self.ignored = True
        self.git_calls: list[list[str]] = []
        self.freeze_file = tmp_path / "freeze.json"
        self.freeze_file.write_text('{"region_signal": "x-ms-region"}', encoding="utf-8")
        self.out = tmp_path / "out"
        self.respond = lambda request, n: httpx.Response(200, headers={"x-ms-region": "Australia East"}, json=USAGE)

    def run(self, command, **kwargs):
        self.git_calls.append(command)
        if command[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(command, 0, self.porcelain, "")
        if command[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(command, 0, COMMIT + "\n", "")
        return subprocess.CompletedProcess(command, 0 if self.ignored else 1, "", "")

    def handler(self, request: httpx.Request) -> httpx.Response:
        with self.lock:     # B1's burst calls this from many threads at once
            self.requests.append(request)
            n = len(self.requests)
        return self.respond(request, n)


@pytest.fixture
def env(tmp_path, monkeypatch):
    env = _Env(tmp_path)
    _Tokens.created = []
    monkeypatch.setattr(cli, "TokenSource", _Tokens)
    monkeypatch.setattr(cli, "FREEZE_FILE", env.freeze_file)
    monkeypatch.setattr(cli, "_run", env.run)
    monkeypatch.setattr(cli, "_sleep_sync", env.sleeps.append)
    monkeypatch.setattr(cli, "_sync_client", lambda: httpx.Client(transport=httpx.MockTransport(env.handler)))
    monkeypatch.setattr(cli.truststore, "inject_into_ssl", lambda: None)
    monkeypatch.setenv("GATEWAY_CALLER_LABELS", LABELS)
    return env


def _argv(env: _Env, command: str, *extra: str) -> list[str]:
    return [command, "--out", str(env.out), "--tenant", TENANT, *extra]


def _files(env: _Env) -> list[str]:
    return sorted(p.name for p in env.out.iterdir()) if env.out.exists() else []


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _everything_written(env: _Env) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in env.out.iterdir())


def _gateway_429(request, n):
    return httpx.Response(429, headers={"Retry-After": "9"}, json={"error": {"code": "429"}})


# --- minute-budget and day-budget -------------------------------------------------------------

BUDGET_ARGS = ("--base-url", BASE, "--scope", SCOPE)


@pytest.mark.parametrize("command", ["minute-budget", "day-budget"])
def test_a_budget_run_is_measured_so_it_refuses_an_unfrozen_signal_or_a_dirty_tree(env, capsys, command):
    env.freeze_file.write_text('{"region_signal": null}', encoding="utf-8")
    assert cli.main(_argv(env, command, *BUDGET_ARGS)) == 1
    assert "freeze.json" in capsys.readouterr().err

    env.freeze_file.write_text('{"region_signal": "x-ms-region"}', encoding="utf-8")
    env.porcelain = " M x\n"
    assert cli.main(_argv(env, command, *BUDGET_ARGS)) == 1
    assert "not clean" in capsys.readouterr().err
    assert env.requests == [] and _Tokens.created == [] and _files(env) == []


@pytest.mark.parametrize("command", ["minute-budget", "day-budget"])
def test_a_budget_run_refuses_an_output_directory_git_would_see(env, capsys, command):
    env.ignored = False
    inside = freeze.REPO_ROOT / "eval" / "gateway-out-not-real"
    assert cli.main([command, "--out", str(inside), "--tenant", TENANT, *BUDGET_ARGS]) == 1
    assert "not git-ignored" in capsys.readouterr().err
    assert env.requests == [] and not inside.exists()


def test_minute_budget_sends_one_burst_and_writes_every_record_and_its_result(env, capsys):
    env.respond = lambda request, n: _gateway_429(request, n) if n > 30 else httpx.Response(
        200, headers={"x-ms-region": "Australia East"}, json=USAGE)

    assert cli.main(_argv(env, "minute-budget", *BUDGET_ARGS)) == 0

    assert len(env.requests) == 40 and _Tokens.created == [(SCOPE, TENANT)]
    assert {str(r.url) for r in env.requests} == {BASE + "chat/completions"}
    assert {json.loads(r.content)["model"] for r in env.requests} == {"releaselens-chat"}
    assert _files(env) == ["budget-minute.jsonl", "check-b1.json"]
    records = _lines(env.out / "budget-minute.jsonl")
    assert sorted(r["seq"] for r in records) == list(range(40))      # one line each, no seq twice
    assert sorted(r["status"] for r in records) == [200] * 30 + [429] * 10
    assert sum(r["prompt_tokens"] for r in records) == 300 * 30 and {r["caller"] for r in records} == {"owner"}
    assert json.loads((env.out / "check-b1.json").read_text(encoding="utf-8"))["passed"] is True
    assert "B1: pass" in capsys.readouterr().out


def test_a_failed_check_still_writes_its_result_and_exits_1(env, capsys):
    env.respond = lambda request, n: httpx.Response(401, json={})
    assert cli.main(_argv(env, "minute-budget", *BUDGET_ARGS)) == 1
    assert json.loads((env.out / "check-b1.json").read_text(encoding="utf-8"))["passed"] is False
    assert "B1: fail" in capsys.readouterr().out


def _gateway_records(env: _Env, name: str, tokens: list[int], statuses: list[int] | None = None) -> Path:
    env.out.mkdir(exist_ok=True)
    statuses = statuses or [200] * len(tokens)
    path = env.out / name
    path.write_text("".join(json.dumps({"seq": i, "status": s, "prompt_tokens": t - 20 if t else 0,
                                        "completion_tokens": 20 if t else 0, "caller": "owner"}) + "\n"
                            for i, (t, s) in enumerate(zip(tokens, statuses, strict=True))), encoding="utf-8")
    return path


def _403(request, n):
    return httpx.Response(403, json={})


def _day_responses(env: _Env, *, answers: dict) -> None:
    env.respond = lambda request, n: answers.get(n, lambda r, k: httpx.Response(
        200, headers={"x-ms-region": "Australia East"}, json=USAGE))(request, n)


def test_day_budget_waits_out_each_429_and_stops_at_the_403_counting_the_sessions_earlier_records(env, capsys):
    _gateway_records(env, "failover-gateway.jsonl", [320] * 100)        # 32,000
    _gateway_records(env, "budget-minute.jsonl", [320] * 30)            #  9,600
    _gateway_records(env, "smoke.jsonl", [320, 320])                    #    640: 42,240 earlier
    # 200s at requests 1 and 3..10: 9 x 320 = 2,880 more, 45,120 in all, then the 403
    _day_responses(env, answers={2: _gateway_429, 11: _403})

    assert cli.main(_argv(env, "day-budget", *BUDGET_ARGS)) == 0

    assert env.sleeps == [9]
    out = capsys.readouterr().out
    assert "B2: pass" in out and "45120 tokens recorded today before the 403" in out
    assert [r["status"] for r in _lines(env.out / "budget-day.jsonl")][-2:] == [200, 403]
    assert {"check-b2.json", "budget-day.jsonl"} <= set(_files(env))


def test_day_budget_fails_a_403_that_comes_too_early_and_still_writes_its_records_and_result(env, capsys):
    _gateway_records(env, "failover-gateway.jsonl", [320] * 10)
    _day_responses(env, answers={3: _403})

    assert cli.main(_argv(env, "day-budget", *BUDGET_ARGS)) == 1

    assert "B2: fail" in capsys.readouterr().out
    assert json.loads((env.out / "check-b2.json").read_text(encoding="utf-8"))["passed"] is False
    assert len(_lines(env.out / "budget-day.jsonl")) == 3


def test_day_budget_leaves_out_records_from_before_00_00_utc_today(env):
    stale = _gateway_records(env, "failover-gateway.jsonl", [45000])
    three_days_ago = time.time() - 3 * 86400
    os.utime(stale, (three_days_ago, three_days_ago))
    _day_responses(env, answers={2: _403})

    assert cli.main(_argv(env, "day-budget", *BUDGET_ARGS)) == 1         # the stale 45,000 did not count

    detail = json.loads((env.out / "check-b2.json").read_text(encoding="utf-8"))["detail"]
    assert "320 tokens recorded today before the 403" in detail


def test_day_budget_counts_earlier_gateway_records_by_prefix_so_a_renamed_file_still_counts(env):
    # What the runbook tells the owner after a crash: keep the file, give it another name.
    _gateway_records(env, "failover-gateway-crashed.jsonl", [320] * 100)     # 32,000
    _gateway_records(env, "failover-gateway.jsonl", [320] * 20)              #  6,400
    _gateway_records(env, "budget-minute-2.jsonl", [320] * 20)               #  6,400
    _gateway_records(env, "budget-day-crashed.jsonl", [320])                 #    320: 45,120 earlier
    _day_responses(env, answers={1: _403})

    assert cli.main(_argv(env, "day-budget", *BUDGET_ARGS)) == 0

    detail = json.loads((env.out / "check-b2.json").read_text(encoding="utf-8"))["detail"]
    assert "45120 tokens recorded today before the 403 (45120 earlier, 0 in this run)" in detail


def test_day_budget_does_not_count_direct_runs_or_other_files(env):
    _gateway_records(env, "failover-direct.jsonl", [45000])
    _gateway_records(env, "failover-direct-1.jsonl", [45000])
    _gateway_records(env, "notes-budget-minute.jsonl", [45000])
    _gateway_records(env, "failover-gateway.jsonl", [320])
    _day_responses(env, answers={1: _403})

    assert cli.main(_argv(env, "day-budget", *BUDGET_ARGS)) == 1

    detail = json.loads((env.out / "check-b2.json").read_text(encoding="utf-8"))["detail"]
    assert "320 tokens recorded today before the 403" in detail


def test_day_budget_counts_its_own_file_once(env, capsys):
    # The run's own 200s are counted as `in this run`, and its file is written after: a rerun that
    # finds budget-day.jsonl refuses, so the file is never both earlier and current.
    _day_responses(env, answers={4: _403})
    assert cli.main(_argv(env, "day-budget", *BUDGET_ARGS)) == 1
    detail = json.loads((env.out / "check-b2.json").read_text(encoding="utf-8"))["detail"]
    assert "960 tokens recorded today before the 403 (0 earlier, 960 in this run)" in detail

    (env.out / "check-b2.json").unlink()                       # budget-day.jsonl stays in place
    assert cli.main(_argv(env, "day-budget", *BUDGET_ARGS)) == 1
    assert "already exists" in capsys.readouterr().err


def test_day_budget_counts_only_the_200_rows_of_the_earlier_records(env):
    _gateway_records(env, "failover-gateway.jsonl", [45000, 45000], statuses=[200, 429])
    _day_responses(env, answers={1: _403})
    assert cli.main(_argv(env, "day-budget", *BUDGET_ARGS)) == 0

    _gateway_records(env, "failover-gateway.jsonl", [30000, 30000], statuses=[429, 429])
    (env.out / "budget-day.jsonl").unlink()
    (env.out / "check-b2.json").unlink()
    env.requests.clear()
    assert cli.main(_argv(env, "day-budget", *BUDGET_ARGS)) == 1


@pytest.mark.parametrize("command", ["minute-budget", "day-budget"])
def test_a_budget_run_never_overwrites_an_earlier_one(env, capsys, command):
    env.out.mkdir()
    existing = env.out / ("budget-minute.jsonl" if command == "minute-budget" else "budget-day.jsonl")
    existing.write_text("earlier\n", encoding="utf-8")
    assert cli.main(_argv(env, command, *BUDGET_ARGS)) == 1
    assert "already exists" in capsys.readouterr().err
    assert existing.read_text(encoding="utf-8") == "earlier\n" and env.requests == []


def test_a_crash_during_a_budget_run_keeps_the_records_already_paid_for(env):
    def answer(request, n):
        if n == 3:
            raise RuntimeError("a bug on the third request")
        return httpx.Response(200, headers={"x-ms-region": "Australia East"}, json=USAGE)

    env.respond = answer
    assert cli.main(_argv(env, "minute-budget", *BUDGET_ARGS)) == 1       # a bug: reported, exit 1
    # B1's burst is sent at once, so the other 39 still went out and were paid for: all are kept.
    assert [r["status"] for r in _lines(env.out / "budget-minute.jsonl")] == [200] * 39
    assert not (env.out / "check-b1.json").exists()


def test_ctrl_c_during_a_retry_after_sleep_keeps_the_records_already_paid_for(env, monkeypatch):
    def interrupted(seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_sleep_sync", interrupted)
    _day_responses(env, answers={3: _gateway_429})

    with pytest.raises(KeyboardInterrupt):
        cli.main(_argv(env, "day-budget", *BUDGET_ARGS))

    assert [r["status"] for r in _lines(env.out / "budget-day.jsonl")] == [200, 200, 429]
    assert not (env.out / "check-b2.json").exists()


# --- access -----------------------------------------------------------------------------------

def test_access_needs_no_freeze_and_sends_no_token_then_a_token_for_the_wrong_audience(env, capsys):
    env.freeze_file.write_text('{"region_signal": null}', encoding="utf-8")
    env.porcelain = " M x\n"
    env.respond = lambda request, n: httpx.Response(401, json={})

    assert cli.main(_argv(env, "access", "--base-url", BASE)) == 0

    first, second = env.requests
    assert "authorization" not in first.headers
    assert second.headers["authorization"] == f"Bearer {TOKEN}"
    assert _Tokens.created == [("https://ai.azure.com/.default", TENANT)]
    assert json.loads((env.out / "check-b4.json").read_text(encoding="utf-8"))["passed"] is True
    assert "B4: pass" in capsys.readouterr().out


def test_access_fails_when_the_gateway_lets_a_call_through(env):
    env.respond = lambda request, n: httpx.Response(401 if n == 1 else 200, json=USAGE)
    assert cli.main(_argv(env, "access", "--base-url", BASE)) == 1
    assert json.loads((env.out / "check-b4.json").read_text(encoding="utf-8"))["passed"] is False


# --- smoke ------------------------------------------------------------------------------------

SMOKE_ARGS = ("--direct-url", DIRECT, "--gateway-url", BASE, "--scope", SCOPE)


def _labelled(request, n):
    return httpx.Response(200, headers={"x-ms-region": "Southeast Asia", "x-releaselens-backend": "secondary"},
                          json=USAGE)


def test_smoke_calls_direct_then_the_gateway_then_revision_2_before_any_freeze(env, capsys):
    env.freeze_file.write_text('{"region_signal": null}', encoding="utf-8")
    env.respond = _labelled

    assert cli.main(_argv(env, "smoke", *SMOKE_ARGS)) == 0

    assert [str(r.url) for r in env.requests] == [
        DIRECT + "chat/completions", BASE + "chat/completions",
        "https://gateway.example.com/openai/v1;rev=2/chat/completions"]
    assert _Tokens.created == [("https://ai.azure.com/.default", TENANT), (SCOPE, TENANT)]
    assert env.git_calls == []          # not measured: no tree check, and the output is outside the repository
    out = capsys.readouterr().out
    for expected in ("smoke-direct", "smoke-gateway", "smoke-rev2", "x-ms-region", "x-releaselens-backend"):
        assert expected in out
    results = json.loads((env.out / "check-smoke.json").read_text(encoding="utf-8"))
    assert [r["check"] for r in results] == ["smoke-direct", "smoke-gateway", "smoke-rev2"]


def test_smoke_records_its_two_gateway_calls_usage_but_not_the_direct_one(env):
    env.respond = _labelled
    cli.main(_argv(env, "smoke", *SMOKE_ARGS))
    records = _lines(env.out / "smoke.jsonl")
    assert [(r["status"], r["prompt_tokens"] + r["completion_tokens"], r["caller"]) for r in records] == [
        (200, 320, "owner")] * 2
    text = (env.out / "smoke.jsonl").read_text(encoding="utf-8")
    for secret in (TOKEN, "example.com", TENANT, OWNER_OID):
        assert secret not in text


def test_smoke_fails_and_withholds_a_hostname_in_the_gateways_answer(env, capsys):
    host = "aoai-releaselens-sea-a1b2c3.openai.azure.com"
    env.respond = lambda request, n: httpx.Response(200, json={"error": host}) if n == 2 else _labelled(request, n)

    assert cli.main(_argv(env, "smoke", *SMOKE_ARGS)) == 1

    seen = capsys.readouterr()
    assert host not in seen.out + seen.err and host not in _everything_written(env)
    results = json.loads((env.out / "check-smoke.json").read_text(encoding="utf-8"))
    assert [r["passed"] for r in results] == [True, False, True]


# --- metric-totals ----------------------------------------------------------------------------

def _metric_response(owner: float, deploy: float | None = None):
    rows = [[OWNER_OID, owner]] + ([[DEPLOY_OID, deploy]] if deploy is not None else [])
    table = {"tables": [{"columns": [{"name": "Caller"}, {"name": "total"}], "rows": rows}]}
    return lambda request, n: httpx.Response(200, json=table)


def _record_file(env: _Env, name: str, tokens: list[int]) -> None:
    env.out.mkdir(exist_ok=True)
    (env.out / name).write_text("".join(
        json.dumps({"seq": i, "status": 200, "prompt_tokens": t, "completion_tokens": 0, "caller": "owner"}) + "\n"
        for i, t in enumerate(tokens)), encoding="utf-8")


METRIC_ARGS = ("--app-id", APP, "--since", "2026-10-09T01:30:00Z")


def test_metric_totals_compares_the_clients_records_with_the_metric_by_label(env, capsys):
    _record_file(env, "failover-gateway.jsonl", [320] * 3)
    _record_file(env, "budget-minute.jsonl", [320] * 2)
    env.respond = _metric_response(1600.0 * 1.019, 320.0)

    code = cli.main(_argv(env, "metric-totals", *METRIC_ARGS, "--client-total", "deploy=320"))

    assert code == 0
    (request,) = env.requests
    assert request.url.host == "api.applicationinsights.io"
    assert _Tokens.created == [("https://api.applicationinsights.io/.default", TENANT)]
    out = capsys.readouterr().out
    assert "owner" in out and "deploy" in out and "B5: pass" in out
    assert OWNER_OID not in out and APP not in out
    assert json.loads((env.out / "check-b5.json").read_text(encoding="utf-8"))["passed"] is True


def test_metric_totals_counts_gateway_records_by_prefix_but_not_direct_ones(env):
    _record_file(env, "failover-gateway.jsonl", [500])
    _record_file(env, "failover-gateway-crashed.jsonl", [300])
    _record_file(env, "budget-minute-1.jsonl", [200])
    _record_file(env, "budget-day.jsonl", [100])
    _record_file(env, "budget-day-crashed.jsonl", [40])
    _record_file(env, "smoke-2.jsonl", [20])
    _record_file(env, "failover-direct.jsonl", [9999])
    env.respond = _metric_response(1160.0)

    assert cli.main(_argv(env, "metric-totals", *METRIC_ARGS)) == 0


def test_metric_totals_counts_the_smoke_calls_the_gateway_answered(env):
    _record_file(env, "failover-gateway.jsonl", [1000])
    _record_file(env, "smoke.jsonl", [320, 320])
    env.respond = _metric_response(1640.0)
    assert cli.main(_argv(env, "metric-totals", *METRIC_ARGS)) == 0


def test_a_hand_entered_total_is_labelled_as_from_the_workflow_log_in_the_result(env, capsys):
    _record_file(env, "failover-gateway.jsonl", [1000])
    env.respond = _metric_response(1000.0, 320.0)

    assert cli.main(_argv(env, "metric-totals", *METRIC_ARGS, "--client-total", "deploy=320")) == 0

    detail = json.loads((env.out / "check-b5.json").read_text(encoding="utf-8"))["detail"]
    assert "deploy: from the workflow log, entered by hand" in detail
    assert "owner: from the workflow log" not in detail
    assert "from the workflow log, entered by hand" in capsys.readouterr().out


def test_a_total_entered_by_hand_for_a_caller_that_has_records_is_refused_before_any_request(env, capsys):
    _record_file(env, "failover-gateway.jsonl", [1000])
    assert cli.main(_argv(env, "metric-totals", *METRIC_ARGS, "--client-total", "owner=5")) == 1
    assert "owner" in capsys.readouterr().err
    assert env.requests == [] and _Tokens.created == []


def test_the_same_label_entered_twice_by_hand_is_refused(env):
    _record_file(env, "failover-gateway.jsonl", [1000])
    assert cli.main(_argv(env, "metric-totals", *METRIC_ARGS, "--client-total", "deploy=1",
                          "--client-total", "deploy=2")) == 1
    assert env.requests == []


def test_the_report_shows_the_hand_entered_label_from_the_b5_result(env):
    _both_runs(env)
    env.respond = _metric_response(1000.0, 320.0)
    _record_file(env, "failover-gateway.jsonl", [1000])
    cli.main(_argv(env, "metric-totals", *METRIC_ARGS, "--client-total", "deploy=320"))
    _both_runs(env)
    assert cli.main(_report_argv(env, "--b3", "passed")) == 0
    assert "deploy: from the workflow log, entered by hand" in (env.out / "report.md").read_text(encoding="utf-8")


def test_metric_totals_fails_outside_two_percent(env):
    _record_file(env, "failover-gateway.jsonl", [1000])
    env.respond = _metric_response(1021.0)
    assert cli.main(_argv(env, "metric-totals", *METRIC_ARGS)) == 1
    assert json.loads((env.out / "check-b5.json").read_text(encoding="utf-8"))["passed"] is False


def test_an_oid_without_a_label_is_other_and_fails_the_comparison(env, capsys):
    _record_file(env, "failover-gateway.jsonl", [1000])
    stranger = "44444444-4444-4444-4444-444444444444"
    table = {"tables": [{"columns": [{"name": "Caller"}, {"name": "total"}],
                         "rows": [[OWNER_OID, 1000], [stranger, 50]]}]}
    env.respond = lambda request, n: httpx.Response(200, json=table)

    assert cli.main(_argv(env, "metric-totals", *METRIC_ARGS)) == 1
    seen = capsys.readouterr()
    assert "other" in seen.out and stranger not in seen.out + seen.err + _everything_written(env)


def test_metric_totals_needs_the_labels_variable_and_records_before_any_request(env, capsys, monkeypatch):
    monkeypatch.delenv("GATEWAY_CALLER_LABELS")
    _record_file(env, "failover-gateway.jsonl", [1])
    assert cli.main(_argv(env, "metric-totals", *METRIC_ARGS)) == 1
    assert "GATEWAY_CALLER_LABELS" in capsys.readouterr().err

    monkeypatch.setenv("GATEWAY_CALLER_LABELS", LABELS)
    (env.out / "failover-gateway.jsonl").unlink()
    assert cli.main(_argv(env, "metric-totals", *METRIC_ARGS)) == 1
    assert env.requests == [] and _Tokens.created == []


def test_a_query_failure_is_reported_without_the_url_the_token_or_the_app_id(env, capsys):
    _record_file(env, "failover-gateway.jsonl", [1])
    env.respond = lambda request, n: httpx.Response(403, json={"error": TOKEN})
    assert cli.main(_argv(env, "metric-totals", *METRIC_ARGS)) == 1
    seen = capsys.readouterr()
    for secret in (TOKEN, APP, "applicationinsights"):
        assert secret not in seen.out + seen.err


# --- report -----------------------------------------------------------------------------------

def _run_file(env: _Env, name: str, statuses: list[int | None], regions: list[str | None]) -> None:
    env.out.mkdir(exist_ok=True)
    (env.out / name).write_text("".join(json.dumps({
        "seq": i, "sent_at": 4.0 * i, "status": s, "latency_ms": 100.0 + i, "region": r, "model_called": s == 200,
        "waited_ms": 0, "prompt_tokens": 300, "completion_tokens": 20, "caller": "owner"}) + "\n"
        for i, (s, r) in enumerate(zip(statuses, regions, strict=True))), encoding="utf-8")


def _both_runs(env: _Env) -> None:
    _run_file(env, "failover-direct.jsonl", [429] * 20 + [200] * 25, [None] * 20 + ["primary"] * 25)
    _run_file(env, "failover-gateway.jsonl", [200] * 45, ["primary"] * 20 + ["secondary"] * 25)


def _report_argv(env: _Env, *extra: str) -> list[str]:
    return ["report", "--dir", str(env.out), *extra]


def test_report_writes_the_verdict_the_checks_the_freeze_and_the_commit(env, capsys):
    _both_runs(env)
    (env.out / "check-b1.json").write_text(json.dumps({"check": "B1", "passed": True, "detail": "refused"}),
                                           encoding="utf-8")

    assert cli.main(_report_argv(env, "--b3", "passed")) == 0

    text = (env.out / "report.md").read_text(encoding="utf-8")
    assert "Verdict: held\n" in text and COMMIT in text and "x-ms-region" in text
    assert "B1: pass" in text and "B2: not run" in text and "B3: pass" in text and "entered by hand" in text
    assert ["git", "rev-parse", "HEAD"] in env.git_calls


def test_report_needs_b3_entered_as_passed_or_failed(env):
    _both_runs(env)
    with pytest.raises(SystemExit):
        cli.main(_report_argv(env))
    with pytest.raises(SystemExit):
        cli.main(_report_argv(env, "--b3", "maybe"))


def test_report_refuses_while_the_signal_is_not_frozen(env, capsys):
    _both_runs(env)
    env.freeze_file.write_text('{"region_signal": null}', encoding="utf-8")
    assert cli.main(_report_argv(env, "--b3", "passed")) == 1
    assert "freeze.json" in capsys.readouterr().err and not (env.out / "report.md").exists()


def test_report_needs_both_runs_records(env, capsys):
    _run_file(env, "failover-direct.jsonl", [200], ["primary"])
    assert cli.main(_report_argv(env, "--b3", "passed")) == 1
    assert "failover-gateway.jsonl" in capsys.readouterr().err


def test_report_does_not_need_a_clean_tree(env):
    _both_runs(env)
    env.porcelain = " M x\n"
    assert cli.main(_report_argv(env, "--b3", "failed")) == 0
    assert "B3: fail" in (env.out / "report.md").read_text(encoding="utf-8")


def test_report_refuses_to_write_a_guid_from_a_check_result_and_says_no_more(env, capsys):
    _both_runs(env)
    leaked = "55555555-5555-5555-5555-555555555555"
    (env.out / "check-b1.json").write_text(json.dumps({"check": "B1", "passed": True, "detail": leaked}),
                                           encoding="utf-8")
    assert cli.main(_report_argv(env, "--b3", "passed")) == 1
    seen = capsys.readouterr()
    assert leaked not in seen.out + seen.err and not (env.out / "report.md").exists()


@pytest.mark.parametrize("spelling", [OWNER_OID, OWNER_OID.replace("-", "")])
def test_report_refuses_an_oid_from_the_labels_variable_in_either_spelling(env, spelling):
    _both_runs(env)
    (env.out / "check-b1.json").write_text(json.dumps({"check": "B1", "passed": True, "detail": spelling}),
                                           encoding="utf-8")
    assert cli.main(_report_argv(env, "--b3", "passed")) == 1
    assert not (env.out / "report.md").exists()


# --- everything the harness writes ------------------------------------------------------------

def test_the_budget_files_hold_no_token_oid_url_or_hostname(env):
    env.respond = lambda request, n: _gateway_429(request, n) if n == 2 else httpx.Response(
        200, headers={"x-ms-region": "Australia East"}, json=USAGE)
    cli.main(_argv(env, "minute-budget", *BUDGET_ARGS))
    text = _everything_written(env)
    for secret in (TOKEN, "example.com", TENANT, "22222222", OWNER_OID, "gateway"):
        assert secret not in text
