import json
import subprocess
import pytest
from app.webapp.private_az import PrivateAz, TransportRefused

TENANT = "00000000-0000-0000-0000-000000000001"
SUB = "00000000-0000-0000-0000-000000000002"
PRINCIPAL = "owner@example.test"


def account(): return {"tenantId": TENANT, "id": SUB, "user": {"name": PRINCIPAL, "type": "user"}}


@pytest.fixture(autouse=True)
def private_work_domain(monkeypatch): monkeypatch.setenv("WORK_DOMAIN", "work.example.test")


@pytest.mark.parametrize("kind", ["tenant", "subscription", "principal", "work_account", "missing_work_domain"])
def test_wrong_account_stops_before_operation_or_token(kind, monkeypatch):
    payload = account()
    if kind == "tenant": payload["tenantId"] = "wrong"
    elif kind == "subscription": payload["id"] = "wrong"
    elif kind == "principal": payload["user"]["name"] = "other@example.test"
    elif kind == "work_account": monkeypatch.setenv("WORK_DOMAIN", "example.test")
    else: monkeypatch.delenv("WORK_DOMAIN")
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
    with pytest.raises(TransportRefused, match="account_refused"):
        PrivateAz(TENANT, SUB, PRINCIPAL, run=run).call(["account", "get-access-token"])
    assert len(calls) == 1
    assert calls[0][1:3] == ["account", "show"]


def test_checked_operation_captures_all_output_and_returns_only_in_memory(capsys):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps(account() if len(calls) == 1 else {"private": "result"}), "private error text")
    az = PrivateAz(TENANT, SUB, PRINCIPAL, run=run)
    assert az.call(["webapp", "show", "--name", "private-app"]) == {"private": "result"}
    assert len(calls) == 2
    assert all(kwargs["capture_output"] and kwargs["text"] and not kwargs["check"] for _, kwargs in calls)
    assert capsys.readouterr() == ("", "")
    assert "owner@" not in repr(az) and TENANT not in repr(az)


@pytest.mark.parametrize("failure", ["exit", "json", "exception", "timeout"])
def test_cli_failure_never_exposes_arguments_or_response(failure, capsys):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if len(calls) == 1: return subprocess.CompletedProcess(args, 0, json.dumps(account()), "")
        if failure == "exception": raise OSError("private host token")
        if failure == "timeout": raise subprocess.TimeoutExpired(args, 1, output="private token")
        return subprocess.CompletedProcess(args, 1 if failure == "exit" else 0, "private invalid-json", "private error")
    with pytest.raises(TransportRefused) as error: PrivateAz(TENANT, SUB, PRINCIPAL, run=run).call(["webapp", "show", "private-host"])
    assert "private" not in str(error.value)
    assert error.value.__cause__ is None
    assert capsys.readouterr() == ("", "")


def test_ci_service_principal_guard_does_not_need_owner_domain(monkeypatch):
    monkeypatch.delenv("WORK_DOMAIN")
    client = "00000000-0000-0000-0000-000000000003"
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        payload = account() | {"user": {"name": client, "type": "servicePrincipal"}} if len(calls) == 1 else {}
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
    assert PrivateAz(TENANT, SUB, client, run=run).call(["webapp", "show"]) == {}
