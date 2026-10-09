# ExteraContext MCP — closed-loop plugin development context

This document captures the current product and architecture context for the `feat/closed-loop-plugin-development` design track. It is maintained through a grill-with-docs process: accepted definitions and decisions are recorded here as they are agreed.

## Product purpose

ExteraContext MCP is **not** a standalone coding agent and must not become a second LLM orchestration product.

It is a development tool/control plane for an external coding agent implementing ExteraGram/AyuGram plugins. Its job is to support the full development loop, from helping the agent choose and implement the correct API path through deployment, runtime validation, diagnosis, retry, evidence capture, and reuse of successful implementations.

The external coding agent remains responsible for editing source code and producing the plugin build submitted for validation.

## Target closed loop

```text
issue / task
  ↓
project + platform knowledge
  ↓
implementation guidance / API verification
  ↓
external agent edits + builds source
  ↓
DevelopmentRun / Iteration
  ↓
artifact identity + preflight
  ↓
deploy / reload
  ↓
runtime and acceptance validation
  ↓
PASS ───────────────→ signed evidence → reusable knowledge candidate
  │
 FAIL / BLOCKED
  ↓
diagnostics + repair_context
  ↓
external agent edits source
  ↓
new Iteration
```

## Accepted principles

1. **External agent owns source edits.** ExteraContext MCP does not host an LLM and does not independently rewrite plugin source as an autonomous agent.
2. **ExteraContext MCP owns the development evidence loop.** It makes the path from task context to validated implementation reproducible and machine-readable.
3. **Help while writing is part of the product.** Retrieval, API/signature resolution, recipes, compatibility checks, project constraints, and static/runtime inspection should help the agent choose the correct implementation before acceptance testing.
4. **Runtime validation is first-class.** Build success, source inspection, and documentation are not substitutes for execution when the task requires runtime evidence.
5. **Successful implementations can become reusable knowledge.** Runtime-confirmed discoveries enter the existing guarded collector/verifier pipeline rather than becoming trusted solely because the implementing agent reports success.
6. **Evidence status remains explicit.** Static evidence, runtime observations, behavior verification, and accepted knowledge are distinct states.
7. **The current trusted knowledge/write-back model is preserved.** The closed loop extends the MCP; it does not bypass collector/blind-verifier or SQLite promotion guards.
8. **Development state is persisted outside the project repository.** One task maps to a `DevelopmentRun`; each source/artifact revision maps to a child `Iteration`.
9. **Development state and reusable knowledge are separate stores.** Device-runner `development.sqlite` owns run/iteration state; ExteraContext knowledge storage remains the durable knowledge plane.
10. **Project repositories remain declarative.** Device-local transient state, logs, test journals, and evidence bundles are not committed as project state.
11. **The normal path is a high-level development state machine.** The external agent should not have to manually remember preflight → install/update → reload → reset → acceptance → evidence ordering.
12. **Atomic primitives remain available for diagnosis.** They do not authorize stale-artifact attachment or caller-asserted PASS states.
13. **One MCP surface, isolated runtime trust domain.** The coding agent talks only to ExteraContext MCP; ExteraContext delegates device/runtime work to a separately spawned trusted `extera-plugin-test-mcp` process over stdio.
14. **The trusted runner is build-pinned.** ExteraContext verifies `runner-build.json`, server identity/version, and the 27-tool contract before delegated calls.
15. **Caller-provided PASS/FAIL is never runtime proof.** `test_run` owns the result; `test_collect_evidence` accepts only `test_run_id`; the signed EvidenceBundle is the canonical runtime object.
16. **Machine evidence and knowledge promotion are different boundaries.** Attested runtime evidence can enter ExteraContext as `runtime-verified` evidence, but the statement derived from it remains a candidate until collector, blind verifier Phase A, comparison Phase B, and promotion guards succeed.

## Development state model

```text
DevelopmentRun
├── project identity
├── task / issue identity
├── target client / device
├── acceptance plan
├── status
│
├── Iteration 1
│   ├── source commit + dirty flag
│   ├── plugin ID/version
│   ├── EAF SHA-256
│   ├── lifecycle/preflight result
│   ├── test_run_id
│   ├── signed EvidenceBundle or diagnostics
│   └── repair_context
│
└── Iteration N ...
```

A new source/artifact identity starts a new `Iteration`; evidence from an older iteration must never be silently attributed to a newer one.

## Implemented MCP interaction model

### Knowledge core

The existing ExteraContext tools remain responsible for target resolution, retrieval, compatibility, provenance, and guarded knowledge capture.

### Trusted device/runtime backend

ExteraContext now proxies the reviewed 27-tool `extera-plugin-test-mcp` surface through an isolated stdio subprocess. The backend covers:

- ADB/device and PySDK readiness;
- plugin install/update/reload/enable/disable, logs and navigation;
- deterministic assertions, diagnostics, `test_run` and signed evidence;
- `DevelopmentRun` / `Iteration` orchestration;
- runtime-backed knowledge proposals.

The normal coding-agent path is:

```text
development_start
→ external agent edits/builds
→ development_submit_revision
→ development_execute_iteration
   ├─ PASS → signed EvidenceBundle
   └─ FAIL/BLOCKED → diagnostics + repair_context
→ external agent fixes/builds
→ next revision / iteration
```

For a reusable discovery:

```text
knowledge_propose_from_test
→ trusted runner verifies PASS + attestation
→ ExteraContext reflect_on_task orchestration
→ collector
→ blind verifier Phase A
→ comparison Phase B
→ promotion guards
```

The old caller-controlled `record_runtime_result` endpoint remains disabled.

## Trust pin for the first integrated backend

- reviewed archive SHA-256: `8033725591222a159e882a43e9a85d2f5ce7b13d1d6bdd24daf4177b5c3f8cf0`
- exact archive size: `2,417,871` bytes
- runner package/server version: `0.1.0`
- pinned compiled SHA-256: `240ab1d6d164b1e14b04d47289c7e6e17690d84ac78b72997c7767a2c2f742cc`

See `docs/CLOSED_LOOP_DEVICE_BACKEND.md` and ADR-0004 for deployment and trust details.

## Remaining design questions

The core closed-loop architecture is implemented. Remaining work is narrower and can evolve independently:

- whether ExteraContext should eventually own a trusted build adapter, or continue accepting builds produced by the external coding agent;
- when failure diagnosis should automatically invoke REA/static APK inspection rather than only returning `repair_context`;
- which acceptance plans specifically require a physical device and which are allowed to pass on an emulator;
- how project-specific acceptance manifests should be versioned and reviewed for broader plugin repositories.
