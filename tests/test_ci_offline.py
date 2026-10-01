#!/usr/bin/env python3
"""Corpus-independent query smoke test. Never fetches or builds a knowledge DB."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUERY = ROOT / "scripts" / "query.py"
sys.path.insert(0, str(ROOT / "scripts"))
from prepare_ci_fixture import create_fixture


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
    create_fixture(db)


class FixtureHelperTests(unittest.TestCase):
    def test_helper_creates_exact_synthetic_contents(self):
        with tempfile.TemporaryDirectory(prefix="exteracontext-fixture-helper-") as tmp:
            destination = Path(tmp) / "new" / "fixture.sqlite"
            created = create_fixture(destination)
            self.assertEqual(created, destination.absolute())
            with closing(sqlite3.connect(created)) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM facts").fetchone()[0], 2000)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM docs").fetchone()[0], 100)
                self.assertIsNotNone(con.execute("SELECT 1 FROM facts WHERE api='send_request'").fetchone())
                self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_helper_refuses_existing_file_without_overwriting(self):
        with tempfile.TemporaryDirectory(prefix="exteracontext-fixture-refuse-") as tmp:
            destination = Path(tmp) / "existing.sqlite"
            marker = b"preserve existing file"
            destination.write_bytes(marker)
            with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
                create_fixture(destination)
            self.assertEqual(destination.read_bytes(), marker)


def main() -> None:
    if "--prepare-fixture" in sys.argv[1:]:
        index = sys.argv.index("--prepare-fixture") + 1
        if index >= len(sys.argv):
            raise SystemExit("--prepare-fixture requires a destination path")
        db = Path(sys.argv[index]).resolve()
        db.parent.mkdir(parents=True, exist_ok=True)
        fixture(db)
        print(f"prepared synthetic CI database: {db}")
        return

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
        assert status["facts"] == 2000 and status["docs"] == 100, status
        assert status["db"] == str(db), status

        api = invoke(db, overlay, "api", "fixture_symbol", "--format", "json")
        assert api.returncode == 0, api.stderr
        assert any(row["id"] == "fixture-1" for row in json.loads(api.stdout)), api.stdout

        search = invoke(db, overlay, "search", "fixture_symbol", "--format", "json")
        assert search.returncode == 0, search.stderr
        assert any(row["id"] == "fixture-1" for row in json.loads(search.stdout)), search.stdout
        assert not overlay.exists(), "read-only retrieval must not create an overlay"

        if "--suite" in sys.argv[1:]:
            # Run the complete Python test discovery against an isolated synthetic
            # base DB. This catches corpus-dependent integration tests without
            # copying, downloading, or building any production knowledge corpus.
            env = os.environ.copy()
            env.update({"EXTERACONTEXT_DB": str(db),
                        "EXTERACONTEXT_KNOWLEDGE_DB": str(overlay),
                        "EXTERACONTEXT_AUTO_SYNC": "0",
                        "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
            completed = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
                cwd=ROOT, env=env, text=True, encoding="utf-8", capture_output=True, check=False)
            print(completed.stdout, end="")
            print(completed.stderr, end="", file=sys.stderr)
            assert completed.returncode == 0, "complete Python unittest discovery failed"

    print("ci-offline: ok")


if __name__ == "__main__":
    main()
