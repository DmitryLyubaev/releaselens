"""Bind saved Terraform plans to immutable release, target, inputs and remote state."""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Callable

from app.delivery_search.contracts import DeliveryRefused, VerifiedBundle, canonical, exclusive_json, sha256_file


@dataclass(frozen=True)
class PlanBinding:
    source_sha: str
    bundle_digest: str
    target: str
    plan_sha256: str
    state_lineage: str | None
    state_serial: int
    state_sha256: str
    locks_sha256: str
    inputs_sha256: str


def bind_plan(plan: Path, bundle: VerifiedBundle, target: str, state: dict | None,
              inputs: Path, locks: Path) -> PlanBinding:
    try:
        if target not in ("test","demo-prod") or not re.fullmatch(r"[0-9a-f]{40}",bundle.manifest.source_sha) or not re.fullmatch(r"[0-9a-f]{64}",bundle.digest):
            raise DeliveryRefused("plan")
        lineage, serial = None, 0
        if state is not None:
            lineage, serial = state["lineage"], state["serial"]
            if not re.fullmatch(r"[0-9a-fA-F-]{36}",lineage) or type(serial) is not int or serial < 0:
                raise DeliveryRefused("plan")
        return PlanBinding(bundle.manifest.source_sha,bundle.digest,target,sha256_file(plan),lineage,
            serial,hashlib.sha256(canonical(state)).hexdigest(),sha256_file(locks),sha256_file(inputs))
    except (KeyError,TypeError,ValueError,OSError):
        raise DeliveryRefused("plan") from None


def verify_plan(binding: PlanBinding, plan: Path, bundle: VerifiedBundle, target: str,
                state: dict | None, inputs: Path, locks: Path) -> None:
    if binding != bind_plan(plan,bundle,target,state,inputs,locks):
        raise DeliveryRefused("plan")


def apply_exact(binding: PlanBinding, plan: Path, bundle: VerifiedBundle, target: str,
                inputs: Path, locks: Path, *, reservation: Path, cwd: Path, env: dict,
                runner: Callable = subprocess.run, state_reader: Callable | None = None) -> None:
    if reservation.exists():
        raise DeliveryRefused("reserved")
    if state_reader is None:
        pull = runner(["terraform","state","pull"],cwd=cwd,env=env,capture_output=True,text=True,timeout=60)
        if pull.returncode != 0:
            # A failed CLI call is not evidence of absent state. Initial state requires an
            # authenticated canonical Blob 404 reader supplied by the runtime.
            raise DeliveryRefused("plan")
        try:
            state = json.loads(pull.stdout)
        except ValueError:
            raise DeliveryRefused("plan") from None
    else:
        state = state_reader()
    verify_plan(binding,plan,bundle,target,state,inputs,locks)
    exclusive_json(reservation,asdict(binding))
    result = runner(["terraform","apply","-input=false",str(plan.resolve())],cwd=cwd,env=env,
                    capture_output=True,text=True,timeout=480)
    exclusive_json(reservation.with_suffix(".result.json"),{"binding":asdict(binding),"returncode":result.returncode,
        "stdout":result.stdout,"stderr":result.stderr})
    if result.returncode != 0:
        raise DeliveryRefused("plan")


def validate_index_plan(plan: dict, host: str, target: str) -> None:
    try:
        rows = plan["resource_changes"]
        if len(rows) != 1 or target not in ("test","demo-prod"):
            raise DeliveryRefused("plan")
        row = rows[0]
        after = row["change"]["after"]
        if (row["address"] != "azapi_data_plane_resource.index" or row["mode"] != "managed"
            or row["type"] != "azapi_data_plane_resource" or row["change"]["actions"] != ["create"]
            or after["type"] != "Microsoft.Search/searchServices/indexes@2024-07-01"
            or after["parent_id"] != host or after["name"] != "p8-"+target):
            raise DeliveryRefused("plan")
    except (KeyError,TypeError):
        raise DeliveryRefused("plan") from None
