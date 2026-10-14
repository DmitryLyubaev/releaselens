"""Agent lifecycle tests use fake child processes, never account credentials."""
import importlib.util
import os
from pathlib import Path
import signal

import pytest


@pytest.fixture
def runtime(repo_root):
    spec = importlib.util.spec_from_file_location("agent_start", repo_root / "build/agents/start.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def harness(monkeypatch, runtime, codes=(0, 0, 0)):
    calls, handlers, signals = [], {}, []
    returns = iter(codes)

    class Child:
        pid = 123456
        def __init__(self, argv, **kw):
            self.code = next(returns)
            calls.append((argv, {**kw, "env": dict(kw["env"])}))
        def wait(self, timeout=None):
            if self.code == "signal":
                self.code = 0
                handlers[signal.SIGTERM](signal.SIGTERM, None)
            return self.code
        def poll(self):
            return None
        def kill(self):
            signals.append("kill")
        def send_signal(self, signum):
            signals.append(signum)

    monkeypatch.setattr(runtime.subprocess, "Popen", Child)
    monkeypatch.setattr(runtime.signal, "signal", lambda sig, fn: handlers.setdefault(sig, fn))
    monkeypatch.setattr(runtime.os, "killpg", lambda pid, sig: signals.append(sig), raising=False)
    return calls, signals


def env(mode="worker"):
    return {"AZP_URL": "https://dev.azure.com/example", "AZP_POOL": "demo",
            "AZP_TOKEN": "synthetic-secret-value", "P7_SESSION": "demo-test",
            "P7_MODE": mode, "P7Agent": "ReleaseLens", "PATH": os.environ["PATH"]}


def test_worker_runs_once_and_unregisters_exact_name(runtime, monkeypatch, tmp_path):
    calls, _ = harness(monkeypatch, runtime)
    assert runtime.run_agent(tmp_path, env(), "nonce1") == 0
    assert len(calls) == 3
    assert calls[1][0][-1] == "--once"
    assert "remove" in calls[2][0]
    assert "--replace" not in calls[0][0]
    assert calls[0][1]["env"]["VSTS_AGENT_INPUT_AGENT"] == "p7-demo-test-worker-nonce1"
    assert calls[0][1]["cwd"] == tmp_path
    assert calls[2][1]["cwd"] == tmp_path


def test_failed_registration_does_not_remove_another_agent(runtime, monkeypatch, tmp_path):
    calls, _ = harness(monkeypatch, runtime, (9,))
    assert runtime.run_agent(tmp_path, env(), "n") == 9
    assert len(calls) == 1


def test_listener_failure_is_preserved(runtime, monkeypatch, tmp_path):
    calls, _ = harness(monkeypatch, runtime, (0, 17, 0))
    assert runtime.run_agent(tmp_path, env(), "n") == 17
    assert len(calls) == 3


def test_term_forwards_signal_and_bounds_cleanup(runtime, monkeypatch, tmp_path):
    calls, signals = harness(monkeypatch, runtime, (0, "signal", 0))
    assert runtime.run_agent(tmp_path, env(), "n") == 143
    assert signal.SIGTERM in signals
    assert len(calls) == 3
    assert runtime.CLEANUP_TIMEOUT == 30


def test_placeholder_registers_offline_without_listener(runtime, monkeypatch, tmp_path):
    calls, _ = harness(monkeypatch, runtime, (0,))
    assert runtime.run_agent(tmp_path, env("placeholder"), "n") == 0
    assert len(calls) == 1
    assert calls[0][1]["env"]["VSTS_AGENT_INPUT_AGENT"] == "p7-demo-test-placeholder"


def test_pat_never_enters_argv_stdout_or_capabilities(runtime, monkeypatch, tmp_path, capsys):
    calls, _ = harness(monkeypatch, runtime)
    assert runtime.run_agent(tmp_path, env(), "n") == 0
    assert "synthetic-secret-value" not in repr([c[0] for c in calls])
    assert "synthetic-secret-value" not in capsys.readouterr().out
    assert "AZP_TOKEN" not in calls[1][1]["env"]
    assert "VSTS_AGENT_INPUT_TOKEN" not in calls[1][1]["env"]
    assert {"AZP_TOKEN", "VSTS_AGENT_INPUT_TOKEN"} <= set(calls[1][1]["env"]["VSO_AGENT_IGNORE"].split(","))


def test_two_workers_have_distinct_names(runtime):
    assert runtime.agent_name("test", "worker", "a") != runtime.agent_name("test", "worker", "b")


@pytest.mark.parametrize("key,value", [("AZP_TOKEN", ""), ("P7_MODE", "forever"), ("P7_SESSION", "../bad"), ("P7Agent", "wrong")])
def test_invalid_input_never_starts_child(runtime, monkeypatch, tmp_path, key, value):
    calls, _ = harness(monkeypatch, runtime, ())
    settings = env()
    settings[key] = value
    assert runtime.run_agent(tmp_path, settings, "n") == 2
    assert not calls
