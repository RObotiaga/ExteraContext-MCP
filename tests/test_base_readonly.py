#!/usr/bin/env python3
"""Behavioral guarantees for the immutable retrieval connection."""
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from prepare_ci_fixture import create_fixture

spec = importlib.util.spec_from_file_location('query_readonly_test', ROOT / 'scripts/query.py')
query = importlib.util.module_from_spec(spec)
spec.loader.exec_module(query)


class BaseReadOnlyTests(unittest.TestCase):
    def test_read_connection_rejects_write_even_if_query_only_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'base with # and %.sqlite'
            create_fixture(db)
            with patch.object(query, 'DB', db), patch.dict('os.environ', {'EXTERACONTEXT_AUTO_SYNC': '0'}):
                con = query.con()
                try:
                    self.assertEqual(con.execute('PRAGMA query_only').fetchone()[0], 1)
                    con.execute('PRAGMA query_only=OFF')
                    with self.assertRaisesRegex(sqlite3.OperationalError, 'readonly'):
                        con.execute("UPDATE facts SET claim='corrupted'")
                finally:
                    con.close()

    def test_replacement_between_verification_and_connect_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = create_fixture(Path(tmp) / 'base.sqlite')
            replacement = create_fixture(Path(tmp) / 'replacement.sqlite')
            original_connect = sqlite3.connect
            def replace_before_connect(*args, **kwargs):
                replacement.replace(db)
                return original_connect(*args, **kwargs)
            with patch.object(query, 'DB', db), patch.dict('os.environ', {'EXTERACONTEXT_AUTO_SYNC': '0'}), \
                 patch.object(query.sqlite3, 'connect', side_effect=replace_before_connect):
                with self.assertRaisesRegex(RuntimeError, 'changed'):
                    connection = query.con()
                    connection.close()

    def test_checkpointed_wal_base_read_creates_no_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = create_fixture(Path(tmp) / 'base.sqlite')
            connection = sqlite3.connect(db)
            try:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            finally:
                connection.close()
            self.assertFalse(Path(str(db) + '-wal').exists())
            self.assertFalse(Path(str(db) + '-shm').exists())
            with patch.object(query, 'DB', db), patch.dict('os.environ', {'EXTERACONTEXT_AUTO_SYNC': '0'}):
                connection = query.con()
                try:
                    self.assertGreater(connection.execute('SELECT COUNT(*) FROM facts').fetchone()[0], 0)
                    self.assertFalse(Path(str(db) + '-wal').exists())
                    self.assertFalse(Path(str(db) + '-shm').exists())
                finally:
                    connection.close()

    def test_disappearing_database_is_not_recreated(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'absent.sqlite'
            with patch.object(query, 'DB', db), patch.object(query, 'ensure_db'):
                with self.assertRaises((sqlite3.OperationalError, FileNotFoundError)):
                    query.con()
            self.assertFalse(db.exists())


if __name__ == '__main__':
    unittest.main()
