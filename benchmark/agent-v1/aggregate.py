#!/usr/bin/env python3
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "benchmark-results")
files = sorted(root.rglob("assessment.json"))
if not files:
    raise SystemExit(f"no assessment.json files under {root}")

rows = [json.loads(p.read_text("utf-8")) for p in files]
by_mode = defaultdict(list)
for row in rows:
    by_mode[row["mode"]].append(row)

def rate(items, key):
    vals = [bool(x[key]) for x in items if x.get(key) is not None]
    return None if not vals else sum(vals) / len(vals)

def inv_rate(items, key):
    vals = [bool(x[key]) for x in items if x.get(key) is not None]
    return None if not vals else sum(vals) / len(vals)

summary = {"runs": len(rows), "modes": {}}
for mode in ["A", "B", "C"]:
    items = by_mode.get(mode, [])
    summary["modes"][mode] = {
        "runs": len(items),
        "completion_rate": rate(items, "materially_completed"),
        "clean_task_success_rate": rate(items, "clean_success"),
        "invented_api_rate": inv_rate(items, "invented_api"),
        "donor_contamination_rate": inv_rate(items, "donor_contamination"),
        "version_mismatch_rate": inv_rate(items, "version_mismatch"),
        "constraints_handled_rate": rate(items, "constraints_handled"),
        "correct_unknown_rate": rate(items, "correct_unknown"),
        "evidence_boundary_correct_rate": rate(items, "evidence_boundary_correct"),
        "unnecessary_low_level_fallback_rate": inv_rate(items, "unnecessary_low_level_fallback"),
        "static_success_rate": rate(items, "static_success"),
        "load_success_rate": rate(items, "load_success"),
        "runtime_success_rate": rate(items, "runtime_success"),
    }

print(json.dumps(summary, ensure_ascii=False, indent=2))
