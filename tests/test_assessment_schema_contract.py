"""The evaluator must enforce every field in the checked-in assessment schema."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark" / "agent-v1"
sys.path.insert(0, str(BENCH))
from scoring import check_assessment, validate_assessment_schema  # noqa: E402


class AssessmentSchemaContractTests(unittest.TestCase):
    def valid(self):
        return {
            "protocol_version": "1.1", "assessment_schema_version": 2,
            "run_id": "v1-001", "task_id": "example", "mode": "A", "repeat": 1,
            "pilot": False,
            "environment_evidence": {
                "applicability": "not-applicable", "rationale": "MCP not used",
                "actual_mcp_package_version": None, "protocol_revision": None,
                "frozen_commit": None, "db_sha256": None, "attestation_sha256": None,
                "doctor_artifact_path": None,
            },
            "constraints": [], "constraints_handled": True,
            "materially_completed": True, "invented_api": False,
            "donor_contamination": False, "version_mismatch": False,
            "correct_unknown": None, "evidence_boundary_correct": True,
            "unnecessary_low_level_fallback": False, "python_compile_success": None,
            "target_static_success": None, "load_success": None, "runtime_success": None,
            "reported_tests": [], "clean_success": True,
        }

    def assert_invalid(self, row, message):
        with self.assertRaisesRegex(ValueError, message):
            validate_assessment_schema(row)
        # Schema rejection must precede packet/semantic checks.
        with self.assertRaisesRegex(ValueError, message):
            check_assessment(row)

    def test_valid_schema_contract(self):
        validate_assessment_schema(self.valid())

    def test_rejects_missing_required_test_name_or_verified(self):
        for test in (
            {"status": "pass", "verified": False},
            {"name": "unit", "status": "pass"},
        ):
            with self.subTest(test=test):
                row = self.valid()
                row["reported_tests"] = [test]
                self.assert_invalid(row, "missing required property")

    def test_rejects_wrong_types_const_enum_pattern_and_extra_properties(self):
        cases = [
            ("expected type 'boolean'", lambda r: r.update(reported_tests=[{"name": "unit", "status": "pass", "verified": "yes"}])),
            ("does not equal const", lambda r: r.update(pilot=True)),
            ("not in enum", lambda r: r.update(mode="D")),
            ("does not match pattern", lambda r: r.update(run_id="bad-id")),
            ("additional property", lambda r: r.update(unexpected=True)),
            ("additional property", lambda r: r.update(environment_evidence={**r["environment_evidence"], "unexpected": 1})),
        ]
        for message, mutate in cases:
            with self.subTest(message=message):
                row = self.valid()
                mutate(row)
                self.assert_invalid(row, message)

    def test_rejects_numeric_bounds_and_nested_item_shape(self):
        row = self.valid()
        row["repeat"] = 0
        with self.assertRaisesRegex(ValueError, "below minimum"):
            validate_assessment_schema(row)
        row = self.valid()
        row["constraints"] = [{"constraint": "x", "passed": True, "evidence": ""}]
        with self.assertRaisesRegex(ValueError, "shorter than minLength"):
            validate_assessment_schema(row)

    def test_if_then_allof_contract_for_mode_b(self):
        row = self.valid()
        row["mode"] = "B"
        with self.assertRaisesRegex(ValueError, "missing required property 'checkout_verified'"):
            validate_assessment_schema(row)
        row["environment_evidence"].update({
            "checkout_verified": True,
            "knowledge_commit": "a" * 40,
            "knowledge_tree": "b" * 40,
            "checkout_artifact_path": "knowledge-checkout.json",
        })
        validate_assessment_schema(row)
        row["environment_evidence"]["knowledge_tree"] = "not-a-tree"
        with self.assertRaisesRegex(ValueError, "does not match pattern"):
            validate_assessment_schema(row)


if __name__ == "__main__":
    unittest.main()
