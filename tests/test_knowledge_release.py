#!/usr/bin/env python3
"""Offline contract tests for published, hash-pinned Knowledge database releases."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import knowledge_release as release_cli  # noqa: E402
import update_knowledge as updater  # noqa: E402

COMMIT = "a" * 40


def create_corpus(path: Path) -> None:
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE docs(path TEXT, title TEXT, kind TEXT, content TEXT)")
        db.execute("INSERT INTO docs VALUES ('x', 'title', 'code', 'text')")
        db.execute("CREATE TABLE facts(id TEXT, topic TEXT, claim TEXT, api TEXT, evidence_url TEXT, evidence_path TEXT, version TEXT, status TEXT, recipe TEXT, source_id TEXT, source_fact_id TEXT, original_status TEXT, canonical_topic TEXT, review_status TEXT, platform TEXT)")
        db.execute("INSERT INTO facts(id,claim) VALUES ('f', 'claim')")
        db.execute("CREATE TABLE meta(key TEXT, value TEXT)")
        db.execute("INSERT INTO meta VALUES ('facts', '1')")
        db.execute("CREATE TABLE source_runs(call_id TEXT, source_id TEXT, role TEXT)")
        db.execute("CREATE TABLE source_provenance(source_id TEXT, collector_runs INTEGER, reviewer_runs INTEGER, has_collector INTEGER, has_reviewer INTEGER)")
        db.execute("CREATE VIRTUAL TABLE docs_fts USING fts5(title, content)")
        db.execute("CREATE VIRTUAL TABLE facts_fts USING fts5(claim)")
        db.commit()


class FakeResponse:
    def __init__(self, data: bytes, *, content_length: bool = True, chunk_size: int | None = None):
        self.data = data
        self.offset = 0
        self.chunk_size = chunk_size
        self.max_read = 0
        self.headers = {"Content-Length": str(len(data))} if content_length else {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size=-1):
        if size < 0:
            size = len(self.data) - self.offset
        if self.chunk_size is not None:
            size = min(size, self.chunk_size)
        self.max_read = max(self.max_read, size)
        chunk = self.data[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk


class FakeOpener:
    def __init__(self, assets: dict[str, bytes], corrupt: bool = False, *,
                 omit_content_length: bool = False, chunk_size: int | None = None,
                 oversized: bool = False, corrupt_manifest: bool = False):
        self.assets = assets
        self.corrupt = corrupt
        self.omit_content_length = omit_content_length
        self.chunk_size = chunk_size
        self.oversized = oversized
        self.corrupt_manifest = corrupt_manifest
        self.responses: list[FakeResponse] = []
        self.downloads = 0

    def response(self, data: bytes, *, api: bool = False) -> FakeResponse:
        response = FakeResponse(
            data,
            content_length=api or not self.omit_content_length,
            chunk_size=self.chunk_size,
        )
        self.responses.append(response)
        return response

    def open(self, request, timeout=None):
        url = request.full_url
        if "/releases/tags/" in url:
            payload = {
                "tag_name": f"knowledge-{COMMIT}", "draft": False, "prerelease": False,
                "assets": [
                    {"name": "exteracontext.sqlite", "digest": f"sha256:{hashlib.sha256(self.assets['exteracontext.sqlite']).hexdigest()}", "browser_download_url": f"https://github.com/RObotiaga/ExteraContext-MCP/releases/download/knowledge-{COMMIT}/exteracontext.sqlite"},
                    {"name": "manifest.json", "digest": f"sha256:{hashlib.sha256(self.assets['manifest.json']).hexdigest()}", "browser_download_url": f"https://github.com/RObotiaga/ExteraContext-MCP/releases/download/knowledge-{COMMIT}/manifest.json"},
                ],
            }
            return self.response(json.dumps(payload).encode(), api=True)
        self.downloads += 1
        name = "manifest.json" if url.endswith("manifest.json") else "exteracontext.sqlite"
        data = self.assets[name]
        if (self.corrupt or self.oversized) and name == "exteracontext.sqlite":
            data += b"altered"
        if self.corrupt_manifest and name == "manifest.json":
            data += b"altered"
        return self.response(data)


class KnowledgeReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="knowledge-release-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source_db = self.root / "candidate.sqlite"
        create_corpus(self.source_db)
        self.manifest_path = self.root / "manifest.json"
        self.manifest = updater.create_manifest(self.source_db, COMMIT)
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.lock_path = self.root / "KNOWLEDGE_LOCK"
        self.lock = release_cli.make_lock(self.manifest_path)
        self.lock_path.write_text(json.dumps(self.lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.assets = {"exteracontext.sqlite": self.source_db.read_bytes(), "manifest.json": self.manifest_path.read_bytes()}

    def test_privileged_publisher_hashes_bytes_and_only_parses_manifest_json(self):
        workflow = (ROOT / ".github" / "workflows" / "sync-knowledge.yml").read_text(encoding="utf-8")
        publisher = workflow.split("  publish-and-open-pr:", 1)[1]
        verify_step = publisher.split("      - name: Publish per-commit release assets", 1)[0]
        self.assertIn("contents: write", publisher)
        self.assertIn("pull-requests: write", publisher)
        self.assertIn("privileged publisher intentionally never opens/parses candidate SQLite", verify_step)
        self.assertNotIn("knowledge_release.py verify", verify_step)
        self.assertIn("--source-commit \"$SOURCE_COMMIT\"", verify_step)
        self.assertIn("--database-sha256 \"$EXPECTED_DB_SHA256\"", verify_step)
        self.assertIn("--manifest-sha256 \"$EXPECTED_MANIFEST_SHA256\"", verify_step)

    def test_create_manifest_binds_source_commit_database_schema_and_counts(self):
        self.assertEqual(self.manifest["source_commit"], COMMIT)
        self.assertEqual(self.manifest["database_sha256"], hashlib.sha256(self.assets["exteracontext.sqlite"]).hexdigest())
        self.assertEqual(self.manifest["schema"]["counts"]["facts"], 1)
        self.assertEqual(updater.verify_manifest(self.source_db, self.manifest_path, COMMIT), self.manifest)

    def test_create_manifest_rejects_oversize_before_sqlite_validation(self):
        with (
            patch.object(updater, "MAX_DB_BYTES", 1),
            patch.object(updater.sync_knowledge, "validate", side_effect=AssertionError("SQLite validation must not run")) as validate,
            self.assertRaisesRegex(ValueError, "exceeds the configured size limit"),
        ):
            updater.create_manifest(self.source_db, COMMIT)
        validate.assert_not_called()

    def test_create_manifest_rejects_symlinks_and_nonempty_sidecars_before_validation(self):
        validate = patch.object(updater.sync_knowledge, "validate", side_effect=AssertionError("SQLite validation must not run"))
        with validate as mocked_validate:
            link = self.root / "candidate-link.sqlite"
            try:
                link.symlink_to(self.source_db)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable on this platform")
            with self.assertRaisesRegex(ValueError, "must not be a symlink"):
                updater.create_manifest(link, COMMIT)
            sidecar = Path(str(self.source_db) + "-wal")
            sidecar.write_bytes(b"pending transaction")
            with self.assertRaisesRegex(ValueError, "nonempty SQLite -wal sidecar"):
                updater.create_manifest(self.source_db, COMMIT)
        mocked_validate.assert_not_called()

    def test_lock_generation_uses_only_bounded_json_and_binds_build_outputs(self):
        # The privileged lock path must not invoke SQLite against the candidate artifact.
        expected_manifest_sha = hashlib.sha256(self.assets["manifest.json"]).hexdigest()
        output = self.root / "privileged-lock.json"
        with patch("sqlite3.connect", side_effect=AssertionError("publisher opened SQLite")):
            result = release_cli.make_lock(
                self.manifest_path,
                expected_commit=COMMIT,
                expected_database_sha256=self.manifest["database_sha256"],
                expected_manifest_sha256=expected_manifest_sha,
            )
        self.assertEqual(result["source_commit"], COMMIT)
        self.assertEqual(result["database_sha256"], self.manifest["database_sha256"])
        self.assertEqual(result["manifest_sha256"], expected_manifest_sha)

    def test_lock_rejects_extra_count_key_with_recomputed_digests(self):
        malformed_manifest = json.loads(json.dumps(self.manifest))
        malformed_manifest["schema"]["counts"]["unrecognized_count"] = 0
        malformed_manifest["schema_sha256"] = updater.schema_digest(malformed_manifest["schema"])
        path = self.root / "extra-count.json"
        manifest_bytes = (json.dumps(malformed_manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        path.write_bytes(manifest_bytes)
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()

        with self.assertRaisesRegex(ValueError, "counts contain unexpected keys"):
            release_cli.make_lock(
                path,
                expected_commit=COMMIT,
                expected_database_sha256=malformed_manifest["database_sha256"],
                expected_manifest_sha256=manifest_digest,
            )

    def test_lock_rejects_build_output_commit_and_digest_mismatches(self):
        expected_manifest_sha = hashlib.sha256(self.assets["manifest.json"]).hexdigest()
        with self.assertRaisesRegex(ValueError, "source commit does not match"):
            release_cli.make_lock(
                self.manifest_path,
                expected_commit="b" * 40,
                expected_database_sha256=self.manifest["database_sha256"],
                expected_manifest_sha256=expected_manifest_sha,
            )
        with self.assertRaisesRegex(ValueError, "database SHA-256 does not match"):
            release_cli.make_lock(
                self.manifest_path,
                expected_commit=COMMIT,
                expected_database_sha256="b" * 64,
                expected_manifest_sha256=expected_manifest_sha,
            )
        with self.assertRaisesRegex(ValueError, "manifest bytes do not match"):
            release_cli.make_lock(
                self.manifest_path,
                expected_commit=COMMIT,
                expected_database_sha256=self.manifest["database_sha256"],
                expected_manifest_sha256="b" * 64,
            )

    def test_lock_rejects_oversized_or_incomplete_manifest_metadata(self):
        oversized = self.root / "oversized.json"
        oversized.write_bytes(b" " * (release_cli.MAX_MANIFEST_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "manifest exceeds"):
            release_cli.make_lock(oversized)
        broken = dict(self.manifest)
        broken["schema"] = {"counts": {"docs": 1, "facts": 1}, "meta": {}}
        broken["schema_sha256"] = updater.schema_digest(broken["schema"])
        malformed = self.root / "incomplete.json"
        malformed.write_text(json.dumps(broken), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "counts are incomplete"):
            release_cli.make_lock(malformed)

    def test_reject_manifest_for_other_commit_or_corrupt_database(self):
        with self.assertRaisesRegex(ValueError, "source commit"):
            updater.verify_manifest(self.source_db, self.manifest_path, "b" * 40)
        broken = self.root / "broken.sqlite"
        broken.write_bytes(b"not a database")
        with self.assertRaises((ValueError, sqlite3.Error)):
            updater.verify_manifest(broken, self.manifest_path, COMMIT)

    def test_legacy_lock_fails_closed_without_artifact_hashes(self):
        legacy = self.root / "legacy-lock"
        legacy.write_text(COMMIT + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no verified artifact hashes"):
            updater.load_lock(legacy)

    def test_lock_rejects_wrong_release_tag_and_untrusted_repository(self):
        bad = dict(self.lock, release_tag="knowledge-main")
        path = self.root / "bad-lock"
        path.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "content-addressed"):
            updater.load_lock(path)
        bad = dict(self.lock, artifact_repository="attacker/repository")
        path.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "repository identity"):
            updater.load_lock(path)

    def test_deploy_downloads_only_exact_asset_and_hash_pins_before_replace(self):
        opener = FakeOpener(self.assets)
        deployed = self.root / "deployment" / "exteracontext.sqlite"
        with patch.object(updater, "opener", return_value=opener):
            self.assertTrue(updater.deploy(deployed, self.lock_path))
            self.assertEqual(deployed.read_bytes(), self.assets["exteracontext.sqlite"])
            self.assertEqual(updater.sha256_file(deployed), self.lock["database_sha256"])
            self.assertEqual(opener.downloads, 2)
            self.assertFalse(updater.deploy(deployed, self.lock_path))
            self.assertEqual(opener.downloads, 2)

    @unittest.skipUnless(os.name == "posix", "POSIX mode and nanosecond timestamp preservation")
    def test_successful_replacement_inherits_existing_file_modes_and_timestamps(self):
        deployed = self.root / "metadata-success" / "exteracontext.sqlite"
        manifest_path = deployed.parent / ".knowledge-manifest.json"
        deployed.parent.mkdir(parents=True)
        deployed.write_bytes(b"old database")
        manifest_path.write_bytes(b"old manifest")
        os.chmod(deployed, 0o640)
        os.chmod(manifest_path, 0o604)
        timestamp_ns = 1_600_000_000_123_456_789
        os.utime(deployed, ns=(timestamp_ns, timestamp_ns))
        os.utime(manifest_path, ns=(timestamp_ns + 1_000_000, timestamp_ns + 1_000_000))
        old_db_stat = deployed.stat()
        old_manifest_stat = manifest_path.stat()

        with patch.object(updater, "opener", return_value=FakeOpener(self.assets)):
            self.assertTrue(updater.deploy(deployed, self.lock_path))

        installed_db_stat = deployed.stat()
        installed_manifest_stat = manifest_path.stat()
        self.assertEqual(stat.S_IMODE(installed_db_stat.st_mode), stat.S_IMODE(old_db_stat.st_mode))
        self.assertEqual(stat.S_IMODE(installed_manifest_stat.st_mode), stat.S_IMODE(old_manifest_stat.st_mode))
        self.assertEqual(installed_db_stat.st_mtime_ns, old_db_stat.st_mtime_ns)
        self.assertEqual(installed_manifest_stat.st_mtime_ns, old_manifest_stat.st_mtime_ns)

    def test_staged_manifest_replace_failure_restores_previous_pair_or_removes_new_pair(self):
        real_replace = Path.replace

        def fail_staged_manifest(source, target):
            if source.name == "manifest.json" and source.parent.name.startswith(".knowledge-release-"):
                raise OSError("injected staged manifest replacement failure")
            return real_replace(source, target)

        for have_previous_pair in (True, False):
            with self.subTest(have_previous_pair=have_previous_pair):
                deployed = self.root / ("deployment-existing" if have_previous_pair else "deployment-empty") / "exteracontext.sqlite"
                manifest_path = deployed.parent / ".knowledge-manifest.json"
                if have_previous_pair:
                    deployed.parent.mkdir(parents=True, exist_ok=True)
                    old_db = b"previous database bytes"
                    old_manifest = b"previous manifest bytes"
                    deployed.write_bytes(old_db)
                    manifest_path.write_bytes(old_manifest)
                    if os.name == "posix":
                        os.chmod(deployed, 0o640)
                        os.chmod(manifest_path, 0o604)
                        timestamp_ns = 1_610_000_000_123_456_789
                        os.utime(deployed, ns=(timestamp_ns, timestamp_ns))
                        os.utime(manifest_path, ns=(timestamp_ns + 1_000_000, timestamp_ns + 1_000_000))
                    old_db_stat = deployed.stat()
                    old_manifest_stat = manifest_path.stat()
                with (
                    patch.object(updater, "opener", return_value=FakeOpener(self.assets)),
                    patch.object(Path, "replace", new=fail_staged_manifest),
                    self.assertRaisesRegex(OSError, "injected staged manifest replacement failure"),
                ):
                    updater.deploy(deployed, self.lock_path)
                if have_previous_pair:
                    self.assertEqual(deployed.read_bytes(), old_db)
                    self.assertEqual(manifest_path.read_bytes(), old_manifest)
                    if os.name == "posix":
                        restored_db_stat = deployed.stat()
                        restored_manifest_stat = manifest_path.stat()
                        self.assertEqual(stat.S_IMODE(restored_db_stat.st_mode), stat.S_IMODE(old_db_stat.st_mode))
                        self.assertEqual(stat.S_IMODE(restored_manifest_stat.st_mode), stat.S_IMODE(old_manifest_stat.st_mode))
                        self.assertEqual(restored_db_stat.st_mtime_ns, old_db_stat.st_mtime_ns)
                        self.assertEqual(restored_manifest_stat.st_mtime_ns, old_manifest_stat.st_mtime_ns)
                else:
                    self.assertFalse(deployed.exists())
                    self.assertFalse(manifest_path.exists())

    def test_hash_mismatch_preserves_existing_database(self):
        opener = FakeOpener(self.assets, corrupt=True)
        deployed = self.root / "deployment" / "exteracontext.sqlite"
        deployed.parent.mkdir()
        old_db = b"previous database"
        old_manifest = b"previous manifest"
        deployed.write_bytes(old_db)
        manifest_path = deployed.parent / ".knowledge-manifest.json"
        manifest_path.write_bytes(old_manifest)
        with patch.object(updater, "opener", return_value=opener), self.assertRaisesRegex(ValueError, "tracked KNOWLEDGE_LOCK"):
            updater.deploy(deployed, self.lock_path)
        self.assertEqual(deployed.read_bytes(), old_db)
        self.assertEqual(manifest_path.read_bytes(), old_manifest)

    def test_chunked_asset_without_content_length_streams_to_stage(self):
        opener = FakeOpener(self.assets, omit_content_length=True, chunk_size=37)
        deployed = self.root / "chunked" / "exteracontext.sqlite"
        with patch.object(updater, "opener", return_value=opener):
            self.assertTrue(updater.deploy(deployed, self.lock_path))
        self.assertEqual(deployed.read_bytes(), self.assets["exteracontext.sqlite"])
        db_responses = [r for r in opener.responses if r.data == self.assets["exteracontext.sqlite"]]
        self.assertTrue(db_responses)
        self.assertGreater(len(db_responses[0].data) // 37, 1)
        self.assertLessEqual(db_responses[0].max_read, updater.CHUNK_BYTES)

    def test_corrupt_manifest_download_preserves_database_and_manifest(self):
        opener = FakeOpener(self.assets, corrupt_manifest=True, omit_content_length=True, chunk_size=13)
        deployed = self.root / "corrupt-manifest" / "exteracontext.sqlite"
        deployed.parent.mkdir()
        old_db = b"previous database"
        old_manifest = b"previous manifest"
        deployed.write_bytes(old_db)
        manifest_path = deployed.parent / ".knowledge-manifest.json"
        manifest_path.write_bytes(old_manifest)
        with patch.object(updater, "opener", return_value=opener), self.assertRaisesRegex(ValueError, "manifest bytes do not match tracked KNOWLEDGE_LOCK"):
            updater.deploy(deployed, self.lock_path)
        self.assertEqual(deployed.read_bytes(), old_db)
        self.assertEqual(manifest_path.read_bytes(), old_manifest)

    def test_oversized_chunked_download_preserves_database_and_manifest(self):
        opener = FakeOpener(self.assets, oversized=True, omit_content_length=True, chunk_size=11)
        deployed = self.root / "oversized" / "exteracontext.sqlite"
        deployed.parent.mkdir()
        old_db = b"previous database"
        old_manifest = b"previous manifest"
        deployed.write_bytes(old_db)
        manifest_path = deployed.parent / ".knowledge-manifest.json"
        manifest_path.write_bytes(old_manifest)
        with patch.object(updater, "opener", return_value=opener), self.assertRaisesRegex(ValueError, "locked asset size"):
            updater.deploy(deployed, self.lock_path)
        self.assertEqual(deployed.read_bytes(), old_db)
        self.assertEqual(manifest_path.read_bytes(), old_manifest)

    def test_nonempty_destination_sidecar_preserves_database_and_manifest(self):
        opener = FakeOpener(self.assets)
        for suffix in ("-wal", "-journal"):
            with self.subTest(sidecar=suffix):
                deployed = self.root / suffix[1:] / "exteracontext.sqlite"
                deployed.parent.mkdir()
                old_db = b"previous deployed database"
                old_manifest = b"previous deployed manifest\n"
                deployed.write_bytes(old_db)
                manifest_path = deployed.parent / ".knowledge-manifest.json"
                manifest_path.write_bytes(old_manifest)
                sidecar = Path(str(deployed) + suffix)
                sidecar.write_bytes(b"pending transaction")

                with patch.object(updater, "opener", return_value=opener), self.assertRaisesRegex(ValueError, f"nonempty SQLite {suffix} sidecar"):
                    updater.deploy(deployed, self.lock_path)

                self.assertEqual(deployed.read_bytes(), old_db)
                self.assertEqual(manifest_path.read_bytes(), old_manifest)
                self.assertEqual(sidecar.read_bytes(), b"pending transaction")
        self.assertEqual(opener.downloads, 0)

    def test_zero_length_destination_sidecars_are_allowed(self):
        opener = FakeOpener(self.assets)
        deployed = self.root / "deployment" / "exteracontext.sqlite"
        deployed.parent.mkdir()
        Path(str(deployed) + "-wal").touch()
        Path(str(deployed) + "-journal").touch()

        with patch.object(updater, "opener", return_value=opener):
            self.assertTrue(updater.deploy(deployed, self.lock_path))

        self.assertEqual(deployed.read_bytes(), self.assets["exteracontext.sqlite"])
        self.assertEqual(opener.downloads, 2)

    def test_staged_sidecar_after_validation_preserves_deployed_pair(self):
        opener = FakeOpener(self.assets)
        deployed = self.root / "deployment" / "exteracontext.sqlite"
        deployed.parent.mkdir()
        old_db = b"previous deployed database"
        old_manifest = b"previous deployed manifest\n"
        deployed.write_bytes(old_db)
        manifest_path = deployed.parent / ".knowledge-manifest.json"
        manifest_path.write_bytes(old_manifest)
        original_verify = updater.verify_manifest

        def verify_then_create_sidecar(db, manifest_path, expected_commit=None):
            result = original_verify(db, manifest_path, expected_commit)
            Path(str(db) + "-journal").write_bytes(b"unexpected staged journal")
            return result

        with (
            patch.object(updater, "opener", return_value=opener),
            patch.object(updater, "verify_manifest", side_effect=verify_then_create_sidecar),
            self.assertRaisesRegex(ValueError, "nonempty SQLite -journal sidecar"),
        ):
            updater.deploy(deployed, self.lock_path)

        self.assertEqual(deployed.read_bytes(), old_db)
        self.assertEqual(manifest_path.read_bytes(), old_manifest)

    def test_redirect_handler_rejects_non_github_assets(self):
        from urllib.request import Request
        handler = updater.RestrictedRedirect()
        with self.assertRaises(Exception):
            handler.redirect_request(Request("https://github.com/"), None, 302, "Found", {}, "https://evil.invalid/file")


if __name__ == "__main__":
    unittest.main()
