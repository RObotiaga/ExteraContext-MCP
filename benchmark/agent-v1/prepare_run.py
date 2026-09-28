#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent

p = argparse.ArgumentParser(description="Prepare one public ExteraContext agent-benchmark run packet")
p.add_argument("run_id")
p.add_argument("output", type=Path)
args = p.parse_args()

manifest = json.loads((ROOT / "run-manifest.json").read_text("utf-8"))
tasks = json.loads((ROOT / "tasks.json").read_text("utf-8"))["tasks"]
run = next((x for x in manifest["runs"] if x["run_id"] == args.run_id), None)
if run is None:
    raise SystemExit(f"unknown run id: {args.run_id}")
task = next(x for x in tasks if x["id"] == run["task_id"])

out = args.output
if out.exists():
    shutil.rmtree(out)
out.mkdir(parents=True)

(out / "RUN.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", "utf-8")
(out / "TASK.json").write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", "utf-8")
(out / "MODE.md").write_text((ROOT / "prompts" / f"MODE_{run['mode']}.md").read_text("utf-8"), "utf-8")
shutil.copy2(ROOT / "result.schema.json", out / "result.schema.json")

task_md = f"""# Benchmark task {task['id']}

Target:

```json
{json.dumps(task['target'], ensure_ascii=False, indent=2)}
```

Task:

{task['prompt']}

Run metadata:

- run_id: {run['run_id']}
- mode: {run['mode']}
- repeat: {run['repeat']}

Read MODE.md for the mode constraints. Complete the task in the supplied target project/fixture and create BENCHMARK_RESULT.json matching result.schema.json.
"""
(out / "TASK.md").write_text(task_md, "utf-8")

for forbidden in ["ground-truth.json", "assessment.schema.json"]:
    if (out / forbidden).exists():
        raise AssertionError(f"private evaluator file leaked: {forbidden}")

print(out)
