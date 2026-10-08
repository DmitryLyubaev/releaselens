"""The harness commands: smoke, minute-budget, day-budget, access, metric-totals, report.

No network and no real time: the transport is an `httpx.MockTransport`, the token source is a
fake, sleep is a fake, git is a stub, and truststore is not injected.
"""

import json
import subprocess
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
        self.requests.append(request)
        return self.respond(request, len(self.requests))


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


def test_minute_budget_sends_until_the_429_and_writes_its_records_and_result(env, capsys):
    env.respond = lambda request, n: _gateway_429(request, n) if n == 4 else httpx.Response(
        200, headers={"x-ms-region": "Australia East"}, json=USAGE)

    assert cli.main(_argv(env, "minute-budget", *BUDGET_ARGS)) == 0

    assert len(env.requests) == 4 and _Tokens.created == [(SCOPE, TENANT)]
    assert {str(r.url) for r in env.requests} == {BASE + "chat/completions"}
    assert {json.loads(r.content)["model"] for r in env.requests} == {"releaselens-chat"}
    assert _files(env) == ["budget-minute.jsonl", "check-b1.json"]
    records = _lines(env.out / "budget-minute.jsonl")
    assert [r["status"] for r in records] == [200, 200, 200, 429]
    assert [r["prompt_tokens"] for r in records] == [300, 300, 300, 0] and {r["caller"] for r in records} == {"owner"}
    assert records[-1]["model_called"] is False
    assert json.loads((env.out / "check-b1.json").read_text(encoding="utf-8"))["passed"] is True
    assert "B1: pass" in capsys.readouterr().out


def test_a_failed_check_still_writes_its_result_and_exits_1(env, capsys):
    env.respond = lambda request, n: httpx.Response(401, json={})
    assert cli.main(_argv(env, "minute-budget", *BUDGET_ARGS)) == 1
    assert json.loads((env.out / "check-b1.json").read_text(encoding="utf-8"))["passed"] is False
    assert "B1: fail" in capsys.readouterr().out


def test_day_budget_waits_out_each_429_and_stops_at_the_403(env):
    answers = {2: _gateway_429, 4: lambda r, n: httpx.Response(403, json={})}
    env.respond = lambda request, n: answers.get(n, lambda r, k: httpx.Response(
        200, headers={"x-ms-region": "Australia East"}, json=USAGE))(request, n)

    assert cli.main(_argv(env, "day-budget", *BUDGET_ARGS)) == 0

    assert env.sleeps == [9]
    assert [r["status"] for r in _lines(env.out / "budget-day.jsonl")] == [200, 429, 200, 403]
    assert _files(env) == ["budget-day.jsonl", "check-b2.json"]


@pytest.mark.parametrize("command", ["minute-budget", "day-budget"])
def test_a_budget_run_never_overwrites_an_earlier_one(env, capsys, command):
    env.out.mkdir()
    existing = env.out / ("budget-minute.jsonl" if command == "minute-budget" else "budget-day.jsonl")
    existing.write_text("earlier\n", encoding="utf-8")
    assert cli.main(_argv(env, command, *BUDGET_ARGS)) == 1
    assert "already exists" in capsys.readouterr().err
    assert existing.read_text(encoding="utf-8") == "earlier\n" and env.requests == []


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
