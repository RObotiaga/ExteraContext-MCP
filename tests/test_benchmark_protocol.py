"""Strict offline regression tests for benchmark protocol 1.1 (stdlib unittest)."""
import hashlib
import hmac
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from unittest import mock
from types import SimpleNamespace
from pathlib import Path


def make_symlink_or_skip(test: unittest.TestCase, target: Path, link: Path) -> None:
    """Skip only when the OS denies symlink creation for lack of privilege."""
    try:
        link.symlink_to(target)
    except OSError as exc:
        if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 1314:
            test.skipTest(f"symlink creation requires privileges unavailable on this platform: {exc}")
        raise


ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark" / "agent-v1"
sys.path.insert(0, str(BENCH))
from scoring import GROUND, RUNS, check_assessment, summarize  # noqa: E402
import build_frozen_corpus as frozen  # noqa: E402
import prepare_run as prepare_module  # noqa: E402
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
        self.key_path = self.tmp / "attestation-test.key"
        self.key = b"0123456789abcdef0123456789abcdef"
        if len(self.key) < 32:
            raise AssertionError("test HMAC key must contain at least 32 bytes")
        self.key_path.write_bytes(self.key)

    def prepare_packet(self, run_id, packet):
        """Prepare packets offline; Mode B mocks only the three Git outputs."""
        mode = next(row["mode"] for row in RUNS.values() if row["run_id"] == run_id)
        if mode != "B":
            return prepare(run_id, packet)
        checkout = self.tmp / f"checkout-{packet.name}"
        checkout.mkdir()
        tree = "0123456789abcdef" * 2 + "01234567"

        def git_run(command, **kwargs):
            if command[:4] != ["git", "--no-optional-locks", "--no-replace-objects", "-C"]:
                raise AssertionError("unexpected subprocess command in Mode B test")
            git_args = command[5:]
            outputs = {
                ("rev-parse", "HEAD"): PIN,
                ("rev-parse", "HEAD^{tree}"): tree,
                ("status", "--porcelain", "--untracked-files=all", "--ignored=matching"): "",
            }
            key = tuple(git_args)
            if key not in outputs:
                raise AssertionError(f"unexpected Git query: {key!r}")
            return SimpleNamespace(stdout=outputs[key])

        with mock.patch.object(prepare_module.subprocess, "run", side_effect=git_run):
            return prepare(run_id, packet, frozen_checkout=checkout)

    def test_mode_b_prepare_rejects_gitignored_local_file(self):
        checkout = self.tmp / "ignored-contamination-checkout"
        checkout.mkdir()
        subprocess.run(["git", "init", str(checkout)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(checkout), "config", "user.name", "Benchmark Test"], check=True)
        subprocess.run(["git", "-C", str(checkout), "config", "user.email", "benchmark@example.invalid"], check=True)
        (checkout / ".gitignore").write_text("local-secret.bin\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(checkout), "add", ".gitignore"], check=True)
        subprocess.run(["git", "-C", str(checkout), "commit", "-m", "fixture"], check=True, capture_output=True)
        (checkout / "local-secret.bin").write_bytes(b"ignored local contamination")

        actual_run = subprocess.run
        tree = "0123456789abcdef" * 2 + "01234567"

        def report_pinned_commit_but_real_status(command, **kwargs):
            git_args = command[5:]
            if git_args == ["rev-parse", "HEAD"]:
                return SimpleNamespace(stdout=PIN + "\n")
            if git_args == ["rev-parse", "HEAD^{tree}"]:
                return SimpleNamespace(stdout=tree + "\n")
            return actual_run(command, **kwargs)

        run_id = next(row["run_id"] for row in RUNS.values() if row["mode"] == "B")
        with mock.patch.object(prepare_module.subprocess, "run", side_effect=report_pinned_commit_but_real_status):
            with self.assertRaisesRegex(ValueError, "including ignored files"):
                prepare(run_id, self.tmp / "ignored-contamination-packet", frozen_checkout=checkout)

    def make_packet_record(self, run_id="v1-001", directory="packet", row=None):
        """Create a prepared packet plus protocol-valid evaluator evidence."""
        packet = self.tmp / directory
        self.prepare_packet(run_id, packet)
        run = json.loads((packet / "RUN.json").read_text(encoding="utf-8"))
        row = assessment(run_id) if row is None else row
        evidence = {"applicability": "not-applicable", "rationale": "A/B has no MCP deployment",
                    "actual_mcp_package_version": None, "protocol_revision": None,
                    "frozen_commit": None, "db_sha256": None, "attestation_sha256": None,
                    "doctor_artifact_path": None}
        if run["mode"] == "B":
            expected = run["environment_expected"]
            checkout_artifact = packet / "knowledge-checkout.json"
            checkout_artifact.write_text(json.dumps({
                "schema_version": 1,
                "commands": ["git rev-parse HEAD", "git rev-parse HEAD^{tree}",
                             "git status --porcelain --untracked-files=all --ignored=matching"],
                "head": PIN,
                "tree": expected["frozen_tree"],
                "status_porcelain": "",
            }), encoding="utf-8")
            evidence.update({"checkout_verified": True,
                             "knowledge_commit": PIN,
                             "knowledge_tree": expected["frozen_tree"],
                             "checkout_artifact_path": "knowledge-checkout.json"})
        if run["mode"] == "C":
            from scoring import MCP_PACKAGE_VERSION, MCP_PROTOCOL_REVISION
            runtime = packet / "runtime"
            database = runtime / "exteracontext.sqlite"
            database.write_bytes(b"test frozen database")
            db_hash = hashlib.sha256(database.read_bytes()).hexdigest()
            payload = {
                "attestation_schema_version": frozen.SCHEMA_VERSION,
                "protocol_version": "1.1",
                "source_commit": PIN,
                "source_tree": "a" * 40,
                "db_sha256": db_hash,
                "db_size_bytes": database.stat().st_size,
                "db_schema_sha256": "b" * 64,
                "counts": {table: (1 if table in {"docs", "facts"} else 0)
                           for table in frozen.COUNT_TABLES},
            }
            frozen.validate_payload(payload)
            attestation = runtime / "frozen-corpus-attestation.json"
            envelope = {
                "payload": payload,
                "attestation": {
                    "operator": "test operator",
                    "signed_at_utc": "2026-07-28T12:00:00+00:00",
                    "algorithm": "HMAC-SHA256",
                    "signature": hmac.new(self.key, frozen.canonical(payload), hashlib.sha256).hexdigest(),
                },
            }
            attestation.write_text(json.dumps(envelope), encoding="utf-8")
            attestation_hash = hashlib.sha256(attestation.read_bytes()).hexdigest()
            run["environment_expected"] = {"frozen_commit": PIN, "frozen_tree": None,
                                            "db_sha256": db_hash, "attestation_sha256": attestation_hash,
                                            "mcp_package_version": MCP_PACKAGE_VERSION}
            (packet / "RUN.json").write_text(json.dumps(run), encoding="utf-8")
            doctor = packet / "doctor.json"
            doctor.write_text(json.dumps({"structuredContent": {
                "ok": True,
                "data": {"index": {
                    "db_path": str(database.resolve()),
                    "db_size_bytes": payload["db_size_bytes"],
                    "db_sha256": db_hash,
                }, "mutable": {}, "mcp": {
                    "server_version": MCP_PACKAGE_VERSION,
                    "protocol_revision": MCP_PROTOCOL_REVISION,
                }},
                "warnings": [],
                "meta": {"tool": "doctor", "server_version": MCP_PACKAGE_VERSION,
                         "server_target_revision": MCP_PROTOCOL_REVISION,
                         "protocol_revision": MCP_PROTOCOL_REVISION},
            }}), encoding="utf-8")
            evidence = {"applicability": "verified", "rationale": "fixture-backed environment evidence",
                        "actual_mcp_package_version": MCP_PACKAGE_VERSION, "protocol_revision": MCP_PROTOCOL_REVISION,
                        "frozen_commit": PIN, "db_sha256": db_hash, "attestation_sha256": attestation_hash,
                        "doctor_artifact_path": "doctor.json"}
        row["environment_evidence"] = evidence
        path = packet / "assessment.json"
        path.write_text(json.dumps(row), encoding="utf-8")
        return path, row, packet

    def assessment_record(self, run_id="v1-001", row=None, directory="packet"):
        path, value, _ = self.make_packet_record(run_id, directory, row)
        return path, value

    def write_packet_assessment(self, path, row):
        path.write_text(json.dumps(row), encoding="utf-8")
        return path

    def test_pilot_ui_thread_failure_and_unknown_denominator(self):
        path, row = self.assessment_record()
        row["constraints"][2]["passed"] = False
        row["constraints"][2]["evidence"] = "callback ran on PluginWorker_0"
        row["constraints_handled"] = False
        row["clean_success"] = False
        self.write_packet_assessment(path, row)
        result = summarize([(path, row)], _allow_incomplete_for_tests=True)
        self.assertEqual(result["modes"]["A"]["constraints_handled_rate"], 0.0)
        self.assertIsNone(result["modes"]["A"]["correct_unknown_rate"])
        row["constraints_handled"] = True
        with self.assertRaisesRegex(ValueError, "contradicts"):
            check_assessment(row, path=path)
        row["constraints_handled"] = False
        row["correct_unknown"] = True
        with self.assertRaisesRegex(ValueError, "must be null"):
            check_assessment(row, path=path)

    def test_unknown_only_on_trap_tasks(self):
        for run_id in ("v1-022", "v1-025", "v1-028"):
            with self.subTest(run_id=run_id):
                path, row = self.assessment_record(run_id, directory=f"packet-{run_id}")
                kwargs = {"attestation_key_path": self.key_path} if RUNS[run_id]["mode"] == "C" else {}
                check_assessment(row, path=path, **kwargs)
                row["correct_unknown"] = None
                with self.assertRaisesRegex(ValueError, "must be boolean"):
                    check_assessment(row, path=path, **kwargs)

    def test_compile_separate_from_target_static_and_clean_flags(self):
        path, row = self.assessment_record()
        row["target_static_success"] = False
        row["clean_success"] = False
        self.write_packet_assessment(path, row)
        result = summarize([(path, row)], _allow_incomplete_for_tests=True)
        self.assertEqual(result["modes"]["A"]["python_compile_success_rate"], 1.0)
        self.assertEqual(result["modes"]["A"]["target_static_success_rate"], 0.0)
        row["clean_success"] = True
        with self.assertRaisesRegex(ValueError, "clean_success"):
            check_assessment(row, path=path)
        row["target_static_success"] = True
        row["invented_api"] = True
        with self.assertRaisesRegex(ValueError, "clean_success"):
            check_assessment(row, path=path)

    def test_stale_duplicate_pilot_exclusion(self):
        from aggregate import aggregate
        pilot = ROOT / "run-v1-001" / "assessment.json"
        self.assertTrue(json.loads(pilot.read_text(encoding="utf-8"))["pilot"])
        (self.tmp / "pilot").mkdir()
        (self.tmp / "pilot" / "assessment.json").write_text(pilot.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "incomplete benchmark schedule.*missing run_id"):
            aggregate(self.tmp)
        self.make_packet_record(directory="fresh")
        # A fresh non-pilot does not make an otherwise pilot-only/partial schedule eligible.
        with self.assertRaisesRegex(ValueError, "incomplete benchmark schedule.*missing run_id"):
            aggregate(self.tmp)
        self.make_packet_record(directory="duplicate")
        with self.assertRaisesRegex(ValueError, "duplicate run_id.*v1-001"):
            aggregate(self.tmp)

    def test_verified_pass_requires_command_or_real_artifact(self):
        path, row = self.assessment_record()
        row["reported_tests"] = [{"name": "unit", "status": "pass", "verified": True}]
        with self.assertRaisesRegex(ValueError, "lacks command"):
            check_assessment(row, path=path)
        row["reported_tests"][0]["artifact_path"] = "missing.log"
        with self.assertRaisesRegex(ValueError, "missing"):
            check_assessment(row, path=path)
        proof = path.parent / "proof.log"
        proof.write_text("pytest: 1 passed", encoding="utf-8")
        row["reported_tests"][0]["artifact_path"] = "proof.log"
        check_assessment(row, path=path)
        row["reported_tests"][0]["artifact_path"] = "../proof.log"
        with self.assertRaisesRegex(ValueError, "missing or external"):
            check_assessment(row, path=path)

    def test_result_schema_requires_revision_and_exact_run_identity(self):
        schema = json.loads((BENCH / "result.schema.json").read_text(encoding="utf-8"))
        self.assertIn("allOf", schema["properties"]["tests"]["items"])
        self.assertTrue(schema["additionalProperties"])
        self.assertIn("protocol_revision", schema["required"])
        self.assertEqual(schema["properties"]["protocol_revision"]["const"], "1.1")
        packet = self.tmp / "packet"
        target_dir = prepare("v1-001", packet)
        run = json.loads((packet / "RUN.json").read_text(encoding="utf-8"))
        task = json.loads((packet / "TASK.json").read_text(encoding="utf-8"))
        self.assertEqual(run["protocol_revision"], "1.1")
        self.assertIsNone(run["environment_expected"]["frozen_tree"])
        result = {"benchmark": "exteracontext-agent-v1", "protocol_revision": "1.1",
                  "run_id": run["run_id"], "task_id": task["id"], "mode": run["mode"],
                  "repeat": run["repeat"], "completed": True, "summary": "done",
                  "external_symbols": [], "uncertainties": [], "files_changed": [], "tests": []}
        target = target_dir / "BENCHMARK_RESULT.json"
        target.write_text(json.dumps(result), encoding="utf-8")
        validate_result(target)

        run_path = packet / "RUN.json"
        canonical_run = json.loads(run_path.read_text(encoding="utf-8"))
        run_path.write_text(json.dumps(dict(canonical_run, untrusted_extension=True)), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unsupported extension"):
            validate_result(target)
        run_path.write_text(json.dumps(canonical_run), encoding="utf-8")
        bad_environment = dict(canonical_run)
        bad_environment["environment_expected"] = dict(canonical_run["environment_expected"], untrusted=True)
        run_path.write_text(json.dumps(bad_environment), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "closed protocol schema"):
            validate_result(target)
        bad_environment["environment_expected"] = dict(canonical_run["environment_expected"], db_sha256="a" * 64)
        run_path.write_text(json.dumps(bad_environment), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "A/B RUN.json expected corpus hashes must be null"):
            validate_result(target)
        run_path.write_text(json.dumps(canonical_run), encoding="utf-8")

        bad = dict(result, protocol_revision="1.0")
        target.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "protocol_revision"):
            validate_result(target)
        bad = dict(result, task_id="different-task")
        target.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "task_id"):
            validate_result(target)
        bad = dict(result, mode="B")
        target.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "mode"):
            validate_result(target)
        bad = dict(result, repeat=2)
        target.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "repeat"):
            validate_result(target)
        bad = dict(result, run_id="v1-002")
        target.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "run_id"):
            validate_result(target)

    def test_mode_b_result_packet_requires_exact_frozen_tree(self):
        packet = self.tmp / "mode-b"
        target_dir = self.prepare_packet("v1-002", packet)
        run_path = packet / "RUN.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        task = json.loads((packet / "TASK.json").read_text(encoding="utf-8"))
        result = {"benchmark": "exteracontext-agent-v1", "protocol_revision": "1.1",
                  "run_id": run["run_id"], "task_id": task["id"], "mode": run["mode"],
                  "repeat": run["repeat"], "completed": True, "summary": "done",
                  "external_symbols": [], "uncertainties": [], "files_changed": [], "tests": []}
        result_path = target_dir / "BENCHMARK_RESULT.json"
        result_path.write_text(json.dumps(result), encoding="utf-8")
        validate_result(result_path)

        expected = run["environment_expected"]
        self.assertEqual(expected["frozen_commit"], PIN)
        self.assertRegex(expected["frozen_tree"], r"^[0-9a-f]{40}$")
        for bad_tree in (None, "A" * 40, "a" * 39, "g" * 40):
            bad_run = dict(run)
            bad_run["environment_expected"] = dict(expected, frozen_tree=bad_tree)
            run_path.write_text(json.dumps(bad_run), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Mode B expected frozen_tree"):
                validate_result(result_path)

        bad_run["environment_expected"] = dict(expected)
        bad_run["environment_expected"].pop("frozen_tree")
        run_path.write_text(json.dumps(bad_run), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "closed protocol schema"):
            validate_result(result_path)

    def test_result_schema_requires_pass_evidence(self):
        packet = self.tmp / "packet"
        target_dir = prepare("v1-001", packet)
        run = json.loads((packet / "RUN.json").read_text(encoding="utf-8"))
        task = json.loads((packet / "TASK.json").read_text(encoding="utf-8"))
        result = {"benchmark": "exteracontext-agent-v1", "protocol_revision": "1.1",
                  "run_id": run["run_id"], "task_id": task["id"], "mode": run["mode"],
                  "repeat": run["repeat"], "completed": True, "summary": "done",
                  "external_symbols": [], "uncertainties": [], "files_changed": [], "tests": []}
        target = target_dir / "BENCHMARK_RESULT.json"
        result["tests"] = [{"name": "self report", "status": "pass"}]
        target.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "schema alternatives"):
            validate_result(target)
        result["tests"][0]["artifact_path"] = "nonexistent.log"
        target.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "missing"):
            validate_result(target)
        result["tests"][0]["command"] = "python -m unittest"
        result["tests"][0].pop("artifact_path")
        target.write_text(json.dumps(result), encoding="utf-8")
        validate_result(target)

    def test_result_validator_checks_mode_c_pin_hashes_and_closed_environment_schema(self):
        import hashlib
        from scoring import MCP_PACKAGE_VERSION
        packet = self.tmp / "mode-c"
        target_dir = prepare("v1-003", packet)
        run_path = packet / "RUN.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        self.assertIsNone(run["environment_expected"]["frozen_tree"])
        database = packet / "runtime" / "exteracontext.sqlite"
        database.write_bytes(b"fixture database")
        db_hash = hashlib.sha256(database.read_bytes()).hexdigest()
        attestation = packet / "runtime" / "frozen-corpus-attestation.json"
        attestation.write_text(json.dumps({"payload": {"source_commit": PIN, "db_sha256": db_hash}}), encoding="utf-8")
        attestation_hash = hashlib.sha256(attestation.read_bytes()).hexdigest()
        run["environment_expected"] = {"frozen_commit": PIN, "frozen_tree": None,
                                        "db_sha256": db_hash, "attestation_sha256": attestation_hash,
                                        "mcp_package_version": MCP_PACKAGE_VERSION}
        run_path.write_text(json.dumps(run), encoding="utf-8")
        task = json.loads((packet / "TASK.json").read_text(encoding="utf-8"))
        result = {"benchmark": "exteracontext-agent-v1", "protocol_revision": "1.1",
                  "run_id": run["run_id"], "task_id": task["id"], "mode": run["mode"],
                  "repeat": run["repeat"], "completed": True, "summary": "done",
                  "external_symbols": [], "uncertainties": [], "files_changed": [], "tests": []}
        result_path = target_dir / "BENCHMARK_RESULT.json"
        result_path.write_text(json.dumps(result), encoding="utf-8")
        validate_result(result_path)

        bad = dict(run)
        bad["environment_expected"] = dict(run["environment_expected"], frozen_commit="x" * 40)
        run_path.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "40-character lowercase hex PIN"):
            validate_result(result_path)
        run_path.write_text(json.dumps(run), encoding="utf-8")
        database.write_bytes(b"tampered database")
        with self.assertRaisesRegex(ValueError, "SQLite hash"):
            validate_result(result_path)

    def test_prepare_run_injects_protocol_revision_and_checks_manifest(self):
        packet = self.tmp / "packet"
        target = prepare("v1-001", packet)
        run_path = packet / "RUN.json"
        task_path = packet / "TASK.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        task = json.loads(task_path.read_text(encoding="utf-8"))
        self.assertEqual(run["protocol_revision"], "1.1")
        instructions = (target / "BENCHMARK_TASK.md").read_text(encoding="utf-8")
        self.assertIn("protocol_revision 1.1", instructions)
        self.assertIn("protocol_revision", (target / "result.schema.json").read_text(encoding="utf-8"))
        result = {"benchmark": "exteracontext-agent-v1", "protocol_revision": "1.1",
                  "run_id": run["run_id"], "task_id": task["id"], "mode": run["mode"],
                  "repeat": run["repeat"], "completed": True, "summary": "done",
                  "external_symbols": [], "uncertainties": [], "files_changed": [], "tests": []}
        result_path = target / "BENCHMARK_RESULT.json"
        result_path.write_text(json.dumps(result), encoding="utf-8")
        validate_result(result_path)

        tampered_run = dict(run, mode="B")
        run_path.write_text(json.dumps(tampered_run), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "RUN.json"):
            validate_result(result_path)
        run_path.write_text(json.dumps(run), encoding="utf-8")
        tampered_task = dict(task, prompt=task["prompt"] + " changed")
        task_path.write_text(json.dumps(tampered_task), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "TASK.json"):
            validate_result(result_path)

    def test_prepare_c_requires_attestation_not_deployment_stamp(self):
        source = self.tmp / "source"
        source.mkdir()
        db = source / "base.sqlite"
        with closing(sqlite3.connect(db)) as conn:
            conn.execute("CREATE TABLE example (id INTEGER)")
        (source / ".knowledge-ref").write_text(PIN + "\n", encoding="utf-8")
        (source / ".knowledge-manifest.json").write_text(json.dumps({"commit_verified": False}), encoding="utf-8")
        first = self.tmp / "v1-003"
        prepare("v1-003", first, frozen_db=db)
        setup = (first / "RESOURCE_SETUP.md").read_text(encoding="utf-8")
        self.assertIn("NOT ELIGIBLE", setup)
        self.assertFalse((first / "runtime" / "exteracontext.sqlite").exists())
        self.assertFalse((first / "runtime" / ".knowledge-ref").exists())
        env = json.loads((first / "MCP_BENCHMARK_ENV.json").read_text(encoding="utf-8"))
        self.assertEqual(env["EXTERACONTEXT_KNOWLEDGE_REF"], "")

    def test_frozen_corpus_verifier_rejects_forgery_and_unverified_deployment_manifest(self):
        (self.tmp / "not-an-attestation.json").write_text(json.dumps({"commit_verified": False}), encoding="utf-8")
        key = self.tmp / "key"
        key.write_bytes(b"K" * 32)
        with self.assertRaisesRegex(ValueError, "invalid frozen corpus attestation"):
            frozen.verify_attestation(self.tmp / "not-an-attestation.json", key, self.tmp, self.tmp / "absent.sqlite")
        with self.assertRaisesRegex(ValueError, "not verifiable"):
            frozen.inspect_commit(self.tmp, PIN)

    def test_signed_corpus_attestation_binds_local_commit_and_sqlite_bytes(self):
        from unittest import mock
        repo = self.tmp / "knowledge"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
        (repo / "source.txt").write_text("local fixture", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "source.txt"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture"], check=True, capture_output=True)
        commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        db = self.tmp / "frozen.sqlite"
        with closing(sqlite3.connect(db)) as conn:
            conn.executescript("""
                CREATE TABLE docs(path TEXT,title TEXT,kind TEXT,content TEXT);
                CREATE TABLE facts(id TEXT,topic TEXT,claim TEXT,api TEXT,evidence_url TEXT,evidence_path TEXT,version TEXT,status TEXT,recipe TEXT,source_id TEXT,source_fact_id TEXT,original_status TEXT,canonical_topic TEXT,review_status TEXT,platform TEXT);
                CREATE TABLE meta(key TEXT,value TEXT);
                CREATE TABLE source_runs(call_id TEXT,source_id TEXT,role TEXT);
                CREATE TABLE source_provenance(source_id TEXT,collector_runs TEXT,reviewer_runs TEXT,has_collector INTEGER,has_reviewer INTEGER);
                CREATE VIRTUAL TABLE docs_fts USING fts5(path,title,content);
                CREATE VIRTUAL TABLE facts_fts USING fts5(id,topic,claim,api);
                INSERT INTO docs VALUES('doc.md','doc','api','text');
                INSERT INTO facts VALUES('api:x','topic','claim','x','','','','code','','','','','','','android');
            """)
        key = self.tmp / "trusted.key"
        key.write_bytes(b"K" * 32)
        with mock.patch.object(frozen, "PIN", commit):
            candidate = frozen.build_candidate(repo, db, commit)
            self.assertFalse(candidate["eligible"])
            self.assertIn("operator_attestation_required", candidate)
            confirm = f"ATTEST {commit} {candidate['db_sha256']}"
            with self.assertRaisesRegex(ValueError, "confirmation"):
                frozen.seal_candidate(candidate, key.read_bytes(), "operator", "wrong")
            sealed = frozen.seal_candidate(candidate, key.read_bytes(), "authorized", confirm)
            attestation = self.tmp / "attestation.json"
            attestation.write_text(json.dumps(sealed), encoding="utf-8")
            verified = frozen.verify_attestation(attestation, key, repo, db, commit)
            self.assertEqual(verified["source_tree"], candidate["source_tree"])
            with mock.patch("prepare_run.PIN", commit):
                packet = self.tmp / "valid-mode-c"
                prepare("v1-003", packet, frozen_db=db, frozen_attestation=attestation,
                        attestation_key=key, frozen_repo=repo)
                self.assertIn("verified/copied", (packet / "RESOURCE_SETUP.md").read_text(encoding="utf-8"))
                self.assertTrue((packet / "runtime" / "exteracontext.sqlite").is_file())
            forged = json.loads(attestation.read_text(encoding="utf-8"))
            forged["payload"]["source_commit"] = "0" * 40
            bad = self.tmp / "forged.json"
            bad.write_text(json.dumps(forged), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source commit|signature"):
                frozen.verify_attestation(bad, key, repo, db, commit)
            wrong_key = self.tmp / "wrong.key"
            wrong_key.write_bytes(b"Z" * 32)
            with self.assertRaisesRegex(ValueError, "signature"):
                frozen.verify_attestation(attestation, wrong_key, repo, db, commit)
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("INSERT INTO meta VALUES('drift','1')")
                conn.commit()
            with self.assertRaisesRegex(ValueError, "mismatch"):
                frozen.verify_attestation(attestation, key, repo, db, commit)

    def test_builder_rejects_symlink_database_and_hmac_key(self):
        db_target = self.tmp / "real.sqlite"
        with closing(sqlite3.connect(db_target)) as conn:
            conn.execute("CREATE TABLE marker (id INTEGER)")
        db_link = self.tmp / "linked.sqlite"
        make_symlink_or_skip(self, db_target, db_link)
        with self.assertRaisesRegex(ValueError, "database must not be a symlink"):
            frozen.inspect_database(db_link)

        key_target = self.tmp / "real.key"
        key_target.write_bytes(b"K" * 32)
        key_link = self.tmp / "linked.key"
        make_symlink_or_skip(self, key_target, key_link)
        with self.assertRaisesRegex(ValueError, "HMAC key must not be a symlink"):
            frozen._key_bytes(key_link)

    def test_builder_rejects_symlink_repository(self):
        repo_target = self.tmp / "real-repo"
        repo_target.mkdir()
        repo_link = self.tmp / "linked-repo"
        make_symlink_or_skip(self, repo_target, repo_link)
        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            frozen.inspect_commit(repo_link, PIN)

    def test_prepare_b_no_private_material(self):
        target = self.prepare_packet("v1-002", self.tmp / "b-verified")
        run = json.loads((target.parent / "RUN.json").read_text(encoding="utf-8"))
        expected = json.loads((target.parent / "knowledge-checkout.expected.json").read_text(encoding="utf-8"))
        self.assertEqual(run["environment_expected"]["frozen_tree"], expected["tree"])
        self.assertEqual(expected["commit"], PIN)
        self.assertIn(expected["tree"], (target.parent / "RESOURCE_SETUP.md").read_text(encoding="utf-8"))
        self.assertFalse(any(p.name in {"ground-truth.json", "assessment.schema.json", "EVALUATOR.md"} for p in target.parent.rglob("*")))
        self.assertFalse((target.parent / "MCP_BENCHMARK_ENV.json").exists())

        unverified_target = prepare("v1-002", self.tmp / "b-unverified")
        self.assertIn("NOT READY", (unverified_target.parent / "RESOURCE_SETUP.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
