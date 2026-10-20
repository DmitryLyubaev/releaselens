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
import traceback
import xml.etree.ElementTree as ET

import httpx

from app.delivery_search.bundle import fetch_bundle,read_json
from app.delivery_search.client import UPLOAD_CHECKS,UploadRefused,document_count,upload_bundle
from app.delivery_search.contracts import REQUEST_CHECKS,DeliveryRefused,RequestRefused,SearchTarget,canonical,exclusive_json,sha256_file
from app.delivery_search.gate import grade_rows,run_gate,write_gate
from app.retrieval.arms import ArmResult,Hit
from app.retrieval.azure_auth import TokenSource
from .identity import IDENTITY_CHECKS,IdentityRefused,verify_principal,terraform_environment
from .plans import PlanBinding,apply_exact,bind_plan,validate_index_plan,verify_plan
from .settings import Settings,load_settings,role_for
from .storage import STATES,BlobStore,run_key

ROOT = Path(__file__).resolve().parents[2]
STAGES = ("build","plan-test","test","plan-prod","prod","negative","promote-negative","verify")
# Stages of the release run itself; the negative and verify runs check the release the pointer names.
RELEASE_STAGES = ("build","plan-test","test","plan-prod","prod")
# Gates from earlier stages, or other runs, belong to the same release, not necessarily the same commit.
RELEASE_KEYS = ("release_digest","release_run_id")
STACK_FILES = ("main.tf","variables.tf","versions.tf","outputs.tf","backend.tf",".terraform.lock.hcl")
BUNDLED_COPIES = (("scorer.py","eval/app/retrieval/score.py"),
    ("requirements.lock","build/delivery-agents/requirements.lock"),
    ("provider.lock.hcl","infra/pipelines/index/.terraform.lock.hcl"))


def require_gate(receipt: dict,expected: dict) -> None:
    try:
        gate = receipt["gate"]
        if (any(receipt[k] != expected[k] for k in RELEASE_KEYS)
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


def check_bundle(bundle) -> dict:
    """The frozen bundle may predate the running commit; its scorer and lock copies may not differ."""
    for bundled,source in BUNDLED_COPIES:
        try:
            same = sha256_file(bundle.root/bundled) == sha256_file(ROOT/source)
        except OSError:
            same = False
        if not same: raise DeliveryRefused("bundle")
    return {"bundle_digest":bundle.digest,"bundle_source_sha":bundle.manifest.source_sha}


def context(stage: str) -> tuple[Settings,dict]:
    """Runs before AzureCLI@2 login as well as inside the authenticated task."""
    # A negative candidate is never promoted, whatever the settings say.
    if stage == "promote-negative" or stage not in STAGES: raise DeliveryRefused("authority")
    reason = os.environ.get("BUILD_REASON","")
    # The scheduled run only verifies; every other stage is a manual release or negative run.
    if reason not in ("Manual","Schedule") or (stage == "verify") != (reason == "Schedule"):
        raise DeliveryRefused("authority")
    return source_context(stage,reason)


def source_context(stage: str,reason: str) -> tuple[Settings,dict]:
    """main, the delivery settings and a clean checkout of the running commit, for any entrypoint's stage."""
    if os.environ.get("BUILD_SOURCEBRANCH") != "refs/heads/main": raise DeliveryRefused("authority")
    try:
        role = role_for(stage,reason)
    except DeliveryRefused:
        raise DeliveryRefused("authority") from None
    settings = load_settings(os.environ)
    source = os.environ.get("BUILD_SOURCEVERSION","")
    if not re.fullmatch(r"[0-9a-f]{40}",source): raise DeliveryRefused("authority")
    head = subprocess.run(["git","rev-parse","HEAD"],cwd=ROOT,capture_output=True,text=True,timeout=10)
    if head.returncode != 0 or head.stdout.strip() != source: raise DeliveryRefused("authority")
    if subprocess.run(["git","diff","--quiet","HEAD","--"],cwd=ROOT,timeout=10).returncode != 0:
        raise DeliveryRefused("authority")
    return settings,settings.principals[role]


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


def copy_stack(stack: str,directory: Path) -> Path:
    """A fresh workspace holding only the reviewed configuration: no tfvars, no stray files."""
    directory.mkdir(parents=True)
    for name in STACK_FILES:
        shutil.copyfile(ROOT/"infra/pipelines"/stack/name,directory/name)
    return directory


def init_args(storage_account_name: str,target: str) -> list[str]:
    """Each target initializes against its own state container and key, with the reviewed provider locks."""
    if target not in STATES: raise DeliveryRefused("target")
    container,key = STATES[target]
    return ["terraform","init","-input=false","-reconfigure","-lockfile=readonly",
        "-backend-config=storage_account_name="+storage_account_name,
        "-backend-config=container_name="+container,"-backend-config=key="+key]


def index_inputs(settings: Settings,target: str,schema_file: Path) -> bytes:
    return canonical({"subscription_id":settings.subscription_id,"tenant_id":settings.tenant_id,
        "target":target,"search_host":settings.search_host,"schema_file":str(schema_file)})


def http_client() -> httpx.Client:
    return httpx.Client(timeout=20,follow_redirects=False)


class Runtime:
    def __init__(self,settings: Settings,stage: str,receipt_dir: Path,client: httpx.Client):
        self.settings,self.stage,self.client = settings,stage,client
        role = role_for(stage,os.environ["BUILD_REASON"])
        self.principal = settings.principals[role]
        build_id = os.environ.get("BUILD_BUILDID","")
        if not re.fullmatch(r"[1-9][0-9]{0,15}",build_id): raise DeliveryRefused("authority")
        self.publish = receipt_dir
        self.publish.mkdir(parents=True,exist_ok=True)
        account,claims,self.tf_env = authenticate(self.principal)
        source = {"source_sha":os.environ["BUILD_SOURCEVERSION"],"release_digest":settings.release_digest}
        # Only a digest/provenance-safe projection is retained; never the assertion/access token.
        # Written before any storage call, so an early refusal still publishes who ran.
        exclusive_json(self.publish/"identity.json",source | {"build_id":int(build_id)} | identity_receipt(account,claims) | {
            "role":role,"verified":True,"scheme":"WorkloadIdentityFederation"})
        storage_tokens = TokenSource("https://storage.azure.com/.default",settings.tenant_id)
        self.store = BlobStore(settings.storage_host,storage_tokens,client)
        if stage in RELEASE_STAGES:
            self.proof = source | {"release_run_id":int(build_id)}
            # Only the build of this run names the release; later stages need its pointer unchanged.
            if stage != "build" and self.store.read_pointer() != self.proof: raise DeliveryRefused("authority")
        else:
            pointer = self.store.read_pointer()
            release_run_id = pointer.get("release_run_id")
            if (type(release_run_id) is not int or release_run_id < 1
                or pointer.get("release_digest") != settings.release_digest):
                raise DeliveryRefused("authority")
            self.proof = source | {"release_run_id":release_run_id}
        self.work = Path.home()/"p8"/str(self.proof["release_run_id"])
        self.work.mkdir(parents=True,exist_ok=True)
        # Preserve the exact token-free runtime original on the private evidence channel.
        self.store.put_once("evidence",self.key(f"identities/{stage}.json"),(self.publish/"identity.json").read_bytes())
        self.bundle = fetch_bundle(f"https://{settings.storage_host}/inputs/releases/{settings.release_digest}/release.tar.gz",
            settings.release_digest,self.work/"verified",storage_tokens,client,allowed_host=settings.storage_host)
        self.bundle_record = check_bundle(self.bundle)
        exclusive_json(self.publish/"bundle.json",self.proof | self.bundle_record)
        self.search_tokens = TokenSource("https://search.azure.com/.default",settings.tenant_id)

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

    def key(self,name: str) -> str:
        return run_key(self.proof["release_run_id"],name)

    def target(self,name: str) -> SearchTarget:
        host = self.settings.search_host
        return SearchTarget("https://"+host,"p8-"+name,host)

    def prepare_workspace(self,target: str) -> Path:
        return copy_stack("index",self.work/("index-"+target))

    def initialize(self,directory: Path,target: str):
        self.command(init_args(self.settings.storage_account_name,target),directory,"init-"+target,120)

    def terraform(self,target: str) -> Path:
        directory=self.prepare_workspace(target)
        self.initialize(directory,target)
        return directory

    def command(self,args: list[str],cwd: Path,name: str,seconds: int):
        result = self.process(args,cwd=cwd,env=self.tf_env,capture_output=True,text=True,timeout=seconds)
        exclusive_json(self.work/(name+".private.json"),{"returncode":result.returncode,"stdout":result.stdout,"stderr":result.stderr})
        if result.returncode != 0: raise DeliveryRefused("plan")
        return result

    def process(self,args,**kwargs):
        return subprocess.run(args,**kwargs)

    def plan(self,target: str):
        directory = self.terraform(target)
        inputs = directory/"inputs.tfvars.json"
        inputs.write_bytes(index_inputs(self.settings,target,self.bundle.root/"schema.json"))
        self.command(["terraform","plan","-input=false","-out=plan.tfplan","-var-file=inputs.tfvars.json"],directory,"plan-"+target,120)
        shown = self.command(["terraform","show","-json","plan.tfplan"],directory,"show-"+target,30)
        validate_index_plan(json.loads(shown.stdout),self.settings.search_host,target)
        binding = bind_plan(directory/"plan.tfplan",self.bundle,target,self.store.state(target),inputs,directory/".terraform.lock.hcl")
        envelope = self.proof | {"binding":asdict(binding)}
        plan_publish = self.publish/(target+"-plan")
        plan_publish.mkdir()
        for filename in ("plan.tfplan","inputs.tfvars.json",".terraform.lock.hcl"):
            data = (directory/filename).read_bytes()
            self.store.put_once("evidence",self.key(f"plans/{target}/{filename}"),data)
            (plan_publish/filename).write_bytes(data)
        self.store.put_json(self.key(f"plans/{target}/binding.json"),envelope)
        self.store.put_once("evidence",self.key(f"plans/{target}/shown.json"),shown.stdout.encode())
        exclusive_json(plan_publish/"binding.json",envelope)
        with (plan_publish/"shown.json").open("xb") as out: out.write(shown.stdout.encode())
        exclusive_json(plan_publish/"summary.json",{"target":target,"creates":1,"updates":0,"deletes":0,
                                                    "plan_sha256":binding.plan_sha256,"release_digest":self.bundle.digest})
        suite=ET.Element("testsuite",name="Exact saved plan",tests="1",failures="0",errors="0",skipped="0")
        ET.SubElement(suite,"testcase",classname="search.plan",name="create-only-bound-plan")
        with (plan_publish/"contract.xml").open("xb") as out: out.write(ET.tostring(suite,encoding="utf-8"))

    def apply_and_load(self,target: str):
        validated=self.target(target)
        envelope = self.store.json("evidence",self.key(f"plans/{target}/binding.json"))
        if any(envelope.get(k) != self.proof[k] for k in RELEASE_KEYS): raise DeliveryRefused("plan")
        directory = self.prepare_workspace(target)
        for name in ("plan.tfplan","inputs.tfvars.json"):
            (directory/name).write_bytes(self.store.get("evidence",self.key(f"plans/{target}/{name}"),maximum=16*1024*1024))
        expected_lock = self.store.get("evidence",self.key(f"plans/{target}/.terraform.lock.hcl"))
        if expected_lock != (directory/".terraform.lock.hcl").read_bytes(): raise DeliveryRefused("plan")
        binding=PlanBinding(**envelope["binding"])
        before=self.store.state(target)
        verify_plan(binding,directory/"plan.tfplan",self.bundle,target,before,directory/"inputs.tfvars.json",directory/".terraform.lock.hcl")
        shown=self.store.json("evidence",self.key(f"plans/{target}/shown.json"))
        validate_index_plan(shown,validated.allowed_host,target)
        # The only authorized schema operation is creating an absent new index.
        response = self.client.get(f"{validated.endpoint}/indexes/{validated.index}",
            params={"api-version":"2024-07-01"},headers={"Authorization":"Bearer "+self.search_tokens.token()},
            follow_redirects=False,timeout=20)
        if response.status_code != 404: raise DeliveryRefused("plan")
        published=self.publish/(target+"-apply"); published.mkdir()
        exclusive_json(published/"state-before.json",before)
        self.store.put_once("evidence",self.key(f"applies/{target}/state-before.json"),(published/"state-before.json").read_bytes())
        intent=self.proof | {"component":"index-"+target,"binding":asdict(binding)}
        exclusive_json(published/"intent.json",intent)
        self.store.put_once("evidence",self.key(f"applies/{target}/intent.json"),(published/"intent.json").read_bytes())
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
                self.store.put_once("evidence",self.key(f"applies/{target}/result.json"),data)
        upload_reservation=self.work/("upload-"+target+".json")
        # Verify count access on the new, empty index before bulk work.
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

    def prod_fingerprint(self) -> dict:
        """Read-only: the demo-prod document count and the digest of its index definition."""
        prod = self.target("demo-prod")
        try:
            response = self.client.get(f"{prod.endpoint}/indexes/{prod.index}",params={"api-version":"2024-07-01"},
                headers={"Authorization":"Bearer "+self.search_tokens.token()},follow_redirects=False,timeout=20)
        except httpx.HTTPError:
            raise RequestRefused("search-index-transport") from None
        if response.status_code != 200: raise RequestRefused("search-index-http",response.status_code)
        try:
            schema = hashlib.sha256(canonical(response.json())).hexdigest()
        except ValueError:
            raise DeliveryRefused("gate") from None
        return {"document_count":document_count(prod,self.search_tokens,self.client),"schema_sha256":schema}

    def measure(self,target: str,*,negative=False,prod_before=None) -> bool:
        result,rows = run_gate(self.bundle,self.target(target),read_json(self.bundle.root/(
            "negative.json" if negative else "s2.json")),self.search_tokens,self.client)
        output = self.publish/"gate"
        write_gate(result,rows,output)
        require_result_files(output)
        receipt = self.proof | self.bundle_record | {"stage":self.stage,"gate":asdict(result),"reason":os.environ["BUILD_REASON"],
            "expected_negative":negative,"acceptance_pass":negative_passes(result,rows) if negative else result.passed}
        if prod_before is not None:
            # The negative run proves the prod index is the one it found, before its receipt exists.
            prod_after = self.prod_fingerprint()
            receipt |= {"prod_before":prod_before,"prod_after":prod_after,"prod_unchanged":prod_after == prod_before}
            receipt["acceptance_pass"] = receipt["acceptance_pass"] and receipt["prod_unchanged"]
        for name in ("results.json","results.xml"):
            data = (output/name).read_bytes()
            self.store.put_once("evidence",self.key(f"gates/{self.stage}/{name}"),data)
            receipt[name+"_sha256"] = hashlib.sha256(data).hexdigest()
        self.store.put_json(self.key("receipts/"+self.stage+".json"),receipt)
        exclusive_json(output/"receipt.json",receipt)
        if not receipt["acceptance_pass"]: raise DeliveryRefused("gate")
        return not negative

    def prior_gate(self,stage: str):
        receipt = self.store.json("evidence",self.key("receipts/"+stage+".json"))
        require_gate(receipt,self.proof)
        data = self.store.get("evidence",self.key(f"gates/{stage}/results.json"))
        xml = self.store.get("evidence",self.key(f"gates/{stage}/results.xml"))
        if hashlib.sha256(data).hexdigest()!=receipt["results.json_sha256"] or hashlib.sha256(xml).hexdigest()!=receipt["results.xml_sha256"]:
            raise DeliveryRefused("gate")
        raw = json.loads(data)
        rows = [ArmResult(r["qid"],r["arm"],[Hit(**h) for h in r["hits"]],r["ms"],r["error"],r.get("query_tokens",0)) for r in raw["rows"]]
        if canonical(asdict(grade_rows(self.bundle,rows))) != canonical(raw["gate"]): raise DeliveryRefused("gate")
        require_gate(receipt | {"gate":raw["gate"]},self.proof)
        suite = ET.fromstring(xml)
        if len(suite.findall("testcase")) != 230 or suite.get("failures") != "0" or suite.get("skipped") != "0":
            raise DeliveryRefused("gate")

    def execute(self) -> bool:
        if self.stage == "build":
            result = self.process([sys.executable,"-m","pytest","--strict-config","tests/delivery_search","tests/retrieval","-q",
                "--junitxml="+str(self.publish/"offline.xml")],cwd=ROOT/"eval",capture_output=True,text=True,timeout=240)
            exclusive_json(self.work/"offline-tests.private.json",{"returncode":result.returncode,"stdout":result.stdout,"stderr":result.stderr})
            if result.returncode: raise DeliveryRefused("gate")
            # The search service does not exist yet; the release is named once, for the stages and runs after it.
            self.store.write_pointer(dict(self.proof))
        elif self.stage == "plan-test":
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
        elif self.stage == "negative":
            self.prior_gate("prod")
            return self.measure("test",negative=True,prod_before=self.prod_fingerprint())
        elif self.stage == "verify":
            self.prior_gate("prod")
            negative = self.store.json("evidence",self.key("receipts/negative.json"))
            if (any(negative.get(k) != self.proof[k] for k in RELEASE_KEYS) or negative.get("expected_negative") is not True
                or negative.get("acceptance_pass") is not True or negative.get("prod_unchanged") is not True):
                raise DeliveryRefused("gate")
            return self.measure("demo-prod")
        else:
            raise DeliveryRefused("authority")
        return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage",choices=STAGES)
    parser.add_argument("--receipt-dir",type=Path,required=True)
    parser.add_argument("--guard-only",action="store_true")
    args = parser.parse_args(argv)
    try:
        settings,_ = context(args.stage)
        if args.guard_only:
            print("status=pass code=source-guard")
            return 0
        with http_client() as client:
            runtime = Runtime(settings,args.stage,args.receipt_dir,client)
            passed = runtime.execute()
        print("status=pass code=stage" if passed else "status=expected-failure code=quality")
        return 0 if passed else 17
    except Exception as exc:
        return refusal(args.stage,args.receipt_dir,exc)


def refusal(stage: str,receipt_dir: Path,exc: Exception) -> int:
    """The one public failure shape for every entrypoint: fixed codes, never exception text."""
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
        receipt_dir.mkdir(parents=True,exist_ok=True)
        exclusive_json(receipt_dir/"failure.json",{
            "kind":"stage-failure","stage":stage,"code":code,"check":check,**details})
    with suppress(OSError):
        receipt_dir.parent.mkdir(parents=True,exist_ok=True)
        with (receipt_dir.parent/"failure-private.txt").open("x",encoding="utf-8") as out:
            out.write("".join(traceback.format_exception(exc)))
    print("status=refused code="+code)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
