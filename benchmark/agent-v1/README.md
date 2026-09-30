# Agent benchmark v1

This directory defines the frozen **protocol 1.1** A/B/C benchmark for ExteraContext MCP. It is a procedure, not a claim that 60 device runs have occurred. The prior single Mode A v1-001 result is pilot-only and excluded from aggregates; no C/B comparison follows from it.

## What it compares

- **A** — baseline agent, no ExteraContext knowledge.
- **B** — same agent with the frozen raw Knowledge repository.
- **C** — same agent with ExteraContext MCP, without direct Knowledge access.

The benchmark is frozen to Knowledge commit:

`70f6f614227c8b02d241e7b1e72a0b6691442fd1`

## Files

- `tasks.json` — public task prompts.
- `prompts/` — mode-specific instructions.
- `result.schema.json` — tested-agent output contract.
- `ground-truth.json` — evaluator-only material; never copy it into the tested agent workspace.
- `assessment.schema.json` — evaluator record format, including mandatory per-run environment evidence.
- `run-manifest.json` — 60-run minimum schedule.
- `aggregate.py` — aggregate evaluator records.

## Recommended procedure

For each manifest row:

1. Start a completely fresh DSH/model session.
2. Reset the target fixture/project to the same commit.
3. Expose only the resources permitted by that mode.
4. Give the corresponding mode prompt plus the task text.
5. Save the final worktree/patch, `BENCHMARK_RESULT.json`, runner telemetry and logs under a unique run directory.
6. Outside the tested agent context, score the run with `ground-truth.json` and write `assessment.json` in the prepared packet beside `RUN.json`; include complete `environment_evidence` as specified in `EVALUATOR.md`. `RUN.json.environment_expected` is a closed five-key object (`frozen_commit`, `frozen_tree`, `db_sha256`, `attestation_sha256`, `mcp_package_version`): B pins its verified checkout tree in `frozen_tree`, and the packet-local checkout expectation sidecar and evaluator evidence must match that exact commit/tree; preparation checks `git status --porcelain --untracked-files=all --ignored=matching`, so ignored local files contaminate the checkout and are rejected; A/C set `frozen_tree` to null. For C include a relative in-packet doctor JSON artifact path and report the actual MCP version/protocol and packet commit/DB/attestation pins. For A/B record MCP as not applicable with a rationale.
7. Do not let results from one run enter the context of another.

After all runs:

```bash
python benchmark/agent-v1/aggregate.py path/to/benchmark-results --attestation-key-file path/to/operator-hmac.key
```

The primary metric is Clean Task Success Rate. Production `summarize` and `aggregate.py` require the exact complete frozen schedule: every `run_id` in `run-manifest.json` (currently 60) must appear exactly once among non-pilot assessments. Missing and duplicate IDs are rejected with explicit ID diagnostics; explicit pilot assessments are excluded first and therefore cannot fill a scheduled slot. The CLI exposes no partial-schedule bypass. `aggregate.py` also rejects stale or inconsistent assessments and reports null (not zero) for unavailable denominators. Before semantic scoring, every record is validated against the complete adjacent `assessment.schema.json` contract (including required nested fields, exact types/constants/enums/patterns, closed objects, and Mode B conditional requirements); the scorer uses a stdlib-only validator. Every assessment must be inside its prepared packet with canonical `RUN.json` identity. A/B records MCP as not applicable with a rationale; C must match the packet's frozen commit, DB hash, attestation-file digest and expected MCP package version, include an in-packet doctor JSON artifact with matching server version and protocol, and pass aggregate-time HMAC verification of the exact canonical operator-attested payload. The scorer also requires `structuredContent.data.index` to identify the resolved packet-local `runtime/exteracontext.sqlite` path, the pinned DB SHA-256, and a byte size matching the signed attestation payload. This strengthens packet/database binding but does not independently prove that a remote or device used that live server; the doctor artifact remains operator-supplied evidence. Any aggregate containing Mode C requires `--attestation-key-file` pointing to the trusted operator key outside all packets and outside the tested-agent workspace/session; only the path is supplied, never the key bytes. Never copy the key into a packet or expose it to the tested agent. Mode A/B-only aggregation needs no key. This cryptographically verifies that the payload was sealed by the holder of the operator key; it does not prove the human/operator assertion that the DB corresponds to the frozen commit, nor what server a live device contacted. This remains an evaluator/operator trust boundary. Artifact paths are constrained to the packet. Missing runs/results must be evaluated as failures outside the agent session, never silently omitted. A pilot-only directory has no eligible sample and aggregation fails rather than reporting an A/B/C result.

Run offline checks: `python tests/test_benchmark_protocol.py` and `python benchmark/agent-v1/validate.py`. For agent claims use `python benchmark/agent-v1/validate_result.py RUN/target/BENCHMARK_RESULT.json`; a command alone is not independent proof of a PASS.

## Self-contained run packets

`prepare_run.py` creates an isolated `target/` project. Open that exact directory in a fresh DSH session; no external target fixture is required.

Example:

```bash
python benchmark/agent-v1/prepare_run.py v1-001 ./run-v1-001
```

Then open `./run-v1-001/target` in DSH and follow `BENCHMARK_MODE.md` + `BENCHMARK_TASK.md`.

Before running B, use `python benchmark/agent-v1/prepare_run.py v1-002 ./fresh-b --frozen-checkout PATH_TO_LOCAL_KNOWLEDGE` (the checkout must have the exact pinned HEAD). Mode C requires a verified, signed provenance attestation; a `.knowledge-ref` file or the candidate inventory alone is not proof. No download, synchronization, data collection, or device test is part of this setup. The C packet contains `MCP_BENCHMARK_ENV.json` with the pinned reference, per-run isolated base/mutable SQLite paths and orchestration root, and `EXTERACONTEXT_AUTO_SYNC=0`. Without locally verified resources, `RESOURCE_SETUP.md` says **NOT READY**; do not launch the packet. B exposes only the raw checkout; C exposes only MCP; A exposes neither. Do not regenerate over nonempty directories, including the existing pilot. Device sessions, if later authorized, are manual and fresh.

### Mode C signed-corpus candidate, seal, and verification

These steps use only existing local files and Git objects. They do not fetch, sync, clone, rebuild, or collect knowledge.

1. **Inventory a candidate**. Point `--repo` at the existing local Knowledge Git object repository and `--db` at the proposed SQLite database:

   ```bash
   python benchmark/agent-v1/build_frozen_corpus.py candidate \
     --repo PATH_TO_LOCAL_KNOWLEDGE_GIT_OBJECTS \
     --db PATH_TO_SQLITE \
     --output candidate.json
   ```

   The candidate checks that the exact pinned commit/tree exists in local Git objects and inventories the DB hash, size, schema fingerprint and required row counts. **It is not an attestation and does not establish that this DB was built from that commit.** A trusted, authorized benchmark operator must independently establish the source-commit-to-database relationship before signing. A hash only identifies the candidate bytes.

2. **Provision the HMAC key out-of-band**. Use a trusted secret manager or an approved secure handoff to create a key of at least **32 random bytes**. The key file must be an existing regular file, not a symlink, and be stored outside the repository, candidate/attestation files, run packets and tested-agent workspace. Restrict file access to the authorized operator and verification process (for example, owner-only permissions/ACL); never commit, copy into a run packet, expose to an agent, place in logs, or transmit it with the attestation. Keep the key out-of-band from the candidate and attestation. `build_frozen_corpus.py` reads the raw file bytes and checks length; it cannot establish that those bytes were generated randomly, so the operator must ensure the entropy requirement.

3. **Seal only after operator review**. The explicit confirmation must exactly match the pinned commit and the candidate DB SHA-256:

   ```bash
   python benchmark/agent-v1/build_frozen_corpus.py seal \
     --candidate candidate.json \
     --key-file PATH_TO_OUT_OF_BAND_HMAC_KEY_FILE \
     --operator AUTHORIZED_OPERATOR_ID \
     --confirm 'ATTEST 70f6f614227c8b02d241e7b1e72a0b6691442fd1 <db_sha256>' \
     --output attestation.json
   ```

   This produces an HMAC-SHA256 signature over the candidate payload. The operator's authorization and independent source/database relationship review are essential; generating a signature does not itself make the candidate trustworthy.

4. **Verify while preparing the Mode C packet**. Verification is performed by `prepare_run.py` using the attestation, same protected key file, local Git object repository, and exact SQLite DB:

   ```bash
   python benchmark/agent-v1/prepare_run.py v1-003 ./fresh-c \
     --frozen-db PATH_TO_SQLITE \
     --frozen-attestation attestation.json \
     --attestation-key PATH_TO_OUT_OF_BAND_HMAC_KEY_FILE \
     --frozen-repo PATH_TO_LOCAL_KNOWLEDGE_GIT_OBJECTS
   ```

   The verifier checks the HMAC signature, that the pinned commit/tree exists in the local object repository, and that the exact current DB hash/size/schema/counts match the signed payload. This validates the operator's signed assertion; it does **not independently derive or prove** the historical relationship between that commit and the database. The local repository is used only for verification and is not exposed to the Mode C agent. If any required local resource or verification is unavailable, the packet must remain **NOT READY** and must not be run.

**Current corpus status: NOT ELIGIBLE for Mode C.** The local database copy has `commit_verified: false`; there is no authorized signed attestation proving that the database was built from the frozen Knowledge commit. Do not use it for Mode C or imply that its matching hash/counts prove commit provenance. An operator must establish the relationship and complete the candidate/seal/verification process for the exact database before a Mode C run is eligible. No device test or network operation is authorized or implied by these instructions.

## Interpretation

A/B/C results require the complete preregistered schedule, valid target-bound outputs, independent assessments, reproducible artifacts, and uncertainty reporting. For a complete schedule, `aggregate.py` adds `clean_success_uncertainty`: per-mode Clean Task Success Rate intervals and paired A−B, A−C, and B−C difference intervals from a deterministic 10,000-resample task-cluster percentile bootstrap. Each resample draws the same 10 task IDs for all modes and keeps the two repeats within each selected task, preserving the paired design. The intervals are descriptive, conditional on this frozen task set and rubric, and may be unstable with only 10 clusters; they are not significance tests or proof of superiority. Pilot results and retrieval-only metrics are not comparative agent-performance evidence. Do not claim superiority or production quality from protocol validity, a successful command, or incomplete runs.

Device-level plugin validation is outside this repository-only protocol and remains a separately authorized manual activity.
