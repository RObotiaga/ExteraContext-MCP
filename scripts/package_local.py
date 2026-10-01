#!/usr/bin/env python3
"""Package an already sandbox-built corpus and npm-ci dependencies; never publish."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

from local_install import inventory, verify
from update_knowledge import verify_manifest

ROOT = Path(__file__).resolve().parents[1]


def package(corpus, output, dependencies):
    manifest = json.loads((corpus / '.knowledge-manifest.json').read_text())
    verify_manifest(corpus / 'exteracontext.sqlite', corpus / '.knowledge-manifest.json', manifest['source_commit'])
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError('output already exists; never replace a reviewed artifact')
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        work = Path(temporary)
        payload = work / 'payload'
        payload.mkdir()
        # Explicit allowlist: no .git, credentials, user state, previous runs, or remote code.
        (payload / 'scripts').mkdir()
        for name in ['query.py', 'knowledge.py', 'knowledge_store.py', 'orchestrate.py',
                     'corpus_preflight.py', 'sync_knowledge.py', 'update_knowledge.py']:
            source = ROOT / 'scripts' / name
            if source.is_symlink():
                raise ValueError('runtime symlink refused')
            shutil.copyfile(source, payload / 'scripts' / name)
        for name in ['schemas', 'mcp/src']:
            source = ROOT / name
            destination = payload / name
            destination.mkdir(parents=True)
            for item in sorted(source.rglob('*')):
                if item.is_file() and item.suffix in {'.py', '.mjs', '.json'}:
                    if item.is_symlink():
                        raise ValueError('runtime symlink refused')
                    relative = item.relative_to(source)
                    (destination / relative).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(item, destination / relative)
        for name in ['VERSION', 'SKILL.md', 'KnowledgeStore.md', 'NewKnowledge.md', 'mcp/package.json', 'mcp/package-lock.json']:
            shutil.copyfile(ROOT / name, payload / name)
        shutil.copytree(dependencies, payload / 'mcp/node_modules', symlinks=True,
                        ignore=shutil.ignore_patterns('.bin'))
        (payload / 'data').mkdir()
        for name in ['exteracontext.sqlite', '.knowledge-manifest.json']:
            if (corpus / name).is_symlink():
                raise ValueError('corpus symlink refused')
            shutil.copyfile(corpus / name, payload / 'data' / name)
        files = inventory(payload)
        if any(name.endswith(('.node', '.so', '.dll', '.dylib')) for name in files):
            raise ValueError('native dependencies require a platform-specific artifact')
        commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
        metadata = {'format': 'exteracontext-local-v1', 'runtime_commit': commit,
                    'runtime_tree': 'local working tree, not a published release',
                    'source_commit': manifest['source_commit'], 'files': files}
        (payload / 'BUNDLE.json').write_text(json.dumps(metadata, sort_keys=True, indent=2) + '\n')
        verify(payload)
        shutil.copyfile(ROOT / 'scripts/local_install.py', work / 'install.py')
        shutil.copyfile(ROOT / 'docs/LOCAL_INSTALL.md', work / 'LOCAL_INSTALL.md')
        with output.open('xb') as stream, tarfile.open(fileobj=stream, mode='w:gz') as archive:
            for name in ['install.py', 'LOCAL_INSTALL.md', 'payload']:
                archive.add(work / name, arcname=name)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_name(output.name + '.sha256').write_text(f'{digest}  {output.name}\n')
    print(json.dumps({'artifact': str(output.absolute()), 'sha256': digest, 'source_commit': manifest['source_commit'], 'counts': manifest['schema']['counts']}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dependencies', type=Path, default=ROOT / 'mcp/node_modules')
    args = parser.parse_args()
    package(args.corpus, args.output, args.dependencies)


if __name__ == '__main__':
    main()
