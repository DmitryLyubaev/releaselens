"""Local owner handoff for one diagnostic. Never authenticates or queues a build.

Static browser work precedes authority issuance. The submission window starts
only after final authority-variable readback; neither clock can be renewed.
"""
import argparse
import copy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

from app.delivery_search.contracts import DeliveryRefused, canonical, exclusive_json, sha256_file
from .authority_transport import encode_authority
from .identity_preflight import require_context
from .definition import saved_definition
from .session import bound_file, instant, operation_lock, read, verify_source

FOLDER = 'diagnostic-preparation'
ROLES = ('test', 'prod', 'verify')
READBACKS = {'definition', 'variableGroups', 'groupPermission', 'groupChecks', 'unfinishedBuilds'} | {
    role + suffix for role in ROLES for suffix in ('', 'Permission', 'Checks')}
VARIABLES = {'P8_PREFLIGHT_JSON', 'P8_PREFLIGHT_SHA256', 'P8_PREFLIGHT_SOURCE_SHA'}
STOP_FILES = ('setup-stop.json', 'setup-abort-complete.json', 'closeout.json',
              'owner-stop-authority.json', 'stopped-gate.json', 'quiescence/request.json',
              'first-billable-apply-intent.json', 'paid-keeper-handoff-ack.json',
              'paid-handoff.json', 'paid-clock.json', 'authority.json')
JOB_AND_MARGIN = timedelta(minutes=21)
WINDOW = timedelta(minutes=5)
REVIEW_RUNWAY = timedelta(minutes=4)
DISPATCH_RUNWAY = timedelta(minutes=2)
READBACK_AGE = timedelta(minutes=2)
ENABLE_LIMIT = timedelta(seconds=120)
CHECKOUT_DEFAULTS = {'repository.clean': 'true',
    **{'repository.properties.' + key: value for key, value in {
        'checkoutNestedSubmodules': 'false', 'cleanOptions': '0', 'fetchTags': 'true',
        'gitLfsSupport': 'false', 'labelSources': '0', 'labelSourcesFormat': '$(build.buildNumber)',
        'skipSyncSource': 'false'}.items()}}
DISABLED_OPTIONS = [
    {'enabled': False, 'definition': {'id': '5d58cc01-7c75-450c-be18-a388ddb129ec'},
     'inputs': {'branchFilters': '["+refs/heads/*"]', 'additionalFields': '{}'}},
    {'enabled': False, 'definition': {'id': '57578776-4c22-4526-aeb0-86b6da17ee9c'}, 'inputs': {}},
    {'enabled': False, 'definition': {'id': 'a9db38f9-9fdc-478c-b0f9-464221e58316'},
     'inputs': {'workItemType': 'Issue', 'assignToRequestor': 'true', 'additionalFields': '{}'}}]


def utc_now():
    return datetime.now(timezone.utc)


@contextmanager
def _validation():
    try:
        yield
    except (KeyError, TypeError, ValueError, AttributeError, OSError, OverflowError):
        raise DeliveryRefused('authority') from None


def _digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _open_attempt(directory):
    if any((directory / name).exists() for name in STOP_FILES):
        raise DeliveryRefused('authority')


def _file(directory, binding):
    path = bound_file(directory, binding['path'])
    if sha256_file(path) != binding['sha256']:
        raise DeliveryRefused('authority')
    return path


def _binding(directory, relative):
    path = bound_file(directory, relative)
    return {'path': relative, 'sha256': sha256_file(path)}


def _clock(spec, directory):
    value = read(_file(directory, spec['setup_clock']))
    template = spec['authority_template']
    if any(value[key] != template[key] for key in ('session', 'source_sha')):
        raise DeliveryRefused('authority')
    approval = read(_file(directory, spec['bindings']['approval']))
    if (value['approval_sha256'] != spec['bindings']['approval']['sha256']
            or approval['message_ref'] != template['approval_ref']
            or any(approval[key] != template[key] for key in ('session', 'source_sha', 'tree_sha'))):
        raise DeliveryRefused('authority')
    start = instant(value['S0'])
    if [instant(value[key]) for key in ('ready_or_cleanup_due_at', 'prepaid_closeout_by', 'outer_closeout_by')] != [
            start + timedelta(minutes=n) for n in (60, 120, 330)]:
        raise DeliveryRefused('authority')
    return value


def _normalize(snapshot):
    """Ignore only server-managed top-level audit fields, not operational data."""
    value = copy.deepcopy(snapshot)
    for name in ('definition', *ROLES):
        keys = ('revision', 'createdBy', 'createdOn', 'modifiedBy', 'modifiedOn', '_links', 'url')
        keys += ('createdDate',) if name == 'definition' else ('modificationDate',)
        for key in keys:
            value[name].pop(key, None)
    value['definition'].get('repository', {}).get('properties', {}).pop('lastUpdated', None)
    # Definition GET omits empty collections and false variable attributes.
    value['definition'].setdefault('variables', {})
    value['definition'].setdefault('triggers', [])
    for key in VARIABLES & set(value['definition']['variables']):
        variable = value['definition']['variables'][key]
        variable.setdefault('isSecret', False)
        variable.setdefault('allowOverride', False)
    return value


def _static_amendment(spec, directory, baseline):
    if 'static_amendment' not in spec:
        return
    amendment = read(_file(directory, spec['static_amendment']))
    null_defaults = amendment.get('null_defaults', {})
    if (amendment['kind'] != 'human-approved-diagnostic-static-amendment'
            or amendment['message_ref'] != spec['authority_template']['approval_ref']
            or amendment['baseline_sha256'] != spec['bindings']['baseline']['sha256']
            or amendment['expected_static_sha256'] != _digest(spec['expected_static'])
            or not (amendment['additions'] or null_defaults)):
        raise DeliveryRefused('authority')
    allowed = CHECKOUT_DEFAULTS | {'options': DISABLED_OPTIONS}
    for path, value in amendment['additions'].items():
        if path not in allowed or canonical(value) != canonical(allowed[path]):
            raise DeliveryRefused('checks')
        node = baseline['definition']
        keys = path.split('.')
        for key in keys[:-1]:
            node = node[key]
        if keys[-1] in node:
            raise DeliveryRefused('checks')
        node[keys[-1]] = copy.deepcopy(value)
    # A real definition GET can contain clean:null. Preserve that original and
    # require a distinct, explicitly reviewed null-to-fixed change, never a
    # blanket equivalence between null, absent and configured checkout values.
    for path, value in null_defaults.items():
        if path != 'repository.clean' or value != CHECKOUT_DEFAULTS[path]:
            raise DeliveryRefused('checks')
        repository = baseline['definition']['repository']
        if 'clean' not in repository or repository['clean'] is not None:
            raise DeliveryRefused('checks')
        repository['clean'] = value


def _shape(spec, directory):
    template = spec['authority_template']
    if (spec['kind'] != 'identity-diagnostic-preparation'
            or set(template) != {'kind', 'session', 'source_sha', 'tree_sha', 'pipeline_id',
                                 'approval_ref', 'principals'}
            or set(spec['bindings']) != {'approval', 'identity', 'readers', 'rollback', 'rollback_review', 'baseline'}
            or set(spec['expected_static']) != READBACKS):
        raise DeliveryRefused('authority')
    expected = spec['expected_static']
    definition = _normalize(expected)['definition']
    if (definition['id'] != 2 or definition['queueStatus'] != 'disabled'
            or VARIABLES & set(definition['variables'])
            or not isinstance(definition['triggers'], list)
            or expected['unfinishedBuilds'] != {'count': 0, 'value': []}):
        raise DeliveryRefused('checks')
    # Freeze actual post-edit GET shape, including server-normalized schedules.
    # Only schedules excluding every branch may be staged for this diagnostic.
    for trigger in definition['triggers']:
        if trigger['triggerType'] != 'schedule':
            raise DeliveryRefused('checks')
        for schedule in trigger['schedules']:
            if (schedule['branchFilters'] != ['+refs/heads/' + template['session'] + '-no-schedule', '-refs/heads/*']
                    or schedule['scheduleOnlyWithChanges'] is not True):
                raise DeliveryRefused('checks')
    for role in ROLES:
        principal = template['principals'][role]
        endpoint = expected[role]
        params = endpoint['authorization']['parameters']
        if (endpoint['id'] != principal['service_connection_id']
                or params['serviceprincipalid'] != principal['client_id']
                or params['workloadIdentityFederationIssuer'] != principal['issuer']
                or params['workloadIdentityFederationSubject'] != principal['subject']):
            raise DeliveryRefused('identity')
    baseline = _normalize(read(_file(directory, spec['bindings']['baseline']))['readbacks'])
    if set(baseline) != READBACKS or baseline['definition']['queueStatus'] != 'disabled':
        raise DeliveryRefused('checks')
    _static_amendment(spec, directory, baseline)
    baseline['definition']['triggers'] = copy.deepcopy(definition['triggers'])
    for role in ROLES:
        baseline[role]['authorization']['parameters']['serviceprincipalid'] = template['principals'][role]['client_id']
    if canonical(baseline) != canonical(_normalize(expected)):
        raise DeliveryRefused('checks')


def _observation(directory, relative, expected, now, *, after_save=True):
    """Check a Save readback; staging explicitly requires the unchanged IDs."""
    value = read(bound_file(directory, relative))
    _fresh(value, now)
    actual = value['readbacks']
    left, right = _normalize(actual), _normalize(expected)
    if after_save:
        left['definition'] = saved_definition(left['definition'])
        right['definition'] = saved_definition(right['definition'])
    if set(actual) != READBACKS or canonical(left) != canonical(right):
        raise DeliveryRefused('checks')
    return value


def _fresh(value, now):
    start, end = instant(value.get('started_at', value.get('at'))), instant(value['completed_at'])
    if not start <= end <= now or now - start > READBACK_AGE:
        raise DeliveryRefused('evidence')


def _load(directory):
    _open_attempt(directory)
    staged = read(directory / FOLDER / 'staged.json')
    spec = read(_file(directory, staged['spec']))
    _shape(spec, directory)
    for binding in spec['bindings'].values():
        _file(directory, binding)
    clock = _clock(spec, directory)
    original = read(_file(directory, staged['readbacks']))
    if (instant(original['completed_at']) > instant(staged['at'])
            or canonical(_normalize(original['readbacks'])) != canonical(_normalize(spec['expected_static']))):
        raise DeliveryRefused('evidence')
    verify_source(spec['authority_template'])
    return spec, clock, staged


def stage(directory: Path, spec_path: str, readbacks_path: str):
    with _validation(), operation_lock(directory):
        _open_attempt(directory)
        spec = read(bound_file(directory, spec_path))
        _shape(spec, directory)
        for binding in spec['bindings'].values():
            _file(directory, binding)
        clock = _clock(spec, directory)
        verify_source(spec['authority_template'])
        now = utc_now()
        if not instant(clock['S0']) <= now < instant(clock['ready_or_cleanup_due_at']):
            raise DeliveryRefused('deadline')
        observation = _observation(directory, readbacks_path, spec['expected_static'], now, after_save=False)
        folder = directory / FOLDER
        folder.mkdir(exist_ok=True)
        bindings = {'spec': _binding(directory, spec_path), 'readbacks': _binding(directory, readbacks_path)}
        now = utc_now()
        _fresh(observation, now)
        if now >= instant(clock['ready_or_cleanup_due_at']):
            raise DeliveryRefused('deadline')
        exclusive_json(folder / 'staged.json', {'at': now.isoformat(), **bindings})


def _context(authority, now):
    # Validate the exact same authority schema as the hosted job. This is local
    # validation only; run 1 is not an actual build or authentication receipt.
    require_context(authority, {'BUILD_REASON': 'Manual', 'BUILD_SOURCEBRANCH': 'refs/heads/main',
        'BUILD_SOURCEVERSION': authority['source_sha'], 'P8_PREFLIGHT_SOURCE_SHA': authority['source_sha'],
        'SYSTEM_DEFINITIONID': '2', 'SYSTEM_JOBATTEMPT': '1', 'SYSTEM_STAGEATTEMPT': '1',
        'BUILD_BUILDID': '1', 'P8_PREFLIGHT_SHA256': _digest(authority)}, now)


def draft(directory: Path):
    with _validation(), operation_lock(directory):
        spec, clock, staged = _load(directory)
        now = utc_now()
        _fresh(read(_file(directory, staged['readbacks'])), now)
        expires = min(now + timedelta(minutes=30), instant(clock['ready_or_cleanup_due_at']))
        if now < instant(clock['S0']) or expires - now < WINDOW + JOB_AND_MARGIN:
            raise DeliveryRefused('deadline')
        authority = spec['authority_template'] | {'issued_at': now.isoformat(), 'expires_at': expires.isoformat()}
        _context(authority, now)
        transport = encode_authority(authority)
        expected = copy.deepcopy(spec['expected_static'])
        variables = {
            'P8_PREFLIGHT_JSON': {'value': transport.decode(), 'isSecret': True, 'allowOverride': False},
            'P8_PREFLIGHT_SHA256': {'value': _digest(authority), 'isSecret': False, 'allowOverride': False},
            'P8_PREFLIGHT_SOURCE_SHA': {'value': authority['source_sha'], 'isSecret': False, 'allowOverride': False}}
        expected['definition'].setdefault('variables', {}).update({
            'P8_PREFLIGHT_JSON': {'value': None, 'isSecret': True},
            'P8_PREFLIGHT_SHA256': {'value': _digest(authority)},
            'P8_PREFLIGHT_SOURCE_SHA': {'value': authority['source_sha']}})
        folder = directory / FOLDER
        exclusive_json(folder / 'draft.intent.json', {'at': now.isoformat(), 'staged_sha256': sha256_file(folder / 'staged.json')})
        exclusive_json(folder / 'authority.json', authority)
        exclusive_json(folder / 'variables.private.json', variables)
        exclusive_json(folder / 'configuration.private.json', expected)
        exclusive_json(folder / 'draft.json', {'at': now.isoformat(), 'owner_dispatch_approved': False,
            'bindings': {name: _binding(directory, FOLDER + '/' + name) for name in
                         ('staged.json', 'draft.intent.json', 'authority.json', 'variables.private.json', 'configuration.private.json')}})


def _draft(directory):
    spec, clock, staged = _load(directory)
    value = read(directory / FOLDER / 'draft.json')
    for binding in value['bindings'].values():
        _file(directory, binding)
    authority = read(directory / FOLDER / 'authority.json')
    if any(authority[key] != item for key, item in spec['authority_template'].items()):
        raise DeliveryRefused('authority')
    return spec, clock, staged, authority


def finalize(directory: Path, readbacks_path: str, witness_path: str):
    with _validation(), operation_lock(directory):
        _, clock, _, authority = _draft(directory)
        folder = directory / FOLDER
        expected = read(folder / 'configuration.private.json')
        now = utc_now()
        observed = _observation(directory, readbacks_path, expected, now)
        observed_start = instant(observed.get('started_at', observed.get('at')))
        witness = read(bound_file(directory, witness_path))
        if (witness['definition_id'] != 2
                or witness['revision'] != observed['readbacks']['definition']['revision']
                or witness['variables'] != read(folder / 'variables.private.json')
                or not instant(authority['issued_at']) <= instant(witness['started_at'])
                    <= instant(witness['completed_at']) <= observed_start):
            raise DeliveryRefused('evidence')
        bindings = {name: _binding(directory, path) for name, path in {
            'draft': FOLDER + '/draft.json', 'configured_readbacks': readbacks_path,
            'save_witness': witness_path}.items()}
        draft_hash = sha256_file(folder / 'draft.json')
        now = utc_now()
        _fresh(observed, now)
        _context(authority, now)
        cutoff = min(now + WINDOW, instant(authority['expires_at']) - JOB_AND_MARGIN,
                     instant(clock['ready_or_cleanup_due_at']) - JOB_AND_MARGIN)
        if cutoff - now < REVIEW_RUNWAY:
            raise DeliveryRefused('deadline')
        _open_attempt(directory)
        exclusive_json(folder / 'packet.intent.json', {'at': now.isoformat(), 'draft_sha256': draft_hash})
        packet = {'kind': 'single-fresh-identity-diagnostic-execution-packet',
                  'session': authority['session'], 'source_sha': authority['source_sha'],
                  'tree_sha': authority['tree_sha'], 'at': now.isoformat(),
                  'queue_before': cutoff.isoformat(), 'expires_at': authority['expires_at'],
                  'authority_sha256': _digest(authority), 'job_timeout_minutes': 20,
                  'automatic_retry': False, 'maximum_queues': 1,
                  'enable_max_seconds': 120,
                  'queue': {'pipeline_id': 2, 'mode': 'identity-preflight', 'branch': 'refs/heads/main',
                            'sourceVersion': authority['source_sha'], 'reason': 'Manual'},
                  'bindings': bindings}
        exclusive_json(folder / 'execution-packet.json', packet)
        return packet


def admit(directory: Path, phase: str, readbacks_path: str, review_path: str):
    """Reserve before the owner browser enables/queues. No side effect is executed."""
    with _validation(), operation_lock(directory):
        _, clock, _, authority = _draft(directory)
        folder = directory / FOLDER
        packet = read(folder / 'execution-packet.json')
        for binding in packet['bindings'].values():
            _file(directory, binding)
        packet_hash = sha256_file(folder / 'execution-packet.json')
        review = read(bound_file(directory, review_path))
        if (review['kind'] != 'independent-diagnostic-packet-review' or review['verdict'] != 'PASS'
                or not isinstance(review['reviewer'], str) or not review['reviewer']
                or type(review['critical']) is not int or review['critical'] != 0
                or type(review['important']) is not int or review['important'] != 0
                or review['packet_sha256'] != packet_hash):
            raise DeliveryRefused('evidence')
        if phase not in ('enable', 'queue'):
            raise DeliveryRefused('authority')
        expected = read(folder / 'configuration.private.json')
        if phase == 'queue':
            intent = read(folder / 'dispatch.intent.json')
            if intent['packet_sha256'] != packet_hash or intent['review'] != _binding(directory, review_path):
                raise DeliveryRefused('authority')
            expected['definition']['queueStatus'] = 'enabled'
        bindings = {'review': _binding(directory, review_path), 'readbacks': _binding(directory, readbacks_path)}
        observed = _observation(directory, readbacks_path, expected, utc_now())
        observed_start = instant(observed.get('started_at', observed.get('at')))
        # Re-read wall time after Git, bindings and review checks. Never pass a
        # cached pre-browser timestamp through this boundary.
        now = utc_now()
        _context(authority, now)
        if phase == 'queue' and not timedelta(0) <= now - instant(intent['at']) <= ENABLE_LIMIT:
            raise DeliveryRefused('deadline')
        if (now < instant(packet['at']) or instant(packet['queue_before']) - now < DISPATCH_RUNWAY
                or instant(packet['queue_before']) > min(instant(authority['expires_at']),
                        instant(clock['ready_or_cleanup_due_at'])) - JOB_AND_MARGIN):
            raise DeliveryRefused('deadline')
        if observed_start < instant(packet['at']) or now - observed_start > READBACK_AGE:
            raise DeliveryRefused('evidence')
        _open_attempt(directory)
        exclusive_json(folder / ('dispatch.intent.json' if phase == 'enable' else 'queue.intent.json'), {
            'at': now.isoformat(), 'packet_sha256': packet_hash, **bindings,
            'uncertain_submission_consumes_attempt': True})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('stage', 'draft', 'finalize', 'enable', 'queue'))
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--spec')
    parser.add_argument('--readbacks')
    parser.add_argument('--review')
    parser.add_argument('--save-witness')
    args = parser.parse_args(argv)
    try:
        if args.action == 'stage': stage(args.session, args.spec, args.readbacks)
        elif args.action == 'draft': draft(args.session)
        elif args.action == 'finalize': finalize(args.session, args.readbacks, args.save_witness)
        else: admit(args.session, args.action, args.readbacks, args.review)
        print('status=pass code=diagnostic-preparation')
        return 0
    except Exception:
        print('status=refused code=diagnostic-preparation')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
