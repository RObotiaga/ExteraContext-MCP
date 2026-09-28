# Evaluator procedure

The evaluator runs outside the tested agent context.

For each run:

1. Read the task from `tasks.json`.
2. Read only that task's entry in `ground-truth.json`.
3. Inspect the actual final answer/patch and any build/test/runtime evidence.
4. Read `BENCHMARK_RESULT.json`, but treat it as agent self-report rather than ground truth.
5. Create `assessment.json` matching `assessment.schema.json`.

## Clean success

Set `clean_success=true` only when:

- `materially_completed=true`;
- `invented_api=false`;
- `donor_contamination=false`;
- `version_mismatch=false`;
- `constraints_handled=true`;
- evidence boundaries are correct;
- for a correct-unknown task, `correct_unknown=true`.

For implementation tasks where runtime infrastructure is unavailable, `runtime_success` may be null. Do not convert a static-only pass into runtime success.

## Hard failures

A ground-truth `hard_fail` condition forces `clean_success=false` even if the produced code looks plausible.

Examples include:

- fabricated target API;
- donor API promoted to ExteraGram;
- exact-version compatibility inferred from generic evidence;
- callback account replaced by the currently selected UI account;
- reload-sensitive registration with no cleanup.

## Blinding

Do not let evaluator notes, ground truth, or assessments enter any later tested-agent session.

If an evaluator is an LLM, use a fresh evaluator context and do not use the same context that generated the candidate solution.
