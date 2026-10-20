"""Supply-chain and CI trigger boundaries for the separate P8 agent image."""
import hashlib
import json
import re
import importlib.util
import io
import zipfile

import pytest
import yaml


def test_delivery_image_contract(repo_root):
    base = repo_root / "build/delivery-agents"
    versions = json.loads((base / "versions.json").read_text())
    assert versions["base"] == "ghcr.io/dmitrylyubaev/releaselens-agent@sha256:3167931608ce2390fdc565d737074c9aaa57098f2099aabffe9f2fc431d94c82"
    assert versions["terraform_version"] == "1.15.8" and versions["azure_cli_version"] == "2.81.0"
    for name in ("requirements.lock","cli-requirements.lock"):
        content = (base / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == versions[name.replace(".lock","_sha256")]
        assert b"--hash=sha256:" in content and b"==" in content
    assert re.fullmatch(r"[0-9a-f]{64}",versions["terraform_sha256"])
    docker = (base / "Dockerfile").read_text()
    assert versions["base"] in docker and "USER 10001:10001" in docker
    assert "/opt/p8-cli" in docker and "/opt/p8-workload" in docker
    assert "--require-hashes" in docker and "sha256" in docker
    assert "P8Agent=ReleaseLens" in docker and not re.search(r"^ENTRYPOINT",docker,re.M)
    smoke = (base / "smoke.sh").read_text()
    assert "docker.sock" not in smoke and "pip install" not in smoke
    for command in ["az version","terraform version","test_agents_runtime.py","tests/delivery_search"]:
        assert command in smoke


def test_delivery_image_workflow_only_publishes_smoked_main(repo_root):
    path = repo_root / ".github/workflows/delivery-agent-image.yml"
    w = yaml.safe_load(path.read_bytes())
    assert set(w.get("on",w.get(True))) == {"pull_request","workflow_dispatch"}
    assert w["permissions"] == {"contents":"read"}
    assert "environment" not in json.dumps(w)
    publish = w["jobs"]["publish"]
    assert publish["needs"] == "validate" and "refs/heads/main" in publish["if"] and "workflow_dispatch" in publish["if"]
    for job in w["jobs"].values():
        assert "id-token" not in job.get("permissions",{})
        for step in job["steps"]:
            if "uses" in step: assert re.fullmatch(r"[^@]+@[0-9a-f]{40}",step["uses"])
    steps = publish["steps"]
    smoke = next(i for i,s in enumerate(steps) if s.get("name") == "Smoke the exact image before publication")
    push = next(i for i,s in enumerate(steps) if s.get("name") == "Publish reviewed delivery image")
    assert smoke < push
    assert "build/delivery-agents/smoke.sh" in steps[smoke]["run"]


def test_new_terraform_roots_are_in_credential_free_ci(repo_root):
    w = yaml.safe_load((repo_root / ".github/workflows/ci.yml").read_bytes())
    steps = w["jobs"]["terraform-plan"]["steps"]
    for root in ("platform","service","index"):
        step = next(s for s in steps if s.get("working-directory") == "infra/pipelines/"+root)
        assert "-backend=false" in step["run"] and "terraform test" in step["run"]


def test_ci_installs_delivery_imports_before_infra_collection(repo_root):
    workflow=yaml.safe_load((repo_root/".github/workflows/ci.yml").read_bytes())
    steps=workflow["jobs"]["build-and-test"]["steps"]
    install=next(i for i,step in enumerate(steps) if step.get("name")=="Install eval requirements")
    infra=next(i for i,step in enumerate(steps) if step.get("name")=="Infra tests")
    assert install<infra


def test_installer_rejects_changed_lock_before_download(repo_root,tmp_path):
    spec = importlib.util.spec_from_file_location("p8_install",repo_root / "build/delivery-agents/install.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    (tmp_path / "requirements.lock").write_text("changed")
    (tmp_path / "cli-requirements.lock").write_text("changed")
    (tmp_path / "versions.json").write_text(json.dumps({"requirements_sha256":"a"*64,"cli-requirements_sha256":"b"*64}))
    with pytest.raises(ValueError): module.verify_inputs(tmp_path)


def test_terraform_download_wrong_digest_creates_no_executable(repo_root,tmp_path):
    spec = importlib.util.spec_from_file_location("p8_install",repo_root / "build/delivery-agents/install.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload,"w") as z: z.writestr("terraform",b"untrusted program")
    class Reply(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self,*args): self.close()
    with pytest.raises(ValueError):
        module.install_terraform({"terraform_version":"1.15.8","terraform_sha256":"a"*64},
            tmp_path / "terraform",opener=lambda *a,**kw: Reply(payload.getvalue()))
    assert not (tmp_path / "terraform").exists()


def test_actual_container_versions_emit_a_bound_receipt(repo_root):
    spec=importlib.util.spec_from_file_location("p8_receipt",repo_root/"build/delivery-agents/toolchain_receipt.py")
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    receipt=module.create_receipt("a"*40,"sha256:"+"b"*64,{"azure-cli":"2.81.0"},
        {"terraform_version":"1.15.8"},"5.280.0",(3,12))
    assert receipt["source_sha"]=="a"*40 and receipt["versions"]["agent"]=="5.280.0"
    with pytest.raises(ValueError): module.create_receipt("a"*40,"sha256:"+"b"*64,
        {"azure-cli":"2.81.0"},{"terraform_version":"1.15.8"},"5.280.0",(3,14))
