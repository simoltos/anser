#!/usr/bin/python3
"""Activate a signed VM repository by temporarily mounting its configuration."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
import urllib.request

FIRMWARE = Path('/sys/firmware/qemu_fw_cfg/by_name/opt/anser/local-repository/raw')
RUNTIME = Path('/run/anser-local-repository')
CONFIG = Path('/etc/pacman.conf')
DATABASE = Path('/var/lib/pacman')
ALIASES = {'anser-local-testing': 'anser-testing', 'anser-local': 'anser'}


def checked(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def signer(keyring):
    result = checked(['gpg', '--batch', '--with-colons', '--show-keys', str(keyring)],
                     capture_output=True, text=True).stdout.splitlines()
    primary = False
    identities = []
    for line in result:
        values = line.split(':')
        if values[0] == 'pub':
            if values[1] in ('r', 'e'):
                raise ValueError('The Anser repository key is revoked or expired')
            primary = True
        elif values[0] == 'sub':
            primary = False
        elif values[0] == 'fpr' and primary:
            identities.append(values[9])
            primary = False
    if len(identities) != 1:
        raise ValueError('Expected exactly one Anser public signing identity')
    return identities[0]


def verify(path, expected):
    status = checked(['gpg', '--homedir', '/etc/pacman.d/gnupg', '--batch', '--status-fd', '1',
                      '--verify', str(path)+'.sig', str(path)], capture_output=True, text=True).stdout
    valid = [line.split() for line in status.splitlines() if line.startswith('[GNUPG:] VALIDSIG ')]
    if (len(valid) != 1 or valid[0][-1] != expected
            or re.search(r'\[GNUPG:\] (BADSIG|ERRSIG|EXPSIG|EXPKEYSIG|REVKEYSIG|KEYREVOKED|KEYEXPIRED)\b', status)
            or not re.search(r'^\[GNUPG:\] TRUST_(FULLY|ULTIMATE)\b', status, re.M)):
        raise ValueError('Local repository signature is invalid or untrusted: '+str(path))


def local_config(original, url):
    if '[anser-local' in original:
        raise ValueError('A local repository is already present in the original configuration')
    position = re.search(r'^\[(?:anser-testing|anser|core)\]\s*$', original, re.M)
    if not position:
        raise ValueError('No repository insertion point in pacman.conf')
    additions = ''.join(f'[{alias}]\nSigLevel = Required TrustedOnly\nServer = {url}/{channel}/x86_64\n\n'
                        for alias, channel in ALIASES.items())
    return original[:position.start()]+additions+original[position.start():]


def download(url, target):
    error = None
    for attempt in range(20):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                data = response.read(32*1024*1024+1)
            if len(data) > 32*1024*1024:
                raise ValueError('Repository metadata exceeds the size limit')
            target.write_bytes(data)
            return
        except OSError as failure:
            error = failure
            if attempt < 19:
                time.sleep(1)
    raise error


def activate(config=CONFIG, firmware=FIRMWARE, runtime=RUNTIME, database=DATABASE):
    if not firmware.is_file():
        return False
    if config.is_symlink() or runtime.is_symlink():
        raise ValueError('Refusing linked repository configuration paths')
    url = firmware.read_text().rstrip('\x00').strip().rstrip('/')
    parsed = urlsplit(url)
    if (not re.fullmatch(r'http://10\.0\.2\.2(?::[0-9]+)?', url)
            or parsed.port is not None and not 1 <= parsed.port <= 65535):
        raise ValueError('Expected the explicitly supplied QEMU host HTTP repository')
    if (runtime/'active.json').exists():
        if not config.samefile(runtime/'pacman.conf'):
            raise ValueError('Stale local repository activation state')
        return True
    original = config.read_bytes()
    content = local_config(original.decode(), url)
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    expected = signer(Path('/usr/share/pacman/keyrings/anser.gpg'))
    with tempfile.TemporaryDirectory(prefix='metadata-', dir=runtime) as temporary:
        temporary = Path(temporary)
        for alias, channel in ALIASES.items():
            for kind in ('db', 'files'):
                target = temporary/f'{alias}.{kind}'
                for suffix in ('', '.sig'):
                    download(f'{url}/{channel}/x86_64/{alias}.{kind}{suffix}', Path(str(target)+suffix))
                verify(target, expected)
        # Respect pacman's lock while installing only our distinct sync indices.
        lock = database/'db.lck'
        lock_descriptor = os.open(lock, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
        try:
            sync = database/'sync'
            sync.mkdir(exist_ok=True)
            for path in temporary.iterdir():
                output_descriptor, filename = tempfile.mkstemp(prefix='.anser-local-', dir=sync)
                staged = Path(filename)
                try:
                    with os.fdopen(output_descriptor, 'wb') as output:
                        output.write(path.read_bytes())
                    staged.chmod(0o644)
                    os.replace(staged, sync/path.name)
                finally:
                    staged.unlink(missing_ok=True)
        finally:
            os.close(lock_descriptor)
            lock.unlink()
    target = runtime/'pacman.conf'
    target.write_text(content)
    target.chmod(0o644)
    (runtime/'active.json').write_text(json.dumps({'url':url, 'signer':expected,
        'original_sha256':hashlib.sha256(original).hexdigest()})+'\n')
    try:
        checked(['mount', '--bind', str(target), str(config)])
    except subprocess.CalledProcessError:
        (runtime/'active.json').unlink()
        raise
    print('Anser: signed local repositories activated for this VM session', flush=True)
    return True


def deactivate(config=CONFIG, runtime=RUNTIME):
    target = runtime/'pacman.conf'
    if not (runtime/'active.json').is_file():
        return False
    if not config.samefile(target):
        raise ValueError('The active mount is not owned by the local repository service')
    expected = json.loads((runtime/'active.json').read_text())['original_sha256']
    checked(['umount', str(config)])
    if hashlib.sha256(config.read_bytes()).hexdigest() != expected:
        raise ValueError('Underlying pacman.conf changed during the local session')
    (runtime/'active.json').unlink()
    print('Anser: public repository configuration restored', flush=True)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('activate', 'deactivate'))
    args = parser.parse_args()
    if args.action == 'activate':
        activate()
    else:
        deactivate()


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit('Anser local repositories: '+str(error))
