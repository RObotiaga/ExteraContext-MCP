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

fixture = ROOT / "fixture-template"
assert (fixture / "README.md").exists()
assert (fixture / "plugin.py").exists()
assert (fixture / "fixture.json").exists()

counts = Counter((r["task_id"], r["mode"]) for r in runs)
for task_id in ids:
    for mode in ["A", "B", "C"]:
        assert counts[(task_id, mode)] == 2, (task_id, mode, counts[(task_id, mode)])

run_ids = [r["run_id"] for r in runs]
assert len(run_ids) == len(set(run_ids))
assert all(r["repeat"] in [1, 2] for r in runs)

with tempfile.TemporaryDirectory() as td:
    for run_id in ["v1-001", "v1-002", "v1-003"]:
        out = Path(td) / run_id
        cp = subprocess.run(
            [sys.executable, str(ROOT / "prepare_run.py"), run_id, str(out)],
            check=True,
            capture_output=True,
            text=True,
        )
        target = out / "target"
        assert target.exists()
        assert str(target) in cp.stdout
        names = {p.name for p in target.iterdir()}
        assert {
            "README.md", "plugin.py", "fixture.json",
            "BENCHMARK_MODE.md", "BENCHMARK_TASK.md", "result.schema.json"
        } <= names
        assert (out / "OPEN_IN_DSH.md").exists()
        leaked = {
            p.name for p in out.rglob("*")
            if p.name in {"ground-truth.json", "assessment.schema.json", "EVALUATOR.md"}
        }
        assert not leaked, leaked

print("agent-benchmark-v1: ok")
