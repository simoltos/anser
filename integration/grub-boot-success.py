#!/usr/bin/python3
"""Mark success after a real graphical user session has survived two minutes."""
from pathlib import Path
import json
import subprocess
import time

ENVIRONMENT = Path('/boot/grub/grubenv')
TIMER = 'anser-grub-boot-success.timer'


def graphical_session_ready(properties, now_us):
    try:
        started = int(properties.get('TimestampMonotonic', '0'))
    except ValueError:
        return False
    return (properties.get('Class') == 'user'
            and properties.get('Type') in ('wayland', 'x11')
            and properties.get('Active') == 'yes'
            and started > 0 and now_us - started >= 120_000_000)


def mark_success(environment=ENVIRONMENT):
    data = subprocess.check_output(['grub-editenv', str(environment), 'list'], text=True)
    flags = dict(line.split('=', 1) for line in data.splitlines() if '=' in line)
    if flags.get('menu_auto_hide') != '1' or flags.get('boot_success') == '1':
        return True
    sessions = json.loads(subprocess.check_output(
        ['loginctl', 'list-sessions', '--json=short'], text=True))
    # Match loginctl's monotonic timestamp, excluding suspended time.
    now_us = time.monotonic_ns() // 1000
    for session in sessions:
        try:
            output = subprocess.check_output([
                'loginctl', 'show-session', str(session['session']),
                '-p', 'Class', '-p', 'Type', '-p', 'Active', '-p', 'TimestampMonotonic'], text=True)
        except subprocess.CalledProcessError:
            # A session may disappear between enumeration and inspection.
            continue
        properties = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
        if graphical_session_ready(properties, now_us):
            subprocess.run(['grub-editenv', str(environment), 'set', 'boot_success=1'], check=True)
            return True
    return False


if __name__ == '__main__':
    if mark_success():
        # Stop polling once this boot is successful (or auto-hide was disabled).
        subprocess.run(['systemctl', '--no-block', 'stop', TIMER], check=True)
