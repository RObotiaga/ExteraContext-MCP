# ExteraContext MCP — closed-loop plugin development context

This document captures the current product and architecture context for the `feat/closed-loop-plugin-development` design track. It is maintained through a grill-with-docs process: accepted definitions and decisions are recorded here as they are agreed.

## Product purpose

ExteraContext MCP is **not** a standalone coding agent and must not become a second LLM orchestration product.

It is a development tool/control plane for an external coding agent implementing ExteraGram/AyuGram plugins. Its job is to support the full development loop, from helping the agent choose and implement the correct API path through build, deployment, runtime validation, diagnosis, retry, evidence capture, and reuse of successful implementations.

The external coding agent remains responsible for editing source code and making implementation changes.

## Target closed loop

```text
issue / task
  ↓
prepare task context
  ↓
project + platform knowledge
  ↓
implementation guidance / API verification
  ↓
external agent edits source
  ↓
build
  ↓
artifact identity + preflight
  ↓
deploy / reload
  ↓
runtime and acceptance validation
  ↓
PASS ───────────────→ evidence → reusable knowledge candidate
  │
 FAIL / BLOCKED
  ↓
diagnosis
  ├─ project knowledge
  ├─ platform knowledge
  ├─ logs/runtime observations
  └─ REA when static APK inspection is needed
  ↓
repair context returned to the external agent
  ↓
external agent edits source
  ↓
build / deploy / validate again
```

## Accepted principles

1. **External agent owns source edits.** ExteraContext MCP does not host an LLM and does not independently rewrite plugin source as an autonomous agent.
2. **ExteraContext MCP owns the development evidence loop.** It should make the path from task context to validated implementation reproducible and machine-readable.
3. **Help while writing is part of the product.** Retrieval, API/signature resolution, recipes, compatibility checks, project constraints, and REA-assisted inspection should help the agent choose the correct implementation before runtime testing.
4. **Runtime validation is first-class.** Build success, source inspection, and documentation are not substitutes for execution when the ticket requires device/runtime evidence.
5. **Successful implementations become reusable knowledge.** Runtime- or acceptance-confirmed discoveries should enter the existing guarded collector/verifier knowledge pipeline rather than being trusted solely because the implementing agent reports success.
6. **Evidence status remains explicit.** `code`, `docs`, `static-apk`, runtime observation, behavior verification, and ticket acceptance must not collapse into one generic "verified" state.
7. **The current trusted knowledge/write-back model is preserved.** The closed loop extends the MCP; it does not weaken independent verification or machine-attestation requirements.

## Current implementation baseline

The existing MCP is primarily a knowledge/evidence plane: retrieval, provenance, compatibility queries, staged write-back, knowledge orchestration, and diagnostics. `record_runtime_result` is intentionally fail-closed without trusted machine attestation.

The closed-loop design will extend this baseline with project/task context, build/artifact identity, device/runtime execution, acceptance plans, diagnostic routing, retry state, and evidence hand-off.

## Open design questions

Decisions below are intentionally unresolved until they are grilled and accepted:

- How a development run is represented and persisted.
- Whether build/deploy/test operations are exposed as atomic tools, a high-level state machine, or both.
- How projects define their build, artifact, deploy, and acceptance adapters without granting arbitrary shell authority.
- Which failures automatically route to Knowledge, REA, device runtime, or back to the coding agent.
- How ticket acceptance requirements become executable test plans.
- What qualifies an implementation for automatic knowledge-candidate creation.
- Which runtime evidence must be real-device versus emulator-capable.
