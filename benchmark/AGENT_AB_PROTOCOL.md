# End-to-end agent A/B/C protocol

This is the second-stage benchmark. It requires an external agent runner (Codex CLI, Claude Code, or another model API) so each trial starts with a fresh context.

## Modes

### A — baseline

Give the agent only the target plugin repository and the task prompt. Do not expose `data/wiki`, `scripts/query.py`, this skill, or previous answers.

### B — raw wiki

Give the same repository and task plus read access to `data/wiki`. Do not expose `scripts/query.py` or the ExteraContext instructions. The agent may search/read the wiki manually.

### C — ExteraContext

Give the same repository and task with this skill installed. Keep the wiki behind the skill. Require the normal skill behavior: build a task packet and resolve every load-bearing non-local symbol.

## Isolation

- New process/session for every task and every mode.
- Same model/version/thinking setting.
- Same target client and SDK version.
- Same repository commit.
- No answer from one run may be copied into another.
- Randomize A/B/C ordering per task if the runner permits it.

## Minimum suite

Use 10 tasks x 3 modes x 2 repeats = 60 fresh runs. A stronger run is 30 x 3 x 3 = 270.

Include ordinary public API tasks, lifecycle/reload, multi-account, threading, Java/Xposed, version-sensitive cases, donor traps, and unsupported/unknown cases.

## Automatic evidence checks

For every generated answer/code patch extract external symbols and classify them against the knowledge base:

- target-supported
- donor-only
- unknown
- version-uncertain
- runtime-verified (currently none)

Do not count `code` or `docs` as runtime verification.

## Runtime checks

When an Android test device is available, use build/load/runtime tiers:

1. Static: imports/symbols/signatures resolve.
2. Load: plugin installs/enables without error.
3. Runtime: trigger the feature and assert behavior.

For lifecycle tests perform enable -> trigger -> disable -> trigger -> enable -> trigger -> reload x5 and ensure callbacks do not multiply.

For multi-account tests trigger equivalent events under at least two logged-in account IDs and assert that the callback account is preserved end to end.

## Report

Report per mode:

- working-task rate
- unverified API rate
- donor-only API rate
- version mismatch rate
- load failure rate
- runtime failure rate
- correct-unknown rate
- median repair iterations
- median tool calls
- input/output tokens if the runner exposes them
