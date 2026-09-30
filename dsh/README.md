# DeepSeek Harness integration

## Install the skill in Harness

Extract/copy this whole bundle to one of the filesystem skill roots. For a project-local test, use:

```text
<project>/.dsh/skills/exteracontext/SKILL.md
```

The entire ExteraContext directory (scripts/data/docs/tests) must stay beside `SKILL.md`, because the skill executes local scripts by relative path. Harness also scans `<project>/.agents/skills`, `~/.dsh/skills`, and `~/.agents/skills`.


ExteraContext MCP v0.7.0 uses Harness native subagents for knowledge verification and exposes the Knowledge Core itself as a real MCP server.
The main agent owns orchestration state; DSH owns actual child isolation.

## Connect the ExteraContext MCP server

Install MCP dependencies once:

```bash
cd <exteracontext>/mcp
npm install
```

Add the `@deepseek-ai/dsh-mcp-client` entry from `dsh/mcp-exteracontext.example.yaml` to the active Harness composition. The recommended first run uses `--modern-only` plus `failOnStartupError: true`; successful activation then proves that Harness negotiated the modern protocol instead of falling back to a legacy `initialize` connection.

DSH exposes the raw MCP tool names as `mcp__exteracontext__<tool>`, for example `mcp__exteracontext__search_knowledge`. The official DSH MCP client performs its stdio negotiation in a disposable probe child before spawning the serving child, which matches the official MCP 2026-era stdio negotiation model.

See `dsh/MCP_STRICT_MODERN_TEST.md` for the first conformance smoke test.

## Required capabilities

Use a fresh collector child and an independently launched verifier for Phase A. A continuable verifier is preferred when Harness exposes a continuation handle, but MCP v0.7.0 does not depend on a child ID being visible to the main agent.

The critical blind invariant is temporal: **Phase A must be persisted before the collector candidate is revealed in the Phase-B prompt.** ExteraContext enforces that boundary in its state machine.

Configure the collector to a cheap model. Prefer a different/stronger verifier model for ambiguous or high-impact claims.

## DSH protocol

1. Main agent calls MCP `reflect_on_task` with the task, target, discovery and original evidence. The response contains the orchestration ID, collector prompt/schema, and a one-time `collector_token`.

2. Call a fresh `knowledge_collector` child with the returned collector prompt. Submit its JSON through MCP `submit_collector_result` with the returned `collector_token`. Do **not** invent child/provider IDs. If Harness actually exposes runtime identity, pass it only as optional `runtime_actor`. The response contains the blind Phase-A prompt and a separate one-time `verifier_token`.

3. Start an independent `knowledge_verifier` with the Phase-A prompt. Submit the blind JSON through MCP `submit_verifier_phase_a` with `verifier_token`. Only this successful transition may return/reveal the Phase-B prompt.

4. Prefer sending Phase B to the same verifier child when Harness provides a continuation mechanism. If it does not, a fresh verifier may process Phase B because Phase A is already immutable; use the **same verifier capability token**. If concrete runtime child identity was recorded in Phase A, ExteraContext requires matching identity in Phase B.

5. Submit Phase B through `submit_verifier_phase_b`; SQLite promotion guards decide the final claim state. The verifier token is consumed after completion.

Capability tokens are ExteraContext authorization, not evidence that the host truly created separate LLM processes. Physical isolation remains a Harness property. The acceptance test should record both layers separately.

## Provenance protection

Original evidence is assigned `source-001`, `source-002`, ... at `reflect`. The collector can only return `evidence_refs`; it cannot rewrite repository, commit, path, lines, URL, or excerpt. The orchestrator persists the original evidence selected by those references.

## First real test

Use a task where the result can be verified against the existing wiki and source, but is not already stated verbatim in the task. Recommended first test:

> For ExteraGram Android, determine whether `on_send_message_hook` receives the triggering account and what cleanup/lifecycle constraints are actually evidenced. Implement a minimal plugin using only supported target APIs. Record any genuinely new reusable finding through ExteraContext.

Success criteria:

- collector and verifier have distinct ExteraContext capability actors and distinct DB provenance run IDs; real DSH child IDs are checked only when actually exposed;
- verifier Phase A file contains no collector candidate text;
- Phase A is persisted before candidate disclosure; Phase B uses the same verifier capability token, and matching child identity when such identity was available;
- no donor-only API is promoted as ExteraGram target API;
- any new searchable claim has original evidence + independent verification;
- `python scripts/query.py search '<new claim phrase>'` retrieves an accepted claim;
- existing retrieval benchmark remains green.

See also `dsh/ACCEPTANCE_RETEST_PROMPT.md` for the acceptance regression test.
