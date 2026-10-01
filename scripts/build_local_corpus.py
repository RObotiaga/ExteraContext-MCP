#!/usr/bin/env python3
"""Build reviewed, exact public Knowledge source only in an isolated Docker container.

Image must already exist locally and contain Python with SQLite FTS5. No pull,
network, host code execution from Knowledge, .git mount, or credential forwarding.
"""
import argparse
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--disable-selinux-label', action='store_true', help='explicit local workaround for inaccessible Fedora bind mounts')
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{40}', args.source_commit):
        raise ValueError('expected exact lowercase 40-character commit')
    actual = subprocess.check_output(['git', '-C', str(args.source), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != args.source_commit:
        raise ValueError('source commit mismatch')
    if subprocess.check_output(['git', '-C', str(args.source), 'status', '--porcelain']):
        raise ValueError('source must be clean; inspect builder before building')
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('output must not exist')
    image = subprocess.check_output(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'], text=True).strip()
    source_bytes = subprocess.check_output(['git', '-C', str(args.source), 'archive', args.source_commit])
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        work = Path(temporary)
        exported = work / 'source'
        exported.mkdir()
        with tarfile.open(fileobj=io.BytesIO(source_bytes)) as archive:
            if any(not (member.isfile() or member.isdir()) for member in archive.getmembers()):
                raise ValueError('source archive contains nonregular entries')
            archive.extractall(exported, filter='data')
        for path in [exported, *exported.rglob('*')]:
            path.chmod(0o555 if path.is_dir() else 0o444)
        candidate = work / 'candidate'
        candidate.mkdir(mode=0o1777)
        candidate.chmod(0o1777)
        flags = ['docker', 'run', '--rm', '--pull=never', '--network=none', '--read-only',
                 '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=128',
                 '--memory=2g', '--cpus=2', '--user=65534:65534',
                 '--tmpfs=/tmp:rw,noexec,nosuid,size=64m']
        if args.disable_selinux_label:
            flags += ['--security-opt=label=disable']
        subprocess.run([*flags, '-v', f'{exported}:/source:ro', '-v', f'{candidate}:/out:rw', image,
                        'python3', '-B', '/source/scripts/build_index.py', '--wiki', '/source/data/wiki',
                        '--provenance', '/source/data/legacy-source-runs.json', '--db', '/out/exteracontext.sqlite'], check=True)
        # A second isolated process validates the untrusted SQLite; never run the
        # builder or open SQLite in the publisher/host helper.
        subprocess.run([*flags, '-v', f'{ROOT / "scripts"}:/runtime:ro', '-v', f'{candidate}:/out:rw', image,
                        'python3', '-B', '/runtime/knowledge_release.py', 'manifest', '--db', '/out/exteracontext.sqlite',
                        '--source-commit', actual, '--output', '/out/.knowledge-manifest.json'], check=True)
        subprocess.run([*flags, '-v', f'{candidate}:/out:rw', image, 'chmod', '0444',
                        '/out/exteracontext.sqlite', '/out/.knowledge-manifest.json'], check=True)
        expected = {'exteracontext.sqlite', '.knowledge-manifest.json'}
        if {p.name for p in candidate.iterdir()} != expected or any(p.is_symlink() or not p.is_file() for p in candidate.iterdir()):
            raise ValueError('unexpected/nonregular candidate output')
        output.mkdir(mode=0o700)
        for name in sorted(expected):
            # Create host-owned copies; never retain builder inode ownership.
            with (candidate / name).open('rb') as source, (output / name).open('xb') as destination:
                import shutil
                shutil.copyfileobj(source, destination)
        (output / 'BUILD.json').write_text(json.dumps({'source_commit': actual, 'image_id': image,
            'source_repository': 'RObotiaga/ExteraContext-Knowledge',
            'network': 'none', 'source_mount': 'read-only git archive without .git',
            'uid': 65534, 'selinux_label_disabled': args.disable_selinux_label}, indent=2) + '\n')
        for path in [exported, *exported.rglob('*')]:
            if path.is_dir():
                path.chmod(0o700)
    print(f'Built and validated {actual}: {output}')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(f'Local corpus build refused: {error}')
