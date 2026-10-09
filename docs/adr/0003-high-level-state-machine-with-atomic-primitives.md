# ADR-0003: High-level development state machine with atomic diagnostic primitives

Status: Accepted

## Context

ExteraContext MCP is being extended from a knowledge/evidence plane into a closed-loop plugin-development control plane for an external coding agent. The MCP must guarantee safe ordering and evidence binding for build, artifact verification, deployment, runtime validation, and acceptance testing, while still permitting focused investigation when a standard iteration fails.

A purely atomic tool surface would force the external coding agent to act as the workflow orchestrator. That makes it easy to omit preflight checks, test a stale artifact, mix evidence across iterations, or attach a runtime result to the wrong source identity.

A single opaque `execute everything` operation would protect the happy path but make diagnosis, selective reruns, and unusual project/runtime failures unnecessarily difficult.

## Decision

Expose both levels.

### High-level safe path

The normal development workflow is represented by stateful tools such as:

```text
prepare_task_context
start_development_run
submit_source_revision
execute_iteration
```

`execute_iteration` owns the canonical sequence:

```text
source identity
→ build
→ artifact identity
→ artifact preflight
→ deploy / reload
→ deployed identity preflight
→ acceptance tests
→ evidence collection
→ PASS / FAIL / BLOCKED / INFRA_ERROR
```

The operation never edits source code. On failure it returns structured diagnostic and repair context to the external coding agent. Source changes begin a new iteration.

### Atomic diagnostic primitives

Narrow tools remain available for diagnostics and exceptional control, including build/artifact inspection, device/runtime inspection, log/evidence collection, REA inspection, and selective test reruns.

Atomic tools remain bound to the active `DevelopmentRun` and `Iteration`; they do not bypass source/artifact/deployment identity, authorization, attestation, or evidence integrity rules.

## Consequences

- The safest development path is also the easiest path for an external coding agent.
- Artifact and deployment preflight cannot be accidentally skipped in the normal workflow.
- The MCP remains debuggable rather than becoming an opaque pipeline.
- Tool schemas must distinguish orchestration operations from diagnostic primitives.
- Every diagnostic result must carry run/iteration identity and cannot silently become acceptance evidence for another iteration.
- The state machine must expose failure stage and structured `repair_context` rather than only a generic error string.
