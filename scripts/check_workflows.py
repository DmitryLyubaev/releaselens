"""Static rules for the GitHub environment `azure`.

The environment's `main` branch rule is the only thing that binds the Azure federated credential
to `main`. A run that carries the default branch's ref, such as one started by
`pull_request_target`, `workflow_run` or `issue_comment`, passes that rule whatever code it runs.
So only the workflows in ALLOWED_AZURE may name the environment, and they must keep their
triggers, permissions, concurrency and SHA-pinned actions. Outside ALLOWED_AZURE, a job may call
only a local `./` reusable workflow. The checker scans those files itself, but it cannot see an
external one, which runs in this repository's context and could name the environment itself.
`ci.yml`'s `publish-image` job must pin its actions too, because it builds the image that runs
as the app identity.

Usage: python scripts/check_workflows.py <workflows-dir>
Prints one line per violation. Exit codes: 0 no violations, 1 violations, 2 usage.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ALLOWED_AZURE = {"deploy.yml", "destroy.yml", "gateway-check.yml"}

TRIGGERS = {
    "deploy.yml": {"workflow_dispatch"},
    "destroy.yml": {"workflow_dispatch", "schedule"},
    "gateway-check.yml": {"workflow_dispatch"},
}
DESTROY_CRON = "0 14 * * *"
# gateway-check.yml runs one model call as the deploy identity. Its `main` guard backs up the
# environment's branch rule, which a dispatch from another ref would not pass.
MAIN_GUARD = "github.ref == 'refs/heads/main'"
AZURE_JOB_PERMISSIONS = {"id-token": "write", "contents": "read"}
PREFLIGHT_PERMISSIONS = {"actions": "read"}
CONCURRENCY = {"group": "releaselens-azure", "cancel-in-progress": False, "queue": "max"}
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")

_ABSENT = object()


def check(workflows_dir: Path) -> list[str]:
    """Return one message per violation in the workflow files directly under workflows_dir."""
    violations = []
    # GitHub runs both extensions and ignores subdirectories.
    paths = sorted(p for p in workflows_dir.iterdir()
                   if p.is_file() and p.suffix.lower() in {".yml", ".yaml"})
    for path in paths:
        try:
            workflow = yaml.safe_load(path.read_bytes())
        except yaml.YAMLError as error:
            mark = getattr(error, "problem_mark", None)
            violations.append(f"{path.name}: not valid YAML" + (f" at line {mark.line + 1}" if mark else ""))
            continue
        # A document that is not a mapping is rejected by GitHub, so it can never run.
        violations += _check_workflow(path.name, workflow if isinstance(workflow, dict) else {})
    return violations


def _check_workflow(name, workflow):
    jobs = _jobs(workflow)
    found = []
    for job_id, job in jobs.items():
        environment = _environment(job)
        if environment is None:
            continue
        if "${{" in environment:
            found.append(f"{name}: job '{job_id}': environment '{environment}' is an expression "
                         "and could resolve to azure")
        elif _is_azure(environment) and name not in ALLOWED_AZURE:
            found.append(f"{name}: job '{job_id}': environment '{environment}' is reserved for the "
                         "workflows in ALLOWED_AZURE")
    if name not in ALLOWED_AZURE:
        for job_id, job in jobs.items():
            reusable = job.get("uses")
            if reusable is not None and not str(reusable).startswith("./"):
                found.append(f"{name}: job '{job_id}': reusable workflow '{reusable}' is external and "
                             "could run with environment azure")
    if name in ALLOWED_AZURE:
        found += _check_allowed(name, workflow, jobs)
    if name == "ci.yml" and "publish-image" in jobs:
        found += _unpinned(name, {"publish-image": jobs["publish-image"]})
    return found


def _check_allowed(name, workflow, jobs):
    found = []
    # PyYAML follows YAML 1.1, which reads the bare key `on` as the boolean True.
    on = workflow.get("on", workflow.get(True))
    triggers = _triggers(on)
    if triggers != TRIGGERS[name]:
        found.append(f"{name}: triggers must be exactly {', '.join(sorted(TRIGGERS[name]))} "
                     f"(found: {', '.join(sorted(triggers)) or 'none'})")
    if name == "destroy.yml" and "schedule" in triggers:
        schedule = on.get("schedule") if isinstance(on, dict) else None
        entries = schedule if isinstance(schedule, list) else []
        crons = [entry.get("cron") for entry in entries if isinstance(entry, dict)]
        if DESTROY_CRON not in crons:
            shown = ", ".join(f"'{cron}'" for cron in crons) or "none"
            found.append(f"{name}: schedule must include cron '{DESTROY_CRON}' (found: {shown})")

    permissions = workflow.get("permissions", _ABSENT)
    if permissions != {}:
        found.append(f"{name}: top-level permissions must be {{}} (found: {_show(permissions)})")

    if name == "gateway-check.yml":
        for job_id, job in jobs.items():
            guard = job.get("if", _ABSENT)
            if guard != MAIN_GUARD:
                found.append(f"{name}: job '{job_id}': if must be {_show(MAIN_GUARD)} "
                             f"(found: {_show(guard)})")

    if name in {"deploy.yml", "destroy.yml", "gateway-check.yml"}:
        found += _job_permissions(name, jobs)
        concurrency = workflow.get("concurrency", _ABSENT)
        if concurrency != CONCURRENCY:
            found.append(f"{name}: top-level concurrency must be {_show(CONCURRENCY)} "
                         f"(found: {_show(concurrency)})")

    found += _unpinned(name, jobs)
    return found


def _job_permissions(name, jobs):
    found = []
    for job_id, job in jobs.items():
        permissions = job.get("permissions", _ABSENT)
        if _is_azure(_environment(job)):
            expected = AZURE_JOB_PERMISSIONS
        elif name == "deploy.yml" and job_id == "preflight":
            expected = PREFLIGHT_PERMISSIONS
        else:
            # write-all grants every scope, id-token included.
            if (isinstance(permissions, dict) and "id-token" in permissions) or permissions == "write-all":
                found.append(f"{name}: job '{job_id}': only the job with environment azure may have "
                             f"id-token (found: {_show(permissions)})")
            continue
        if permissions != expected:
            found.append(f"{name}: job '{job_id}': permissions must be {_show(expected)} "
                         f"(found: {_show(permissions)})")
    return found


def _unpinned(name, jobs):
    found = []
    for job_id, job in jobs.items():
        steps = job.get("steps") if isinstance(job.get("steps"), list) else []
        # A job-level `uses` calls a reusable workflow, which is pinned the same way.
        refs = [job.get("uses")] + [step.get("uses") for step in steps if isinstance(step, dict)]
        for ref in refs:
            if ref is None or str(ref).startswith("./"):
                continue
            if not PINNED.fullmatch(str(ref)):
                found.append(f"{name}: job '{job_id}': '{ref}' must be pinned to a 40-hex commit SHA")
    return found


def _jobs(workflow):
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return {}
    return {str(job_id): job for job_id, job in jobs.items() if isinstance(job, dict)}


def _environment(job):
    """The job's environment name, from `environment: <name>` or `environment: {name: <name>}`."""
    environment = job.get("environment")
    if isinstance(environment, dict):
        environment = environment.get("name")
    return None if environment is None else str(environment)


def _is_azure(environment):
    # GitHub environment names are case-insensitive. Surrounding whitespace is ignored too, so a
    # padded name errs towards a match.
    return environment is not None and environment.strip().casefold() == "azure"


def _triggers(on):
    if isinstance(on, str):
        return {on}
    if isinstance(on, (list, dict)):
        return {str(trigger) for trigger in on}
    return set()


def _show(value):
    return "absent" if value is _ABSENT else json.dumps(value, default=str)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("workflows_dir", type=Path)
    args = parser.parse_args(argv)
    if not args.workflows_dir.is_dir():
        parser.error(f"not a directory: {args.workflows_dir}")
    violations = check(args.workflows_dir)
    for violation in violations:
        print(violation)
    if not violations:
        print(f"no violations in {args.workflows_dir}")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
