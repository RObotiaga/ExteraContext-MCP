#!/usr/bin/env python3
"""Aggregate only independently evaluated, non-pilot protocol 1.1 runs."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from scoring import summarize


def aggregate(root: Path) -> dict:
    records = []
    excluded = []
    for path in sorted(root.rglob("assessment.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("pilot") is True:
            excluded.append(str(path))
            continue
        records.append((path, row))
    result = summarize(records)
    result["excluded_pilots"] = excluded
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(aggregate(Path(sys.argv[1] if len(sys.argv) > 1 else "benchmark-results")), ensure_ascii=False, indent=2))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
