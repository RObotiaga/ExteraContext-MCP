"""Evaluator-side protocol 1.1 checks; never import this module in a tested-agent run."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROTOCOL = "1.1"
SCHEMA_VERSION = 2
UNKNOWN_TASKS = frozenset({"version-sensitive-symbol", "donor-ayufilter", "unsupported-teleport"})
GROUND = json.loads((ROOT / "ground-truth.json").read_text(encoding="utf-8"))["tasks"]
RUNS = {run["run_id"]: run for run in json.loads((ROOT / "run-manifest.json").read_text(encoding="utf-8"))["runs"]}


def check_assessment(row: dict, *, path: Path | None = None) -> None:
    """Reject stale, ungrounded and internally inconsistent evaluator records."""
    if row.get("protocol_version") != PROTOCOL or row.get("assessment_schema_version") != SCHEMA_VERSION:
        raise ValueError("assessment requires protocol 1.1 and schema version 2")
    run_id = row.get("run_id")
    if run_id not in RUNS:
        raise ValueError(f"unknown run_id: {run_id}")
    run = RUNS[run_id]
    for key in ("task_id", "mode", "repeat"):
        if row.get(key) != run[key]:
            raise ValueError(f"{run_id}: incorrect {key}")
    if row.get("pilot") is not False:
        raise ValueError("pilot assessments cannot enter protocol 1.1 aggregation")
    must = GROUND[run["task_id"]].get("must_handle", [])
    constraints = row.get("constraints")
    if not isinstance(constraints, list) or len(constraints) != len(must):
        raise ValueError(f"{run_id}: require one evaluator verdict per constraint")
    if [item.get("constraint") for item in constraints if isinstance(item, dict)] != must:
        raise ValueError(f"{run_id}: constraints must match ground truth in order")
    for item in constraints:
        if type(item.get("passed")) is not bool or not isinstance(item.get("evidence"), str) or not item["evidence"].strip():
            raise ValueError(f"{run_id}: each constraint requires boolean verdict and evidence")
    if row.get("constraints_handled") is not all(item["passed"] for item in constraints):
        raise ValueError(f"{run_id}: constraints_handled contradicts individual verdicts")
    unknown = row.get("correct_unknown")
    if run["task_id"] in UNKNOWN_TASKS:
        if type(unknown) is not bool:
            raise ValueError(f"{run_id}: correct_unknown must be boolean for trap task")
    elif unknown is not None:
        raise ValueError(f"{run_id}: correct_unknown must be null for implementation task")
    for key in ("materially_completed", "invented_api", "donor_contamination", "version_mismatch", "evidence_boundary_correct", "clean_success", "unnecessary_low_level_fallback"):
        if type(row.get(key)) is not bool:
            raise ValueError(f"{run_id}: {key} must be boolean")
    for key in ("python_compile_success", "target_static_success", "load_success", "runtime_success"):
        if row.get(key) is not None and type(row[key]) is not bool:
            raise ValueError(f"{run_id}: {key} must be boolean or null")
    clean_possible = (row["materially_completed"] and not row["invented_api"] and
                      not row["donor_contamination"] and not row["version_mismatch"] and
                      row["constraints_handled"] and row["evidence_boundary_correct"] and
                      unknown is not False and
                      (run["task_id"] in UNKNOWN_TASKS or row["target_static_success"] is True))
    if row["clean_success"] and not clean_possible:
        raise ValueError(f"{run_id}: clean_success contradicts hard-failure flags")
    tests = row.get("reported_tests", [])
    if not isinstance(tests, list):
        raise ValueError(f"{run_id}: reported_tests must be a list")
    for test in tests:
        if not isinstance(test, dict) or test.get("status") not in ("pass", "fail", "not-run"):
            raise ValueError(f"{run_id}: invalid reported test")
        if test["status"] == "pass" and test.get("verified") is True:
            if not test.get("command") and not test.get("artifact_path"):
                raise ValueError(f"{run_id}: verified PASS lacks command/artifact")
            if test.get("artifact_path"):
                if path is None:
                    raise ValueError("artifact verification requires assessment path")
                artifact = (path.parent / test["artifact_path"]).resolve()
                if not artifact.is_relative_to(path.parent.resolve()) or not artifact.is_file():
                    raise ValueError(f"{run_id}: missing or external test artifact")
        elif test.get("verified") is True:
            raise ValueError(f"{run_id}: only PASS may be verified")


def rate(rows: list[dict], key: str) -> float | None:
    values = [row[key] for row in rows if row.get(key) is not None]
    return sum(values) / len(values) if values else None


def summarize(records: list[tuple[Path, dict]]) -> dict:
    if not records:
        raise ValueError("no eligible protocol 1.1 assessments")
    unique: set[str] = set()
    by_mode = {mode: [] for mode in "ABC"}
    for path, row in records:
        check_assessment(row, path=path)
        if row["run_id"] in unique:
            raise ValueError(f"duplicate run_id: {row['run_id']}")
        unique.add(row["run_id"])
        by_mode[row["mode"]].append(row)
    keys = ("materially_completed", "clean_success", "invented_api", "donor_contamination",
            "version_mismatch", "constraints_handled", "correct_unknown", "evidence_boundary_correct",
            "unnecessary_low_level_fallback", "python_compile_success", "target_static_success",
            "load_success", "runtime_success")
    result = {"protocol_version": PROTOCOL, "runs": len(records), "modes": {}}
    for mode, rows in by_mode.items():
        result["modes"][mode] = {"runs": len(rows), **{f"{key}_rate": rate(rows, key) for key in keys},
                                 "verified_pass_tests": sum(t.get("verified") is True for row in rows for t in row.get("reported_tests", [])),
                                 "unverified_pass_tests": sum(t.get("status") == "pass" and t.get("verified") is not True for row in rows for t in row.get("reported_tests", []))}
    return result
