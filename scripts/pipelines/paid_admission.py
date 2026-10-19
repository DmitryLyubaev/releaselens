"""Read-only paid keeper admission shared by future attempt adapters.

No clock, authority, process or resource is created here. The caller supplies a
bounded native process query; all authority/intent/ownership files remain real.
"""
import hashlib
import json

from app.delivery_search.contracts import DeliveryRefused, sha256_file
from .session import instant, read
from .startup import observe_keeper
from .watchdog import same_process


def verify_paid_owner(directory, raw, *, session, source_sha, native_snapshot, require_intent=True):
    try:
        authority_hash = sha256_file(directory / 'authority.json')
        if raw['session'] != session or raw['source_sha'] != source_sha or read(directory / 'authority.json') != raw:
            raise DeliveryRefused('authority')
        if require_intent:
            intent = read(directory / 'first-billable-apply-intent.json')
            if (any(intent[k] != raw[k] for k in ('session', 'source_sha', 'apply_started_at'))
                    or intent['authority_sha256'] != authority_hash or intent['maximum'] != 1
                    or intent['paid_packet_review_sha256'] != sha256_file(directory / 'reviews/paid-execution-packet.json')):
                raise DeliveryRefused('authority')
        ack = read(directory / 'paid-keeper-handoff-ack.json')
        clock = read(directory / 'setup-clock.json')
        if (ack['session'] != session or ack['same_native_process'] is not True
                or ack['paid_authority_sha256'] != authority_hash
                or ack['setup_clock_sha256'] != sha256_file(directory / 'setup-clock.json')
                or clock['session'] != session
                or not instant(clock['S0']) <= instant(raw['apply_started_at']) <= instant(clock['ready_or_cleanup_due_at'])):
            raise DeliveryRefused('authority')
        own = read(directory / 'watchdog-owned.json')
        setup = read(directory / 'setup-keeper-owned.json')['process']
        if own['pid'] != ack['pid'] or not same_process(setup, own):
            raise DeliveryRefused('authority')
        captured = []
        def heartbeat():
            # Recheck cancellation and authority after the slow native query too.
            if (any((directory / name).exists() for name in ('cleanup-native.json', 'paid-cleanup-fallback-required.json',
                    'owner-stop-authority.json', 'stopped-gate.json', 'quiescence/request.json'))
                    or sha256_file(directory / 'authority.json') != authority_hash):
                raise DeliveryRefused('authority')
            data = (directory / 'watchdog-heartbeat.json').read_bytes()
            captured.append(hashlib.sha256(data).hexdigest())
            return json.loads(data)
        observation = observe_keeper(own, heartbeat, native_snapshot, session=session, source_sha=source_sha)
        return {'at': observation['checked_at'], 'session': session, 'source_sha': source_sha,
                'authority_sha256': authority_hash, 'paid_intent_required': require_intent,
                'same_native_keeper': observation['keeper'], 'fresh_heartbeat_sha256': captured[0]}
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise DeliveryRefused('authority') from exc
