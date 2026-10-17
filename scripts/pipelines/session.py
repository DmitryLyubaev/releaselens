"""Fresh session authority and crash-safe local reservations. Never loads credentials."""
from contextlib import contextmanager
from dataclasses import dataclass,fields
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from app.delivery_search.contracts import DeliveryRefused,canonical,exclusive_json,sha256_file
from .readiness import TIMING_PROFILES,require_owner_id


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None: raise DeliveryRefused("authority")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class SessionAuthority:
    session: str
    source_sha: str
    tree_sha: str
    release_digest: str
    apply_started_at: str | None
    queue_cutoff_at: str
    cleanup_due_at: str
    cleanup_start_by: str
    closeout_by: str
    manifest_sha256: str
    timing_profile: str = "standard"

    def validate(self):
        try:
            if self.timing_profile not in TIMING_PROFILES: raise ValueError()
            if not re.fullmatch(r"[a-z][a-z0-9-]{3,11}",self.session): raise ValueError()
            for value,n in ((self.source_sha,40),(self.tree_sha,40),(self.release_digest,64),(self.manifest_sha256,64)):
                if not re.fullmatch("[0-9a-f]{"+str(n)+"}",value): raise ValueError()
            times = [instant(getattr(self,key)) for key in ("queue_cutoff_at","cleanup_due_at","cleanup_start_by","closeout_by")]
            if self.apply_started_at is not None:
                start = instant(self.apply_started_at)
                if times != [start+timedelta(minutes=n) for n in TIMING_PROFILES[self.timing_profile]]: raise ValueError()
            elif times != sorted(times): raise ValueError()
        except (ValueError,TypeError,OverflowError): raise DeliveryRefused("authority") from None

    def cleanup_due(self,now: datetime) -> bool:
        self.validate()
        return self.apply_started_at is not None and now >= instant(self.cleanup_due_at)


def read(path: Path) -> dict:
    try:
        value = json.loads(path.read_bytes())
        if not isinstance(value,dict): raise ValueError()
        return value
    except (OSError,ValueError,TypeError): raise DeliveryRefused("authority") from None


def bound_file(directory: Path,relative: str) -> Path:
    root = directory.resolve()
    path = (root/relative).resolve()
    if not path.is_relative_to(root) or path == root: raise DeliveryRefused("authority")
    return path


def scope_digest(raw: dict) -> str:
    owner=require_owner_id(raw)
    profile=raw.get("timing_profile","standard")
    if profile not in TIMING_PROFILES: raise DeliveryRefused("authority")
    value={key:raw[key] for key in ("owner","owned_group_ids","principals","scopes",
        "pipeline_id","storage_host","search_host","manifest_sha256","baseline_sha256")}
    return hashlib.sha256(canonical(value|{"owner_ado_id":owner,"timing_profile":profile})).hexdigest()


def load(directory: Path) -> tuple[SessionAuthority,dict,dict]:
    raw = read(directory/"authority.json")
    try:
        a = SessionAuthority(**{f.name:raw[f.name] for f in fields(SessionAuthority) if f.name!="timing_profile"},
            timing_profile=raw.get("timing_profile","standard"))
        a.validate()
        receipt = read(directory/"owner-approval-receipt.json")
        if (not receipt.get("message_ref") or receipt.get("kind") != "human-live-approval"
            or receipt["scope_sha256"]!=scope_digest(raw)
            or receipt["session"] != a.session or receipt["source_sha"] != a.source_sha
            or receipt["tree_sha"] != a.tree_sha or receipt["release_digest"] != a.release_digest
            or sha256_file(directory/"owner-approval-receipt.json") != raw["approval_receipt_sha256"]
            or sha256_file(directory/"cleanup-manifest.json") != a.manifest_sha256
            or sha256_file(directory/"baseline.json") != raw["baseline_sha256"]):
            raise DeliveryRefused("authority")
        manifest = read(directory/"cleanup-manifest.json")
        baseline = read(directory/"baseline.json")
        for value in (manifest,baseline):
            if any(value[k] != getattr(a,k) for k in ("session","source_sha","tree_sha")):
                raise DeliveryRefused("authority")
        if baseline["canonical_absent"] is not True or not baseline.get("observed_at"):
            raise DeliveryRefused("authority")
        absence=baseline["absence"]
        if (len(absence)!=len(raw["owned_group_ids"]) or {r["id"] for r in absence}!=set(raw["owned_group_ids"])
            or any(r["status"]!=404 for r in absence)): raise DeliveryRefused("authority")
        if a.apply_started_at is not None and instant(baseline["observed_at"]) >= instant(a.apply_started_at):
            raise DeliveryRefused("authority")
        if not all(key in baseline["retained"] for key in ("resources","roleAssignments","deletedVaults")):
            raise DeliveryRefused("authority")
        validate_manifest(manifest,raw,directory)
        from .ownership import resolve_existing
        manifest=resolve_existing(directory,raw,manifest)
        return a,raw,manifest
    except (KeyError,TypeError,ValueError): raise DeliveryRefused("authority") from None


def validate_manifest(manifest: dict,authority: dict,directory: Path):
    limits = {"index-test":1,"index-demo-prod":1,"foundation":23,"identity":7,"agents":9}
    try:
        components = manifest["components"]
        if {v["name"] for v in components} != set(limits) or len(components) != 5:
            raise DeliveryRefused("authority")
        groups = authority["owned_group_ids"]
        prefix = "/subscriptions/"+authority["owner"]["subscription_id"]+"/resourceGroups/"
        if len(groups) != 3 or len(set(groups)) != 3 or any(
            not group.startswith(prefix) or "/" in group[len(prefix):] or authority["session"] not in group[len(prefix):]
            for group in groups): raise DeliveryRefused("authority")
        seen = set()
        for component in components:
            bound_file(directory,component["directory"])
            if not re.fullmatch(r"[0-9a-f]{64}",component["config_sha256"]): raise DeliveryRefused("authority")
            rows = component["resources"]
            if len([r for r in rows if r.get("kind")!="local"]) > limits[component["name"]]: raise DeliveryRefused("authority")
            if sum(r.get("kind")=="local" for r in rows)>1: raise DeliveryRefused("authority")
            for row in rows:
                key = (component["name"],row["address"])
                if key in seen: raise DeliveryRefused("authority")
                seen.add(key)
                if row.get("id") is None:
                    from .ownership import validate_policy
                    validate_policy(component,row,authority,directory)
                elif not isinstance(row["id"],str) or not row["id"]: raise DeliveryRefused("authority")
                if component["name"].startswith("index-"):
                    if (row["address"] != "azapi_data_plane_resource.index" or row["kind"] != "index"
                        or row["name"] != "p8-"+component["name"][6:] or row["host"] != authority["search_host"]):
                        raise DeliveryRefused("authority")
                elif row.get("kind") == "local":
                    if component["name"] != "agents" or not row["address"].startswith("terraform_data."):
                        raise DeliveryRefused("authority")
                elif (row["kind"] != "arm" or row.get("id") is not None and not any(row["id"].lower() == g.lower() or row["id"].lower().startswith(g.lower()+"/") for g in groups)
                    or not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}(-preview)?",row["api_version"])):
                    raise DeliveryRefused("authority")
    except (KeyError,TypeError,ValueError): raise DeliveryRefused("authority") from None


@contextmanager
def operation_lock(directory: Path):
    directory.mkdir(parents=True,exist_ok=True)
    with (directory/"operation.lock").open("a+b") as handle:
        if handle.seek(0,2) == 0: handle.write(b"0"); handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except (OSError,BlockingIOError): raise DeliveryRefused("reserved") from None
        try: yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def _reserve(directory: Path,operation: str,identity: str) -> Path:
    if not re.fullmatch(r"[a-z0-9-]{1,80}",operation) or not re.fullmatch(r"[0-9a-f]{64}",identity):
        raise DeliveryRefused("authority")
    folder = directory/"intents"
    folder.mkdir(exist_ok=True)
    path = folder/(operation+".json")
    exclusive_json(path,{"operation":operation,"identity_sha256":identity,"at":datetime.now(timezone.utc).isoformat()})
    return path


def reserve_operation(directory: Path,operation: str,identity: str) -> Path:
    with operation_lock(directory): return _reserve(directory,operation,identity)


def reserve_budget(directory: Path,name: str,maximum: int) -> Path:
    if not re.fullmatch(r"[a-z-]{1,40}",name) or not 1 <= maximum <= 200: raise DeliveryRefused("authority")
    with operation_lock(directory):
        for i in range(maximum):
            operation = f"budget-{name}-{i:03}"
            if not (directory/"intents"/(operation+".json")).exists():
                return _reserve(directory,operation,hashlib.sha256(name.encode()).hexdigest())
        raise DeliveryRefused("reserved")


def verify_source(authority):
    root=Path(__file__).resolve().parents[2]
    git=shutil.which("git") or ("C:/Program Files/Git/cmd/git.exe" if os.name=="nt" else "git")
    values=authority if isinstance(authority,dict) else vars(authority)
    for args,value in ((["rev-parse","HEAD"],values["source_sha"]),(["rev-parse","HEAD^{tree}"],values["tree_sha"])):
        result=subprocess.run([git,*args],cwd=root,capture_output=True,text=True,timeout=10)
        if result.returncode or result.stdout.strip()!=value: raise DeliveryRefused("authority")
    if subprocess.run([git,"diff","--quiet","HEAD","--"],cwd=root,capture_output=True,timeout=10).returncode:
        raise DeliveryRefused("authority")


def reserve_dispatch(directory: Path,mode: str,now: datetime) -> Path:
    a,raw,manifest=load(directory)
    verify_source(a)
    if (a.apply_started_at is None or now<instant(a.apply_started_at) or now>=instant(a.queue_cutoff_at)
        or (directory/"stopped-gate.json").exists() or mode not in ("release","negative","scheduled")):
        raise DeliveryRefused("authority")
    if mode!="release":
        from .evidence import grade
        grades=grade(directory)
        if any(grades[k]["status"]!="PASS" for k in ("P8-4","P8-5")):
            raise DeliveryRefused("gate")
    identity=hashlib.sha256((a.source_sha+a.release_digest).encode()).hexdigest()
    with operation_lock(directory):
        dispatch=_reserve(directory,"dispatch-"+mode,identity)
        for name in ({"release":("test","prod"),"negative":("negative",),"scheduled":("verify",)}[mode]):
            _reserve(directory,"queries-"+name,identity)
        return dispatch


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser(); parser.add_argument("--session",type=Path,required=True)
    parser.add_argument("--reserve-dispatch",choices=("release","negative","scheduled"),required=True)
    args=parser.parse_args()
    try:
        reserve_dispatch(args.session,args.reserve_dispatch,datetime.now(timezone.utc))
        print("status=pass code=reserved")
    except Exception:
        print("status=refused code=authority"); raise SystemExit(2)
