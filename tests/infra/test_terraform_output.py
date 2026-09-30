"""Terraform's output reaches the public run log only through a filter that hides the
subscription ID, and destroy.yml retries a destroy only on azurerm issue #33433.

GitHub masks a secret only where its whole value appears. Terraform shortens a long resource ID in
its progress lines to its first 39 characters, `...` and its tail, so a subscription ID can appear
cut short there, and GitHub does not mask that."""

import re
import shutil
import subprocess

import pytest
import yaml

# Listed, so that the tests below cannot pass by finding no Terraform step at all.
TERRAFORM_STEPS = {
    "deploy.yml": {"Apply the app stack", "Open Postgres to this runner", "Close Postgres to this runner"},
    "destroy.yml": {"Destroy the app stack"},
}
TERRAFORM_WRITE = re.compile(r"\bterraform(\s+-\S+)*\s+(apply|destroy)\b")
# The command, its own arguments, then both streams into the filter; destroy tees its log first.
PIPED = re.compile(r'\bterraform(\s+-\S+)*\s+(apply|destroy)\b[^|;&>]*\s2>&1 (\| tee "[^"]+" )?\| redact(;|$)')
DEFINITION = re.compile(r"redact\(\) \{ sed -u -E '[^']+'; \}")
FAKE_SUBSCRIPTION = "00000000-1111-2222-3333-444444444444"
CONTAINER_APP_ID = (
    f"/subscriptions/{FAKE_SUBSCRIPTION}/resourceGroups/rg-releaselens/providers/"
    "Microsoft.App/containerApps/ca-releaselens-api"
)


def _lines(run):
    """The script's logical lines: `\\` continuations joined, comments and blank lines dropped."""
    lines = (line.strip() for line in run.replace("\\\n", " ").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _workflow(repo_root, name):
    return yaml.safe_load((repo_root / ".github" / "workflows" / name).read_bytes())


def _steps(repo_root, name):
    return [step for job in _workflow(repo_root, name)["jobs"].values() for step in job["steps"]]


def _terraform_steps(repo_root, name):
    return [step for step in _steps(repo_root, name) if step.get("name") in TERRAFORM_STEPS[name]]


@pytest.mark.parametrize("name", sorted(TERRAFORM_STEPS))
def test_every_terraform_apply_and_destroy_is_piped_through_the_filter(repo_root, name):
    writers = {}
    for step in _steps(repo_root, name):
        for line in _lines(step.get("run", "")):
            if TERRAFORM_WRITE.search(line):
                writers.setdefault(step["name"], []).append(line)
    assert set(writers) == TERRAFORM_STEPS[name]
    for step, lines in writers.items():
        for line in lines:
            assert PIPED.search(line), f"{step}: {line}"


@pytest.mark.parametrize("name", sorted(TERRAFORM_STEPS))
def test_each_terraform_step_defines_the_filter_once(repo_root, name):
    for step in _terraform_steps(repo_root, name):
        definitions = [line for line in _lines(step["run"]) if line.startswith("redact()")]
        assert len(definitions) == 1, step["name"]
        assert DEFINITION.fullmatch(definitions[0]), definitions[0]


@pytest.mark.parametrize("name", sorted(TERRAFORM_STEPS))
def test_terraform_steps_run_in_bash_with_pipefail(repo_root, name):
    # GitHub runs `shell: bash` as `bash -eo pipefail`, so a pipeline fails when terraform does.
    # Any other shell, or a custom template such as `bash {0}`, would report the filter's status.
    workflow = _workflow(repo_root, name)
    assert workflow["defaults"] == {"run": {"shell": "bash"}}
    for job in workflow["jobs"].values():
        assert "defaults" not in job
    for step in _terraform_steps(repo_root, name):
        assert "shell" not in step, step["name"]


def _filter(repo_root):
    definitions = {
        line
        for name in TERRAFORM_STEPS
        for step in _terraform_steps(repo_root, name)
        for line in _lines(step["run"])
        if line.startswith("redact()")
    }
    assert len(definitions) == 1, f"the steps define different filters: {definitions}"
    return definitions.pop()


def _truncate_id(resource_id, max_len=80):
    """A copy of truncateId in hashicorp/terraform v1.15.8, internal/command/views/hook_ui.go,
    which the "Still creating/modifying/destroying..." lines call with maxIdLen = 80. An ID over 80
    characters keeps its first 39 and its last 38, around `...`."""
    if len(resource_id) <= max_len:
        return resource_id
    part = max_len // 2
    right = len(resource_id) - part - 1
    overlap = max_len - (part * 2 + len("..."))
    if overlap < 0:
        right -= overlap
    return resource_id[:part - 1] + "..." + resource_id[right:]


# CONTAINER_APP_ID as Terraform truncates it: /subscriptions/ and 24 of the GUID's 36 characters,
# then the ID's last 38.
TRUNCATED_APP_ID = "/subscriptions/00000000-1111-2222-3333-...t.App/containerApps/ca-releaselens-api"


def test_the_truncated_sample_is_what_terraform_prints():
    assert _truncate_id(CONTAINER_APP_ID) == TRUNCATED_APP_ID


FILTER_SAMPLES = {
    "truncated": (
        f"azurerm_container_app.api: Still destroying... [id={TRUNCATED_APP_ID}, 10s elapsed]",
        "azurerm_container_app.api: Still destroying... "
        "[id=/subscriptions/<redacted>...t.App/containerApps/ca-releaselens-api, 10s elapsed]",
    ),
    "whole": (
        "azurerm_container_app_environment.this: Creation complete after 2m1s "
        f"[id=/subscriptions/{FAKE_SUBSCRIPTION}/resourceGroups/rg-releaselens/providers/"
        "Microsoft.App/managedEnvironments/cae-releaselens]",
        "azurerm_container_app_environment.this: Creation complete after 2m1s "
        "[id=/subscriptions/<redacted>/resourceGroups/rg-releaselens/providers/"
        "Microsoft.App/managedEnvironments/cae-releaselens]",
    ),
    "masked": (
        "azurerm_container_app.api: Destruction complete after 1m0s "
        "[id=/subscriptions/***/resourceGroups/rg-releaselens/providers/"
        "Microsoft.App/containerApps/ca-releaselens-api]",
        "azurerm_container_app.api: Destruction complete after 1m0s "
        "[id=/subscriptions/***/resourceGroups/rg-releaselens/providers/"
        "Microsoft.App/containerApps/ca-releaselens-api]",
    ),
    "twice-on-one-line": (
        f"from /subscriptions/{FAKE_SUBSCRIPTION}/a to /subscriptions/{FAKE_SUBSCRIPTION.upper()}/b",
        "from /subscriptions/<redacted>/a to /subscriptions/<redacted>/b",
    ),
}


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("line, expected", FILTER_SAMPLES.values(), ids=list(FILTER_SAMPLES))
def test_the_filter_hides_the_subscription_id(repo_root, line, expected):
    script = f"{_filter(repo_root)}\nredact <<'EOF'\n{line}\nEOF\n"
    printed = subprocess.run(["bash"], input=script.encode(), capture_output=True, check=True).stdout
    assert printed == f"{expected}\n".encode()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_filter_leaves_no_run_of_the_guid_in_any_truncated_id(repo_root):
    # /subscriptions/<guid>/resourceGroups/rg- is 70 characters, so these IDs are 81 to 100 long.
    # From 81 to 88, the 38-character tail Terraform keeps starts inside the GUID.
    lengths = range(81, 101)
    ids = [f"/subscriptions/{FAKE_SUBSCRIPTION}/resourceGroups/rg-{'x' * (n - 70)}" for n in lengths]
    assert [len(resource_id) for resource_id in ids] == list(lengths)
    lines = [f"azurerm_resource_group.x: Still destroying... [id={_truncate_id(i)}, 10s elapsed]" for i in ids]
    script = f"{_filter(repo_root)}\nredact <<'EOF'\n" + "\n".join(lines) + "\nEOF\n"
    printed = subprocess.run(["bash"], input=script.encode(), capture_output=True, check=True).stdout
    printed = printed.decode().splitlines()
    assert len(printed) == len(ids)
    runs = {FAKE_SUBSCRIPTION[i:i + 4] for i in range(len(FAKE_SUBSCRIPTION) - 3)}
    for length, line in zip(lengths, printed):
        assert not [run for run in runs if run in line], f"{length} characters: {line}"


ISSUE_33433 = 'polling support for the Content-Type "" was not implemented'
OUTCOMES = {
    "ok": "exit 0",
    "33433": (
        "echo 'Error: deleting Container App (Container App Name: \"ca-releaselens-api\"): polling "
        f"after Delete: internal-error: {ISSUE_33433}' >&2; exit 1"
    ),
    "other": "echo 'Error: Error acquiring the state lock' >&2; exit 1",
}

# A terraform that succeeds on everything but destroy, and plays one outcome per destroy call.
# Each destroy call prints a line naming it, and a resource ID, as the real one would.
STUB = f"""#!/usr/bin/env bash
[ "$1" = destroy ] || exit 0
calls=$(( $(cat "$0.calls" 2>/dev/null || echo 0) + 1 ))
echo "$calls" > "$0.calls"
echo "stub: terraform $*"
echo "azurerm_container_app.api: Destroying... [id={CONTAINER_APP_ID}]"
case "$calls" in
@CASES@
  *) {OUTCOMES["other"]} ;;
esac
"""


def _run_destroy_step(repo_root, outcomes):
    steps = [step for step in _steps(repo_root, "destroy.yml") if step.get("name") == "Destroy the app stack"]
    assert len(steps) == 1
    cases = "\n".join(f"  {call}) {OUTCOMES[outcome]} ;;" for call, outcome in enumerate(outcomes, 1))
    # Built inside bash, so that the paths are bash's own on Linux, in WSL and in Git Bash alike.
    # The guard stops the run before any terraform but the stub could be called.
    preamble = f"""set -eo pipefail
RUNNER_TEMP=$(mktemp -d)
trap 'rm -rf "$RUNNER_TEMP"' EXIT
mkdir "$RUNNER_TEMP/bin"
cat > "$RUNNER_TEMP/bin/terraform" <<'STUB'
{STUB.replace("@CASES@", cases)}STUB
chmod +x "$RUNNER_TEMP/bin/terraform"
PATH="$RUNNER_TEMP/bin:$PATH"
[ "$(command -v terraform)" = "$RUNNER_TEMP/bin/terraform" ] || {{ echo "not the stub" >&2; exit 99; }}
"""
    return subprocess.run(["bash"], input=(preamble + steps[0]["run"]).encode(), capture_output=True)


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize(
    "outcomes, succeeds, calls",
    [
        (["33433", "33433", "ok"], True, 3),
        (["33433", "33433", "33433"], False, 3),
        (["other"], False, 1),
        # Judged on the latest attempt's output alone, so the earlier #33433 text does not count.
        (["33433", "other"], False, 2),
    ],
    ids=["33433-twice-then-ok", "33433-three-times", "other-error", "33433-then-other"],
)
def test_destroy_retries_only_on_azurerm_33433(repo_root, outcomes, succeeds, calls):
    result = _run_destroy_step(repo_root, outcomes)
    output = result.stdout.decode()
    destroys = [line for line in output.splitlines() if line.startswith("stub: terraform destroy")]
    assert len(destroys) == calls, output + result.stderr.decode()
    assert all("-lock-timeout=10m" in line.split() for line in destroys)
    assert (result.returncode == 0) is succeeds
    # One warning per retry, naming the issue.
    warnings = [line for line in output.splitlines() if line.startswith("::warning::")]
    assert len(warnings) == calls - 1
    assert all("#33433" in line for line in warnings)
    # The destroy step's output went through the filter too.
    assert "/subscriptions/<redacted>/resourceGroups/rg-releaselens" in output
    assert FAKE_SUBSCRIPTION not in output
