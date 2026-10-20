"""The per-run search service: create it, report whether its release is complete, and tear down idempotently."""
import argparse
from contextlib import suppress
import json
import os
from pathlib import Path
import re
import subprocess

from app.delivery_search.bundle import fetch_bundle
from app.delivery_search.contracts import DeliveryRefused,canonical,exclusive_json
from app.retrieval.azure_auth import TokenSource
from .run import (RELEASE_KEYS,authenticate,check_bundle,copy_stack,holds,http_client,identity_receipt,index_inputs,
    init_args,refusal,require_gate,service_present,source_context)
from .settings import Settings,role_for
from .storage import BlobStore,run_key

COMMANDS = ("create-service","status","teardown-prod","teardown-test")
# The schedule never creates and alone reads the status; a teardown is scheduled or the owner's manual fallback.
REASONS = {"create-service":("Manual",),"status":("Schedule",),"teardown-prod":("Manual","Schedule"),
    "teardown-test":("Manual","Schedule")}
LOCATION = "australiaeast"
INDEX_ADDRESS = "azapi_data_plane_resource.index"
SERVICE_ADDRESS = "azurerm_search_service.search"
DESTROY = ["terraform","destroy","-auto-approve","-input=false","-var-file=inputs.tfvars.json"]


def release_status(settings: Settings,store,present: bool) -> str:
    """Read-only. ready only when the release the pointer names passed prod and its negative run."""
    if not present: return "absent"
    try:
        pointer = store.read_pointer()
        release_run_id = pointer.get("release_run_id")
        if type(release_run_id) is not int or release_run_id < 1 or pointer.get("release_digest") != settings.release_digest:
            return "incomplete"
        expected = {"release_digest":settings.release_digest,"release_run_id":release_run_id}
        require_gate(store.json("evidence",run_key(release_run_id,"receipts/prod.json")),expected)
        negative = store.json("evidence",run_key(release_run_id,"receipts/negative.json"))
    except DeliveryRefused:
        # Missing or unreadable evidence: the release may still be running, so never verify or tear down under it.
        return "incomplete"
    if (any(negative.get(k) != expected[k] for k in RELEASE_KEYS) or negative.get("expected_negative") is not True
        or negative.get("acceptance_pass") is not True):
        return "incomplete"
    return "ready"


def validate_service_plan(plan: dict,settings: Settings) -> None:
    """The one change a create may make: one new Basic search service, 1 replica and 1 partition,
    with the configured name, in the configured group."""
    try:
        rows = [row for row in plan["resource_changes"] if row["mode"] == "managed"]
        if len(rows) != 1: raise DeliveryRefused("plan")
        row = rows[0]
        after = row["change"]["after"]
        if (row["address"] != SERVICE_ADDRESS or row["type"] != "azurerm_search_service" or row["change"]["actions"] != ["create"]
            or after["name"] != settings.search_service_name or after["resource_group_name"] != settings.search_group
            or after["sku"] != "basic" or after["replica_count"] != 1 or after["partition_count"] != 1):
            raise DeliveryRefused("plan")
    except (KeyError,TypeError):
        raise DeliveryRefused("plan") from None


class Lifecycle:
    def __init__(self,settings: Settings,stage: str,receipt_dir: Path,client):
        self.settings,self.stage,self.client = settings,stage,client
        role = role_for(stage,os.environ["BUILD_REASON"])
        self.principal = settings.principals[role]
        self.build_id = os.environ.get("BUILD_BUILDID","")
        if not re.fullmatch(r"[1-9][0-9]{0,15}",self.build_id): raise DeliveryRefused("authority")
        self.publish = receipt_dir
        self.publish.mkdir(parents=True,exist_ok=True)
        account,claims,self.tf_env = authenticate(self.principal)
        exclusive_json(self.publish/"identity.json",{"source_sha":os.environ["BUILD_SOURCEVERSION"],
            "release_digest":settings.release_digest,"build_id":int(self.build_id),"stage":stage}
            | identity_receipt(account,claims) | {"role":role,"verified":True,"scheme":"WorkloadIdentityFederation"})
        self.storage_tokens = TokenSource("https://storage.azure.com/.default",settings.tenant_id)
        self.store = BlobStore(settings.storage_host,self.storage_tokens,client)
        self.work = Path.home()/"p8"/stage
        self.work.mkdir(parents=True,exist_ok=True)
        self.failure = "plan" if stage == "create-service" else "cleanup"
        self.workspaces = {}

    def process(self,args,**kwargs):
        return subprocess.run(args,**kwargs)

    def present(self) -> bool:
        return service_present(self.settings,self.process)

    def command(self,args: list[str],cwd: Path,name: str,seconds: int):
        result = self.process(args,cwd=cwd,env=self.tf_env,capture_output=True,text=True,timeout=seconds)
        exclusive_json(self.work/(name+".private.json"),{"returncode":result.returncode,"stdout":result.stdout,"stderr":result.stderr})
        if result.returncode != 0: raise DeliveryRefused(self.failure)
        return result

    def workspace(self,target: str) -> Path:
        """A clean copy of the reviewed stack under this stage's own work directory, initialized once
        against the target's own state container."""
        if target not in self.workspaces:
            if target == "service": directory = copy_stack("service",self.work/"service")
            else: directory = copy_stack("index",self.work/("index-"+target))
            self.command(init_args(self.settings.storage_account_name,target),directory,"init-"+target,120)
            self.workspaces[target] = directory
        return self.workspaces[target]

    def service_inputs(self,directory: Path) -> None:
        (directory/"inputs.tfvars.json").write_bytes(canonical({"search_service_name":self.settings.search_service_name,
            "resource_group_name":self.settings.search_group,"location":LOCATION,
            "subscription_id":self.settings.subscription_id,"tenant_id":self.settings.tenant_id}))

    def create(self) -> None:
        # A retry always begins from a clean teardown: never adopt or replace a service, or a state that holds one.
        if self.present() or holds(self.store.state("service")): raise DeliveryRefused("reserved")
        directory = self.workspace("service")
        self.service_inputs(directory)
        self.command(["terraform","plan","-input=false","-out=service.tfplan","-var-file=inputs.tfvars.json"],directory,"plan-service",120)
        shown = self.command(["terraform","show","-json","service.tfplan"],directory,"show-service",30)
        try:
            plan = json.loads(shown.stdout)
        except ValueError:
            raise DeliveryRefused("plan") from None
        validate_service_plan(plan,self.settings)
        self.command(["terraform","apply","-input=false","service.tfplan"],directory,"apply-service",1800)
        output = self.command(["terraform","output","-json","search_host"],directory,"output-service",30)
        try:
            host = json.loads(output.stdout)
        except ValueError:
            raise DeliveryRefused("target") from None
        # Every later stage addresses the host the settings name.
        if host != self.settings.search_host: raise DeliveryRefused("target")

    def teardown_index(self,target: str,present: bool) -> str:
        if not holds(self.store.state(target)): return "empty"
        directory = self.workspace(target)
        if not present:
            self.forget_index(target)
            return "state-removed"
        host,digest = self.settings.storage_host,self.settings.release_digest
        bundle = fetch_bundle(f"https://{host}/inputs/releases/{digest}/release.tar.gz",digest,self.work/"verified",
            self.storage_tokens,self.client,allowed_host=host)
        check_bundle(bundle)
        # The same inputs the index plan used; the schema file must exist for Terraform to read the configuration.
        (directory/"inputs.tfvars.json").write_bytes(index_inputs(self.settings,target,bundle.root/"schema.json"))
        self.command(DESTROY,directory,"destroy-"+target,480)
        return "destroyed"

    def forget_index(self,target: str) -> None:
        # The service took the index with it; only the state still lists it. Terraform alone changes state.
        self.command(["terraform","state","rm",INDEX_ADDRESS],self.workspace(target),"state-rm-"+target,120)

    def teardown_service(self) -> str:
        if not holds(self.store.state("service")): return "empty"
        directory = self.workspace("service")
        self.service_inputs(directory)
        self.command(DESTROY,directory,"destroy-service",1800)
        return "destroyed"

    def teardown_test(self,present: bool,actions: dict) -> None:
        failed = None
        try:
            actions["test"] = self.teardown_index("test",present)
        except Exception as exc:
            actions["test"],failed = "failed",exc
        # The service goes even when its index did not: it must not stay billing.
        try:
            actions["service"] = self.teardown_service()
        except Exception as exc:
            if failed is not None: raise exc from failed
            raise
        if failed is not None:
            # Once the service is confirmed gone, nothing later would clear the index state; clear it now,
            # and still end red for the failed destroy.
            try:
                if not self.present() and holds(self.store.state("test")):
                    self.forget_index("test")
                    actions["test-state"] = "state-removed"
            except Exception as exc:
                raise exc from failed
            raise failed
        # A service no state lists is still billing; the run must not end green.
        if self.present(): raise DeliveryRefused("cleanup")

    def teardown(self) -> None:
        present = self.present()
        actions,outcome = {},"failed"
        try:
            if self.stage == "teardown-prod": actions["demo-prod"] = self.teardown_index("demo-prod",present)
            else: self.teardown_test(present,actions)
            outcome = "pass"
        finally:
            self.record(present,actions,outcome)

    def record(self,present: bool,actions: dict,outcome: str) -> None:
        """Teardown evidence, create-only per build; it never blocks or reddens a teardown."""
        receipt = {}
        with suppress(OSError,ValueError):
            receipt = json.loads((self.publish/"identity.json").read_bytes())
        receipt |= {"service_present":present,"actions":actions,"outcome":outcome}
        with suppress(OSError,DeliveryRefused):
            exclusive_json(self.publish/"teardown.json",receipt)
        try:
            self.store.put_json(f"teardowns/{self.build_id}/{self.stage}.json",receipt)
        except Exception as exc:
            code = DeliveryRefused(exc.code).code if isinstance(exc,DeliveryRefused) else "internal"
            with suppress(OSError,DeliveryRefused):
                exclusive_json(self.publish/"evidence-failure.json",{"kind":"evidence-failure","stage":self.stage,"code":code})

    def execute(self) -> str | None:
        if self.stage == "create-service": self.create()
        elif self.stage == "status": return release_status(self.settings,self.store,self.present())
        elif self.stage in ("teardown-prod","teardown-test"): self.teardown()
        else: raise DeliveryRefused("authority")
        return None


def context(stage: str) -> tuple[Settings,dict]:
    reason = os.environ.get("BUILD_REASON","")
    if reason not in REASONS.get(stage,()): raise DeliveryRefused("authority")
    return source_context(stage,reason)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage",choices=COMMANDS)
    parser.add_argument("--receipt-dir",type=Path,required=True)
    args = parser.parse_args(argv)
    try:
        settings,_ = context(args.stage)
        with http_client() as client:
            status = Lifecycle(settings,args.stage,args.receipt_dir,client).execute()
    except Exception as exc:
        return refusal(args.stage,args.receipt_dir,exc)
    if status is None:
        print("status=pass code=stage")
    else:
        print("##vso[task.setvariable variable=P8Status;isOutput=true]"+status)
        # An incomplete release may still be billing: the scheduled run shows orange, not green. Still exit 0.
        if status == "incomplete": print("##vso[task.complete result=SucceededWithIssues;]")
        print("status=pass code="+status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
