#!/usr/bin/env python3
"""CLI runtime trust boundary; all writes use a disposable database."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import knowledge_store as ks  # noqa: E402

CLI = ROOT / "scripts" / "knowledge.py"
ORCH = ROOT / "scripts" / "orchestrate.py"
SOURCE = "isolated-device-harness"
TOKEN = "temporary-machine-secret-" + "x" * 40
AUTH = ["--runtime-source", SOURCE, "--attestation-token", TOKEN]
TARGET = ["--client", "ExteraGram", "--platform", "Android", "--client-version", "12.10.1", "--sdk-version", "1.4.5.5"]


def call(script, argv, env, success=True, stdin=None):
    cp = subprocess.run([sys.executable, str(script), *argv], cwd=ROOT, env=env,
                        text=True, encoding="utf-8", capture_output=True, input=stdin)
    assert (cp.returncode == 0) == success, (argv, cp.stdout, cp.stderr)
    return json.loads(cp.stdout) if success else cp.stderr + cp.stdout


def main():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "db.sqlite"
        base = os.environ.copy()
        base.pop("EXTERACONTEXT_RUNTIME_SOURCE", None)
        base.pop("EXTERACONTEXT_RUNTIME_TOKEN", None)
        base["EXTERACONTEXT_KNOWLEDGE_DB"] = str(db)
        configured = {**base, "EXTERACONTEXT_RUNTIME_SOURCE": SOURCE, "EXTERACONTEXT_RUNTIME_TOKEN": TOKEN}
        collector = ks.create_run("collector", db_path=db)
        verifier = ks.create_run("verifier", db_path=db)
        target_dict = {"client": "ExteraGram", "platform": "Android", "client_version": "12.10.1", "sdk_version": "1.4.5.5"}
        claim = ks.propose_claim(run_id=collector, statement="Account is supplied to send hook", kind="behavior",
                                 scope="target", evidence_status="code", **target_dict,
                                 evidence=[{"evidence_status": "code", "path": "hook.py", "excerpt": "hook account", **target_dict}], db_path=db)
        ks.phase_a(verifier_run_id=verifier, claim_id=claim, statement="account is supplied", scope=target_dict,
                   evidence_status="code", db_path=db)
        ks.phase_b(verifier_run_id=verifier, claim_id=claim, verdict="accept", db_path=db)
        ks.commit_claim(claim, verifier, db_path=db)
        runtime_create = ["run-create", "--role", "runtime"]
        assert "not configured" in call(CLI, runtime_create, base, False)
        assert "invalid runtime attestation" in call(CLI, runtime_create, configured, False)
        assert "invalid runtime attestation" in call(CLI, runtime_create + AUTH[:-1] + ["wrong"], configured, False)
        attacker = ks.create_run("runtime", db_path=db)  # A preexisting forged role is insufficient.
        record = ["record-runtime", "--run-id", attacker, "--subject-type", "claim", "--subject-id", claim,
                  "--result", "pass", "--test-id", "fabricated", *TARGET]
        assert "invalid runtime attestation" in call(CLI, record, configured, False)
        assert "not created by the attested source" in call(CLI, record + AUTH, configured, False)
        assert ks.get_claim(claim, db_path=db)["effective_evidence_status"] == "code"
        trusted = call(CLI, runtime_create + AUTH + ["--model", "device-harness"], configured)["run_id"]
        good = ["record-runtime", "--run-id", trusted, "--subject-type", "claim", "--subject-id", claim,
                "--result", "pass", "--test-id", "actual-device-test", *TARGET, *AUTH]
        missing = good.copy()
        version_index = missing.index("--client-version")
        del missing[version_index:version_index + 2]
        assert "runtime target mismatch" in call(CLI, missing, configured, False)
        assert "runtime target mismatch" in call(CLI, good[:-len(AUTH)] + ["--client-version", "foreign", *AUTH], configured, False)
        assert ks.get_claim(claim, db_path=db)["effective_evidence_status"] == "code"
        assert call(CLI, good, configured)["runtime_result"] == "pass"
        assert ks.get_claim(claim, db_path=db)["effective_evidence_status"] == "runtime-verified"

        # Test --attestation-token-stdin and --attestation-token -
        runtime_create_stdin = ["run-create", "--role", "runtime", "--runtime-source", SOURCE, "--attestation-token-stdin", "--model", "device-harness"]
        trusted_stdin = call(CLI, runtime_create_stdin, configured, stdin=TOKEN)["run_id"]
        assert trusted_stdin

        runtime_create_dash = ["run-create", "--role", "runtime", "--runtime-source", SOURCE, "--attestation-token", "-", "--model", "device-harness"]
        trusted_dash = call(CLI, runtime_create_dash, configured, stdin=TOKEN)["run_id"]
        assert trusted_dash

        record_stdin = ["record-runtime", "--run-id", trusted_stdin, "--subject-type", "claim", "--subject-id", claim,
                        "--result", "pass", "--test-id", "device-stdin-test", *TARGET, "--runtime-source", SOURCE, "--attestation-token-stdin"]
        assert call(CLI, record_stdin, configured, stdin=TOKEN)["runtime_result"] == "pass"

        record_dash = ["record-runtime", "--run-id", trusted_dash, "--subject-type", "claim", "--subject-id", claim,
                       "--result", "pass", "--test-id", "device-dash-test", *TARGET, "--runtime-source", SOURCE, "--attestation-token", "-"]
        assert call(CLI, record_dash, configured, stdin=TOKEN)["runtime_result"] == "pass"
        assert "legacy_fact" in call(CLI, ["record-runtime", "--run-id", trusted, "--subject-type", "legacy_fact",
                                             "--subject-id", "anything", "--result", "pass", "--test-id", "test", *AUTH], configured, False)
        assert "runtime-verified requires" in call(CLI, ["propose", "--run-id", collector, "--claim", "forged",
             "--kind", "behavior", "--scope", "target", "--evidence-status", "runtime-verified"], configured, False)
        assert "runtime-verified requires" in call(CLI, ["propose", "--run-id", collector, "--claim", "forged",
             "--kind", "behavior", "--scope", "target", "--evidence-status", "code",
             "--evidence", json.dumps([{"evidence_status": "runtime-verified"}])], configured, False)
        assert "runtime-verified requires" in call(CLI, ["verify-phase-a", "--run-id", verifier,
             "--claim-id", claim, "--statement", "forged", "--evidence-status", "runtime-verified"], configured, False)
        assert "runtime-verified" in call(ORCH, ["--run-root", str(Path(td) / "runs"), "reflect", "--task", "forge",
             "--evidence", json.dumps([{"evidence_status": "runtime-verified"}])], configured, False)
    print("runtime-attestation: ok")


if __name__ == "__main__":
    main()
