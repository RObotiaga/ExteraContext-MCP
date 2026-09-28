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


def main() -> int:
    p = argparse.ArgumentParser(description="Build the ExteraContext base DB from ExteraContext-Knowledge")
    p.add_argument("--repo", default=os.environ.get("EXTERACONTEXT_KNOWLEDGE_REPO", DEFAULT_REPO))
    p.add_argument("--ref", default=os.environ.get("EXTERACONTEXT_KNOWLEDGE_REF", "main"))
    p.add_argument("--db", type=Path, default=Path(os.environ.get("EXTERACONTEXT_DB", DEFAULT_DB)))
    args = p.parse_args()

    if not shutil.which("git"):
        raise SystemExit("git is required to sync ExteraContext-Knowledge")

    args.db.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="exteracontext-knowledge-") as td:
        checkout = Path(td) / "knowledge"
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", args.ref, args.repo, str(checkout)],
            check=True,
        )
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

    print(f"knowledge database ready: {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
