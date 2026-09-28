# ExteraContext v0.6.1 acceptance-fix report

## Trigger

The first real DeepSeek Harness acceptance run demonstrated the MCP read path but exposed two integration defects:

1. staged write-back required `subagent_provider` / `subagent_id` values that the Harness child invocation did not expose to the parent;
2. Node→Python output inherited a Windows cp1251 path in the real environment, causing `get_recipe` to fail on U+2192 and corrupting Russian text.

## Fix 1 — MCP-issued capability actors

The public write-back API no longer requires host-internal child identity.

- `reflect_on_task` issues a one-time `collector_token` and internal collector actor ID.
- `submit_collector_result` validates/consumes the collector capability and issues a separate `verifier_token`.
- `submit_verifier_phase_a` and `submit_verifier_phase_b` require the same verifier capability.
- only SHA-256 token hashes are stored in orchestration state;
- status output never exposes token hashes or plaintext tokens;
- collector and verifier still create distinct `knowledge_runs`, so existing SQLite promotion guards remain authoritative.

Optional host runtime identity is accepted as `runtime_actor` provenance. It is never authorization. When verifier Phase A supplies concrete runtime child identity, Phase B must provide matching identity.

## Fix 2 — UTF-8 bridge

`mcp/src/bridge.mjs` now forces:

```text
PYTHONUTF8=1
PYTHONIOENCODING=utf-8
```

Python MCP-facing entry points additionally reconfigure stdout/stderr to UTF-8. A regression test round-trips:

```text
Проверка → русский текст ✓
```

without corruption, and a direct `recipe` call containing `→` now returns Russian recipe text successfully.

## Security/state-machine tests

`tests/test_orchestrator.py` verifies:

- wrong collector token rejected;
- token scoped to orchestration;
- collector token cannot authorize verifier stage;
- Phase B before Phase A rejected;
- collector evidence refs cannot be invented;
- collector/verifier runtime actors must differ when such metadata exists;
- verifier runtime identity continuity is checked when attested;
- replay after completion rejected;
- token plaintext/hashes absent from status output;
- original evidence provenance is preserved;
- write-back completes successfully with **no provider/child metadata**, reproducing the actual DSH acceptance environment.

## Regression results

Core tests:

```text
smoke:                 PASS
skill-contract:        PASS
knowledge-writeback:   PASS
legacy-provenance:     PASS
orchestrator:          PASS
MCP offline tests:     8/8 PASS
```

Retrieval remains unchanged:

```text
Hit@1        0.80
Hit@3        0.80
Hit@5        1.00
Hit@10       1.00
Recall@10    0.9111
MRR@10       0.8433
```

Exact-term holdout remains:

```text
Top1         0.75
Top3         0.9167
Top10        1.00
Recall       0.9583
```

The full official MCP SDK wire test still requires npm dependencies in the execution environment. The test remains included in `mcp/test/sdk-integration.test.mjs` and should be executed in DeepSeek Harness after `npm install`.
