"""Regrade hash-bound private originals. Public output is an explicit fixed projection."""
import argparse
from dataclasses import asdict
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET

from app.delivery_search.bundle import verify_bundle
from app.delivery_search.contracts import DeliveryRefused,canonical,sha256_file,exclusive_json
from app.delivery_search.gate import grade_rows
from app.retrieval.arms import ArmResult,Hit
from .session import load,read,bound_file,instant
from .cleanup import retained_unchanged
from .identity import ROLE_IDS,role_matrix
from .checks import verify_checks
from .plans import PlanBinding,verify_plan,validate_index_plan


def timing_pass(cleanup: dict,authority: dict) -> bool:
    try:
        return (instant(cleanup["cleanup_started_at"])<=instant(authority["cleanup_start_by"])
            and instant(cleanup["verified_at"])<=instant(authority["closeout_by"]))
    except (KeyError,TypeError,ValueError,DeliveryRefused): return False


class MissingOriginal(Exception): pass


def require_group_absence(rows,groups):
    if len(rows)!=len(groups) or {row["id"] for row in rows}!=set(groups) or any(row["status"]!=404 for row in rows):
        raise DeliveryRefused("gate")


def require_empty_state(command,result,cwd):
    if (command["args"]!=["terraform","state","list"] or command["cwd"]!=cwd
        or command["pid"]!=result["pid"] or result["returncode"]!=0 or result["stdout"].strip()):
        raise DeliveryRefused("gate")


def require_no_matches(rows):
    if len(rows)!=229 or any(row.hits for row in rows): raise DeliveryRefused("gate")


def require_toolchain(log,binding,source):
    lines=[line.split("=",1)[1] for line in log.splitlines() if line.startswith("P8_TOOLCHAIN_RECEIPT=")]
    if len(lines)!=1: raise DeliveryRefused("inputs")
    receipt=json.loads(lines[0])
    if (receipt["kind"]!="container-toolchain" or receipt["source_sha"]!=source or binding["source_sha"]!=source
        or receipt["config_digest"]!=binding["config_digest"]
        or receipt["versions"]!={"terraform":"1.15.8","azure_cli":"2.81.0","agent":"5.280.0","python":"3.12"}):
        raise DeliveryRefused("inputs")


def require_index_absence(proof,component,authority,manifest):
    target=component["name"][6:]
    if component["name"] not in ("index-test","index-demo-prod") or proof["component"]!=component["name"]:
        raise DeliveryRefused("gate")
    backend,index=proof["backend"],proof["index"]
    def parent(observation,marker):
        expected={r["id"] for c in manifest["components"] if c["name"]=="foundation" for r in c["resources"]
            if r.get("id") and marker in r["id"].lower() and "/" not in r["id"].lower().split(marker,1)[1]}
        return observation["status"]==404 and observation["id"] in expected
    if backend["kind"]=="arm-parent":
        valid_backend=parent(backend,"/microsoft.storage/storageaccounts/")
    else:
        valid_backend=(backend["kind"]=="blob" and backend["host"]==authority["storage_host"]
            and backend["container"]==("test-state" if target=="test" else "prod-state") and backend["key"]=="index.tfstate"
            and (backend["status"]==404 and backend["error_code"] in ("BlobNotFound","ContainerNotFound")
                or backend["status"]==200 and backend["length"]==0))
    if index["kind"]=="arm-parent": valid_index=parent(index,"/microsoft.search/searchservices/")
    else: valid_index=(index["kind"]=="index" and index["host"]==authority["search_host"] and index["name"]=="p8-"+target and index["status"]==404)
    if not valid_backend or not valid_index: raise DeliveryRefused("gate")


def require_evaluations(suites,expected,plan_id):
    if not suites or not plan_id or any(str(suite["context"]["PlanId"]).lower()!=str(plan_id).lower() for suite in suites):
        raise DeliveryRefused("checks")
    runs=[row for suite in suites for row in suite["checkRuns"]]
    for item in expected:
        selected=[row for row in runs if row["checkConfigurationRef"]["id"]==item["id"]]
        if not selected: raise DeliveryRefused("checks")
        for row in selected:
            ref=row["checkConfigurationRef"]
            if (row["status"]!="approved" or ref["type"]["id"]!=item["type_id"]
                or any(str(ref["resource"][key])!=str(item["resource"][key]) for key in ("id","type"))):
                raise DeliveryRefused("checks")
    return runs


class Originals:
    def __init__(self,directory,a,raw):
        self.directory,self.a,self.raw=directory,a,raw
        self.registry=read(directory/"originals.json")
        if (self.registry.get("kind")!="actual" or self.registry.get("session")!=a.session
            or self.registry.get("source_sha")!=a.source_sha): raise DeliveryRefused("authority")

    def path(self,key):
        entry=self.registry.get("files",{}).get(key)
        if entry is None: raise MissingOriginal()
        path=bound_file(self.directory,entry["path"])
        if (entry["session"]!=self.a.session or entry["source_sha"]!=self.a.source_sha
            or entry["kind"]!="actual" or entry["channel"] not in ("owner-browser","owner-arm","runtime","github")
            or instant(entry["captured_at"])>datetime.now(timezone.utc)
            or sha256_file(path)!=entry["sha256"]): raise DeliveryRefused("authority")
        return path

    def json(self,key): return json.loads(self.path(key).read_bytes())

    def build(self,name,result="succeeded",reason="manual"):
        build=self.json(name+"-build")
        if (build["definition"]["id"]!=self.raw["pipeline_id"] or build["definition"]["id"]==1
            or build["sourceVersion"]!=self.a.source_sha or build["sourceBranch"]!="refs/heads/main"
            or build["status"]!="completed" or build["result"]!=result or build["reason"]!=reason):
            raise DeliveryRefused("gate")
        return build

    def gate(self,stage,bundle,*,negative=False):
        data=self.json("gate-"+stage+"-json")
        receipt=self.json("gate-"+stage+"-receipt")
        run=self.build("negative","failed") if stage=="negative" else self.build("scheduled",reason="schedule") if stage=="verify" else self.build("release")
        if (receipt["source_sha"]!=self.a.source_sha or receipt["release_digest"]!=self.a.release_digest
            or receipt["run_id"]!=run["id"] or receipt["stage"]!=stage
            or receipt["expected_negative"] is not negative
            or receipt["results.json_sha256"]!=sha256_file(self.path("gate-"+stage+"-json"))
            or receipt["results.xml_sha256"]!=sha256_file(self.path("gate-"+stage+"-xml"))):
            raise DeliveryRefused("gate")
        rows=[ArmResult(r["qid"],r["arm"],[Hit(**h) for h in r["hits"]],r["ms"],r["error"],r.get("query_tokens",0)) for r in data["rows"]]
        grade=grade_rows(bundle,rows)
        if canonical(asdict(grade))!=canonical(data["gate"]) or canonical(data["gate"])!=canonical(receipt["gate"]):
            raise DeliveryRefused("gate")
        suite=ET.fromstring(self.path("gate-"+stage+"-xml").read_bytes())
        cases=suite.findall("testcase")
        if (len(cases)!=230 or len({c.get("name") for c in cases})!=230 or suite.get("tests")!="230"
            or suite.get("errors")!="0" or suite.get("skipped")!="0" or any(c.find("skipped") is not None for c in cases)
            or grade.evaluated!=229 or grade.errors or grade.missing or grade.skipped):
            raise DeliveryRefused("gate")
        if negative:
            require_no_matches(rows)
            if grade.passed or grade.correct!=0 or suite.get("failures")!="1": raise DeliveryRefused("gate")
        elif not grade.passed or suite.get("failures")!="0": raise DeliveryRefused("gate")
        return {"evaluated":229,"correct":grade.correct,"errors":0,"missing":0,"skipped":0}


def _check_identity(o,stage,role):
    original=o.json("identity-"+stage); expected=o.raw["principals"][role]
    account,claims=original["account"],original["claims"]
    federation,access=claims["federation"],claims["access"]
    at=instant(original["at"]).timestamp()
    if (original["source_sha"]!=o.a.source_sha or original["release_digest"]!=o.a.release_digest
        or original["scheme"]!="WorkloadIdentityFederation" or original["role"]!=role
        or account["id"]!=expected["subscription_id"] or account["tenantId"]!=expected["tenant_id"]
        or account["user"]["type"].lower()!="serviceprincipal" or account["user"]["name"].lower()!=expected["client_id"].lower()
        or federation["iss"]!=expected["issuer"] or federation["sub"]!=expected["subject"]
        or federation["aud"]!="api://AzureADTokenExchange" or federation["exp"]<=at+30
        or access["oid"]!=expected["object_id"] or access["tid"]!=expected["tenant_id"]
        or access.get("appid",access.get("azp"))!=expected["client_id"] or access["exp"]<=at+30):
        raise DeliveryRefused("identity")


def grade(directory: Path) -> dict[str,dict]:
    results={"P8-"+str(i):{"status":"NOT RUN","code":"missing-original"} for i in range(1,9)}
    if not (directory/"authority.json").exists(): return results
    try:
        a,raw,manifest=load(directory)
        o=Originals(directory,a,raw)
    except Exception:
        return {key:{"status":"FAIL","code":"authority"} for key in results}
    with tempfile.TemporaryDirectory(prefix="p8-grade-",dir=directory) as scratch:
        bundle=None
        def inputs():
            nonlocal bundle
            if bundle is None:
                bundle=verify_bundle(o.path("bundle"),a.release_digest,Path(scratch)/"bundle")
                if bundle.manifest.source_sha!=a.source_sha: raise DeliveryRefused("bundle")
            return bundle
        def perform(number,work):
            try:
                metrics=work() or {}
                results["P8-"+str(number)]={"status":"PASS","code":"originals-verified",**metrics}
            except MissingOriginal: pass
            except Exception as exc:
                results["P8-"+str(number)]={"status":"FAIL","code":exc.code if isinstance(exc,DeliveryRefused) else "invalid-original"}
        def image():
            inputs()
            run=o.json("image-run"); digest=o.json("image-digest"); pull=o.json("image-anonymous-pull")
            smoke=o.path("image-smoke").read_text(encoding="utf-8")
            require_toolchain(smoke,digest,a.source_sha)
            if (run["status"]!="completed" or run["conclusion"]!="success" or run["head_sha"]!=a.source_sha
                or digest["source_sha"]!=a.source_sha or not re.fullmatch(r"sha256:[0-9a-f]{64}",digest["digest"])
                or pull["digest"]!=digest["digest"] or pull["authenticated"] is not False or pull["status"]!=200):
                raise DeliveryRefused("inputs")
        perform(1,image)
        def identities():
            for stage,role in (("test","test"),("plan-prod","prod"),("prod","prod"),("verify","verify")): _check_identity(o,stage,role)
            rows=o.json("rbac")
            actual={(r["principalId"],r["roleDefinitionId"].rsplit("/",1)[-1],r["scope"]) for r in rows
                if r["principalId"] in {p["object_id"] for p in raw["principals"].values()}}
            expected={(raw["principals"][role]["object_id"],ROLE_IDS[name],scope)
                for role,grants in role_matrix(raw["scopes"]).items() for name,scope in grants}
            if actual!=expected: raise DeliveryRefused("identity")
        perform(2,identities)
        def plans():
            for target in ("test","demo-prod"):
                prefix="plan-"+target
                binding=PlanBinding(**o.json(prefix+"-binding")["binding"])
                verify_plan(binding,o.path(prefix+"-binary"),inputs(),target,o.json(prefix+"-state-before"),
                    o.path(prefix+"-inputs"),o.path(prefix+"-locks"))
                validate_index_plan(o.json(prefix+"-shown"),raw["search_host"],target)
                applied=o.json(prefix+"-apply-result")
                if applied["returncode"]!=0 or applied["binding"]!=asdict(binding): raise DeliveryRefused("plan")
            validation=o.json("offline-boundaries")
            if validation["source_sha"]!=a.source_sha or not all(validation[k] is True for k in (
                "tampered_plan_refused","stale_state_refused","missing_gate_refused","skipped_gate_refused","wrong_source_refused")):
                raise DeliveryRefused("gate")
        perform(3,plans)
        perform(4,lambda:o.gate("test",inputs()))
        def promotion():
            metrics=o.gate("prod",inputs())
            verify_checks(o.json("checks-snapshot"),raw["checks_expected"])
            suites=o.json("checks-evaluations")
            build=o.build("release")
            evaluations=require_evaluations(suites,raw["checks_expected"]["checks"],build["orchestrationPlan"]["planId"])
            approval=o.json("prod-approval")
            association=o.json("prod-approval-map")
            approval_checks={check["id"] for check in raw["checks_expected"]["checks"] if check["kind"]=="approval"}
            if (association["build_id"]!=build["id"] or association["approval_id"]!=approval["id"]
                or not any(row["id"]==association["check_run_id"] and row["checkConfigurationRef"]["id"] in approval_checks
                    for row in evaluations)): raise DeliveryRefused("checks")
            if (approval["status"]!="approved" or not any(step["status"]=="approved"
                and step["actualApprover"]["id"]==raw["owner_ado_id"] for step in approval["steps"])):
                raise DeliveryRefused("checks")
            return metrics
        perform(5,promotion)
        def negative():
            metrics=o.gate("negative",inputs(),negative=True)
            before=o.json("prod-before-negative"); after=o.json("prod-after-negative")
            if (canonical(before)!=canonical(after) or set(before)!={"schema_sha256","state_sha256","documents_sha256"}
                or any(not re.fullmatch(r"[0-9a-f]{64}",value) for value in before.values())):
                raise DeliveryRefused("gate")
            return metrics
        perform(6,negative)
        def scheduled():
            metrics=o.gate("verify",inputs())
            definition=o.json("definition-disabled")
            queue=o.json("queue-after")
            if definition["id"]!=raw["pipeline_id"] or definition["queueStatus"]!="disabled" or any(
                row["status"]!="completed" for row in queue["value"] if row["definition"]["id"]==raw["pipeline_id"]):
                raise DeliveryRefused("gate")
            return metrics
        perform(7,scheduled)
        def closeout():
            cleanup=read(directory/"cleanup-result.json")
            attempts=[read(p) for p in sorted(directory.glob("cleanup-attempt-*.json"))]
            if not attempts: raise MissingOriginal()
            first_delete=o.json("cleanup-first-delete")
            if first_delete["session"]!=a.session or first_delete["source_sha"]!=a.source_sha: raise DeliveryRefused("authority")
            actual=dict(cleanup,cleanup_started_at=first_delete["at"])
            require_group_absence([o.json("group-absence-"+str(i)) for i in range(len(raw["owned_group_ids"]))],raw["owned_group_ids"])
            for component in manifest["components"]:
                cwd=raw["owner_session_path"].rstrip("/")+"/"+component["directory"]
                try:
                    command=o.json("empty-state-command-"+component["name"])
                except MissingOriginal:
                    require_index_absence(o.json("empty-state-absence-"+component["name"]),component,raw,manifest)
                else: require_empty_state(command,o.json("empty-state-result-"+component["name"]),cwd)
            baseline=read(directory/"baseline.json")
            retained=read(directory/f"retained-after-{cleanup['attempt']}.json")
            revoked=o.json("pat-revocation"); exited=o.json("helper-exit")
            original=o.json("definition-original"); restored=o.json("definition-disabled")
            if (cleanup["source_sha"]!=a.source_sha or cleanup["manifest_sha256"]!=a.manifest_sha256
                or cleanup["physical_pass"] is not True or cleanup["canonical_absence"] is not True
                or cleanup.get("quiescence_before") is not True
                or cleanup["empty_states"] is not True or not timing_pass(actual,raw)
                or not retained_unchanged(baseline["retained"],retained)
                or revoked.get("kind")!="human-pat-revoked" or not revoked.get("message_ref")
                or revoked["session"]!=a.session or instant(revoked["at"])<instant(actual["cleanup_started_at"])
                or exited["active_owned"]!=[] or exited["session"]!=a.session
                or original["variables"]!=restored["variables"]
                or original["id"]!=restored["id"] or restored["queueStatus"]!="disabled"):
                raise DeliveryRefused("gate")
            from .watchdog import process_present
            if not {"watchdog-owned.json","cleanup-native.json"}.issubset({row["name"] for row in exited["helpers"]}):
                raise DeliveryRefused("gate")
            if any(process_present(row["expected"],row["actual"]) for row in exited["helpers"]):
                raise DeliveryRefused("gate")
            for key in ("repository","process","queue","triggers","options","properties","jobAuthorizationScope"):
                if canonical(original.get(key))!=canonical(restored.get(key)): raise DeliveryRefused("gate")
        perform(8,closeout)
    return results


def public_projection(authority: dict,results: dict) -> dict:
    projection={key:value if isinstance(value,str) and re.fullmatch(pattern,value) else None
        for key,pattern in (("session",r"[a-z][a-z0-9-]{3,11}"),("source_sha",r"[0-9a-f]{40}"),("release_digest",r"[0-9a-f]{64}"))
        for value in (authority.get(key),)}
    projection["cost_actual"]=None  # Measured cost needs its own reviewed billing original.
    projection["checks"]={}
    codes={"originals-verified","missing-original","authority","identity","bundle","inputs","plan","gate","checks","invalid-original"}
    for key,value in results.items():
        if key not in {"P8-"+str(i) for i in range(1,9)}: continue
        row={"status":value.get("status") if value.get("status") in ("PASS","FAIL","NOT RUN") else "FAIL",
             "code":value.get("code") if value.get("code") in codes else "invalid-original"}
        for field in ("evaluated","correct","errors","missing","skipped"):
            if type(value.get(field)) is int and 0<=value[field]<=229: row[field]=value[field]
        projection["checks"][key]=row
    return projection


def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument("--session",type=Path,required=True)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args(argv)
    results=grade(args.session)
    raw=read(args.session/"authority.json") if (args.session/"authority.json").exists() else {}
    public=public_projection(raw,results)
    if args.output: exclusive_json(args.output,public)
    print(" ".join(key+"="+value["status"].replace(" ","-") for key,value in results.items()))
    return 0 if all(v["status"]=="PASS" for v in results.values()) else 2


if __name__=="__main__": raise SystemExit(main())
