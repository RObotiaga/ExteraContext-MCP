# Evaluator procedure — protocol 1.1

The evaluator runs outside the tested agent context.

For each run:

1. Read the task from `tasks.json`.
2. Read only that task's entry in `ground-truth.json`.
3. Inspect the actual final answer/patch and any build/test/runtime evidence.
4. Read `BENCHMARK_RESULT.json`, but treat it as agent self-report rather than ground truth.
5. Create `assessment.json` matching `assessment.schema.json`.

Do not aggregate assessments from an older protocol revision with protocol 1.1.

## Required constraints

`ground-truth.json` defines `required_constraints` as a map of canonical constraint IDs to descriptions.

For every required constraint, write:

```json
"constraints": {
  "ui_thread_return": {
    "passed": false,
    "evidence": "Callback executes directly on PluginWorker_0 when no host UI runner is supplied."
  }
}
```

`constraints_handled=true` **if and only if every required constraint passed**. If a task has no required constraints, use an empty object and `constraints_handled=true`.

Do not infer aggregate success from the agent's prose. Inspect the implementation.

## Correct-unknown applicability

Use `correct_unknown_applicable` from ground truth.

- When it is `true`, set `correct_unknown` to true or false after evaluating whether the agent correctly preserved the unsupported/unknown/version-uncertain boundary.
- When it is `false`, set `correct_unknown=null`.

A normal implementation task does not earn Correct Unknown credit merely because the agent mentions uncertainty.

For a task whose requested outcome is correctly determining that something is unsupported/unknown, a correct evidence-based refusal can still be `materially_completed=true`.

## Validation tiers

These are separate metrics:

- `python_compile_success`: the produced Python source parses/compiles in the benchmark environment. This says nothing about ExteraGram API validity.
- `target_static_success`: target-specific imports/symbols/signatures can be statically validated against the allowed target artifacts.
- `load_success`: the plugin is actually loaded/enabled by the target client/runtime without error.
- `runtime_success`: the requested behavior is triggered and asserted in the target runtime.

Use `null` when a tier was not actually evaluated. Never promote Python compilation to target static success, or static success to load/runtime success.

## Test evidence

A tested agent may self-report tests, but a reported `pass` is not evidence by itself.

Protocol 1.1 requires every `tests[].status == "pass"` entry to include at least one reproducible locator:

- `evidence.command` — a command the evaluator can rerun; or
- `evidence.artifact_path` — a log/report/test artifact that actually exists.

Verify the command/artifact when practical. Count a pass in `verified_test_passes` only when the evidence is reproducible and supports the stated test. Record unsupported self-reported passes in `unverified_self_reported_test_passes`.

A missing test file/log must not be silently accepted because the agent says a test passed.

## Clean success

Set `clean_success=true` only when all applicable conditions are true:

- `materially_completed=true`;
- `invented_api=false`;
- `donor_contamination=false`;
- `version_mismatch=false`;
- `constraints_handled=true`;
- `evidence_boundary_correct=true`;
- when `correct_unknown_applicable=true`, `correct_unknown=true`;
- no ground-truth `hard_fail` condition occurred.

A runtime test is not universally required for clean success unless the task or ground truth explicitly requires it. But never claim a stronger validation tier than was actually demonstrated.

## Hard failures

A ground-truth `hard_fail` condition forces `clean_success=false` even if the produced code looks plausible.

Examples include:

- fabricated target API;
- donor API promoted to ExteraGram;
- exact-version compatibility inferred from generic evidence;
- callback account replaced by the currently selected UI account;
- reload-sensitive registration with no cleanup.

## Blinding

Do not let evaluator notes, ground truth, assessments, or previous candidate answers enter any later tested-agent session.

If an evaluator is an LLM, use a fresh evaluator context and do not use the same context that generated the candidate solution.
