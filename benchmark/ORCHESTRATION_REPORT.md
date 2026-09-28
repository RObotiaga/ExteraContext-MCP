# ExteraContext v0.5 orchestration report

## Added

- `scripts/orchestrate.py`: staged `reflect -> collector -> blind Phase A -> Phase B -> guarded commit` state machine.
- Original-evidence references: collector can select only evidence snapshotted by the parent (`source-001`, ...); it cannot rewrite provenance.
- Child identity provenance: collector/verifier provider, model and child/run identifiers are persisted in `knowledge_runs.metadata_json` / `session_id`.
- Phase B identity guard: when a verifier child id is recorded, Phase B must come from that same child.
- DSH profile and first-test prompt under `dsh/`.
- JSON result contracts under `schemas/`.
- `tests/test_orchestrator.py` end-to-end simulation.

## Automated checks

- `tests/smoke.py`: PASS
- `tests/test_skill_contract.py`: PASS
- `tests/test_knowledge_writeback.py`: PASS
- `tests/test_legacy_provenance.py`: PASS
- `tests/test_orchestrator.py`: PASS

## Retrieval regression

Main 15-case benchmark after orchestration-only changes:

- Hit@1: 0.80
- Hit@3: 0.80
- Hit@5: 1.00
- Hit@10: 1.00
- expected recall@10: 0.9111
- MRR@10: 0.8433

12-case holdout2:

- Hit@1: 0.75
- Hit@3: 0.9167
- Hit@10: 1.00
- recall: 0.9583

The orchestration layer does not change retrieval ranking.

## DSH boundary

This package does not embed a model runtime. DeepSeek Harness is responsible for actual child isolation and model routing. The intended real test uses:

- fresh one-shot collector child;
- separate continuable verifier child;
- verifier Phase A result persisted before candidate disclosure;
- Phase B sent to the same verifier child.

This is the first test that can verify the *physical* two-child requirement rather than only distinct provenance run IDs.
