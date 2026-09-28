# ExteraContext v0.6 MCP report

## Scope

v0.6 adds a real Model Context Protocol server as a thin layer over the existing Python/SQLite ExteraContext core. The knowledge schema, retrieval engine, provenance, write-back state machine, and SQLite guards were not reimplemented in JavaScript.

## Protocol implementation

- Official TypeScript MCP SDK v2 packages are pinned at `2.1.0`.
- Server target revision: `2026-07-28`.
- stdio modern serving uses `serveStdio(factory)`.
- Streamable HTTP modern serving uses `createMcpHandler(factory)` and the official Node adapter.
- Default serving accepts modern plus legacy fallback for compatibility.
- `--modern-only` rejects 2025-era openings for conformance testing.
- HTTP v0.6 is loopback-only and intentionally has no built-in authentication.
- Every tool advertises an input schema, output schema, and MCP tool annotations.
- Successful tool results return both text content and schema-backed `structuredContent`.

## Tool surface

Read/decision tools:

1. `resolve_target`
2. `search_knowledge`
3. `find_api`
4. `find_usage`
5. `get_recipe`
6. `get_evidence`
7. `check_compatibility`
8. `get_capture_status`
9. `doctor`

Guarded append/write tools:

10. `reflect_on_task`
11. `submit_collector_result`
12. `submit_verifier_phase_a`
13. `submit_verifier_phase_b`
14. `record_runtime_result`

The write surface cannot bypass the existing collector → blind verifier → SQLite promotion rules.

## Local tests

Passed in the build environment:

```text
smoke:                OK
skill-contract:       OK
knowledge-writeback:  OK
legacy-provenance:    OK
orchestrator:         OK
MCP source contract:  OK
Python MCP bridge:    OK
JavaScript syntax:    OK
```

The official-SDK wire integration suite contains two tests:

- strict modern stdio, client pinned to `2026-07-28`;
- strict modern Streamable HTTP, client pinned to `2026-07-28`.

They assert modern era/version negotiation, tool discovery, output-schema-backed structured results, and a real Python retrieval call. In this build environment `npm install` could not reach the registry and timed out, so these two tests were **skipped**, not counted as passes. They run automatically after dependencies are installed (`cd mcp && npm install && npm test`).

## Retrieval regression

Main 15-case benchmark after MCP changes:

```text
Hit@1             80.0%
Hit@3             80.0%
Hit@5            100.0%
Hit@10           100.0%
Expected recall   91.1%
MRR@10             0.8433
```

Exact-term holdout (12 cases):

```text
Hit@1             75.0%
Hit@3             91.7%
Hit@10           100.0%
Recall             95.8%
```

Donor holdout (6 cases):

```text
Named donor Top-1   6/6
Named donor Top-3   6/6
Named donor Top-10  6/6
Donor warning       6/6
```

Therefore the MCP layer did not change the measured retrieval behavior.

## DeepSeek Harness gate

`dsh/mcp-exteracontext.example.yaml` configures the official DSH MCP client with stdio, `--modern-only`, and `failOnStartupError: true`. `dsh/MCP_STRICT_MODERN_TEST.md` is the first real external conformance test. Because legacy openings are rejected, successful DSH activation and tool discovery demonstrate that DSH negotiated the modern era rather than silently using a legacy fallback.
