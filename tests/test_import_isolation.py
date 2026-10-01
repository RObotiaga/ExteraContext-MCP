#!/usr/bin/env python3
"""Importing test modules must not run integration scenarios."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestImportIsolation(unittest.TestCase):
    def test_scenario_modules_import_without_execution(self):
        for name in ('test_knowledge_writeback', 'test_skill_contract', 'test_orchestrator', 'test_runtime_attestation'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                env = {**os.environ, 'EXTERACONTEXT_DB': str(Path(tmp) / 'absent.sqlite'),
                       'EXTERACONTEXT_KNOWLEDGE_DB': str(Path(tmp) / 'absent-overlay.sqlite'),
                       'EXTERACONTEXT_AUTO_SYNC': '0'}
                code = f'import sys; sys.path.insert(0, "tests"); import {name}'
                result = subprocess.run([sys.executable, '-B', '-c', code], cwd=ROOT,
                                        env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
