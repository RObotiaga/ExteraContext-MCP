# Agent benchmark v1

This directory contains the first frozen end-to-end A/B/C benchmark for ExteraContext MCP.

## What it compares

- **A** — baseline agent, no ExteraContext knowledge.
- **B** — same agent with the frozen raw Knowledge repository.
- **C** — same agent with ExteraContext MCP, without direct Knowledge access.

The benchmark is frozen to Knowledge commit:

`70f6f614227c8b02d241e7b1e72a0b6691442fd1`

## Files

- `tasks.json` — public task prompts.
- `prompts/` — mode-specific instructions.
- `result.schema.json` — tested-agent output contract.
- `ground-truth.json` — evaluator-only material; never copy it into the tested agent workspace.
- `assessment.schema.json` — evaluator record format.
- `run-manifest.json` — 60-run minimum schedule.
- `aggregate.py` — aggregate evaluator records.

## Recommended procedure

For each manifest row:

1. Start a completely fresh DSH/model session.
2. Reset the target fixture/project to the same commit.
3. Expose only the resources permitted by that mode.
4. Give the corresponding mode prompt plus the task text.
5. Save the final worktree/patch, `BENCHMARK_RESULT.json`, runner telemetry and logs under a unique run directory.
6. Outside the tested agent context, score the run with `ground-truth.json` and write `assessment.json`.
7. Do not let results from one run enter the context of another.

After all runs:

```bash
python benchmark/agent-v1/aggregate.py path/to/benchmark-results
```

The primary metric is Clean Task Success Rate. Treat missing structured output as a failed structured run rather than silently dropping it.
