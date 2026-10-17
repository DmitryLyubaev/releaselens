"""Pure pre-billing checks shared by the reviewed owner launchers and runtime."""
from datetime import datetime,timedelta
import re

from app.delivery_search.contracts import DeliveryRefused
from .checks import verify_checks

TIMING_PROFILES={"standard":(75,78,80,120),"provisioning":(180,183,185,240)}
# Full existing parent lifecycles, not reduced provider timeouts. The live jobs
# total 6+13+6+12+6+6 minutes; owner/cron/artifact handoffs have ten more minutes.
REMAINING_SECONDS={"clock":10140,"foundation":9840,"foundation-apply":9480,
    "agents":6840,"agents-apply":6480}

def require_owner_id(authority: dict) -> str:
    value=authority.get("owner_ado_id")
    if (not isinstance(value,str) or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",value)
        or value.replace("-","")=="0"*32):
        raise DeliveryRefused("authority")
    return value

def require_owner_binding(authority: dict,snapshot: dict) -> None:
    owner=require_owner_id(authority)
    try:
        expected=authority["checks_expected"]
        verify_checks(snapshot,expected)
        selected=[r for r in expected["checks"] if r["kind"]=="approval"]
        if len(selected)!=1: raise DeliveryRefused("authority")
        actual=next(r for r in snapshot["checks"] if r["id"]==selected[0]["id"])
        approvers=actual["settings"]["approvers"]
        if len(approvers)!=1 or approvers[0]["id"]!=owner: raise DeliveryRefused("authority")
    except (KeyError,TypeError,StopIteration): raise DeliveryRefused("authority") from None

def require_execution_window(authority: dict,now: datetime,phase: str) -> None:
    from .session import SessionAuthority,instant
    try:
        values={k:authority[k] for k in SessionAuthority.__dataclass_fields__ if k!="timing_profile"}
        a=SessionAuthority(**values,timing_profile=authority.get("timing_profile","standard"))
        a.validate()
        if now.tzinfo is None or a.apply_started_at is None or now<instant(a.apply_started_at):
            raise DeliveryRefused("authority")
        seconds=REMAINING_SECONDS[phase]
        end=instant(a.queue_cutoff_at)-timedelta(minutes=10)
        if now+timedelta(seconds=seconds)>end: raise DeliveryRefused("deadline")
    except (KeyError,TypeError,ValueError): raise DeliveryRefused("authority") from None
