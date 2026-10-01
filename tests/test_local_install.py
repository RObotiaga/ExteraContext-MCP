"""Local artifact lifecycle; tiny fixtures here are security tests, not corpus acceptance."""
import json
import os
import shutil
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from prepare_ci_fixture import create_fixture
from update_knowledge import create_manifest


class LocalInstallTests(unittest.TestCase):
    def pointer_failure_case(self, operation, failure):
        import local_install as installer
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'install'
            for path in [root / 'releases', root / 'state/runs']:
                path.mkdir(parents=True)

            def payload(name):
                source = Path(temporary) / name
                (source / 'scripts').mkdir(parents=True)
                (source / 'scripts/query.py').write_text('pass\n')
                (source / 'VERSION').write_text(name)
                (source / 'BUNDLE.json').write_text(json.dumps({
                    'format': 'exteracontext-local-v1', 'files': installer.inventory(source)}))
                return source

            installer.install(payload('A'), root)
            installer.install(payload('B'), root)
            before = {name: os.readlink(root / name) for name in ['current', 'previous']}
            candidate = payload('C')

            def invoke():
                if operation == 'install':
                    installer.install(candidate, root)
                else:
                    with patch.object(sys, 'argv', ['local_install.py', '--root', str(root), '--rollback']):
                        installer.main()

            stale = root / '.current-new'
            if failure == 'stale':
                stale.write_text('existing entry must be preserved')
                with self.assertRaisesRegex(ValueError, 'stale pointer staging entry'):
                    invoke()
                self.assertEqual(stale.read_text(), 'existing entry must be preserved')
                stale.unlink()
            else:
                function = os.symlink if failure == 'prepare' else os.replace

                def fail_current(source, destination, *args, **kwargs):
                    if Path(destination) == root / ('.current-new' if failure == 'prepare' else 'current'):
                        raise OSError('injected current filesystem failure')
                    return function(source, destination, *args, **kwargs)

                with patch.object(installer.os, 'symlink' if failure == 'prepare' else 'replace', fail_current):
                    with self.assertRaisesRegex(OSError, 'injected current filesystem failure'):
                        invoke()
            self.assertEqual({name: os.readlink(root / name) for name in before}, before,
                             f'failed {operation} lost release pointers')
            self.assertFalse((root / '.previous-new').exists())
            self.assertFalse(stale.is_symlink())
            # Retry rollback must really activate A, not silently roll back B to B.
            with patch.object(sys, 'argv', ['local_install.py', '--root', str(root), '--rollback']):
                installer.main()
            self.assertEqual(os.readlink(root / 'current'), before['previous'])
            self.assertEqual(os.readlink(root / 'previous'), before['current'])

    def test_stale_current_preserves_install_and_rollback_pointers(self):
        for operation in ['install', 'rollback']:
            with self.subTest(operation=operation):
                self.pointer_failure_case(operation, 'stale')

    def test_current_filesystem_failure_preserves_install_and_rollback_pointers(self):
        for operation in ['install', 'rollback']:
            for failure in ['prepare', 'replace']:
                with self.subTest(operation=operation, failure=failure):
                    self.pointer_failure_case(operation, failure)

    def test_failed_first_update_restores_absent_previous(self):
        import local_install as installer
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'releases').mkdir()
            old = root / 'releases' / ('a' * 64)
            new = root / 'releases' / ('b' * 64)
            old.mkdir()
            new.mkdir()
            (root / 'current').symlink_to('releases/' + old.name)
            replace = os.replace

            def fail_current(source, destination):
                if Path(destination) == root / 'current':
                    raise OSError('current replace failed')
                return replace(source, destination)

            with patch.object(installer.os, 'replace', fail_current):
                with self.assertRaisesRegex(OSError, 'current replace failed'):
                    installer.switch(root, new, old)
            self.assertEqual(os.readlink(root / 'current'), 'releases/' + old.name)
            self.assertFalse(os.path.lexists(root / 'previous'))
            self.assertEqual(sorted(path.name for path in root.iterdir()), ['current', 'releases'])
            installer.switch(root, new, old)
            self.assertEqual(os.readlink(root / 'current'), 'releases/' + new.name)
            self.assertEqual(os.readlink(root / 'previous'), 'releases/' + old.name)

    def test_builder_refuses_wrong_commit_before_container_or_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'corpus'
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/build_local_corpus.py'), '--source', str(ROOT), '--source-commit', '0' * 40, '--image', 'not-a-real-image', '--output', str(output)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('source commit mismatch', result.stderr)
            self.assertFalse(output.exists())

    def test_nested_bundle_named_file_is_not_exempt_from_hashing(self):
        from local_install import inventory
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'nested').mkdir()
            (root / 'nested/BUNDLE.json').write_text('untrusted nested file')
            self.assertIn('nested/BUNDLE.json', inventory(root))

    @unittest.skipUnless(shutil.which('node'), 'Node prerequisite unavailable')
    def test_start_refuses_sqlite_sidecar_symlink_before_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'install'
            (root / 'state').mkdir(parents=True)
            (root / 'state/overlay.sqlite-wal').symlink_to(Path(temporary) / 'outside')
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/local_install.py'), '--root', str(root), '--check'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('unsafe state file', result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node prerequisite unavailable')
    def test_node_prerequisite_probe_does_not_execute_node_options(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sentinel = root / 'executed'
            injected = root / 'inject.mjs'
            injected.write_text('import {writeFileSync} from "node:fs"; writeFileSync(' + json.dumps(str(sentinel)) + ', "executed");')
            env = {**os.environ, 'NODE_OPTIONS': '--import=' + str(injected)}
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/local_install.py'), '--root', str(root / 'install'), '--check'], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(sentinel.exists(), 'prerequisite probe executed injected code')

    def test_runtime_ignores_python_and_node_injection_environment(self):
        from local_install import environment
        from unittest.mock import patch
        with patch.dict(os.environ, {'PYTHONPATH': '/untrusted', 'PYTHONHOME': '/untrusted', 'NODE_OPTIONS': '--import=/untrusted.mjs', 'NODE_PATH': '/untrusted'}):
            env = environment(Path('/release'), Path('/install'))
        for name in ['PYTHONPATH', 'PYTHONHOME', 'NODE_OPTIONS', 'NODE_PATH']:
            self.assertTrue(name not in env, f'injection variable retained: {name}')

    @unittest.skipUnless(shutil.which('node') and (ROOT / 'mcp/node_modules/@modelcontextprotocol/server').is_dir(), 'Install locked MCP dependencies for package lifecycle tests')
    def test_verified_offline_package_installs_without_paths_and_preserves_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            corpus = work / 'corpus'
            corpus.mkdir()
            db = corpus / 'exteracontext.sqlite'
            create_fixture(db)
            with sqlite3.connect(db) as con:
                con.executescript('CREATE TABLE source_runs(call_id TEXT, source_id TEXT, role TEXT); CREATE TABLE source_provenance(source_id TEXT, collector_runs TEXT, reviewer_runs TEXT, has_collector INT, has_reviewer INT);')
            (corpus / '.knowledge-manifest.json').write_text(json.dumps(create_manifest(db, 'a' * 40)))
            artifact = work / 'artifact.tar.gz'
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/package_local.py'), '--corpus', str(corpus), '--output', str(artifact)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            import tarfile
            with tarfile.open(artifact) as archive:
                names = set(archive.getnames())
                for forbidden in ['payload/scripts/prepare_ci_fixture.py', 'payload/scripts/build_local_corpus.py', 'payload/scripts/package_local.py']:
                    self.assertNotIn(forbidden, names, 'build/fixture tooling is not runtime')
                archive.extractall(work / 'unpacked', filter='data')
            install = work / 'unpacked/install.py'
            root = work / 'user-install'
            result = subprocess.run([sys.executable, str(install), '--root', str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / 'current/data/exteracontext.sqlite').is_file())
            self.assertTrue((root / 'state/runs').is_dir())
            sentinel = root / 'state/overlay.sqlite'
            sentinel.write_bytes(b'preserve me')
            result = subprocess.run([sys.executable, str(install), '--root', str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sentinel.read_bytes(), b'preserve me')
            self.assertTrue((root / 'exteracontext').is_file())
            # A new payload gets a distinct release; rollback must preserve state.
            payload = work / 'unpacked/payload'
            sys.path.insert(0, str(ROOT / 'scripts'))
            from local_install import inventory
            bundle = json.loads((payload / 'BUNDLE.json').read_text())
            (payload / 'VERSION').write_text('local-update-test\n')
            bundle['files'] = inventory(payload)
            (payload / 'BUNDLE.json').write_text(json.dumps(bundle))
            old = os.readlink(root / 'current')
            launcher = root / 'exteracontext'
            launcher.unlink()
            launcher.symlink_to(work / 'outside')
            result = subprocess.run([sys.executable, str(install), '--root', str(root)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(os.readlink(root / 'current'), old, 'failed install changed active release')
            launcher.unlink()
            result = subprocess.run([sys.executable, str(install), '--root', str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual(os.readlink(root / 'current'), old)
            active_update = os.readlink(root / 'current')
            stale = root / '.previous-new'
            stale.write_text('stale staging entry')
            result = subprocess.run([sys.executable, str(launcher), '--rollback'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(os.readlink(root / 'current'), active_update, 'failed rollback changed active release')
            stale.unlink()
            result = subprocess.run([sys.executable, str(launcher), '--rollback'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(os.readlink(root / 'current'), old)
            self.assertEqual(sentinel.read_bytes(), b'preserve me')


if __name__ == '__main__':
    unittest.main()
