# ExteraContext Knowledge Store

`data/knowledge.sqlite` is the mutable, append-oriented engineering memory. It is intentionally separate from `data/exteracontext.sqlite`, which is a generated read-only index of the bundled wiki.

## Trust boundary

A model observation is not trusted merely because an agent wrote it. The write path has three distinct objects:

1. **Claim** — one falsifiable statement with explicit scope and target.
2. **Evidence** — immutable provenance attached to a claim or legacy wiki fact.
3. **Verification** — a second agent's blind Phase-A extraction followed by its Phase-B comparison verdict.

All new claims start as `candidate`. SQLite triggers block direct promotion to trusted lifecycle states unless the required independent verification exists.

## Legacy wiki provenance

The bundled read-only wiki predates the executable write-back protocol. `data/legacy-source-runs.json` records the original collector/reviewer runs reconstructed from the supplied prompt archive. `scripts/build_index.py` imports this into `source_runs` and `source_provenance`.

Legacy review is classified as `independent-source-reread-nonblind`: reviewers were separate runs with their own call IDs and re-read the primary snapshots, but they could inspect and edit collector outputs. Therefore:

- legacy review provenance may support auditability and source-review confidence;
- it never changes a fact's evidence status (`docs`, `code`, etc.);
- it never implies `runtime-verified`;
- it is not treated as equivalent to the blind two-phase verifier required for newly written trusted claims.

Use `python scripts/query.py evidence <fact-id>` to see this provenance for a legacy fact.

## Tables

- `knowledge_runs` — collector/verifier/main/runtime provenance.
- `knowledge_claims` — atomic claims and lifecycle state.
- `knowledge_evidence` — append-only source/runtime evidence.
- `knowledge_links` — evidence links to new claims or legacy `facts` IDs.
- `knowledge_verifications` — blind extraction plus comparison verdict.
- `knowledge_conflicts` — unresolved contradictory claims/evidence.
- `knowledge_revisions` — automatic audit trail for claim text/state transitions.
- `knowledge_claims_fts` — only trusted/conflicting claims exposed to normal retrieval.

## Lifecycle

```text
candidate
  |-- accept / accept-with-changes -> verified
  |-- attach-evidence              -> superseded (evidence moves to existing subject)
  |-- conflict                     -> conflicting
  |-- needs-runtime                -> needs-runtime
  `-- reject                       -> rejected
```

A later correction is a new claim/revision/conflict, not deletion of old provenance.

## Two-agent protocol

Create separate provenance runs:

```bash
python scripts/knowledge.py run-create --role collector --model <cheap-model> --task-id <id>
python scripts/knowledge.py run-create --role verifier  --model <model>       --task-id <id>
```

The collector creates the candidate and original evidence with `propose`. The verifier then performs `verify-phase-a` using only original evidence and relevant existing claims. Only after that extraction is fixed should the candidate be revealed and `verify` called.

Accepted example:

```bash
python scripts/knowledge.py propose \
  --run-id <collector> \
  --claim "on_send_message_hook receives the account for the triggering send operation." \
  --api on_send_message_hook \
  --kind behavior --scope target --evidence-status code \
  --client ExteraGram --platform Android --client-version 12.10.1 \
  --repository exteraSquad/plugins-pysdk-builds --commit <sha> \
  --path <file> --lines <range>

python scripts/knowledge.py verify-phase-a \
  --run-id <verifier> --claim-id <claim> \
  --statement "The callback signature exposes an account parameter." \
  --scope-json '{"client":"ExteraGram","client_version":"12.10.1"}' \
  --evidence-status code

python scripts/knowledge.py verify \
  --run-id <verifier> --claim-id <claim> \
  --verdict accept-with-changes \
  --final-statement "on_send_message_hook exposes the account parameter on the inspected target source."

python scripts/knowledge.py commit --claim-id <claim> --verifier-run-id <verifier>
```

## Duplicate evidence

Do not create two trusted claims that say the same thing. Create the candidate normally, let the verifier independently establish the observation, then use:

```bash
python scripts/knowledge.py verify \
  --run-id <verifier> --claim-id <candidate> \
  --verdict attach-evidence \
  --existing-subject-type claim \
  --existing-subject-id <existing-claim>
python scripts/knowledge.py commit --claim-id <candidate> --verifier-run-id <verifier>
```

`legacy_fact` can be used instead of `claim` when new evidence supports an immutable fact from the bundled wiki.

## Conflicts

Contradictions are data. They are never resolved by overwriting the older record:

```bash
python scripts/knowledge.py verify \
  --run-id <verifier> --claim-id <candidate> \
  --verdict conflict \
  --existing-subject-type claim \
  --existing-subject-id <other-claim> \
  --notes "Possible version boundary"
python scripts/knowledge.py commit --claim-id <candidate> --verifier-run-id <verifier>
```

The conflicting candidate becomes searchable with an explicit `conflicting` lifecycle label so a future agent can investigate the boundary.


## MCP capability actors

The MCP orchestration layer cannot assume that a host such as DeepSeek Harness exposes internal child-run IDs. v0.6.1 therefore separates **authorization identity** from optional **runtime provenance**:

- `reflect_on_task` creates a collector capability actor and returns a one-time collector token;
- collector submission consumes that token and creates a different verifier capability actor/token;
- verifier Phase A and Phase B require the same verifier token;
- orchestration state stores SHA-256 token hashes only;
- wrong-role, wrong-orchestration, replay, or wrong-stage token use fails closed;
- the database still records distinct collector and verifier `knowledge_runs`, so SQLite independence guards remain effective.

If a host exposes `child_id`, `session_id`, `invocation_id`, or similar identity, it may be attached as caller-attested `runtime_actor` metadata. This metadata does not authorize writes. If verifier Phase A contains concrete runtime identity, Phase B must match it.

Capability continuity does not prove that two physical LLM processes were isolated. That property belongs to the host and should be reported separately in acceptance tests.

## Runtime evidence

Runtime evidence is machine evidence, not an agent opinion. Create a dedicated runtime run:

```bash
python scripts/knowledge.py run-create --role runtime --model device-harness --task-id <id>
python scripts/knowledge.py record-runtime \
  --run-id <runtime-run> --subject-type claim --subject-id <claim> \
  --result pass --test-id outgoing-hook-basic --runs 3 \
  --client ExteraGram --client-version 12.10.1 --sdk-version 1.4.5.5
```

Only `runtime` runs may use this direct path. A runtime pass can strengthen a verified claim to `runtime-verified`; it never directly promotes an unreviewed candidate.

## Retrieval

The ordinary read interface remains `scripts/query.py`. It merges the generated wiki with accepted agent knowledge. Use `query.py evidence knowledge:<claim-id>` to inspect the full evidence and verification audit trail.

## Safety invariants

- Unknown target/version fields remain unknown.
- Collector and verifier are different run IDs.
- Phase A precedes candidate comparison.
- Static source/docs evidence never becomes runtime evidence.
- Candidate/rejected/superseded claims are excluded from normal retrieval.
- Evidence is not deleted when a claim changes state.
- Conflicts remain visible and auditable.
