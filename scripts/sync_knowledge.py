#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "exteracontext.sqlite"
DEFAULT_REPO = "https://github.com/RObotiaga/ExteraContext-Knowledge.git"
LOCK_FILE = ROOT / "KNOWLEDGE_LOCK"
STAMP_FILE = ROOT / "data" / ".knowledge-ref"


def default_ref() -> str:
    explicit = os.environ.get("EXTERACONTEXT_KNOWLEDGE_REF")
    if explicit:
        return explicit.strip()
    if LOCK_FILE.exists():
        value = LOCK_FILE.read_text("utf-8").strip()
        if value:
            return value
    return "main"


def main() -> int:
    p = argparse.ArgumentParser(description="Build the ExteraContext base DB from ExteraContext-Knowledge")
    p.add_argument("--repo", default=os.environ.get("EXTERACONTEXT_KNOWLEDGE_REPO", DEFAULT_REPO))
    p.add_argument("--ref", default=default_ref())
    p.add_argument("--db", type=Path, default=Path(os.environ.get("EXTERACONTEXT_DB", DEFAULT_DB)))
    args = p.parse_args()

    if not shutil.which("git"):
        raise SystemExit("git is required to sync ExteraContext-Knowledge")

    args.db.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="exteracontext-knowledge-") as td:
        checkout = Path(td) / "knowledge"
        subprocess.run(["git", "clone", "-q", "--no-checkout", args.repo, str(checkout)], check=True)
        subprocess.run(["git", "-C", str(checkout), "checkout", "-q", "--detach", args.ref], check=True)

        build = checkout / "scripts" / "build_index.py"
        wiki = checkout / "data" / "wiki"
        provenance = checkout / "data" / "legacy-source-runs.json"
        tmp_db = args.db.with_suffix(args.db.suffix + ".tmp")
        subprocess.run(
            [
                sys.executable,
                str(build),
                "--wiki",
                str(wiki),
                "--provenance",
                str(provenance),
                "--db",
                str(tmp_db),
            ],
            check=True,
        )
        tmp_db.replace(args.db)

    stamp = args.db.parent / ".knowledge-ref"
    stamp.write_text(args.ref.strip() + "\n", encoding="utf-8")
    print(f"knowledge database ready: {args.db} @ {args.ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
