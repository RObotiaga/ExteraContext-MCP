#!/usr/bin/env python3
"""Fail-closed provenance checks, using temporary SQLite databases only."""
from __future__ import annotations

import gc
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import knowledge_store as ks  # noqa: E402


class ProvenanceScopeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(tmp.name)
        self.db = self.root / "overlay.sqlite"
        self.collector = ks.create_run("collector", db_path=self.db)
        self.verifier = ks.create_run("verifier", db_path=self.db)

    def propose(self, *, scope="target", evidence=(), **target):
        return ks.propose_claim(
            run_id=self.collector, statement="Hook behavior", kind="behavior", scope=scope,
            evidence_status="code", evidence=evidence, db_path=self.db, **target,
        )

    def review(self, claim, scope, verdict="accept", **kwargs):
        ks.phase_a(verifier_run_id=self.verifier, claim_id=claim, statement="Source analysis",
                   scope=scope, evidence_status="code", db_path=self.db)
        ks.phase_b(verifier_run_id=self.verifier, claim_id=claim, verdict=verdict,
                   db_path=self.db, **kwargs)
        return ks.commit_claim(claim, self.verifier, db_path=self.db)

    def test_donor_not_relabelled_as_target(self):
        donor = {"repository": "donor/sdk", "path": "sdk.py", "client": "AyuGram", "platform": "Android"}
        claim = self.propose(client="ExteraGram", platform="Android", evidence=[donor])
        stored = ks.get_claim(claim, self.db)["evidence"][0]
        self.assertEqual(stored["client"], "AyuGram")
        with self.assertRaisesRegex(ValueError, "explicit matching source target"):
            ks.phase_a(verifier_run_id=self.verifier, claim_id=claim, statement="Wrong target",
                       scope={"client": "ExteraGram"}, evidence_status="code", db_path=self.db)
        self.review_rejected_target(claim)

    def review_rejected_target(self, claim):
        ks.phase_a(verifier_run_id=self.verifier, claim_id=claim, statement="Donor only",
                   scope={}, evidence_status="code", db_path=self.db)
        ks.phase_b(verifier_run_id=self.verifier, claim_id=claim, verdict="accept", db_path=self.db)
        with self.assertRaisesRegex(ValueError, "phase A must independently establish"):
            ks.commit_claim(claim, self.verifier, db_path=self.db)
        self.assertEqual(ks.get_claim(claim, self.db)["state"], "candidate")

    def test_target_fields_not_inherited_even_from_unscoped_source(self):
        claim = self.propose(client="ExteraGram", evidence=[{"repository": "unscoped", "path": "api.py"}])
        self.assertIsNone(ks.get_claim(claim, self.db)["evidence"][0]["client"])
        with self.assertRaisesRegex(ValueError, "explicit matching source target"):
            ks.phase_a(verifier_run_id=self.verifier, claim_id=claim, statement="Overbroad",
                       scope={"client": "ExteraGram"}, evidence_status="code", db_path=self.db)
        self.review_rejected_target(claim)

    def test_phase_a_cannot_expand_project_or_target_scope(self):
        project = self.propose(scope="project", evidence=[{"repository": "project"}])
        with self.assertRaisesRegex(ValueError, "project claim cannot assert target"):
            ks.phase_a(verifier_run_id=self.verifier, claim_id=project, statement="Expanded",
                       scope={"scope": "target"}, evidence_status="code", db_path=self.db)
        target = self.propose(client="ExteraGram", evidence=[{"repository": "target", "client": "ExteraGram"}])
        for overbroad in ({"scope": "global"}, {"client": "AyuGram"}, {"client_version": "99"}):
            with self.subTest(overbroad=overbroad), self.assertRaisesRegex(ValueError, "scope|client"):
                ks.phase_a(verifier_run_id=self.verifier, claim_id=target, statement="Expanded",
                           scope=overbroad, evidence_status="code", db_path=self.db)

    def test_accept_requires_support_and_complete_explicit_target(self):
        empty = self.propose(scope="project")
        ks.phase_a(verifier_run_id=self.verifier, claim_id=empty, statement="No source", scope={},
                   evidence_status="inference", db_path=self.db)
        ks.phase_b(verifier_run_id=self.verifier, claim_id=empty, verdict="accept", db_path=self.db)
        with self.assertRaisesRegex(ValueError, "linked supporting evidence"):
            ks.commit_claim(empty, self.verifier, db_path=self.db)
        donor = self.propose(client="ExteraGram", client_version="12", evidence=[
            {"repository": "ExteraGram/sdk", "client": "ExteraGram"}])
        with self.assertRaisesRegex(ValueError, "explicit matching source target"):
            ks.phase_a(verifier_run_id=self.verifier, claim_id=donor, statement="Partially scoped",
                       scope={"client": "ExteraGram", "client_version": "12"}, evidence_status="code", db_path=self.db)
        ks.phase_a(verifier_run_id=self.verifier, claim_id=donor, statement="Partially scoped",
                   scope={"client": "ExteraGram"}, evidence_status="code", db_path=self.db)
        ks.phase_b(verifier_run_id=self.verifier, claim_id=donor, verdict="accept", db_path=self.db)
        with self.assertRaisesRegex(ValueError, "phase A must independently establish"):
            ks.commit_claim(donor, self.verifier, db_path=self.db)
        valid = self.propose(client="ExteraGram", platform="Android", evidence=[
            {"repository": "target/source", "path": "hook.py", "client": "ExteraGram", "platform": "Android"}])
        result = self.review(valid, {"client": "ExteraGram", "platform": "Android"})
        self.assertEqual(result["state"], "verified")

    def test_attach_and_conflict_reject_unknown_and_deleted_subjects(self):
        for verdict in ("attach-evidence", "conflict"):
            with self.subTest(verdict=verdict):
                candidate = self.propose(scope="project", evidence=[{"repository": "source"}])
                ks.phase_a(verifier_run_id=self.verifier, claim_id=candidate, statement="Extracted",
                           scope={}, evidence_status="code", db_path=self.db)
                for subject_type in ("claim", "legacy_fact"):
                    with self.assertRaisesRegex(ValueError, "unknown|unavailable"):
                        with patch.dict(os.environ, {"EXTERACONTEXT_DB": str(self.root / "absent.sqlite")}):
                            ks.phase_b(verifier_run_id=self.verifier, claim_id=candidate, verdict=verdict,
                                       existing_subject_type=subject_type, existing_subject_id="absent", db_path=self.db)
                other = self.propose(scope="project", evidence=[{"repository": "source"}])
                ks.phase_b(verifier_run_id=self.verifier, claim_id=candidate, verdict=verdict,
                           existing_subject_type="claim", existing_subject_id=other, db_path=self.db)
                c = ks.connect(self.db)
                c.execute("DELETE FROM knowledge_claims WHERE id=?", (other,))
                c.commit(); c.close()
                with self.assertRaisesRegex(ValueError, "unknown existing claim"):
                    ks.commit_claim(candidate, self.verifier, db_path=self.db)
                self.assertEqual(ks.get_claim(candidate, self.db)["state"], "candidate")

    def test_legacy_fact_checked_in_immutable_base_read_only(self):
        base = self.root / "immutable.sqlite"
        c = sqlite3.connect(base)
        c.execute("CREATE TABLE facts(id TEXT PRIMARY KEY)")
        c.execute("INSERT INTO facts VALUES('known')")
        c.commit(); c.close()
        candidate = self.propose(scope="project", evidence=[{"repository": "source"}])
        ks.phase_a(verifier_run_id=self.verifier, claim_id=candidate, statement="Extracted",
                   scope={}, evidence_status="code", db_path=self.db)
        with patch.dict(os.environ, {"EXTERACONTEXT_DB": str(base)}):
            with self.assertRaisesRegex(ValueError, "unknown existing legacy_fact"):
                ks.phase_b(verifier_run_id=self.verifier, claim_id=candidate, verdict="conflict",
                           existing_subject_type="legacy_fact", existing_subject_id="unknown", db_path=self.db)
            ks.phase_b(verifier_run_id=self.verifier, claim_id=candidate, verdict="conflict",
                       existing_subject_type="legacy_fact", existing_subject_id="known", db_path=self.db)
            self.assertEqual(ks.commit_claim(candidate, self.verifier, db_path=self.db)["state"], "conflicting")


if __name__ == "__main__":
    unittest.main()
