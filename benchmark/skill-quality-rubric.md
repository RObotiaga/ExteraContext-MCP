# ExteraContext Skill — Quality Criteria

Anchors: 50 = usable but easy for an agent to misuse, 70 = clear and reliable, 90+ = hard to use wrong. Reward behavioral precision, evidence safety, and economy of context rather than length.

## Dimensions (total: 100)

### Activation & scope (15 points)
- Clearly states when the skill should trigger and what work it governs. (0-8)
- Keeps unknown target/version fields unknown instead of encouraging guesses. (0-7)

### Retrieval procedure (20 points)
- Gives an explicit execution order from target resolution to task retrieval to symbol verification. (0-10)
- Uses focused, one-concept retrieval and states when a combined query is justified. (0-10)

### Evidence safety (20 points)
- Distinguishes runtime/code/docs/inference/secondary evidence without silent promotion. (0-10)
- Prevents donor-client or unresolved symbols from silently becoming target-client APIs. (0-10)

### New-knowledge integrity (20 points)
- New knowledge requires a separate collector and independent verifier with original evidence. (0-10)
- Prevents self-confirmation, duplicate promotion, scope inflation, and destructive overwrite of provenance. (0-10)

### Operational completeness (15 points)
- Contains concrete commands/decision gates and explicit completion criteria. (0-8)
- Handles no-result, conflict, negative evidence, and insufficient-runtime-evidence cases. (0-7)

### Precision & progressive disclosure (10 points)
- Main skill stays concise and routes specialized write-back detail to NewKnowledge.md. (0-5)
- Avoids redundant guidance and rules that cannot be executed by the current prototype. (0-5)
