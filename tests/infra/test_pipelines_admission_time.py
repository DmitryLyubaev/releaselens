"""Slow external inspection must not extend an admission deadline."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import importlib

import pytest

from test_pipelines_session import authority


@pytest.fixture
def modules(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root / 'eval'))
    return tuple(importlib.import_module('scripts.pipelines.' + n)
                 for n in ('session', 'startup', 'evidence'))


@pytest.mark.parametrize('boundary', ['source', 'grade', 'lock'])
def test_dispatch_rechecks_wall_clock_after_slow_validation(modules, monkeypatch, tmp_path, boundary):
    session, _, evidence = modules
    raw = authority()
    a = session.SessionAuthority(**raw)
    cutoff = datetime.fromisoformat(a.queue_cutoff_at)
    clock = [cutoff - timedelta(seconds=1)]
    monkeypatch.setattr(session, 'utc_now', lambda: clock[0], raising=False)
    monkeypatch.setattr(session, 'load', lambda path: (a, {}, {}))

    def source(_):
        if boundary == 'source': clock[0] = cutoff
    def grade(_):
        if boundary == 'grade': clock[0] = cutoff
        return {'P8-4': {'status': 'PASS'}, 'P8-5': {'status': 'PASS'}}
    original_lock = session.operation_lock
    @contextmanager
    def lock(path):
        with original_lock(path):
            if boundary == 'lock': clock[0] = cutoff
            yield
    monkeypatch.setattr(session, 'verify_source', source)
    monkeypatch.setattr(evidence, 'grade', grade)
    monkeypatch.setattr(session, 'operation_lock', lock)
    with pytest.raises(session.DeliveryRefused):
        session.reserve_dispatch(tmp_path, 'negative', cutoff - timedelta(seconds=1))
    assert not list((tmp_path / 'intents').glob('*.json'))


def test_dispatch_rechecks_stop_created_during_grading(modules, monkeypatch, tmp_path):
    session, _, evidence = modules
    raw = authority(); a = session.SessionAuthority(**raw)
    now = datetime.fromisoformat(raw['apply_started_at']) + timedelta(minutes=20)
    monkeypatch.setattr(session, 'utc_now', lambda: now, raising=False)
    monkeypatch.setattr(session, 'load', lambda path: (a, {}, {}))
    monkeypatch.setattr(session, 'verify_source', lambda _: None)
    def grade(_):
        (tmp_path / 'owner-stop-authority.json').write_text('{}')
        return {'P8-4': {'status': 'PASS'}, 'P8-5': {'status': 'PASS'}}
    monkeypatch.setattr(evidence, 'grade', grade)
    with pytest.raises(session.DeliveryRefused): session.reserve_dispatch(tmp_path, 'negative', now)
    assert not list((tmp_path / 'intents').glob('*.json'))


@pytest.mark.parametrize('delay,changed,allowed', [(0, False, True), (6, False, False),
                                                 (0, True, False), (40, False, False)])
def test_keeper_freshness_is_checked_after_native_inspection(modules, monkeypatch, delay, changed, allowed):
    _, startup, _ = modules
    clock = [datetime(2026, 10, 20, 0, tzinfo=timezone.utc)]
    at = clock[0] - timedelta(seconds=35)
    owned = {'pid': 123, 'created': (at - timedelta(minutes=1)).isoformat(),
             'command_line': 'python owned-keeper.py', 'sha256': 'a' * 64}
    heartbeat = {'pid': 123, 'session': 'p8-offline', 'source_sha': 'b' * 40, 'at': at.isoformat()}
    monkeypatch.setattr(startup, 'utc_now', lambda: clock[0])
    def snapshot(pid):
        assert pid == 123
        clock[0] += timedelta(seconds=delay)
        return dict(owned, command_line='different process') if changed else dict(owned)
    def observe():
        return startup.observe_keeper(owned, lambda: dict(heartbeat), snapshot,
                                      session='p8-offline', source_sha='b' * 40)
    if allowed:
        proof = observe()
        assert proof['checked_at'] == clock[0].isoformat() and proof['heartbeat'] == heartbeat
    else:
        with pytest.raises(startup.StartupRefused): observe()


def test_keeper_reads_latest_heartbeat_after_process_query(modules, monkeypatch):
    _, startup, _ = modules
    now = datetime(2026, 10, 20, 0, tzinfo=timezone.utc)
    owned = {'pid': 123, 'created': (now - timedelta(minutes=2)).isoformat(),
             'command_line': 'python keeper.py', 'sha256': 'a' * 64}
    queried = []
    monkeypatch.setattr(startup, 'utc_now', lambda: now)
    def snapshot(pid): queried.append(pid); return dict(owned)
    def heartbeat():
        assert queried == [123]
        return {'pid': 123, 'session': 'p8-offline', 'source_sha': 'b' * 40, 'at': now.isoformat()}
    assert startup.observe_keeper(owned, heartbeat, snapshot, session='p8-offline',
                                  source_sha='b' * 40)['checked_at'] == now.isoformat()


def test_final_browser_handoff_rechecks_time_after_source_validation(modules, monkeypatch, tmp_path):
    session, _, _ = modules
    raw = authority(); a = session.SessionAuthority(**raw)
    clock = [datetime.fromisoformat(raw['apply_started_at']) + timedelta(minutes=20)]
    monkeypatch.setattr(session, 'utc_now', lambda: clock[0])
    monkeypatch.setattr(session, 'load', lambda _: (a, {}, {}))
    monkeypatch.setattr(session, 'verify_source', lambda _: None)
    session.reserve_dispatch(tmp_path, 'release', clock[0])
    original = (tmp_path / 'intents/dispatch-release.json').read_bytes()
    session.require_dispatch_current(tmp_path, 'release')
    def slow(_): clock[0] = datetime.fromisoformat(a.queue_cutoff_at)
    monkeypatch.setattr(session, 'verify_source', slow)
    with pytest.raises(session.DeliveryRefused): session.require_dispatch_current(tmp_path, 'release')
    assert (tmp_path / 'intents/dispatch-release.json').read_bytes() == original
