"""Finite Windows owner supervisor. Structured argv crosses WSL; no shell interpolation."""
import argparse
import ctypes
from datetime import datetime,timezone,timedelta
import hashlib
import json
import os
from pathlib import Path,PureWindowsPath
import subprocess
import sys
import time

from app.delivery_search.contracts import DeliveryRefused,exclusive_json,sha256_file
from .session import load,read,operation_lock,instant

OWNER_PYTHON="/home/dmitrylyubaev/.venvs/releaselens-webapp/bin/python"


def linux_path(value: str) -> str:
    path=PureWindowsPath(value)
    if not path.is_absolute() or len(path.drive)!=2 or path.drive[1]!=":": raise DeliveryRefused("authority")
    return "/mnt/"+path.drive[0].lower()+"/"+"/".join(path.parts[1:])


def wsl_command(session: str,*,probe=False,status=False) -> list[str]:
    path=linux_path(session)
    root=str(Path(path).parents[2]).replace("\\","/")
    # PureWindowsPath is needed here when constructing Linux paths on Windows.
    root=path.rsplit("/",3)[0]
    return ["wsl.exe","--distribution","Ubuntu","--cd",root,"--exec","/usr/bin/env",
        "PYTHONPATH="+root+":"+root+"/eval",OWNER_PYTHON,"-m",
        "scripts.pipelines.watchdog" if probe else "scripts.pipelines.cleanup",
        "--session",path,*(["--wsl-probe"] if probe else ["--status"] if status else [])]


def same_process(expected: dict,actual: dict | None) -> bool:
    return actual is not None and all(expected.get(k)==actual.get(k) and expected.get(k) is not None
        for k in ("pid","created","command_line","sha256"))


def process_present(expected: dict,actual: dict | None) -> bool:
    return actual is not None and all(expected.get(k)==actual.get(k) and expected.get(k) is not None
        for k in ("pid","created","command_line"))


def process_snapshot(pid: int) -> dict | None:
    if os.name!="nt" or type(pid) is not int or pid<1: raise DeliveryRefused("authority")
    command=("$p=Get-CimInstance Win32_Process -Filter 'ProcessId="+str(pid)+"'; "
        "if ($null -ne $p) { @{pid=[int]$p.ProcessId;created=$p.CreationDate.ToUniversalTime().ToString('o');"
        "command_line=$p.CommandLine} | ConvertTo-Json -Compress }")
    result=subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-Command",command],
        capture_output=True,text=True,timeout=15)
    if result.returncode: raise DeliveryRefused("authority")
    if not result.stdout.strip(): return None
    value=json.loads(result.stdout); value["sha256"]=sha256_file(Path(__file__))
    return value


def probe(directory: Path):
    child=subprocess.Popen(wsl_command(str(directory.resolve()),probe=True),stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,creationflags=subprocess.CREATE_NO_WINDOW)
    before=process_snapshot(child.pid)
    stdout,stderr=child.communicate(timeout=45)
    after=process_snapshot(child.pid)
    if before is None or same_process(before,after) or child.returncode or stdout.strip()!="status=pass code=wsl-probe":
        raise DeliveryRefused("authority")
    exclusive_json(directory/"watchdog-probe-process.json",{"expected":before,"actual_after":after,"exited":True})
    original=read(directory/"watchdog-wsl-probe.json")
    if original["session_path"]!=linux_path(str(directory.resolve())) or original["argv_has_backslash"]:
        raise DeliveryRefused("authority")
    exclusive_json(directory/"watchdog-windows-probe.json",{"returncode":0,
        "helper_sha256":sha256_file(Path(__file__)),
        "wsl_original_sha256":sha256_file(directory/"watchdog-wsl-probe.json"),
        "command":wsl_command(str(directory.resolve()),probe=True),"at":datetime.now(timezone.utc).isoformat()})


def adopt_authority(previous,current):
    current.validate()
    if (any(getattr(previous,k)!=getattr(current,k) for k in ("session","source_sha","tree_sha","release_digest","manifest_sha256","timing_profile"))
        or previous.apply_started_at is not None and previous.apply_started_at!=current.apply_started_at):
        raise DeliveryRefused("authority")
    return current


def prepare_handoff(directory,a,raw,now):
    """Wake the browser operator without authorizing early restoration or cleanup."""
    a.validate()
    if a.apply_started_at is None or now<instant(a.queue_cutoff_at)-timedelta(minutes=10):
        return
    if type(raw["pipeline_id"]) is not int or raw["pipeline_id"]<=1:
        raise DeliveryRefused("authority")
    path=directory/"quiescence-preparation.json"
    value={"kind":"owner-browser-quiescence-preparation","session":a.session,"source_sha":a.source_sha,
        "manifest_sha256":a.manifest_sha256,"pipeline_id":raw["pipeline_id"],
        "queue_cutoff_at":a.queue_cutoff_at,"cleanup_due_at":a.cleanup_due_at,
        "acknowledge_by":(instant(a.cleanup_due_at)-timedelta(seconds=45)).isoformat(),
        "actions":["prepare-owner-browser-restoration"]}
    if path.exists():
        previous=read(path)
        if any(previous.get(key)!=item for key,item in value.items()):
            raise DeliveryRefused("authority")
        return previous
    exclusive_json(path,value|{"at":now.isoformat()})


def cutoff_handoff(directory,a,raw,now):
    try:
        prepare_handoff(directory,a,raw,now)
    except Exception:
        # Readiness is advisory. It cannot suppress the original cutoff request
        # or the independent due-time deletion fallback.
        pass
    if now>=instant(a.queue_cutoff_at):
        from .quiescence import request
        try: request(directory,a,raw,now)
        except Exception as exc:
            path=directory/"cutoff-handoff-failure.json"
            if not path.exists():
                exclusive_json(path,{"source_sha":a.source_sha,"session":a.session,"at":now.isoformat(),
                    "code":exc.code if isinstance(exc,DeliveryRefused) else "internal"})
            # A missing browser handoff cannot cancel the independent cleanup deadline.


def verify_exits(directory: Path):
    a,raw,manifest=load(directory)
    helpers=[]
    for name in ("watchdog-owned.json","cleanup-native.json","inbox-owned.json","observer-owned.json"):
        if not (directory/name).exists():
            if name in ("watchdog-owned.json","cleanup-native.json"): raise DeliveryRefused("authority")
            continue
        expected=read(directory/name); actual=process_snapshot(expected["pid"])
        helpers.append({"name":name,"expected":expected,"actual":actual})
        if process_present(expected,actual): raise DeliveryRefused("reserved")
    status=subprocess.run(wsl_command(str(directory.resolve()),status=True),capture_output=True,text=True,timeout=45)
    if status.returncode or status.stdout.strip()!="status=idle": raise DeliveryRefused("reserved")
    original=read(directory/"operation-status.json")
    if original["active_owned"]: raise DeliveryRefused("reserved")
    exclusive_json(directory/"helper-exit-verification.json",{"session":a.session,"source_sha":a.source_sha,
        "helpers":helpers,"active_owned":[],"operation_status_sha256":sha256_file(directory/"operation-status.json"),
        "at":datetime.now(timezone.utc).isoformat()})


def dispatch_cleanup(directory: Path):
    a,raw,_=load(directory)
    now=datetime.now(timezone.utc)
    if not a.cleanup_due(now):
        from .quiescence import request_closeout,status
        request_closeout(directory,now)
        if not status(directory,a,raw)["verified"]:
            # Do not consume the one native-helper reservation for a pending
            # early handoff. The original due-time supervisor must still launch.
            raise DeliveryRefused("cleanup")
    # All owner callers use this reservation, so an early cleanup is adopted at due.
    with operation_lock(directory):
        marker=directory/"cleanup-native.json"
        if marker.exists():
            original=read(marker)
            actual=process_snapshot(original["pid"])
            if same_process(original,actual): return None
            if (directory/"cleanup-result.json").exists(): return None
            raise DeliveryRefused("reserved")  # Reconcile the dead owned helper before a fresh continuation.
        exclusive_json(directory/"cleanup-launch-intent.json",{"argv":wsl_command(str(directory.resolve())),
            "helper_sha256":sha256_file(Path(__file__)),"at":datetime.now(timezone.utc).isoformat()})
        with (directory/"cleanup-native.private.log").open("xb") as out:
            child=subprocess.Popen(wsl_command(str(directory.resolve())),stdout=out,stderr=out,
                creationflags=subprocess.CREATE_NO_WINDOW)
        actual=process_snapshot(child.pid)
        if actual is None: raise DeliveryRefused("reserved")
        exclusive_json(marker,actual)
        return child


def supervise(directory: Path):
    if os.name!="nt": raise DeliveryRefused("authority")
    a,raw,manifest=load(directory)
    from .session import verify_source
    verify_source(a)
    probe_original=read(directory/"watchdog-windows-probe.json")
    if (probe_original["wsl_original_sha256"]!=sha256_file(directory/"watchdog-wsl-probe.json")
        or probe_original["helper_sha256"]!=sha256_file(Path(__file__))):
        raise DeliveryRefused("authority")
    with operation_lock(directory/"watchdog"):
        ownership=process_snapshot(os.getpid())
        exclusive_json(directory/"watchdog-owned.json",ownership)
        lease_end=instant(a.closeout_by)+timedelta(minutes=30)
        child=None
        try:
            while datetime.now(timezone.utc)<lease_end:
                new_a,new_raw,new_manifest=load(directory)
                if new_raw["approval_receipt_sha256"]!=raw["approval_receipt_sha256"]:
                    raise DeliveryRefused("authority")
                a=adopt_authority(a,new_a)
                lease_end=instant(a.closeout_by)+timedelta(minutes=30)
                if (directory/"cleanup-result.json").exists() and read(directory/"cleanup-result.json").get("physical_pass") is True:
                    break
                # Preparation is preserved. The approved authority must already carry the
                # actual first-apply start before this finite live supervisor is launched.
                if a.apply_started_at is not None:
                    ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)
                    cutoff_handoff(directory,a,new_raw,datetime.now(timezone.utc))
                    if a.cleanup_due(datetime.now(timezone.utc)) and child is None:
                        child=dispatch_cleanup(directory)
                heartbeat={"pid":os.getpid(),"at":datetime.now(timezone.utc).isoformat(),
                    "source_sha":a.source_sha,"session":a.session,"child_pid":child.pid if child else None}
                temporary=directory/"watchdog-heartbeat.tmp"
                temporary.write_text(json.dumps(heartbeat),encoding="utf-8")
                os.replace(temporary,directory/"watchdog-heartbeat.json")
                time.sleep(10)
        finally:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            exclusive_json(directory/"watchdog-returned.json",{"pid":os.getpid(),"at":datetime.now(timezone.utc).isoformat()})


def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument("--session",type=Path,required=True)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--probe",action="store_true"); group.add_argument("--wsl-probe",action="store_true")
    group.add_argument("--run",action="store_true"); group.add_argument("--request-cleanup",action="store_true")
    group.add_argument("--verify-exit",action="store_true")
    args=parser.parse_args(argv)
    try:
        if args.wsl_probe:
            if os.name=="nt": raise DeliveryRefused("authority")
            time.sleep(3)  # Allows the Windows parent to capture the real native process identity.
            exclusive_json(args.session/"watchdog-wsl-probe.json",{"session_path":str(args.session.resolve()),
                "argv_has_backslash":any("\\" in value for value in sys.argv),"pid":os.getpid(),
                "at":datetime.now(timezone.utc).isoformat()})
            print("status=pass code=wsl-probe"); return 0
        if args.probe: probe(args.session)
        elif args.run: supervise(args.session)
        elif args.verify_exit: verify_exits(args.session)
        else:
            load(args.session)
            dispatch_cleanup(args.session)
        print("status=pass code=watchdog"); return 0
    except Exception as exc:
        print("status=refused code="+(exc.code if isinstance(exc,DeliveryRefused) else "internal")); return 2


if __name__=="__main__": raise SystemExit(main())
