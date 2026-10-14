"""Mocked secret upload boundary and isolated Terraform contracts."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def seed(repo_root):
    spec = importlib.util.spec_from_file_location("seed_pat", repo_root / "scripts/agents/seed_pat.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def config(tmp_path):
    pat = tmp_path / "pat.txt"
    pat.write_text("synthetic-private-pat", encoding="utf-8")
    return {"vault_name": "demo-vault", "subscription": "sub", "tenant": "tenant",
            "owner": "owner@example.com", "pat_file": str(pat), "receipt": str(tmp_path / "receipt.json"),
            "session": "session-1", "source": "a" * 40, "forbidden_domains": ["example-employer.invalid"]}


def runner(account=None, upload_rc=0):
    calls = []
    account = account or {"id": "sub", "tenantId": "tenant", "state": "Enabled",
                          "user": {"type": "user", "name": "owner@example.com"}}
    def run(args, **kw):
        calls.append(args)
        if args[1:3] == ["account", "show"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(account), stderr="")
        return SimpleNamespace(returncode=upload_rc, stdout="", stderr="synthetic-private-pat raw private error")
    return run, calls


def test_private_file_upload_never_passes_pat_value(seed, config, capsys):
    run, calls = runner()
    assert seed.seed(config, run=run) is True
    assert calls[0][1:3] == ["account", "show"]
    assert "--file" in calls[1] and "--value" not in calls[1]
    assert "synthetic-private-pat" not in repr(calls)
    receipt = Path(config["receipt"]).read_text()
    assert "synthetic-private-pat" not in receipt
    assert "synthetic-private-pat" not in capsys.readouterr().out


@pytest.mark.parametrize("change", ["subscription", "tenant", "owner", "work", "principal"])
def test_wrong_account_never_uploads(seed, config, change):
    a = {"id": "sub", "tenantId": "tenant", "state": "Enabled",
         "user": {"type": "user", "name": "owner@example.com"}}
    if change == "subscription": a["id"] = "other"
    if change == "tenant": a["tenantId"] = "other"
    if change == "owner": a["user"]["name"] = "different@example.com"
    if change == "work":
        a["user"]["name"] = config["owner"] = "work@example-employer.invalid"
    if change == "principal": a["user"]["type"] = "servicePrincipal"
    run, calls = runner(a)
    assert seed.seed(config, run=run) is False
    assert len(calls) == 1


@pytest.mark.parametrize("value", ["", " \n", "pat\n", "one\ntwo"])
def test_invalid_pat_does_not_upload(seed, config, value):
    Path(config["pat_file"]).write_text(value)
    run, calls = runner()
    assert seed.seed(config, run=run) is False
    assert not any("keyvault" in c for c in calls)


def test_missing_file_does_not_upload(seed, config):
    Path(config["pat_file"]).unlink()
    run, calls = runner()
    assert seed.seed(config, run=run) is False
    assert not any("keyvault" in c for c in calls)


def test_unowned_receipt_never_reuses_or_replaces_secret(seed, config):
    Path(config["receipt"]).write_text('{"session":"different"}')
    run, calls = runner()
    assert seed.seed(config, run=run) is False
    assert not any("keyvault" in c for c in calls)


def test_matching_receipt_is_not_replayed(seed, config):
    run, calls = runner()
    assert seed.seed(config, run=run)
    Path(config["pat_file"]).unlink()
    assert seed.seed(config, run=run)
    assert sum("keyvault" in c for c in calls) == 1


def test_role_timeout_has_no_raw_error_or_receipt(seed, config, capsys):
    run, calls = runner(upload_rc=1)
    clock = iter([0, 0, 31, 62, 93, 124])
    assert seed.seed(config, run=run, clock=lambda: next(clock), sleep=lambda _: None) is False
    assert len(calls) <= 6
    assert not Path(config["receipt"]).exists()
    assert "synthetic-private-pat" not in capsys.readouterr().out


def test_stack_has_no_secret_values_or_existing_stack_dependency(repo_root):
    directory = repo_root / "infra/agents"
    text = "\n".join(p.read_text() for p in directory.glob("*.tf"))
    assert 'resource "azurerm_container_app_job" "worker"' in text
    assert 'resource "azurerm_container_app_job" "placeholder"' in text
    assert 'azurerm_key_vault_secret' not in text
    assert 'terraform_remote_state' not in text and 'backend "azurerm"' not in text
    assert 'resource_provider_registrations = "none"' in text
    assert 'Key Vault Secrets User' in text and 'Key Vault Secrets Officer' in text
    assert 'purge_soft_delete_on_destroy    = false' in text
    assert 'depends_on = [terraform_data.pat_ready, azurerm_role_assignment.reader]' in text


def test_private_working_files_ignored(repo_root):
    assert '.local/project7/' in (repo_root / '.gitignore').read_text()


def test_upload_timeout_respects_remaining_readiness_budget(seed, config):
    values = iter([0, 119])
    run, _ = runner()
    timeouts = []
    def timed(args, **kwargs):
        if "keyvault" in args:
            timeouts.append(kwargs["timeout"])
        return run(args, **kwargs)
    assert seed.seed(config, run=timed, clock=lambda: next(values))
    assert timeouts == [1]


def test_secret_reference_is_not_hidden_from_drift_check(repo_root):
    text = (repo_root / "infra/agents/main.tf").read_text()
    assert "ignore_changes" not in text
    assert text.count('key_vault_secret_id = "${azurerm_key_vault.agent.vault_uri}secrets/azp-token"') == 2
