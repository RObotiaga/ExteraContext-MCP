#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]
FIXTURE = ROOT / "fixture-template"
KNOWLEDGE_REPO = "https://github.com/RObotiaga/ExteraContext-Knowledge.git"

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

protocol_revision = run.get("protocol_revision") or manifest.get("protocol_revision")
frozen_knowledge_commit = run.get("frozen_knowledge_commit") or manifest.get("frozen_knowledge_commit")
if protocol_revision != "1.1":
    raise SystemExit(f"unsupported benchmark protocol revision: {protocol_revision!r}")
if not frozen_knowledge_commit:
    raise SystemExit("missing frozen_knowledge_commit")

out = args.output.resolve()
if out.exists():
    shutil.rmtree(out)
out.mkdir(parents=True)

target = out / "target"
runtime = out / "runtime"
runtime.mkdir()
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
- protocol_revision: {protocol_revision}
- frozen_knowledge_commit: {frozen_knowledge_commit}

Work only inside this target project unless BENCHMARK_MODE.md explicitly permits an external benchmark resource.

Create BENCHMARK_RESULT.json in this directory and make it match result.schema.json.
"""
(target / "BENCHMARK_TASK.md").write_text(task_md, "utf-8")

resource_setup = []
if run["mode"] == "A":
    resource_setup.append("No ExteraContext Knowledge or MCP resource is permitted for this run.")

elif run["mode"] == "B":
    knowledge_checkout = out / "resources" / "ExteraContext-Knowledge"
    resource_setup.extend([
        "Prepare a read-only checkout of raw Knowledge at exactly this commit:",
        "",
        f"- repository: `{KNOWLEDGE_REPO}`",
        f"- commit: `{frozen_knowledge_commit}`",
        f"- suggested path: `{knowledge_checkout}`",
        "",
        "Example operator setup:",
        "",
        "```bash",
        f'git clone "{KNOWLEDGE_REPO}" "{knowledge_checkout}"',
        f'git -C "{knowledge_checkout}" checkout --detach "{frozen_knowledge_commit}"',
        "```",
        "",
        "Expose that checkout read-only to the tested agent. Do not expose ExteraContext MCP.",
    ])

elif run["mode"] == "C":
    base_db = runtime / "exteracontext.sqlite"
    mutable_db = runtime / "knowledge.sqlite"
    run_root = runtime / "knowledge-runs"
    env = {
        "EXTERACONTEXT_KNOWLEDGE_REF": frozen_knowledge_commit,
        "EXTERACONTEXT_AUTO_SYNC": "1",
        "EXTERACONTEXT_DB": str(base_db),
        "EXTERACONTEXT_KNOWLEDGE_DB": str(mutable_db),
        "EXTERACONTEXT_RUN_ROOT": str(run_root),
    }
    (out / "MCP_BENCHMARK_ENV.json").write_text(json.dumps(env, ensure_ascii=False, indent=2) + "\n", "utf-8")
    resource_setup.extend([
        "Start a dedicated ExteraContext MCP instance for this run.",
        "",
        f"The MCP source checkout is `{REPO_ROOT}`, but its current KNOWLEDGE_LOCK must **not** control the benchmark.",
        f"Force the frozen Knowledge commit `{frozen_knowledge_commit}` and use the run-local databases under `{runtime}`.",
        "",
        "Use the exact environment in `MCP_BENCHMARK_ENV.json` when launching the MCP server.",
        "This isolates both the immutable base DB and mutable write-back DB from every other benchmark run.",
        "",
        "Suggested stdio server command:",
        "",
        "```bash",
        f'cd "{REPO_ROOT}"',
        "node mcp/src/index.mjs --transport stdio --modern-only",
        "```",
        "",
        "Do not expose the raw Knowledge checkout to the tested agent.",
    ])

(out / "RESOURCE_SETUP.md").write_text("# Benchmark resource setup\n\n" + "\n".join(resource_setup) + "\n", "utf-8")

launch = f"""# Launch this run

Protocol revision: **{protocol_revision}**

Open the following directory as a **new, clean DSH session/project**:

`{target}`

Before starting the tested agent, follow `RESOURCE_SETUP.md` from the run root.

Then instruct the agent:

> Read BENCHMARK_MODE.md and BENCHMARK_TASK.md, perform the benchmark task, and create BENCHMARK_RESULT.json.

Mode: {run['mode']}
Task: {run['task_id']}
Repeat: {run['repeat']}
Frozen Knowledge: {frozen_knowledge_commit}
"""
(out / "OPEN_IN_DSH.md").write_text(launch, "utf-8")

private_names = {"ground-truth.json", "assessment.schema.json", "EVALUATOR.md"}
for pth in out.rglob("*"):
    if pth.name in private_names:
        raise AssertionError(f"private evaluator file leaked into run packet: {pth}")

print(target)
