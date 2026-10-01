# Local ready-to-use MCP artifact (no public publication)

This path packages the real, separately built Knowledge SQLite corpus, its strict
manifest, the Python runtime, and locked JavaScript dependencies. It does not
change `KNOWLEDGE_LOCK`, GitHub settings, the protected publisher, or client config.
No public download/release is created by these commands.

## User prerequisites and installation

Supported/tested: Linux POSIX filesystem, Node.js >=20 and Python >=3.11 with
stdlib SQLite FTS5. The clean-container test used Alpine, Node 24.18.1 and Python
3.14.7. Python does not need pip packages; npm is NOT needed on the installed
machine. Node and Python themselves are not bundled. Use your OS's approved
package manager to supply them; the installer never uses sudo, installs global
packages, or modifies package-manager/config settings. Windows is unsupported by
this installer (`fcntl`, POSIX permissions/symlinks); macOS has not been tested.
The existing source/manual Windows deployment remains separate.

Obtain an independently reviewed artifact and its SHA-256 through a trusted
channel. Before extraction, compare `sha256sum artifact.tar.gz` with that trusted
value (or verify the trusted adjacent `.sha256` with `sha256sum -c`). An adjacent
checksum/manifest alone is NOT authentication. Do not extract an untrusted tar or
execute its bootstrap installer merely because it comes with its own checksum.

Extract the approved tarball into a new, user-owned directory. From that directory,
one command installs and starts stdio MCP, without database paths or environment:

```sh
python3 install.py --start
```

For an MCP host, install without starting (`python3 install.py`), then configure
that host yourself to spawn:

```text
command: python3
args: ["/home/YOUR_USER/.local/share/exteracontext/exteracontext"]
```

No special cwd or DB/overlay/run environment is needed. Append `--modern-only`
for strict MCP 2026-07-28. Direct shell startup also works:

```sh
~/.local/share/exteracontext/exteracontext --modern-only
```

Installer messages go to stderr, not the MCP stdout channel. `--root /absolute/path`
optionally chooses another user-owned, non-group/world-writable installation root.
Symlink directory components are refused. The installed launcher remembers this
location through its own directory; it does not require a saved environment file.

Layout:

```text
~/.local/share/exteracontext/
  exteracontext                 launcher
  .install.lock                serialized install/activation operations
  current -> releases/<digest> active release
  previous -> releases/<digest> rollback target after an update
  releases/<digest>/           read-only runtime, node_modules, base + manifest
  state/overlay.sqlite         mutable agent knowledge, lazily created on writes
  state/runs/                  capture/orchestration state
```

Every install/start verifies the exact file inventory and SHA-256s, then performs
existing full corpus preflight with strict manifest enforcement. Missing files,
extra files, symlinks, corrupt manifests, hash/schema/count mismatches, or invalid
SQLite fail closed. Runtime directories/files are made read-only (0555/0444).
Base retrieval retains immutable read-only SQLite behavior. The managed launcher
sets AUTO_SYNC/AUTO_BUILD=0 and REQUIRE_MANIFEST=1 regardless of user overrides;
it removes Python/Node module-injection environment variables. Other application
secrets, e.g. an intentionally configured HTTP bearer, are not written to disk.
This is a trusted-local-process boundary, not a sandbox against the same user or
root; installed code, Node/Python executables and PATH must be trusted.

HTTP remains loopback-only and requires the existing explicitly supplied bearer
secret. It is not a zero-config remote service. The default convenient path is
stdio, so no HTTP secret is generated, copied or silently weakened.

## Repeat install, update and rollback

Re-running `python3 install.py` from the same extracted artifact is idempotent:
the release is reverified and overlay/run directories are preserved. For an
update, independently review/checksum/extract a new artifact, then run its
`python3 install.py`. The candidate is verified and preflighted before atomically
switching `current`; failed candidate verification keeps the old active release.
Install/activation uses a filesystem lock. Running processes retain their resolved
old release; restart the MCP client to use the update. Old releases are retained;
there is deliberately no automatic pruning or overlay deletion.

```sh
~/.local/share/exteracontext/exteracontext --check
~/.local/share/exteracontext/exteracontext --rollback
```

Rollback verifies/preflights `previous`, switches pointers, and leaves user state
alone. It rolls back runtime/base, NOT overlay data or future incompatible overlay
schema migrations. Back up `state/` with writers stopped before upgrades that
change its schema. No cross-version schema migration compatibility is promised.
Pointer replacement is atomic, not a power-loss-durable transaction spanning the
launcher, previous pointer, state and filesystem. Stale staging entries fail closed
and need explicit inspection; there is no speculative automatic recovery.

## Maintainer: isolated real corpus build

Use an already available, locally approved Docker image containing `python3`
with SQLite FTS5. The helper resolves its local image ID and uses `--pull=never`.
It never executes Knowledge code on the host. Fetch public source without stored
credentials/hooks, inspect `scripts/build_index.py`, and record its exact commit.
All work/output directories should be disposable scratch directories.

```sh
git -c credential.helper= -c core.hooksPath=/dev/null clone --depth 1 \
  https://github.com/RObotiaga/ExteraContext-Knowledge.git /scratch/knowledge-source
# Inspect the builder and obtain git rev-parse HEAD before proceeding.
python3 -B scripts/build_local_corpus.py \
  --source /scratch/knowledge-source --source-commit EXACT_40_CHARACTER_COMMIT \
  --image APPROVED_LOCAL_IMAGE --output /scratch/new-corpus
```

The source must be clean and match the exact commit. The helper mounts a git
archive, not `.git`, as read-only. Docker runs UID 65534, network=none, read-only
root, no capabilities, no-new-privileges, bounded memory/CPU/pids and a noexec
scratch tmpfs. Only source/output are mounted for the untrusted builder. A second
isolated process mounts the project metadata validator and validates the candidate
SQLite/manifest; no container receives host environment or credentials.
`BUILD.json` records the resolved image ID/commit/isolation. Output must not exist.
No fixture corpus or synthetic fallback is supported by the build helper.

Fedora bind mounts in this environment required explicit
`--disable-selinux-label`. This disables the SELinux label layer for these disposable
containers only, not globally. Network/capability/UID/read-only boundaries remain;
it is a documented weakening, not an invisible workaround. Prefer correctly
labelled dedicated bind mounts when your policy permits them. Tooling requires
Python with tar extraction filters (tested 3.14; use 3.12+ for build tooling).

## Maintainer: dependency preparation and packaging

In an isolated dependency workspace, copy ONLY `mcp/package.json` and
`mcp/package-lock.json`, then run `npm ci --ignore-scripts --no-audit --no-fund`
with an approved Node/npm image. Fetching dependencies requires registry network
access or a populated npm cache. Do not mount home, credentials, repository `.git`
or production state. Retain the npm-ci log. No lifecycle scripts are executed.

```sh
python3 -B scripts/package_local.py \
  --corpus /scratch/new-corpus \
  --dependencies /scratch/dependency-build/node_modules \
  --output /scratch/artifacts/exteracontext-local.tar.gz
```

Packaging has an explicit runtime allowlist; no fixtures, builder, repository git
metadata, production state or user secrets are included. Dependencies must come
from the reviewed lock's successful npm-ci workspace; arbitrary `--dependencies`
input is not authenticated by the packager. Native Node dependencies are refused:
these would need platform-specific artifacts. `BUNDLE.json` records all payload
hashes, exact base HEAD and Knowledge commit. The runtime may include uncommitted
review changes: its file hashes, not HEAD alone, identify this local build.
Output is exclusive: existing reviewed artifacts are never overwritten.
The helper opens the candidate DB read-only using the existing validator; run
packaging itself in an unprivileged disposable environment for untrusted inputs.
The tarball and an adjacent `.sha256` are the local deliverables.

`package_local.py` is deliberately not a reproducible-bytes tar build: archive
metadata/timestamps can differ. File hashes and corpus digest identify content.
The corpus stores evidence URL/path/provenance records and indexed document text;
original remote raw source trees are not bundled. Static evidence is not Android
runtime verification, and a donor fact is not proof of target compatibility.

## Clean artifact acceptance

`scripts/local_acceptance.mjs` uses the official SDK shipped inside the artifact,
not a host SDK. Run it as `/acceptance.mjs` in a fresh networkless container with
HOME=/work/home, a writable disposable /work, the approved tar at /artifact.tar.gz,
and writable disposable /out. No repository, prior deps/db or user environment
is needed. Container entry command:

```sh
mkdir -p /work/home /work/extracted && \
  tar -xzf /artifact.tar.gz -C /work/extracted && node /acceptance.mjs
```

It checks initialize/list, doctor, representative search/API/evidence with real
`official-sdk:send-request`, restart/reinstall preservation, captured run storage,
a failed inner-manifest candidate, update/rollback real retrieval and tamper
rejection. An initialized overlay sentinel is LOCAL STATE TEST DATA, never a
replacement corpus. The update probe changes only the local version marker to
exercise pointer mechanics; it does not prove future schema migration compatibility.
Wire traces redact capabilities. `/out/acceptance-results.json` and
`/out/acceptance-trace.json` retain actual results.

## Distribution limitations

This local artifact solves absent corpus/releases for offline installation only.
It does NOT make the existing remote AUTO_SYNC usable, publish a release, advance
KNOWLEDGE_LOCK, alter protected publication, establish branch/environment rules,
or make claims about other OSes. Public Knowledge has no top-level LICENSE file
in the inspected source; redistribution/licensing must be reviewed before any
public bundle publication. Parent/independent review remains required before
publication or production promotion.
