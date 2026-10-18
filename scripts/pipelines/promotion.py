"""Verify pending production against the saved plan and authenticated target reads.

This module grants no execution or approval authority and has no command entrypoint.
"""
from datetime import datetime, timezone
import hashlib
import re

def require_run_identity(original,build_id):
    assert original['run_id']==build_id

def require_pinned_approval(run,expected):
    ref=run['checkConfigurationRef']
    assert ref['id']==expected['id'] and ref['type']['id']==expected['type_id'] and ref['version']==expected['version']
    assert all(str(ref['resource'][key])==str(expected['resource'][key]) for key in ['id','type'])

def verify_pending_prod_target(binding, plan, shown, bundle, inputs, locks, *,
                               store, client, authority, foundation,
                               owner_tokens, search_tokens):
    """Verify exact initial state and a separate authenticated index 404.

    Terraform init can create a real empty state. Its canonical content, lineage
    and serial must remain bound to the saved plan; never substitute None.
    Cleanup's backend-absence predicate has different semantics and is not used.
    """
    from app.delivery_search.contracts import DeliveryRefused, SearchTarget
    from scripts.pipelines.plans import validate_index_plan, verify_plan
    from scripts.pipelines.storage import METADATA_OPERATIONS

    target = SearchTarget('https://' + authority['search_host'],
                          'p8-demo-prod', authority['search_host'])
    if store.host != authority['storage_host'] or foundation['name'] != 'foundation':
        raise DeliveryRefused('target')
    state = store.state('demo-prod')
    if state is not None:
        if (not isinstance(state, dict)
            or type(state.get('version')) is not int or state['version'] != 4
            or not isinstance(state.get('terraform_version'), str)
            or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?', state['terraform_version'])
            or type(state.get('serial')) is not int or state['serial'] < 0
            or not isinstance(state.get('lineage'), str)
            or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', state['lineage'])
            or state.get('resources') != [] or state.get('outputs') != {}
            or 'check_results' not in state or state['check_results'] is not None):
            raise DeliveryRefused('plan')
    validate_index_plan(shown, target.allowed_host, 'demo-prod')
    verify_plan(binding, plan, bundle, 'demo-prod', state, inputs, locks)

    # A missing Search service is not a healthy target ready for promotion.
    # Preserve both owned parent reads from the previous absence-check path.
    storage_id = authority['scopes']['inputs'].split('/blobServices/')[0]
    parents = []
    for kind, resource_id, host in [
        ('search', authority['scopes']['service'], authority['search_host']),
        ('storage', storage_id, authority['storage_host']),
    ]:
        suffix = '.search.windows.net' if kind == 'search' else '.blob.core.windows.net'
        if resource_id.rsplit('/', 1)[-1].lower() + suffix != host:
            raise DeliveryRefused('target')
        matches = [r for r in foundation['resources'] if r.get('kind') == 'arm'
                   and str(r.get('id', '')).lower() == resource_id.lower()]
        if len(matches) != 1:
            raise DeliveryRefused('target')
        response = client.get('https://management.azure.com' + resource_id,
                              params={'api-version': matches[0]['api_version']},
                              headers={'Authorization': 'Bearer ' + owner_tokens.token()})
        if response.status_code != 200:
            raise DeliveryRefused('plan')
        actual_id = response.json().get('id')
        if not isinstance(actual_id, str) or actual_id.lower() != resource_id.lower():
            raise DeliveryRefused('plan')
        parents.append({'kind': kind, 'id': resource_id, 'status': 200,
                        'at': datetime.now(timezone.utc).isoformat(),
                        'body_sha256': hashlib.sha256(response.content).hexdigest()})

    claim = store.reserve_budget('metadata', METADATA_OPERATIONS)
    response = client.get(target.endpoint + '/indexes/' + target.index,
                          params={'api-version': '2024-07-01'},
                          headers={'Authorization': 'Bearer ' + search_tokens.token()})
    if response.status_code != 404:
        raise DeliveryRefused('plan')
    return {'state_kind': 'absent' if state is None else 'initialized-empty',
            'state_lineage': binding.state_lineage, 'state_serial': binding.state_serial,
            'state_sha256': binding.state_sha256, 'parents': parents,
            'index': {'host': target.allowed_host, 'name': target.index, 'status': 404,
                      'at': datetime.now(timezone.utc).isoformat(),
                      'body_sha256': hashlib.sha256(response.content).hexdigest(),
                      'metadata_budget_claim': claim}}
