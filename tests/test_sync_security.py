#!/usr/bin/env python3
"""Offline-only, disposable SQLite fixtures; no installed corpus or network access."""
from contextlib import closing
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync_knowledge.py"


def make_corpus(path: Path, *, full: bool = True) -> None:
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE docs(path TEXT, title TEXT, kind TEXT, content TEXT)")
        db.execute("INSERT INTO docs VALUES ('x', 'title', 'code', 'text')")
        if full:
            db.execute("CREATE TABLE facts(id TEXT, topic TEXT, claim TEXT, api TEXT, evidence_url TEXT, evidence_path TEXT, version TEXT, status TEXT, recipe TEXT, source_id TEXT, source_fact_id TEXT, original_status TEXT, canonical_topic TEXT, review_status TEXT, platform TEXT)")
            db.execute("INSERT INTO facts(id,claim) VALUES ('f', 'claim')")
            db.execute("CREATE TABLE meta(key TEXT, value TEXT)")
            db.execute("INSERT INTO meta VALUES ('facts', '1')")
            db.execute("CREATE TABLE source_runs(call_id TEXT, source_id TEXT, role TEXT)")
            db.execute("CREATE TABLE source_provenance(source_id TEXT, collector_runs INTEGER, reviewer_runs INTEGER, has_collector INTEGER, has_reviewer INTEGER)")
            db.execute("CREATE VIRTUAL TABLE docs_fts USING fts5(title, content)")
            db.execute("CREATE VIRTUAL TABLE facts_fts USING fts5(claim)")
        db.commit()


class OfflineBootstrapSecurity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "installed.sqlite"
        self.db = self.root / "deployment" / "exteracontext.sqlite"

    def run_sync(self, *extra):
        return subprocess.run([sys.executable, str(SCRIPT), "--source", str(self.source), "--db", str(self.db), *extra], capture_output=True, text=True, timeout=15)

    def test_copy_integrity_manifest_and_source_unchanged(self):
        make_corpus(self.source)
        original = self.source.read_bytes()
        result = self.run_sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.db.read_bytes(), original)
        manifest = json.loads((self.db.parent / ".knowledge-manifest.json").read_text("utf-8"))
        self.assertEqual(manifest["source_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(manifest["schema"]["counts"]["facts"], 1)
        self.assertFalse(manifest["commit_verified"])
        self.assertFalse((self.db.parent / ".knowledge-ref").exists())
        self.assertEqual(self.run_sync().returncode, 0)

    def test_reject_nonempty_wal_without_creating_destination(self):
        make_corpus(self.source)
        Path(str(self.source) + "-wal").write_bytes(b"pending transactions")
        result = self.run_sync()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nonempty SQLite -wal", result.stderr)
        self.assertFalse(self.db.exists())

    def test_reject_nonempty_shm_before_destination_changes(self):
        make_corpus(self.source)
        self.db.parent.mkdir(parents=True)
        self.db.write_bytes(b"previous destination")
        Path(str(self.source) + "-shm").write_bytes(b"active shared memory")
        result = self.run_sync()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nonempty SQLite -shm", result.stderr)
        self.assertEqual(self.db.read_bytes(), b"previous destination")
        self.assertFalse((self.db.parent / ".knowledge-manifest.json").exists())

    def test_zero_length_shm_does_not_block_static_snapshot(self):
        make_corpus(self.source)
        Path(str(self.source) + "-shm").touch()
        self.assertEqual(self.run_sync().returncode, 0)

    def test_zero_length_wal_does_not_block_static_snapshot(self):
        make_corpus(self.source)
        Path(str(self.source) + "-wal").touch()
        self.assertEqual(self.run_sync().returncode, 0)

    def test_reject_invalid_schema_preserving_existing_destination(self):
        make_corpus(self.source, full=False)
        self.db.parent.mkdir()
        self.db.write_bytes(b"previous")
        result = self.run_sync()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing corpus tables", result.stderr)
        self.assertEqual(self.db.read_bytes(), b"previous")
        self.assertFalse((self.db.parent / ".knowledge-manifest.json").exists())

    def test_reject_corrupt_database(self):
        self.source.write_bytes(b"not sqlite")
        result = self.run_sync()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.db.exists())

    def test_no_remote_build_interface(self):
        make_corpus(self.source)
        for option in ("--repo", "--ref"):
            with self.subTest(option=option):
                result = self.run_sync(option, "https://example.invalid/evil")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.db.exists())

    def test_reject_source_destination_alias(self):
        make_corpus(self.source)
        result = subprocess.run([sys.executable, str(SCRIPT), "--source", str(self.source), "--db", str(self.source)], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.source.exists())


if __name__ == "__main__":
    unittest.main()
