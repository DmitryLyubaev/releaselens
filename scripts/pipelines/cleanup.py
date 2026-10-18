"""Independent owner cleanup. Fresh delete-only plans, never an original destroy replay."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from app.delivery_search.contracts import DeliveryRefused,canonical,exclusive_json,sha256_file
from .session import load,read,operation_lock,_reserve,instant

ORDER = ("index-test","index-demo-prod","foundation","identity","agents")


def early_allowed(stop: dict,source: str,session: str) -> bool:
    return (stop.get("kind") == "human-explicit-stop" and bool(stop.get("message_ref"))
        and stop.get("source_sha") == source and stop.get("session") == session)


def require_idle(driver):
    if driver.active_owned(): raise DeliveryRefused("reserved")


def quiescence_before(directory,a,raw):
    try:
        from .quiescence import request,status
        # An early completed/explicit-stop closeout uses the same browser protocol.
        # Missing browser acknowledgement is recorded, never an unbounded billable wait.
        request(directory,a,raw,datetime.now(timezone.utc),early=True)
        observed=status(directory,a,raw)
        number=1
        while (directory/f"quiescence-before-{number}.json").exists(): number+=1
        exclusive_json(directory/f"quiescence-before-{number}.json",observed)
        return observed["verified"]
    except Exception: return False


def retained_unchanged(before: dict,after: dict) -> bool:
    def ordered(value):
        if isinstance(value,list): return sorted((ordered(v) for v in value),key=canonical)
        if isinstance(value,dict): return {k:ordered(v) for k,v in value.items()}
        return value
    return (all(key in before and key in after for key in ("resources","roleAssignments","deletedVaults"))
            and canonical(ordered(before)) == canonical(ordered(after)))


def same_resource_id(actual, pinned):
    """Tolerate ARM grammar casing, keeping resource names and subscription exact.

    The retained identity manifest was built with lowercase grammar segments.
    Terraform returns their conventional mixed case. Other ID formats stay exact.
    """
    if actual == pinned:
        return isinstance(actual, str)
    if not isinstance(actual, str) or not isinstance(pinned, str):
        return False
    left, right = actual.split('/'), pinned.split('/')
    if len(left) != len(right) or len(left) not in (5, 9, 11) or left[0] != '' or right[0] != '':
        return False
    grammar = {1: 'subscriptions', 3: 'resourcegroups'}
    if len(left) > 5:
        grammar.update({5: 'providers', 6: 'microsoft.managedidentity', 7: 'userassignedidentities'})
    if len(left) > 9:
        grammar[9] = 'federatedidentitycredentials'
    return all(a.lower() == b.lower() == grammar[i] if i in grammar else a == b
               for i, (a, b) in enumerate(zip(left, right)))


def validate_remaining_delete(plan: dict,original: list[dict],remaining: list[dict]):
    try:
        bound = {row["address"]:row["id"] for row in original}
        current = {row["address"]:row["id"] for row in remaining}
        if len(bound)!=len(original) or len(current)!=len(remaining) or any(not same_resource_id(v,bound.get(k)) for k,v in current.items()):
            raise DeliveryRefused("plan")
        rows=list(plan.get("resource_changes",[]))
        for drift in plan.get("resource_drift",[]):
            if drift["mode"]!="managed" or drift["address"] not in current: raise DeliveryRefused("plan")
            change=drift["change"]
            if not same_resource_id(change["before"]["id"],current[drift["address"]]): raise DeliveryRefused("plan")
            if change["actions"]==["delete"]: rows.append(drift)
            elif (change["actions"]!=["update"] or not same_resource_id(change["after"]["id"],current[drift["address"]])):
                raise DeliveryRefused("plan")
        if len(rows)!=len(current) or {r["address"] for r in rows}!=set(current): raise DeliveryRefused("plan")
        for row in rows:
            change=row["change"]
            if (row["mode"]!="managed" or change["actions"]!=["delete"] or change.get("after") is not None
                or not same_resource_id(change["before"]["id"],current[row["address"]])): raise DeliveryRefused("plan")
    except (KeyError,TypeError,ValueError): raise DeliveryRefused("plan") from None


def execute(directory: Path,now: datetime,*,driver=None) -> dict:
    a,raw,manifest=load(directory)
    if a.apply_started_at is None: return {"status":"no-apply","physical_pass":False}
    if (directory/"cleanup-result.json").exists(): return read(directory/"cleanup-result.json")
    if not a.cleanup_due(now):
        stop=read(directory/"owner-stop-authority.json") if (directory/"owner-stop-authority.json").exists() else {}
        from .evidence import grade
        completed=all(v["status"]=="PASS" for k,v in grade(directory).items() if k!="P8-8")
        if not completed and not early_allowed(stop,a.source_sha,a.session):
            return {"status":"pending","physical_pass":False}
    if driver is None:
        from .owner import OwnerDriver
        driver=OwnerDriver(directory,raw,manifest)
    with operation_lock(directory):
        for intent in (directory/"intents").glob("cleanup-*.json"):
            if read(intent)["identity_sha256"]!=a.manifest_sha256: raise DeliveryRefused("authority")
        require_idle(driver)
        driver.verify_owner()
        attempt=1
        while (directory/f"intents/cleanup-{attempt}.json").exists(): attempt+=1
        _reserve(directory,f"cleanup-{attempt}",a.manifest_sha256)
        start=datetime.now(timezone.utc).isoformat()
        summary={"session":a.session,"source_sha":a.source_sha,"manifest_sha256":a.manifest_sha256,
            "attempt":attempt,"cleanup_started_at":start,"physical_pass":False,"status":"failed","components":[]}
        summary["quiescence_before"]=quiescence_before(directory,a,raw)
        try:
            components={v["name"]:v for v in manifest["components"]}
            for name in ORDER:
                if name not in components: continue
                require_idle(driver)
                component=components[name]
                checkpoint=directory/("component-empty-"+name+".json")
                if checkpoint.exists():
                    proof=read(checkpoint)
                    if (proof.get("manifest_sha256")!=a.manifest_sha256 or proof.get("source_sha")!=a.source_sha
                        or proof.get("name")!=name or proof.get("empty_state") is not True): raise DeliveryRefused("plan")
                    summary["components"].append(proof)
                    continue
                remaining=driver.state(component)
                # Validate ownership even for an empty plan. No extra state can be silently removed.
                if any(row not in component["resources"] for row in remaining):
                    # Drivers may return the minimal address/id projection only.
                    bound={r["address"]:r["id"] for r in component["resources"]}
                    if any(not same_resource_id(r["id"],bound.get(r["address"])) for r in remaining): raise DeliveryRefused("plan")
                if remaining:
                    plan=driver.plan(component,attempt,remaining)
                    validate_remaining_delete(plan,component["resources"],remaining)
                    require_idle(driver)
                    driver.apply(component,attempt)
                if driver.state(component): raise DeliveryRefused("plan")
                proof={"name":name,"empty_state":True,"manifest_sha256":a.manifest_sha256,"source_sha":a.source_sha,
                       "at":datetime.now(timezone.utc).isoformat()}
                exclusive_json(checkpoint,proof)
                summary["components"].append(proof)
            require_idle(driver)
            retained=driver.retained()
            exclusive_json(directory/f"retained-after-{attempt}.json",retained)
            unchanged=retained_unchanged(read(directory/"baseline.json")["retained"],retained)
            absence=driver.canonical_absence()
            summary.update({"canonical_absence":absence,"retained_unchanged":unchanged,
                "empty_states":True,"physical_pass":absence and unchanged,
                "status":"pass" if absence and unchanged else "failed"})
        except Exception as exc:
            summary["failure_code"]=exc.code if isinstance(exc,DeliveryRefused) else "timeout" if isinstance(exc,(TimeoutError,subprocess.TimeoutExpired)) else "internal"
            # Original private errors survive; public output uses only the fixed code above.
            exclusive_json(directory/f"cleanup-error-{attempt}.private.json",{"type":type(exc).__name__,"message":str(exc)})
        summary["verified_at"]=datetime.now(timezone.utc).isoformat()
        exclusive_json(directory/f"cleanup-attempt-{attempt}.json",summary)
        if summary["physical_pass"]: exclusive_json(directory/"cleanup-result.json",summary)
        return summary


def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument("--session",type=Path,required=True)
    parser.add_argument("--status",action="store_true")
    args=parser.parse_args(argv)
    try:
        if args.status:
            a,raw,manifest=load(args.session)
            from .owner import OwnerDriver
            active=OwnerDriver(args.session,raw,manifest).active_owned()
            temporary=args.session/"operation-status.tmp"
            temporary.write_bytes(canonical({"session":a.session,"source_sha":a.source_sha,"active_owned":active,
                "at":datetime.now(timezone.utc).isoformat()}))
            temporary.replace(args.session/"operation-status.json")
            print("status=active" if active else "status=idle"); return 2 if active else 0
        result=execute(args.session,datetime.now(timezone.utc))
        print("status="+result["status"])
        return 0 if result["physical_pass"] or result["status"] in ("pending","no-apply") else 2
    except Exception as exc:
        print("status=refused code="+(exc.code if isinstance(exc,DeliveryRefused) else "internal"))
        return 2


if __name__=="__main__": raise SystemExit(main())
