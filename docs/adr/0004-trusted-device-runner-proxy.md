# ADR-0004: Isolated trusted device runner behind one ExteraContext MCP surface

Status: accepted

## Context

Closed-loop plugin development now has a real-device backend (`extera-plugin-test-mcp`) that owns ADB/DevServer access, plugin lifecycle mutations, deterministic acceptance tests, signed EvidenceBundles, and `DevelopmentRun` / `Iteration` state.

The existing ExteraContext MCP owns retrieval, provenance, collector/blind-verifier orchestration, and knowledge promotion. Merging the device runner directly into the knowledge process would enlarge the trusted computing base and couple two different MCP SDK/runtime stacks.

## Decision

ExteraContext remains the single MCP surface exposed to the coding agent, but the device/runtime implementation runs as an isolated trusted stdio subprocess.

The ExteraContext closed-loop entrypoint:

1. registers the 27 public device/development tools with the same names and bounded schemas;
2. lazily connects to `extera-plugin-test-mcp` through the official MCP client;
3. pins the trusted runner compiled fingerprint from `dist/runner-build.json`;
4. verifies the child server identity/version and the presence of all 27 required tools before the first delegated call;
5. proxies ordinary device/runtime calls without weakening their result semantics;
6. treats `knowledge_propose_from_test` specially: after the trusted runner accepts a signed PASS EvidenceBundle, ExteraContext starts its existing `reflect` orchestration and returns the collector capability/prompt rather than promoting the claim directly.

The runtime EvidenceBundle is machine evidence and may enter the capture workflow as `runtime-verified` evidence. The knowledge statement derived from that evidence remains a candidate until collector, blind verifier Phase A, comparison Phase B, and SQLite promotion guards succeed.

## Runner pin

The initially accepted runner archive is:

- archive SHA-256: `8033725591222a159e882a43e9a85d2f5ce7b13d1d6bdd24daf4177b5c3f8cf0`
- `dist/runner-build.json` compiled SHA-256: `240ab1d6d164b1e14b04d47289c7e6e17690d84ac78b72997c7767a2c2f742cc`
- producer/server: `extera-plugin-test-mcp` version `0.1.0`

A deliberate runner upgrade must update the pinned compiled fingerprint or explicitly provide `EXTERACONTEXT_DEVICE_MCP_EXPECTED_COMPILED_SHA256` after review.

## Configuration

Default runner entry:

`~/.codex/mcp-servers/extera-plugin-test-mcp/dist/src/server.js`

Overrides:

- `EXTERACONTEXT_DEVICE_MCP_ENTRY`
- `EXTERACONTEXT_DEVICE_MCP_COMMAND`
- `EXTERACONTEXT_DEVICE_MCP_ARGS` (JSON array of strings)
- `EXTERACONTEXT_DEVICE_MCP_CWD`
- `EXTERACONTEXT_DEVICE_MCP_EXPECTED_COMPILED_SHA256`

The runner keeps its own HMAC attestation secret. The coding agent is not given that secret.

## Consequences

- The coding agent sees one ExteraContext MCP instead of two unrelated servers.
- Knowledge-only operation remains available through the previous entrypoint.
- A missing/offline/untrusted device backend fails only device/development tool calls; it does not weaken or fabricate knowledge results.
- Device/runtime code can evolve independently, but upgrades are explicit because the parent verifies the runner build fingerprint and tool contract.
