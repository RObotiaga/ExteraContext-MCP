# Persistent knowledge write-back report

## Implemented

- Separate mutable SQLite store: `data/knowledge.sqlite`.
- Immutable/generated wiki index remains `data/exteracontext.sqlite`.
- Provenance runs for `collector`, `verifier`, `main`, and `runtime` roles.
- Atomic claims with `project` / `target` / `global` scope and explicit target/version fields.
- Append-oriented evidence records and links to both new claims and legacy wiki facts.
- Two-phase verifier record: blind Phase A extraction, then Phase B candidate comparison.
- Lifecycle states: `candidate`, `verified`, `conflicting`, `needs-runtime`, `rejected`, `superseded`.
- Duplicate handling through `attach-evidence` instead of duplicate trusted claims.
- Conflict preservation instead of overwrite.
- Automatic revision/audit rows on claim creation and state/text transitions.
- Runtime-only direct machine evidence path.
- Normal `query.py` retrieval merges accepted agent knowledge with the generated wiki.
- `query.py evidence knowledge:<claim-id>` exposes the full evidence/verifier trail.

## Database-enforced invariants

SQLite triggers reject:

- creating a claim directly in a trusted state;
- collector self-verification;
- a Phase-B verdict without a prior Phase-A extraction;
- promotion to `verified` without an independent `accept`/`accept-with-changes` verdict;
- transitions to conflict/reject/needs-runtime/superseded without matching verifier verdicts;
- direct evidence links to trusted claims unless they come from a runtime harness or a verified `attach-evidence` flow;
- an effective evidence level stronger than the strongest linked evidence.

The database can enforce run separation and ordering, but cannot prove that two run IDs were executed by physically different model processes. Isolation remains an orchestration responsibility of `SKILL.md` / `NewKnowledge.md` until a subagent runner is added.

## Tests

`tests/test_knowledge_writeback.py` covers:

- candidate exclusion from trusted retrieval;
- self-verification rejection;
- direct SQL trusted-state bypass rejection;
- Phase-B-before-Phase-A rejection;
- accepted and modified claims;
- runtime-role bypass protection;
- runtime evidence strengthening;
- duplicate → `attach-evidence`;
- conflict preservation;
- `query.py` integration.

Existing tests also pass:

- `tests/test_skill_contract.py`
- `tests/smoke.py`

## Retrieval regression

The original 15-case retrieval benchmark remains unchanged after adding the mutable store:

- Hit@1: 80.0%
- Hit@3: 80.0%
- Hit@5: 100%
- Hit@10: 100%
- Expected recall@10: 91.1%
- MRR@10: 0.843
- Average task packet: ~14.4k characters

Additional holdouts remained at 100% Hit@10 with 95.8% expected recall. Donor holdout remains 6/6 Top-1 with 100% donor warnings.

## Not implemented yet

- automatic spawning/isolation of collector and verifier subagents;
- `reflect_on_task()` automatic discovery extraction;
- strict target resolver/version compatibility solver;
- MCP write/read tools;
- automatic runtime/device harness;
- semantic duplicate resolution beyond exact normalization + FTS candidate search.
