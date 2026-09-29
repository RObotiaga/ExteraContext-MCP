#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

PROTOCOL_REVISION = "1.1"

root = Path(sys.argv[1] if len(sys.argv) > 1 else "benchmark-results")
files = sorted(root.rglob("assessment.json"))
if not files:
    raise SystemExit(f"no assessment.json files under {root}")

rows = [json.loads(p.read_text("utf-8")) for p in files]

bad = [
    (str(path), row.get("protocol_revision"), row.get("assessment_schema_version"))
    for path, row in zip(files, rows)
    if row.get("protocol_revision") != PROTOCOL_REVISION or row.get("assessment_schema_version") != 2
]
if bad:
    raise SystemExit(
        "refusing to aggregate mixed/legacy assessments; protocol 1.1 + assessment schema 2 required: "
        + json.dumps(bad, ensure_ascii=False)
    )

by_mode = defaultdict(list)
for row in rows:
    by_mode[row["mode"]].append(row)

def rate(items, key):
    vals = [bool(x[key]) for x in items if x.get(key) is not None]
    return None if not vals else sum(vals) / len(vals)

def avg(items, key):
    vals = [x[key] for x in items if isinstance(x.get(key), (int, float)) and not isinstance(x.get(key), bool)]
    return None if not vals else sum(vals) / len(vals)

def constraint_summary(items):
    per_constraint = defaultdict(list)
    all_values = []
    for row in items:
        for constraint_id, record in (row.get("constraints") or {}).items():
            passed = bool(record.get("passed"))
            per_constraint[constraint_id].append(passed)
            all_values.append(passed)
    return {
        "overall_required_constraint_pass_rate": None if not all_values else sum(all_values) / len(all_values),
        "by_constraint": {
            key: {"n": len(vals), "pass_rate": sum(vals) / len(vals)}
            for key, vals in sorted(per_constraint.items())
        },
    }

summary = {
    "protocol_revision": PROTOCOL_REVISION,
    "runs": len(rows),
    "modes": {},
}
for mode in ["A", "B", "C"]:
    items = by_mode.get(mode, [])
    summary["modes"][mode] = {
        "runs": len(items),
        "completion_rate": rate(items, "materially_completed"),
        "clean_task_success_rate": rate(items, "clean_success"),
        "invented_api_rate": rate(items, "invented_api"),
        "donor_contamination_rate": rate(items, "donor_contamination"),
        "version_mismatch_rate": rate(items, "version_mismatch"),
        "constraints_handled_rate": rate(items, "constraints_handled"),
        "constraints": constraint_summary(items),
        "correct_unknown_rate": rate(items, "correct_unknown"),
        "evidence_boundary_correct_rate": rate(items, "evidence_boundary_correct"),
        "unnecessary_low_level_fallback_rate": rate(items, "unnecessary_low_level_fallback"),
        "python_compile_success_rate": rate(items, "python_compile_success"),
        "target_static_success_rate": rate(items, "target_static_success"),
        "load_success_rate": rate(items, "load_success"),
        "runtime_success_rate": rate(items, "runtime_success"),
        "avg_verified_test_passes": avg(items, "verified_test_passes"),
        "avg_unverified_self_reported_test_passes": avg(items, "unverified_self_reported_test_passes"),
    }

print(json.dumps(summary, ensure_ascii=False, indent=2))
