# ExteraContext MCP v0.7.0

ExteraContext exposes one MCP surface for plugin-development knowledge, guarded write-back, and closed-loop runtime validation. It is a tool/control plane for an external coding agent; it is not a second coding agent.

The knowledge core remains Python/SQLite. Real-device operations are delegated to an isolated, build-pinned `extera-plugin-test-mcp` stdio subprocess. This keeps ADB/DevServer/runtime attestation outside the knowledge process while allowing the coding agent to use one MCP server.

## Protocol

The server targets MCP revision `2026-07-28` using the official TypeScript SDK v2 entry points:

- stdio: `serveStdio(factory)`
- Streamable HTTP: `createMcpHandler(factory)` + the Node adapter

Default serving remains dual-era for compatibility: modern `2026-07-28` is available, while 2025-era clients can fall back. Pass `--modern-only` to reject legacy openings.

## Install

Requires Node.js 20+ and Python 3.11+ with SQLite FTS5. Install the exact MCP dependency tree:

```bash
cd mcp
npm ci
```

The MCP client package is a runtime dependency because the closed-loop server uses it to proxy calls to the trusted device runner.

For an offline/production knowledge corpus, follow `docs/LOCAL_INSTALL.md` and `docs/PRODUCTION_READINESS.md`. The base knowledge database still performs its existing schema/manifest preflight before either transport starts.

### Install the trusted device backend

The first accepted backend build is documented in `docs/CLOSED_LOOP_DEVICE_BACKEND.md` and ADR-0004.

Default runner location:

```text
~/.codex/mcp-servers/extera-plugin-test-mcp/dist/src/server.js
```

Accepted build identity:

- reviewed archive SHA-256: `8033725591222a159e882a43e9a85d2f5ce7b13d1d6bdd24daf4177b5c3f8cf0`
- exact archive size: `2,417,871` bytes
- runner package/server version: `0.1.0`
- pinned compiled SHA-256: `240ab1d6d164b1e14b04d47289c7e6e17690d84ac78b72997c7767a2c2f742cc`

The archive filename version and package/server version are separate identifiers. ExteraContext trusts the reviewed compiled fingerprint from `dist/runner-build.json`, not the filename.

Optional runner overrides:

- `EXTERACONTEXT_DEVICE_MCP_ENTRY`
- `EXTERACONTEXT_DEVICE_MCP_COMMAND`
- `EXTERACONTEXT_DEVICE_MCP_ARGS` — JSON array of command arguments
- `EXTERACONTEXT_DEVICE_MCP_CWD`
- `EXTERACONTEXT_DEVICE_MCP_EXPECTED_COMPILED_SHA256`

A fingerprint override is an explicit trust decision and must not be changed automatically.

## Run

The normal entrypoint is now the closed-loop server.

Stdio:

```bash
npm start
# equivalent:
node mcp/src/index-closed-loop.mjs --transport stdio
```

Strict modern mode:

```bash
node mcp/src/index-closed-loop.mjs --transport stdio --modern-only
```

Streamable HTTP:

```bash
node mcp/src/index-closed-loop.mjs --transport http --host 127.0.0.1 --port 7357 --path /mcp
```

HTTP remains loopback-only and requires `EXTERACONTEXT_MCP_HTTP_TOKEN` of at least 32 UTF-8 bytes. Every MCP request must carry `Authorization: Bearer <token>`; Host/Origin validation remains enabled.

The previous knowledge-only server remains available as a fallback:

```bash
npm run start:core
# equivalent:
node mcp/src/index.mjs --transport stdio
```

A missing/offline/untrusted device backend fails device/development calls; it does not weaken or fabricate normal knowledge responses.

## Core knowledge tools

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
- `record_runtime_result` — intentionally still disabled for caller-provided PASS/FAIL

The existing capability-token protocol is unchanged: `reflect_on_task` issues a one-time collector token; collector submission issues a separate verifier token; Phase A remains blind and must be persisted before comparison Phase B.

## Closed-loop plugin-development tools

The closed-loop server adds the reviewed 27-tool device/runtime surface without requiring the coding agent to connect to a second MCP server.

### Layer 1 — Device & Environment

- `test_list_devices`
- `test_doctor`
- `plugin_get_sdk_info`
- `plugin_doctor`

### Layer 2 — Plugin Lifecycle & Navigation

- `plugin_list_installed`
- `plugin_inspect_installed`
- `plugin_get_status`
- `plugin_install`
- `plugin_update`
- `plugin_reload`
- `plugin_enable`
- `plugin_disable`
- `plugin_get_logs`
- `plugin_capture_settings`
- `plugin_open_chat`
- `plugin_open_dialogs`

### Layer 3 — Runtime Verification & Evidence

- `plugin_capture_screen`
- `plugin_assert`
- `plugin_get_diagnostics`
- `test_reset_state`
- `test_run`
- `test_collect_evidence`

### Layer 4 — Development Orchestration

- `development_start`
- `development_submit_revision`
- `development_execute_iteration`
- `development_get`

### Layer 5 — Knowledge Feedback

- `knowledge_propose_from_test`

## Development loop

A normal agent workflow is:

```text
knowledge retrieval / compatibility checks
        ↓
development_start
        ↓
external coding agent edits + builds
        ↓
development_submit_revision
        ↓
development_execute_iteration
        ↓
  PASS / FAIL / BLOCKED / INFRA_ERROR
        ↓
FAIL/BLOCKED → repair_context → external agent edits again
PASS → signed EvidenceBundle → optional knowledge proposal
```

The external agent remains responsible for source changes. `DevelopmentRun` and `Iteration` are state/evidence orchestration, not autonomous coding.

## Runtime trust boundary

`test_run` generates the runner-owned `test_run_id`. `test_collect_evidence` accepts that ID rather than caller-provided PASS/FAIL and creates the signed canonical EvidenceBundle. The device runner verifies its HMAC attestation before `knowledge_propose_from_test` accepts a PASS run.

The parent ExteraContext process additionally pins and verifies the child runner build/tool contract before delegated calls.

`knowledge_propose_from_test` does **not** directly promote a runtime claim. After the trusted runner accepts the signed evidence, ExteraContext immediately starts its existing `reflect_on_task` capture workflow. The resulting knowledge statement still passes:

```text
runtime-backed evidence
→ collector
→ blind verifier Phase A
→ verifier comparison Phase B
→ SQLite promotion guards
```

The attested runtime EvidenceBundle can be represented as `runtime-verified` evidence because its provenance/result come from the trusted runner. The statement derived from that evidence remains a candidate until the normal write-back gates pass.

The older public `record_runtime_result` remains fail-closed because its input is caller-controlled and therefore cannot substitute for this attested path.

## Trusted runner checks

Before the first delegated call, ExteraContext verifies:

1. `dist/runner-build.json` exists;
2. `compiled_sha256` matches the reviewed pin;
3. runner server name/version match the expected build when reported;
4. all 27 required tools are advertised.

A mismatch fails closed.

## Knowledge corpus and production boundaries

Provide a locally approved base database at `data/exteracontext.sqlite` or set `EXTERACONTEXT_DB`. The MCP process performs the existing offline SQLite/schema and manifest-consistency preflight before startup. `EXTERACONTEXT_REQUIRE_MANIFEST=1` keeps strict corpus deployments fail-closed.

`EXTERACONTEXT_AUTO_SYNC` unset or `0` remains offline. Only explicit `EXTERACONTEXT_AUTO_SYNC=1` invokes the existing hash-pinned knowledge updater. See `docs/PRODUCTION_READINESS.md` for release/workflow controls and corpus readiness checks.

The device runner is a separate trust domain. Its machine attestation secret belongs outside the coding workspace; the accepted backend defaults to `%USERPROFILE%/.codex/secrets/.runner_attestation_secret` or a protected `EXTERA_RUNNER_SECRET`.

## UTF-8 boundary

The Node/Python bridge still launches Python with `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8` so Russian text and symbols such as `→` do not depend on Windows legacy code pages.

## Tests

Static/offline contract checks:

```bash
cd mcp
npm run test:offline
```

Wire tests with installed SDK dependencies:

```bash
npm run test:wire
```

The wire suite includes:

- the existing ExteraContext stdio/HTTP SDK integration tests;
- `device-runner-wire.test.mjs`, which launches a fake child MCP runner, verifies the build/tool contract, performs a real stdio MCP proxy call, and verifies that a mismatched compiled fingerprint fails closed.

Full suite:

```bash
npm test
```

The fake runner proves the parent/child MCP protocol and trust-pin behavior without requiring ADB. It does not replace a real-device release gate. Before merging/deploying a new trusted runner build, run at least one combined `ExteraContext → trusted runner → target device` acceptance cycle in the intended Windows/device environment.
