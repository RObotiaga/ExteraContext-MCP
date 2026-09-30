#!/usr/bin/env python3
"""Offline checks for doctor base-database identity metadata."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import query  # noqa: E402


class DoctorMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "tiny-corpus.sqlite"
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE facts (id TEXT PRIMARY KEY, status TEXT, source_id TEXT);
                CREATE TABLE source_runs (call_id TEXT, source_id TEXT, role TEXT);
                CREATE TABLE source_provenance (source_id TEXT, has_reviewer INTEGER);
                INSERT INTO meta VALUES ('facts', '1'), ('docs', '0');
                INSERT INTO facts VALUES ('fixture:one', 'code', 'official-sdk');
                INSERT INTO source_runs VALUES ('run-1', 'official-sdk', 'collector');
                INSERT INTO source_provenance VALUES ('official-sdk', 1);
                """
            )
            conn.commit()

    def _doctor_json(self):
        with mock.patch.object(query, "DB", self.db), \
             mock.patch.dict("os.environ", {"EXTERACONTEXT_DB": str(self.db)}), \
             mock.patch.object(query.ks, "stats", return_value={"available": False}), \
             contextlib.redirect_stdout(io.StringIO()) as stdout:
            query.command_doctor(None)
        return json.loads(stdout.getvalue())

    def test_reports_resolved_path_size_hash_and_preserves_base_bytes(self):
        before = self.db.read_bytes()
        expected_hash = hashlib.sha256(before).hexdigest()

        result = self._doctor_json()

        self.assertEqual(result["db_path"], str(self.db.resolve()))
        self.assertEqual(result["db_size_bytes"], len(before))
        self.assertEqual(result["db_sha256"], expected_hash)
        self.assertEqual(self.db.read_bytes(), before)

    def test_detects_file_change_during_hash(self):
        original_identity = query._stat_identity
        calls = 0

        def mutate_before_second_stat(path):
            nonlocal calls
            calls += 1
            if calls == 4:
                with path.open("ab") as stream:
                    stream.write(b"changed-during-hash")
            return original_identity(path)

        with mock.patch.object(query, "DB", self.db), \
             mock.patch.dict("os.environ", {"EXTERACONTEXT_DB": str(self.db)}), \
             mock.patch.object(query.ks, "stats", return_value={"available": False}), \
             mock.patch.object(query, "_stat_identity", side_effect=mutate_before_second_stat), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "changed while hashing"):
                query.command_doctor(None)
        self.assertEqual(calls, 4)

    def test_rejects_nonempty_sqlite_sidecars(self):
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix):
                sidecar = Path(str(self.db) + suffix)
                sidecar.write_bytes(b"pending sqlite state")
                try:
                    with self.assertRaisesRegex(ValueError, "nonempty SQLite"):
                        self._doctor_json()
                finally:
                    sidecar.unlink(missing_ok=True)

    def test_allows_empty_sqlite_sidecars(self):
        sidecars = [Path(str(self.db) + suffix) for suffix in ("-wal", "-shm", "-journal")]
        for sidecar in sidecars:
            sidecar.touch()
        try:
            result = self._doctor_json()
            self.assertEqual(result["db_path"], str(self.db.resolve()))
        finally:
            for sidecar in sidecars:
                sidecar.unlink(missing_ok=True)

    def test_rejects_replacement_between_connection_metadata_and_hash(self):
        original_hash = query._hash_database

        def replace_before_hash(path, expected_identity=None):
            replacement = path.with_name("replacement.sqlite")
            shutil.copy2(path, replacement)
            os.replace(replacement, path)
            return original_hash(path, expected_identity=expected_identity)

        with (
            mock.patch.object(query, "DB", self.db),
            mock.patch.dict("os.environ", {"EXTERACONTEXT_DB": str(self.db)}),
            mock.patch.object(query.ks, "stats", return_value={"available": False}),
            mock.patch.object(query, "_hash_database", side_effect=replace_before_hash),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(RuntimeError, "changed after it was read"):
                query.command_doctor(None)

    def test_rejects_atomic_path_replacement_after_open_before_streaming(self):
        expected_identity = query._stat_identity(self.db)
        original_open = Path.open
        replaced = False

        if os.name == "nt":
            # Windows commonly denies os.replace() while the target is open.
            # Return an open descriptor for a different temporary SQLite file
            # instead; _hash_database must reject its fstat identity before streaming.
            other_db = Path(self.temp.name) / "different-corpus.sqlite"
            with contextlib.closing(sqlite3.connect(other_db)) as conn:
                conn.execute("CREATE TABLE identity_probe (value TEXT)")
                conn.commit()

            def open_different_database(path, *args, **kwargs):
                nonlocal replaced
                if path == self.db and not replaced:
                    replaced = True
                    return original_open(other_db, *args, **kwargs)
                return original_open(path, *args, **kwargs)

            patched_open = open_different_database
        else:
            # POSIX permits replacing a path while its old inode remains open.
            def open_then_replace(path, *args, **kwargs):
                nonlocal replaced
                source = original_open(path, *args, **kwargs)
                if path == self.db and not replaced:
                    replacement = self.db.with_name("replacement-during-open.sqlite")
                    shutil.copy2(self.db, replacement)
                    try:
                        os.replace(replacement, self.db)
                    except BaseException:
                        source.close()
                        raise
                    replaced = True
                return source

            patched_open = open_then_replace

        with mock.patch.object(Path, "open", new=patched_open):
            with self.assertRaisesRegex(RuntimeError, "changed while hashing"):
                query._hash_database(self.db, expected_identity=expected_identity)

        self.assertTrue(replaced)


if __name__ == "__main__":
    unittest.main()
