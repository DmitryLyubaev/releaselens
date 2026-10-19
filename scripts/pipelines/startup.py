"""Read-only admission of a keeper started directly or through a Windows venv launcher.

Receipts retain the watchdog's PID/creation/command/helper-hash identity. A
different keeper PID is admitted only as the still-live launcher's direct child.
This module creates no authority, clocks, processes or cloud resources.
"""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


class StartupRefused(ValueError):
    """The current processes or receipts do not establish startup ownership."""


def utc_now():
    return datetime.now(timezone.utc)


def _pid(value):
    if type(value) is not int or value < 1:
        raise StartupRefused('pid')
    return value


def _instant(value):
    if not isinstance(value, str):
        raise StartupRefused('timestamp')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise StartupRefused('timestamp') from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise StartupRefused('timestamp')
    return result


def _identity(expected, actual):
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        raise StartupRefused('process')
    _pid(expected['pid'])
    _pid(actual['pid'])
    _instant(expected['created'])
    if (not isinstance(expected['command_line'], str) or not expected['command_line'].strip()
            or not isinstance(expected['sha256'], str)
            or re.fullmatch('[0-9a-f]{64}', expected['sha256']) is None
            or any(expected[k] != actual.get(k) for k in ('pid', 'created', 'command_line', 'sha256'))):
        raise StartupRefused('process')


def validate_startup(launch, owned, heartbeat, *, clock_sha256, session, source_sha, snapshots, now):
    """Validate recorded identities against one current native observation."""
    try:
        launcher, keeper = launch['process'], owned['process']
        launcher_pid, keeper_pid = _pid(launcher['pid']), _pid(keeper['pid'])
        actual_launcher, actual_keeper = snapshots.get(launcher_pid), snapshots.get(keeper_pid)
        _identity(launcher, actual_launcher)
        _identity(keeper, actual_keeper)
        if (launcher['command_line'] != keeper['command_line'] or launcher['sha256'] != keeper['sha256']
                or _instant(keeper['created']) < _instant(launcher['created'])):
            raise StartupRefused('chain')
        if launcher_pid != keeper_pid and _pid(actual_keeper['parent_pid']) != launcher_pid:
            raise StartupRefused('parent')
        if (not isinstance(clock_sha256, str) or re.fullmatch('[0-9a-f]{64}', clock_sha256) is None
                or not isinstance(session, str) or not session.strip()
                or not isinstance(source_sha, str) or re.fullmatch('[0-9a-f]{40}', source_sha) is None
                or any(value['setup_clock_sha256'] != clock_sha256 for value in (launch, owned, heartbeat))
                or any(value['session'] != session or value['source_sha'] != source_sha for value in (owned, heartbeat))
                or _pid(heartbeat['pid']) != keeper_pid):
            raise StartupRefused('binding')
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise StartupRefused('timestamp')
        if not timedelta(0) <= now - _instant(heartbeat['at']) < timedelta(seconds=40):
            raise StartupRefused('heartbeat')
        return {'checked_at': now.isoformat(), 'launcher_pid': launcher_pid, 'keeper_pid': keeper_pid,
                'launcher_is_wrapper': launcher_pid != keeper_pid, 'setup_clock_sha256': clock_sha256,
                'session': session, 'source_sha': source_sha,
                'launcher': actual_launcher, 'keeper': actual_keeper}
    except (KeyError, TypeError, AttributeError) as exc:
        raise StartupRefused('receipt') from exc


def process_snapshots(pids):
    """Read at most two Windows process identities together, including their parent IDs."""
    if os.name != 'nt':
        raise StartupRefused('platform')
    wanted = set(_pid(pid) for pid in pids)
    if not 1 <= len(wanted) <= 2:
        raise StartupRefused('pid')
    process_filter = ' OR '.join('ProcessId=' + str(pid) for pid in sorted(wanted))
    command = ("$rows=@(Get-CimInstance Win32_Process -Filter '" + process_filter + "' | ForEach-Object {"
               "@{pid=[int]$_.ProcessId;parent_pid=[int]$_.ParentProcessId;"
               "created=$_.CreationDate.ToUniversalTime().ToString('o');command_line=$_.CommandLine;"
               "executable_path=$_.ExecutablePath}}); ConvertTo-Json -InputObject $rows -Depth 3 -Compress")
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                capture_output=True, text=True, timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise StartupRefused('query')
        rows = json.loads(result.stdout)
        if not isinstance(rows, list):
            raise StartupRefused('query')
        helper_hash = hashlib.sha256(Path(__file__).with_name('watchdog.py').read_bytes()).hexdigest()
        values = {}
        for row in rows:
            pid = _pid(row['pid'])
            if pid not in wanted or pid in values:
                raise StartupRefused('query')
            values[pid] = dict(row, sha256=helper_hash)
        return values
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as exc:
        raise StartupRefused('query') from exc


def observe_startup(launch, owned, heartbeat, *, clock_sha256, session, source_sha):
    snapshots = process_snapshots([launch['process']['pid'], owned['process']['pid']])
    # Sampling now before a slow CIM query could admit an already expired heartbeat.
    return validate_startup(launch, owned, heartbeat, clock_sha256=clock_sha256,
                            session=session, source_sha=source_sha, snapshots=snapshots, now=utc_now())


def record_startup(directory, *, clock_sha256, session, source_sha):
    """Bind the actual launch/keeper receipts before a monitor adapter freezes its prompt."""
    directory = Path(directory)
    receipts = [json.loads((directory / name).read_text(encoding='utf-8')) for name in
                ('setup-keeper-launch.json', 'setup-keeper-owned.json', 'setup-keeper-heartbeat.json')]
    proof = observe_startup(*receipts, clock_sha256=clock_sha256, session=session, source_sha=source_sha)
    with (directory / 'startup-process-binding.json').open('x', encoding='utf-8') as stream:
        json.dump(proof, stream, indent=2)
        stream.write('\n')
    return proof
