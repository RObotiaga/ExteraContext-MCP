# ExteraContext MCP

MCP server and mutable knowledge runtime for ExteraContext.

The immutable ExteraGram/AyuGram knowledge corpus is produced separately. This repository owns retrieval, MCP serving, guarded write-back, orchestration, compatibility queries, DeepSeek Harness integration, and the separate mutable agent-knowledge store. Ordinary operation is offline: with `EXTERACONTEXT_AUTO_SYNC` unset or `0`, retrieval does not access the network. An explicit `EXTERACONTEXT_AUTO_SYNC=1` opts into the trusted, hash-pinned updater described below; it downloads only SQLite and manifest release assets from this fixed MCP GitHub repository and never clones or executes Knowledge repository code.

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

The default source uses the current user's `~/.dsh/skills/exteracontext/data/exteracontext.sqlite`; default destination is `data/exteracontext.sqlite`. This manual copy command performs no network access, package installation, crawling, or corpus `build_index.py` execution. `--repo` and `--ref` are intentionally unsupported. To provision an already-existing local corpus, run `python scripts/sync_knowledge.py --source <existing.sqlite> --db data/exteracontext.sqlite`.

Keep `EXTERACONTEXT_AUTO_SYNC` unset or set it to `0` for offline operation. Explicitly setting it to `1` invokes `scripts/update_knowledge.py`, which only downloads hash-pinned `exteracontext.sqlite` and `manifest.json` release assets from the fixed repository `RObotiaga/ExteraContext-MCP`; it verifies the lock's asset hashes, manifest source commit, schema and counts, SQLite integrity, and stages validated files before deployment. It does not clone or execute code from the Knowledge repository. The checked-in `KNOWLEDGE_LOCK` is currently a legacy bare commit SHA without artifact hashes, so the updater fails closed; opt-in updates remain unusable until a candidate is published and the lock-update PR is merged, with the matching per-commit release assets available. `EXTERACONTEXT_AUTO_BUILD=1` is a separate, explicit development-only opt-in for a monolithic checkout containing local `data/wiki` and `scripts/build_index.py`; do not enable it in production or split-repository deployments. A missing base database otherwise fails visibly.

The script opens the installed source as SQLite `mode=ro&immutable=1` to avoid writing it (including WAL shared-memory files), checks integrity, required schema and nonempty docs/facts, then copies to a temporary file in the destination directory, revalidates and atomically replaces the destination. It **refuses a nonempty source WAL/SHM/journal**, since immutable reads omit uncheckpointed transactions; quiesce/checkpoint it externally before deployment. It also refuses a nonempty destination WAL/SHM/journal. Avoid concurrent writers or readers during replacement. Deployment writes `data/.knowledge-manifest.json` with SHA-256, source location, row counts and metadata; it explicitly does **not** claim verification of a git commit. The DB and manifest are each atomically replaced, not a single transaction across both files: after an interrupted deployment, compare the manifest digest to the deployed DB before use. No `.knowledge-ref` stamp is fabricated. The deployed SQLite is ignored by git (`data/*.sqlite`); do not commit it or sensitive local manifest provenance. The mutable `data/knowledge.sqlite` overlay is separate and unaffected.

If MCP dependencies are already installed, run the server with `node mcp/src/index.mjs --transport stdio --modern-only`. This offline deployment does not install them.

## Protected Knowledge update workflow

`.github/workflows/sync-knowledge.yml` is scheduled daily and can also be dispatched manually. It resolves an exact commit of `RObotiaga/ExteraContext-Knowledge` `main`, builds a candidate in an unprivileged, read-only, networkless container, and runs the project's test suites against that candidate. Only after those checks does the publisher job wait for the protected GitHub Actions environment named `knowledge-publish`; a required reviewer must be configured by a repository administrator. After approval, the workflow publishes immutable-by-hash SQLite and manifest release assets in this MCP repository and opens a pull request to `develop` updating `KNOWLEDGE_LOCK`. There is no direct push to `main`. The lock becomes usable only after that PR is reviewed/merged and the matching per-commit release assets exist.

GitHub scheduled workflows execute from the repository's default branch. This Knowledge workflow has schedule/manual-dispatch triggers only: merging a PR into non-default `develop` or pushing a feature branch does not run Knowledge maintenance. Check the default branch before promotion. At the PR stage, the GitHub API reported `develop` unprotected and zero environments; before enabling publication, an administrator must configure `knowledge-publish` required reviewers and deployment branch restrictions, plus appropriate branch protection/rules. These protections are not established by the workflow YAML.

The scheduled workflow has not been run as part of this documentation update, and no updater fetch was executed here. GitHub Actions needs network access for checkout and package installation (including Actions services); only the candidate-build container is explicitly networkless. SHA-256 pins establish downloaded-byte integrity, not trust in the GitHub repository, GitHub/TLS account security, workflow administration, or reviewers. See [`docs/PRODUCTION_READINESS.md`](docs/PRODUCTION_READINESS.md) for required controls and the current lock status.

Security tests use temporary local SQLite fixtures only and cover offline copying, source preservation, manifests, invalid/corrupt schema, nonempty WAL/SHM refusal, remote-option refusal, source/destination alias refusal, and acceptance of zero-length WAL/SHM sidecars. The quiescence check refuses nonempty WAL, SHM, and journal sidecars; no new test run is claimed here. A missing/invalid installed corpus must be supplied or rebuilt through a separate trusted process; this script cannot collect or rebuild one.

## MCP surface

The server exposes target resolution, knowledge/API/usage/recipe/evidence retrieval, compatibility checks, diagnostics, and the staged write-back protocol. MCP v0.7.0 uses MCP-issued capability tokens so DeepSeek Harness does not need to expose internal child IDs. Optional runtime actor metadata can still be stored as provenance.

The MCP package version is sourced from `mcp/package.json` and reported by the handshake, `doctor`, CLI help and runtime errors. The root `VERSION` (`0.7.0-mcp`) intentionally identifies the bundled skill/repository release with its `-mcp` suffix; its numeric release tracks the MCP package version.

See `mcp/README.md`, `dsh/README.md`, `SKILL.md`, `NewKnowledge.md`, and `KnowledgeStore.md`.
