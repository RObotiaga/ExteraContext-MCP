#!/usr/bin/env python3
"""Host-side release metadata helpers; never builds from or executes Knowledge sources.

Deployment replaces the database and manifest with separate per-file atomic renames,
not a two-file transaction. Power loss between those renames can leave a mismatched
pair; the next AUTO_SYNC run verifies the pair and repairs it from the locked release,
or fails closed if the release cannot be fetched and verified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_knowledge as release  # noqa: E402


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Publish a complete host-owned file without opening the destination at all.
    # link() atomically refuses ANY existing destination entry, including dangling
    # symlinks (Windows open("x") can follow those). Same-parent staging ensures
    # the hardlink stays on one filesystem; unsupported filesystems fail closed.
    fd, temporary = tempfile.mkstemp(prefix=".knowledge-json-", dir=path.parent)
    staged = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.link(staged, path)
    finally:
        staged.unlink(missing_ok=True)


MAX_MANIFEST_BYTES = 1024 * 1024


def make_lock(
    manifest_path: Path,
    *,
    expected_commit: str | None = None,
    expected_database_sha256: str | None = None,
    expected_manifest_sha256: str | None = None,
) -> dict:
    """Create a lock from bounded manifest JSON; deliberately never opens SQLite.

    In the privileged publisher, expected values come from the unprivileged build job's
    outputs. This binds the manifest to the tested source commit and exact candidate bytes.
    """
    manifest_bytes = manifest_path.read_bytes()
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest exceeds the configured size limit")
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    if expected_manifest_sha256 is not None and manifest_digest != expected_manifest_sha256:
        raise ValueError("manifest bytes do not match build job SHA-256")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("manifest is not valid bounded UTF-8 JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != release.FORMAT:
        raise ValueError("unsupported manifest format")
    if manifest.get("source_repository") != release.SOURCE_REPOSITORY:
        raise ValueError("manifest source repository is invalid")
    if manifest.get("database_asset") != "exteracontext.sqlite":
        raise ValueError("manifest database asset is invalid")
    commit = manifest.get("source_commit")
    if not isinstance(commit, str) or not release.COMMIT_RE.fullmatch(commit):
        raise ValueError("manifest has no exact source commit")
    if expected_commit is not None and commit != expected_commit:
        raise ValueError("manifest source commit does not match build job source commit")
    database_digest = manifest.get("database_sha256")
    if not isinstance(database_digest, str) or not release.SHA256_RE.fullmatch(database_digest):
        raise ValueError("manifest database SHA-256 is invalid")
    if expected_database_sha256 is not None and database_digest != expected_database_sha256:
        raise ValueError("manifest database SHA-256 does not match build job output")
    size_bytes = manifest.get("size_bytes")
    if type(size_bytes) is not int or not 0 < size_bytes <= release.MAX_DB_BYTES:
        raise ValueError("manifest database size is invalid or exceeds the configured limit")
    schema = manifest.get("schema")
    if not isinstance(schema, dict) or manifest.get("schema_sha256") != release.schema_digest(schema):
        raise ValueError("manifest schema digest is invalid")
    counts = schema.get("counts")
    required_counts = {"docs", "facts", "source_runs", "source_provenance"}
    if not isinstance(counts, dict) or not required_counts <= counts.keys():
        raise ValueError("manifest corpus counts are incomplete")
    if set(counts) != required_counts:
        raise ValueError("manifest corpus counts contain unexpected keys")
    if any(type(counts[key]) is not int or counts[key] < 0 for key in required_counts):
        raise ValueError("manifest corpus counts are invalid")
    if not counts["docs"] or not counts["facts"]:
        raise ValueError("manifest corpus is empty")
    if not isinstance(schema.get("meta"), dict):
        raise ValueError("manifest corpus metadata is invalid")
    return {
        "format": release.FORMAT,
        "source_repository": release.SOURCE_REPOSITORY,
        "source_commit": commit,
        "artifact_repository": release.REPOSITORY,
        "release_tag": f"knowledge-{commit}",
        "database_asset": "exteracontext.sqlite",
        "database_sha256": database_digest,
        "manifest_asset": "manifest.json",
        "manifest_sha256": manifest_digest,
        "size_bytes": size_bytes,
        "schema_sha256": manifest["schema_sha256"],
        "counts": counts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    manifest_parser = commands.add_parser("manifest", help="validate candidate DB and create source-bound manifest")
    manifest_parser.add_argument("--db", type=Path, required=True)
    manifest_parser.add_argument("--source-commit", required=True)
    manifest_parser.add_argument("--output", type=Path, required=True)
    verify_parser = commands.add_parser("verify", help="verify candidate database against its manifest")
    verify_parser.add_argument("--db", type=Path, required=True)
    verify_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser.add_argument("--source-commit", required=True)
    lock_parser = commands.add_parser("lock", help="create reviewable KNOWLEDGE_LOCK JSON from bounded manifest metadata")
    lock_parser.add_argument("--manifest", type=Path, required=True)
    lock_parser.add_argument("--output", type=Path, required=True)
    lock_parser.add_argument("--source-commit", required=True)
    lock_parser.add_argument("--database-sha256", required=True)
    lock_parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "manifest":
            write_json(args.output, release.create_manifest(args.db, args.source_commit))
        elif args.command == "verify":
            release.verify_manifest(args.db, args.manifest, args.source_commit)
        elif args.command == "lock":
            write_json(
                args.output,
                make_lock(
                    args.manifest,
                    expected_commit=args.source_commit,
                    expected_database_sha256=args.database_sha256,
                    expected_manifest_sha256=args.manifest_sha256,
                ),
            )
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        # Keep CLI errors concise, while leaving process failures from validation visible.
        parser.exit(1, f"Knowledge release metadata refused: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
