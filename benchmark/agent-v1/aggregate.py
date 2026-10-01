#!/usr/bin/env python3
"""Aggregate only independently evaluated, non-pilot protocol 1.1 runs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from scoring import summarize


def aggregate(root: Path, attestation_key_path: Path | None = None) -> dict:
    records = []
    excluded = []
    for path in sorted(root.rglob("assessment.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("pilot") is True:
            excluded.append(str(path))
            continue
        records.append((path, row))
    result = summarize(records, attestation_key_path=attestation_key_path)
    result["excluded_pilots"] = excluded
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path("benchmark-results"))
    parser.add_argument("--attestation-key-file", type=Path,
                        help="operator HMAC key file outside all run packets; never copy it into a packet")
    args = parser.parse_args()
    try:
        print(json.dumps(aggregate(args.root, args.attestation_key_file), ensure_ascii=False, indent=2))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
