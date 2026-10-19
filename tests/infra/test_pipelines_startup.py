"""Keeper admission must bind the actual interpreter, including Windows venv launchers."""
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest


@pytest.fixture
def startup(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / 'eval'))
    monkeypatch.syspath_prepend(str(root))
    return importlib.import_module('scripts.pipelines.startup')


def inputs(*, wrapper=True):
    launcher = {'pid': 101, 'created': '2026-01-01T00:00:00+00:00',
                'command_line': 'python.exe keeper.py run', 'sha256': 'a' * 64}
    keeper = dict(launcher, pid=102, created='2026-01-01T00:00:00.040000+00:00') if wrapper else dict(launcher)
    launch = {'process': launcher, 'setup_clock_sha256': 'b' * 64}
    owned = {'session': 'p8-fixture', 'source_sha': 'c' * 40,
             'process': keeper, 'setup_clock_sha256': 'b' * 64}
    heartbeat = {'pid': keeper['pid'], 'session': 'p8-fixture', 'source_sha': 'c' * 40,
                 'setup_clock_sha256': 'b' * 64, 'at': '2026-01-01T00:00:02+00:00'}
    snapshots = {101: dict(launcher, parent_pid=77)}
    if wrapper:
        snapshots[102] = dict(keeper, parent_pid=101)
    return launch, owned, heartbeat, snapshots


def admit(startup, launch, owned, heartbeat, snapshots, now=None):
    return startup.validate_startup(launch, owned, heartbeat,
        clock_sha256='b' * 64, session='p8-fixture', source_sha='c' * 40,
        snapshots=snapshots, now=now or datetime(2026, 1, 1, 0, 0, 3, tzinfo=timezone.utc))


def test_verified_launcher_child_is_admitted_as_the_keeper(startup):
    result = admit(startup, *inputs())
    assert result['launcher_pid'] == 101
    assert result['keeper_pid'] == 102
    assert result['launcher_is_wrapper'] is True


def test_direct_interpreter_launch_remains_admitted(startup):
    result = admit(startup, *inputs(wrapper=False))
    assert result['launcher_pid'] == result['keeper_pid'] == 101
    assert result['launcher_is_wrapper'] is False


@pytest.mark.parametrize('change', ['parent', 'missing-launcher', 'missing-keeper',
    'reused-launcher', 'reused-keeper', 'command', 'helper-hash', 'child-before-parent'])
def test_unrelated_or_changed_process_chain_is_refused(startup, change):
    launch, owned, heartbeat, snapshots = inputs()
    if change == 'parent': snapshots[102]['parent_pid'] = 999
    elif change == 'missing-launcher': snapshots.pop(101)
    elif change == 'missing-keeper': snapshots.pop(102)
    elif change == 'reused-launcher': snapshots[101]['created'] = '2026-01-01T00:00:01+00:00'
    elif change == 'reused-keeper': snapshots[102]['created'] = '2026-01-01T00:00:01+00:00'
    elif change == 'command': snapshots[102]['command_line'] = 'python.exe unrelated.py run'
    elif change == 'helper-hash': snapshots[102]['sha256'] = 'd' * 64
    else:
        owned['process']['created'] = snapshots[102]['created'] = '2025-12-31T23:59:59+00:00'
    with pytest.raises(startup.StartupRefused):
        admit(startup, launch, owned, heartbeat, snapshots)


@pytest.mark.parametrize('change', ['heartbeat-pid', 'session', 'source', 'clock',
    'owned-session', 'owned-source', 'owned-clock', 'launch-clock', 'stale', 'boundary', 'future', 'naive', 'bool-pid'])
def test_wrong_binding_or_nonfresh_heartbeat_is_refused(startup, change):
    launch, owned, heartbeat, snapshots = inputs(wrapper=False)
    if change == 'heartbeat-pid': heartbeat['pid'] = 999
    elif change == 'session': heartbeat['session'] = 'another-session'
    elif change == 'source': heartbeat['source_sha'] = 'd' * 40
    elif change == 'clock': heartbeat['setup_clock_sha256'] = 'd' * 64
    elif change == 'owned-session': owned['session'] = 'another-session'
    elif change == 'owned-source': owned['source_sha'] = 'd' * 40
    elif change == 'owned-clock': owned['setup_clock_sha256'] = 'd' * 64
    elif change == 'launch-clock': launch['setup_clock_sha256'] = 'd' * 64
    elif change == 'stale': heartbeat['at'] = '2025-12-31T23:59:00+00:00'
    elif change == 'boundary': heartbeat['at'] = '2025-12-31T23:59:23+00:00'
    elif change == 'future': heartbeat['at'] = '2026-01-01T00:00:04+00:00'
    elif change == 'naive': heartbeat['at'] = '2026-01-01T00:00:02'
    else: owned['process']['pid'] = heartbeat['pid'] = True
    with pytest.raises(startup.StartupRefused):
        admit(startup, launch, owned, heartbeat, snapshots)


@pytest.mark.parametrize('field', ['pid', 'created', 'command_line', 'sha256'])
def test_incomplete_recorded_identity_is_refused(startup, field):
    launch, owned, heartbeat, snapshots = inputs(wrapper=False)
    owned['process'].pop(field)
    with pytest.raises(startup.StartupRefused):
        admit(startup, launch, owned, heartbeat, snapshots)


def test_slow_native_query_cannot_pass_an_expired_heartbeat(startup, monkeypatch):
    launch, owned, heartbeat, snapshots = inputs(wrapper=False)
    monkeypatch.setattr(startup, 'process_snapshots', lambda pids: snapshots)
    result_time = datetime(2026, 1, 1, 0, 0, 43, tzinfo=timezone.utc)
    monkeypatch.setattr(startup, 'utc_now', lambda: result_time)
    with pytest.raises(startup.StartupRefused):
        startup.observe_startup(launch, owned, heartbeat,
            clock_sha256='b' * 64, session='p8-fixture', source_sha='c' * 40)


def test_refused_admission_writes_no_binding_and_success_cannot_overwrite(startup, monkeypatch, tmp_path):
    launch, owned, heartbeat, snapshots = inputs()
    for name, value in [('launch', launch), ('owned', owned), ('heartbeat', heartbeat)]:
        (tmp_path / ('setup-keeper-' + name + '.json')).write_text(json.dumps(value))
    monkeypatch.setattr(startup, 'process_snapshots', lambda pids: snapshots)
    monkeypatch.setattr(startup, 'utc_now', lambda: datetime(2026, 1, 1, 0, 0, 3, tzinfo=timezone.utc))
    args = {'clock_sha256': 'b' * 64, 'session': 'p8-fixture', 'source_sha': 'c' * 40}
    with pytest.raises(startup.StartupRefused):
        startup.record_startup(tmp_path, **dict(args, session='wrong-session'))
    target = tmp_path / 'startup-process-binding.json'
    assert not target.exists()
    proof = startup.record_startup(tmp_path, **args)
    original = target.read_bytes()
    assert json.loads(original) == proof
    with pytest.raises(FileExistsError):
        startup.record_startup(tmp_path, **args)
    assert target.read_bytes() == original


@pytest.mark.skipif(os.name != 'nt', reason='Real Windows venv launcher regression')
def test_real_windows_self_launch_admits_and_exits(tmp_path):
    root = Path(__file__).resolve().parents[2]
    nonce = uuid.uuid4().hex
    env = dict(os.environ, PYTHONPATH=str(root), KEEPER_STARTUP_PROBE_DIRECTORY=str(tmp_path),
               KEEPER_STARTUP_PROBE_NONCE=nonce)
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('fixtures') /
                             'keeper_startup_probe.py'), 'start'],
                            env=env, capture_output=True, text=True, timeout=110)
    child_log = tmp_path / 'child.log'
    assert result.returncode == 0, result.stdout + result.stderr + (child_log.read_text() if child_log.exists() else '')
    proof = json.loads((tmp_path / 'startup-process-binding.json').read_text())
    assert proof['session'] == 'offline-startup-probe-' + nonce
    # This test must be run using a real venv, locally and in Windows CI.
    assert proof['launcher_is_wrapper'] is True
    assert proof['launcher_pid'] != proof['keeper_pid']
    assert proof['keeper']['parent_pid'] == proof['launcher_pid']
    assert proof['launcher']['executable_path'] != proof['keeper']['executable_path']
    assert json.loads((tmp_path / 'probe-exit.json').read_text()) == {
        'offline_only': True, 'nonce': nonce, 'exit_code': 0, 'active_owned': [],
        'authority_created': False, 'clock_started': False}
