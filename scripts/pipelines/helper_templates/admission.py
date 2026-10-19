"""Thin adapters; fresh packet preparation binds these sources and their callers.

The context is the reviewed attempt's common module (S, SESSION, SOURCE, read).
This module creates no authority and performs no browser/cloud mutation.
"""
from datetime import datetime, timezone, timedelta

from app.delivery_search.contracts import DeliveryRefused
from ..definition import require_setup_restored
from ..paid_admission import verify_paid_owner as verify_owner
from ..session import require_dispatch_current


def setup_operations(context):
    """Paid-transfer writers must hold this same lock as prepaid rollback."""
    from ..session import operation_lock
    return operation_lock(context.S / 'setup-operations')


def verify_paid_owner(context, raw, *, native_snapshot, require_intent=True):
    return verify_owner(context.S, raw, session=context.SESSION, source_sha=context.SOURCE,
                        native_snapshot=native_snapshot, require_intent=require_intent)


def verify_restoration(context, clients):
    if clients not in ('new', 'original'):
        raise DeliveryRefused('authority')
    proof = context.read(context.S / 'setup-ado-restoration.private.json')
    if (proof['kind'] != 'actual-browser-setup-restoration-readbacks'
            or proof['session'] != context.SESSION or proof['source_sha'] != context.SOURCE):
        raise DeliveryRefused('authority')
    before = context.read(context.S / 'platform-operational-baseline.private.json')['readbacks']
    principals = context.read(context.S / 'identity-apply/actual.private.json')['identities'] if clients == 'new' else None
    require_setup_restored(before, proof['readbacks'], principals=principals)
    if not timedelta(0) <= datetime.now(timezone.utc) - datetime.fromisoformat(proof['at']) < timedelta(minutes=10):
        raise DeliveryRefused('evidence')
    return True


def final_dispatch(context, mode, *, native_snapshot):
    # Source/intent checks precede the potentially slow paid process query.
    # The shared final guard checks cancellation and wall time after both.
    from ..session import read
    raw = read(context.S / 'authority.json')
    proof = require_dispatch_current(context.S, mode, owner_check=lambda: verify_paid_owner(
        context, raw, native_snapshot=native_snapshot))
    if proof['session'] != context.SESSION or proof['source_sha'] != context.SOURCE:
        raise DeliveryRefused('authority')
    return proof
