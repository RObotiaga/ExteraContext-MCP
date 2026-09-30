"""Strict offline regression tests for benchmark protocol 1.1 (stdlib unittest)."""
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark" / "agent-v1"
sys.path.insert(0, str(BENCH))
from scoring import GROUND, RUNS, check_assessment, summarize  # noqa: E402
from prepare_run import PIN, prepare  # noqa: E402
from validate_result import validate_result  # noqa: E402


def assessment(run_id="v1-001"):
    run = RUNS[run_id]
    return {"protocol_version": "1.1", "assessment_schema_version": 2,
            "run_id": run_id, "task_id": run["task_id"], "mode": run["mode"], "repeat": run["repeat"],
            "pilot": False, "constraints": [{"constraint": c, "passed": True, "evidence": "inspected patch"}
                                            for c in GROUND[run["task_id"]].get("must_handle", [])],
            "constraints_handled": True, "materially_completed": True, "invented_api": False,
            "donor_contamination": False, "version_mismatch": False,
            "correct_unknown": True if run["task_id"] in {"version-sensitive-symbol", "donor-ayufilter", "unsupported-teleport"} else None,
            "evidence_boundary_correct": True, "unnecessary_low_level_fallback": False,
            "python_compile_success": True, "target_static_success": True,
            "load_success": None, "runtime_success": None, "reported_tests": [], "clean_success": True}


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)

    def test_pilot_ui_thread_failure_and_unknown_denominator(self):
        row = assessment()
        row["constraints"][2]["passed"] = False
        row["constraints"][2]["evidence"] = "callback ran on PluginWorker_0"
        row["constraints_handled"] = False
        row["clean_success"] = False
        result = summarize([(self.tmp / "assessment.json", row)])
        self.assertEqual(result["modes"]["A"]["constraints_handled_rate"], 0.0)
        self.assertIsNone(result["modes"]["A"]["correct_unknown_rate"])
        row["constraints_handled"] = True
        with self.assertRaisesRegex(ValueError, "contradicts"):
            check_assessment(row)
        row["constraints_handled"] = False
        row["correct_unknown"] = True
        with self.assertRaisesRegex(ValueError, "must be null"):
            check_assessment(row)

    def test_unknown_only_on_trap_tasks(self):
        for run_id in ("v1-022", "v1-025", "v1-028"):
            with self.subTest(run_id=run_id):
                row = assessment(run_id)
                check_assessment(row)
                row["correct_unknown"] = None
                with self.assertRaisesRegex(ValueError, "must be boolean"):
                    check_assessment(row)

    def test_compile_separate_from_target_static_and_clean_flags(self):
        row = assessment()
        row["target_static_success"] = False
        row["clean_success"] = False
        result = summarize([(self.tmp / "assessment.json", row)])
        self.assertEqual(result["modes"]["A"]["python_compile_success_rate"], 1.0)
        self.assertEqual(result["modes"]["A"]["target_static_success_rate"], 0.0)
        row["clean_success"] = True
        with self.assertRaisesRegex(ValueError, "clean_success"):
            check_assessment(row)
        row["target_static_success"] = True
        row["invented_api"] = True
        with self.assertRaisesRegex(ValueError, "clean_success"):
            check_assessment(row)

    def test_stale_duplicate_pilot_exclusion(self):
        from aggregate import aggregate
        pilot = ROOT / "run-v1-001" / "assessment.json"
        self.assertTrue(json.loads(pilot.read_text(encoding="utf-8"))["pilot"])
        (self.tmp / "pilot").mkdir()
        (self.tmp / "pilot" / "assessment.json").write_text(pilot.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no eligible"):
            aggregate(self.tmp)
        (self.tmp / "fresh").mkdir()
        path = self.tmp / "fresh" / "assessment.json"
        path.write_text(json.dumps(assessment()), encoding="utf-8")
        result = aggregate(self.tmp)
        self.assertEqual(result["runs"], 1)
        self.assertEqual(len(result["excluded_pilots"]), 1)
        stale = assessment()
        stale.pop("protocol_version")
        path.write_text(json.dumps(stale), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "protocol 1.1"):
            aggregate(self.tmp)
        path.write_text(json.dumps(assessment()), encoding="utf-8")
        (self.tmp / "duplicate").mkdir()
        (self.tmp / "duplicate" / "assessment.json").write_text(json.dumps(assessment()), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            aggregate(self.tmp)

    def test_verified_pass_requires_command_or_real_artifact(self):
        row = assessment()
        path = self.tmp / "assessment.json"
        row["reported_tests"] = [{"name": "unit", "status": "pass", "verified": True}]
        with self.assertRaisesRegex(ValueError, "lacks command"):
            check_assessment(row, path=path)
        row["reported_tests"][0]["artifact_path"] = "missing.log"
        with self.assertRaisesRegex(ValueError, "missing"):
            check_assessment(row, path=path)
        (self.tmp / "proof.log").write_text("pytest: 1 passed", encoding="utf-8")
        row["reported_tests"][0]["artifact_path"] = "proof.log"
        check_assessment(row, path=path)
        row["reported_tests"][0]["artifact_path"] = "../proof.log"
        with self.assertRaisesRegex(ValueError, "missing or external"):
            check_assessment(row, path=path)

    def test_result_pass_requires_evidence(self):
        schema = json.loads((BENCH / "result.schema.json").read_text(encoding="utf-8"))
        self.assertIn("allOf", schema["properties"]["tests"]["items"])
        target = self.tmp / "BENCHMARK_RESULT.json"
        item = {"name": "self report", "status": "pass"}
        target.write_text(json.dumps({"tests": [item]}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "requires"):
            validate_result(target)
        item["artifact_path"] = "nonexistent.log"
        target.write_text(json.dumps({"tests": [item]}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "missing"):
            validate_result(target)
        item["command"] = "python -m unittest"
        item.pop("artifact_path")
        target.write_text(json.dumps({"tests": [item]}), encoding="utf-8")
        validate_result(target)

    def test_prepare_c_isolated_offline_and_pinned(self):
        source = self.tmp / "source"
        source.mkdir()
        db = source / "base.sqlite"
        with closing(sqlite3.connect(db)) as conn:
            conn.execute("CREATE TABLE example (id INTEGER)")
        (source / ".knowledge-ref").write_text(PIN + "\n", encoding="utf-8")
        first, second = self.tmp / "v1-003", self.tmp / "v1-005"
        prepare("v1-003", first, frozen_db=db)
        prepare("v1-005", second, frozen_db=db)
        a = json.loads((first / "MCP_BENCHMARK_ENV.json").read_text(encoding="utf-8"))
        b = json.loads((second / "MCP_BENCHMARK_ENV.json").read_text(encoding="utf-8"))
        self.assertEqual(a["EXTERACONTEXT_KNOWLEDGE_REF"], PIN)
        self.assertEqual(b["EXTERACONTEXT_KNOWLEDGE_REF"], PIN)
        self.assertEqual(a["EXTERACONTEXT_AUTO_SYNC"], "0")
        for key in ("EXTERACONTEXT_DB", "EXTERACONTEXT_KNOWLEDGE_DB", "EXTERACONTEXT_RUN_ROOT"):
            self.assertNotEqual(a[key], b[key])
            self.assertTrue(Path(a[key]).resolve().is_relative_to(first.resolve()))
        self.assertEqual((first / "runtime" / ".knowledge-ref").read_text(encoding="utf-8").strip(), PIN)
        with closing(sqlite3.connect(a["EXTERACONTEXT_KNOWLEDGE_DB"])) as conn:
            conn.execute("CREATE TABLE run_a (id INTEGER)")
        with closing(sqlite3.connect(b["EXTERACONTEXT_KNOWLEDGE_DB"])) as conn:
            self.assertFalse(conn.execute("SELECT name FROM sqlite_master WHERE name='run_a'").fetchall())
        with self.assertRaisesRegex(ValueError, "overwrite"):
            prepare("v1-003", first, frozen_db=db)
        (source / ".knowledge-ref").write_text("wrong", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "stamp"):
            prepare("v1-012", self.tmp / "bad", frozen_db=db)

    def test_prepare_b_no_private_material(self):
        bare = self.tmp / "not-a-checkout"
        bare.mkdir()
        with self.assertRaises(subprocess.CalledProcessError):
            prepare("v1-002", self.tmp / "bad-b", frozen_checkout=bare)
        target = prepare("v1-002", self.tmp / "b")
        self.assertIn("NOT READY", (self.tmp / "b" / "RESOURCE_SETUP.md").read_text(encoding="utf-8"))
        self.assertFalse(any(p.name in {"ground-truth.json", "assessment.schema.json", "EVALUATOR.md"} for p in target.parent.rglob("*")))
        self.assertFalse((self.tmp / "b" / "MCP_BENCHMARK_ENV.json").exists())


if __name__ == "__main__":
    unittest.main()
