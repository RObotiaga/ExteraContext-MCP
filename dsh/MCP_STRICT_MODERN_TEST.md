# DeepSeek Harness strict-modern MCP test

Use this after `cd mcp && npm install` in the ExteraContext bundle.

## Why strict mode

Run ExteraContext with `--modern-only`. The server then rejects legacy `initialize` openings. If the DSH MCP plugin activates successfully and discovers the tools, the connection had to negotiate the modern MCP era rather than silently fall back to a 2025 revision.

## Configuration

Use `dsh/mcp-exteracontext.example.yaml` and replace its absolute `cwd`.

Expected model-facing names include:

```text
mcp__exteracontext__doctor
mcp__exteracontext__resolve_target
mcp__exteracontext__search_knowledge
mcp__exteracontext__find_api
mcp__exteracontext__reflect_on_task
```

## Smoke prompt

Give the Harness main agent:

```text
Use ExteraContext MCP. First call doctor. Then resolve target ExteraGram Android 12.10.1 / PySDK 1.4.5.5. Look up send_request and explain only what the retrieved target evidence establishes. Report the MCP protocol era/revision found in the tool metadata. Do not use web search for these API facts.
```

Pass conditions:

1. MCP plugin activation succeeds with `failOnStartupError: true`.
2. `mcp__exteracontext__doctor` exists.
3. `doctor` returns `ok: true` and `meta.protocol_era = modern`.
4. `meta.protocol_revision = 2026-07-28`.
5. `find_api` returns evidence for `send_request`.
6. No donor-only result is described as target support.

Then run `dsh/MAIN_AGENT_TEST_PROMPT.md` for the full retrieval → implementation → two-subagent knowledge-capture test.
