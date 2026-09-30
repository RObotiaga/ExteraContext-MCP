#!/usr/bin/env python3
"""Create a run packet; never fetch knowledge or launch the device/agent."""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixture-template"
PIN = "70f6f614227c8b02d241e7b1e72a0b6691442fd1"


def prepare(run_id: str, out: Path, *, frozen_checkout: Path | None = None,
            frozen_db: Path | None = None) -> Path:
    manifest = json.loads((ROOT / "run-manifest.json").read_text(encoding="utf-8"))
    tasks = json.loads((ROOT / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    run = next((r for r in manifest["runs"] if r["run_id"] == run_id), None)
    if run is None:
        raise ValueError(f"unknown run id: {run_id}")
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"refusing to overwrite nonempty run directory: {out}")
    if frozen_checkout is not None:
        head = subprocess.run(["git", "-C", str(frozen_checkout), "rev-parse", "HEAD"],
                              check=True, text=True, capture_output=True).stdout.strip()
        if head != PIN:
            raise ValueError(f"Knowledge checkout must be at {PIN}; got {head}")
    if frozen_db is not None:
        if not frozen_db.is_file() or not frozen_db.with_name(".knowledge-ref").is_file():
            raise ValueError("frozen SQLite database and adjacent .knowledge-ref are required")
        if frozen_db.with_name(".knowledge-ref").read_text(encoding="utf-8").strip() != PIN:
            raise ValueError("frozen SQLite database stamp does not match Knowledge pin")
        with closing(sqlite3.connect(f"file:{frozen_db.resolve().as_posix()}?mode=ro", uri=True)) as con:
            if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("frozen SQLite database failed integrity check")
    if run["mode"] == "B" and frozen_db is not None:
        raise ValueError("B must use raw frozen checkout, not SQLite")
    if run["mode"] == "C" and frozen_checkout is not None:
        raise ValueError("C must not expose raw Knowledge checkout")
    if run["mode"] == "A" and (frozen_checkout or frozen_db):
        raise ValueError("A must not expose Knowledge resources")
    task = next(t for t in tasks if t["id"] == run["task_id"])
    out.mkdir(parents=True, exist_ok=True)
    target = out / "target"
    shutil.copytree(FIXTURE, target)
    (out / "RUN.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "TASK.json").write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (target / "BENCHMARK_MODE.md").write_text((ROOT / "prompts" / f"MODE_{run['mode']}.md").read_text(encoding="utf-8"), encoding="utf-8")
    shutil.copy2(ROOT / "result.schema.json", target / "result.schema.json")
    (target / "BENCHMARK_TASK.md").write_text(
        f"# Benchmark task {task['id']}\n\nTarget:\n\n```json\n{json.dumps(task['target'], ensure_ascii=False, indent=2)}\n```\n\n"
        f"Task:\n\n{task['prompt']}\n\nRun: {run_id}, mode {run['mode']}, repeat {run['repeat']}.\n\n"
        "Work only inside this target project unless BENCHMARK_MODE.md explicitly permits an external resource.\n"
        "Create BENCHMARK_RESULT.json matching result.schema.json.\n", encoding="utf-8")
    setup = [f"# Resource setup for {run_id}", "", f"Protocol 1.1; frozen Knowledge commit: `{PIN}`.", ""]
    if run["mode"] == "B":
        setup += ["Expose ONLY a read-only raw Knowledge checkout at the exact pinned commit.",
                  "Do not mount the MCP, SQLite databases or other benchmark runs.",
                  f"Verified checkout: `{frozen_checkout.resolve()}`" if frozen_checkout else
                  "NOT READY: supply --frozen-checkout to verify the local checkout before launching."]
    elif run["mode"] == "C":
        runtime = out / "runtime"
        runtime.mkdir()
        (runtime / "knowledge-runs").mkdir()
        # New run-local mutable DB; no copy of prior write-back state or other run's data.
        with closing(sqlite3.connect(runtime / "knowledge.sqlite")) as con:
            con.execute("PRAGMA journal_mode=DELETE")
        if frozen_db:
            shutil.copy2(frozen_db, runtime / "exteracontext.sqlite")
            (runtime / ".knowledge-ref").write_text(PIN + "\n", encoding="utf-8")
        env = {"EXTERACONTEXT_DB": str((runtime / "exteracontext.sqlite").resolve()),
               "EXTERACONTEXT_KNOWLEDGE_DB": str((runtime / "knowledge.sqlite").resolve()),
               "EXTERACONTEXT_RUN_ROOT": str((runtime / "knowledge-runs").resolve()),
               "EXTERACONTEXT_KNOWLEDGE_REF": PIN,
               "EXTERACONTEXT_AUTO_SYNC": "0"}
        (out / "MCP_BENCHMARK_ENV.json").write_text(json.dumps(env, indent=2) + "\n", encoding="utf-8")
        setup += ["Expose ONLY the MCP (not raw Knowledge or other runs). Inject every variable from MCP_BENCHMARK_ENV.json into the MCP server process.",
                  "No network or automatic syncing: use a locally provisioned base DB whose adjacent .knowledge-ref equals the pinned commit.",
                  "Every run uses independent mutable knowledge.sqlite and knowledge-runs/. Do not point MCP at profile-wide mutable databases.",
                  "Base DB verified/copied at preparation." if frozen_db else
                  "NOT READY: rerun with --frozen-db PATH to verify/copy a pinned local base DB before launching."]
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
    parser.add_argument("--frozen-db", type=Path, help="local pinned SQLite with adjacent .knowledge-ref for mode C")
    args = parser.parse_args()
    try:
        print(prepare(args.run_id, args.output, frozen_checkout=args.frozen_checkout, frozen_db=args.frozen_db))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
