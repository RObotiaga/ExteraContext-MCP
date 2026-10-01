#!/usr/bin/env python3
"""Create a disposable synthetic SQLite corpus for offline CI tests.

This fixture is deliberately tiny and synthetic; it never reads or copies any
production knowledge database. Destination creation is exclusive: existing
files, directories and symlinks are never overwritten.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import sqlite3
import sys
from pathlib import Path

FACT_COUNT = 2000
DOC_COUNT = 100

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE docs(path TEXT PRIMARY KEY, title TEXT, kind TEXT, content TEXT);
CREATE TABLE facts(
    id TEXT PRIMARY KEY, topic TEXT, claim TEXT, api TEXT,
    evidence_url TEXT, evidence_path TEXT, version TEXT, status TEXT,
    recipe TEXT, source_id TEXT, source_fact_id TEXT,
    original_status TEXT, canonical_topic TEXT, review_status TEXT,
    platform TEXT
);
CREATE VIRTUAL TABLE facts_fts USING fts5(
    id UNINDEXED, topic, claim, api, recipe, source_id, version, platform
);
CREATE VIRTUAL TABLE docs_fts USING fts5(path UNINDEXED, title, content);
"""


def create_fixture(destination: Path) -> Path:
    """Create and validate a synthetic 2000-fact/100-doc database exclusively."""
    db = destination.expanduser().absolute()
    db.parent.mkdir(parents=True, exist_ok=True)

    # Reserve the destination atomically, so a typo or concurrent invocation
    # cannot overwrite an existing file (including a symlink).
    try:
        with db.open("xb"):
            pass
    except FileExistsError as exc:
        raise FileExistsError(f"refusing to overwrite existing fixture path: {db}") from exc

    try:
        with closing(sqlite3.connect(db)) as con:
            con.executescript(SCHEMA)
            docs = [("fixture.md", "Local fixture", "topic", "Synthetic local CI fixture")]
            docs.extend((f"fixture-{i}.md", f"Fixture {i}", "test fixture", "synthetic")
                        for i in range(2, DOC_COUNT + 1))
            con.executemany("INSERT INTO docs VALUES (?, ?, ?, ?)", docs)
            con.executemany("INSERT INTO docs_fts VALUES (?, ?, ?)",
                            [(path, title, content) for path, title, _kind, content in docs])

            facts = []
            for i in range(1, FACT_COUNT + 1):
                api = "fixture_symbol" if i == 1 else "send_request" if i == 2 else f"fixture_api_{i}"
                claim = f"{api} is a synthetic fixture API"
                facts.append((f"fixture-{i}", "local", claim, api, "", "fixture.md", "", "docs", "",
                              "test-fixture", f"fixture-{i}", "docs", "local", "fixture", "test"))
            con.executemany("INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", facts)
            con.executemany(
                "INSERT INTO facts_fts(id, topic, claim, api, recipe, source_id, version, platform) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(r[0], r[1], r[2], r[3], r[8], r[9], r[6], r[14]) for r in facts],
            )
            con.executemany("INSERT INTO meta VALUES (?, ?)",
                            [("facts", str(FACT_COUNT)), ("docs", str(DOC_COUNT))])
            con.commit()
            actual_facts = con.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            actual_docs = con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
            known = con.execute("SELECT 1 FROM facts WHERE api='send_request'").fetchone()
            if (actual_facts, actual_docs, known is not None) != (FACT_COUNT, DOC_COUNT, True):
                raise RuntimeError("synthetic fixture validation failed")
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("synthetic fixture integrity_check failed")
        return db
    except BaseException:
        # Remove only artifacts created for this destination; preserve any
        # unrelated pre-existing path (the DB itself was reserved exclusively).
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(str(db) + suffix).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="new fixture DB path; must not already exist")
    args = parser.parse_args(argv)
    try:
        db = create_fixture(args.destination)
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
        parser.exit(1, f"fixture creation refused/failed: {exc}\n")
    print(f"prepared synthetic CI database: {db} ({FACT_COUNT} facts, {DOC_COUNT} docs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
