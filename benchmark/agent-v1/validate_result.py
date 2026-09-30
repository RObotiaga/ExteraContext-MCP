#!/usr/bin/env python3
"""Validate self-reported tests; a command is evidence metadata, not proof it ran."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def validate_result(path: Path) -> dict:
    result = json.loads(path.read_text(encoding="utf-8"))
    tests = result.get("tests")
    if not isinstance(tests, list):
        raise ValueError("tests must be an array")
    for test in tests:
        if not isinstance(test, dict) or test.get("status") not in ("pass", "fail", "not-run"):
            raise ValueError("invalid test entry/status")
        if test["status"] == "pass":
            command = test.get("command")
            artifact_path = test.get("artifact_path")
            if not (isinstance(command, str) and command.strip()) and not (isinstance(artifact_path, str) and artifact_path.strip()):
                raise ValueError(f"PASS {test.get('name')} requires a command or artifact_path")
            if artifact_path:
                artifact = (path.parent / artifact_path).resolve()
                if not artifact.is_relative_to(path.parent.resolve()) or not artifact.is_file():
                    raise ValueError(f"PASS {test.get('name')} artifact is missing or outside target")
    return result


if __name__ == "__main__":
    try:
        validate_result(Path(sys.argv[1]))
        print("BENCHMARK_RESULT test evidence: structurally valid (not independently executed)")
    except (IndexError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
