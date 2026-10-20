"""Hosted-agent pipeline YAML (release, negative, teardown, schedule, identity preflight), parsed offline."""
import importlib
import json
import re

import httpx
import pytest
import yaml

STATUS_OUTPUT = "dependencies.Status.outputs['Status.StatusCheck.P8Status']"
READY = f"eq({STATUS_OUTPUT}, 'ready')"
# A completed Status step's output stays readable after its job fails, so teardown also needs the stage result.
TEARDOWN_CONDITION = ("and(always(), in(dependencies.Status.result, 'Succeeded', 'SucceededWithIssues'), "
                      "eq(dependencies.Status.outputs['Status.StatusCheck.P8Status'], 'ready'))")
SETTINGS = ("P8_TENANT_ID", "P8_SUBSCRIPTION_ID", "P8_STORAGE_ACCOUNT", "P8_SEARCH_GROUP", "P8_SEARCH_SERVICE",
            "P8_RELEASE_DIGEST", "P8_PRINCIPALS_JSON")
BUILD_ENV = {"BUILD_REASON": "$(Build.Reason)", "BUILD_SOURCEBRANCH": "$(Build.SourceBranch)",
             "BUILD_SOURCEVERSION": "$(Build.SourceVersion)", "BUILD_BUILDID": "$(Build.BuildId)"}
PYTHONPATH = "$(Build.SourcesDirectory):$(Build.SourcesDirectory)/eval"
HOSTED = {"vmImage": "ubuntu-24.04"}
RELEASE = [("BuildAndTest", "ReleaseLens-P8-Test", None), ("CreateService", "ReleaseLens-P8-Test", None),
           ("PlanTest", "ReleaseLens-P8-Test", None), ("TestIndex", "ReleaseLens-P8-Test", "ReleaseLens-P8-Test"),
           ("PlanProd", "ReleaseLens-P8-Prod", None), ("ProdIndex", "ReleaseLens-P8-Prod", "ReleaseLens-P8-DemoProd")]
MODES = [("release", "Manual"), ("negative", "Manual"), ("teardown", "Manual"), ("identity-preflight", "Manual"),
         ("release", "Schedule")]


@pytest.fixture
def modules(repo_root,monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / "eval"))
    return tuple(importlib.import_module("scripts.pipelines."+name) for name in ("run","storage"))


# A small reader for the Azure Pipelines template syntax the files use: if-blocks and template inclusion.
def operand(text, params, variables):
    text = text.strip()
    if text in ("true", "false"): return text == "true"
    if text.startswith("'"): return text[1:-1]
    match = re.fullmatch(r"variables\['([^']+)'\]", text)
    if match: return variables.get(match.group(1))
    match = re.fullmatch(r"parameters\.(\w+)", text)
    if match: return params[match.group(1)]
    raise AssertionError("unsupported expression "+text)


def holds(expression, params, variables):
    match = re.fullmatch(r"(eq|ne)\((.+?),\s*(.+)\)", expression.strip())
    if match is None: raise AssertionError("unsupported condition "+expression)
    same = operand(match.group(2), params, variables) == operand(match.group(3), params, variables)
    return same if match.group(1) == "eq" else not same


def substitute(text, params):
    whole = re.fullmatch(r"\$\{\{\s*parameters\.(\w+)\s*\}\}", text)
    if whole: return params[whole.group(1)]
    return re.sub(r"\$\{\{\s*parameters\.(\w+)\s*\}\}", lambda m: str(params[m.group(1)]), text)


def expand(node, ctx):
    params, variables, root = ctx
    if isinstance(node, str): return substitute(node, params)
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            match = re.fullmatch(r"\$\{\{\s*if (.+?)\s*\}\}", key)
            if match is None:
                out[key] = expand(value, ctx)
            elif holds(match.group(1), params, variables):
                out.update(expand(value, ctx))
        return out
    if isinstance(node, list):
        out = []
        for item in node:
            if isinstance(item, dict) and len(item) == 1 and re.fullmatch(r"\$\{\{\s*if .+\}\}", next(iter(item))):
                key, value = next(iter(item.items()))
                if holds(re.fullmatch(r"\$\{\{\s*if (.+?)\s*\}\}", key).group(1), params, variables):
                    out.extend(expand(value, ctx))
            elif isinstance(item, dict) and "template" in item:
                path = root / item["template"]
                body = yaml.safe_load(path.read_bytes())
                given = expand(item.get("parameters", {}), ctx)
                inner = {p["name"]: given.get(p["name"], p.get("default")) for p in body.get("parameters", [])}
                assert set(given) <= set(inner), "undeclared template parameter"
                for p in body.get("parameters", []):
                    if "values" in p: assert inner[p["name"]] in p["values"], p["name"]
                    assert inner[p["name"]] is not None or "default" in p, "missing "+p["name"]
                (kind,) = [k for k in ("steps", "stages", "jobs") if k in body]
                out.extend(expand(body[kind], (inner, variables, root)))
            else:
                out.append(expand(item, ctx))
        return out
    return node


def render(repo_root, mode, reason):
    root = repo_root / "pipelines/templates"
    template = yaml.safe_load((root / "search-delivery.yml").read_bytes())
    assert mode in template["parameters"][0]["values"]
    return expand(template["stages"], ({"mode": mode}, {"Build.Reason": reason}, root))


def jobs_of(stage): return stage["jobs"]
def job_name(job): return job.get("job") or job["deployment"]
def job_steps(job): return job["steps"] if "steps" in job else job["strategy"]["runOnce"]["deploy"]["steps"]


def azure_steps(job): return [s for s in job_steps(job) if s.get("task") == "AzureCLI@2"]


def stage_table(stages):
    rows = []
    for stage in stages:
        (job,) = jobs_of(stage)
        (azure,) = azure_steps(job)
        rows.append((stage["stage"], azure["inputs"]["azureSubscription"], job.get("environment")))
    return rows


def command_of(stage):
    (job,) = jobs_of(stage)
    return azure_steps(job)[0]["inputs"]["inlineScript"]


def by_name(stages): return {s["stage"]: s for s in stages}


def every_job(repo_root):
    for mode, reason in MODES:
        for stage in render(repo_root, mode, reason):
            for job in jobs_of(stage): yield mode, reason, stage, job


def test_main_file_triggers_parameters_and_variables(repo_root):
    source = yaml.safe_load((repo_root / "pipelines/search-delivery.yml").read_bytes())
    assert source["trigger"] == "none" and source["pr"] == "none" and source["lockBehavior"] == "sequential"
    assert source["extends"]["template"] == "templates/search-delivery.yml"
    assert source["parameters"] == [{"name":"mode","type":"string","default":"release",
                                     "values":["release","negative","teardown","identity-preflight"]}]
    assert source["extends"]["parameters"] == {"mode": "${{ parameters.mode }}"}
    template = yaml.safe_load((repo_root / "pipelines/templates/search-delivery.yml").read_bytes())
    assert template["parameters"][0]["values"] == ["release", "negative", "teardown", "identity-preflight"]


def test_every_job_uses_hosted_ubuntu(repo_root):
    seen = list(every_job(repo_root))
    assert len(seen) == 15
    for mode, reason, stage, job in seen:
        assert job["pool"] == HOSTED, (mode, stage["stage"])


def test_no_self_hosted_pool_or_demands(repo_root):
    for path in ("pipelines/search-delivery.yml", *(f"pipelines/templates/{n}.yml" for n in (
            "search-delivery", "setup-steps", "stage-steps", "identity-preflight-task"))):
        text = (repo_root / path).read_text(encoding="utf-8")
        assert "ReleaseLens-P7" not in text and "demands" not in text and "name: ReleaseLens" not in text, path


def test_schedule_is_0600_utc_main_always(repo_root):
    source = yaml.safe_load((repo_root / "pipelines/search-delivery.yml").read_bytes())
    (schedule,) = source["schedules"]
    assert schedule["cron"] == "0 6 * * *" and schedule["always"] is True
    assert schedule["branches"] == {"include": ["main"]}


def test_variable_group_is_delivery_and_no_authority_variables(repo_root):
    source = yaml.safe_load((repo_root / "pipelines/search-delivery.yml").read_bytes())
    assert source["variables"] == [{"group": "ReleaseLens-P8-Delivery"}]
    for path in ("pipelines/search-delivery.yml", *(f"pipelines/templates/{n}.yml" for n in (
            "search-delivery", "setup-steps", "stage-steps", "identity-preflight-task"))):
        text = (repo_root / path).read_text(encoding="utf-8")
        for banned in ("P8_AUTHORITY", "P8_PREFLIGHT", "P8_SOURCE_SHA", "p8-authority", "ReleaseLens-P8\n"):
            assert banned not in text, (path, banned)
        assert not re.search(r"group: ReleaseLens-P8$", text, re.M), path


def test_release_stage_order_and_connections(repo_root):
    stages = render(repo_root, "release", "Manual")
    assert stage_table(stages) == RELEASE
    previous = None
    for stage in stages:
        assert stage.get("dependsOn") == previous
        assert "condition" not in stage or stage["condition"] == "succeeded()"
        previous = stage["stage"]
    commands = [command_of(s).split("scripts.pipelines.")[1].split(" --receipt-dir")[0] for s in stages]
    assert commands == ["run build", "service create-service", "run plan-test", "run test", "run plan-prod", "run prod"]


def test_deployment_jobs_are_exactly_the_two_index_stages(repo_root):
    stages = by_name(render(repo_root, "release", "Manual"))
    for name, environment in (("TestIndex", "ReleaseLens-P8-Test"), ("ProdIndex", "ReleaseLens-P8-DemoProd")):
        (job,) = stages[name]["jobs"]
        assert job["deployment"] == name and job["environment"] == environment and "runOnce" in job["strategy"]
        assert job_steps(job)[0] == {"download": "none"}
    for name in ("BuildAndTest", "CreateService", "PlanTest", "PlanProd"):
        (job,) = stages[name]["jobs"]
        assert "job" in job and "environment" not in job


def test_prod_index_is_deployment_job_on_demoprod_environment(repo_root):
    prod = by_name(render(repo_root, "release", "Manual"))["ProdIndex"]
    (job,) = prod["jobs"]
    assert job["deployment"] == "ProdIndex" and job["environment"] == "ReleaseLens-P8-DemoProd"
    (azure,) = azure_steps(job)
    assert azure["inputs"]["azureSubscription"] == "ReleaseLens-P8-Prod"
    assert "scripts.pipelines.run prod " in command_of(prod)
    assert prod["dependsOn"] == "PlanProd"


def test_negative_promote_stage_runs_promote_negative_with_default_condition(repo_root):
    stages = render(repo_root, "negative", "Manual")
    assert stage_table(stages) == [("NegativeGate", "ReleaseLens-P8-Test", "ReleaseLens-P8-Test"),
                                   ("PromoteNegative", "ReleaseLens-P8-Prod", None)]
    gate, promote = stages
    assert gate["jobs"][0]["deployment"] == "NegativeGate" and "scripts.pipelines.run negative " in command_of(gate)
    assert "scripts.pipelines.run promote-negative " in command_of(promote)
    assert promote["dependsOn"] == "NegativeGate"
    assert promote.get("condition", "succeeded()") == "succeeded()"
    assert "condition" not in gate


def test_schedule_verify_and_teardown_conditions(repo_root):
    stages = render(repo_root, "release", "Schedule")
    assert [s["stage"] for s in stages] == ["Status", "Verify", "TeardownProd", "TeardownTest"]
    assert stage_table(stages) == [("Status", "ReleaseLens-P8-Verify", None), ("Verify", "ReleaseLens-P8-Verify", None),
                                   ("TeardownProd", "ReleaseLens-P8-Prod", None), ("TeardownTest", "ReleaseLens-P8-Test", None)]
    status, verify, prod, test = stages
    assert "condition" not in status and "dependsOn" not in status
    assert [j["job"] for j in status["jobs"]] == ["Status"]
    (check,) = azure_steps(status["jobs"][0])
    assert check["name"] == "StatusCheck"
    assert "scripts.pipelines.service status " in check["inputs"]["inlineScript"]
    assert verify["dependsOn"] == "Status"
    assert verify["condition"] == f"and(succeeded(), {READY})"
    assert prod["dependsOn"] == ["Status", "Verify"]
    assert prod["condition"] == TEARDOWN_CONDITION
    assert test["dependsOn"] == ["Status", "TeardownProd"]
    assert test["condition"] == TEARDOWN_CONDITION
    assert "scripts.pipelines.run verify " in command_of(verify)
    assert "scripts.pipelines.service teardown-prod " in command_of(prod)
    assert "scripts.pipelines.service teardown-test " in command_of(test)


def test_status_stage_publishes_receipts_but_no_junit(repo_root):
    status = render(repo_root, "release", "Schedule")[0]
    steps = job_steps(status["jobs"][0])
    assert not [s for s in steps if s.get("task") == "PublishTestResults@2"]
    assert [s for s in steps if s.get("task") == "PublishPipelineArtifact@1"]


def test_teardown_mode_has_both_teardown_stages_unconditional(repo_root):
    stages = render(repo_root, "teardown", "Manual")
    assert stage_table(stages) == [("TeardownProd", "ReleaseLens-P8-Prod", None), ("TeardownTest", "ReleaseLens-P8-Test", None)]
    prod, test = stages
    assert "dependsOn" not in prod and "condition" not in prod
    assert test["dependsOn"] == "TeardownProd" and test["condition"] == "always()"
    assert "P8Status" not in json.dumps(stages)
    assert "scripts.pipelines.service teardown-prod " in command_of(prod)
    assert "scripts.pipelines.service teardown-test " in command_of(test)


def test_identity_preflight_mode_is_one_job_with_three_roles(repo_root):
    (stage,) = render(repo_root, "identity-preflight", "Manual")
    (job,) = stage["jobs"]
    assert [s["inputs"]["azureSubscription"] for s in azure_steps(job)] == [
        "ReleaseLens-P8-Test", "ReleaseLens-P8-Prod", "ReleaseLens-P8-Verify"]
    scripts = [s["inputs"]["inlineScript"] for s in azure_steps(job)]
    for role, script in zip(("test", "prod", "verify"), scripts):
        assert f"scripts.pipelines.identity_preflight {role} --receipt-dir" in script
    text = json.dumps(job)
    assert "identity_preflight all --guard-only" in text and "identity_preflight all --receipt-dir" in text
    assert "P8_PREFLIGHT" not in text


def test_setup_steps_start_every_job(repo_root):
    for mode, reason, stage, job in every_job(repo_root):
        steps = job_steps(job)
        steps = steps[1:] if steps[0] == {"download": "none"} else steps
        assert steps[0] == {"checkout": "self", "clean": True, "persistCredentials": False}, stage["stage"]
        assert steps[1]["task"] == "UsePythonVersion@0" and steps[1]["inputs"]["versionSpec"] == "3.12"
        text = "\n".join(s.get("bash", "") for s in steps[2:5])
        flags = "--disable-pip-version-check --require-hashes --only-binary=:all: --no-deps"
        assert f"p8-cli/bin/pip\" install {flags} -r build/delivery-agents/cli-requirements.lock" in text
        assert f"p8-workload/bin/pip\" install {flags} -r build/delivery-agents/requirements.lock" in text
        assert ('"$(Agent.TempDirectory)/p8-workload/bin/python" build/delivery-agents/install.py build/delivery-agents '
                '--terraform-out "$(Agent.TempDirectory)/p8-bin/terraform"') in text
        assert text.index("cli-requirements.lock") < text.index("install.py")
        assert "##vso[task.prependpath]$(Agent.TempDirectory)/p8-cli/bin" in text
        assert "##vso[task.prependpath]$(Agent.TempDirectory)/p8-bin" in text
        assert all("persistCredentials" not in s or s["persistCredentials"] is False for s in steps)


def test_every_azure_step_is_keyless_and_carries_settings(repo_root):
    count = 0
    for mode, reason, stage, job in every_job(repo_root):
        for step in azure_steps(job):
            count += 1
            inputs = step["inputs"]
            assert inputs["addSpnToEnvironment"] is True and inputs["visibleAzLogin"] is False
            assert inputs["scriptType"] == "bash" and inputs["workingDirectory"] == "$(Build.SourcesDirectory)"
            assert "\"$(Agent.TempDirectory)/p8-workload/bin/python\" -m scripts.pipelines." in inputs["inlineScript"]
            assert inputs["keepAzSessionActive"] is True
            env = step["env"]
            assert env["SYSTEM_ACCESSTOKEN"] == "$(System.AccessToken)"
            assert env["SYSTEM_OIDCREQUESTURI"] == "$(System.OidcRequestUri)"
            assert env["PYTHONPATH"] == PYTHONPATH
            assert BUILD_ENV.items() <= env.items()
            for name in SETTINGS: assert env[name] == f"$({name})", name
    assert count == 17


def test_guard_runs_before_login_for_run_stages(repo_root):
    for mode, reason, stage, job in every_job(repo_root):
        steps = job_steps(job)
        (azure,) = azure_steps(job) if mode != "identity-preflight" else (None,)
        script = azure["inputs"]["inlineScript"] if azure else ""
        if "scripts.pipelines.run " not in script: continue
        guards = [i for i, s in enumerate(steps) if "--guard-only" in s.get("bash", "")]
        assert len(guards) == 1 and guards[0] < steps.index(azure), stage["stage"]
        guard = steps[guards[0]]
        assert "SYSTEM_ACCESSTOKEN" not in guard["env"] and guard["env"]["PYTHONPATH"] == PYTHONPATH
        for name in SETTINGS: assert guard["env"][name] == f"$({name})"


def test_junit_is_published_where_a_gate_runs(repo_root):
    for mode, reason, stage, job in every_job(repo_root):
        script = azure_steps(job)[0]["inputs"]["inlineScript"] if azure_steps(job) else ""
        junit = [s for s in job_steps(job) if s.get("task") == "PublishTestResults@2"]
        if "scripts.pipelines.run " in script and "promote-negative" not in script:
            (publish,) = junit
            assert publish["condition"] == "always()" and publish["inputs"]["failTaskOnMissingResultsFile"] is True
            assert publish["inputs"]["testResultsFormat"] == "JUnit"
        else:
            assert not junit, stage["stage"]


def test_every_job_publishes_its_receipts_and_has_a_timeout(repo_root):
    names = {}
    for mode, reason, stage, job in every_job(repo_root):
        (artifact,) = [s for s in job_steps(job) if s.get("task") == "PublishPipelineArtifact@1"]
        assert artifact["condition"] == "always()"
        assert artifact["inputs"]["targetPath"] in ("$(Agent.TempDirectory)/p8-publish", "$(Agent.TempDirectory)/p8-identity-preflight")
        names.setdefault((mode, reason), []).append(artifact["inputs"]["artifact"])
        assert type(job["timeoutInMinutes"]) is int and 15 <= job["timeoutInMinutes"] <= 60, stage["stage"]
        assert job["cancelTimeoutInMinutes"] == 1
    for artifacts in names.values(): assert len(artifacts) == len(set(artifacts))


def test_release_timeouts_fit_the_run_day_timeline(repo_root):
    stages = by_name(render(repo_root, "release", "Manual"))
    limits = {name: stage["jobs"][0]["timeoutInMinutes"] for name, stage in stages.items()}
    assert limits == {"BuildAndTest": 20, "CreateService": 50, "PlanTest": 15, "TestIndex": 20, "PlanProd": 15, "ProdIndex": 20}


def test_other_job_timeouts_are_exact(repo_root):
    limits = lambda mode, reason: {n: s["jobs"][0]["timeoutInMinutes"] for n, s in by_name(render(repo_root, mode, reason)).items()}
    assert limits("release", "Schedule") == {"Status": 15, "Verify": 20, "TeardownProd": 30, "TeardownTest": 60}
    assert limits("negative", "Manual") == {"NegativeGate": 20, "PromoteNegative": 15}
    assert limits("teardown", "Manual") == {"TeardownProd": 30, "TeardownTest": 60}
    assert limits("identity-preflight", "Manual") == {"IdentityPreflight": 15}


def test_terraform_jobs_outlast_their_subprocess_timeouts_by_ten_minutes(repo_root):
    """A job timeout mid-apply or mid-destroy kills Terraform and can strand a state lease and a billing service.
    A command that runs several subprocesses needs the sum of their timeouts, not the longest."""
    read = lambda name: (repo_root / "scripts/pipelines" / name).read_text(encoding="utf-8")
    service, plans, run = read("service.py"), read("plans.py"), read("run.py")
    seconds = lambda text, pattern: int(re.search(pattern, text).group(1))
    index_apply = seconds(plans, r'(?s)"terraform","apply".*?timeout=(\d+)')
    service_apply = seconds(service, r'"apply-service",(\d+)')
    service_destroy = seconds(service, r'"destroy-service",(\d+)')
    index_destroy = seconds(service, r'"destroy-"\+target,(\d+)')
    init = seconds(service, r'"init-"\+target,(\d+)')
    plan, show, output = (seconds(service, rf'"{name}-service",(\d+)') for name in ("plan", "show", "output"))
    state_rm = seconds(service, r'"state-rm-"\+target,(\d+)')
    present = seconds(run, r'(?s)"search","service","show".*?timeout=(\d+)')
    az_login = seconds(run, r'subprocess\.run\(\["az",\*args\].*?timeout=(\d+)')
    assert (index_apply, service_apply, service_destroy, index_destroy) == (480, 1800, 1800, 480)
    assert (init, plan, show, output, state_rm, present, az_login) == (120, 120, 30, 30, 120, 60, 30)
    # Two az calls authenticate each service stage (run.py authenticate: account show, then get-access-token).
    login = 2 * az_login
    # create: login, present, init, plan, show, apply, output (Lifecycle.create).
    create = login + present + init + plan + show + service_apply + output
    # teardown-test, the longest path: login, present, test-index init and destroy, service init and destroy,
    # then on failure present again and state rm (Lifecycle.teardown_test); the success path ends with present instead.
    teardown_test = login + present + init + index_destroy + init + service_destroy + present + state_rm
    code_seconds = {"run test": index_apply, "run prod": index_apply, "service create-service": create,
                    "service teardown-prod": index_destroy, "service teardown-test": teardown_test}
    assert (create, teardown_test) == (2220, 2820)
    checked = set()
    for mode, reason, stage, job in every_job(repo_root):
        steps = azure_steps(job)
        if not steps: continue
        command = steps[0]["inputs"]["inlineScript"].split("scripts.pipelines.")[1].split(" --receipt-dir")[0]
        if command not in code_seconds: continue
        checked.add(command)
        assert job["timeoutInMinutes"] >= code_seconds[command] / 60 + 10, (mode, stage["stage"])
    assert checked == set(code_seconds)


def test_no_docker_or_opt_p8_workload_paths(repo_root):
    for path in ("pipelines/search-delivery.yml", *(f"pipelines/templates/{n}.yml" for n in (
            "search-delivery", "setup-steps", "stage-steps", "identity-preflight-task"))):
        text = (repo_root / path).read_text(encoding="utf-8").lower()
        for banned in ("docker", "/opt/p8", "/opt/", "container:", "p8agent", "ghcr"):
            assert banned not in text, (path, banned)


def test_templates_take_no_untrusted_step_parameters(repo_root):
    for name in ("search-delivery", "setup-steps", "stage-steps", "identity-preflight-task"):
        body = yaml.safe_load((repo_root / f"pipelines/templates/{name}.yml").read_bytes())
        for parameter in body.get("parameters", []): assert parameter["type"] != "stepList", name


def test_blob_claim_and_state_absence_fail_closed(modules):
    _,storage = modules
    requests = []
    class Tokens:
        def token(self): return "synthetic"
    def server(request):
        requests.append(request)
        if request.method == "PUT":
            assert request.headers["if-none-match"] == "*"
            return httpx.Response(412)
        return httpx.Response(404,headers={"x-ms-error-code":"ContainerNotFound"})
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        store = storage.BlobStore("owned.blob.core.windows.net",Tokens(),client)
        with pytest.raises(Exception): store.put_once("evidence","claims/release.json",b"{}");
        with pytest.raises(Exception): store.state("test")
    assert len(requests) == 2


def test_missing_results_files_refuse(modules,tmp_path):
    run,_ = modules
    with pytest.raises(Exception): run.require_result_files(tmp_path)
    (tmp_path/"results.json").write_text("{}")
    with pytest.raises(Exception): run.require_result_files(tmp_path)


def test_stage_steps_command_is_an_allow_list(repo_root):
    body = yaml.safe_load((repo_root / "pipelines/templates/stage-steps.yml").read_bytes())
    (command,) = [p for p in body["parameters"] if p["name"] == "command"]
    assert command["values"] == ["build", "plan-test", "test", "plan-prod", "prod", "negative", "promote-negative",
                                 "verify", "create-service", "status", "teardown-prod", "teardown-test"]
    used = set()
    for mode, reason in MODES:
        for stage in render(repo_root, mode, reason):
            for job in jobs_of(stage):
                for step in azure_steps(job):
                    used |= set(re.findall(r"scripts\.pipelines\.(?:run|service) (\S+)", step["inputs"]["inlineScript"]))
    assert used == set(command["values"])
