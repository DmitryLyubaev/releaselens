"""The gateway-check job holds an OIDC token for the deploy identity, so it installs a short,
hash-locked list instead of eval/requirements.txt."""

import re

import yaml

PIN = re.compile(r"^([A-Za-z0-9._-]+)==(\S+)")
HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}")


def _name(raw):
    return re.sub(r"[-_.]+", "-", raw).lower()


def _requirements(path):
    """{normalised name: (version, hash count)} for a pip requirements file."""
    text = path.read_text(encoding="utf-8").replace("\\\n", " ")
    found = {}
    for line in text.splitlines():
        match = PIN.match(line.strip())
        if match:
            found[_name(match[1])] = (match[2], len(HASH.findall(line)))
    return found


def test_gateway_check_requirements_match_the_eval_pins_and_carry_hashes(repo_root):
    locked = _requirements(repo_root / "eval" / "requirements-gateway-check.txt")
    full = _requirements(repo_root / "eval" / "requirements.txt")
    assert {"httpx", "azure-identity"} <= set(locked)
    for name, (version, hashes) in locked.items():
        assert name in full, f"{name} is not in eval/requirements.txt"
        assert version == full[name][0], f"{name}: {version} differs from eval/requirements.txt"
        assert hashes >= 1, f"{name} has no hash"


def test_gateway_check_requirements_are_well_formed_for_pip(repo_root):
    # pip reads a requirement as one pin, then its hashes on continuation lines. Any other shape
    # either fails in the workflow or installs a package without a hash check.
    text = (repo_root / "eval" / "requirements-gateway-check.txt").read_text(encoding="utf-8")
    pin_line = re.compile(r"[A-Za-z0-9._-]+==\S+ \\")
    hash_line = re.compile(r"    --hash=sha256:[0-9a-f]{64}( \\)?")
    previous_continues = False
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            assert not previous_continues, line
            continue
        assert pin_line.fullmatch(line) or hash_line.fullmatch(line), line
        assert bool(hash_line.fullmatch(line)) == previous_continues, line
        previous_continues = line.endswith("\\")
    assert not previous_continues


def test_gateway_check_workflow_installs_by_hash_from_binary_wheels_without_dependencies(repo_root):
    workflow = yaml.safe_load((repo_root / ".github" / "workflows" / "gateway-check.yml").read_bytes())
    installs = [step["run"] for step in workflow["jobs"]["check"]["steps"]
                if "pip install" in step.get("run", "")]
    assert len(installs) == 1
    install = installs[0].split()
    for flag in ("--require-hashes", "--only-binary=:all:", "--no-deps"):
        assert flag in install
    assert install[install.index("-r") + 1] == "eval/requirements-gateway-check.txt"


def test_ci_installs_the_same_list_the_same_way_in_a_clean_venv_and_imports_the_check(repo_root):
    # B3's workflow has one dispatch inside a short window and prints status=error for any failure,
    # so a package the lock file lacks must show up here, in a job with no environment and no secret.
    workflow = yaml.safe_load((repo_root / ".github" / "workflows" / "ci.yml").read_bytes())
    job = workflow["jobs"]["build-and-test"]
    assert "environment" not in job
    steps = [step for step in job["steps"] if "requirements-gateway-check.txt" in step.get("run", "")]
    assert len(steps) == 1
    run = steps[0]["run"]
    assert "-m venv" in run
    install = next(line for line in run.splitlines() if "--require-hashes" in line).split()
    for flag in ("--require-hashes", "--only-binary=:all:", "--no-deps"):
        assert flag in install
    assert install[install.index("-r") + 1] == "eval/requirements-gateway-check.txt"
    assert "-c \"import app.gateway.ci_check, azure.identity\"" in run
    # The import runs from eval/, where the harness is the top-level package `app`, and with the
    # venv's own interpreter, not the one that has every eval requirement.
    assert steps[0].get("working-directory") is None
    lines = run.splitlines()
    assert "cd eval" in [line.strip() for line in lines]
    assert lines.index(next(line for line in lines if line.strip() == "cd eval")) < lines.index(
        next(line for line in lines if "import app.gateway.ci_check" in line))
    assert "/bin/python" in next(line for line in lines if "import app.gateway.ci_check" in line)
    assert "/bin/pip" in " ".join(install[:1])
