#!/usr/bin/env python3
"""Keep the Job Journal dashboard running in the background on macOS.

  python3 scripts/dashboard_service.py install    start now, at every login, and after any exit
  python3 scripts/dashboard_service.py status     show whether it is running
  python3 scripts/dashboard_service.py uninstall  stop it and remove the login item
"""

import os
from pathlib import Path
import plistlib
import subprocess
import sys

LABEL = 'local.job-journal.dashboard'
ROOT = Path(__file__).resolve().parent.parent
PLIST = Path.home() / 'Library' / 'LaunchAgents' / f'{LABEL}.plist'
LOG = ROOT / 'data' / 'dashboard.log'
DOMAIN = f'gui/{os.getuid()}'


def launchctl(*args, check=True):
    return subprocess.run(['launchctl', *args], check=check, capture_output=True, text=True)


def uninstall():
    launchctl('bootout', f'{DOMAIN}/{LABEL}', check=False)
    PLIST.unlink(missing_ok=True)
    print('Dashboard service removed. Start it by hand with: python3 scripts/serve.py')


def install():
    launchctl('bootout', f'{DOMAIN}/{LABEL}', check=False)  # replace any earlier copy
    LOG.parent.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    with PLIST.open('wb') as handle:
        plistlib.dump({
            'Label': LABEL,
            'ProgramArguments': [sys.executable, str(ROOT / 'scripts' / 'serve.py')],
            'WorkingDirectory': str(ROOT),
            'RunAtLoad': True,
            'KeepAlive': True,           # restart after any exit or crash
            'ThrottleInterval': 5,       # wait 5s between restarts, e.g. while the port is still busy
            'StandardOutPath': str(LOG),
            'StandardErrorPath': str(LOG),
            'EnvironmentVariables': {'PYTHONUNBUFFERED': '1'},
        }, handle)
    launchctl('bootstrap', DOMAIN, str(PLIST))
    print(f'Dashboard service installed: http://127.0.0.1:8765 (log: {LOG})')


def status():
    result = launchctl('print', f'{DOMAIN}/{LABEL}', check=False)
    if result.returncode:
        print('Not installed.')
        return
    fields = dict(line.strip().split(' = ', 1) for line in result.stdout.splitlines() if ' = ' in line and line.startswith('\t') and not line.startswith('\t\t'))
    print(f"state: {fields.get('state', '?')}  pid: {fields.get('pid', '-')}  last exit: {fields.get('last exit code', '-')}")


if __name__ == '__main__':
    commands = {'install': install, 'uninstall': uninstall, 'status': status}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    commands[sys.argv[1]]()
