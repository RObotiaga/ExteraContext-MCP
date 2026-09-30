"""Focused path/symlink guards for local Mode-C corpus attestation."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmark" / "agent-v1"))
import build_frozen_corpus as frozen  # noqa: E402


class FrozenCorpusPathSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make_symlink(self, link: Path, target: Path, *, target_is_directory: bool = False) -> None:
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except (OSError, NotImplementedError) as exc:
            # Skip only Windows' explicit privilege-not-held error. Generic access
            # denied and all other failures must fail rather than hide test issues.
            if os.name == "nt" and isinstance(exc, OSError) and getattr(exc, "winerror", None) == 1314:
                self.skipTest(f"Windows symlink privilege unavailable (winerror={exc.winerror})")
            raise

    def test_database_symlink_is_rejected_before_resolve(self):
        actual = self.root / "actual.sqlite"
        actual.write_bytes(b"not opened because link must be rejected first")
        link = self.root / "database-link.sqlite"
        self.make_symlink(link, actual)
        self.assertTrue(link.is_symlink())
        with self.assertRaisesRegex(ValueError, "database must not be a symlink"):
            frozen.inspect_database(link)

    def test_hmac_key_symlink_is_rejected_before_resolve(self):
        actual = self.root / "trusted.key"
        actual.write_bytes(b"K" * 32)
        link = self.root / "key-link"
        self.make_symlink(link, actual)
        self.assertTrue(link.is_symlink())
        with self.assertRaisesRegex(ValueError, "HMAC key must not be a symlink"):
            frozen._key_bytes(link)

    def test_repository_symlink_is_rejected_before_resolve(self):
        actual = self.root / "repository"
        actual.mkdir()
        link = self.root / "repository-link"
        self.make_symlink(link, actual, target_is_directory=True)
        self.assertTrue(link.is_symlink())
        with self.assertRaisesRegex(ValueError, "repository must not be a symlink"):
            frozen.inspect_commit(link)

    def test_database_and_key_require_regular_files(self):
        directory = self.root / "directory"
        directory.mkdir()
        with self.assertRaisesRegex(ValueError, "database must be an existing regular file"):
            frozen.inspect_database(directory)
        with self.assertRaisesRegex(ValueError, "HMAC key must be an existing regular file"):
            frozen._key_bytes(directory)

    def test_repository_requires_directory(self):
        source_file = self.root / "not-a-repository"
        source_file.write_text("ordinary file", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "repository must be an existing directory"):
            frozen.inspect_commit(source_file)


if __name__ == "__main__":
    unittest.main()
