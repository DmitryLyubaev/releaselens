"""Durable owner-browser handoff. No browser credentials or ADO token scopes here."""
import argparse
from datetime import datetime,timezone
from pathlib import Path

from app.delivery_search.contracts import DeliveryRefused,canonical,exclusive_json,sha256_file
from .session import load,read,verify_source,operation_lock,instant

MODES=("release","negative","scheduled")


def _build(build,mode,a,raw,directory):
    intent=read(directory/"intents"/("dispatch-"+mode+".json"))
    if (type(build["id"]) is not int or build["id"]<1 or raw["pipeline_id"]==1
        or build["definition"]["id"]!=raw["pipeline_id"] or build["sourceVersion"]!=a.source_sha
        or build["sourceBranch"]!="refs/heads/main" or build["reason"]!=("schedule" if mode=="scheduled" else "manual")
        or not instant(intent["at"])<=instant(build["queueTime"])<instant(a.queue_cutoff_at)):
        raise DeliveryRefused("authority")


def register_build(directory,mode,build):
    a,raw,_=load(directory); verify_source(a)
    if mode not in MODES: raise DeliveryRefused("authority")
    _build(build,mode,a,raw,directory)
    with operation_lock(directory/"quiescence"):
        if (directory/"quiescence/request.json").exists(): raise DeliveryRefused("deadline")
        folder=directory/"owned-builds"; folder.mkdir(exist_ok=True)
        if any(read(p)["id"]==build["id"] for p in folder.glob("*.json")): raise DeliveryRefused("reserved")
        exclusive_json(folder/(mode+".json"),build)


def request_closeout(directory,now):
    a,raw,_=load(directory); verify_source(a)
    if now<instant(a.queue_cutoff_at):
        from .cleanup import early_allowed
        from .evidence import grade
        stop=read(directory/"owner-stop-authority.json") if (directory/"owner-stop-authority.json").exists() else {}
        complete=all(value["status"]=="PASS" for key,value in grade(directory).items() if key!="P8-8")
        if not complete and not early_allowed(stop,a.source_sha,a.session): raise DeliveryRefused("authority")
    return request(directory,a,raw,now,early=True)


def request(directory,a,raw,now,*,early=False):
    if type(raw["pipeline_id"]) is not int or raw["pipeline_id"]<=1: raise DeliveryRefused("authority")
    if not early and now<instant(a.queue_cutoff_at): raise DeliveryRefused("deadline")
    with operation_lock(directory/"quiescence"):
        path=directory/"quiescence/request.json"
        if path.exists():
            existing=read(path)
            if any(existing[key]!=value for key,value in (("session",a.session),("source_sha",a.source_sha),
                ("manifest_sha256",a.manifest_sha256),("pipeline_id",raw["pipeline_id"]))): raise DeliveryRefused("authority")
            return existing
        records=[]
        for mode in MODES:
            original=directory/"owned-builds"/(mode+".json")
            if original.exists():
                build=read(original); _build(build,mode,a,raw,directory)
                records.append({"mode":mode,"id":build["id"],"sha256":sha256_file(original)})
        value={"kind":"owner-browser-quiescence-request","session":a.session,"source_sha":a.source_sha,
            "tree_sha":a.tree_sha,"manifest_sha256":a.manifest_sha256,"pipeline_id":raw["pipeline_id"],
            "build_ids":[r["id"] for r in records],"builds":records,"at":now.isoformat(),
            "actions":["disable-pipeline","cancel-session-builds","restore-original-variables"]}
        exclusive_json(path,value)
        return value


def require_restored_definition(before,after,pipeline_id):
    if (before["id"]!=pipeline_id or after["id"]!=pipeline_id or after["queueStatus"]!="disabled"
        or before.get("variables",{})!=after.get("variables",{})):
        raise DeliveryRefused("gate")
    for key in ("repository","process","queue","triggers","options","properties","jobAuthorizationScope"):
        if canonical(before.get(key))!=canonical(after.get(key)): raise DeliveryRefused("gate")


def _verified(directory,a,raw,requested):
    from .evidence import Originals
    originals=Originals(directory,a,raw)
    keys=["definition-original","definition-disabled","queue-after"]
    before=originals.json(keys[0]); after=originals.json(keys[1]); queue=originals.json(keys[2])
    require_restored_definition(before,after,raw["pipeline_id"])
    if queue["count"]!=0 or queue["value"]!=[]: raise DeliveryRefused("gate")
    for row in requested["builds"]:
        path=directory/"owned-builds"/(row["mode"]+".json")
        if sha256_file(path)!=row["sha256"] or read(path)["id"]!=row["id"]: raise DeliveryRefused("authority")
        key="quiesced-build-"+str(row["id"]); keys.append(key)
        current=originals.json(key)
        _build(current,row["mode"],a,raw,directory)
        if current["id"]!=row["id"] or current["status"]!="completed": raise DeliveryRefused("gate")
    return {key:sha256_file(originals.path(key)) for key in keys}


def acknowledge(directory,request_sha256,outcome):
    a,raw,_=load(directory); verify_source(a)
    if outcome not in ("verified","unavailable","failed"): raise DeliveryRefused("authority")
    with operation_lock(directory/"quiescence"):
        path=directory/"quiescence/request.json"
        if sha256_file(path)!=request_sha256: raise DeliveryRefused("authority")
        requested=read(path)
        if requested["session"]!=a.session or requested["source_sha"]!=a.source_sha or requested["pipeline_id"]!=raw["pipeline_id"]:
            raise DeliveryRefused("authority")
        evidence=_verified(directory,a,raw,requested) if outcome=="verified" else {}
        exclusive_json(directory/"quiescence/ack.json",{"kind":"owner-browser-quiescence-attempt",
            "session":a.session,"source_sha":a.source_sha,"request_sha256":request_sha256,"outcome":outcome,
            "originals":evidence,"at":datetime.now(timezone.utc).isoformat()})


def status(directory,a,raw):
    path=directory/"quiescence/request.json"
    result={"verified":False,"browser_attempt":"missing","request_sha256":sha256_file(path)}
    if not (directory/"quiescence/ack.json").exists(): return result
    ack=read(directory/"quiescence/ack.json"); requested=read(path)
    if (ack["request_sha256"]!=result["request_sha256"] or ack["session"]!=a.session or ack["source_sha"]!=a.source_sha
        or requested["pipeline_id"]!=raw["pipeline_id"] or requested["manifest_sha256"]!=a.manifest_sha256
        or instant(ack["at"])<instant(requested["at"])): raise DeliveryRefused("authority")
    result["browser_attempt"]=ack["outcome"]
    if ack["outcome"]=="verified":
        if ack["originals"]!=_verified(directory,a,raw,requested): raise DeliveryRefused("authority")
        result["verified"]=True
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument("--session",type=Path,required=True)
    parser.add_argument("--register-build",choices=MODES); parser.add_argument("--original",type=Path)
    parser.add_argument("--acknowledge",choices=("verified","unavailable","failed")); parser.add_argument("--request-sha256")
    parser.add_argument("--request-closeout",action="store_true")
    args=parser.parse_args(argv)
    try:
        if sum((bool(args.register_build),bool(args.acknowledge),args.request_closeout))!=1: raise DeliveryRefused("authority")
        if args.register_build: register_build(args.session,args.register_build,read(args.original))
        elif args.acknowledge: acknowledge(args.session,args.request_sha256,args.acknowledge)
        else: request_closeout(args.session,datetime.now(timezone.utc))
        print("status=pass code=quiescence"); return 0
    except Exception:
        print("status=refused code=quiescence"); return 2


if __name__=="__main__": raise SystemExit(main())
