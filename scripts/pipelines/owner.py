"""Owner-side WSL adapter. No account mutation, refresh, retry, purge or agent self-destroy."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

import httpx

from app.delivery_search.contracts import DeliveryRefused,canonical,exclusive_json,sha256_file
from app.delivery_search.contracts import SearchTarget
from app.retrieval.azure_auth import TokenSource
from .run import jwt_claims
from .session import bound_file


def config_digest(directory: Path) -> str:
    files=sorted(p for p in directory.iterdir() if p.is_file() and (
        p.name.endswith((".tf",".tfvars.json")) or p.name in (".terraform.lock.hcl","backend.hcl")))
    return hashlib.sha256(canonical({p.name:sha256_file(p) for p in files})).hexdigest()


def state_rows(state: dict) -> list[dict]:
    def walk(module):
        result=[{"address":r["address"],"id":r["values"]["id"]} for r in module.get("resources",[]) if r["mode"]=="managed"]
        for child in module.get("child_modules",[]): result.extend(walk(child))
        return result
    return walk(state.get("values",{}).get("root_module",{}))


def verify_backend(directory: Path,expected: dict):
    from .session import read
    backend=read(directory/".terraform/terraform.tfstate")["backend"]
    workspace=directory/".terraform/environment"
    if workspace.exists() and workspace.read_text().strip()!="default": raise DeliveryRefused("plan")
    if backend["type"]!=expected["type"]: raise DeliveryRefused("plan")
    if backend["type"]=="azurerm":
        config=backend["config"]
        if (config.get("use_oidc") is not False or config.get("use_azuread_auth") is not True
            or any(config.get(k)!=expected[k] for k in ("storage_account_name","container_name","key"))
            or config.get("access_key") or config.get("sas_token") or config.get("client_secret")):
            raise DeliveryRefused("plan")
    elif backend["type"]=="local":
        path=Path(backend["config"].get("path") or "terraform.tfstate")
        if not path.is_absolute(): path=directory/path
        if path.resolve()!=bound_file(directory,expected["path"]): raise DeliveryRefused("plan")
    else: raise DeliveryRefused("plan")


class OwnerDriver:
    def __init__(self,directory: Path,authority: dict,manifest: dict):
        from .metadata import require_policy
        require_policy(authority)
        if os.name=="nt" or not Path("/proc").exists(): raise DeliveryRefused("authority")
        self.directory,self.a,self.manifest=directory.resolve(),authority,manifest
        self.logs=self.directory/"owner-originals"; self.logs.mkdir(exist_ok=True)
        self.counter=len(list(self.logs.glob("*.json")))
        self.env={k:v for k,v in os.environ.items() if not k.startswith(("ARM_","AZURE_","TF_"))}
        self.env.update({"ARM_USE_OIDC":"false","ARM_USE_CLI":"true","ARM_USE_AZUREAD":"true",
            "ARM_TENANT_ID":authority["owner"]["tenant_id"],"ARM_SUBSCRIPTION_ID":authority["owner"]["subscription_id"],
            "TF_IN_AUTOMATION":"true","TF_INPUT":"false"})
        self.tokens=None

    def record(self,name,value):
        self.counter+=1
        exclusive_json(self.logs/f"{self.counter:04}-{name}.json",value)

    def process(self,args,cwd=None,seconds=120):
        # A separate process group makes timeout ownership inspectable. Do not auto-kill
        # a provider and immediately start another process against its locked state.
        child=subprocess.Popen(args,cwd=cwd,env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            text=True,start_new_session=True)
        self.record("process-start",{"pid":child.pid,"pgid":child.pid,"cwd":str(cwd),"args":args,
            "started_at":datetime.now(timezone.utc).isoformat()})
        try:
            stdout,stderr=child.communicate(timeout=seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid,signal.SIGTERM)
            except ProcessLookupError: pass
            try: stdout,stderr=child.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                self.record("process-timeout",{"pid":child.pid,"active":True})
                raise
            self.record("process-timeout",{"pid":child.pid,"stdout":stdout,"stderr":stderr,"returncode":child.returncode})
            raise TimeoutError("owned operation timed out") from None
        self.record("process-result",{"pid":child.pid,"stdout":stdout,"stderr":stderr,"returncode":child.returncode})
        if child.returncode: raise DeliveryRefused("plan")
        return stdout

    def az(self,args):
        return json.loads(self.process(["az",*args,"--subscription",self.a["owner"]["subscription_id"],"--output","json"],seconds=120))

    def verify_owner(self):
        from .session import verify_source
        verify_source(self.a)
        # Credentials are never read from an old token file or a deployment identity.
        account=self.az(["account","show"])
        owner=self.a["owner"]
        if account["id"]!=owner["subscription_id"] or account["tenantId"]!=owner["tenant_id"] or account["user"]["type"].lower()!="user":
            raise DeliveryRefused("identity")
        # Capture token output only in RAM, never in process-result originals.
        result=subprocess.run(["az","account","get-access-token","--resource","https://management.azure.com/",
            "--subscription",owner["subscription_id"],"--output","json"],env=self.env,capture_output=True,text=True,timeout=30)
        if result.returncode: raise DeliveryRefused("identity")
        claims=jwt_claims(json.loads(result.stdout)["accessToken"])
        if claims.get("oid")!=owner["object_id"] or claims.get("tid")!=owner["tenant_id"] or claims.get("exp",0)<=time.time()+30:
            raise DeliveryRefused("identity")
        self.tokens=TokenSource("https://management.azure.com/.default",owner["tenant_id"])

    def active_owned(self):
        active=[]
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit() or int(proc.name)==os.getpid(): continue
            try:
                cwd=(proc/"cwd").resolve(strict=True)
                argv=(proc/"cmdline").read_bytes().replace(b"\x00",b" ").decode(errors="replace")
                if cwd.is_relative_to(self.directory) and any(word in argv for word in ("terraform","terraform-provider","scripts.pipelines.cleanup")):
                    active.append({"pid":int(proc.name),"cwd":str(cwd),"command_line":argv})
            except (OSError,ValueError): continue
        return active

    def component_dir(self,component):
        cwd=bound_file(self.directory,component["directory"])
        if config_digest(cwd)!=component["config_sha256"]: raise DeliveryRefused("plan")
        verify_backend(cwd,component["backend"])
        return cwd

    def state(self,component):
        if component["name"].startswith("index-") and self.index_state_absence(component): return []
        if component["name"] in ("foundation","agents") and self.local_state_absence(component): return []
        cwd=self.component_dir(component)
        shown=json.loads(self.process(["terraform","show","-json"],cwd))
        if any(r.get("resolve") for r in component["resources"]):
            from .ownership import bind_state
            bind_state(self.directory,self.a,component,shown)
        rows=state_rows(shown)
        listed=self.process(["terraform","state","list"],cwd).splitlines()
        if set(listed)!={r["address"] for r in rows}: raise DeliveryRefused("plan")
        return rows

    def local_state_absence(self,component):
        """Never turn a failed state-list command into an empty Terraform state."""
        if component.get("backend",{}).get("type")!="local": return False
        cwd=self.component_dir(component)
        state=bound_file(cwd,component["backend"]["path"])
        paths=(state,Path(str(state)+".backup"),state.with_name("."+state.name+".lock.info"),
            self.directory/"apply-intents"/(component["name"]+".json"))
        if any(p.exists() for p in paths): return False
        from .ownership import local_component_group,require_local_state_absence,require_empty_show_json
        group=local_component_group(component,self.a)
        if component["name"] in getattr(self,"_absent_local_components",set()): return True
        if self.tokens is None: raise DeliveryRefused("identity")
        shown=json.loads(self.process(["terraform","show","-json"],cwd))
        require_empty_show_json(shown)
        resource=next(r for r in component["resources"] if r.get("id")==group)
        with httpx.Client(timeout=20,follow_redirects=False) as client:
            response=client.get("https://management.azure.com"+group,
                params={"api-version":resource["api_version"]},headers={"Authorization":"Bearer "+self.tokens.token()})
        observation={"id":group,"status":response.status_code,"body":response.text}
        self.record("local-state-group-observation",observation)
        if response.status_code!=404: raise DeliveryRefused("plan")
        proof={"kind":"never-applied-local-"+component["name"],"component":component["name"],
            **{k:self.a[k] for k in ("session","source_sha","manifest_sha256")},
            "at":datetime.now(timezone.utc).isoformat(),"directory":str(cwd),"state_path":str(state),
            "state_absent":True,"backup_absent":True,"lock_absent":True,"apply_intent_absent":True,
            "terraform_show":shown,"group":observation}
        require_local_state_absence(proof,component,self.a)
        self.record("local-state-absence",proof)
        if not hasattr(self,"_absent_local_components"): self._absent_local_components=set()
        self._absent_local_components.add(component["name"])
        return True

    def index_state_absence(self,component):
        """A nonexistent backend is empty only with an authenticated absent-index proof."""
        from .storage import BlobStore
        from .session import reserve_budget
        target=component["name"][6:]
        checked=SearchTarget("https://"+self.a["search_host"],"p8-"+target,self.a["search_host"])
        if component["name"] in getattr(self,"_absent_indices",{}): return True
        container="test-state" if target=="test" else "prod-state"
        storage_tokens=TokenSource("https://storage.azure.com/.default",self.a["owner"]["tenant_id"])
        with httpx.Client(timeout=20,follow_redirects=False) as client:
            store=BlobStore(self.a["storage_host"],storage_tokens,client,metadata_policy=self.a['metadata_policy'])
            parents={}
            for group in getattr(self,"manifest",{}).get("components",[]):
                if group["name"]!="foundation": continue
                for resource in group["resources"]:
                    if not resource.get("id"): continue
                    low=resource["id"].lower()
                    kind=next((kind for kind,marker in (("storage","/microsoft.storage/storageaccounts/"),
                        ("search","/microsoft.search/searchservices/")) if marker in low and "/" not in low.split(marker,1)[1]),None)
                    if kind and resource.get("kind")=="arm" and getattr(self,"tokens",None) is not None:
                        expected_host=low.rsplit("/",1)[1]+(".blob.core.windows.net" if kind=="storage" else ".search.windows.net")
                        if parents.get(kind) or self.a["storage_host" if kind=="storage" else "search_host"]!=expected_host:
                            raise DeliveryRefused("target")
                        response=client.get("https://management.azure.com"+resource["id"],params={"api-version":resource["api_version"]},
                            headers={"Authorization":"Bearer "+self.tokens.token()})
                        self.record("parent-absence",{"id":resource["id"],"status":response.status_code,"body":response.text})
                        if response.status_code not in (200,404): raise DeliveryRefused("plan")
                        parents[kind]={"kind":"arm-parent","id":resource["id"],"status":response.status_code}
            if parents.get("storage",{}).get("status")==404:
                backend=parents["storage"]
            else:
                response=client.get(store._url(container,"index.tfstate"),headers={
                    "Authorization":"Bearer "+storage_tokens.token(),"x-ms-version":"2023-11-03"})
                backend={"kind":"blob","host":self.a["storage_host"],"container":container,"key":"index.tfstate",
                    "status":response.status_code,"error_code":response.headers.get("x-ms-error-code"),"length":len(response.content)}
                self.record("backend-observation",backend | {"body":response.text})
                if response.status_code==200 and response.content: return False
                if not (response.status_code==200 and response.headers.get("content-length")=="0" or
                    response.status_code==404 and backend["error_code"] in ("BlobNotFound","ContainerNotFound")):
                    raise DeliveryRefused("plan")
            if parents.get("search",{}).get("status")==404:
                index=parents["search"]
            else:
                if backend.get("error_code")=="ContainerNotFound" or backend["kind"]=="arm-parent":
                    # Infrastructure failed before any dispatch, so no worker can have
                    # consumed metadata slots. Never use this fallback after dispatch.
                    if (self.directory/"intents/dispatch-release.json").exists(): raise DeliveryRefused("plan")
                    reserve_budget(self.directory,"metadata",244,metadata_policy=self.a['metadata_policy'])
                else:
                    store.reserve_cleanup_metadata(self.directory,self.a)
                response=client.get(f"{checked.endpoint}/indexes/{checked.index}",params={"api-version":"2024-07-01"},
                    headers={"Authorization":"Bearer "+TokenSource("https://search.azure.com/.default",self.a["owner"]["tenant_id"]).token()})
                index={"kind":"index","host":checked.allowed_host,"name":checked.index,"status":response.status_code}
                if response.status_code!=404: raise DeliveryRefused("plan")
            proof={"component":component["name"],"backend":backend,"index":index,"source_sha":self.a["source_sha"],
                "session":self.a["session"],"at":datetime.now(timezone.utc).isoformat()}
            self.record("index-state-absence",proof)
            if not hasattr(self,"_absent_indices"): self._absent_indices={}
            self._absent_indices[component["name"]]=proof
            return True

    def plan(self,component,attempt,remaining):
        cwd=self.component_dir(component)
        name=f"cleanup-{attempt}.tfplan"
        if (cwd/name).exists(): raise DeliveryRefused("reserved")
        # Backend is configured on the owner once before billing with use_oidc=false.
        # Never reinitialize or refresh state as an unreviewed cleanup side effect.
        self.process(["terraform","plan","-destroy","-input=false","-refresh=true","-out="+name,
            "-var-file=inputs.tfvars.json"],cwd,seconds=180)
        shown=json.loads(self.process(["terraform","show","-json",name],cwd))
        exclusive_json(cwd/(name+".binding.json"),{"sha256":sha256_file(cwd/name),"remaining":remaining,
            "config_sha256":component["config_sha256"]})
        return shown

    def apply(self,component,attempt):
        from .session import read
        cwd=self.component_dir(component); name=f"cleanup-{attempt}.tfplan"
        binding=read(cwd/(name+".binding.json"))
        if sha256_file(cwd/name)!=binding["sha256"] or self.state(component)!=binding["remaining"]:
            raise DeliveryRefused("plan")
        exclusive_json(cwd/(name+".apply-intent.json"),binding | {"at":datetime.now(timezone.utc).isoformat()})
        first=self.directory/"cleanup-first-delete.json"
        if not first.exists():
            exclusive_json(first,{"session":self.a["session"],"source_sha":self.a["source_sha"],
                "at":datetime.now(timezone.utc).isoformat(),"component":component["name"],"attempt":attempt})
        self.process(["terraform","apply","-input=false",name],cwd,seconds=2100 if component["name"]=="agents" else 600)

    def canonical_absence(self):
        if self.tokens is None: raise DeliveryRefused("identity")
        with httpx.Client(timeout=20,follow_redirects=False) as client:
            for group in self.a["owned_group_ids"]:
                response=client.get("https://management.azure.com"+group,params={"api-version":"2021-04-01"},
                    headers={"Authorization":"Bearer "+self.tokens.token()})
                self.record("group-absence",{"id":group,"status":response.status_code,"body":response.text})
                if response.status_code!=404: return False
        return True

    def retained(self):
        groups=[g.lower() for g in self.a["owned_group_ids"]]
        def retained_id(row):
            value=row["id"].lower()
            return not any(value==g or value.startswith(g+"/") for g in groups)
        resources=self.az(["resource","list"])
        roles=self.az(["role","assignment","list","--all","--include-inherited",
            "--fill-principal-name","false","--fill-role-definition-name","false"])
        deleted=self.az(["keyvault","list-deleted"])
        owned_vaults={r["id"].lower() for c in self.manifest["components"] for r in c["resources"]
            if r.get("id") and "/providers/microsoft.keyvault/vaults/" in r["id"].lower()}
        new_deleted=[r for r in deleted if r.get("properties",{}).get("vaultId","").lower() in owned_vaults]
        self.record("owned-soft-deleted-no-purge",new_deleted)
        return {"resources":[r for r in resources if retained_id(r)],
            "roleAssignments":[r for r in roles if retained_id(r)],"deletedVaults":[r for r in deleted if r not in new_deleted]}
