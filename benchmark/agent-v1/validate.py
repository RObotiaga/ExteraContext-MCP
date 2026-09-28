#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
tasks_doc = json.loads((ROOT / "tasks.json").read_text("utf-8"))
manifest = json.loads((ROOT / "run-manifest.json").read_text("utf-8"))
ground = json.loads((ROOT / "ground-truth.json").read_text("utf-8"))

tasks = tasks_doc["tasks"]
runs = manifest["runs"]
ids = [t["id"] for t in tasks]

assert len(tasks) == 10, len(tasks)
assert len(set(ids)) == 10
assert len(runs) == 60, len(runs)
assert set(ground["tasks"]) == set(ids)

counts = Counter((r["task_id"], r["mode"]) for r in runs)
for task_id in ids:
    for mode in ["A", "B", "C"]:
        assert counts[(task_id, mode)] == 2, (task_id, mode, counts[(task_id, mode)])

run_ids = [r["run_id"] for r in runs]
assert len(run_ids) == len(set(run_ids))
assert all(r["repeat"] in [1, 2] for r in runs)

with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "packet"
    subprocess.run([sys.executable, str(ROOT / "prepare_run.py"), runs[0]["run_id"], str(out)], check=True)
    names = {p.name for p in out.iterdir()}
    assert "ground-truth.json" not in names
    assert "assessment.schema.json" not in names
    assert {"RUN.json", "TASK.json", "TASK.md", "MODE.md", "result.schema.json"} <= names

print("agent-benchmark-v1: ok")
