import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmark" / "agent-v1"))
import scoring
import build_frozen_corpus


class BenchmarkIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.packet_counter = 0
        self.key = b"K" * 32
        self.key_path = self.root / "operator-hmac.key"
        self.key_path.write_bytes(self.key)

    def _packet(self, mode="C", run_id=None):
        self.packet_counter += 1
        if run_id is None:
            run = next(row.copy() for row in scoring.RUNS.values() if row["mode"] == mode)
        else:
            run = scoring.RUNS[run_id].copy()
            mode = run["mode"]
        packet = self.root / f"{run['run_id']}-{self.packet_counter}"
        packet.mkdir(parents=True)
        run["protocol_revision"] = scoring.PROTOCOL
        package_version = scoring.MCP_PACKAGE_VERSION
        expected = {
            "frozen_commit": scoring.PIN,
            "frozen_tree": None,
            "db_sha256": None,
            "attestation_sha256": None,
            "mcp_package_version": package_version,
        }
        if mode == "B":
            expected["frozen_tree"] = "a" * 40
        if mode == "C":
            runtime = packet / "runtime"
            runtime.mkdir()
            db = runtime / "exteracontext.sqlite"
            db.write_bytes(b"frozen sqlite fixture")
            expected["db_sha256"] = hashlib.sha256(db.read_bytes()).hexdigest()
            payload = {
                "attestation_schema_version": build_frozen_corpus.SCHEMA_VERSION,
                "protocol_version": scoring.PROTOCOL,
                "source_commit": scoring.PIN,
                "source_tree": "a" * 40,
                "db_sha256": expected["db_sha256"],
                "db_size_bytes": len(db.read_bytes()),
                "db_schema_sha256": "b" * 64,
                "counts": {name: (1 if name in ("docs", "facts") else 0)
                           for name in build_frozen_corpus.COUNT_TABLES},
            }
            attestation = runtime / "frozen-corpus-attestation.json"
            signature = hmac.new(self.key, build_frozen_corpus.canonical(payload), hashlib.sha256).hexdigest()
            attestation.write_text(json.dumps({"payload": payload, "attestation": {
                "operator": "integrity-test-operator", "signed_at_utc": "2026-07-28T12:00:00+00:00",
                "algorithm": "HMAC-SHA256", "signature": signature,
            }}), encoding="utf-8")
            expected["attestation_sha256"] = hashlib.sha256(attestation.read_bytes()).hexdigest()
            doctor = packet / "doctor.json"
            doctor.write_text(json.dumps({"structuredContent": {
                "ok": True,
                "data": {"index": {
                    "db_path": str(db.resolve()),
                    "db_size_bytes": payload["db_size_bytes"],
                    "db_sha256": expected["db_sha256"],
                }, "mutable": {}, "mcp": {
                    "server_version": package_version,
                    "protocol_revision": scoring.MCP_PROTOCOL_REVISION,
                }},
                "warnings": [],
                "meta": {
                    "tool": "doctor",
                    "server_version": package_version,
                    "server_target_revision": scoring.MCP_PROTOCOL_REVISION,
                    "protocol_revision": scoring.MCP_PROTOCOL_REVISION,
                },
            }}), encoding="utf-8")
        run["environment_expected"] = expected
        if mode == "B":
            (packet / "knowledge-checkout.expected.json").write_text(json.dumps({
                "schema_version": 1, "commit": scoring.PIN, "tree": "a" * 40,
            }), encoding="utf-8")
            (packet / "knowledge-checkout.json").write_text(json.dumps({
                "schema_version": 1,
                "commands": ["git rev-parse HEAD", "git rev-parse HEAD^{tree}",
                             "git status --porcelain --untracked-files=all --ignored=matching"],
                "head": scoring.PIN,
                "tree": "a" * 40,
                "status_porcelain": "",
            }), encoding="utf-8")
        (packet / "RUN.json").write_text(json.dumps(run), encoding="utf-8")
        row = self._assessment(run)
        return packet, run, row

    @staticmethod
    def _assessment(run):
        must = scoring.GROUND[run["task_id"]].get("must_handle", [])
        unknown = run["task_id"] in scoring.UNKNOWN_TASKS
        evidence = {
            "applicability": "not-applicable",
            "rationale": "This A/B run does not deploy the MCP server.",
            "actual_mcp_package_version": None,
            "protocol_revision": None,
            "frozen_commit": None,
            "db_sha256": None,
            "attestation_sha256": None,
            "doctor_artifact_path": None,
        }
        if run["mode"] == "C":
            expected = run["environment_expected"]
            evidence = {
                "applicability": "verified",
                "rationale": "Evaluator inspected the in-packet MCP doctor JSON and pins.",
                "actual_mcp_package_version": expected["mcp_package_version"],
                "protocol_revision": scoring.MCP_PROTOCOL_REVISION,
                "frozen_commit": expected["frozen_commit"],
                "db_sha256": expected["db_sha256"],
                "attestation_sha256": expected["attestation_sha256"],
                "doctor_artifact_path": "doctor.json",
            }
        elif run["mode"] == "B":
            expected = run["environment_expected"]
            evidence.update({
                "checkout_verified": True,
                "knowledge_commit": scoring.PIN,
                "knowledge_tree": expected["frozen_tree"],
                "checkout_artifact_path": "knowledge-checkout.json",
            })
        return {
            "protocol_version": scoring.PROTOCOL,
            "assessment_schema_version": scoring.SCHEMA_VERSION,
            "run_id": run["run_id"], "task_id": run["task_id"], "mode": run["mode"],
            "repeat": run["repeat"], "pilot": False, "environment_evidence": evidence,
            "constraints": [{"constraint": item, "passed": True, "evidence": "reviewed"} for item in must],
            "constraints_handled": True, "materially_completed": True, "invented_api": False,
            "donor_contamination": False, "version_mismatch": False,
            "correct_unknown": True if unknown else None, "evidence_boundary_correct": True,
            "unnecessary_low_level_fallback": False, "python_compile_success": None,
            "target_static_success": True, "load_success": None, "runtime_success": None,
            "reported_tests": [], "clean_success": True,
        }

    def _check(self, packet, row, key_path="default"):
        assessment = packet / "assessment.json"
        assessment.write_text(json.dumps(row), encoding="utf-8")
        if key_path == "default":
            key_path = self.key_path
        scoring.check_assessment(row, path=assessment, attestation_key_path=key_path)

    def _refresh_attestation_pin(self, packet, run, row):
        attestation = packet / "runtime" / "frozen-corpus-attestation.json"
        digest = hashlib.sha256(attestation.read_bytes()).hexdigest()
        run["environment_expected"]["attestation_sha256"] = digest
        (packet / "RUN.json").write_text(json.dumps(run), encoding="utf-8")
        row["environment_evidence"]["attestation_sha256"] = digest

    def test_assessment_without_environment_evidence_fails(self):
        packet, _, row = self._packet("A")
        row.pop("environment_evidence")
        with self.assertRaisesRegex(ValueError, "environment_evidence"):
            self._check(packet, row)

    def test_mode_c_rejects_wrong_version_pin_hash_and_digest(self):
        for field, value in (("actual_mcp_package_version", "9.9.9"),
                             ("frozen_commit", "0" * 40),
                             ("db_sha256", "0" * 64),
                             ("attestation_sha256", "f" * 64)):
            with self.subTest(field=field):
                packet, _, row = self._packet("C")
                row["environment_evidence"][field] = value
                with self.assertRaises(ValueError):
                    self._check(packet, row)

    def test_missing_or_malicious_doctor_artifact_path_fails(self):
        for artifact in (None, "missing.json", "../../outside.json"):
            with self.subTest(artifact=artifact):
                packet, _, row = self._packet("C")
                row["environment_evidence"]["doctor_artifact_path"] = artifact
                with self.assertRaisesRegex(ValueError, "artifact"):
                    self._check(packet, row)

    def test_doctor_artifact_must_match_deployed_package_and_protocol(self):
        packet, _, row = self._packet("C")
        artifact = packet / "doctor.json"
        doctor = json.loads(artifact.read_text(encoding="utf-8"))
        doctor["structuredContent"]["meta"]["server_version"] = "0.6.1"
        artifact.write_text(json.dumps(doctor), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "doctor artifact"):
            self._check(packet, row)

    def test_mode_c_doctor_database_binding_rejects_wrong_path_hash_and_size(self):
        for field, value, message in (
                ("db_path", None, "db_path"),
                ("db_sha256", "f" * 64, "db_sha256"),
                ("db_size_bytes", 1, "db_size_bytes")):
            with self.subTest(field=field):
                packet, _, row = self._packet("C")
                artifact = packet / "doctor.json"
                doctor = json.loads(artifact.read_text(encoding="utf-8"))
                if field == "db_path":
                    wrong_db = packet / "runtime" / "other.sqlite"
                    wrong_db.write_bytes(b"other database")
                    value = str(wrong_db.resolve())
                doctor["structuredContent"]["data"]["index"][field] = value
                artifact.write_text(json.dumps(doctor), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    self._check(packet, row)

    def test_correct_in_packet_mode_c_evidence_passes(self):
        packet, _, row = self._packet("C")
        self._check(packet, row)

    def test_mode_c_requires_out_of_packet_attestation_key(self):
        packet, _, row = self._packet("C")
        with self.assertRaisesRegex(ValueError, "requires an out-of-packet"):
            self._check(packet, row, key_path=None)

    def test_mode_c_rejects_wrong_attestation_key(self):
        packet, _, row = self._packet("C")
        wrong_key = self.root / "wrong-operator.key"
        wrong_key.write_bytes(b"W" * 32)
        with self.assertRaisesRegex(ValueError, "signature is invalid"):
            self._check(packet, row, key_path=wrong_key)

    def test_mode_c_rejects_forged_signature_even_when_packet_hash_is_rebound(self):
        packet, run, row = self._packet("C")
        attestation_path = packet / "runtime" / "frozen-corpus-attestation.json"
        envelope = json.loads(attestation_path.read_text(encoding="utf-8"))
        envelope["attestation"]["signature"] = "0" * 64
        attestation_path.write_text(json.dumps(envelope), encoding="utf-8")
        self._refresh_attestation_pin(packet, run, row)
        with self.assertRaisesRegex(ValueError, "signature is invalid"):
            self._check(packet, row)

    def test_non_mcp_run_requires_not_applicable_rationale(self):
        packet, _, row = self._packet("A")
        self._check(packet, row)
        row["environment_evidence"]["rationale"] = " "
        with self.assertRaisesRegex(ValueError, "rationale"):
            self._check(packet, row)

    def test_run_environment_expected_is_closed_five_key_contract(self):
        for mode in ("A", "B", "C"):
            with self.subTest(mode=mode):
                packet, run, row = self._packet(mode)
                run["environment_expected"].pop("frozen_tree")
                (packet / "RUN.json").write_text(json.dumps(run), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "canonical frozen environment"):
                    self._check(packet, row)

                packet, run, row = self._packet(mode)
                run["environment_expected"]["unexpected"] = None
                (packet / "RUN.json").write_text(json.dumps(run), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "canonical frozen environment"):
                    self._check(packet, row)

    def test_mode_a_and_c_require_null_frozen_tree(self):
        for mode in ("A", "C"):
            with self.subTest(mode=mode):
                packet, run, row = self._packet(mode)
                run["environment_expected"]["frozen_tree"] = "a" * 40
                (packet / "RUN.json").write_text(json.dumps(run), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "must not pin a Knowledge tree"):
                    self._check(packet, row)

    def test_mode_b_requires_run_sidecar_evidence_tree_agreement(self):
        packet, run, row = self._packet("B")
        sidecar = packet / "knowledge-checkout.expected.json"
        expectation = json.loads(sidecar.read_text(encoding="utf-8"))
        expectation["tree"] = "b" * 40
        sidecar.write_text(json.dumps(expectation), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "checkout tree pin"):
            self._check(packet, row)

        packet, run, row = self._packet("B")
        run["environment_expected"]["frozen_tree"] = "not-a-tree"
        (packet / "RUN.json").write_text(json.dumps(run), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "valid frozen tree pin"):
            self._check(packet, row)

    def test_mode_b_requires_exact_pin_tree_and_local_clean_artifact(self):
        packet, _, row = self._packet("B")
        self._check(packet, row)
        cases = [
            ("checkout_verified", False, "verified"),
            ("knowledge_commit", "0" * 40, "RUN.json pin"),
            ("knowledge_tree", "b" * 40, "tree"),
            ("checkout_artifact_path", None, "artifact"),
            ("checkout_artifact_path", "../../outside.json", "artifact"),
        ]
        for field, value, message in cases:
            with self.subTest(field=field, value=value):
                packet, _, bad = self._packet("B")
                bad["environment_evidence"][field] = value
                with self.assertRaisesRegex(ValueError, message):
                    self._check(packet, bad)

    def test_mode_b_rejects_missing_or_dirty_or_mismatched_artifact(self):
        for mutate, message in (
            (lambda p, a: (p / "knowledge-checkout.json").unlink(), "artifact"),
            (lambda p, a: (p / "knowledge-checkout.json").write_text(json.dumps({
                "schema_version": 1,
                "commands": ["git rev-parse HEAD", "git rev-parse HEAD^{tree}",
                             "git status --porcelain --untracked-files=all --ignored=matching"],
                "head": scoring.PIN, "tree": "a" * 40, "status_porcelain": " M tracked.py"}), encoding="utf-8"), "dirty"),
            (lambda p, a: (p / "knowledge-checkout.json").write_text(json.dumps({
                "schema_version": 1,
                "commands": ["git rev-parse HEAD", "git rev-parse HEAD^{tree}",
                             "git status --porcelain --untracked-files=all"],
                "head": scoring.PIN, "tree": "a" * 40, "status_porcelain": ""}), encoding="utf-8"), "wrong, dirty, or mismatched"),
            (lambda p, a: (p / "knowledge-checkout.json").write_text("{}", encoding="utf-8"), "structure"),
        ):
            with self.subTest(message=message):
                packet, _, row = self._packet("B")
                mutate(packet, row)
                with self.assertRaisesRegex(ValueError, message):
                    self._check(packet, row)

    def test_mode_b_requires_prepared_tree_pin(self):
        packet, _, row = self._packet("B")
        (packet / "knowledge-checkout.expected.json").unlink()
        with self.assertRaisesRegex(ValueError, "expectation"):
            self._check(packet, row)

    def test_assessment_must_be_inside_packet_with_canonical_run(self):
        packet, run, row = self._packet("C")
        outside = self.root / "assessment.json"
        outside.write_text(json.dumps(row), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "prepared run packet"):
            scoring.check_assessment(row, path=outside)
        run["task_id"] = "tampered"
        (packet / "RUN.json").write_text(json.dumps(run), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "RUN.json"):
            self._check(packet, row)

    def _complete_schedule_records(self, run_ids=None):
        records = []
        selected = list(scoring.RUNS) if run_ids is None else list(run_ids)
        for run_id in selected:
            packet, _, row = self._packet(run_id=run_id)
            assessment = packet / "assessment.json"
            assessment.write_text(json.dumps(row), encoding="utf-8")
            records.append((assessment, row))
        return records

    def test_aggregation_rejects_59_of_60_and_lists_missing_run(self):
        all_ids = list(scoring.RUNS)
        records = self._complete_schedule_records(all_ids[:-1])
        missing = all_ids[-1]
        with self.assertRaisesRegex(ValueError, f"incomplete benchmark schedule.*{missing}"):
            scoring.summarize(records, attestation_key_path=self.key_path)

    def test_aggregation_rejects_duplicate_run_id(self):
        records = self._complete_schedule_records(["v1-001", "v1-001"])
        with self.assertRaisesRegex(ValueError, "duplicate run_id.*v1-001"):
            scoring.summarize(records)

    def test_mode_c_key_must_be_outside_every_packet_in_aggregation_set(self):
        c_run = next(run_id for run_id, run in scoring.RUNS.items() if run["mode"] == "C")
        a_run = next(run_id for run_id, run in scoring.RUNS.items() if run["mode"] == "A")
        records = self._complete_schedule_records([c_run, a_run])
        a_assessment, _ = next(record for record in records if record[1]["mode"] == "A")
        packet_key = a_assessment.parent / "operator-hmac.key"
        packet_key.write_bytes(self.key)

        # The key is outside the C packet and would pass its per-assessment check,
        # but must not be co-located inside any other packet in this aggregation.
        with self.assertRaisesRegex(ValueError, "outside every prepared run packet"):
            scoring.summarize(records, attestation_key_path=packet_key,
                              _allow_incomplete_for_tests=True)

    def test_complete_60_run_schedule_with_modes_abc_aggregates(self):
        records = self._complete_schedule_records()
        result = scoring.summarize(records, attestation_key_path=self.key_path)
        self.assertEqual(result["runs"], 60)
        self.assertEqual({mode: result["modes"][mode]["runs"] for mode in "ABC"},
                         {"A": 20, "B": 20, "C": 20})
        uncertainty = result["clean_success_uncertainty"]
        self.assertEqual(uncertainty["method"], "paired task-cluster percentile bootstrap")
        self.assertEqual(uncertainty["task_clusters"], 10)
        self.assertEqual(set(uncertainty["paired_differences"]), {"A-B", "A-C", "B-C"})
        self.assertIn("lower", uncertainty["confidence_intervals"]["A"])
        self.assertIn("upper", uncertainty["confidence_intervals"]["A"])


if __name__ == "__main__":
    unittest.main()
