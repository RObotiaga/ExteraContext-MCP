#!/usr/bin/env python3
"""SQLite handles must be closed and failed writes rolled back on Windows."""
from __future__ import annotations

import contextlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import knowledge_store as ks  # noqa: E402


class ConnectionCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "overlay.sqlite"
        self.collector = ks.create_run("collector", db_path=self.db)
        self.verifier = ks.create_run("verifier", db_path=self.db)

    def claim(self, evidence=None):
        return ks.propose_claim(
            run_id=self.collector, statement="A source-backed hook", kind="behavior",
            scope="project", evidence_status="code",
            evidence=[{"repository": "project", "path": "hook.py"}] if evidence is None else evidence,
            db_path=self.db,
        )

    def review(self, claim, verdict="accept"):
        ks.phase_a(verifier_run_id=self.verifier, claim_id=claim, statement="Blind extraction",
                   scope={}, evidence_status="code", db_path=self.db)
        ks.phase_b(verifier_run_id=self.verifier, claim_id=claim, verdict=verdict, db_path=self.db)

    def removable(self):
        # No gc.collect(): an abandoned sqlite3.Connection must not pass by finalization.
        self.db.unlink()
        self.assertFalse(self.db.exists())

    def test_phase_a_guard_closes_connection_immediately(self):
        claim = ks.propose_claim(
            run_id=self.collector, statement="Target-specific hook", kind="behavior",
            scope="target", client="ExteraGram", evidence_status="code",
            evidence=[{"repository": "donor", "client": "AyuGram"}], db_path=self.db,
        )
        with self.assertRaisesRegex(ValueError, "explicit matching source target"):
            ks.phase_a(verifier_run_id=self.verifier, claim_id=claim, statement="Overbroad",
                       scope={"client": "ExteraGram"}, evidence_status="code", db_path=self.db)
        self.assertEqual(ks.get_claim(claim, self.db)["verifications"], [])
        self.removable()

    def test_commit_support_guard_closes_connection_immediately(self):
        claim = self.claim(evidence=[])
        self.review(claim)
        with self.assertRaisesRegex(ValueError, "linked supporting evidence"):
            ks.commit_claim(claim, self.verifier, db_path=self.db)
        self.assertEqual(ks.get_claim(claim, self.db)["state"], "candidate")
        self.removable()

    def test_unknown_run_and_claim_guards_close_connection(self):
        with self.assertRaisesRegex(ValueError, "unknown run"):
            ks.propose_claim(run_id="missing", statement="Missing", kind="behavior", scope="project",
                             evidence_status="code", db_path=self.db)
        with self.assertRaisesRegex(ValueError, "unknown claim"):
            ks.phase_a(verifier_run_id=self.verifier, claim_id="missing", statement="Blind",
                       scope={}, evidence_status="code", db_path=self.db)
        with self.assertRaisesRegex(ValueError, "unknown claim"):
            ks.commit_claim("missing", self.verifier, db_path=self.db)
        self.removable()

    def test_propose_invalid_later_evidence_rolls_back_claim_and_closes(self):
        with self.assertRaisesRegex(ValueError, "invalid evidence.evidence_status"):
            self.claim(evidence=[{"repository": "first"}, {"evidence_status": "invalid"}])
        self.assertEqual(ks.stats(self.db)["claims"], 0)
        self.assertEqual(ks.stats(self.db)["evidence"], 0)
        self.removable()

    def test_trigger_abort_rolls_back_evidence_insert_and_closes(self):
        claim = self.claim()
        runtime = ks.create_run("runtime", db_path=self.db)
        with contextlib.closing(sqlite3.connect(self.db)) as c:
            c.execute("CREATE TRIGGER reject_link BEFORE INSERT ON knowledge_links "
                      "BEGIN SELECT RAISE(ABORT, 'injected link failure'); END")
        before = ks.stats(self.db)["evidence"]
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected link failure"):
            ks.add_evidence(run_id=runtime, subject_type="claim", subject_id=claim,
                            evidence_status="code", evidence_type="runtime-test", db_path=self.db)
        self.assertEqual(ks.stats(self.db)["evidence"], before)
        self.removable()

    def test_trigger_abort_rolls_back_verdict_and_closes(self):
        claim = self.claim()
        ks.phase_a(verifier_run_id=self.verifier, claim_id=claim, statement="Blind extraction",
                   scope={}, evidence_status="code", db_path=self.db)
        with contextlib.closing(sqlite3.connect(self.db)) as c:
            c.execute("CREATE TRIGGER reject_verdict BEFORE UPDATE OF verdict ON knowledge_verifications "
                      "BEGIN SELECT RAISE(ABORT, 'injected verdict failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected verdict failure"):
            ks.phase_b(verifier_run_id=self.verifier, claim_id=claim, verdict="accept", db_path=self.db)
        self.assertIsNone(ks.get_claim(claim, self.db)["verifications"][0]["verdict"])
        self.removable()

    def test_read_paths_close_on_query_or_helper_exception(self):
        claim = self.claim()
        self.review(claim)
        ks.commit_claim(claim, self.verifier, db_path=self.db)
        cases = [
            (ks.get_claim, (claim,), {"claim_row_to_dict": ValueError("injected reader failure")}),
            (ks.find_duplicates, ("source-backed hook",), {"normalize_statement": ValueError("injected reader failure")}),
            (ks.search_verified, ("hook",), {"_top_supporting_evidence": ValueError("injected reader failure")}),
            (ks.api_search, ("hook",), {"_top_supporting_evidence": ValueError("injected reader failure")}),
        ]
        # Run each against a distinct database so each failure proves immediate removability.
        for reader, args, mocks in cases:
            with self.subTest(reader=reader.__name__):
                name, error = next(iter(mocks.items()))
                with patch.object(ks, name, side_effect=error):
                    with self.assertRaisesRegex(ValueError, "injected reader failure"):
                        reader(*args, db_path=self.db)
                # Recreate a valid DB for the next reader without relying on gc.
                self.removable()
                self.collector = ks.create_run("collector", db_path=self.db)
                self.verifier = ks.create_run("verifier", db_path=self.db)
                claim = self.claim()
                self.review(claim)
                ks.commit_claim(claim, self.verifier, db_path=self.db)
        self.removable()

    def test_stats_closes_on_query_exception(self):
        with contextlib.closing(sqlite3.connect(self.db)) as c:
            c.execute("DROP TABLE knowledge_conflicts")
            c.commit()
        with self.assertRaises((sqlite3.OperationalError, ValueError)):
            ks.stats(self.db)
        self.removable()

    def test_read_connect_failure_closes_invalid_schema(self):
        with contextlib.closing(sqlite3.connect(self.db)) as c:
            c.execute("UPDATE knowledge_meta SET value='unsupported' WHERE key='schema_version'")
            c.commit()
        with self.assertRaisesRegex(ValueError, "unsupported knowledge overlay schema"):
            ks.read_connect(self.db)
        self.removable()


if __name__ == "__main__":
    unittest.main()
