"""Offline Windows self-launch probe. Uses only a caller-owned temporary directory."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from scripts.pipelines.startup import process_snapshots, record_startup


DIRECTORY = Path(os.environ['KEEPER_STARTUP_PROBE_DIRECTORY'])
NONCE = os.environ['KEEPER_STARTUP_PROBE_NONCE']
CLOCK_HASH = 'b' * 64
SOURCE = 'c' * 40
SESSION = 'offline-startup-probe-' + NONCE


def write(name, value):
    with (DIRECTORY / name).open('x', encoding='utf-8') as stream:
        json.dump(value, stream)


def child():
    own = process_snapshots([os.getpid()])[os.getpid()]
    write('setup-keeper-owned.json', {'process': own, 'session': SESSION, 'source_sha': SOURCE,
                                    'setup_clock_sha256': CLOCK_HASH})
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        heartbeat = {'pid': os.getpid(), 'session': SESSION, 'source_sha': SOURCE,
                     'setup_clock_sha256': CLOCK_HASH, 'at': datetime.now(timezone.utc).isoformat()}
        temporary = DIRECTORY / 'heartbeat.tmp'
        temporary.write_text(json.dumps(heartbeat), encoding='utf-8')
        os.replace(temporary, DIRECTORY / 'setup-keeper-heartbeat.json')
        release = DIRECTORY / 'release.json'
        if release.exists() and json.loads(release.read_text(encoding='utf-8')) == {'nonce': NONCE}:
            return
        time.sleep(.1)
    raise RuntimeError('Offline probe release deadline expired')


def start():
    # Same argv form and creation flag as session_keeper.start. The Windows
    # venv executable may keep this Popen PID while spawning another interpreter.
    with (DIRECTORY / 'child.log').open('xb') as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'run'],
                                   stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        launcher = process_snapshots([process.pid])[process.pid]
        write('setup-keeper-launch.json', {'process': launcher, 'setup_clock_sha256': CLOCK_HASH})
        deadline = time.monotonic() + 20
        while not (DIRECTORY / 'setup-keeper-heartbeat.json').exists():
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('Offline probe did not become ready')
            time.sleep(.05)
        record_startup(DIRECTORY, clock_sha256=CLOCK_HASH, session=SESSION, source_sha=SOURCE)
    finally:
        write('release.json', {'nonce': NONCE})
        process.wait(timeout=50)  # The child's independent bound still applies on failure.
    if process.returncode:
        raise RuntimeError('Offline child failed: ' + (DIRECTORY / 'child.log').read_text())
    proof = json.loads((DIRECTORY / 'startup-process-binding.json').read_text(encoding='utf-8'))
    after = process_snapshots([proof['launcher_pid'], proof['keeper_pid']])
    if after:
        raise RuntimeError('Offline owned process remains present')
    write('probe-exit.json', {'offline_only': True, 'nonce': NONCE, 'exit_code': process.returncode,
                            'active_owned': [], 'authority_created': False, 'clock_started': False})


if __name__ == '__main__':
    if sys.argv[1:] == ['start']:
        start()
    elif sys.argv[1:] == ['run']:
        child()
    else:
        raise SystemExit('Expected start or run')
