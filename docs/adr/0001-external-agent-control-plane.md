# ADR-0001: ExteraContext MCP is a development control plane, not a coding agent

- Status: Accepted
- Date: 2026-10-09
- Branch: `feat/closed-loop-plugin-development`

## Context

ExteraContext MCP already provides evidence-aware plugin-development knowledge, compatibility retrieval, guarded write-back, and knowledge orchestration. The next design goal is a fully closed development loop for ExteraGram/AyuGram plugins: implementation guidance, build, artifact verification, deployment, runtime testing, failure diagnosis, retry, evidence capture, and reuse of successful implementations.

A central architectural question is whether ExteraContext MCP should itself become an autonomous coding agent or remain an instrument used by an external coding agent.

## Decision

ExteraContext MCP remains a **tool/control plane for an external coding agent**.

The external agent owns source-code edits and implementation decisions. ExteraContext MCP supplies the capabilities and state required to make those decisions evidence-aware and to verify them through the full development lifecycle.

ExteraContext MCP may orchestrate deterministic development operations such as task-context preparation, build invocation through bounded project adapters, artifact identity checks, deployment, runtime/acceptance testing, diagnostic routing, evidence collection, and guarded knowledge-candidate creation. It does not embed an LLM or autonomously act as a second coding agent.

## Consequences

### Positive

- Keeps model/provider choice outside the MCP.
- Avoids duplicating Codex/DeepSeek Harness/other coding-agent orchestration.
- Allows the same MCP to serve multiple coding agents.
- Gives runtime/device evidence an authority independent from the implementing model.
- Preserves the existing collector/verifier trust boundary for reusable knowledge.
- Makes project-development workflows deterministic and testable independently of LLM behavior.

### Constraints

- MCP APIs must return actionable structured context to the external agent when a repair is needed.
- Development-loop state must not assume the external agent can be resumed by provider-specific child/session identifiers.
- Source editing cannot be required for progress inside the MCP; the MCP must stop at an explicit `needs_agent_change` boundary and provide enough evidence for the external agent to continue.
- Runtime PASS reported by the external agent is not authoritative machine evidence.

## Rejected alternatives

### ExteraContext as a standalone coding agent

Rejected because it duplicates the responsibility of the external coding agent, couples the project to model/runtime concerns, and weakens the clean separation between implementation and independent validation.

### Hybrid embedded DeveloperAgent abstraction

Not selected as the primary architecture. A future host may wrap ExteraContext with its own agent abstraction, but that abstraction does not belong to the MCP core.
