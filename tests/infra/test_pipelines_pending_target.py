"""Offline regression cases for the real pending-target verification path.

Execution requires the human's explicit offline-test permission.
All HTTP goes through MockTransport; no CLI, sockets or Azure credentials.
"""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'eval')]
from scripts.pipelines import promotion
from app.delivery_search.contracts import DeliveryRefused
from scripts.pipelines.plans import bind_plan
from scripts.pipelines.storage import BlobStore

SEARCH_ID = '/subscriptions/personal/resourceGroups/owned/providers/Microsoft.Search/searchServices/p8-owned'
STORAGE_ID = '/subscriptions/personal/resourceGroups/owned/providers/Microsoft.Storage/storageAccounts/p8owned'
INITIAL = {
    'version': 4, 'terraform_version': '1.15.8', 'serial': 1,
    'lineage': '00000000-0000-0000-0000-000000000001',
    'outputs': {}, 'resources': [], 'check_results': None,
}


class OfflineToken:
    def token(self):
        return 'offline-fixture-token'


@pytest.fixture
def target(tmp_path):
    files = {}
    for name in ('plan', 'inputs', 'locks'):
        files[name] = tmp_path / name
        files[name].write_bytes(('fixture-' + name).encode())
    bundle = SimpleNamespace(digest='b' * 64, manifest=SimpleNamespace(source_sha='a' * 40))
    authority = {
        'search_host': 'p8-owned.search.windows.net',
        'storage_host': 'p8owned.blob.core.windows.net',
        'scopes': {'service': SEARCH_ID,
                   'inputs': STORAGE_ID + '/blobServices/default/containers/inputs'},
    }
    foundation = {'name': 'foundation', 'resources': [
        {'kind': 'arm', 'id': SEARCH_ID, 'api_version': '2025-05-01'},
        {'kind': 'arm', 'id': STORAGE_ID, 'api_version': '2023-05-01'},
    ]}
    shown = {'resource_changes': [{
        'address': 'azapi_data_plane_resource.index', 'mode': 'managed',
        'type': 'azapi_data_plane_resource', 'change': {
            'actions': ['create'], 'after': {
                'type': 'Microsoft.Search/searchServices/indexes@2024-07-01',
                'parent_id': authority['search_host'], 'name': 'p8-demo-prod',
            }},
    }]}
    config = {'state': copy.deepcopy(INITIAL), 'index_status': 404,
              'search_parent_status': 200, 'storage_parent_status': 200,
              'parent_id_override': None, 'budget_status': 201,
              'state_status': 200, 'state_error': None, 'empty_placeholder': False}
    requests = []

    def transport(request):
        requests.append(request)
        host, path = request.url.host, request.url.path
        if request.method == 'GET' and host == 'p8owned.blob.core.windows.net' and path == '/prod-state/index.tfstate':
            if config['empty_placeholder']:
                return httpx.Response(200, content=b'', headers={'content-length': '0'})
            if config['state'] is None:
                return httpx.Response(404, headers={'x-ms-error-code': 'BlobNotFound'})
            if config['state_status'] != 200:
                return httpx.Response(config['state_status'], headers={'x-ms-error-code': config['state_error'] or 'AuthorizationFailure'})
            return httpx.Response(200, json=config['state'])
        if request.method == 'GET' and host == 'management.azure.com' and path in (SEARCH_ID, STORAGE_ID):
            kind = 'search' if path == SEARCH_ID else 'storage'
            return httpx.Response(config[kind + '_parent_status'],
                                  json={'id': config['parent_id_override'] or path})
        if request.method == 'PUT' and host == 'p8owned.blob.core.windows.net' and path in {
            '/evidence/budgets/metadata/' + f'{i:03}.json' for i in range(120)
        }:
            assert request.headers['if-none-match'] == '*'
            return httpx.Response(config['budget_status'])
        if request.method == 'GET' and host == 'p8-owned.search.windows.net' and path == '/indexes/p8-demo-prod':
            assert request.url.params['api-version'] == '2024-07-01'
            return httpx.Response(config['index_status'], json={'error': {'code': '', 'message': 'Fixture index absence'}})
        raise AssertionError('Unexpected external operation: ' + request.method + ' ' + str(request.url))

    with httpx.Client(transport=httpx.MockTransport(transport), follow_redirects=False) as client:
        store = BlobStore(authority['storage_host'], OfflineToken(), client)
        def binding(state):
            return bind_plan(files['plan'], bundle, 'demo-prod', state, files['inputs'], files['locks'])
        def verify(saved=None):
            return promotion.verify_pending_prod_target(
                saved or binding(INITIAL), files['plan'], shown, bundle,
                files['inputs'], files['locks'], store=store, client=client,
                authority=authority, foundation=foundation,
                owner_tokens=OfflineToken(), search_tokens=OfflineToken())
        yield SimpleNamespace(config=config, requests=requests, files=files,
                              bundle=bundle, authority=authority, shown=shown,
                              foundation=foundation, binding=binding, verify=verify)


def index_reads(target):
    return [r for r in target.requests if r.url.host.endswith('.search.windows.net')]


def test_initialized_serial_one_state_reaches_real_index_absence_check(target):
    result = target.verify()
    assert result['state_kind'] == 'initialized-empty'
    assert result['state_lineage'] == '00000000-0000-0000-0000-000000000001'
    assert result['state_serial'] == 1
    assert result['index']['status'] == 404
    assert len(index_reads(target)) == 1
    assert [r.method for r in target.requests] == ['GET', 'GET', 'GET', 'PUT', 'GET']


def test_authentically_absent_state_must_match_absent_plan_binding(target):
    target.config['state'] = None
    result = target.verify(target.binding(None))
    assert result['state_kind'] == 'absent' and result['state_lineage'] is None
    assert result['state_serial'] == 0 and result['index']['status'] == 404


@pytest.mark.parametrize('change', [
    {'serial': 2}, {'lineage': '00000000-0000-0000-0000-000000000002'},
    {'check_results': []}, {'resources': [{'mode': 'managed', 'type': 'unexpected'}]},
    {'outputs': {'index': {'value': 'already-used'}}}, {'serial': True}, {'version': 3},
])
def test_state_drift_or_used_state_is_refused_before_index_check(target, change):
    saved = target.binding(INITIAL)
    target.config['state'].update(change)
    with pytest.raises(DeliveryRefused):
        target.verify(saved)
    assert not index_reads(target)


def test_absence_cannot_replace_the_bound_initialized_state(target):
    target.config['state'] = None
    with pytest.raises(DeliveryRefused):
        target.verify()
    assert not index_reads(target)


@pytest.mark.parametrize('change', [
    {'version': 4.0}, {'version': True}, {'terraform_version': None},
    {'terraform_version': ''}, {'terraform_version': 'not-a-version'},
    {'lineage': '-' * 36}, {'check_results': []},
])
def test_matching_bound_malformed_state_is_refused(target, change):
    malformed = copy.deepcopy(INITIAL)
    malformed.update(change)
    target.config['state'] = malformed
    # These malformed values still satisfy the old bind_plan shape checks.
    saved = target.binding(malformed)
    with pytest.raises(DeliveryRefused):
        target.verify(saved)
    assert not index_reads(target)


@pytest.mark.parametrize('key', ['version', 'terraform_version', 'check_results', 'resources', 'outputs'])
def test_matching_bound_incomplete_state_is_refused(target, key):
    malformed = copy.deepcopy(INITIAL)
    del malformed[key]
    target.config['state'] = malformed
    with pytest.raises(DeliveryRefused):
        target.verify(target.binding(malformed))
    assert not index_reads(target)


def test_authenticated_zero_byte_placeholder_matches_absent_binding(target):
    target.config['empty_placeholder'] = True
    result = target.verify(target.binding(None))
    assert result['state_kind'] == 'absent'
    assert result['index']['status'] == 404


def test_zero_byte_placeholder_cannot_replace_initialized_binding(target):
    target.config['empty_placeholder'] = True
    with pytest.raises(DeliveryRefused):
        target.verify()
    assert not index_reads(target)


@pytest.mark.parametrize('name', ['plan', 'inputs', 'locks'])
def test_changed_artifact_is_refused_before_index_check(target, name):
    saved = target.binding(INITIAL)
    target.files[name].write_bytes(b'tampered')
    with pytest.raises(DeliveryRefused):
        target.verify(saved)
    assert not index_reads(target)


@pytest.mark.parametrize('status', [200, 401, 403, 429, 500])
def test_existing_index_or_unavailable_absence_is_not_approval(target, status):
    target.config['index_status'] = status
    with pytest.raises(DeliveryRefused):
        target.verify()


@pytest.mark.parametrize('kind', ['search', 'storage'])
@pytest.mark.parametrize('status', [403, 404, 500])
def test_missing_or_unreadable_parent_is_refused(target, kind, status):
    target.config[kind + '_parent_status'] = status
    with pytest.raises(DeliveryRefused):
        target.verify()
    assert not index_reads(target)


def test_wrong_parent_identity_is_refused(target):
    target.config['parent_id_override'] = SEARCH_ID + '-foreign'
    with pytest.raises(DeliveryRefused):
        target.verify()
    assert not index_reads(target)


def test_metadata_reservation_failure_cannot_send_the_index_get(target):
    target.config['budget_status'] = 403
    with pytest.raises(DeliveryRefused):
        target.verify()
    assert not index_reads(target)


def test_all_108_ordinary_metadata_slots_exhausted_preserves_physical_tail(target):
    target.config['budget_status'] = 412
    with pytest.raises(DeliveryRefused):
        target.verify()
    puts = [r for r in target.requests if r.method == 'PUT']
    assert len(puts) == 108
    assert [r.url.path for r in puts] == [
        '/evidence/budgets/metadata/' + f'{i:03}.json' for i in range(108)
    ]
    assert not index_reads(target)


def test_storage_auth_failure_is_not_empty_state(target):
    target.config['state_status'] = 403
    with pytest.raises(DeliveryRefused):
        target.verify()
    assert not index_reads(target)


def test_non_create_plan_is_refused(target):
    target.shown['resource_changes'][0]['change']['actions'] = ['update']
    with pytest.raises(DeliveryRefused):
        target.verify()
    assert not index_reads(target)
