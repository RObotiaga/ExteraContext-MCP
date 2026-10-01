#!/usr/bin/env python3
"""Offline POSIX user installation. No downloads, package managers, or config edits."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(path):
    files = {}
    for item in sorted(path.rglob('*')):
        if item.is_symlink() or not (item.is_file() or item.is_dir()):
            raise ValueError(f'unsupported payload entry: {item}')
        if item.is_file() and item != path / 'BUNDLE.json':
            files[item.relative_to(path).as_posix()] = digest(item)
    return files


def verify(path):
    manifest = path / 'BUNDLE.json'
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('missing/unsafe bundle manifest')
    data = json.loads(manifest.read_text())
    if data.get('format') != 'exteracontext-local-v1' or data.get('files') != inventory(path):
        raise ValueError('bundle inventory/hash mismatch')
    return digest(manifest)


def directory(path):
    # Reject symlink ancestors too: installation must never redirect writes.
    for part in [*reversed(path.parents), path]:
        if part.is_symlink():
            raise ValueError(f'symlink directory refused: {part}')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError(f'directory must be user-owned and not group/world writable: {path}')


def clean_environment():
    return {name: value for name, value in os.environ.items()
            if name not in {'PYTHONPATH', 'PYTHONHOME', 'NODE_OPTIONS', 'NODE_PATH'}}


def environment(release, root):
    env = clean_environment()
    env.update(EXTERACONTEXT_DB=str(release / 'data/exteracontext.sqlite'),
               EXTERACONTEXT_KNOWLEDGE_DB=str(root / 'state/overlay.sqlite'),
               EXTERACONTEXT_RUN_ROOT=str(root / 'state/runs'),
               EXTERACONTEXT_WIKI=str(release / 'data/wiki'),
               EXTERACONTEXT_AUTO_SYNC='0', EXTERACONTEXT_AUTO_BUILD='0',
               EXTERACONTEXT_REQUIRE_MANIFEST='1', EXTERACONTEXT_PYTHON=sys.executable,
               PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    return env


def preflight(release, root):
    subprocess.run([sys.executable, '-E', '-s', '-B', str(release / 'scripts/query.py'), 'preflight'],
                   env=environment(release, root), stdout=subprocess.DEVNULL, check=True)


def prerequisites():
    if os.name != 'posix' or sys.version_info < (3, 11):
        raise ValueError('requires POSIX Python >=3.11 with SQLite FTS5, and Node >=20')
    node = shutil.which('node')
    if not node:
        raise ValueError('Node >=20 required; install using your platform package manager')
    major = subprocess.check_output([node, '-p', 'process.versions.node.split(".")[0]'],
                                    env=clean_environment(), text=True).strip()
    if int(major) < 20:
        raise ValueError('Node >=20 required')
    return node


def selected(root, name='current'):
    pointer = root / name
    if not pointer.is_symlink():
        raise ValueError(f'no managed {name} release')
    target = os.readlink(pointer)
    parts = Path(target).parts
    if len(parts) != 2 or parts[0] != 'releases' or len(parts[1]) != 64 or any(c not in '0123456789abcdef' for c in parts[1]):
        raise ValueError(f'unsafe {name} pointer')
    release = root / target
    directory(release)
    if verify(release) != parts[1]:
        raise ValueError('release identity mismatch')
    return release


def switch(root, release, previous=None):
    changes = [('previous', previous)] if previous is not None else []
    changes.append(('current', release))
    for name, _ in changes:
        pointer = root / name
        if pointer.exists() and not pointer.is_symlink():
            raise ValueError(f'unmanaged {name} entry')
        temporary = root / ('.' + name + '-new')
        if temporary.exists() or temporary.is_symlink():
            raise ValueError(f'stale pointer staging entry: {temporary}')
    prepared = []
    try:
        # Prepare the old previous link too: recovery must not need symlink creation
        # after a replacement failure (e.g. ENOSPC).
        with tempfile.TemporaryDirectory(prefix='.pointers-', dir=root) as backup_dir:
            backup = Path(backup_dir) / 'previous'
            pointer = root / 'previous'
            if previous is not None and pointer.is_symlink():
                os.symlink(os.readlink(pointer), backup)
            for name, target in changes:
                temporary = root / ('.' + name + '-new')
                os.symlink('releases/' + target.name, temporary)
                prepared.append(temporary)
            if previous is not None:
                os.replace(prepared[0], pointer)
            try:
                os.replace(prepared[-1], root / 'current')
            except OSError:
                if previous is not None:
                    if backup.is_symlink():
                        os.replace(backup, pointer)
                    else:
                        pointer.unlink()
                raise
    finally:
        for temporary in prepared:
            temporary.unlink(missing_ok=True)


def install(source, root):
    identity = verify(source)
    destination = root / 'releases' / identity
    if not destination.exists():
        staged = Path(tempfile.mkdtemp(prefix='.stage-', dir=root / 'releases'))
        try:
            shutil.copytree(source, staged, dirs_exist_ok=True)
            if verify(staged) != identity:
                raise ValueError('payload changed during copy')
            preflight(staged, root)
            for item in staged.rglob('*'):
                item.chmod(0o555 if item.is_dir() else 0o444)
            staged.chmod(0o555)
            os.rename(staged, destination)
        finally:
            if staged.exists():
                for item in staged.rglob('*'):
                    if item.is_dir():
                        item.chmod(0o700)
                staged.chmod(0o700)
                shutil.rmtree(staged)
    else:
        if destination.is_symlink() or verify(destination) != identity:
            raise ValueError('existing release corrupted')
        preflight(destination, root)
    launcher = root / 'exteracontext'
    if launcher.is_symlink():
        raise ValueError('launcher symlink refused')
    fd, name = tempfile.mkstemp(prefix='.launcher-', dir=root)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(Path(__file__).read_bytes())
        os.chmod(name, 0o500)
        os.replace(name, launcher)
    finally:
        Path(name).unlink(missing_ok=True)
    old = None
    if (root / 'current').is_symlink():
        old = selected(root)
    switch(root, destination, old if old != destination else None)
    print(f'Installed {identity}\nStart: {launcher}', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve().parent
    default = here if (here / 'releases').is_dir() else Path.home() / '.local/share/exteracontext'
    parser.add_argument('--root', type=Path, default=default)
    parser.add_argument('--start', action='store_true', help='install then start stdio')
    parser.add_argument('--rollback', action='store_true')
    parser.add_argument('--check', action='store_true', help='verify installed release without serving')
    args, server_args = parser.parse_known_args()
    node = prerequisites()
    root = args.root.expanduser().absolute()
    for path in [root, root / 'releases', root / 'state', root / 'state/runs']:
        directory(path)
    for path in [root / 'state' / ('overlay.sqlite' + suffix) for suffix in ['', '-wal', '-shm', '-journal']] + [root / '.install.lock']:
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_uid != os.getuid())):
            raise ValueError(f'unsafe state file: {path}')
    fd = os.open(root / '.install.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (here / 'payload').is_dir():
            install(here / 'payload', root)
            if not args.start:
                return
        if args.rollback:
            previous = selected(root, 'previous')
            preflight(previous, root)
            old = selected(root)
            switch(root, previous, old)
            print(f'Rolled back to {previous.name}', file=sys.stderr)
            return
        release = selected(root)
        preflight(release, root)
    if args.check:
        print(f'Verified {release.name}')
        return
    os.execve(node, [node, str(release / 'mcp/src/index.mjs'), *server_args], environment(release, root))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f'ExteraContext installation refused: {error}', file=sys.stderr)
        sys.exit(1)
