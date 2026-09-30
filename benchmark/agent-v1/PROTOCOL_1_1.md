# Protocol 1.1 revision note

Protocol 1.1 was introduced after inspecting the first Mode A pilot run.

The pilot exposed four evaluator/setup defects:

1. `constraints_handled` could be true even when an explicitly required constraint (UI-thread return) failed.
2. `correct_unknown` could be credited on ordinary implementation tasks where it was not an applicable metric.
3. Python compilation and target-specific static validation were conflated.
4. Agent self-reported test passes could appear without a rerunnable command, test file or log artifact.

A fifth fairness issue was discovered at the same time: the normal MCP `KNOWLEDGE_LOCK` had advanced after the benchmark was frozen, so Mode C could receive a newer corpus than Mode B.

Protocol 1.1 fixes all five:

- explicit per-task `required_constraints`;
- explicit `correct_unknown_applicable`;
- `python_compile_success` vs `target_static_success`;
- reproducible test-pass evidence contract;
- run-local MCP databases pinned with `EXTERACONTEXT_KNOWLEDGE_REF=70f6f614227c8b02d241e7b1e72a0b6691442fd1`.

The original pre-1.1 `v1-001` result is pilot data only. Regenerate and rerun `v1-001`, `v1-002` and `v1-003` before interpreting the first A/B/C triplet.
