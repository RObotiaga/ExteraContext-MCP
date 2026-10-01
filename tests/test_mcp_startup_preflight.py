#!/usr/bin/env python3
"""Actual MCP processes must reject bad corpus before serving either transport."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node') and (ROOT / 'mcp/node_modules/@modelcontextprotocol/server').exists(), 'MCP SDK dependencies absent')
class McpStartupPreflightTests(unittest.TestCase):
    def test_missing_and_corrupt_corpus_refuse_both_transports(self):
        with tempfile.TemporaryDirectory() as tmp:
            for transport in ('stdio', 'http'):
                for corrupt in (False, True):
                    with self.subTest(transport=transport, corrupt=corrupt):
                        db = Path(tmp) / f'{transport}-{corrupt}.sqlite'
                        if corrupt:
                            db.write_bytes(b'not sqlite')
                        env = {**os.environ, 'EXTERACONTEXT_DB': str(db),
                               'EXTERACONTEXT_AUTO_SYNC': '0',
                               'EXTERACONTEXT_MCP_HTTP_TOKEN': 'local-test-only-' + 'x' * 40}
                        child = subprocess.Popen(['node', str(ROOT / 'mcp/src/index.mjs'), '--transport', transport],
                                                 env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                        try:
                            stdout, stderr = child.communicate('', timeout=3)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            stdout, stderr = child.communicate()
                            self.fail(f'{transport} served invalid corpus: {stderr}')
                        self.assertNotEqual(child.returncode, 0, (stdout, stderr))
                        self.assertNotIn('listening on', stderr)
                        self.assertFalse(Path(str(db) + '-wal').exists())


if __name__ == '__main__':
    unittest.main()
