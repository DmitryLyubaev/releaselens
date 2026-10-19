from test_pipelines_metadata_policy import POLICY
"""Offline cases for complete ordered fingerprints and admission."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest

sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[2] / 'eval')]
from scripts.pipelines.fingerprint import (fingerprint_capacity, page_parameters, verified_documents,
                                      fingerprint_get, capture_search_originals)


@pytest.mark.parametrize('phase,used,allowed', [
    ('before', 20, True), ('before', 21, False),
    ('after', 64, True), ('after', 65, False),
])
def test_admission_keeps_physical_tail_and_remaining_minimum_gate_reads(phase, used, allowed):
    if allowed:
        fingerprint_capacity(used, phase)
    else:
        with pytest.raises(ValueError):
            fingerprint_capacity(used, phase)


@pytest.fixture
def complete_pages():
    ids = sorted(str(i) for i in range(41825))
    rows = [{'chunk_id': i, 'artefact': 'fixture', 'content': 'fixture'} for i in ids]
    pages = [{'value': rows[i:i+1000]} for i in range(0, len(rows), 1000)]
    pages[0]['@odata.count'] = 41825
    return pages, set(ids)


def test_pages_request_the_unique_sortable_key_and_cover_all_ids(complete_pages):
    pages, expected = complete_pages
    for page in range(42):
        params = page_parameters(page)
        assert params['$orderby'] == 'chunk_id asc'
        assert params['$skip'] == str(page * 1000)
    rows = verified_documents(pages, expected)
    assert len(rows) == 41825
    assert [row['chunk_id'] for row in rows] == [str(i) for i in range(41825)]


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'foreign', 'wrong-count', 'unordered', 'continuation'])
def test_incomplete_or_unstable_fingerprint_is_refused(complete_pages, change):
    pages, expected = copy.deepcopy(complete_pages)
    if change == 'missing':
        pages[0]['value'].pop()
    elif change == 'duplicate':
        pages[0]['value'][1] = pages[0]['value'][0]
    elif change == 'foreign':
        pages[0]['value'][0]['chunk_id'] = 'foreign'
    elif change == 'wrong-count':
        pages[0]['@odata.count'] = 41826
    elif change == 'unordered':
        pages[0]['value'][0], pages[0]['value'][1] = pages[0]['value'][1], pages[0]['value'][0]
    else:
        pages[-1]['@odata.nextLink'] = 'unexpected'
    with pytest.raises(ValueError):
        verified_documents(pages, expected)


@pytest.mark.parametrize('sortable', [True, False])
def test_real_transport_reserves_before_each_of_43_reads_and_checks_schema(complete_pages, tmp_path, sortable):
    from scripts.pipelines.storage import BlobStore
    pages, expected = complete_pages
    schema = {'name': 'p8-demo-prod', 'fields': [
        {'name': 'chunk_id', 'key': True, 'sortable': sortable, 'retrievable': True},
        {'name': 'artefact', 'retrievable': True},
        {'name': 'content', 'retrievable': True},
        {'name': 'vector', 'retrievable': False},
    ]}
    requests = []
    def transport(request):
        requests.append(request)
        if request.url.path.endswith('/operations/metadata-workload-stopped.json'):
            return httpx.Response(404, headers={'x-ms-error-code':'BlobNotFound'})
        if request.url.host == 'owned.blob.core.windows.net':
            assert request.method == 'PUT' and request.headers['if-none-match'] == '*'
            return httpx.Response(201)
        assert request.method == 'GET'
        assert len(requests) >= 2 and requests[-2].method == 'PUT'
        assert requests[-2].url.host == 'owned.blob.core.windows.net'
        if request.url.path.endswith('/docs'):
            assert request.url.params['$orderby'] == 'chunk_id asc'
            return httpx.Response(200, json=pages[int(request.url.params['$skip']) // 1000])
        return httpx.Response(200, json=schema)
    captured = []
    tokens = SimpleNamespace(token=lambda: 'offline-fixture')
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        store = BlobStore('owned.blob.core.windows.net', tokens, client, metadata_policy=POLICY)
        def get(suffix, params, label):
            return fingerprint_get(client, store, tokens, 'owned.search.windows.net',
                                   suffix, params, tmp_path, label, captured)
        if sortable:
            _, docs = capture_search_originals(get, expected)
            assert len(docs) == 41825 and len(captured) == 43
            assert len(requests) == 129
        else:
            with pytest.raises(ValueError):
                capture_search_originals(get, expected)
            assert len(captured) == 1 and len(requests) == 3
