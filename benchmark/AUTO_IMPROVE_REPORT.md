# Auto-improve pass — ExteraContext skill

This pass applies the core `crimeacs/auto-improve` discipline to `SKILL.md` and `NewKnowledge.md`: explicit rubric, small mutations, a champion/candidate gate, and automatic regression checks. The upstream `improve.py` itself was not executed because this environment has no Gemini API key, so no claim is made that an independent model judge ran here.

## Judge signals used

- `benchmark/skill-quality-rubric.md` — behavioral specification for the skill.
- `tests/test_skill_contract.py` — deterministic checks for critical rules.
- `tests/smoke.py` — existing executable smoke test.
- `benchmark/evaluate_retrieval.py` — existing retrieval quality benchmark; retrieval behavior must not regress as a side effect of skill edits.

## Candidate mutations

### KEEP — explicit operating loop and focused retrieval

Added a single execution order to `SKILL.md` and the Context7-derived rule that independent concepts should be retrieved separately. This makes the skill harder to misuse on broad tasks and does not change the retrieval engine itself.

### KEEP — high-ranked result is not authority

Added a stop condition for missing target evidence. The agent must preserve an explicit gap rather than broadening until donor evidence appears authoritative.

### KEEP — blind verifier phase

Strengthened `NewKnowledge.md` from “second agent re-reads evidence” to a two-phase verifier:

1. Phase A: original evidence + existing claims only; the collector candidate is hidden. The verifier makes its own falsifiable extraction and narrowest supported scope.
2. Phase B: only after Phase A is fixed, the collector candidate is revealed for comparison.

This follows auto-improve's separation principle more closely and reduces anchoring/self-confirmation.

### DISCARD — mandatory third judge on every new claim

A third model would increase cost/latency and contradict the intended cheap two-subagent path. Escalation remains conditional for ambiguity, version conflict, or collector/verifier disagreement.

### DISCARD — hard global cap of three retrieval calls

Context7 uses a small call cap for a generic public-doc service, but ExteraContext tasks routinely contain several independent engineering concepts. The skill instead requires one concept per retrieval and small focused packets without imposing an arbitrary global cap.

## Regression results

After the accepted edits:

- `tests/smoke.py`: PASS
- `tests/test_skill_contract.py`: PASS
- main retrieval benchmark: Hit@1 0.80, Hit@3 0.80, Hit@5 1.00, Hit@10 1.00, expected recall@10 0.9111, MRR@10 0.8433
- average task packet: 14,403.8 characters

The retrieval numbers match the previous v0.5 engine because this pass intentionally changed agent policy, not `scripts/query.py`.

## Remaining limitation

This is a benchmark-gated adaptation of the auto-improve method, not a faithful run of its Gemini mutator/evaluator pair. A future external A/B run should use a genuinely separate judge model to pairwise-evaluate agent behavior with the old and new skill versions.
