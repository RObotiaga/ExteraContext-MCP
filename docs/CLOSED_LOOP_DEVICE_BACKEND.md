# Closed-loop plugin development backend

ExteraContext can expose the trusted `extera-plugin-test-mcp` runtime harness through the same MCP surface as knowledge retrieval and guarded write-back. The coding agent still owns source edits; ExteraContext owns the development state/evidence loop around those edits.

## Accepted runner build

The first accepted backend is the fixed archive supplied and reviewed for this integration:

- archive file family: `extera-plugin-test-mcp-v1.0.1-fixed.zip`
- archive SHA-256: `8033725591222a159e882a43e9a85d2f5ce7b13d1d6bdd24daf4177b5c3f8cf0`
- exact archive size: `2,417,871` bytes
- backend package/server version: `0.1.0`
- `dist/runner-build.json` compiled SHA-256: `240ab1d6d164b1e14b04d47289c7e6e17690d84ac78b72997c7767a2c2f742cc`

The archive filename version and the package/server version are intentionally recorded separately. The trust pin used by ExteraContext is the compiled SHA-256 from `runner-build.json`, not the filename.

## Installation

The default runtime location is:

```text
~/.codex/mcp-servers/extera-plugin-test-mcp/
```

On Windows this normally expands to:

```text
C:\Users\<user>\.codex\mcp-servers\extera-plugin-test-mcp\
```

Extract the trusted backend so this file exists:

```text
~/.codex/mcp-servers/extera-plugin-test-mcp/dist/src/server.js
```

Install its locked dependencies according to that backend's package metadata. The backend itself requires the host/device dependencies it reports through `test_doctor` (ADB, Java/Android tooling, Appium where required, and access to the ExteraGram/AyuGram DevServer).

ExteraContext's own MCP dependencies are installed separately:

```bash
cd mcp
npm ci
```

`@modelcontextprotocol/client` is a runtime dependency because ExteraContext uses it to speak MCP to the isolated trusted runner.

## Configuration

The default runner path can be overridden without changing repository code:

- `EXTERACONTEXT_DEVICE_MCP_ENTRY` — path to the runner JS entrypoint;
- `EXTERACONTEXT_DEVICE_MCP_COMMAND` — executable used to spawn it (defaults to the current Node executable);
- `EXTERACONTEXT_DEVICE_MCP_ARGS` — JSON array of command arguments;
- `EXTERACONTEXT_DEVICE_MCP_CWD` — runner working directory;
- `EXTERACONTEXT_DEVICE_MCP_EXPECTED_COMPILED_SHA256` — reviewed runner fingerprint override.

The last variable is an explicit trust override, not an auto-discovery mechanism. Change it only after reviewing and testing a new runner build.

The runner keeps its HMAC attestation secret outside the project workspace (default `%USERPROFILE%/.codex/secrets/.runner_attestation_secret`, or `EXTERA_RUNNER_SECRET`). The coding agent must not receive that secret.

## One MCP surface, two trust domains

```text
external coding agent
        |
        v
ExteraContext MCP
  | knowledge core
  |  - retrieval
  |  - provenance
  |  - collector / blind verifier
  |  - promotion guards
  |
  +-- trusted stdio MCP child
       extera-plugin-test-mcp
       - ADB / DevServer
       - plugin install/update/reload
       - deterministic assertions
       - DevelopmentRun / Iteration
       - signed EvidenceBundle
```

The device runner is lazy-started on the first delegated device/development call. Before accepting it, the parent verifies:

1. `dist/runner-build.json` exists;
2. `compiled_sha256` matches the pinned/reviewed value;
3. child server identity is `extera-plugin-test-mcp` when the child reports a name;
4. child server version matches the build manifest;
5. all 27 required tools are advertised.

A mismatch fails closed. Knowledge-only tools do not depend on the device runner being online.

## Tool surface

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

### Layer 5 — Knowledge Feedback Bridge

- `knowledge_propose_from_test`

The parent exposes these names directly; the coding agent does not need to connect to a second MCP server.

## Trusted evidence flow

`test_run` creates the runner-owned `test_run_id`. `test_collect_evidence` accepts the ID rather than caller-supplied PASS/FAIL and creates the signed canonical EvidenceBundle. The runner verifies that attestation before `knowledge_propose_from_test` accepts the runtime proposal.

ExteraContext then performs an additional semantic boundary:

```text
signed PASS EvidenceBundle
        |
        v
runtime-backed evidence item
        |
        v
reflect_on_task / collector workflow
        |
        v
collector
        |
        v
blind verifier Phase A
        |
        v
comparison Phase B
        |
        v
SQLite promotion guards
```

The machine evidence may be marked `runtime-verified` because its provenance and PASS result are attested by the trusted runner. The *derived knowledge statement* is still only a candidate until the collector/verifier workflow succeeds. A narrow runtime observation must not be generalized solely because the underlying test passed.

The old public `record_runtime_result` endpoint intentionally remains fail-closed. It accepts caller-controlled PASS/FAIL and therefore is not used for this path.

## Normal coding-agent loop

```text
prepare/search knowledge
        |
        v
development_start
        |
external agent edits/builds source
        |
        v
development_submit_revision
        |
        v
development_execute_iteration
        |
    +---+---+
    |       |
   PASS    FAIL/BLOCKED
    |       |
    |       +--> repair_context -> external agent edits again
    |
    +--> optional knowledge_propose_from_test
             -> collector / blind verifier
```

`DevelopmentRun` is state/evidence orchestration. It never authorizes ExteraContext to become a second autonomous coding agent.

## Tests

The repository has two levels that do not require a physical phone:

- `mcp/test/closed-loop-contract.test.mjs` — static contract for the 27 tools, trust pin and knowledge bridge;
- `mcp/test/device-runner-wire.test.mjs` — starts a fake MCP runner, verifies the build/tool contract, performs a real stdio MCP call through the proxy, and verifies a bad build fingerprint fails closed.

Run:

```bash
cd mcp
npm test
```

A real-device release gate remains separate: run the trusted backend against the intended AyuGram/ExteraGram client and device, then run at least one combined ExteraContext -> runner -> device acceptance cycle. A fake-runner wire pass proves the proxy protocol, not physical-device behavior.
