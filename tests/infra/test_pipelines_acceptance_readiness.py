"""Offline regressions for workload completion and shared metadata limits."""
import importlib
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import httpx
import pytest


@pytest.fixture
def modules(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / 'eval'))
    return SimpleNamespace(**{name: importlib.import_module('scripts.pipelines.' + name)
                             for name in ('storage', 'evidence', 'quiescence', 'cleanup')})


def test_budget_cursor_does_not_rescan_occupied_prefix(modules):
    requests = []
    def server(request):
        requests.append(request.url.path)
        slot = int(request.url.path.rsplit('/', 1)[1][:3])
        return httpx.Response(412 if slot < 50 else 201)
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        store = modules.storage.BlobStore('owned.blob.core.windows.net',
                                         SimpleNamespace(token=lambda: 'offline'), client)
        claims = [store.reserve_budget('metadata', 108) for _ in range(43)]
    assert claims[0].endswith('/050.json') and claims[-1].endswith('/092.json')
    assert len(requests) == 93  # Fifty old slots, then one PUT per new claim.


def test_ordinary_budget_cannot_consume_physical_observation_tail(modules):
    paths = []
    def server(request):
        paths.append(request.url.path)
        slot = int(request.url.path.rsplit('/', 1)[1][:3])
        return httpx.Response(412 if slot < 108 else 201)
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        store = modules.storage.BlobStore('owned.blob.core.windows.net',
                                         SimpleNamespace(token=lambda: 'offline'), client)
        with pytest.raises(modules.storage.DeliveryRefused):
            store.reserve_budget('metadata', 108)
        claim = store.reserve_budget('metadata', 120, start=108)
    assert claim == 'budgets/metadata/108.json'
    assert paths[-2:] == ['/evidence/budgets/metadata/107.json',
                          '/evidence/budgets/metadata/108.json']


def test_legacy_120_caller_is_capped_and_all_twelve_physical_claims_remain(modules):
    blobs = set()
    def server(request):
        path = request.url.path
        if path in blobs:
            return httpx.Response(412)
        blobs.add(path)
        return httpx.Response(201)
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        store = modules.storage.BlobStore('owned.blob.core.windows.net',
                                         SimpleNamespace(token=lambda: 'offline'), client)
        for _ in range(108):
            store.reserve_budget('metadata', 120)
        with pytest.raises(modules.storage.DeliveryRefused):
            store.reserve_budget('metadata', 120)
        claims = [store.reserve_budget('metadata', 120, start=108) for _ in range(12)]
        with pytest.raises(modules.storage.DeliveryRefused):
            store.reserve_budget('metadata', 120, start=108)
        restarted = modules.storage.BlobStore('owned.blob.core.windows.net', store.tokens, client)
        with pytest.raises(modules.storage.DeliveryRefused):
            restarted.reserve_budget('metadata', 120)
        with pytest.raises(modules.storage.DeliveryRefused):
            restarted.reserve_budget('metadata', 120, start=108)
    assert len(blobs) == 120 and claims[-1] == 'budgets/metadata/119.json'


@pytest.mark.parametrize('maximum,start', [(121, 0), (120, 109), (119, 108)])
def test_metadata_range_cannot_be_widened(modules, maximum, start):
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail('invalid range sent HTTP'))) as client:
        store = modules.storage.BlobStore('owned.blob.core.windows.net',
                                         SimpleNamespace(token=lambda: 'offline'), client)
        with pytest.raises(modules.storage.DeliveryRefused):
            store.reserve_budget('metadata', maximum, start=start)


@pytest.mark.parametrize('missing', ['P8-' + str(i) for i in range(1, 7)])
@pytest.mark.parametrize('status', [None, 'FAIL', 'NOT RUN'])
def test_incomplete_workload_cannot_authorize_early_restoration(modules, monkeypatch, tmp_path, missing, status):
    grades = {'P8-' + str(i): {'status': 'PASS'} for i in range(1, 7)}
    if status is None:
        del grades[missing]
    else:
        grades[missing]['status'] = status
    monkeypatch.setattr(modules.evidence, 'grade', lambda path: grades)
    assert modules.evidence.workload_complete(tmp_path) is False


def test_completed_workload_can_request_restoration_before_final_p8_7(modules, monkeypatch, tmp_path):
    grades = {'P8-' + str(i): {'status': 'PASS'} for i in range(1, 7)}
    grades['P8-7'] = {'status': 'NOT RUN'}
    a = SimpleNamespace(release_digest='b' * 64, source_sha='a' * 40)
    monkeypatch.setattr(modules.evidence, 'grade', lambda path: grades)
    monkeypatch.setattr(modules.evidence, 'load', lambda path: (a, {}, {}))
    monkeypatch.setattr(modules.evidence, 'Originals', lambda *args: SimpleNamespace(path=lambda key: tmp_path / key))
    monkeypatch.setattr(modules.evidence, 'verify_bundle', lambda *args: SimpleNamespace(manifest=SimpleNamespace(source_sha=a.source_sha)))
    calls = []
    monkeypatch.setattr(modules.evidence, 'scheduled_workload', lambda *args: calls.append('scheduled-originals'))
    assert modules.evidence.workload_complete(tmp_path) is True
    assert calls == ['scheduled-originals'] and grades['P8-7']['status'] == 'NOT RUN'
    def refuse(*args):
        raise modules.evidence.DeliveryRefused('gate')
    monkeypatch.setattr(modules.evidence, 'scheduled_workload', refuse)
    assert modules.evidence.workload_complete(tmp_path) is False


def test_early_request_uses_workload_readiness_without_claiming_final_success(modules, monkeypatch, tmp_path):
    a = SimpleNamespace(queue_cutoff_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                        source_sha='a' * 40, session='offline')
    monkeypatch.setattr(modules.quiescence, 'load', lambda path: (a, {}, {}))
    monkeypatch.setattr(modules.quiescence, 'verify_source', lambda authority: None)
    monkeypatch.setattr(modules.evidence, 'workload_complete', lambda path: True)
    monkeypatch.setattr(modules.quiescence, 'request', lambda *args, **kwargs: {'request': 'restoration'})
    assert modules.quiescence.request_closeout(tmp_path, datetime.now(timezone.utc)) == {'request': 'restoration'}


@pytest.mark.parametrize('complete,acknowledged', [(False, False), (True, False), (True, True)])
def test_early_cleanup_shares_workload_predicate_and_still_requires_restoration(modules, monkeypatch, tmp_path, complete, acknowledged):
    import json
    a = SimpleNamespace(apply_started_at='offline', source_sha='a' * 40,
                        session='offline', manifest_sha256='c' * 64,
                        cleanup_due=lambda now: False)
    retained = {'resources': [], 'roleAssignments': [], 'deletedVaults': []}
    (tmp_path / 'baseline.json').write_text(json.dumps({'retained': retained}))
    monkeypatch.setattr(modules.cleanup, 'load', lambda path: (a, {}, {'components': []}))
    monkeypatch.setattr(modules.evidence, 'workload_complete', lambda path: complete)
    monkeypatch.setattr(modules.cleanup, 'quiescence_before', lambda *args: acknowledged)
    driver = SimpleNamespace(active_owned=lambda: [], verify_owner=lambda: None,
                             retained=lambda: retained, canonical_absence=lambda: True)
    result = modules.cleanup.execute(tmp_path, datetime.now(timezone.utc), driver=driver)
    assert result['physical_pass'] is (complete and acknowledged)
    assert bool(list((tmp_path / 'intents').glob('cleanup-*.json'))) is (complete and acknowledged)
