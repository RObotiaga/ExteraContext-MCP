# ExteraContext MCP v0.6.1

ExteraContext exposes its existing Python/SQLite knowledge core as an MCP server. The MCP layer is intentionally thin: retrieval, provenance, write-back guards, orchestration, and benchmarks remain in the Python core.

## Protocol

The server targets MCP revision `2026-07-28` using the official TypeScript SDK v2 entry points:

- stdio: `serveStdio(factory)`
- Streamable HTTP: `createMcpHandler(factory)` + the Node adapter

Default serving is dual-era for compatibility: modern `2026-07-28` is available, while 2025-era clients can fall back. Pass `--modern-only` to reject legacy openings. This flag is useful for proving that a client such as DeepSeek Harness actually negotiated the modern revision.

## Install

Requires Node.js 20+ and Python 3 with this ExteraContext bundle intact.

```bash
cd mcp
npm install
```

Dependencies are pinned in `package.json`:

- `@modelcontextprotocol/server` 2.1.0
- `@modelcontextprotocol/node` 2.1.0
- `@modelcontextprotocol/client` 2.1.0 for integration tests
- `zod` 4.6.5

## Run over stdio

Compatible dual-era mode:

```bash
node mcp/src/index.mjs --transport stdio
```

Strict modern mode:

```bash
node mcp/src/index.mjs --transport stdio --modern-only
```

For a spawn-based MCP host, use an absolute working directory pointing at the ExteraContext root.

## Run over Streamable HTTP

```bash
node mcp/src/index.mjs --transport http --host 127.0.0.1 --port 7357 --path /mcp
```

Strict modern mode:

```bash
node mcp/src/index.mjs --transport http --host 127.0.0.1 --port 7357 --path /mcp --modern-only
```

v0.6.1 deliberately allows only loopback binds and has no authentication layer. Do not expose it directly to an untrusted network. Put authentication/reverse-proxy controls in front before changing the bind policy.

## MCP tools

Read path:

- `resolve_target`
- `search_knowledge`
- `find_api`
- `find_usage`
- `get_recipe`
- `get_evidence`
- `check_compatibility`
- `get_capture_status`
- `doctor`

Guarded write path:

- `reflect_on_task`
- `submit_collector_result`
- `submit_verifier_phase_a`
- `submit_verifier_phase_b`
- `record_runtime_result`

Every tool declares an output schema and returns both ordinary text content and `structuredContent`. Results also include the serving `protocol_era` and the intended `protocol_revision` in metadata. Read/write annotations mark read-only, destructive, open-world, and idempotent behavior.

The MCP layer does not weaken Knowledge Core rules. Trusted write-back still requires the collector + blind verifier state machine and SQLite promotion guards.

### Capability-token write-back

The public MCP API deliberately does **not** require DeepSeek Harness to expose internal child IDs. `reflect_on_task` returns a one-time `collector_token`; after a successful collector submission, `submit_collector_result` returns a distinct `verifier_token`. The verifier token is required for both Phase A and Phase B. Tokens are scoped to one orchestration, role, and stage; only token hashes are retained in orchestration state, and replay/wrong-role/wrong-orchestration calls fail closed.

Optional `runtime_actor` metadata (`provider`, `child_id`, `session_id`, `invocation_id`, `run_id`) may be supplied when the host exposes it. It is provenance, not authorization. If Phase A supplies a concrete runtime identity, Phase B must supply a matching identity.

### UTF-8 boundary

The Node bridge always launches Python with `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8`. This prevents Windows legacy code pages such as cp1251 from corrupting Russian text or failing on symbols such as `→`.

## Tests

Tests that do not require npm dependencies:

```bash
node --test mcp/test/mcp-contract.test.mjs
node --test mcp/test/bridge.test.mjs
```

After `npm install`, run the complete suite:

```bash
cd mcp
npm test
```

`test/sdk-integration.test.mjs` spawns the real stdio server in `--modern-only` mode with the official MCP client pinned to `2026-07-28`. It asserts:

- protocol era is `modern`;
- negotiated revision is exactly `2026-07-28`;
- tools are discoverable;
- `doctor` returns schema-validated `structuredContent`;
- the Python retrieval bridge works through a real MCP tool call.

If the official client package is not installed, that integration test explicitly skips rather than pretending the wire path was tested.
