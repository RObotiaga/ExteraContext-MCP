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

HTTP starts only on loopback and **requires** `EXTERACONTEXT_MCP_HTTP_TOKEN` in the server environment (at least 32 UTF-8 bytes). Every MCP request must carry `Authorization: Bearer <token>`; missing/invalid credentials are rejected. Set the token through a protected process environment or secret manager, not in a URL, command line, checked-in config, or shell history. Protect the client-side bearer credential as well. Host/Origin checks and a loopback bind are additional boundaries, not a substitute for authentication; do not expose the port to an untrusted network. Use a trusted reverse proxy with its own access policy if remote access is required; the server itself does not permit public binds. `stdio` has no bearer handshake and assumes a trusted local launching host, process environment, and filesystem.

Before starting either transport, provide a locally approved existing base database at `data/exteracontext.sqlite` or set `EXTERACONTEXT_DB` to one. For an offline copy of an already-installed corpus, run `python scripts/sync_knowledge.py --source <existing.sqlite> --db data/exteracontext.sqlite` after taking a backup and quiescing its SQLite writer. The resulting `data/.knowledge-manifest.json` records the source/destination SHA-256 and schema, but `commit_verified: false`: a repository-commit pin cannot be verified from this copy. This operation never collects or rebuilds a corpus. Do not turn on `EXTERACONTEXT_AUTO_SYNC`; startup must fail visibly if the base DB is absent. See [production readiness](../docs/PRODUCTION_READINESS.md) for deployment and startup checks.

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
- `record_runtime_result` (**currently disabled**: the MCP endpoint fails closed until a trusted machine attestation is integrated; caller-provided PASS/FAIL cannot establish runtime verification)

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

After dependencies are already available locally, run the complete suite (do not install or fetch packages as part of an offline verification):

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

If the official client package is not installed, that integration test explicitly skips rather than pretending the wire path was tested. A skip is **not** an SDK integration pass. Corpus-independent CI does not exercise the real SDK/database wire path. The HTTP bearer boundary test provisions a test-only token and checks unauthenticated and bearer-supplied requests; the authenticated **client SDK HTTP negotiation test is explicitly skipped** pending verification of the client SDK Authorization option. This is not an HTTP SDK integration pass. Verify installed local dependencies, approved corpus, authenticated negotiation, and negative authentication cases in the intended environment before release.
