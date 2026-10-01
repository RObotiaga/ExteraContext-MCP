#!/usr/bin/env python3
"""Managed corpus consistency is enforced, not merely documented."""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from prepare_ci_fixture import create_fixture
from sync_knowledge import sync
from update_knowledge import create_manifest


class ManifestPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source/base.sqlite'
        create_fixture(self.source)
        with closing(sqlite3.connect(self.source)) as c:
            c.executescript('CREATE TABLE source_runs(call_id TEXT, source_id TEXT, role TEXT); CREATE TABLE source_provenance(source_id TEXT, collector_runs TEXT, reviewer_runs TEXT, has_collector INT, has_reviewer INT);')
        self.db = self.root / 'deployed/base.sqlite'
        self.manifest = sync(self.source, self.db)
        self.env = {**os.environ, 'EXTERACONTEXT_DB': str(self.db),
                    'EXTERACONTEXT_KNOWLEDGE_DB': str(self.root / 'overlay.sqlite'),
                    'EXTERACONTEXT_AUTO_SYNC': '0', 'EXTERACONTEXT_REQUIRE_MANIFEST': '0'}

    def invoke(self, *args):
        return subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/query.py'), *args],
                              env=self.env, capture_output=True, text=True)

    def test_retrieval_refuses_manifest_digest_mismatch(self):
        data = json.loads(self.manifest.read_text())
        data['destination_sha256'] = '0' * 64
        self.manifest.write_text(json.dumps(data))
        result = self.invoke('api', 'fixture_symbol', '--format', 'json')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('manifest', result.stderr.lower())

    def test_retrieval_refuses_malformed_manifest(self):
        self.manifest.write_text('{broken')
        result = self.invoke('api', 'fixture_symbol', '--format', 'json')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('manifest', result.stderr.lower())

    def test_strict_deployment_refuses_missing_manifest(self):
        self.manifest.unlink()
        self.env['EXTERACONTEXT_REQUIRE_MANIFEST'] = '1'
        result = self.invoke('api', 'fixture_symbol', '--format', 'json')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('manifest', result.stderr.lower())

    def test_doctor_distinguishes_consistency_from_commit_provenance(self):
        result = self.invoke('doctor')
        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads(result.stdout)['corpus_verification']
        self.assertEqual(status['manifest_status'], 'verified')
        self.assertFalse(status['commit_verified'])
        self.assertEqual(status['method'], 'offline-existing-db-copy')
        self.manifest.unlink()
        result = self.invoke('doctor')
        self.assertEqual(json.loads(result.stdout)['corpus_verification']['manifest_status'], 'absent-unverified')

    def test_release_manifest_supported_without_claiming_authenticated_source(self):
        data = create_manifest(self.db, 'a' * 40)
        self.manifest.write_text(json.dumps(data))
        result = self.invoke('doctor')
        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads(result.stdout)['corpus_verification']
        self.assertEqual(status['manifest_status'], 'verified')
        self.assertFalse(status['commit_verified'])
        self.assertEqual(status['declared_source_commit'], 'a' * 40)

    def test_retrieval_refuses_size_or_schema_mismatch(self):
        for field, value in [('size_bytes', 1), ('schema', {})]:
            with self.subTest(field=field):
                self.manifest = sync(self.source, self.db)
                data = json.loads(self.manifest.read_text())
                data[field] = value
                self.manifest.write_text(json.dumps(data))
                result = self.invoke('api', 'fixture_symbol', '--format', 'json')
                self.assertNotEqual(result.returncode, 0, result.stdout)

    @unittest.skipUnless(shutil.which('node') and (ROOT / 'mcp/node_modules/@modelcontextprotocol/server').exists(), 'MCP SDK dependencies absent')
    def test_manifest_mismatch_blocks_actual_mcp_startup(self):
        data = json.loads(self.manifest.read_text())
        data['destination_sha256'] = '0' * 64
        self.manifest.write_text(json.dumps(data))
        env = {**self.env, 'EXTERACONTEXT_MCP_HTTP_TOKEN': 'startup-test-only-' + 'x' * 40,
               'EXTERACONTEXT_PYTHON': sys.executable}
        for transport in ('stdio', 'http'):
            with self.subTest(transport=transport):
                result = subprocess.run(['node', str(ROOT / 'mcp/src/index.mjs'), '--transport', transport],
                                        env=env, input='', capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn('manifest', result.stderr.lower())
                self.assertNotIn('listening on', result.stderr)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO only')
    def test_nonregular_manifest_refused_without_blocking(self):
        self.manifest.unlink()
        os.mkfifo(self.manifest)
        try:
            result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/query.py'), 'api', 'fixture_symbol', '--format', 'json'],
                                    env=self.env, capture_output=True, text=True, timeout=3)
        except subprocess.TimeoutExpired:
            self.fail('manifest FIFO blocked retrieval instead of being refused')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('regular', result.stderr.lower())

    def test_large_manifest_refused(self):
        self.manifest.write_bytes(b' ' * (1024 * 1024 + 1))
        result = self.invoke('api', 'fixture_symbol', '--format', 'json')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('manifest', result.stderr.lower())


if __name__ == '__main__':
    unittest.main()
