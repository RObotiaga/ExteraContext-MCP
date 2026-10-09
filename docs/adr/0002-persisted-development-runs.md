# ADR-0002: Persist closed-loop development state in a dedicated runtime store

Status: Accepted

## Context

ExteraContext MCP is being extended from a knowledge/evidence server into a development control plane for an external coding agent. A single plugin task can require multiple source edits, builds, deployments, runtime tests, diagnoses, and retries before acceptance passes.

A stateless MCP would force the external agent to carry all previous build identities, artifact hashes, deployment state, test outcomes, and evidence between calls. Persisting that state in the plugin repository would mix machine-local runtime state with source-controlled project configuration.

The existing `knowledge.sqlite` has a different trust and lifecycle contract: it stores reusable knowledge candidates and verified knowledge, not transient development attempts.

## Decision

Introduce a dedicated mutable development store, nominally `data/development.sqlite`.

The primary persisted aggregate is `DevelopmentRun`:

- one run represents one project task/ticket;
- a run records project/task identity, target client/SDK/platform, acceptance requirements, and lifecycle status;
- each materially distinct source/artifact revision creates a child `Iteration`;
- each iteration records source identity, build result, artifact hashes, deployment identity, tests/assertions, evidence, diagnosis, and repair context;
- evidence is iteration-bound and cannot be silently reused as proof for a later source/artifact identity.

The storage boundaries are:

- `data/exteracontext.sqlite`: immutable base corpus;
- `data/knowledge.sqlite`: guarded reusable knowledge overlay;
- `data/development.sqlite`: development-run and iteration state;
- plugin repositories: declarative build/test/acceptance manifests only, with no transient device/run bookkeeping.

## Consequences

### Positive

- The external coding agent can resume a failed development loop without reconstructing state from conversation history.
- Artifact preflight can bind source revision, build output, deployment and device evidence to the same iteration.
- Stale artifact and stale evidence errors become detectable rather than being interpreted as functional failures.
- Historical iterations remain available for diagnosis and benchmark metrics.
- Reusable knowledge can be derived from successful runs without conflating transient execution state with the trusted knowledge store.

### Costs

- A development-state schema and migrations must be maintained separately from knowledge-store schema.
- Retention/cleanup policies are required for logs, screenshots and large evidence artifacts.
- Concurrency rules are required if multiple external agents operate on the same project/task.

## Rejected alternatives

### Stateless calls

Rejected because it moves orchestration state back into the external agent and undermines the goal of a closed development loop.

### Store development state in the plugin repository

Rejected because runtime/device state and evidence are environment-specific and should not dirty or couple the source tree.

### Reuse `knowledge.sqlite`

Rejected because development attempts and reusable trusted knowledge have different trust, retention and lifecycle semantics.
