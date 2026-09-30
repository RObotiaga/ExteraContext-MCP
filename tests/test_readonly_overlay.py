#!/usr/bin/env python3
"""Regression coverage for optional read-only overlays and evidence direction."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import knowledge_store as ks  # noqa: E402
import query  # noqa: E402


TARGET = {"client": "ExteraGram", "platform": "Android", "client_version": "12.10.1", "sdk_version": "1.4.5.5"}


class ReadOnlyOverlayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "overlay.sqlite"

    def _claim(self, evidence, status="code"):
        collector = ks.create_run("collector", db_path=self.db)
        verifier = ks.create_run("verifier", db_path=self.db)
        claim = ks.propose_claim(
            run_id=collector, statement="on_send_message_hook account routing", kind="api",
            scope="target", evidence_status=status, api_symbol="on_send_message_hook",
            evidence=evidence, db_path=self.db, **TARGET,
        )
        ks.phase_a(verifier_run_id=verifier, claim_id=claim, statement="Independent source check",
                   scope=TARGET, evidence_status=status, db_path=self.db)
        ks.phase_b(verifier_run_id=verifier, claim_id=claim, verdict="accept", db_path=self.db)
        return claim, ks.commit_claim(claim, verifier, db_path=self.db)

    def test_absent_overlay_does_not_create_file_or_parent(self):
        missing = Path(self.tmp.name) / "missing" / "knowledge.sqlite"
        self.assertIsNone(ks.get_claim("claim", missing))
        self.assertEqual(ks.search_verified("message", db_path=missing), [])
        self.assertEqual(ks.api_search("message", db_path=missing), [])
        self.assertEqual(ks.find_duplicates("message", db_path=missing), [])
        self.assertFalse(ks.stats(missing)["available"])
        self.assertFalse(missing.parent.exists())
        with patch.object(ks, "KNOWLEDGE_DB", missing):
            self.assertEqual(query.search_dynamic_facts("message"), [])

    def test_read_methods_leave_existing_database_unchanged(self):
        claim, _ = self._claim([{"evidence_status": "code", "repository": "source", "path": "base.py", **TARGET}])
        before = self.db.read_bytes()
        with patch.object(ks, "init_db", side_effect=AssertionError("read called init_db")), \
             patch.object(ks, "connect", side_effect=AssertionError("read called writable connect")):
            self.assertEqual(ks.get_claim(claim, self.db)["id"], claim)
            self.assertEqual(ks.find_duplicates("on_send_message_hook account routing", db_path=self.db)[0]["id"], claim)
            self.assertEqual(ks.search_verified("on_send_message_hook", db_path=self.db)[0]["id"], claim)
            self.assertEqual(ks.api_search("on_send_message_hook", db_path=self.db)[0]["id"], claim)
            self.assertEqual(ks.stats(self.db)["claims"], 1)
        self.assertEqual(before, self.db.read_bytes())
        c = ks.read_connect(self.db)
        try:
            with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                c.execute("UPDATE knowledge_claims SET statement='changed' WHERE id=?", (claim,))
        finally:
            c.close()

    def test_corruption_and_schema_mismatch_are_not_absence(self):
        self.db.write_bytes(b"not a sqlite database")
        for read in (lambda: ks.get_claim("x", self.db),
                     lambda: ks.search_verified("message", db_path=self.db),
                     lambda: ks.api_search("message", db_path=self.db),
                     lambda: ks.find_duplicates("message", db_path=self.db),
                     lambda: ks.stats(self.db)):
            with self.assertRaises(sqlite3.DatabaseError):
                read()
        self.db.unlink()
        ks.init_db(self.db)
        c = sqlite3.connect(self.db)
        c.execute("UPDATE knowledge_meta SET value='99' WHERE key='schema_version'")
        c.commit(); c.close()
        with self.assertRaisesRegex(ValueError, "schema version"):
            ks.stats(self.db)
        with self.assertRaisesRegex(ValueError, "schema version"):
            ks.api_search("message", db_path=self.db)

    def test_query_surfaces_overlay_errors(self):
        base = Path(self.tmp.name) / "base.sqlite"
        c = sqlite3.connect(base)
        c.executescript("""
            CREATE TABLE facts (id TEXT, api TEXT, claim TEXT, status TEXT, source_id TEXT);
            CREATE VIRTUAL TABLE facts_fts USING fts5(id UNINDEXED, claim);
            CREATE TABLE meta (key TEXT, value TEXT);
        """)
        c.close()
        self.db.write_bytes(b"not a sqlite database")
        with patch.object(ks, "KNOWLEDGE_DB", self.db), patch.object(query, "DB", base):
            for read in (lambda: query.search_dynamic_facts("message"),
                         lambda: query.api_lookup("message"),
                         lambda: query.command_evidence(query.argparse.Namespace(key="knowledge:x", limit=2, format="json")),
                         lambda: query.command_doctor(query.argparse.Namespace())):
                with self.assertRaises(sqlite3.DatabaseError):
                    read()

    def test_conflicts_cannot_promote_and_runtime_fail_downgrades(self):
        claim, committed = self._claim([
            {"evidence_status": "code", "relation": "supports", "source_type": "target-code", "path": "base.py", **TARGET},
            {"evidence_status": "runtime-verified", "relation": "conflicts", "source_type": "donor", "path": "conflict.py"},
        ])
        self.assertEqual(committed["effective_evidence_status"], "code")
        self.assertEqual(ks.search_verified("on_send_message_hook", db_path=self.db)[0]["top_evidence"]["path"], "base.py")
        runtime = ks.create_run("runtime", db_path=self.db)
        ks.record_runtime_result(run_id=runtime, subject_type="claim", subject_id=claim,
                                 passed=True, test_id="pass", db_path=self.db)
        self.assertEqual(ks.get_claim(claim, self.db)["effective_evidence_status"], "runtime-verified")
        ks.record_runtime_result(run_id=runtime, subject_type="claim", subject_id=claim,
                                 passed=False, test_id="fail", db_path=self.db)
        self.assertEqual(ks.get_claim(claim, self.db)["effective_evidence_status"], "code")
        self.assertEqual(ks.api_search("on_send_message_hook", db_path=self.db)[0]["top_evidence"]["path"], "base.py")
        ks.record_runtime_result(run_id=runtime, subject_type="claim", subject_id=claim,
                                 passed=True, test_id="another-pass", db_path=self.db)
        self.assertEqual(ks.get_claim(claim, self.db)["effective_evidence_status"], "code")
        c = ks.connect(self.db)
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "runtime failure"):
                c.execute("UPDATE knowledge_claims SET effective_evidence_status='runtime-verified' WHERE id=?", (claim,))
        finally:
            c.close()

    def test_only_conflicting_evidence_does_not_promote(self):
        claim, committed = self._claim([
            {"evidence_status": "runtime-verified", "relation": "supports", "source_type": "target-code", "path": "base.py", **TARGET},
            {"evidence_status": "runtime-verified", "relation": "refutes", "evidence_type": "runtime-test", "path": "negative.py", "metadata": {"passed": 0}, **TARGET},
        ], status="runtime-verified")
        self.assertEqual(committed["effective_evidence_status"], "inference")
        self.assertNotIn("top_evidence", ks.api_search("on_send_message_hook", db_path=self.db)[0])


if __name__ == "__main__":
    unittest.main()
