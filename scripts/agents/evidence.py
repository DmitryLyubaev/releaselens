"""Offline A1-A6 grading from retained originals. Outputs only allowlisted facts."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

PROJECTS = ("ReleaseLens.Core.Tests", "ReleaseLens.Web.Tests")
REASONS = {"A1": "queue, execution, image, agent or worker-count proof",
           "A2": "original build, source, published manifest and executed TRX proof",
           "A3": "distinct fresh agents, executions and workspaces",
           "A4": "original deliberate failure after successful ordinary tests",
           "A5": "three idle observations with cleared queue and offline placeholder",
           "A6": "owned absence, empty state, inventories, exclusive cleanup and PAT revocation"}


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone required")
    return result


def trx_count(path: Path) -> int:
    if path.stat().st_size > 10_000_000:
        raise ValueError("oversized results")
    root = ET.fromstring(path.read_bytes())
    ns = {"t": "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"}
    summary = root.find("t:ResultSummary", ns)
    counter = root.find("t:ResultSummary/t:Counters", ns)
    results = root.findall("t:Results/t:UnitTestResult", ns)
    if summary is None or counter is None or summary.get("outcome") not in {"Completed", "Passed"}:
        raise ValueError("missing summary")
    total, executed, passed = (int(counter.attrib[k]) for k in ("total", "executed", "passed"))
    if (total <= 0 or total != executed or total != passed or len(results) != total
            or int(counter.get("failed", "0")) != 0 or int(counter.get("notExecuted", "0")) != 0
            or any(r.get("outcome") != "Passed" for r in results)
            or len({r.attrib["executionId"] for r in results}) != total):
        raise ValueError("tests were empty, skipped, failed or inconsistent")
    return executed


def task_succeeded(timeline: dict, name: str) -> bool:
    records = [r for r in timeline["records"] if r.get("type") == "Task" and r.get("name") == name]
    return len(records) == 1 and records[0].get("result") == "succeeded"


def grade_run(run: dict, observations: dict, allowed_paths: set[Path], index: int) -> dict:
    flags = {key: False for key in ("A1", "A2", "A4", "A5")}
    stats = {"label": ("run-1", "run-2", "negative")[index], "pipeline_result": None}
    try:
        build, execution, manifest = run["build"], run["execution"], run["workload"]
        properties = execution["properties"]
        if build.get("result") in {"succeeded", "failed", "canceled"}:
            stats["pipeline_result"] = build["result"]
        queued, started, finished = (timestamp(build[k]) for k in ("queueTime", "startTime", "finishTime"))
        execution_started, execution_finished = (timestamp(properties[k]) for k in ("startTime", "endTime"))
        times_valid = queued <= execution_started <= started <= finished <= execution_finished
        samples = run["worker_samples"]
        sample_times = [timestamp(s["time"]) for s in samples]
        counts_valid = (bool(samples) and all(queued <= t <= execution_finished for t in sample_times)
                        and all(a <= b for a, b in zip(sample_times, sample_times[1:]))
                        and all(len(s["executions"]) <= 1 and set(s["executions"]) <= {execution["name"]} for s in samples)
                        and any(s["executions"] == [execution["name"]] and execution_started <= t <= execution_finished
                                for s, t in zip(samples, sample_times)))
        activity = run["activity"]
        no_manual = isinstance(activity["value"], list) and not activity.get("nextLink") and not any(
            r.get("operationName", {}).get("value", "").casefold() == "microsoft.app/jobs/start/action"
            for r in activity["value"])
        agent = manifest["agent"]
        matching_agent = (agent.startswith(f"p7-{observations['session']}-worker-")
                          and any(a["name"] == agent and a["status"] == "online" for a in run["pool_snapshot"]["value"]))
        flags["A1"] = (times_valid and counts_valid and no_manual and matching_agent
                        and observations["worker_definition"]["properties"]["configuration"]["triggerType"] == "Event"
                        and properties["template"]["containers"][0]["image"] == observations["image_digest"]
                        and properties["status"] in {"Succeeded", "Failed", "Stopped"})
        if times_valid:
            stats.update(queue_to_agent_seconds=(started - queued).total_seconds(),
                         build_seconds=(finished - started).total_seconds())
        common = (build["status"] == "completed" and build["sourceVersion"] == observations["source"]
                  and build["sourceBranch"] == "refs/heads/main" and manifest["source"] == observations["source"]
                  and str(build["id"]) == manifest["build_id"] and manifest["session"] == observations["session"]
                  and manifest["workspace_fresh"] is True and bool(manifest["workspace_id"])
                  and task_succeeded(run["timeline"], "Require approved source and fresh workspace")
                  and task_succeeded(run["timeline"], "Build and test Core and Web")
                  and task_succeeded(run["timeline"], "Publish Project 7 test results")
                  and task_succeeded(run["timeline"], "Publish Project 7 workload artifact"))
        workload_task, = [r for r in run["timeline"]["records"] if r.get("name") == "Build and test Core and Web"]
        work_start, work_finish = (timestamp(workload_task[k]) for k in ("startTime", "finishTime"))
        common = common and started <= work_start <= work_finish <= finished
        if common:
            stats["build_test_seconds"] = (work_finish - work_start).total_seconds()
        folder = Path(run["artifact_dir"]).resolve()
        count = 0
        for project in PROJECTS:
            relative = f"{project}/{project}.trx"
            path = folder / project / f"{project}.trx"
            if path.resolve() not in allowed_paths or hashlib.sha256(path.read_bytes()).hexdigest() != manifest["trx"][relative]:
                raise ValueError("missing or altered artifact")
            count += trx_count(path)
        stats["executed_tests"] = count
        flags["A2"] = common and (index == 2 or build["result"] == "succeeded")
        negative_records = [r for r in run["timeline"]["records"]
                            if r.get("type") == "Task" and r.get("name") == "Intentional negative pipeline step"]
        flags["A4"] = (common and flags["A2"] and build["result"] == "failed" and len(negative_records) == 1
                        and negative_records[0].get("result") == "failed") if index == 2 else False
    except (KeyError, ValueError, TypeError, OSError, ET.ParseError, AttributeError, IndexError):
        pass
    # Idle/cleanup observations remain gradeable even if build evidence is incomplete.
    try:
        idle = run["idle_samples"]
        times = [timestamp(s["time"]) for s in idle]
        flags["A5"] = (len(idle) >= 3 and all((b - a).total_seconds() >= 30 for a, b in zip(times, times[1:]))
                        and times[0] >= timestamp(run["execution"]["properties"]["endTime"])
                        and all(s["workers"] == [] and s["online_agents"] == [] and s["queued"] == 0 for s in idle)
                        and observations["placeholder"] == {"name": f"p7-{observations['session']}-placeholder", "status": "offline"})
        if flags["A5"]:
            stats["return_to_idle_seconds"] = (times[0] - timestamp(run["build"]["finishTime"])).total_seconds()
            if stats["return_to_idle_seconds"] < 0:
                flags["A5"] = False
    except (KeyError, ValueError, TypeError, AttributeError):
        pass
    return {"flags": flags, "stats": stats}


def closeout_valid(obs: dict) -> bool:
    try:
        close = obs["closeout"]
        owned = obs["owned_ids"]
        group = obs["owned_group_id"]
        window = {k: timestamp(v) for k, v in obs["window"].items()}
        start, cutoff = window["apply_started_at"], window["queue_cutoff_at"]
        cleanup, deadline, end = (window[k] for k in ("cleanup_started_at", "cleanup_deadline_at", "closeout_at"))
        timing = (start <= cleanup <= end <= deadline and start <= cutoff <= deadline
                  and (cutoff - start).total_seconds() <= 1800
                  and (cleanup - start).total_seconds() <= 2700
                  and (deadline - start).total_seconds() <= 3600
                  and all(start <= timestamp(r["build"]["queueTime"]) <= min(cutoff, cleanup)
                          for r in obs["runs"])
                  and cleanup <= timestamp(close["pat_revocation_confirmed_at"]) <= end)
        return (len(owned) == len(set(owned)) == 9 and all(i == group or i.startswith(group + "/") for i in owned)
                and len(close["destroyed_ids"]) == 9 and set(close["destroyed_ids"]) == set(owned)
                and close["group_exists"] is False and close["state_read_ok"] is True
                and close["terraform_addresses"] == [] and close["operations_active"] == []
                and close["cleanup_lock_exclusive"] is True and close["queue_disabled"] is True
                and close["queued"] == 0 and close["pat_revoked"] is True and timing
                and timestamp(close["pat_revocation_confirmed_at"]) is not None
                and close["inventories_complete"] is True and bool(close["retained_before"])
                and close["retained_before"].keys() == close["retained_after"].keys()
                and all(sorted(before) == sorted(close["retained_after"][key])
                        for key, before in close["retained_before"].items()))
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def grade(observations: dict, trx_paths: list[Path]) -> dict:
    result = {"schema_version": 1, "checks": {key: {"status": "not-run", "reason": reason}
                                             for key, reason in REASONS.items()}, "runs": []}
    if not isinstance(observations, dict):
        return result
    source, image = observations.get("source", ""), observations.get("image_digest", "")
    if (not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source)
            or not isinstance(image, str) or not re.fullmatch(r"ghcr\.io/[a-z0-9_-]+/releaselens-agent@sha256:[0-9a-f]{64}", image)):
        return result
    result.update(source=source, image_digest=image)
    runs = observations.get("runs", [])
    if not isinstance(runs, list) or len(runs) > 3:
        return result
    allowed = {p.resolve() for p in trx_paths}
    grades = [grade_run(run, observations, allowed, index) for index, run in enumerate(runs)]
    result["runs"] = [g["stats"] for g in grades]

    def check(key, value, present=True):
        if present:
            result["checks"][key]["status"] = "pass" if value else "fail"

    try:
        baseline = observations["initial_idle"]
        origin = (baseline["workers"] == [] and baseline["online_agents"] == [] and baseline["queued"] == 0
                  and timestamp(baseline["time"]) <= timestamp(runs[0]["build"]["queueTime"]))
    except (KeyError, ValueError, TypeError, IndexError):
        origin = False
    check("A1", origin and all(g["flags"]["A1"] for g in grades), bool(grades))
    check("A2", all(g["flags"]["A2"] for g in grades), bool(grades))
    try:
        distinct = all(len({str(r[field][key]) for r in runs}) == len(runs) for field, key in
                       (("workload", "agent"), ("workload", "workspace_id"), ("execution", "name"), ("build", "id")))
        sequential = all(timestamp(b["build"]["queueTime"]) >= timestamp(a["idle_samples"][-1]["time"])
                         for a, b in zip(runs, runs[1:]))
    except (KeyError, ValueError, TypeError, IndexError):
        distinct = False
        sequential = False
    check("A3", distinct and sequential and all(g["flags"]["A2"] for g in grades[:2]), len(runs) >= 2)
    check("A4", grades[2]["flags"]["A4"] if len(grades) == 3 else False, len(grades) == 3)
    check("A5", all(g["flags"]["A5"] for g in grades), bool(grades))
    check("A6", closeout_valid(observations), "closeout" in observations)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--trx-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = grade(json.loads(args.observations.read_text(encoding="utf-8")), list(args.trx_dir.rglob("*.trx")))
        # Evidence is append-only: the operator chooses a new output path each time.
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
        passed = all(c["status"] == "pass" for c in result["checks"].values())
        print(f"acceptance={'pass' if passed else 'incomplete'}")
        return 0 if passed else 1
    except (OSError, ValueError, TypeError):
        print("acceptance=refused category=input_or_output")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
