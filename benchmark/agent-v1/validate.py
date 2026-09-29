#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
PROTOCOL_REVISION = "1.1"
FROZEN_KNOWLEDGE = "70f6f614227c8b02d241e7b1e72a0b6691442fd1"

tasks_doc = json.loads((ROOT / "tasks.json").read_text("utf-8"))
manifest = json.loads((ROOT / "run-manifest.json").read_text("utf-8"))
ground = json.loads((ROOT / "ground-truth.json").read_text("utf-8"))
result_schema = json.loads((ROOT / "result.schema.json").read_text("utf-8"))
assessment_schema = json.loads((ROOT / "assessment.schema.json").read_text("utf-8"))

tasks = tasks_doc["tasks"]
runs = manifest["runs"]
ids = [t["id"] for t in tasks]

assert len(tasks) == 10, len(tasks)
assert len(set(ids)) == 10
assert len(runs) == 60, len(runs)
assert set(ground["tasks"]) == set(ids)

assert manifest["protocol_revision"] == PROTOCOL_REVISION
assert manifest["frozen_knowledge_commit"] == FROZEN_KNOWLEDGE
assert ground["schema_version"] == 2
assert ground["protocol_revision"] == PROTOCOL_REVISION
assert all(r["protocol_revision"] == PROTOCOL_REVISION for r in runs)
assert all(r["frozen_knowledge_commit"] == FROZEN_KNOWLEDGE for r in runs)

correct_unknown_ids = {
    task_id
    for task_id, spec in ground["tasks"].items()
    if spec["correct_unknown_applicable"]
}
assert correct_unknown_ids == {
    "version-sensitive-symbol",
    "donor-ayufilter",
    "unsupported-teleport",
}

assert set(ground["tasks"]["outgoing-account-lifecycle"]["required_constraints"]) == {
    "triggering_account",
    "background_work",
    "ui_thread_return",
    "cleanup_reload",
}
for task_id, spec in ground["tasks"].items():
    assert isinstance(spec["required_constraints"], dict)
    assert isinstance(spec["correct_unknown_applicable"], bool)

# Protocol 1.1 schemas must prevent mixing old runs and unsupported test PASS claims.
assert "protocol_revision" in result_schema["required"]
assert result_schema["properties"]["protocol_revision"]["const"] == PROTOCOL_REVISION
test_item = result_schema["properties"]["tests"]["items"]
assert "evidence" in test_item["required"]
assert test_item["allOf"], "PASS evidence condition missing"

required_assessment = set(assessment_schema["required"])
for field in {
    "assessment_schema_version",
    "protocol_revision",
    "constraints",
    "python_compile_success",
    "target_static_success",
    "correct_unknown",
}:
    assert field in required_assessment
assert assessment_schema["properties"]["assessment_schema_version"]["const"] == 2
assert assessment_schema["properties"]["protocol_revision"]["const"] == PROTOCOL_REVISION

fixture = ROOT / "fixture-template"
assert (fixture / "README.md").exists()
assert (fixture / "plugin.py").exists()
assert (fixture / "fixture.json").exists()

counts = Counter((r["task_id"], r["mode"]) for r in runs)
for task_id in ids:
    for mode in ["A", "B", "C"]:
        assert counts[(task_id, mode)] == 2, (task_id, mode, counts[(task_id, mode)])

run_ids = [r["run_id"] for r in runs]
assert len(run_ids) == len(set(run_ids))
assert all(r["repeat"] in [1, 2] for r in runs)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)

    packets = {}
    for run_id in ["v1-001", "v1-002", "v1-003"]:
        out = root / run_id
        cp = subprocess.run(
            [sys.executable, str(ROOT / "prepare_run.py"), run_id, str(out)],
            check=True,
            capture_output=True,
            text=True,
        )
        packets[run_id] = out
        target = out / "target"
        assert target.exists()
        assert str(target) in cp.stdout
        names = {p.name for p in target.iterdir()}
        assert {
            "README.md", "plugin.py", "fixture.json",
            "BENCHMARK_MODE.md", "BENCHMARK_TASK.md", "result.schema.json"
        } <= names
        assert (out / "OPEN_IN_DSH.md").exists()
        assert (out / "RESOURCE_SETUP.md").exists()
        leaked = {
            p.name for p in out.rglob("*")
            if p.name in {"ground-truth.json", "assessment.schema.json", "EVALUATOR.md"}
        }
        assert not leaked, leaked

    # v1-001=A, v1-002=B, v1-003=C in the frozen manifest.
    a_setup = (packets["v1-001"] / "RESOURCE_SETUP.md").read_text("utf-8")
    b_setup = (packets["v1-002"] / "RESOURCE_SETUP.md").read_text("utf-8")
    c_setup = (packets["v1-003"] / "RESOURCE_SETUP.md").read_text("utf-8")
    assert "No ExteraContext Knowledge or MCP" in a_setup
    assert FROZEN_KNOWLEDGE in b_setup
    assert FROZEN_KNOWLEDGE in c_setup

    c_env = json.loads((packets["v1-003"] / "MCP_BENCHMARK_ENV.json").read_text("utf-8"))
    assert c_env["EXTERACONTEXT_KNOWLEDGE_REF"] == FROZEN_KNOWLEDGE
    assert c_env["EXTERACONTEXT_AUTO_SYNC"] == "1"
    for key in ["EXTERACONTEXT_DB", "EXTERACONTEXT_KNOWLEDGE_DB", "EXTERACONTEXT_RUN_ROOT"]:
        assert str(packets["v1-003"] / "runtime") in c_env[key]

    # Regression for the first pilot's evaluator bug:
    # correct_unknown must stay null for ordinary implementation tasks and
    # a failed required constraint must force constraints_handled=false.
    result_dir = root / "aggregate-fixture" / "run"
    result_dir.mkdir(parents=True)
    assessment = {
        "assessment_schema_version": 2,
        "protocol_revision": PROTOCOL_REVISION,
        "task_id": "outgoing-account-lifecycle",
        "mode": "A",
        "repeat": 1,
        "materially_completed": False,
        "invented_api": False,
        "donor_contamination": False,
        "version_mismatch": False,
        "constraints": {
            "triggering_account": {"passed": True, "evidence": "fixture"},
            "background_work": {"passed": True, "evidence": "fixture"},
            "ui_thread_return": {"passed": False, "evidence": "worker thread"},
            "cleanup_reload": {"passed": True, "evidence": "fixture"},
        },
        "constraints_handled": False,
        "correct_unknown": None,
        "unnecessary_low_level_fallback": False,
        "python_compile_success": True,
        "target_static_success": False,
        "load_success": None,
        "runtime_success": None,
        "evidence_boundary_correct": True,
        "verified_test_passes": 0,
        "unverified_self_reported_test_passes": 3,
        "clean_success": False,
        "notes": "protocol regression fixture",
    }
    (result_dir / "assessment.json").write_text(
        json.dumps(assessment, ensure_ascii=False, indent=2) + "\n",
        "utf-8",
    )
    agg = subprocess.run(
        [sys.executable, str(ROOT / "aggregate.py"), str(root / "aggregate-fixture")],
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(agg.stdout)
    mode_a = summary["modes"]["A"]
    assert mode_a["constraints_handled_rate"] == 0.0
    assert mode_a["correct_unknown_rate"] is None
    assert mode_a["python_compile_success_rate"] == 1.0
    assert mode_a["target_static_success_rate"] == 0.0
    assert mode_a["avg_unverified_self_reported_test_passes"] == 3.0

print("agent-benchmark-v1 protocol 1.1: ok")
