# ExteraContext MCP

MCP server and mutable knowledge runtime for ExteraContext.

The immutable ExteraGram/AyuGram knowledge corpus is produced separately. This repository owns retrieval, MCP serving, guarded write-back, orchestration, compatibility queries, DeepSeek Harness integration, and the separate mutable agent-knowledge store. Deployment below uses an **already built local SQLite corpus**; it performs no collection, network access, cloning, or execution of corpus scripts.

## Architecture

```text
Installed, already-built corpus SQLite
        |
        | offline validated copy (no rebuild)
        v
 data/exteracontext.sqlite   (deployed immutable base)
        +
 data/knowledge.sqlite       (mutable verified overlay)
        |
        v
 ExteraContext MCP 2026-07-28
```

## Offline deployment (no collection)

Prerequisite: an **existing, already-built** installed corpus at `C:\Users\sofar\.dsh\skills\exteracontext\data\exteracontext.sqlite` (or another local file passed explicitly). From the repository root, with Python 3 and SQLite FTS5 available:

```powershell
python scripts/sync_knowledge.py --source 'C:\Users\sofar\.dsh\skills\exteracontext\data\exteracontext.sqlite'
# Or specify both --source and --db for other local locations.
python -m unittest discover -s tests -p test_sync_security.py -v
```

The default source uses the current user's `~/.dsh/skills/exteracontext/data/exteracontext.sqlite`; default destination is `data/exteracontext.sqlite`. No `git`, network, package installation, crawler, or corpus `build_index.py` runs. `--repo` and `--ref` are intentionally unsupported; environment variables naming a knowledge repository/ref do not affect this command. Leave `EXTERACONTEXT_AUTO_SYNC` unset: the legacy automatic caller in `query.py` may invoke this offline script when enabled, but cannot fetch missing data; its ref/stamp mechanism is not provenance verification.

The script opens the installed source as SQLite `mode=ro&immutable=1` to avoid writing it (including WAL shared-memory files), checks integrity, required schema and nonempty docs/facts, then copies to a temporary file in the destination directory, revalidates and atomically replaces the destination. It **refuses a nonempty source WAL/journal**, since immutable reads omit uncheckpointed transactions; quiesce/checkpoint it externally before deployment. It also refuses a nonempty destination WAL/journal. Avoid concurrent writers or readers during replacement. Deployment writes `data/.knowledge-manifest.json` with SHA-256, source location, row counts and metadata; it explicitly does **not** claim verification of a git commit. The DB and manifest are each atomically replaced, not a single transaction across both files: after an interrupted deployment, compare the manifest digest to the deployed DB before use. No `.knowledge-ref` stamp is fabricated. The deployed SQLite is ignored by git (`data/*.sqlite`); do not commit it or sensitive local manifest provenance. The mutable `data/knowledge.sqlite` overlay is separate and unaffected.

If MCP dependencies are already installed, run the server with `node mcp/src/index.mjs --transport stdio --modern-only`. This offline deployment does not install them.

Security tests use temporary local SQLite fixtures only; `python -B -m unittest discover -s tests -p test_sync_security.py -v` passed **7/7 tests** (offline copy, source preservation, manifest, invalid/corrupt schema, WAL refusal, remote-option refusal, alias refusal, and zero-length WAL). A missing/invalid installed corpus must be supplied or rebuilt through a separate trusted process; this script cannot collect or rebuild one.

## MCP surface

The server exposes target resolution, knowledge/API/usage/recipe/evidence retrieval, compatibility checks, diagnostics, and the staged write-back protocol. v0.6.1 uses MCP-issued capability tokens so DeepSeek Harness does not need to expose internal child IDs. Optional runtime actor metadata can still be stored as provenance.

See `mcp/README.md`, `dsh/README.md`, `SKILL.md`, `NewKnowledge.md`, and `KnowledgeStore.md`.
