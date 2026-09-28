# ExteraContext MCP

MCP server and mutable knowledge runtime for ExteraContext.

The immutable ExteraGram/AyuGram knowledge corpus lives in `RObotiaga/ExteraContext-Knowledge`. This repository owns retrieval, MCP serving, guarded write-back, orchestration, compatibility queries, DeepSeek Harness integration, and the separate mutable agent-knowledge store.

## Architecture

```text
ExteraContext-Knowledge
        |
        | compile / sync
        v
 data/exteracontext.sqlite   (immutable base, generated)
        +
 data/knowledge.sqlite       (mutable verified overlay)
        |
        v
 ExteraContext MCP 2026-07-28
```

Generated SQLite files are not committed. Bootstrap the base index with:

```bash
python scripts/sync_knowledge.py
```

Then install the MCP dependencies and start the server:

```bash
cd mcp
npm install
cd ..
node mcp/src/index.mjs --transport stdio --modern-only
```

Set `EXTERACONTEXT_AUTO_SYNC=1` to let `query.py` bootstrap the base database automatically when it is missing. `EXTERACONTEXT_KNOWLEDGE_REPO` can override the default knowledge repository URL.

## MCP surface

The server exposes target resolution, knowledge/API/usage/recipe/evidence retrieval, compatibility checks, diagnostics, and the staged write-back protocol. v0.6.1 uses MCP-issued capability tokens so DeepSeek Harness does not need to expose internal child IDs. Optional runtime actor metadata can still be stored as provenance.

See `mcp/README.md`, `dsh/README.md`, `SKILL.md`, `NewKnowledge.md`, and `KnowledgeStore.md`.
