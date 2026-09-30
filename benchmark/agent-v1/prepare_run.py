#!/usr/bin/env python3
"""Create a run packet; never fetch knowledge or launch the device/agent."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

from build_frozen_corpus import PIN, verify_attestation

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixture-template"
PROTOCOL_REVISION = "1.1"


def prepare(run_id: str, out: Path, *, frozen_checkout: Path | None = None,
            frozen_db: Path | None = None, frozen_attestation: Path | None = None,
            attestation_key: Path | None = None, frozen_repo: Path | None = None) -> Path:
    manifest = json.loads((ROOT / "run-manifest.json").read_text(encoding="utf-8"))
    tasks = json.loads((ROOT / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    if manifest.get("benchmark") != "exteracontext-agent-v1":
        raise ValueError("run manifest benchmark identifier is invalid")
    runs = manifest.get("runs")
    if not isinstance(runs, list) or any(not isinstance(r, dict) for r in runs):
        raise ValueError("run manifest runs must be an array of objects")
    if len({r.get("run_id") for r in runs}) != len(runs):
        raise ValueError("run manifest contains duplicate run ids")
    run = next((r.copy() for r in runs if r.get("run_id") == run_id), None)
    if run is None:
        raise ValueError(f"unknown run id: {run_id}")
    declared_revision = manifest.get("protocol_revision", PROTOCOL_REVISION)
    if str(declared_revision) != PROTOCOL_REVISION:
        raise ValueError(f"unsupported run manifest protocol revision: {declared_revision!r}")
    if run.get("protocol_revision", PROTOCOL_REVISION) != PROTOCOL_REVISION:
        raise ValueError(f"run {run_id} protocol revision is not {PROTOCOL_REVISION}")
    run["protocol_revision"] = PROTOCOL_REVISION
    task = next((t for t in tasks if isinstance(t, dict) and t.get("id") == run.get("task_id")), None)
    if task is None:
        raise ValueError(f"run {run_id} references unknown task: {run.get('task_id')!r}")
    if run.get("mode") not in {"A", "B", "C"} or not isinstance(run.get("repeat"), int) or run["repeat"] < 1:
        raise ValueError(f"run {run_id} has invalid mode/repeat")
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"refusing to overwrite nonempty run directory: {out}")
    checkout_details = None
    if frozen_checkout is not None:
        def git_output(*args: str) -> str:
            return subprocess.run(["git", "--no-optional-locks", "--no-replace-objects", "-C", str(frozen_checkout), *args],
                                  check=True, text=True, capture_output=True, encoding="utf-8", timeout=15).stdout.strip()
        head = git_output("rev-parse", "HEAD")
        tree = git_output("rev-parse", "HEAD^{tree}")
        status = git_output("status", "--porcelain", "--untracked-files=all", "--ignored=matching")
        if head != PIN:
            raise ValueError(f"Knowledge checkout must be at {PIN}; got {head}")
        if len(tree) != 40 or any(c not in "0123456789abcdef" for c in tree):
            raise ValueError("Knowledge checkout returned invalid tree hash")
        if status:
            raise ValueError("Knowledge checkout must be clean, including ignored files (git status --porcelain is nonempty)")
        checkout_details = {"frozen_commit": head, "frozen_tree": tree}
    verified_corpus = None
    verified_attestation_sha256 = None
    if run["mode"] == "C" and any(x is not None for x in (frozen_attestation, attestation_key, frozen_repo)):
        if not all(x is not None for x in (frozen_db, frozen_attestation, attestation_key, frozen_repo)):
            raise ValueError("Mode C provenance requires --frozen-db, --frozen-attestation, --attestation-key, and --frozen-repo together")
        attestation_bytes = frozen_attestation.read_bytes()
        verified_corpus = verify_attestation(frozen_attestation, attestation_key, frozen_repo, frozen_db, PIN)
        if frozen_attestation.read_bytes() != attestation_bytes:
            raise ValueError("attestation changed while it was being verified")
        verified_attestation_sha256 = hashlib.sha256(attestation_bytes).hexdigest()
    if run["mode"] == "B" and frozen_db is not None:
        raise ValueError("B must use raw frozen checkout, not SQLite")
    if run["mode"] != "C" and any(x is not None for x in (frozen_attestation, attestation_key, frozen_repo)):
        raise ValueError("frozen corpus attestation arguments are valid only for Mode C")
    if run["mode"] == "C" and frozen_checkout is not None:
        raise ValueError("C must not expose raw Knowledge checkout")
    if run["mode"] == "A" and (frozen_checkout or frozen_db):
        raise ValueError("A must not expose Knowledge resources")
    package = json.loads((ROOT.parents[1] / "mcp" / "package.json").read_text(encoding="utf-8"))
    expected_environment = {
        "frozen_commit": PIN,
        "frozen_tree": checkout_details["frozen_tree"] if run["mode"] == "B" and checkout_details is not None else None,
        "db_sha256": verified_corpus["db_sha256"] if verified_corpus is not None else None,
        "attestation_sha256": verified_attestation_sha256,
        "mcp_package_version": package["version"],
    }
    run["environment_expected"] = expected_environment
    out.mkdir(parents=True, exist_ok=True)
    target = out / "target"
    shutil.copytree(FIXTURE, target)
    (out / "RUN.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if run["mode"] == "B" and checkout_details is not None:
        (out / "knowledge-checkout.expected.json").write_text(json.dumps({
            "schema_version": 1,
            "commit": checkout_details["frozen_commit"],
            "tree": checkout_details["frozen_tree"],
        }, indent=2) + "\n", encoding="utf-8")
    (out / "TASK.json").write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (target / "BENCHMARK_MODE.md").write_text((ROOT / "prompts" / f"MODE_{run['mode']}.md").read_text(encoding="utf-8"), encoding="utf-8")
    shutil.copy2(ROOT / "result.schema.json", target / "result.schema.json")
    (target / "BENCHMARK_TASK.md").write_text(
        f"# Benchmark task {task['id']}\n\nTarget:\n\n```json\n{json.dumps(task['target'], ensure_ascii=False, indent=2)}\n```\n\n"
        f"Task:\n\n{task['prompt']}\n\nRun: {run_id}, mode {run['mode']}, repeat {run['repeat']}; protocol_revision {PROTOCOL_REVISION}.\n\n"
        "Work only inside this target project unless BENCHMARK_MODE.md explicitly permits an external resource.\n"
        "Create BENCHMARK_RESULT.json matching result.schema.json. Include the exact run_id, task_id, mode, repeat, and protocol_revision from this packet.\n", encoding="utf-8")
    setup = [f"# Resource setup for {run_id}", "", f"Protocol 1.1; frozen Knowledge commit: `{PIN}`.", ""]
    if run["mode"] == "B":
        setup += ["Expose ONLY a read-only raw Knowledge checkout at the exact pinned commit.",
                  "Do not mount the MCP, SQLite databases or other benchmark runs."]
        if checkout_details is None:
            setup += ["NOT READY: supply --frozen-checkout at the exact PIN; this packet cannot be scored or aggregated until verified checkout expectations and a packet-local artifact exist."]
        else:
            setup += [f"Verified checkout: `{frozen_checkout.resolve()}`",
                      f"Expected commit: `{checkout_details['frozen_commit']}`; expected tree: `{checkout_details['frozen_tree']}`; checkout was clean when this packet was prepared.",
                      "Before evaluation, place a packet-local `knowledge-checkout.json` artifact in this packet with exactly: `schema_version` (1), `commands` (the three strings `git rev-parse HEAD`, `git rev-parse HEAD^{tree}`, `git status --porcelain --untracked-files=all --ignored=matching` in that order), `head`, `tree`, and `status_porcelain` (empty string for clean). Ignored files are reported and rejected too; the artifact must report the exact expected commit/tree and an entirely empty status.",
                      "This artifact is operator-supplied evidence, not cryptographic proof; trust it only when produced/collected by an independently trusted harness. The evaluator rejects missing, malformed, mismatched, dirty, or out-of-packet artifacts."]
    elif run["mode"] == "C":
        runtime = out / "runtime"
        runtime.mkdir()
        (runtime / "knowledge-runs").mkdir()
        # New run-local mutable DB; no copy of prior write-back state or other run's data.
        with closing(sqlite3.connect(runtime / "knowledge.sqlite")) as con:
            con.execute("PRAGMA journal_mode=DELETE")
        if verified_corpus is not None:
            copied_db = runtime / "exteracontext.sqlite"
            shutil.copy2(frozen_db, copied_db)
            # Verify copied bytes again to close the source-change/copy race; retain signed provenance.
            verify_attestation(frozen_attestation, attestation_key, frozen_repo, copied_db, PIN)
            copied_attestation = runtime / "frozen-corpus-attestation.json"
            shutil.copy2(frozen_attestation, copied_attestation)
            if hashlib.sha256(copied_attestation.read_bytes()).hexdigest() != verified_attestation_sha256:
                raise ValueError("attestation changed after verification or during packet copy")
        env = {"EXTERACONTEXT_DB": str((runtime / "exteracontext.sqlite").resolve()),
               "EXTERACONTEXT_KNOWLEDGE_DB": str((runtime / "knowledge.sqlite").resolve()),
               "EXTERACONTEXT_RUN_ROOT": str((runtime / "knowledge-runs").resolve()),
               "EXTERACONTEXT_KNOWLEDGE_REF": PIN if verified_corpus is not None else "",
               "EXTERACONTEXT_AUTO_SYNC": "0"}
        (out / "MCP_BENCHMARK_ENV.json").write_text(json.dumps(env, indent=2) + "\n", encoding="utf-8")
        setup += ["Expose ONLY the MCP (not raw Knowledge or other runs). Inject every variable from MCP_BENCHMARK_ENV.json into the MCP server process.",
                  "No network or automatic syncing. A .knowledge-ref or deployment .knowledge-manifest.json is not benchmark provenance and is never accepted as proof.",
                  "Every run uses independent mutable knowledge.sqlite and knowledge-runs/. Do not point MCP at profile-wide mutable databases.",
                  "Base DB verified/copied from a signed operator attestation bound to the exact frozen Git commit, DB SHA-256, schema fingerprint, and counts." if verified_corpus else
                  "NOT READY / NOT ELIGIBLE: no authorized signed corpus attestation was supplied. The local deployment DB manifest has commit_verified=false and is not evidence of the frozen source relation. Build a local candidate, obtain an authorized operator attestation with an out-of-band key, then rerun with --frozen-db, --frozen-attestation, --attestation-key, and --frozen-repo."]
    else:
        setup += ["Closed-book baseline: expose neither Knowledge nor ExteraContext MCP."]
    (out / "RESOURCE_SETUP.md").write_text("\n".join(setup) + "\n", encoding="utf-8")
    (out / "OPEN_IN_DSH.md").write_text(
        f"# Launch {run_id}\n\nAfter satisfying RESOURCE_SETUP.md, open `{target.resolve()}` as a new clean DSH session.\n"
        "Ask: Read BENCHMARK_MODE.md and BENCHMARK_TASK.md, perform the task, and create BENCHMARK_RESULT.json.\n"
        "Never expose ground truth, evaluator assessments, or previous runs. Device execution remains manual.\n", encoding="utf-8")
    private = {"ground-truth.json", "assessment.schema.json", "EVALUATOR.md"}
    if any(p.name in private for p in out.rglob("*")):
        raise AssertionError("private evaluator file leaked")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("output", type=Path)
    parser.add_argument("--frozen-checkout", type=Path, help="local checkout for mode B; verify exact HEAD")
    parser.add_argument("--frozen-db", type=Path, help="local Mode C SQLite; requires a signed corpus attestation")
    parser.add_argument("--frozen-attestation", type=Path, help="operator-signed JSON from build_frozen_corpus.py")
    parser.add_argument("--attestation-key", type=Path, help="out-of-band trusted 32+ random byte HMAC key")
    parser.add_argument("--frozen-repo", type=Path, help="local Git object database used to verify the frozen commit; never exposed to Mode C")
    args = parser.parse_args()
    try:
        print(prepare(args.run_id, args.output, frozen_checkout=args.frozen_checkout, frozen_db=args.frozen_db,
                      frozen_attestation=args.frozen_attestation, attestation_key=args.attestation_key,
                      frozen_repo=args.frozen_repo))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
