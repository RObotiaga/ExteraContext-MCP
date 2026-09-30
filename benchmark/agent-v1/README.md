# Agent benchmark v1

This directory defines the frozen **protocol 1.1** A/B/C benchmark for ExteraContext MCP. It is a procedure, not a claim that 60 device runs have occurred. The prior single Mode A v1-001 result is pilot-only and excluded from aggregates; no C/B comparison follows from it.

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

The primary metric is Clean Task Success Rate. `aggregate.py` rejects stale, duplicate or inconsistent non-pilot assessments, excludes explicit pilot records and reports null (not zero) for unavailable denominators. A missing run/result must be evaluated as a failure outside the agent session before aggregation, never silently omitted. A pilot-only directory has no eligible sample and aggregation fails rather than reporting an A/B/C result.\n\nRun offline checks: `python tests/test_benchmark_protocol.py` and `python benchmark/agent-v1/validate.py`. For agent claims use `python benchmark/agent-v1/validate_result.py RUN/target/BENCHMARK_RESULT.json`; a command alone is not independent proof of a PASS.


## Self-contained run packets

`prepare_run.py` now creates an isolated `target/` project. Open that exact directory in a fresh DSH session; no external target fixture is required.

Example:

```bash
python benchmark/agent-v1/prepare_run.py v1-001 ./run-v1-001
```

Then open `./run-v1-001/target` in DSH and follow `BENCHMARK_MODE.md` + `BENCHMARK_TASK.md`.

Before running B, use `python benchmark/agent-v1/prepare_run.py v1-002 ./fresh-b --frozen-checkout PATH_TO_LOCAL_KNOWLEDGE` (must have exact pinned HEAD). Before C, use `python benchmark/agent-v1/prepare_run.py v1-003 ./fresh-c --frozen-db PATH_TO_PINNED_SQLITE` (adjacent `.knowledge-ref` must equal the pinned commit); no download/sync is performed. The C packet includes `MCP_BENCHMARK_ENV.json` with pinned ref and per-run isolated base/mutable SQLite and orchestration root, `EXTERACONTEXT_AUTO_SYNC=0`. Without locally verified resources the packet's `RESOURCE_SETUP.md` says **NOT READY**; do not launch it. B exposes only raw checkout; C exposes only MCP; A exposes neither. Do not regenerate over nonempty directories, including the existing pilot. Device sessions remain manual and fresh.\n\nThe frozen DB stamp checks the declared ref and SQLite integrity, not the provenance of every row: the operator must independently establish the base SQLite was built from the exact frozen Knowledge commit. See `EVALUATOR.md` for per-constraint scoring, nullable correct-unknown, syntax versus target-static checks and test-evidence handling.
