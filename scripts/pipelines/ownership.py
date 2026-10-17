"""Resolve only predeclared computed slots. The approved manifest never changes."""
import copy
import hashlib
from pathlib import Path
import re

from app.delivery_search.contracts import DeliveryRefused,canonical,exclusive_json,sha256_file
from .session import bound_file,read


def validate_policy(component,spec,raw,directory):
    """Unknown values can fill fixed typed slots, never introduce an address or scope."""
    rule=spec["resolve"]; plan=component["create_plan"]
    if plan["kind"]=="owner":
        for name in ("binary","shown"):
            bound_file(directory,plan[name])
            if not re.fullmatch(r"[0-9a-f]{64}",plan[name+"_sha256"]): raise DeliveryRefused("authority")
    elif plan["kind"]=="pipeline-index":
        if component["name"]!="index-"+plan["target"] or plan["target"] not in ("test","demo-prod"):
            raise DeliveryRefused("authority")
    else: raise DeliveryRefused("authority")
    if rule["kind"]=="role-assignment":
        if (spec["kind"]!="arm" or not spec["address"].startswith("azurerm_role_assignment.")
            or plan["kind"]!="owner" or not any(rule["scope"].lower()==g.lower() or rule["scope"].lower().startswith(g.lower()+"/") for g in raw["owned_group_ids"])
            or not re.fullmatch(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",rule["role_definition_id"])):
            raise DeliveryRefused("authority")
        if bool(rule.get("principal_id"))==bool(rule.get("principal_ref")): raise DeliveryRefused("authority")
        if rule.get("principal_id"):
            if not re.fullmatch(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",rule["principal_id"]): raise DeliveryRefused("authority")
        else:
            ref=next(r for r in component["resources"] if r["address"]==rule["principal_ref"])
            if (not ref["id"] or not ref["address"].startswith("azurerm_user_assigned_identity.")
                or "/providers/microsoft.managedidentity/userassignedidentities/" not in ref["id"].lower()):
                raise DeliveryRefused("authority")
    elif rule["kind"]=="index":
        if (spec["kind"]!="index" or plan["kind"]!="pipeline-index" or spec["address"]!="azapi_data_plane_resource.index"
            or spec["host"]!=raw["search_host"] or spec["name"]!="p8-"+plan["target"]): raise DeliveryRefused("authority")
    elif rule["kind"]=="local-gate":
        if (plan["kind"]!="owner" or component["name"]!="agents" or spec["kind"]!="local"
            or spec["address"]!="terraform_data.pat_ready" or rule["input"]["session"]!=raw["session"]
            or rule["input"]["source"]!=raw["source_sha"]): raise DeliveryRefused("authority")
    else: raise DeliveryRefused("authority")


def resources(state):
    def walk(module):
        rows=[r for r in module.get("resources",[]) if r["mode"]=="managed"]
        for child in module.get("child_modules",[]): rows.extend(walk(child))
        return rows
    return walk(state.get("values",{}).get("root_module",{}))


def binding_path(directory,component,address):
    return directory/"identity-bindings"/component/(hashlib.sha256(address.encode()).hexdigest()+".json")


def validate_slot(spec,row,all_rows,raw):
    values=row["values"]; identity=values["id"]
    if not isinstance(identity,str) or not identity or len(identity)>2048: raise DeliveryRefused("authority")
    if spec.get("id") is not None and spec["id"]!=identity: raise DeliveryRefused("authority")
    rule=spec.get("resolve")
    if rule is None:
        if spec.get("id") is None: raise DeliveryRefused("authority")
        return identity
    if rule["kind"]=="role-assignment":
        principal=rule.get("principal_id")
        if rule.get("principal_ref"):
            reference=all_rows[rule["principal_ref"]]
            if reference["type"]!="azurerm_user_assigned_identity": raise DeliveryRefused("authority")
            principal=reference["values"]["principal_id"]
        prefix=rule["scope"]+"/providers/Microsoft.Authorization/roleAssignments/"
        if (row["type"]!="azurerm_role_assignment" or values["scope"].lower()!=rule["scope"].lower()
            or not principal or values["principal_id"].lower()!=principal.lower()
            or values["role_definition_id"].rsplit("/",1)[-1].lower()!=rule["role_definition_id"].lower()
            or not identity.lower().startswith(prefix.lower()) or not re.fullmatch(r"[0-9a-fA-F-]{36}",identity[len(prefix):])):
            raise DeliveryRefused("authority")
    elif rule["kind"]=="index":
        if (row["type"]!="azapi_data_plane_resource" or values["parent_id"]!=spec["host"] or values["name"]!=spec["name"]
            or values["type"]!="Microsoft.Search/searchServices/indexes@2024-07-01"
            or identity!=spec["host"]+"/indexes('"+spec["name"]+"')"):
            raise DeliveryRefused("authority")
    elif rule["kind"]=="local-gate":
        expected=rule["input"]
        if (row["type"]!="terraform_data" or spec["address"]!="terraform_data.pat_ready"
            or expected["session"]!=raw["session"] or expected["source"]!=raw["source_sha"]
            or canonical(values["input"])!=canonical(expected)): raise DeliveryRefused("authority")
    else: raise DeliveryRefused("authority")
    return identity


def validate_create_intent(directory,raw,component,intent=None):
    if intent is None: intent=read(directory/"apply-intents"/(component["name"]+".json"))
    if (intent["source_sha"]!=raw["source_sha"] or intent["session"]!=raw["session"]
        or intent["manifest_sha256"]!=raw["manifest_sha256"] or intent["component"]!=component["name"]):
        raise DeliveryRefused("authority")
    plan=component["create_plan"]
    if plan["kind"]=="owner":
        if (sha256_file(bound_file(directory,plan["binary"]))!=plan["binary_sha256"]
            or sha256_file(bound_file(directory,plan["shown"]))!=plan["shown_sha256"]
            or intent["plan_sha256"]!=plan["binary_sha256"]): raise DeliveryRefused("authority")
        changes=read(bound_file(directory,plan["shown"]))["resource_changes"]
        managed=[row for row in changes if row["mode"]=="managed"]
        if ({row["address"] for row in managed}!={row["address"] for row in component["resources"]}
            or any(row["change"]["actions"]!=["create"] for row in managed)):
            raise DeliveryRefused("authority")
    elif plan["kind"]=="pipeline-index":
        binding=intent["binding"]
        if (component["name"]!="index-"+plan["target"] or binding["target"]!=plan["target"]
            or binding["source_sha"]!=raw["source_sha"] or binding["bundle_digest"]!=raw["release_digest"]
            or not re.fullmatch(r"[0-9a-f]{64}",binding["plan_sha256"])): raise DeliveryRefused("authority")
    else: raise DeliveryRefused("authority")


def reserve_owner_apply(directory,component_name):
    """Called by the separately approved owner launcher immediately before its one apply."""
    from .session import load,operation_lock,verify_source
    a,raw,manifest=load(directory); verify_source(a)
    from .readiness import require_owner_binding,require_execution_window
    if sha256_file(directory/"checks-snapshot.json")!=raw["checks_snapshot_sha256"]:
        raise DeliveryRefused("authority")
    require_owner_binding(raw,read(directory/"checks-snapshot.json"))
    if component_name in ("foundation","agents"):
        from datetime import datetime,timezone
        require_execution_window(raw,datetime.now(timezone.utc),component_name+"-apply")
    component=next(c for c in manifest["components"] if c["name"]==component_name)
    if component["create_plan"]["kind"]!="owner": raise DeliveryRefused("authority")
    intent={"session":a.session,"source_sha":a.source_sha,"component":component_name,
        "manifest_sha256":a.manifest_sha256,"plan_sha256":component["create_plan"]["binary_sha256"]}
    validate_create_intent(directory,raw,component,intent)
    with operation_lock(directory):
        folder=directory/"apply-intents"; folder.mkdir(exist_ok=True)
        exclusive_json(folder/(component_name+".json"),intent)
    return intent


def local_component_group(component,authority):
    """Only a foundation or agents exact bound local group can prove this case."""
    try:
        if component["name"] not in ("foundation","agents") or component["backend"]["type"]!="local": raise DeliveryRefused("plan")
        roots=[r["id"] for r in component["resources"] if r.get("kind")=="arm"
            and r.get("id") in authority["owned_group_ids"]]
        if len(roots)!=1: raise DeliveryRefused("plan")
        group=roots[0]
        for row in component["resources"]:
            if row.get("kind")!="arm": continue
            value=row.get("id") or row["resolve"]["scope"]
            if value.lower()!=group.lower() and not value.lower().startswith(group.lower()+"/"):
                raise DeliveryRefused("plan")
        return group
    except (KeyError,TypeError,AttributeError): raise DeliveryRefused("plan") from None


def require_empty_show_json(shown):
    """Recognize the pinned Terraform show schema, never raw state or plan JSON."""
    if (not isinstance(shown,dict) or shown.get("format_version")!="1.0"
        or not set(shown).issubset({"format_version","terraform_version","values","checks"})
        or ("terraform_version" in shown and not isinstance(shown["terraform_version"],str))
        or ("checks" in shown and shown["checks"]!=[])):
        raise DeliveryRefused("gate")
    values=shown.get("values",{})
    if (not isinstance(values,dict) or not set(values).issubset({"root_module","outputs"})
        or ("outputs" in values and values["outputs"]!={})):
        raise DeliveryRefused("gate")
    root=values.get("root_module",{})
    if (not isinstance(root,dict) or not set(root).issubset({"resources","child_modules"})
        or any(root[key]!=[] for key in root)):
        raise DeliveryRefused("gate")


def require_local_state_absence(proof,component,authority):
    from pathlib import PurePosixPath
    try:
        group=local_component_group(component,authority)
        relative=PurePosixPath(component["backend"]["path"])
        if relative.is_absolute() or ".." in relative.parts or str(relative)==".": raise DeliveryRefused("gate")
        cwd=str(PurePosixPath(authority["owner_session_path"])/component["directory"])
        path=str(PurePosixPath(cwd)/relative)
        if (proof["kind"]!="never-applied-local-"+component["name"] or proof["component"]!=component["name"]
            or any(proof[k]!=authority[k] for k in ("session","source_sha","manifest_sha256"))
            or proof["directory"].replace("\\","/")!=cwd or proof["state_path"].replace("\\","/")!=path
            or any(proof[k] is not True for k in ("state_absent","backup_absent","lock_absent","apply_intent_absent"))
            or proof["group"]["id"]!=group or proof["group"]["status"]!=404):
            raise DeliveryRefused("gate")
        require_empty_show_json(proof["terraform_show"])
    except (KeyError,TypeError,AttributeError): raise DeliveryRefused("gate") from None


def _original(directory,raw,name):
    if sha256_file(directory/"cleanup-manifest.json")!=raw["manifest_sha256"]: raise DeliveryRefused("authority")
    return next(c for c in read(directory/"cleanup-manifest.json")["components"] if c["name"]==name)


def _record_identity(directory,raw,component,spec,record):
    if (record["manifest_sha256"]!=raw["manifest_sha256"] or record["source_sha"]!=raw["source_sha"]
        or record["session"]!=raw["session"] or record["component"]!=component["name"] or record["address"]!=spec["address"]):
        raise DeliveryRefused("authority")
    path=bound_file(directory,record["state_path"])
    if sha256_file(path)!=record["state_sha256"]: raise DeliveryRefused("authority")
    all_rows={r["address"]:r for r in resources(read(path))}
    original={r["address"]:r for r in component["resources"]}
    if not set(all_rows).issubset(original): raise DeliveryRefused("authority")
    # Verify referenced identities against their immutable original ARM IDs first.
    for address,row in all_rows.items():
        if original[address].get("id") is not None and original[address]["id"]!=row["values"]["id"]:
            raise DeliveryRefused("authority")
    identity=validate_slot(spec,all_rows[spec["address"]],all_rows,raw)
    if identity!=record["id"]: raise DeliveryRefused("authority")
    return identity


def resolve_existing(directory,raw,manifest):
    result=copy.deepcopy(manifest)
    for component in result["components"]:
        for spec in component["resources"]:
            if spec.get("id") is not None: continue
            path=binding_path(directory,component["name"],spec["address"])
            if path.exists():
                validate_create_intent(directory,raw,component)
                spec["id"]=_record_identity(directory,raw,component,spec,read(path))
    return result


def bind_state(directory,raw,component,state):
    original=_original(directory,raw,component["name"])
    rows=resources(state); by_address={r["address"]:r for r in rows}
    approved={r["address"]:r for r in original["resources"]}
    if len(rows)!=len(by_address) or not set(by_address).issubset(approved): raise DeliveryRefused("authority")
    # All guards run before any new binding record. Existing bindings can never change.
    selected={address:validate_slot(spec,by_address[address],by_address,raw)
        for address,spec in approved.items() if address in by_address}
    unresolved=[spec for spec in original["resources"] if spec.get("id") is None and spec["address"] in selected]
    if unresolved: validate_create_intent(directory,raw,original)
    for spec in unresolved:
        path=binding_path(directory,component["name"],spec["address"])
        if path.exists() and _record_identity(directory,raw,original,spec,read(path))!=selected[spec["address"]]:
            raise DeliveryRefused("authority")
    if unresolved:
        content=canonical(state)+b"\n"; digest=hashlib.sha256(content).hexdigest()
        folder=directory/"ownership-state"; folder.mkdir(exist_ok=True)
        snapshot=folder/(component["name"]+"-"+digest+".json")
        if not snapshot.exists(): exclusive_json(snapshot,state)
        if sha256_file(snapshot)!=digest: raise DeliveryRefused("authority")
        for spec in unresolved:
            path=binding_path(directory,component["name"],spec["address"])
            if not path.exists():
                path.parent.mkdir(parents=True,exist_ok=True)
                exclusive_json(path,{"session":raw["session"],"source_sha":raw["source_sha"],"manifest_sha256":raw["manifest_sha256"],
                    "component":component["name"],"address":spec["address"],"id":selected[spec["address"]],
                    "state_path":snapshot.relative_to(directory).as_posix(),"state_sha256":digest})
    resolved=resolve_existing(directory,raw,{"components":[original]})
    component["resources"]=resolved["components"][0]["resources"]
