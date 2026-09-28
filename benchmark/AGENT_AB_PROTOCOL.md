# ExteraContext end-to-end agent A/B/C benchmark v1

This benchmark measures whether ExteraContext improves an agent's real ExteraGram/AyuGram plugin-development work. It is intentionally separate from retrieval Hit@K benchmarks and from MCP acceptance tests.

## Frozen benchmark state

- MCP repository: record the exact commit for every run.
- Knowledge corpus: `70f6f614227c8b02d241e7b1e72a0b6691442fd1`.
- ExteraContext MCP baseline: v0.6.1.
- Use the same model, model build, reasoning level, target repository commit, tool permissions and time/token budget for all modes in one comparison.

Do not silently update the Knowledge corpus in the middle of a benchmark series.

## Modes

### A — baseline

Give the agent only the target project/fixture and task prompt.

Forbidden:
- ExteraContext MCP;
- ExteraContext Knowledge/wiki;
- copied answers from other modes;
- web search for ExteraGram/AyuGram API facts.

Normal local code inspection and ordinary language/runtime libraries are allowed.

### B — raw Knowledge

Give the same target project/fixture and task prompt plus read-only access to the frozen `ExteraContext-Knowledge` checkout.

Forbidden:
- ExteraContext MCP;
- `scripts/query.py` or other MCP retrieval helpers;
- answers from previous runs;
- web search for ExteraGram/AyuGram API facts.

The agent must search/read the Knowledge repository manually.

### C — ExteraContext MCP

Give the same target project/fixture and task prompt and the ExteraContext MCP connection.

Forbidden:
- direct access to the Knowledge checkout/database;
- direct SQLite access;
- copied answers from other runs;
- web search for ExteraGram/AyuGram API facts.

The agent should use MCP retrieval normally and preserve evidence boundaries.

## Isolation

Every task/mode/repeat is a fresh process/session.

- Same model/version/reasoning setting.
- Same target repository/fixture commit.
- Same task text.
- Same target client/SDK metadata.
- Same maximum wall time and token budget.
- No continuation across modes.
- No previous generated patch in the worktree.
- Mutable ExteraContext knowledge must start from the same benchmark snapshot for every C run unless the task explicitly tests write-back.
- Run order is defined by `agent-v1/run-manifest.json`; do not regroup all A, then all B, then all C.

## Public vs private benchmark material

Only copy these items into the agent workspace:

- the target project/fixture;
- the selected task text from `agent-v1/tasks.json`;
- the mode-specific prompt;
- the result schema.

Do **not** expose `agent-v1/ground-truth.json` or evaluator notes to the tested agent.

The benchmark operator/evaluator may use the private ground truth after the run.

## Agent output contract

Every run must create `BENCHMARK_RESULT.json` matching `agent-v1/result.schema.json`.

The final implementation/answer must also remain in the target worktree so it can be inspected and, where possible, built or tested.

A run that does not produce a parseable result file is still a run and is scored as a failure for structured-completion metrics.

## Primary outcome

The primary metric is **Clean Task Success Rate**.

A run is clean-success only when all applicable conditions are true:

1. The requested task is materially completed.
2. No invented or unsupported target API is used as established fact.
3. No donor-only API is presented as target-supported.
4. No known target-version incompatibility is ignored.
5. Required account/thread/lifecycle constraints are handled.
6. For unknown/unsupported cases, the agent refuses to fabricate an implementation and states the evidence gap.

This is intentionally stricter than "produced plausible code".

## Secondary metrics

Per mode report:

- task completion rate;
- clean task success rate;
- unsupported/invented API rate;
- donor contamination rate;
- version mismatch rate;
- correct-unknown rate;
- static/build/load/runtime success when applicable;
- repair iterations;
- tool calls;
- wall time;
- input/output tokens when available;
- evidence-boundary correctness;
- unnecessary low-level fallback rate (reflection/Xposed when a supported higher-level API exists).

## Minimum suite

v1 contains 10 tasks.

Run:

`10 tasks × 3 modes × 2 repeats = 60 fresh runs`

Do not tune ExteraContext against these 60 results and then report the same set as unbiased. After any benchmark-driven retrieval changes, create a new holdout set.

## Runtime tiers

When a compatible Android test environment is available:

1. **Static** — imports/symbols/signatures resolve.
2. **Load** — plugin installs/enables without error.
3. **Runtime** — requested behavior is actually triggered and asserted.

Lifecycle tasks should test enable → trigger → disable → trigger → enable → trigger → repeated reloads and ensure callbacks do not multiply.

Multi-account tasks should trigger equivalent events under at least two account IDs and verify that the callback account is preserved end-to-end.

Static/docs evidence must never be relabeled runtime-verified merely because the agent produced code.

## Evaluation

Use `agent-v1/ground-truth.json` only from the evaluator context.

For each run create an evaluator record matching `agent-v1/assessment.schema.json`. Then aggregate all records with:

```bash
python benchmark/agent-v1/aggregate.py benchmark-results/
```

The evaluator should inspect the actual patch/answer, not only trust the agent's self-reported `BENCHMARK_RESULT.json`.

## Interpretation

The benchmark answers:

> Does ExteraContext MCP improve reliable plugin-development outcomes compared with no knowledge system and with the same raw knowledge exposed directly?

It does not by itself prove runtime correctness of the underlying ExteraGram APIs. That still requires device/runtime evidence.
