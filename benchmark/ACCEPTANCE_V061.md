# ExteraContext MCP v0.6.1 acceptance result

Status: **PASS**

Acceptance retest completed in DeepSeek Harness after the v0.6.1 capability-token and UTF-8 fixes.

## End-to-end result

The following chain completed successfully:

```text
modern MCP
→ Unicode-safe retrieval
→ focused target retrieval
→ donor / unsupported handling
→ collector stage
→ blind verifier Phase A
→ same-verifier Phase B
→ guarded commit
→ negative token / transition checks
→ full restart
→ ordinary MCP search
→ persisted verified claim
```

After restart, ordinary MCP search returned:

- claim: `claim_c6c95657d89b4a6ab59a`;
- state: `verified`;
- scope: project;
- sentinel value: `7319`;
- fixture evidence persisted;
- `accept` verification provenance persisted.

The acceptance fixture was `acceptance-test-fixture.md`.

## Evidence boundary

The persisted claim's evidence status is `code`.

This acceptance result demonstrates persistence and the ExteraContext write-back/verification machinery. It does **not** establish runtime behavior on an ExteraGram build.

Client and SDK versions for the sentinel remain unknown and were not invented.

## v0.6.1 acceptance coverage

PASS:

- modern MCP protocol/version path;
- Unicode handling;
- focused retrieval;
- donor-only handling;
- unsupported/unknown handling;
- staged write-back;
- capability-token role/stage guards;
- negative invalid-transition tests;
- persistence across restart;
- ordinary retrieval of verified mutable knowledge after restart.

Marker emitted by the retest:

`EXTERACONTEXT_MCP_V061_TEST_COMPLETE`

## Consequence

v0.6.1 is the first ExteraContext MCP baseline eligible for the end-to-end A/B/C agent benchmark. Retrieval/runtime quality should now be measured separately from MCP infrastructure correctness.
