# ExteraContext MCP v0.7.0 — acceptance regression test

Run this in a fresh DeepSeek Harness session with ExteraContext MCP configured in `--modern-only` mode. Do not use the ExteraContext CLI, direct SQLite access, or direct reads of `data/wiki` to answer the test.

## 1. Connectivity and UTF-8

1. Call `doctor` and confirm `protocol_era=modern`, `protocol_revision=2026-07-28`, and server version `0.7.0` (the MCP package/server version). Record the actual version returned by the deployed instance; a mismatch is a FAIL for this v0.7.0 acceptance run.
2. Call `get_recipe` with a query containing exactly: `cleanup → reload русский`.
3. Treat any `UnicodeEncodeError`, mojibake such as `����`, or loss/change of `→` as FAIL.

## 2. Retrieval regression

Use focused MCP retrieval to investigate:

> For ExteraGram Android, intercept an outgoing text message before send, preserve the callback account, move expensive work off the UI path, update UI safely, and ensure disable/reload does not leave duplicated callbacks.

Keep unknown versions unknown. Do not promote donor evidence to ExteraGram support. Report evidence IDs and runtime-verification status.

## 3. Donor and unknown handling

- Query `AyuFilter`. It must remain donor-only unless direct ExteraGram evidence is returned.
- Query `ExteraGram.plugins.teleportMessage(message)`. An empty/unsupported result must remain unsupported; do not invent an implementation.

## 4. Capability-token write-back

Create/read the project fixture containing:

`For the ExteraContext MCP acceptance test project only, EXTERACONTEXT_WRITEBACK_SENTINEL has the value 7319.`

Confirm ordinary `search_knowledge` does not already return this sentinel.

Then:

1. Call `reflect_on_task` with the original fixture evidence. Save the returned `orchestration_id` and `collector_token`.
2. Launch a fresh collector child with the returned collector prompt. Do not invent or guess provider/child IDs.
3. Call `submit_collector_result` with `orchestration_id`, `collector_token`, and collector JSON. `runtime_actor` is optional and must be supplied only if Harness actually exposes truthful runtime identity. Save returned `verifier_token`.
4. Launch an independent verifier with the returned blind Phase-A prompt. The prompt must not contain the collector candidate.
5. Submit Phase A with `submit_verifier_phase_a` using `verifier_token`.
6. Only after Phase A succeeds, process the returned Phase-B prompt. Prefer the same verifier child if Harness exposes a continuation handle; otherwise a new Phase-B child is permitted because Phase A is already immutable.
7. Submit Phase B with `submit_verifier_phase_b` using the same `verifier_token`.
8. Confirm ordinary retrieval now returns the sentinel as project-scoped trusted knowledge with evidence and collector/verifier provenance.

The test must not fail merely because DSH does not expose `subagent_id` or `provider`.

## 5. Negative guards

Start a second orchestration and demonstrate at least these failures through MCP without touching SQLite:

- wrong collector token;
- collector token used as verifier token;
- Phase B attempted before Phase A;
- verifier token from another orchestration;
- replay of a consumed/finished transition.

If Harness exposes concrete verifier runtime child identity in Phase A, also prove that a mismatching Phase-B identity is rejected.

## 6. Persistence checkpoint

Before restart, confirm `EXTERACONTEXT_WRITEBACK_SENTINEL` is retrievable through normal MCP search and record its claim ID/state/evidence.

Then stop and output exactly:

`READY_FOR_RESTART`

After a full Harness + MCP restart, run a fresh session and retrieve the sentinel using only MCP. PASS only if claim, value `7319`, project scope, evidence, verification provenance, and trusted state persist.

Final marker:

`EXTERACONTEXT_MCP_V070_TEST_COMPLETE`
