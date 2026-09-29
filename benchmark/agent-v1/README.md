# Agent benchmark v1 — protocol 1.1

This directory contains the frozen end-to-end A/B/C benchmark for ExteraContext MCP.

## What it compares

- **A** — baseline agent, no ExteraContext knowledge.
- **B** — same agent with the frozen raw Knowledge repository.
- **C** — same agent with ExteraContext MCP, without direct Knowledge access.

The benchmark is frozen to Knowledge commit:

`70f6f614227c8b02d241e7b1e72a0b6691442fd1`

Protocol revision **1.1** fixes evaluator/test-evidence semantics and makes the frozen Knowledge pin explicit for both B and C.

Results produced with the earlier protocol must be treated as pilot data and must not be mixed into the final 60-run aggregate. In particular, the original `v1-001` run should be rerun from a newly generated packet.

## Files

- `tasks.json` — public task prompts.
- `prompts/` — mode-specific instructions.
- `result.schema.json` — tested-agent output contract.
- `ground-truth.json` — evaluator-only material; never copy it into the tested agent workspace.
- `assessment.schema.json` — evaluator record format.
- `run-manifest.json` — 60-run schedule with frozen protocol/Knowledge metadata.
- `prepare_run.py` — generates an isolated target and mode-specific operator resource setup.
- `EVALUATOR.md` — evaluator rules.
- `aggregate.py` — aggregate evaluator records.
- `validate.py` — benchmark/regression validation.

## Protocol 1.1 corrections

- Required task constraints are evaluated individually. `constraints_handled=true` only when every required constraint passes.
- `correct_unknown` is null for tasks where that metric is not applicable.
- Python compilation and target-specific static validation are separate metrics.
- A self-reported test `pass` must include a rerunnable command or an existing artifact/log path.
- Mode C uses its own run-local immutable/mutable SQLite paths and forces the same frozen Knowledge commit used by Mode B.
- The aggregator rejects legacy/mixed assessment revisions.

## Recommended procedure

For each manifest row:

1. Pull the current benchmark code.
2. Generate a **new** run packet:
   ```bash
   python benchmark/agent-v1/prepare_run.py v1-001 ./run-v1-001
   ```
3. Follow `run-v1-001/RESOURCE_SETUP.md` before launching the tested agent.
4. Start a completely fresh DSH/model session and open only `run-v1-001/target`.
5. Use the same model/version/reasoning setting, target fixture, time/token budget and ordinary tool permissions across A/B/C.
6. Save the final worktree, `BENCHMARK_RESULT.json`, runner telemetry and logs under the unique run directory.
7. Outside the tested-agent context, score the run with `ground-truth.json` and write a protocol-1.1 `assessment.json`.
8. Do not let previous answers, ground truth or evaluator notes enter another run.

After all runs:

```bash
python benchmark/agent-v1/aggregate.py path/to/benchmark-results
```

The primary metric is Clean Task Success Rate.

## Self-contained target packets

Every run contains:

```text
run-v1-XXX/
├── OPEN_IN_DSH.md
├── RESOURCE_SETUP.md
├── RUN.json
├── TASK.json
├── runtime/
└── target/
    ├── README.md
    ├── plugin.py
    ├── fixture.json
    ├── BENCHMARK_MODE.md
    ├── BENCHMARK_TASK.md
    └── result.schema.json
```

Mode B additionally needs a read-only checkout of the exact frozen Knowledge commit described in `RESOURCE_SETUP.md`.

Mode C must launch a dedicated MCP instance using `MCP_BENCHMARK_ENV.json`. Those environment variables override the repository's current `KNOWLEDGE_LOCK` and isolate the base DB, mutable knowledge DB and write-back run root for that one benchmark run.
