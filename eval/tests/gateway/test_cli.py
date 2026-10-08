"""`python -m app.gateway failover`: the guard comes before any request, and what the run writes.

No network and no real time: the transport is an `httpx.MockTransport`, the token source is a
fake, the clock and sleep are fakes, git is a stub, and truststore is not injected.
"""

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from app.gateway import __main__ as cli
from app.gateway import freeze

BASE = "https://gateway.example.com/openai/v1/"
SCOPE = "api://22222222-2222-2222-2222-222222222222/.default"
TENANT = "00000000-0000-0000-0000-000000000000"
TOKEN = "fake-token-not-real"


class _Clock:
    now = 1000.0   # not zero: the run's own clock must start at zero whatever the machine's says

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class _Tokens:
    created: list[tuple[str, str]] = []

    def __init__(self, scope: str, tenant_id: str) -> None:
        _Tokens.created.append((scope, tenant_id))

    def token(self) -> str:
        return TOKEN


class _Env:
    """Everything the CLI reaches for, replaced."""

    def __init__(self, tmp_path: Path) -> None:
        self.requests: list[httpx.Request] = []
        self.git_calls: list[list[str]] = []
        self.porcelain = ""
        self.ignored = True
        self.freeze_file = tmp_path / "freeze.json"
        self.freeze_file.write_text('{"region_signal": "x-ms-region"}', encoding="utf-8")
        self.out = tmp_path / "out"
        self.injected: list[bool] = []

    def run(self, command, **kwargs):
        self.git_calls.append(command)
        if command[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(command, 0, self.porcelain, "")
        return subprocess.CompletedProcess(command, 0 if self.ignored else 1, "", "")

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        region = "Southeast Asia" if len(self.requests) % 2 == 0 else "Australia East"
        return httpx.Response(200, headers={"x-ms-region": region},
                              json={"usage": {"prompt_tokens": 300, "completion_tokens": 20}})


@pytest.fixture
def env(tmp_path, monkeypatch):
    env = _Env(tmp_path)
    clock = _Clock()
    _Tokens.created = []
    monkeypatch.setattr(cli, "TokenSource", _Tokens)
    monkeypatch.setattr(cli, "FREEZE_FILE", env.freeze_file)
    monkeypatch.setattr(cli, "_run", env.run)
    monkeypatch.setattr(cli, "_now", clock)
    monkeypatch.setattr(cli, "_sleep", clock.sleep)
    monkeypatch.setattr(cli, "_http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(env.handler)))
    monkeypatch.setattr(cli.truststore, "inject_into_ssl", lambda: env.injected.append(True))
    return env


def _argv(env: _Env, *extra: str, mode: str = "direct") -> list[str]:
    return ["failover", "--mode", mode, "--out", str(env.out), "--tenant", TENANT, "--base-url", BASE, *extra]


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_measured_run_refuses_without_a_frozen_region_signal(env, capsys):
    env.freeze_file.write_text('{"region_signal": null}', encoding="utf-8")

    assert cli.main(_argv(env)) == 1

    assert "freeze.json" in capsys.readouterr().err
    assert env.requests == [] and _Tokens.created == [] and not env.out.exists()


def test_a_measured_run_refuses_a_dirty_tree(env, capsys):
    env.porcelain = " M eval/app/gateway/rule.py\n"

    assert cli.main(_argv(env)) == 1

    assert "not clean" in capsys.readouterr().err
    assert env.requests == [] and _Tokens.created == [] and not env.out.exists()


def test_the_unfrozen_signal_is_reported_before_the_tree_is_asked(env):
    env.freeze_file.write_text('{"region_signal": null}', encoding="utf-8")
    cli.main(_argv(env))
    assert env.git_calls == []


def test_an_output_directory_git_would_see_is_refused_before_any_request(env, capsys, monkeypatch):
    env.ignored = False
    inside = freeze.REPO_ROOT / "eval" / "gateway-out-not-real"
    assert cli.main(["failover", "--mode", "direct", "--out", str(inside), "--tenant", TENANT,
                     "--base-url", BASE]) == 1
    assert "not git-ignored" in capsys.readouterr().err
    assert env.requests == [] and not inside.exists()


def test_direct_mode_runs_the_workload_and_writes_the_records(env, capsys):
    assert cli.main(_argv(env, "--deployment", "dep-not-real")) == 0

    assert _Tokens.created == [("https://ai.azure.com/.default", TENANT)]
    assert len(env.requests) == 45
    assert {str(r.url) for r in env.requests} == {BASE + "chat/completions"}
    assert {r.headers["authorization"] for r in env.requests} == {f"Bearer {TOKEN}"}
    assert {json.loads(r.content)["model"] for r in env.requests} == {"dep-not-real"}

    records = _lines(env.out / "failover-direct.jsonl")
    assert [r["seq"] for r in records] == list(range(45))
    assert [r["sent_at"] for r in records] == [4.0 * n for n in range(45)]   # on the run's own clock
    assert {r["status"] for r in records} == {200}
    assert {r["region"] for r in records} == {"primary", "secondary"}
    assert {r["caller"] for r in records} == {"owner"}
    assert all(r["model_called"] and r["prompt_tokens"] == 300 for r in records)

    out = capsys.readouterr().out
    assert "45" in out and "failover-direct.jsonl" in out


def test_the_default_deployment_is_the_failover_test_one(env):
    cli.main(_argv(env))
    assert {json.loads(r.content)["model"] for r in env.requests} == {"releaselens-chat-failover-test"}


def test_gateway_mode_takes_its_scope_from_the_flag_and_names_its_file(env):
    assert cli.main(_argv(env, "--scope", SCOPE, mode="gateway")) == 0

    assert _Tokens.created == [(SCOPE, TENANT)]
    assert len(_lines(env.out / "failover-gateway.jsonl")) == 45


def test_gateway_mode_without_a_scope_is_refused_before_any_request(env, capsys):
    assert cli.main(_argv(env, mode="gateway")) == 1
    assert "--scope" in capsys.readouterr().err
    assert env.requests == [] and not env.out.exists()


def test_a_run_never_overwrites_an_earlier_one(env, capsys):
    env.out.mkdir()
    existing = env.out / "failover-direct.jsonl"
    existing.write_text("earlier\n", encoding="utf-8")

    assert cli.main(_argv(env)) == 1

    assert "already exists" in capsys.readouterr().err
    assert existing.read_text(encoding="utf-8") == "earlier\n" and env.requests == []


def test_the_records_hold_no_token_oid_url_or_hostname(env):
    cli.main(_argv(env, "--scope", SCOPE))
    text = (env.out / "failover-direct.jsonl").read_text(encoding="utf-8")
    for secret in (TOKEN, "example.com", "gateway", TENANT, "22222222", "openai"):
        assert secret not in text


def test_nothing_secret_is_printed(env, capsys):
    cli.main(_argv(env, "--scope", SCOPE))
    seen = capsys.readouterr()
    for secret in (TOKEN, "example.com", TENANT, "22222222"):
        assert secret not in seen.out + seen.err


def test_a_timeout_run_still_writes_45_records(env):
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    env.handler = timeout
    assert cli.main(_argv(env)) == 0
    assert {r["status"] for r in _lines(env.out / "failover-direct.jsonl")} == {None}


def test_truststore_is_injected_before_the_network_is_used(env):
    cli.main(_argv(env))
    assert env.injected == [True]


def test_a_failure_that_is_a_bug_is_reported_with_its_traceback_and_a_nonzero_exit(env, capsys, monkeypatch):
    def boom(self, *args, **kwargs):
        raise RuntimeError("a bug")

    monkeypatch.setattr(_Tokens, "token", boom)
    assert cli.main(_argv(env)) == 1
    assert "RuntimeError" in capsys.readouterr().err
