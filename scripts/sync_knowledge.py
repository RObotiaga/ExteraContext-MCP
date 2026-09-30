#!/usr/bin/env python3
"""Deploy an already-built knowledge corpus, without fetching or executing corpus code."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "exteracontext.sqlite"
DEFAULT_SOURCE = Path.home() / ".dsh" / "skills" / "exteracontext" / "data" / "exteracontext.sqlite"
REQUIRED_COLUMNS = {
    "docs": {"path", "title", "kind", "content"},
    "facts": {"id", "topic", "claim", "api", "evidence_url", "evidence_path", "version", "status", "recipe", "source_id", "source_fact_id", "original_status", "canonical_topic", "review_status", "platform"},
    "meta": {"key", "value"},
    "source_runs": {"call_id", "source_id", "role"},
    "source_provenance": {"source_id", "collector_runs", "reviewer_runs", "has_collector", "has_reviewer"},
    "docs_fts": set(),
    "facts_fts": set(),
}


def require_quiescent(path: Path) -> None:
    """immutable SQLite reads do not see WAL; never silently discard WAL transactions."""
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError(f"nonempty SQLite {suffix} sidecar: {sidecar}; quiesce/checkpoint externally first")


def validate(path: Path) -> dict[str, object]:
    # immutable=1 is essential: even mode=ro may create/change a WAL -shm file.
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError(f"SQLite integrity_check failed: {path}")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError(f"SQLite foreign_key_check failed: {path}")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = REQUIRED_COLUMNS.keys() - tables
        if missing:
            raise ValueError(f"missing corpus tables: {sorted(missing)}")
        for table, columns in REQUIRED_COLUMNS.items():
            actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if not columns <= actual:
                raise ValueError(f"invalid {table} schema; missing columns: {sorted(columns - actual)}")
        for table in ("docs_fts", "facts_fts"):
            definition = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
            if "using fts5" not in definition.lower():
                raise ValueError(f"{table} is not an FTS5 virtual table")
        counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("docs", "facts", "source_runs", "source_provenance")}
        if not counts["docs"] or not counts["facts"]:
            raise ValueError("corpus has no docs or facts")
        return {"counts": counts, "meta": dict(connection.execute("SELECT key, value FROM meta"))}


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sync(source: Path, db: Path) -> Path:
    source = source.expanduser().absolute()
    db = db.expanduser().absolute()
    if not source.is_file() or source.is_symlink():
        raise ValueError(f"source must be a regular non-symlink SQLite file: {source}")
    if source.resolve() == db.resolve() or db.is_symlink():
        raise ValueError("destination must be distinct from source and not a symlink")
    require_quiescent(source)
    before = source.stat()
    identity = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    details = validate(source)
    source_hash = fingerprint(source)
    if identity(source.stat()) != identity(before):
        raise ValueError("source changed during validation")
    db.parent.mkdir(parents=True, exist_ok=True)
    manifest = db.parent / ".knowledge-manifest.json"
    if manifest.is_symlink():
        raise ValueError("manifest cannot be a symlink")
    require_quiescent(db)
    temp_db = temp_manifest = None
    try:
        fd, temp_db = tempfile.mkstemp(prefix=".knowledge-db-", suffix=".sqlite", dir=db.parent)
        with os.fdopen(fd, "wb") as output, source.open("rb") as inp:
            for chunk in iter(lambda: inp.read(1024 * 1024), b""):
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        require_quiescent(source)
        if identity(source.stat()) != identity(before) or fingerprint(temp_db_path := Path(temp_db)) != source_hash:
            raise ValueError("source changed during copy")
        validate(temp_db_path)
        payload = {
            "format": 1,
            "method": "offline-existing-db-copy",
            "source": str(source),
            "source_sha256": source_hash,
            "destination_sha256": source_hash,
            "size_bytes": temp_db_path.stat().st_size,
            "schema": details,
            "commit_verified": False,
            "note": "This is a byte-for-byte deployment, not a rebuild or proof of a repository commit.",
        }
        fd, temp_manifest = tempfile.mkstemp(prefix=".knowledge-manifest-", suffix=".json", dir=db.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(payload, output, indent=2, ensure_ascii=False, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        require_quiescent(db)
        os.replace(temp_db, db)
        temp_db = None
        os.replace(temp_manifest, manifest)
        temp_manifest = None
        return manifest
    finally:
        for path in (temp_db, temp_manifest):
            if path is not None:
                Path(path).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline deployment of an already-built SQLite corpus (no git/network/build execution)")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="existing installed corpus SQLite file")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="destination base SQLite file")
    args = parser.parse_args(argv)
    try:
        manifest = sync(args.source, args.db)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.exit(1, f"offline bootstrap refused: {exc}\n")
    print(f"knowledge database ready: {args.db} (offline; manifest: {manifest})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
