#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixture-template"

p = argparse.ArgumentParser(description="Prepare one isolated ExteraContext agent-benchmark run packet")
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

target = out / "target"
shutil.copytree(FIXTURE, target)

(out / "RUN.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", "utf-8")
(out / "TASK.json").write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", "utf-8")

mode_text = (ROOT / "prompts" / f"MODE_{run['mode']}.md").read_text("utf-8")
(target / "BENCHMARK_MODE.md").write_text(mode_text, "utf-8")
shutil.copy2(ROOT / "result.schema.json", target / "result.schema.json")

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

Work only inside this target project unless BENCHMARK_MODE.md explicitly permits an external resource.

Create BENCHMARK_RESULT.json in this directory and make it match result.schema.json.
"""
(target / "BENCHMARK_TASK.md").write_text(task_md, "utf-8")

launch = f"""# Launch this run

Open the following directory as a **new, clean DSH session/project**:

`{target.resolve()}`

Then instruct the agent:

> Read BENCHMARK_MODE.md and BENCHMARK_TASK.md, perform the benchmark task, and create BENCHMARK_RESULT.json.

Mode: {run['mode']}
Task: {run['task_id']}
Repeat: {run['repeat']}

For mode B, separately provide read-only access to the frozen ExteraContext-Knowledge checkout specified by the benchmark protocol.
For mode C, enable the frozen ExteraContext MCP configuration and do not expose the raw Knowledge checkout.
"""
(out / "OPEN_IN_DSH.md").write_text(launch, "utf-8")

private_names = {"ground-truth.json", "assessment.schema.json", "EVALUATOR.md"}
for pth in out.rglob("*"):
    if pth.name in private_names:
        raise AssertionError(f"private evaluator file leaked into run packet: {pth}")

print(target)
