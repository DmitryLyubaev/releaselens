"""Fixed-stage entrypoint. Secrets stay in process memory; public output is allowlisted."""
import argparse
import base64
from contextlib import suppress
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET

import httpx

from app.delivery_search.bundle import fetch_bundle,read_json
from app.delivery_search.client import UPLOAD_CHECKS,UploadRefused,document_count,upload_bundle
from app.delivery_search.contracts import REQUEST_CHECKS,DeliveryRefused,RequestRefused,SearchTarget,canonical,exclusive_json,sha256_file
from app.delivery_search.gate import grade_rows,run_gate,write_gate
from app.retrieval.arms import ArmResult,Hit
from app.retrieval.azure_auth import TokenSource
from .checks import verify_checks
from .identity import IDENTITY_CHECKS,IdentityRefused,verify_principal,terraform_environment
from .plans import PlanBinding,apply_exact,bind_plan,validate_index_plan,verify_plan
from .storage import BlobStore
from .authority_transport import decode_authority,read_authority
from .readiness import require_owner_id
from .metadata import require_policy

ROOT = Path(__file__).resolve().parents[2]
STAGES = ("build","test","plan-prod","prod","negative","verify")


class StageBudget:
    """Leave a minute for publishing and one-shot agent shutdown before the YAML limit."""
    def __init__(self,stage,cutoff,*,clock=time.monotonic):
        self.clock = clock
        limit = {"build":300,"test":720,"plan-prod":300,"prod":660,"negative":300,"verify":300}[stage]
        self.ends = clock()+min(limit,(cutoff-datetime.now(timezone.utc)).total_seconds())

    def remaining(self,maximum):
        seconds = self.ends-self.clock()
        if seconds <= 0: raise DeliveryRefused("deadline")
        return min(maximum,seconds)

    def before_request(self,request):
        self.remaining(20)

    def after_response(self,response):
        self.remaining(20)


def dependencies(stage: str) -> tuple[str,...]:
    try:
        return {"build":(),"test":("test-plan",),"plan-prod":("test-gate",),"prod":("test-gate","prod-plan"),
                "negative":("prod-gate",),"verify":("prod-gate",)}[stage]
    except KeyError: raise DeliveryRefused("authority") from None


def connection_role(stage: str,reason: str) -> str:
    if stage not in STAGES or reason not in ("Manual","Schedule") or (stage == "verify") != (reason == "Schedule"):
        raise DeliveryRefused("authority")
    return "verify" if stage == "verify" else "prod" if stage in ("plan-prod","prod") else "test"


def require_gate(receipt: dict,expected: dict) -> None:
    try:
        gate = receipt["gate"]
        if (any(receipt[k] != expected[k] for k in ("source_sha","release_digest","run_id"))
            or gate["expected"] != 229 or gate["evaluated"] != 229 or type(gate["correct"]) is not int
            or gate["correct"] < 120 or gate["correct"] > 229
            or gate["errors"] != 0 or gate["missing"] != 0 or gate["skipped"] != 0 or gate["passed"] is not True):
            raise DeliveryRefused("gate")
    except (KeyError,TypeError): raise DeliveryRefused("gate") from None


def negative_passes(result,rows) -> bool:
    return (not result.passed and result.correct==0 and result.evaluated==229
        and result.errors==result.missing==result.skipped==0 and len(rows)==229 and all(not row.hits for row in rows))


def require_result_files(directory: Path) -> None:
    try:
        data = read_json(directory / "results.json")
        suite = ET.fromstring((directory / "results.xml").read_bytes())
        cases = suite.findall("testcase")
        if (len(cases) != 230 or suite.get("tests") != "230" or len({c.get("name") for c in cases}) != 230
            or len(data["rows"]) != 229 or len(data["gate"]["cases"]) != 229
            or any(c.find("skipped") is not None for c in cases)):
            raise DeliveryRefused("gate")
    except (OSError,ValueError,ET.ParseError,KeyError,TypeError):
        raise DeliveryRefused("gate") from None


def context(path: Path,stage: str) -> tuple[dict,dict]:
    """Runs before AzureCLI@2 login as well as inside the authenticated task."""
    a = read_authority(path) if path.exists() else decode_authority(os.environ["P8_AUTHORITY_JSON"].encode())
    if hashlib.sha256(canonical(a)).hexdigest() != os.environ["P8_AUTHORITY_SHA256"]:
        raise DeliveryRefused("authority")
    require_policy(a)
    require_owner_id(a)
    role = connection_role(stage,os.environ["BUILD_REASON"])
    now = datetime.now(timezone.utc)
    if (not a.get("approval_ref") or not re.fullmatch(r"[a-z][a-z0-9-]{3,11}",a["session"])
        or os.environ["BUILD_SOURCEBRANCH"] != "refs/heads/main"
        or os.environ["BUILD_SOURCEVERSION"] != a["source_sha"]
        or os.environ["P8_SOURCE_SHA"] != a["source_sha"]
        or int(os.environ["SYSTEM_DEFINITIONID"]) != a["pipeline_id"] or a["pipeline_id"] == 1
        or int(os.environ.get("SYSTEM_JOBATTEMPT","0")) != 1
        or int(os.environ.get("SYSTEM_STAGEATTEMPT","0")) != 1
        or now >= datetime.fromisoformat(a["queue_cutoff_at"])
        or a["apply_started_at"] is None):
        raise DeliveryRefused("authority")
    for command,value in [(["git","rev-parse","HEAD"],a["source_sha"]),
                          (["git","rev-parse","HEAD^{tree}"],a["tree_sha"])]:
        result = subprocess.run(command,cwd=ROOT,capture_output=True,text=True,check=True,timeout=10)
        if result.stdout.strip() != value: raise DeliveryRefused("authority")
    if subprocess.run(["git","diff","--quiet","HEAD","--"],cwd=ROOT,timeout=10).returncode != 0:
        raise DeliveryRefused("authority")
    principal = a["principals"][role]
    if not all(principal.get(key) for key in ("service_connection_id","oidc_request_origin","oidc_path_prefix")):
        raise DeliveryRefused("identity")
    return a,principal


def jwt_claims(token: str,check: str = "jwt-format") -> dict:
    try:
        parts = token.split(".")
        if len(parts) != 3: raise IdentityRefused(check)
        return json.loads(base64.urlsafe_b64decode(parts[1]+"="*(-len(parts[1])%4)))
    except (ValueError,KeyError,TypeError): raise IdentityRefused(check) from None


def identity_receipt(account: dict,claims: dict) -> dict:
    return {"at":datetime.now(timezone.utc).isoformat(),
        "account":{k:account[k] for k in ("id","tenantId","state","user")},
        "claims":{kind:{k:value[k] for k in keys if k in value} for kind,value,keys in (
            ("federation",claims["federation"],("iss","sub","aud","exp")),
            ("access",claims["access"],("tid","oid","appid","azp","exp")))}}


def authenticate(principal: dict) -> tuple[dict,dict,dict]:
    """The same authentication boundary for diagnostic and delivery jobs."""
    assertion = os.environ.get("idToken")
    if not assertion: raise IdentityRefused("assertion-missing")
    if os.environ.get("servicePrincipalKey"): raise IdentityRefused("secret-present")
    account = Runtime.az_json(["account","show","--output","json"])
    access = Runtime.az_json(["account","get-access-token","--resource","https://management.azure.com/","--output","json"])
    try:
        access_token = access["accessToken"]
    except (KeyError,TypeError):
        raise IdentityRefused("cli-access-token") from None
    claims = {"federation":jwt_claims(assertion),"access":jwt_claims(access_token,"access-format")}
    verify_principal(account,claims,principal)
    return account,claims,terraform_environment(principal,assertion)


class Runtime:
    def __init__(self,a: dict,principal: dict,stage: str,receipt_dir: Path,client: httpx.Client):
        require_policy(a)
        self.a,self.principal,self.stage,self.client = a,principal,stage,client
        self.budget = StageBudget(stage,datetime.fromisoformat(a["queue_cutoff_at"]))
        client.event_hooks["request"].append(self.budget.before_request)
        client.event_hooks["response"].append(self.budget.after_response)
        self.run_id = int(os.environ["BUILD_BUILDID"])
        if self.run_id < 1: raise DeliveryRefused("authority")
        self.work = Path.home()/"p8"/a["session"]/a["source_sha"]
        self.work.mkdir(parents=True,exist_ok=True)
        self.publish = receipt_dir
        self.publish.mkdir(parents=True,exist_ok=True)
        self.proof = {"source_sha":a["source_sha"],"release_digest":a["release_digest"],"run_id":self.run_id}
        account,claims,self.tf_env = authenticate(principal)
        # Only a digest/provenance-safe projection is retained; never the assertion/access token.
        exclusive_json(self.publish/"identity.json",self.proof | identity_receipt(account,claims) | {
            "role":connection_role(stage,os.environ["BUILD_REASON"]),"verified":True,"scheme":"WorkloadIdentityFederation"})
        self.store = BlobStore(a["storage_host"],TokenSource("https://storage.azure.com/.default",principal["tenant_id"]),client,
            metadata_policy=a['metadata_policy'])
        self.store.require_workload_open()
        # Reserve before fetching any corpus/query context. A fresh agent cannot replay a set.
        self.store.put_json(f"operations/{stage}.json",self.proof | {"stage":stage})
        # Preserve the exact token-free runtime original on the existing private
        # evidence channel; artifact download access is not a prerequisite.
        self.store.put_once("evidence",f"identities/{stage}.json",(self.publish/"identity.json").read_bytes())
        def metadata_budget(request):
            if request.url.host==a["search_host"]:
                if request.method=="GET": self.store.reserve_metadata()
                else: self.store.require_workload_open()
        client.event_hooks["request"].append(metadata_budget)
        checks = self.store.get("inputs","checks/snapshot.json")
        if hashlib.sha256(checks).hexdigest() != a["checks_snapshot_sha256"]: raise DeliveryRefused("checks")
        verify_checks(json.loads(checks),a["checks_expected"])
        self.bundle = fetch_bundle(f"https://{a['storage_host']}/inputs/releases/{a['release_digest']}/release.tar.gz",
            a["release_digest"],self.work/"verified",self.store.tokens,client,allowed_host=a["storage_host"])
        if self.bundle.manifest.source_sha != a["source_sha"]: raise DeliveryRefused("authority")
        for bundled,source in [("scorer.py",ROOT/"eval/app/retrieval/score.py"),
            ("requirements.lock",ROOT/"build/delivery-agents/requirements.lock"),
            ("provider.lock.hcl",ROOT/"infra/pipelines/index/.terraform.lock.hcl")]:
            if sha256_file(self.bundle.root/bundled) != sha256_file(source): raise DeliveryRefused("bundle")
        self.search_tokens = TokenSource("https://search.azure.com/.default",principal["tenant_id"])
        self.claim_release()

    @staticmethod
    def az_json(args: list[str]) -> dict:
        check = "cli-account" if args[:2] == ["account","show"] else "cli-access-token"
        try:
            result = subprocess.run(["az",*args],capture_output=True,text=True,timeout=30)
            if result.returncode != 0: raise IdentityRefused(check)
            value = json.loads(result.stdout)
            if not isinstance(value,dict): raise IdentityRefused(check)
            return value
        except (OSError,subprocess.TimeoutExpired,ValueError):
            raise IdentityRefused(check) from None

    def claim_release(self):
        if self.stage == "build":
            self.store.put_json("claims/release.json",self.proof)
            self.release = self.proof
        else:
            self.release = self.store.json("evidence","claims/release.json")
            if any(self.release[k] != self.proof[k] for k in ("source_sha","release_digest")):
                raise DeliveryRefused("authority")
            if self.stage in ("test","plan-prod","prod") and self.release["run_id"] != self.run_id:
                raise DeliveryRefused("authority")

    def target(self,name: str) -> SearchTarget:
        host = self.a["search_host"]
        return SearchTarget("https://"+host,"p8-"+name,host)

    def prepare_workspace(self,target: str) -> Path:
        directory = self.work/("index-"+target)
        directory.mkdir()
        for name in ("main.tf","variables.tf","versions.tf","outputs.tf","backend.tf",".terraform.lock.hcl"):
            shutil.copyfile(ROOT/"infra/pipelines/index"/name,directory/name)
        return directory

    def initialize(self,directory: Path,target: str):
        container = "test-state" if target == "test" else "prod-state"
        args = ["terraform","init","-input=false","-reconfigure","-lockfile=readonly",
            "-backend-config=storage_account_name="+self.a["storage_account_name"],
            "-backend-config=container_name="+container]
        self.command(args,directory,"init-"+target,120)

    def terraform(self,target: str) -> Path:
        directory=self.prepare_workspace(target)
        self.initialize(directory,target)
        return directory

    def command(self,args: list[str],cwd: Path,name: str,seconds: int):
        if datetime.now(timezone.utc) >= datetime.fromisoformat(self.a["queue_cutoff_at"]):
            raise DeliveryRefused("deadline")
        result = self.process(args,cwd=cwd,env=self.tf_env,capture_output=True,text=True,timeout=seconds)
        exclusive_json(self.work/(name+".private.json"),{"returncode":result.returncode,"stdout":result.stdout,"stderr":result.stderr})
        if result.returncode != 0: raise DeliveryRefused("plan")
        return result

    def process(self,args,**kwargs):
        self.store.require_workload_open()
        kwargs["timeout"] = self.budget.remaining(kwargs.get("timeout",120))
        result = subprocess.run(args,**kwargs)
        self.budget.remaining(1)
        return result

    def plan(self,target: str):
        directory = self.terraform(target)
        inputs = directory/"inputs.tfvars.json"
        inputs.write_bytes(canonical({"subscription_id":self.principal["subscription_id"],"tenant_id":self.principal["tenant_id"],
            "target":target,"search_host":self.a["search_host"],"schema_file":str(self.bundle.root/"schema.json")}))
        self.command(["terraform","plan","-input=false","-out=plan.tfplan","-var-file=inputs.tfvars.json"],directory,"plan-"+target,120)
        shown = self.command(["terraform","show","-json","plan.tfplan"],directory,"show-"+target,30)
        validate_index_plan(json.loads(shown.stdout),self.a["search_host"],target)
        binding = bind_plan(directory/"plan.tfplan",self.bundle,target,self.store.state(target),inputs,directory/".terraform.lock.hcl")
        envelope = self.proof | {"binding":asdict(binding)}
        plan_publish = self.publish/(target+"-plan")
        plan_publish.mkdir()
        for filename in ("plan.tfplan","inputs.tfvars.json",".terraform.lock.hcl"):
            data = (directory/filename).read_bytes()
            self.store.put_once("evidence",f"plans/{target}/{filename}",data)
            (plan_publish/filename).write_bytes(data)
        self.store.put_json(f"plans/{target}/binding.json",envelope)
        self.store.put_once("evidence",f"plans/{target}/shown.json",shown.stdout.encode())
        exclusive_json(plan_publish/"binding.json",envelope)
        with (plan_publish/"shown.json").open("xb") as out: out.write(shown.stdout.encode())
        exclusive_json(plan_publish/"summary.json",{"target":target,"creates":1,"updates":0,"deletes":0,
                                                    "plan_sha256":binding.plan_sha256,"release_digest":self.bundle.digest})
        suite=ET.Element("testsuite",name="Exact saved plan",tests="1",failures="0",errors="0",skipped="0")
        ET.SubElement(suite,"testcase",classname="search.plan",name="create-only-bound-plan")
        with (plan_publish/"contract.xml").open("xb") as out: out.write(ET.tostring(suite,encoding="utf-8"))

    def apply_and_load(self,target: str):
        validated=self.target(target)
        envelope = self.store.json("evidence",f"plans/{target}/binding.json")
        if any(envelope[k] != self.proof[k] for k in self.proof): raise DeliveryRefused("plan")
        directory = self.prepare_workspace(target)
        for name in ("plan.tfplan","inputs.tfvars.json"):
            (directory/name).write_bytes(self.store.get("evidence",f"plans/{target}/{name}",maximum=16*1024*1024))
        expected_lock = self.store.get("evidence",f"plans/{target}/.terraform.lock.hcl")
        if expected_lock != (directory/".terraform.lock.hcl").read_bytes(): raise DeliveryRefused("plan")
        binding=PlanBinding(**envelope["binding"])
        before=self.store.state(target)
        verify_plan(binding,directory/"plan.tfplan",self.bundle,target,before,directory/"inputs.tfvars.json",directory/".terraform.lock.hcl")
        shown=self.store.json("evidence",f"plans/{target}/shown.json")
        validate_index_plan(shown,validated.allowed_host,target)
        # The only authorized schema operation is creating an absent new index.
        response = self.client.get(f"{validated.endpoint}/indexes/{validated.index}",
            params={"api-version":"2024-07-01"},headers={"Authorization":"Bearer "+self.search_tokens.token()},
            follow_redirects=False,timeout=20)
        if response.status_code != 404: raise DeliveryRefused("plan")
        published=self.publish/(target+"-apply"); published.mkdir()
        exclusive_json(published/"state-before.json",before)
        self.store.put_once("evidence",f"applies/{target}/state-before.json",(published/"state-before.json").read_bytes())
        intent=self.proof | {"session":self.a["session"],
            "component":"index-"+target,"manifest_sha256":self.a["manifest_sha256"],"binding":asdict(binding)}
        exclusive_json(published/"intent.json",intent)
        self.store.put_once("evidence",f"applies/{target}/intent.json",(published/"intent.json").read_bytes())
        reservation=self.work/("apply-"+target+".json")
        try:
            self.initialize(directory,target)
            apply_exact(binding,directory/"plan.tfplan",self.bundle,target,
                directory/"inputs.tfvars.json",directory/".terraform.lock.hcl",reservation=reservation,
                cwd=directory,env=self.tf_env,state_reader=lambda:self.store.state(target),runner=self.process)
        except Exception as exc:
            original=reservation.with_suffix(".result.json")
            if not original.exists():
                exclusive_json(original,{"binding":asdict(binding),"returncode":None,
                    "error_type":type(exc).__name__,"error":str(exc)})
            raise
        finally:
            original=reservation.with_suffix(".result.json")
            if original.exists():
                data=original.read_bytes()
                with (published/"result.json").open("xb") as out: out.write(data)
                self.store.put_once("evidence",f"applies/{target}/result.json",data)
        upload_reservation=self.work/("upload-"+target+".json")
        # Verify count access and the reused metadata-budget slot before bulk work.
        if document_count(validated,self.search_tokens,self.client) != 0:
            raise DeliveryRefused("plan")
        try:
            upload_bundle(self.bundle,validated,self.search_tokens,self.client,reservation=upload_reservation)
        finally:
            metrics=upload_reservation.with_suffix(".metrics.json")
            if metrics.exists():
                # Published even on refusal; numeric diagnostics contain no corpus,
                # response text or credentials. Preserve any original upload error.
                with suppress(OSError):
                    with (self.publish/metrics.name).open("xb") as out:
                        out.write(metrics.read_bytes())

    def measure(self,target: str,*,negative=False) -> bool:
        result,rows = run_gate(self.bundle,self.target(target),read_json(self.bundle.root/(
            "negative.json" if negative else "s2.json")),self.search_tokens,self.client)
        output = self.publish/"gate"
        write_gate(result,rows,output)
        require_result_files(output)
        receipt = self.proof | {"stage":self.stage,"gate":asdict(result),"reason":os.environ["BUILD_REASON"],
            "expected_negative":negative,"acceptance_pass":negative_passes(result,rows) if negative else result.passed}
        for name in ("results.json","results.xml"):
            data = (output/name).read_bytes()
            self.store.put_once("evidence",f"gates/{self.stage}/{name}",data)
            receipt[name+"_sha256"] = hashlib.sha256(data).hexdigest()
        self.store.put_json("receipts/"+self.stage+".json",receipt)
        exclusive_json(output/"receipt.json",receipt)
        if not receipt["acceptance_pass"]: raise DeliveryRefused("gate")
        return not negative

    def prior_gate(self,stage: str):
        receipt = self.store.json("evidence","receipts/"+stage+".json")
        require_gate(receipt,self.release)
        data = self.store.get("evidence",f"gates/{stage}/results.json")
        xml = self.store.get("evidence",f"gates/{stage}/results.xml")
        if hashlib.sha256(data).hexdigest()!=receipt["results.json_sha256"] or hashlib.sha256(xml).hexdigest()!=receipt["results.xml_sha256"]:
            raise DeliveryRefused("gate")
        raw = json.loads(data)
        rows = [ArmResult(r["qid"],r["arm"],[Hit(**h) for h in r["hits"]],r["ms"],r["error"],r.get("query_tokens",0)) for r in raw["rows"]]
        if canonical(asdict(grade_rows(self.bundle,rows))) != canonical(raw["gate"]): raise DeliveryRefused("gate")
        require_gate(receipt | {"gate":raw["gate"]},self.release)
        suite = ET.fromstring(xml)
        if len(suite.findall("testcase")) != 230 or suite.get("failures") != "0" or suite.get("skipped") != "0":
            raise DeliveryRefused("gate")

    def execute(self) -> bool:
        if self.stage == "build":
            result = self.process([sys.executable,"-m","pytest","--strict-config","tests/delivery_search","tests/retrieval","-q",
                "--junitxml="+str(self.publish/"offline.xml")],cwd=ROOT/"eval",capture_output=True,text=True,timeout=240)
            exclusive_json(self.work/"offline-tests.private.json",{"returncode":result.returncode,"stdout":result.stdout,"stderr":result.stderr})
            if result.returncode: raise DeliveryRefused("gate")
            self.plan("test")
        elif self.stage == "test":
            self.apply_and_load("test")
            self.measure("test")
        elif self.stage == "plan-prod":
            self.prior_gate("test")
            self.plan("demo-prod")
        elif self.stage == "prod":
            self.prior_gate("test")
            self.apply_and_load("demo-prod")
            self.measure("demo-prod")
        else:
            self.prior_gate("prod")
            if self.stage == "verify":
                negative = self.store.json("evidence","receipts/negative.json")
                if (negative["source_sha"] != self.proof["source_sha"] or negative["release_digest"] != self.bundle.digest
                    or negative.get("expected_negative") is not True or negative.get("acceptance_pass") is not True):
                    raise DeliveryRefused("gate")
            return self.measure("test" if self.stage=="negative" else "demo-prod",negative=self.stage=="negative")
        return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage",choices=STAGES)
    parser.add_argument("--authority",type=Path,required=True)
    parser.add_argument("--receipt-dir",type=Path,required=True)
    parser.add_argument("--guard-only",action="store_true")
    args = parser.parse_args(argv)
    try:
        a,principal = context(args.authority,args.stage)
        if args.guard_only:
            print("status=pass code=source-guard")
            return 0
        with httpx.Client(timeout=20,follow_redirects=False) as client:
            runtime = Runtime(a,principal,args.stage,args.receipt_dir,client)
            passed = runtime.execute()
        print("status=pass code=stage" if passed else "status=expected-failure code=quality")
        return 0 if passed else 17
    except Exception as exc:
        code = DeliveryRefused(exc.code).code if isinstance(exc,DeliveryRefused) else "internal"
        check = exc.check if isinstance(exc,IdentityRefused) and exc.check in IDENTITY_CHECKS else "unclassified"
        if isinstance(exc,UploadRefused) and exc.check in UPLOAD_CHECKS:
            check = exc.check
        details={}
        if isinstance(exc,RequestRefused):
            check=exc.check if isinstance(exc.check,str) and exc.check in REQUEST_CHECKS else "unclassified"
            details['http_status']=exc.http_status if type(exc.http_status) is int and 100<=exc.http_status<=599 else None
        # The directory published by every stage must contain the early refusal.
        # Exception text and raw tracebacks remain outside this artifact.
        with suppress(OSError):
            args.receipt_dir.mkdir(parents=True,exist_ok=True)
            exclusive_json(args.receipt_dir/"failure.json",{
                "kind":"stage-failure","stage":args.stage,"code":code,"check":check,**details})
        with suppress(OSError):
            args.receipt_dir.parent.mkdir(parents=True,exist_ok=True)
            with (args.receipt_dir.parent/"failure-private.txt").open("x",encoding="utf-8") as out:
                out.write(traceback.format_exc())
        print("status=refused code="+code)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
