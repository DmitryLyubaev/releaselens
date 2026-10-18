"""Document batching preserves payloads without encoding vectors twice."""
import json
import threading
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from app.delivery_search import client
from app.delivery_search.contracts import DeliveryRefused


def batches(documents):
    function = getattr(client, 'document_batches', None)
    assert callable(function), 'bounded single-encoding batcher is missing'
    return list(function(iter(documents)))


def test_payload_preserves_unicode_escaping_and_numeric_values():
    docs = [{'chunk_id': '1', 'content': 'café "x"\n', 'vector': [0.1, -1e-8]}]
    assert batches(docs) == [(['1'], b'{"value":[{"chunk_id":"1","content":"caf\xc3\xa9 \\"x\\"\\n","vector":[0.1,-1e-08]}]}')]


def test_count_boundary_keeps_each_key_once():
    result = batches([{'chunk_id': str(i)} for i in range(1001)])
    assert [len(keys) for keys, payload in result] == [500, 500, 1]
    assert [key for keys, payload in result for key in keys] == [str(i) for i in range(1001)]
    assert all([d['chunk_id'] for d in json.loads(payload)['value']] == keys for keys, payload in result)


def test_byte_boundary_uses_utf8_and_includes_envelope():
    limit = 12 * 1024 * 1024
    # {"value":[{"chunk_id":"1","content":"..."}]} is 41 bytes without content.
    docs = [{'chunk_id': '1', 'content': 'é' * ((limit - 42) // 2)}]
    result = batches(docs + [{'chunk_id': '2'}])
    assert len(result) == 2
    assert len(result[0][1]) <= limit
    assert json.loads(result[0][1])['value'] == docs


def test_oversized_document_refuses():
    with pytest.raises(DeliveryRefused, match='upload'):
        batches([{'chunk_id': '1', 'content': 'x' * (12 * 1024 * 1024)}])


def test_empty_input_produces_no_request():
    assert batches([]) == []


def test_upload_deadline_is_checked_after_last_successful_response(monkeypatch, tmp_path):
    # Exercise the full uploader with cheap valid document structure; input validation
    # has its own complete frozen-bundle tests and is outside this timer boundary.
    ids = list(range(41825))
    chunk = SimpleNamespace(artefact='a', content='text')
    monkeypatch.setattr(client, 'validate_inputs', lambda root: None)
    monkeypatch.setattr(client, 'load_chunks', lambda path: [SimpleNamespace(
        chunk_id=i, artefact=chunk.artefact, content=chunk.content) for i in ids])
    monkeypatch.setattr(client, 'read_json', lambda path: ids)
    monkeypatch.setattr(client.np, 'load', lambda *a, **kw: np.zeros((41825, 1)))
    now, posts, counts = [0.0], [], []
    monkeypatch.setattr(client.time, 'monotonic', lambda: now[0])
    def server(request):
        if request.method == 'GET':
            counts.append(request)
            return httpx.Response(200, text='41825')
        docs = json.loads(request.content)['value']
        posts.extend(d['chunk_id'] for d in docs)
        if len(posts) == 41825:
            now[0] = 240.0
        return httpx.Response(200, json={'value': [dict(key=d['chunk_id'], status=True,
            statusCode=201) for d in docs]})
    with httpx.Client(transport=httpx.MockTransport(server)) as transport:
        with pytest.raises(DeliveryRefused) as caught:
            client.upload_bundle(SimpleNamespace(root=tmp_path, digest='a'*64),
                client.SearchTarget('https://owned.search.windows.net', 'p8-test', 'owned.search.windows.net'),
                SimpleNamespace(token=lambda: 'synthetic'), transport, reservation=tmp_path/'reservation.json')
    assert caught.value.check == 'upload-deadline'
    assert len(posts) == len(set(posts)) == 41825
    assert counts == []


def test_encoding_next_batch_overlaps_one_request_without_parallel_posts(monkeypatch, tmp_path):
    ids = list(range(41825))
    monkeypatch.setattr(client, 'validate_inputs', lambda root: None)
    monkeypatch.setattr(client, 'load_chunks', lambda path: [SimpleNamespace(
        chunk_id=i, artefact='a', content='text') for i in ids])
    monkeypatch.setattr(client, 'read_json', lambda path: ids)
    monkeypatch.setattr(client.np, 'load', lambda *a, **kw: np.zeros((41825, 1)))
    request_started, next_encoded = threading.Event(), threading.Event()
    original = client.canonical
    def encode(document):
        if document.get('chunk_id') == '501':
            assert request_started.wait(2), 'first request must start while next batch is encoded'
            next_encoded.set()
        return original(document)
    monkeypatch.setattr(client, 'canonical', encode)
    uploaded, active = [], [0]
    def server(request):
        if request.method == 'GET':
            return httpx.Response(200, text='41825')
        active[0] += 1
        assert active[0] == 1, 'only one HTTP upload may be in flight'
        if not request_started.is_set():
            request_started.set()
            assert next_encoded.wait(2), 'JSON encoding must overlap the pending response'
        docs = json.loads(request.content)['value']
        uploaded.extend(d['chunk_id'] for d in docs)
        active[0] -= 1
        return httpx.Response(200, json={'value': [dict(key=d['chunk_id'], status=True,
            statusCode=201) for d in docs]})
    with httpx.Client(transport=httpx.MockTransport(server)) as transport:
        assert client.upload_bundle(SimpleNamespace(root=tmp_path, digest='a'*64),
            client.SearchTarget('https://owned.search.windows.net', 'p8-test', 'owned.search.windows.net'),
            SimpleNamespace(token=lambda: 'synthetic'), transport,
            reservation=tmp_path/'reservation.json') == 41825
    assert uploaded == [str(i) for i in ids]
