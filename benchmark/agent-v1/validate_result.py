#!/usr/bin/env python3
"""Validate an agent result against the protocol schema and frozen run packet."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PROTOCOL_REVISION = "1.1"


def _matches_type(value: Any, expected: Any) -> bool:
    types = expected if isinstance(expected, list) else [expected]
    checks = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }
    return any(checks.get(t, lambda _: False)(value) for t in types)


def _validate_schema(value: Any, schema: dict[str, Any], where: str = "result") -> None:
    if "type" in schema and not _matches_type(value, schema["type"]):
        raise ValueError(f"{where} must have type {schema['type']}")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{where} must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{where} must be one of {schema['enum']!r}")
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        raise ValueError(f"{where} is shorter than minLength")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", float("-inf")):
            raise ValueError(f"{where} is below minimum")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"{where} missing required property {key!r}")
        for key, child in schema.get("properties", {}).items():
            if key in value:
                _validate_schema(value[key], child, f"{where}.{key}")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _validate_schema(item, schema["items"], f"{where}[{index}]")
    for condition in schema.get("allOf", []):
        branch = condition.get("if", {})
        condition_matches = True
        if "required" in branch and any(key not in value for key in branch["required"]):
            condition_matches = False
        for key, child in branch.get("properties", {}).items():
            if key in value and child.get("const", object()) != value[key]:
                condition_matches = False
        if condition_matches and "then" in condition:
            _validate_schema(value, condition["then"], where)
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                _validate_schema(value, option, where)
                return
            except ValueError:
                pass
        raise ValueError(f"{where} must match one of its schema alternatives")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _frozen_pin() -> str:
    try:
        from build_frozen_corpus import PIN
    except ImportError as exc:
        raise ValueError("cannot load frozen corpus PIN") from exc
    return PIN


def _check_packet_extensions(packet: Path, run: dict[str, Any], manifest_run: dict[str, Any]) -> None:
    # Only prepare_run's two documented derived fields are permitted beyond manifest data.
    allowed = set(manifest_run) | {"protocol_revision", "environment_expected"}
    if set(run) - allowed:
        raise ValueError("RUN.json contains unsupported extension fields")
    if any(run.get(key) != value for key, value in manifest_run.items()):
        raise ValueError("RUN.json immutable manifest fields do not match frozen manifest")
    if run.get("protocol_revision") != PROTOCOL_REVISION:
        raise ValueError("RUN.json protocol_revision must be exactly '1.1'")

    expected = run.get("environment_expected")
    required = {"frozen_commit", "frozen_tree", "db_sha256", "attestation_sha256", "mcp_package_version"}
    if not isinstance(expected, dict) or set(expected) != required:
        raise ValueError("RUN.json environment_expected must match the closed protocol schema")
    if not isinstance(expected["mcp_package_version"], str) or not expected["mcp_package_version"].strip():
        raise ValueError("RUN.json environment_expected.mcp_package_version must be a non-empty string")
    mode = manifest_run["mode"]
    pin = _frozen_pin()
    if mode == "A":
        if expected["frozen_tree"] is not None:
            raise ValueError("Mode A expected frozen_tree must be null")
        if any(expected[key] is not None for key in ("db_sha256", "attestation_sha256")):
            raise ValueError("A/B RUN.json expected corpus hashes must be null")
        if expected["frozen_commit"] != pin:
            raise ValueError("Mode A RUN.json frozen commit pin mismatch")
        return
    if mode == "B":
        if not isinstance(expected["frozen_commit"], str) or expected["frozen_commit"] != pin:
            raise ValueError("Mode B expected frozen commit must match exact protocol PIN")
        if not isinstance(expected["frozen_tree"], str) or not re.fullmatch(r"[0-9a-f]{40}", expected["frozen_tree"]):
            raise ValueError("Mode B expected frozen_tree must be a 40-character lowercase hex tree")
        if any(expected[key] is not None for key in ("db_sha256", "attestation_sha256")):
            raise ValueError("Mode B RUN.json expected corpus hashes must be null")
        return
    if expected["frozen_tree"] is not None:
        raise ValueError("Mode C expected frozen_tree must be null")

    if not isinstance(expected["frozen_commit"], str) or not re.fullmatch(r"[0-9a-f]{40}", expected["frozen_commit"]):
        raise ValueError("Mode C expected frozen_commit must be a 40-character lowercase hex PIN")
    if expected["frozen_commit"] != pin:
        raise ValueError("Mode C expected frozen_commit does not match protocol PIN")
    for field in ("db_sha256", "attestation_sha256"):
        if not isinstance(expected[field], str) or not re.fullmatch(r"[0-9a-f]{64}", expected[field]):
            raise ValueError(f"Mode C expected {field} must be a 64-character lowercase SHA-256")
    database = (packet / "runtime" / "exteracontext.sqlite").resolve()
    attestation = (packet / "runtime" / "frozen-corpus-attestation.json").resolve()
    packet_root = packet.resolve()
    if (not database.is_relative_to(packet_root) or not database.is_file()
            or _sha256(database) != expected["db_sha256"]):
        raise ValueError("Mode C packet runtime SQLite hash does not match environment_expected")
    if (not attestation.is_relative_to(packet_root) or not attestation.is_file()
            or _sha256(attestation) != expected["attestation_sha256"]):
        raise ValueError("Mode C packet attestation hash does not match environment_expected")
    try:
        payload = json.loads(attestation.read_text(encoding="utf-8"))["payload"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("Mode C packet attestation is invalid") from exc
    if not isinstance(payload, dict) or payload.get("source_commit") != pin or payload.get("db_sha256") != expected["db_sha256"]:
        raise ValueError("Mode C packet attestation does not bind the expected PIN and DB hash")


def _packet_context(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    packet = path.parent.parent
    run_path = packet / "RUN.json"
    task_path = packet / "TASK.json"
    if not run_path.is_file() or not task_path.is_file():
        raise ValueError("result must be inside a prepared run packet containing RUN.json and TASK.json")
    try:
        packet_run = json.loads(run_path.read_text(encoding="utf-8"))
        packet_task = json.loads(task_path.read_text(encoding="utf-8"))
        manifest = json.loads((ROOT / "run-manifest.json").read_text(encoding="utf-8"))
        tasks = json.loads((ROOT / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"cannot load prepared run packet or frozen benchmark manifests: {exc}") from exc

    if manifest.get("benchmark") != "exteracontext-agent-v1":
        raise ValueError("frozen run manifest benchmark identifier is invalid")
    if not isinstance(packet_run, dict):
        raise ValueError("RUN.json must be an object")
    entries = [entry for entry in manifest.get("runs", [])
               if isinstance(entry, dict) and entry.get("run_id") == packet_run.get("run_id")]
    if len(entries) != 1:
        raise ValueError("prepared run_id must occur exactly once in frozen run manifest")
    expected_revision = manifest.get("protocol_revision", PROTOCOL_REVISION)
    if str(expected_revision) != PROTOCOL_REVISION:
        raise ValueError(f"unsupported frozen run manifest protocol revision: {expected_revision!r}")
    _check_packet_extensions(packet, packet_run, entries[0])

    expected_tasks = [task for task in tasks if isinstance(task, dict) and task.get("id") == entries[0].get("task_id")]
    if len(expected_tasks) != 1 or packet_task != expected_tasks[0]:
        raise ValueError("TASK.json does not exactly match the frozen task manifest for this run")
    return packet_run, expected_tasks[0]


def validate_result(path: Path) -> dict[str, Any]:
    path = path.resolve()
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        schema = json.loads((ROOT / "result.schema.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read result or result schema: {exc}") from exc
    _validate_schema(result, schema)
    if result.get("benchmark") != "exteracontext-agent-v1":
        raise ValueError("result benchmark identifier does not match schema")
    if result.get("protocol_revision") != PROTOCOL_REVISION:
        raise ValueError("result protocol_revision must be exactly '1.1'")

    run, _task = _packet_context(path)
    for field in ("run_id", "task_id", "mode", "repeat", "protocol_revision"):
        if result.get(field) != run.get(field):
            raise ValueError(f"result {field} does not match prepared run manifest")

    for test in result["tests"]:
        if test["status"] == "pass":
            command = test.get("command")
            artifact_path = test.get("artifact_path")
            if not (isinstance(command, str) and command.strip()) and not (isinstance(artifact_path, str) and artifact_path.strip()):
                raise ValueError(f"PASS {test.get('name')} requires a command or artifact_path")
            if artifact_path:
                artifact = (path.parent / artifact_path).resolve()
                if not artifact.is_relative_to(path.parent) or not artifact.is_file():
                    raise ValueError(f"PASS {test.get('name')} artifact is missing or outside target")
    return result


if __name__ == "__main__":
    try:
        validate_result(Path(sys.argv[1]))
        print("BENCHMARK_RESULT test evidence: structurally valid for exact prepared run (not independently executed)")
    except (IndexError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
