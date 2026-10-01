#!/usr/bin/env python3
"""Auto-sync invokes only the trusted pinned-artifact updater when opted in."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import query  # noqa: E402


class AutoSyncContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="exteracontext-auto-sync-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "missing.sqlite"
        self.wiki = self.root / "wiki"
        self.wiki.mkdir()
        self.lock = self.root / "KNOWLEDGE_LOCK"
        self.lock.write_text("70f6f614227c8b02d241e7b1e72a0b6691442fd1" + chr(10), encoding="utf-8")
        self.env_patch = patch.dict(os.environ, {}, clear=False)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        os.environ.pop("EXTERACONTEXT_AUTO_SYNC", None)
        os.environ.pop("EXTERACONTEXT_AUTO_BUILD", None)
        os.environ.pop("EXTERACONTEXT_KNOWLEDGE_LOCK", None)
        self.patches = [
            patch.object(query, "DB", self.db),
            patch.object(query, "WIKI", self.wiki),
            patch.object(query, "SKILL_ROOT", self.root),
            patch.object(query, "_AUTO_SYNC_DONE", False),
            patch("subprocess.run"),
        ]
        started = []
        for item in self.patches:
            started.append(item.start())
            self.addCleanup(item.stop)
        self.mock_subprocess = started[-1]
        def successful_update(*_args, **_kwargs):
            self.db.parent.mkdir(parents=True, exist_ok=True)
            self.db.touch()
        self.mock_subprocess.side_effect = successful_update

    def test_default_has_no_network_sync_or_build(self):
        with self.assertRaises(FileNotFoundError) as raised:
            query.ensure_db()
        self.assertIn("--source <existing.sqlite>", str(raised.exception))
        self.mock_subprocess.assert_not_called()
        self.assertFalse(self.db.exists())

    def test_auto_sync_invokes_only_hash_pinned_updater(self):
        os.environ["EXTERACONTEXT_AUTO_SYNC"] = "1"
        query.ensure_db()
        self.mock_subprocess.assert_called_once_with(
            [sys.executable, str(self.root / "scripts" / "update_knowledge.py"),
             "--db", str(self.db), "--lock", str(self.root / "KNOWLEDGE_LOCK")],
            check=True, stdout=subprocess.DEVNULL,
        )
        lock = self.lock
        self.assertEqual(lock.read_text(encoding="utf-8").strip(), "70f6f614227c8b02d241e7b1e72a0b6691442fd1")
        self.assertTrue(query._AUTO_SYNC_DONE)
        # A successful trusted updater leaves the validated database available.
        self.assertTrue(self.db.is_file())

    def test_auto_sync_fails_closed_when_updater_rejects_unpublished_legacy_pin(self):
        os.environ["EXTERACONTEXT_AUTO_SYNC"] = "true"
        self.mock_subprocess.side_effect = __import__("subprocess").CalledProcessError(1, "updater")
        with self.assertRaisesRegex(RuntimeError, "trusted, hash-pinned Knowledge updater"):
            query.ensure_db()
        self.assertFalse(query._AUTO_SYNC_DONE)
        self.assertFalse(self.db.exists())

    def test_auto_sync_runs_even_if_database_exists_to_validate_current_lock(self):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("CREATE TABLE marker(value TEXT)")
            con.commit()
        os.environ["EXTERACONTEXT_AUTO_SYNC"] = "yes"
        query.ensure_db()
        self.mock_subprocess.assert_called_once()

    def test_successful_auto_sync_is_once_per_process(self):
        os.environ["EXTERACONTEXT_AUTO_SYNC"] = "on"
        query.ensure_db()
        query.ensure_db()
        self.mock_subprocess.assert_called_once()

    def test_non_opt_in_existing_database_does_not_sync(self):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("CREATE TABLE marker(value TEXT)")
            con.execute("INSERT INTO marker VALUES ('existing corpus')")
            con.commit()
        os.environ["EXTERACONTEXT_AUTO_SYNC"] = "0"
        con = query.con()
        try:
            self.assertEqual(con.execute("SELECT value FROM marker").fetchone()[0], "existing corpus")
        finally:
            con.close()
        self.mock_subprocess.assert_not_called()


if __name__ == "__main__":
    unittest.main()
