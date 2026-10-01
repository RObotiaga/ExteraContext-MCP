---
name: exteracontext
description: Evidence-aware ExteraGram/AyuGram plugin development context. Use when implementing, reviewing, debugging, porting, or researching Android plugin APIs, hooks, lifecycle, UI, requests, accounts, Java/Xposed/DEX bridges, Elyx packaging, or SDK compatibility. Reach for it before using a plugin/runtime symbol not defined in the current repository.
---

# ExteraContext

Retrieve task-specific evidence from the bundled ExteraGram plugin-development wiki instead of relying on remembered API names.

## Operating loop

Use this order: resolve the target → split the task into retrieval concepts → fetch a task packet for each concept → verify every load-bearing external symbol → implement/review → capture reusable new knowledge.

When ExteraContext MCP tools are registered, prefer them over shelling out to the CLI. In DeepSeek Harness the names are normally `mcp__exteracontext__resolve_target`, `mcp__exteracontext__search_knowledge`, `mcp__exteracontext__find_api`, and so on. The `scripts/*.py` commands documented below are the local/debug fallback and must preserve the same evidence semantics. First verify the locally deployed base DB exists: missing data must not trigger an automatic download or corpus rebuild. For offline bootstrap of an **existing** approved SQLite corpus, see [`docs/PRODUCTION_READINESS.md`](docs/PRODUCTION_READINESS.md); its manifest hashes the deployed copy but reports `commit_verified: false`, not a verified Knowledge commit pin. Keep `EXTERACONTEXT_AUTO_SYNC=0`.

Keep each retrieval query to one concept. Split independent concerns such as outgoing hooks, account routing, UI threading, and cleanup into separate queries; combine them only when the interaction between those concerns is itself the subject. Prefer a small set of focused packets over loading the whole wiki.

## Resolve the target

Infer client, platform, client version, SDK version, and plugin format from the repository or request. Keep unknown fields unknown; do not manufacture a version.

Completion criterion: the target is explicit enough that evidence from another client cannot silently masquerade as target API.

If a target field cannot be established from the repository or request, keep it unknown and preserve that uncertainty in retrieval and conclusions.

## Get the task packet

With MCP, call `search_knowledge` for each concept after `resolve_target`. CLI fallback:

```bash
python scripts/query.py context "<task>" --target "<client/platform>" [--client-version X] [--sdk-version X]
```

Treat the packet as retrieval, not authority. Prefer `runtime-verified`, then target `code`, then official `docs`. `inference` is a lead; donor-client evidence is a design reference. A high-ranked result is not permission to use a symbol for the target.

If a concept has no direct target evidence, stop treating it as resolved. Record the gap or narrow the claim instead of repeatedly broadening the search until donor evidence looks authoritative.

Completion criterion: you have either direct ExteraGram evidence for the approach or an explicit gap saying direct evidence is absent.

## Resolve every non-local symbol

Before writing a nontrivial ExteraGram/AyuGram runtime symbol that is not defined in the working repository, call MCP `find_api`. For provenance, call `get_evidence`. CLI fallback:

```bash
python scripts/query.py api "<symbol>"
python scripts/query.py evidence "<fact-id-or-symbol>"
```

Use the exact signature/module/version supported by the evidence. A donor implementation may suggest architecture, but its symbol becomes usable only after target-client evidence is found.

Completion criterion: every load-bearing external symbol in the implementation is either evidenced for the target or called out as an unresolved compatibility assumption.

## Reach for recipes only on that branch

For implementation patterns such as lifecycle cleanup, accounts, UI threading, DEX packaging, reload, or release provenance, use MCP `get_recipe`. CLI fallback:

```bash
python scripts/query.py recipe "<pattern>"
```

Do not load the whole wiki unless retrieval is insufficient. The full derived knowledge base lives under `data/wiki/`; `rules.md`, `compatibility.md`, and `gaps.md` are the first manual fallbacks.

## Preserve evidence status

Keep these meanings distinct in conclusions and code review:

- `runtime-verified`: executed on a named build/device.
- `code`: found in a specific source snapshot.
- `docs`: documented, not runtime-proven.
- `inference`: synthesis from evidence.
- `secondary`: community/radar claim.
- `unavailable`: source could not be acquired.

Successful build, source inspection, or README text does not become runtime verification. MCP `record_runtime_result` currently fails closed without trusted machine attestation; never send a user/model-provided PASS as device evidence. The machine-only CLI path is explicitly gated; attestation secrets can be supplied over stdin to avoid argv exposure. See [`KnowledgeStore.md`](KnowledgeStore.md) and [`docs/PRODUCTION_READINESS.md`](docs/PRODUCTION_READINESS.md).

Legacy wiki facts may also carry collector/reviewer provenance from the original 2026-09-27 build. Treat that as `independent-source-reread-nonblind`: the reviewer was a separate fresh run and re-read primary material, but could see/edit the collector output. This strengthens auditability but does not upgrade `docs`/`code` to runtime evidence and is not equivalent to the blind Phase-A verifier used for new write-back.

## Capture new knowledge

When development, source inspection, build results, or runtime testing reveals reusable information that is absent from ExteraContext, or materially confirms/contradicts an existing claim, follow [`NewKnowledge.md`](NewKnowledge.md).

The main agent must not promote its own discovery directly into trusted knowledge. Dispatch a cheap collector subagent to produce a scoped candidate from the original evidence, then dispatch an independent verifier subagent. The verifier must first form its own extraction from the original evidence **without seeing the candidate**, then receive the candidate and compare it against that fixed extraction. Write or attach the knowledge only after the verifier returns `accept`, `accept-with-changes`, `attach-evidence`, `conflict`, or `needs-runtime` according to that protocol.

Completion criterion: any reusable discovery made during the task is either already represented in ExteraContext, passed through the two-subagent protocol, or explicitly left unpersisted because it lacks reusable evidence.

When native subagents are available, use a cheap fresh collector and a separate verifier. The non-negotiable blind boundary is that Phase A is persisted before the candidate is disclosed. Prefer a continuable verifier when the host exposes one, but do not invent provider/child IDs when it does not. MCP-issued collector/verifier capability tokens authorize the staged write path; real runtime child identity is optional provenance. See [`dsh/README.md`](dsh/README.md).


Prefer the MCP staged write path when it is available: `reflect_on_task` → `submit_collector_result` → `submit_verifier_phase_a` → `submit_verifier_phase_b`. It delegates to the same orchestrator and SQLite guards. Otherwise use `scripts/orchestrate.py`; use `scripts/knowledge.py` only as the low-level/debug interface. See [`KnowledgeStore.md`](KnowledgeStore.md) for the storage contract. Never write trusted rows directly to SQLite. The database itself rejects promotion to `verified` without an independent verifier record. Ordinary `query.py` retrieval includes `verified` and explicitly `conflicting` agent knowledge, while candidates/rejected/superseded records remain out of the normal result set.

## Finish

Before presenting or committing an implementation, check that target-client evidence supports its critical API path, lifecycle cleanup is accounted for, account/thread boundaries are explicit where relevant, and any version uncertainty is stated rather than hidden.
