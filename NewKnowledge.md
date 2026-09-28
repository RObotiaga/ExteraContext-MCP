# New knowledge protocol

Use this protocol when work on an ExteraGram/AyuGram plugin produces reusable knowledge that is not already represented by ExteraContext, or produces evidence that materially strengthens, weakens, scopes, or contradicts an existing claim.

The main agent does not write a discovered claim directly as trusted knowledge. New knowledge enters through two independent subagent passes: a cheap collector writes a candidate, then an independent verifier re-checks the original evidence before the candidate can be accepted. The collector and verifier must not share hidden conversation state or a chain of reasoning from the first pass.
For high-impact, ambiguous, conflicting, or version-sensitive claims, prefer a verifier from a different model family or a materially stronger independent model when available; this reduces correlated model errors. For deterministic source/signature checks, a separate cheap verifier run is sufficient.

## 1. Decide whether the discovery is worth keeping

Persist only reusable engineering knowledge: API signatures or behavior, version boundaries, lifecycle/thread/account constraints, compatibility results, working patterns, negative results, source locations, runtime observations, or a correction to existing knowledge.

Keep repository-local implementation details in project scope. Do not promote a local convention, temporary workaround, speculative explanation, or unverified model inference to target/global knowledge.

Before dispatching subagents, search ExteraContext for the same claim or symbol. If the knowledge already exists, treat the discovery as possible new evidence for that claim rather than creating a duplicate claim.

Completion criterion: the main agent can state the candidate knowledge in one falsifiable sentence and identify the original evidence that motivated it.

## 2. Collector subagent — cheap model

Dispatch one isolated subagent using the cheapest available model that is capable of reading the supplied evidence and producing structured output. Give it the original evidence, not the main agent's conclusion alone.

The collector must:

- state one atomic claim per candidate;
- classify it as `api`, `behavior`, `compatibility`, `recipe`, `negative-evidence`, `runtime-result`, or `other`;
- identify the target scope: client, platform, client version, SDK version, language/plugin format when known;
- classify scope as `project`, `target`, or `global`;
- attach provenance: repository/source, commit or immutable snapshot when available, file/path/lines or log/test artifact, and source type;
- state evidence status using the existing vocabulary (`code`, `docs`, `secondary`, `inference`, `unavailable`, `runtime-verified`);
- search for an existing matching or conflicting claim and record its ID when found;
- never upgrade static evidence to `runtime-verified`;
- return `skip` if the finding is not reusable or cannot be grounded in evidence.

The collector writes only a **candidate** record. Its own conclusion is not independent evidence.

Suggested output shape:

```json
{
  "claim": "...",
  "kind": "behavior",
  "scope": "target",
  "target": {
    "client": "ExteraGram",
    "platform": "Android",
    "client_version": "12.10.1",
    "sdk_version": "1.4.5.5"
  },
  "evidence_status": "code",
  "evidence": [
    {
      "source": "owner/repo",
      "commit": "...",
      "path": "...",
      "lines": "..."
    }
  ],
  "existing_claim": null,
  "conflicts_with": [],
  "status": "candidate"
}
```

Completion criterion: every candidate field can be traced back to supplied original evidence; unsupported fields remain unknown rather than guessed.

## 3. Verifier subagent — blind independent re-check

Dispatch a second isolated subagent. The verifier must not share hidden conversation state with the collector and must not receive the collector's reasoning, confidence, or persuasive prose. Verification has two phases.

### Phase A — blind extraction

Give the verifier only:

1. the original source/evidence references;
2. relevant existing ExteraContext claims returned by duplicate/conflict search;
3. the target context known independently from the task, if any.

Do **not** show the collector's candidate yet. The verifier must inspect the original evidence and return an **independent extraction**: one falsifiable sentence describing what the evidence supports, the narrowest justified target/version scope, evidence status, and any conflict or uncertainty it sees.

### Phase B — candidate comparison

After Phase A is fixed, perform Phase B against the collector candidate and the immutable Phase A extraction. Prefer the same verifier session when the host can continue it, but a new Phase-B verifier is acceptable when the host exposes no continuation handle because the blind extraction has already been persisted. The candidate cannot retroactively change Phase A; any changed interpretation must be explicitly justified.

Verify separately:

- whether the claim is actually supported;
- whether the symbol/signature is exact;
- whether the client/platform/version scope is justified;
- whether source type and evidence status are correct;
- whether the claim duplicates an existing fact;
- whether it conflicts with existing knowledge;
- whether a narrower scope is required;
- whether a negative result proves only `not observed/not found in this target` rather than universal non-existence;
- whether donor-client evidence is clearly marked as donor evidence and not target API evidence.

The verifier returns one decision:

- `accept` — evidence supports the candidate as written;
- `accept-with-changes` — evidence supports a corrected/narrower candidate;
- `attach-evidence` — an existing claim already represents the knowledge; add only the new evidence;
- `conflict` — preserve both claims/evidence and open an unresolved conflict;
- `reject` — evidence does not support a reusable claim;
- `needs-runtime` — static evidence is insufficient for the claimed runtime behavior.

For deterministic source/signature checks, the verifier may also use a cheap model. Escalate to a stronger model only when evidence is ambiguous, multiple versions disagree, or the verifier and collector conflict. A disagreement is not resolved by majority or by choosing the more confident wording: preserve the disagreement as `conflict` or request stronger/runtime evidence.

Completion criterion: the verifier's decision cites the original evidence it inspected, includes the Phase A independent extraction, and does not rely on the collector's authority.

## 4. Commit the knowledge

Only after verifier approval may the main agent invoke the knowledge write path.

Apply the verifier decision as follows:

- `accept` / `accept-with-changes`: create the claim as verified-by-review, preserving the underlying evidence status; do not label it `runtime-verified` unless runtime evidence exists.
- `attach-evidence`: add the evidence to the existing claim; do not create a duplicate.
- `conflict`: store the new evidence and explicit conflict relation; do not silently overwrite either side.
- `needs-runtime`: store the candidate as awaiting runtime verification, if useful, and keep its confidence below runtime-verified knowledge.
- `reject`: do not add the claim to the searchable trusted corpus. Optionally retain an audit record of the rejected proposal.

Every write must retain collector run ID, verifier run ID, timestamp, target scope, evidence provenance, and the resulting claim/evidence IDs. Evidence is append-only; corrections create revisions, conflict links, or superseding claims instead of erasing provenance.

The agent that proposed a claim must never treat that same proposal as independent corroboration in the same run.

Completion criterion: every searchable new claim has independent verification, traceable provenance, explicit scope, and no silent evidence-status upgrade.

## 5. When to invoke this protocol

Run this protocol near the end of a task when at least one of these occurred:

- a previously unknown API/signature/behavior was discovered;
- a source confirmed or contradicted an existing ExteraContext claim;
- a plugin build or runtime test produced reusable compatibility evidence;
- a donor implementation revealed a useful pattern or a target incompatibility;
- a repeated implementation pattern is strong enough to propose as a recipe;
- an attempted approach failed in a way that would save future agents from repeating the same dead end.

Do not run it merely because files changed. The trigger is new reusable evidence.

## 6. Executable write path

Prefer `scripts/orchestrate.py` for normal agent use. It turns the protocol into a staged state machine and protects the blind boundary:

```text
reflect -> collector-result -> phase-a-result -> phase-b-result -> guarded commit
```

`reflect` snapshots original evidence, assigns immutable orchestration references (`source-001`, ...), and issues a one-time collector capability token. The collector returns only `evidence_refs`; it cannot author repository/path/commit/line provenance. A successful collector submission consumes that token and issues a separate verifier capability token. Phase A and Phase B require that same verifier capability, while Phase A must be stored before the candidate is revealed. Only token hashes are retained.

DeepSeek Harness child/provider IDs are optional provenance. Never fabricate them. If concrete runtime child identity is supplied for verifier Phase A, Phase B must supply matching identity; otherwise capability-token continuity is the executable guarantee and physical isolation remains a Harness responsibility. See `dsh/README.md`.

Use `scripts/knowledge.py` as the lower-level/debug interface; do not bypass the orchestration protocol during ordinary agent work.

### Low-level storage sequence


Use `scripts/knowledge.py`; do not edit `data/knowledge.sqlite` directly. The mutable knowledge database is separate from the generated wiki index so rebuilding `data/exteracontext.sqlite` cannot erase learned knowledge.

Minimal accepted-claim sequence:

```bash
# 1. Isolated collector run
python scripts/knowledge.py run-create --role collector --model "<cheap-model>" --task-id "<task>"

# 2. Collector creates candidate + original evidence
python scripts/knowledge.py propose \
  --run-id <collector-run> \
  --claim "<one falsifiable sentence>" \
  --kind behavior --scope target --evidence-status code \
  --client ExteraGram --platform Android \
  --repository owner/repo --commit <sha> --path path/to/file --lines 10-20

# 3. Separate verifier run. Phase A happens BEFORE the candidate is revealed.
python scripts/knowledge.py run-create --role verifier --model "<verifier-model>" --task-id "<task>"
python scripts/knowledge.py verify-phase-a \
  --run-id <verifier-run> --claim-id <claim-id> \
  --statement "<independent extraction from original evidence>" \
  --evidence-status code --scope-json '{"client":"ExteraGram"}'

# 4. After Phase A is fixed, compare it with the collector candidate.
python scripts/knowledge.py verify \
  --run-id <verifier-run> --claim-id <claim-id> --verdict accept

# 5. Promotion is a separate guarded transition.
python scripts/knowledge.py commit --claim-id <claim-id> --verifier-run-id <verifier-run>
```

For a duplicate, use verdict `attach-evidence` with `--existing-subject-type claim|legacy_fact --existing-subject-id ...`; the candidate becomes `superseded` and its evidence is appended to the existing subject. For contradictory evidence, use verdict `conflict`; both claims remain auditable and an open conflict is created. `needs-runtime` and `reject` are also explicit terminal review outcomes.

Runtime results are the only direct machine-evidence path. Create a `runtime` provenance run and use `record-runtime`; model/human observations cannot use that bypass. A runtime pass may strengthen the evidence level of an already verified claim, but it does not automatically promote a `candidate` or `needs-runtime` claim to `verified`.

The database enforces the critical invariants with SQLite triggers: every claim starts as `candidate`; collector and verifier run IDs must differ; Phase A must exist before a verdict; and transitions to `verified`, `conflicting`, `needs-runtime`, `rejected`, or `superseded` require the matching verifier verdict.

