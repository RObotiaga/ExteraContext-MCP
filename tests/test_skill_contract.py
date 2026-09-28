#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
skill = (ROOT / "SKILL.md").read_text("utf-8")
new = (ROOT / "NewKnowledge.md").read_text("utf-8")

# Main skill must stay operational and compact enough for automatic invocation.
assert len(skill.split()) < 1300, len(skill.split())
assert "NewKnowledge.md" in skill
assert "KnowledgeStore.md" in skill
assert "scripts/knowledge.py" in skill
assert "Keep unknown fields unknown" in skill
assert "one concept" in skill.lower()
assert "donor" in skill.lower()
assert "runtime-verified" in skill
assert "Completion criterion" in skill
assert "main agent must not" in skill.lower()

# New-knowledge protocol must encode independent two-pass review, not self-confirmation.
for phrase in [
    "cheap collector",
    "independent verifier",
    "original evidence",
    "candidate",
    "attach-evidence",
    "conflict",
    "needs-runtime",
    "must never treat that same proposal as independent corroboration",
]:
    assert phrase.lower() in new.lower(), phrase

# Verifier must form its own view before being anchored by collector reasoning.
assert "independent extraction" in new.lower()
assert "collector's reasoning" in new.lower()
assert "phase a" in new.lower() and "phase b" in new.lower()
assert "do **not** show the collector's candidate yet" in new.lower()
assert "scripts/knowledge.py" in new
assert "sqlite triggers" in new.lower()

print("skill-contract: ok")
