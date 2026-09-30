# ExteraContext MCP v0.7.0 — DeepSeek Harness regression test

Use the installed `exteracontext` skill and the `mcp__exteracontext__*` tools for this task. Follow the retrieval and new-knowledge rules exactly. Use CLI scripts only if an MCP tool is unavailable.

Target: ExteraGram Android. First call `doctor` and record the actual MCP server version and protocol revision; this prompt targets v0.7.0, so a different deployed version must be reported as a mismatch rather than assumed to be v0.7.0. Determine the exact supported way to intercept an outgoing message before send, preserve the triggering account, and avoid duplicated hooks after plugin reload. Prefer public PySDK APIs over Java/Xposed internals when evidence supports them.

Requirements:
1. Resolve target/version from the workspace when available; keep unknown versions unknown.
2. Split retrieval into separate concepts: outgoing hook, account routing, lifecycle/cleanup.
3. Query ExteraContext before using every non-local runtime symbol.
4. Implement the smallest evidence-supported example or patch appropriate to the workspace.
5. If the work reveals reusable knowledge not already represented in ExteraContext, run the automatic write-back protocol through MCP:
   - `reflect_on_task` with original evidence;
   - delegate the returned collector prompt to a cheap fresh `knowledge_collector` child;
   - `submit_collector_result` with the `collector_token` returned by `reflect_on_task`; pass child/model metadata only if Harness actually exposes it;
   - delegate the returned Phase A prompt to a separate `knowledge_verifier` child; a continuable child is preferred but not required when no continuation handle is exposed;
   - `submit_verifier_phase_a` before the candidate is revealed;
   - after Phase A is persisted, process the returned Phase B using the same `verifier_token`; prefer the same verifier child when Harness exposes a continuation handle;
   - `submit_verifier_phase_b` with that same verifier capability and let the guarded commit decide the final state.
6. Do not manufacture evidence just to exercise write-back. If nothing new is learned, state that no new claim was persisted.
7. At the end report: MCP protocol era/revision from `doctor`, APIs used, evidence IDs, unresolved assumptions, capability actor/run IDs, optional real child IDs if exposed, final claim IDs/states, and commands/tests run.
8. Include one UTF-8 retrieval containing Russian text and the symbol `→`; any mojibake or encoding exception is a FAIL.
