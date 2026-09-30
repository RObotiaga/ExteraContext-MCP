#!/usr/bin/env python3
"""Security regressions for the CLI boundary used by the MCP server."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "scripts" / "orchestrate.py"


class OrchestrationSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.runs = self.base / "runs"
        self.env = os.environ.copy()
        self.env["EXTERACONTEXT_KNOWLEDGE_DB"] = str(self.base / "knowledge.sqlite")
        self.env["PYTHONIOENCODING"] = "utf-8"
        self.env.pop("EXTERACONTEXT_LITERAL_INPUTS", None)
        self.secret = "LOCAL_SECRET_ONLY_7f9c02b5"
        self.secret_file = self.base / "secret.txt"
        self.secret_file.write_text(self.secret, encoding="utf-8")
        self.evidence = [{"evidence_id": "ev1", "evidence_status": "code", "excerpt": "benign evidence"}]

    def call(self, command, *, literal=True, success=True):
        argv = [sys.executable, str(ORCH), "--run-root", str(self.runs)]
        if literal:
            argv.append("--literal-inputs")
        cp = subprocess.run(argv + command, cwd=ROOT, env=self.env, text=True,
                            encoding="utf-8", capture_output=True)
        if success:
            self.assertEqual(cp.returncode, 0, cp.stderr)
            return json.loads(cp.stdout)
        self.assertNotEqual(cp.returncode, 0, cp.stdout)
        self.assertNotIn(self.secret, cp.stdout + cp.stderr)
        return cp

    def reflect(self, task="harmless task"):
        return self.call(["reflect", "--task", task, "--evidence", json.dumps(self.evidence)])

    def test_untrusted_task_at_path_is_literal_not_read_or_disclosed(self):
        marker = "@" + str(self.secret_file)
        created = self.reflect(marker)
        oid = created["orchestration_id"]
        state = json.loads((self.runs / oid / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["task"], marker)
        self.assertNotIn(self.secret, json.dumps(state) + json.dumps(created))
        status = self.call(["status", "--id", oid])
        self.assertEqual(set(status), {"orchestration_id", "stage"})
        self.assertNotIn(self.secret, json.dumps(status))
        prompt = self.call_prompt(oid, "collector")
        self.assertIn(marker, prompt)
        self.assertNotIn(self.secret, prompt)

    def call_prompt(self, oid, phase):
        cp = subprocess.run([sys.executable, str(ORCH), "--run-root", str(self.runs),
                             "--literal-inputs", "prompt", "--id", oid, "--phase", phase],
                            cwd=ROOT, env=self.env, text=True, encoding="utf-8", capture_output=True)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        return cp.stdout

    def test_json_and_runtime_actor_at_paths_are_not_dereferenced(self):
        json_file = self.base / "readable.json"
        json_file.write_text(json.dumps({"child_id": self.secret, "client": self.secret}), encoding="utf-8")
        marker = "@" + str(json_file)
        for flag in ("--evidence", "--target"):
            command = ["reflect", "--task", "safe", "--evidence", json.dumps(self.evidence)]
            if flag == "--evidence":
                command[-1] = marker
            else:
                command += ["--target", marker]
            self.call(command, success=False)
        created = self.reflect()
        oid, token = created["orchestration_id"], created["collector_token"]
        payload = {"action": "skip", "claim": "", "kind": "other", "scope": "project",
                   "target": {}, "evidence_status": "code", "evidence_refs": [],
                   "existing_claim": None, "conflicts_with": []}
        prefix = ["collector-result", "--id", oid, "--actor-token", token]
        self.call(prefix + ["--result", marker], success=False)
        self.call(prefix + ["--result", json.dumps(payload), "--runtime-actor", marker], success=False)
        self.assertEqual(self.call(["status", "--id", oid])["stage"], "collector-ready")

        # The same rule applies to both verifier JSON result parameters and their metadata.
        proposed = dict(payload, action="propose", claim="a test claim", evidence_refs=["ev1"])
        ingested = self.call(prefix + ["--result", json.dumps(proposed)])
        verifier = ingested["verifier_token"]
        phase_a_prefix = ["phase-a-result", "--id", oid, "--actor-token", verifier]
        self.call(phase_a_prefix + ["--result", marker], success=False)
        phase_a = {"statement": "benign evidence exists", "scope": {},
                   "evidence_status": "code", "uncertainty": None}
        self.call(phase_a_prefix + ["--result", json.dumps(phase_a), "--runtime-actor", marker], success=False)
        self.assertEqual(self.call(["status", "--id", oid])["stage"], "phase-a-ready")
        self.call(phase_a_prefix + ["--result", json.dumps(phase_a)])
        phase_b_prefix = ["phase-b-result", "--id", oid, "--actor-token", verifier]
        self.call(phase_b_prefix + ["--result", marker], success=False)
        phase_b = {"verdict": "reject", "final_statement": None, "existing_subject_type": None,
                   "existing_subject_id": None, "notes": None}
        self.call(phase_b_prefix + ["--result", json.dumps(phase_b), "--runtime-actor", marker], success=False)
        self.assertEqual(self.call(["status", "--id", oid])["stage"], "phase-b-ready")

    def test_id_traversal_and_symlink_escape_rejected(self):
        outside = self.base / "outside-12345678"
        outside.mkdir()
        (outside / "state.json").write_text(json.dumps({"secret": self.secret}), encoding="utf-8")
        for oid in ("../outside-12345678", "..\\outside-12345678", str(outside),
                    "../../outside-12345678", ".-12345678"):
            self.call(["status", "--id", oid], success=False)
            self.call(["prompt", "--id", oid, "--phase", "collector"], success=False)
        self.runs.mkdir()
        try:
            (self.runs / "escape-12345678").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pass  # Windows may require privileges for symlinks.
        else:
            self.call(["status", "--id", "escape-12345678"], success=False)

    def test_phase_a_ready_status_cannot_reveal_collector_candidate(self):
        created = self.reflect()
        oid = created["orchestration_id"]
        claim = "PRIVATE_COLLECTOR_CANDIDATE_5034"
        payload = {"action": "propose", "claim": claim, "kind": "other", "scope": "project",
                   "target": {}, "evidence_status": "code", "evidence_refs": ["ev1"],
                   "existing_claim": None, "conflicts_with": []}
        ingested = self.call(["collector-result", "--id", oid, "--actor-token", created["collector_token"],
                              "--result", json.dumps(payload)])
        self.assertEqual(ingested["stage"], "phase-a-ready")
        self.assertIn("claim_id", ingested)  # Capability holder's transition response is not public status.
        status = self.call(["status", "--id", oid])
        self.assertEqual(set(status), {"orchestration_id", "stage"})
        self.assertNotIn(claim, json.dumps(status))
        self.assertNotIn(ingested["claim_id"], json.dumps(status))
        self.assertNotIn(str(self.runs), json.dumps(status))
        self.assertNotIn(claim, self.call_prompt(oid, "phase-a"))
        phase_a = {"statement": "benign evidence exists", "scope": {},
                   "evidence_status": "code", "uncertainty": None}
        self.call(["phase-a-result", "--id", oid, "--actor-token", ingested["verifier_token"],
                   "--result", json.dumps(phase_a)])
        after = self.call(["status", "--id", oid])
        self.assertEqual(after["claim_id"], ingested["claim_id"])
        self.assertNotIn(str(self.runs), json.dumps(after))

    def test_trusted_cli_file_arguments_remain_explicitly_available(self):
        evidence_file = self.base / "evidence.json"
        evidence_file.write_text(json.dumps(self.evidence), encoding="utf-8")
        task_file = self.base / "task.txt"
        task_file.write_text("trusted local task", encoding="utf-8")
        created = self.call(["reflect", "--task", "@" + str(task_file),
                             "--evidence", "@" + str(evidence_file)], literal=False)
        state = json.loads((self.runs / created["orchestration_id"] / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["task"], "trusted local task")
        self.assertEqual(state["evidence"][0]["evidence_id"], "ev1")


if __name__ == "__main__":
    unittest.main()
