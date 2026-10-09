---
name: exteracontext
description: Evidence-aware ExteraGram/AyuGram plugin development context and closed-loop runtime validation. Use when implementing, reviewing, debugging, porting, testing, or researching Android plugin APIs, hooks, lifecycle, UI, requests, accounts, Java/Xposed/DEX bridges, Elyx packaging, or SDK compatibility. Reach for it before using a plugin/runtime symbol not defined in the current repository and use the DevelopmentRun tools when real runtime acceptance is required.
---

# ExteraContext

Use task-specific evidence from the bundled ExteraGram plugin-development knowledge base, then use the trusted runtime backend when the task requires execution on a client/device.

External agent edits/builds source; ExteraContext validates revisions, records evidence, and guards knowledge capture.

## Operating loop

Use this order:

```text
resolve target
→ retrieve focused project/platform evidence
→ verify every load-bearing external symbol
→ external agent implements/builds
→ DevelopmentRun / Iteration validation when runtime evidence is required
→ diagnose and repair on FAIL/BLOCKED
→ capture reusable new knowledge through collector/blind verifier
```

When ExteraContext MCP tools are registered, prefer them over shelling out to the CLI. The `scripts/*.py` commands are local/debug fallbacks and must preserve the same evidence semantics. First verify the locally deployed base DB exists: missing data must not trigger an automatic download or corpus rebuild. Keep `EXTERACONTEXT_AUTO_SYNC=0` unless an approved deployment explicitly enables the updater.

Keep each retrieval query to one concept. Split independent concerns such as outgoing hooks, account routing, UI threading, cleanup, or packaging into separate queries; combine them only when their interaction is itself the subject.

## Resolve the target

Infer client, platform, client version, SDK version, and plugin format from the repository or request. Keep unknown fields unknown; do not manufacture a version.

Completion criterion: the target is explicit enough that evidence from another client cannot silently masquerade as target API.

## Get the task packet

With MCP, call `search_knowledge` for each concept after `resolve_target`. CLI fallback:

```bash
python scripts/query.py context "<task>" --target "<client/platform>" [--client-version X] [--sdk-version X]
```

Treat the packet as retrieval, not authority. Prefer `runtime-verified`, then target `code`, then official `docs`. `inference` is a lead; donor-client evidence is a design reference. A high-ranked result is not permission to use a symbol for the target.

If a concept has no direct target evidence, stop treating it as resolved. Record the gap or narrow the claim instead of repeatedly broadening the search until donor evidence looks authoritative.

## Resolve every non-local symbol

Before writing a nontrivial ExteraGram/AyuGram runtime symbol that is not defined in the working repository, call MCP `find_api`. For provenance, call `get_evidence`. CLI fallback:

```bash
python scripts/query.py api "<symbol>"
python scripts/query.py evidence "<fact-id-or-symbol>"
```

Use the exact signature/module/version supported by the evidence. A donor implementation may suggest architecture, but its symbol becomes usable only after target-client evidence is found.

Completion criterion: every load-bearing external symbol is either evidenced for the target or explicitly marked as an unresolved compatibility assumption.

## Reach for recipes only on that branch

For patterns such as lifecycle cleanup, accounts, UI threading, DEX packaging, reload, or release provenance, use MCP `get_recipe`. CLI fallback:

```bash
python scripts/query.py recipe "<pattern>"
```

Do not load the whole wiki unless focused retrieval is insufficient.

## Use the closed-loop runtime path

When a task requires actual client/device behavior, do not stop at build success or source inspection. Prefer high-level development tools over manually sequencing lifecycle calls.

Start one persistent run per task/issue:

```text
development_start
```

The run binds project/task identity, target device/client, and an acceptance plan. After the external agent edits and builds the plugin, submit the exact EAF/source revision:

```text
development_submit_revision
```

This creates the next `Iteration` and binds commit/dirty state plus artifact identity. Then execute:

```text
development_execute_iteration
```

The runner owns the ordering:

```text
preflight
→ artifact/install/update checks
→ enable/reload
→ reset state
→ acceptance test
→ signed evidence on PASS
   or diagnostics + repair_context on failure
```

Treat results distinctly:

- `PASS`: the exact tested revision satisfied the acceptance plan.
- `FAIL`: the correct revision ran in a usable environment but behavior/assertions failed.
- `BLOCKED`: environment/artifact/lifecycle conditions prevented a valid functional test.
- `INFRA_ERROR`: the test infrastructure itself failed.

On `FAIL` or `BLOCKED`, use returned diagnostics/`repair_context`, change source externally, build again, call `development_submit_revision`, and execute the new iteration. Never attach evidence from an older iteration to a newer revision.

Use atomic tools such as `plugin_doctor`, `plugin_get_logs`, `plugin_get_diagnostics`, `plugin_capture_screen`, lifecycle controls, navigation, and assertions for unusual failures. They are diagnostic primitives, not a way to bypass run/iteration identity.

## Runtime evidence boundary

Caller-provided PASS/FAIL is never runtime evidence. The trusted path is:

```text
test_run
→ runner-owned test_run_id
→ test_collect_evidence(test_run_id)
→ signed EvidenceBundle
```

The device runner is a separately spawned, build-pinned trust domain. ExteraContext verifies its build/tool contract before delegating device calls. The coding agent must not receive the runner's attestation secret.

The public `record_runtime_result` endpoint remains fail-closed because its result is caller-controlled. Do not use it instead of signed EvidenceBundle evidence.

A signed EvidenceBundle may enter ExteraContext as `runtime-verified` machine evidence. This does **not** automatically make every derived statement verified knowledge. Scope the claim to what the test demonstrated.

## Preserve evidence status

Keep these meanings distinct:

- `runtime-verified`: executed evidence produced by the trusted runtime path for a named target/build.
- `code`: found in a specific source snapshot.
- `docs`: documented, not runtime-proven.
- `inference`: synthesis from evidence.
- `secondary`: community/radar claim.
- `unavailable`: source could not be acquired.

Successful build, source inspection, README text, or an agent's opinion does not become runtime verification.

Legacy wiki facts may carry collector/reviewer provenance from the original 2026-09-27 build. Treat that as `independent-source-reread-nonblind`: useful auditability, but not equivalent to blind Phase-A verification and not an upgrade from `docs`/`code` to runtime evidence.

## Capture reusable runtime knowledge

If a PASS iteration reveals reusable information absent from ExteraContext or materially confirms/contradicts an existing claim, prefer:

```text
knowledge_propose_from_test
```

This asks the trusted runner to re-validate the PASS run/evidence attestation and starts the normal ExteraContext capture workflow. It does not directly promote the claim.

Required promotion path:

```text
trusted runtime evidence
→ reflect_on_task
→ collector
→ blind verifier Phase A
→ verifier comparison Phase B
→ SQLite promotion guards
```

A narrow test must produce a narrow claim. One tested hook/client/version combination is not evidence it works across all versions or clients.

## Capture other new knowledge

When development, source inspection, or research reveals reusable information without a trusted runtime run, follow [`NewKnowledge.md`](NewKnowledge.md).

The main agent must not promote its own discovery directly. Dispatch a collector to produce a scoped candidate from original evidence, then an independent verifier. The verifier first forms its own extraction from original evidence **without seeing the candidate**, persists Phase A, and only then receives the candidate for Phase B comparison.

Prefer the MCP staged write path:

```text
reflect_on_task
→ submit_collector_result
→ submit_verifier_phase_a
→ submit_verifier_phase_b
```

MCP-issued collector/verifier capability tokens authorize those stages. Runtime actor identity is optional provenance, not authorization. Never write trusted rows directly to SQLite.

Completion criterion: a reusable discovery is already represented, passed through staged verification, or explicitly left unpersisted for insufficient evidence.

## Finish

Before presenting or committing an implementation, check that:

- target-client evidence supports the critical API path;
- lifecycle cleanup and account/thread boundaries are explicit where relevant;
- version uncertainty is stated rather than hidden;
- tasks requiring runtime behavior have an appropriate `DevelopmentRun` acceptance result;
- tested source/artifact identity matches the revision being claimed;
- reusable discoveries are captured through the guarded pipeline or intentionally left unpersisted.

See [`docs/CLOSED_LOOP_DEVICE_BACKEND.md`](docs/CLOSED_LOOP_DEVICE_BACKEND.md), [`KnowledgeStore.md`](KnowledgeStore.md), [`NewKnowledge.md`](NewKnowledge.md), and [`docs/PRODUCTION_READINESS.md`](docs/PRODUCTION_READINESS.md).
