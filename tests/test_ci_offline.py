#!/usr/bin/env python3
"""Corpus-independent query smoke test. Never fetches or builds a knowledge DB."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUERY = ROOT / "scripts" / "query.py"


def invoke(db: Path, overlay: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update({
        "EXTERACONTEXT_DB": str(db),
        "EXTERACONTEXT_KNOWLEDGE_DB": str(overlay),
        "EXTERACONTEXT_AUTO_SYNC": "0",
        "PYTHONIOENCODING": "utf-8",
    })
    return subprocess.run(
        [sys.executable, str(QUERY), *args], cwd=ROOT, env=env,
        text=True, encoding="utf-8", capture_output=True, check=False,
    )


def fixture(db: Path) -> None:
    with closing(sqlite3.connect(db)) as con:
        con.executescript("""
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
            INSERT INTO meta VALUES ('facts', '1'), ('docs', '1');
            INSERT INTO docs VALUES ('fixture.md', 'Local fixture', 'topic', 'Local only');
            INSERT INTO facts VALUES (
                'fixture-1', 'local', 'fixture_symbol is a local fixture',
                'fixture_symbol', '', 'fixture.md', '', 'docs', '', 'test-fixture',
                'fixture-1', 'docs', 'local', 'fixture', 'test'
            );
            INSERT INTO facts_fts(id, topic, claim, api, recipe, source_id, version, platform)
                VALUES ('fixture-1', 'local', 'fixture_symbol is a local fixture',
                        'fixture_symbol', '', 'test-fixture', '', 'test');
            INSERT INTO docs_fts VALUES ('fixture.md', 'Local fixture', 'Local only');
        """)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="exteracontext-ci-") as tmp:
        root = Path(tmp)
        db, overlay = root / "base.sqlite", root / "overlay.sqlite"
        absent = invoke(db, overlay, "doctor")
        assert absent.returncode != 0, absent.stdout
        assert "base database not found" in absent.stderr, absent.stderr
        assert not db.exists() and not overlay.exists(), "missing DB must not trigger a download or build"

        fixture(db)
        doctor = invoke(db, overlay, "doctor")
        assert doctor.returncode == 0, doctor.stderr
        status = json.loads(doctor.stdout)
        assert status["facts"] == 1 and status["docs"] == 1, status
        assert status["db"] == str(db), status

        api = invoke(db, overlay, "api", "fixture_symbol", "--format", "json")
        assert api.returncode == 0, api.stderr
        assert any(row["id"] == "fixture-1" for row in json.loads(api.stdout)), api.stdout

        search = invoke(db, overlay, "search", "fixture_symbol", "--format", "json")
        assert search.returncode == 0, search.stderr
        assert any(row["id"] == "fixture-1" for row in json.loads(search.stdout)), search.stdout
        assert not overlay.exists(), "read-only retrieval must not create an overlay"

        if "--suite" in sys.argv[1:]:
            # Existing overlay/orchestrator tests exercise query.py subprocesses;
            # point them at this tiny base DB rather than the external corpus.
            env = os.environ.copy()
            env.update({"EXTERACONTEXT_DB": str(db), "EXTERACONTEXT_AUTO_SYNC": "0",
                        "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
            for script in ("tests/test_knowledge_writeback.py", "tests/test_orchestrator.py"):
                completed = subprocess.run([sys.executable, str(ROOT / script)], cwd=ROOT, env=env,
                                           text=True, encoding="utf-8", capture_output=True, check=False)
                assert completed.returncode == 0, (script, completed.stdout, completed.stderr)
                print(completed.stdout, end="")
    print("ci-offline: ok")


if __name__ == "__main__":
    main()
