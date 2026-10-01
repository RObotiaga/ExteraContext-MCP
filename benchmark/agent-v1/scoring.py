"""Evaluator-side protocol 1.1 checks; never import this module in a tested-agent run."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from functools import lru_cache
from build_frozen_corpus import PIN, verify_attestation_signature
from uncertainty import clean_success_uncertainty

ROOT = Path(__file__).resolve().parent
PROTOCOL = "1.1"
SCHEMA_VERSION = 2
MCP_PROTOCOL_REVISION = "2026-07-28"
PACKAGE = json.loads((ROOT.parents[1] / "mcp" / "package.json").read_text(encoding="utf-8"))
MCP_PACKAGE_VERSION = PACKAGE["version"]
UNKNOWN_TASKS = frozenset({"version-sensitive-symbol", "donor-ayufilter", "unsupported-teleport"})
GROUND = json.loads((ROOT / "ground-truth.json").read_text(encoding="utf-8"))["tasks"]


@lru_cache(maxsize=1)
def _assessment_schema() -> dict:
    """Load the checked-in assessment contract adjacent to this scorer."""
    try:
        schema = json.loads((ROOT / "assessment.schema.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot load assessment schema: {exc}") from exc
    if not isinstance(schema, dict):
        raise RuntimeError("assessment schema root must be an object")
    return schema


def _json_type_matches(value: object, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return type(value) is bool
    if expected == "integer":
        return type(value) is int
    if expected == "number":
        return type(value) is int or (type(value) is float and math.isfinite(value))
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return False


def _same_json_value(left: object, right: object) -> bool:
    """JSON equality without Python's bool/int equality alias."""
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json_value(a, b) for a, b in zip(left, right))
    if isinstance(left, dict):
        return (left.keys() == right.keys() and
                all(_same_json_value(left[key], right[key]) for key in left))
    return left == right


def _schema_errors(value: object, schema: dict, path: str = "$", root: object = None) -> list[str]:
    """Small stdlib-only validator for the assessment schema's JSON Schema subset."""
    errors: list[str] = []
    if not isinstance(schema, dict):
        return [f"{path}: invalid schema node"]
    if root is None:
        root = value
    expected_type = schema.get("type")
    if expected_type is not None:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(isinstance(item, str) and _json_type_matches(value, item) for item in types):
            return [f"{path}: expected type {expected_type!r}"]
    if "const" in schema and not _same_json_value(value, schema["const"]):
        errors.append(f"{path}: does not equal const {schema['const']!r}")
    if "enum" in schema and not any(_same_json_value(value, option) for option in schema["enum"]):
        errors.append(f"{path}: value is not in enum")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: string shorter than minLength")
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, value) is None:
            errors.append(f"{path}: string does not match pattern {pattern!r}")
    if type(value) in (int, float) and type(value) is not bool:
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: number below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: number above maximum")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            errors.extend(_schema_errors(item, schema["items"], f"{path}[{index}]", root))
    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, child_schema in properties.items():
            if key in value:
                errors.extend(_schema_errors(value[key], child_schema, f"{path}.{key}", root))
        additional = schema.get("additionalProperties", True)
        for key, child_value in value.items():
            if key in properties:
                continue
            if additional is False:
                errors.append(f"{path}: additional property {key!r} is not allowed")
            elif isinstance(additional, dict):
                errors.extend(_schema_errors(child_value, additional, f"{path}.{key}", root))
    for child_schema in schema.get("allOf", []):
        errors.extend(_schema_errors(value, child_schema, path, root))
    condition = schema.get("if")
    if isinstance(condition, dict) and not _schema_errors(value, condition, path, root):
        then_schema = schema.get("then")
        if isinstance(then_schema, dict):
            errors.extend(_schema_errors(value, then_schema, path, root))
    return errors


def validate_assessment_schema(row: object) -> None:
    errors = _schema_errors(row, _assessment_schema())
    if errors:
        raise ValueError("assessment does not satisfy schema: " + "; ".join(errors[:12]))


RUNS = {run["run_id"]: run for run in json.loads((ROOT / "run-manifest.json").read_text(encoding="utf-8"))["runs"]}


def _packet_root(path: Path) -> tuple[Path, dict]:
    """Find and validate the prepared packet enclosing this assessment."""
    if path.is_symlink():
        raise ValueError("assessment path must not be a symlink")
    assessment = path.resolve()
    if not assessment.is_file():
        raise ValueError("assessment must be an existing file inside a prepared packet")
    for parent in assessment.parents:
        run_file = parent / "RUN.json"
        if run_file.is_file():
            packet = parent.resolve()
            if not assessment.is_relative_to(packet):
                raise ValueError("assessment path escapes prepared run packet")
            try:
                run_row = json.loads(run_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ValueError("prepared RUN.json is unreadable") from exc
            if not isinstance(run_row, dict):
                raise ValueError("prepared RUN.json must be an object")
            return packet, run_row
    raise ValueError("assessment is not inside a prepared run packet containing RUN.json")


def _packet_file(packet: Path, relative: object, description: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError(f"{description} artifact path is required")
    candidate = (packet / relative).resolve()
    if not candidate.is_relative_to(packet.resolve()) or not candidate.is_file():
        raise ValueError(f"{description} artifact is missing or outside the prepared packet")
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_environment(row: dict, packet: Path, packet_run: dict, manifest_run: dict,
                      attestation_key_path: Path | None = None) -> None:
    # Packet RUN identity and protocol must still correspond to the frozen public manifest.
    for key in ("run_id", "task_id", "mode", "repeat"):
        if packet_run.get(key) != manifest_run.get(key):
            raise ValueError(f"{manifest_run['run_id']}: prepared RUN.json has incorrect {key}")
    if str(packet_run.get("protocol_revision")) != PROTOCOL:
        raise ValueError(f"{manifest_run['run_id']}: prepared RUN.json protocol mismatch")
    expected = packet_run.get("environment_expected")
    expected_fields = {"frozen_commit", "frozen_tree", "db_sha256", "attestation_sha256", "mcp_package_version"}
    if not isinstance(expected, dict) or set(expected) != expected_fields:
        raise ValueError(f"{manifest_run['run_id']}: RUN.json lacks canonical frozen environment expectations")
    if expected.get("frozen_commit") != PIN:
        raise ValueError(f"{manifest_run['run_id']}: RUN.json frozen commit pin mismatch")
    if expected.get("mcp_package_version") != MCP_PACKAGE_VERSION:
        raise ValueError(f"{manifest_run['run_id']}: RUN.json expected MCP package version mismatch")
    if manifest_run["mode"] == "B":
        if not isinstance(expected.get("frozen_tree"), str) or not re.fullmatch(r"[0-9a-f]{40}", expected["frozen_tree"]):
            raise ValueError(f"{manifest_run['run_id']}: Mode B RUN.json lacks a valid frozen tree pin")
    elif expected.get("frozen_tree") is not None:
        raise ValueError(f"{manifest_run['run_id']}: Mode A/C must not pin a Knowledge tree")

    evidence = row.get("environment_evidence")
    base_required = {"applicability", "rationale", "actual_mcp_package_version", "protocol_revision",
                     "frozen_commit", "db_sha256", "attestation_sha256", "doctor_artifact_path"}
    checkout_fields = {"checkout_verified", "knowledge_commit", "knowledge_tree", "checkout_artifact_path"}
    required = base_required | (checkout_fields if manifest_run["mode"] == "B" else set())
    allowed = base_required | checkout_fields
    if not isinstance(evidence, dict) or not required <= set(evidence) or not set(evidence) <= allowed:
        raise ValueError(f"{manifest_run['run_id']}: explicit complete environment_evidence is required")
    if not isinstance(evidence.get("rationale"), str) or not evidence["rationale"].strip():
        raise ValueError(f"{manifest_run['run_id']}: environment evidence requires a rationale")
    if manifest_run["mode"] != "C":
        if evidence["applicability"] != "not-applicable":
            raise ValueError(f"{manifest_run['run_id']}: non-MCP A/B runs must mark MCP environment not-applicable")
        if any(evidence.get(k) is not None for k in base_required - {"applicability", "rationale"}):
            raise ValueError(f"{manifest_run['run_id']}: non-MCP run must not claim MCP deployment evidence")
        if expected.get("db_sha256") is not None or expected.get("attestation_sha256") is not None:
            raise ValueError(f"{manifest_run['run_id']}: non-C packet unexpectedly pins a corpus DB")
        if manifest_run["mode"] == "A":
            if any(evidence.get(k) not in (None, False) for k in checkout_fields):
                raise ValueError(f"{manifest_run['run_id']}: Mode A must not claim frozen Knowledge checkout evidence")
            if expected.get("frozen_tree") is not None:
                raise ValueError(f"{manifest_run['run_id']}: Mode A must not pin a Knowledge tree")
            return
        # Mode B is non-MCP but still requires exact raw Knowledge checkout evidence.
        if evidence.get("checkout_verified") is not True:
            raise ValueError(f"{manifest_run['run_id']}: Mode B requires verified frozen Knowledge checkout evidence")
        if evidence.get("knowledge_commit") != expected["frozen_commit"]:
            raise ValueError(f"{manifest_run['run_id']}: Mode B checkout commit does not match RUN.json pin")
        expectation_path = _packet_file(packet, "knowledge-checkout.expected.json", "Knowledge checkout expectation")
        try:
            checkout_expected = json.loads(expectation_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"{manifest_run['run_id']}: invalid Knowledge checkout expectation") from exc
        if (not isinstance(checkout_expected, dict) or set(checkout_expected) != {"schema_version", "commit", "tree"} or
                checkout_expected.get("schema_version") != 1 or checkout_expected.get("commit") != expected["frozen_commit"] or
                checkout_expected.get("commit") != PIN or checkout_expected.get("tree") != expected["frozen_tree"] or
                not isinstance(checkout_expected.get("tree"), str) or
                not re.fullmatch(r"[0-9a-f]{40}", checkout_expected["tree"])):
            raise ValueError(f"{manifest_run['run_id']}: Mode B packet lacks a valid exact checkout tree pin")
        if evidence.get("knowledge_tree") != expected["frozen_tree"]:
            raise ValueError(f"{manifest_run['run_id']}: Mode B checkout tree does not match RUN.json pin")
        artifact_path = _packet_file(packet, evidence.get("checkout_artifact_path"), "Knowledge checkout")
        try:
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"{manifest_run['run_id']}: invalid Knowledge checkout artifact JSON") from exc
        artifact_fields = {"schema_version", "commands", "head", "tree", "status_porcelain"}
        expected_commands = ["git rev-parse HEAD", "git rev-parse HEAD^{tree}",
                             "git status --porcelain --untracked-files=all --ignored=matching"]
        if not isinstance(artifact, dict) or set(artifact) != artifact_fields:
            raise ValueError(f"{manifest_run['run_id']}: Knowledge checkout artifact has invalid structure")
        if (artifact.get("schema_version") != 1 or artifact.get("commands") != expected_commands or
                artifact.get("head") != PIN or artifact.get("tree") != checkout_expected["tree"] or
                artifact.get("status_porcelain") != ""):
            raise ValueError(f"{manifest_run['run_id']}: Knowledge checkout artifact is wrong, dirty, or mismatched")
        if expected.get("frozen_commit") != PIN:
            raise ValueError(f"{manifest_run['run_id']}: Mode B packet commit pin mismatch")
        return

    if any(evidence.get(k) not in (None, False) for k in checkout_fields):
        raise ValueError(f"{manifest_run['run_id']}: Mode C must not claim raw checkout exposure")
    if evidence["applicability"] != "verified":
        raise ValueError(f"{manifest_run['run_id']}: Mode C requires verified MCP environment evidence")
    for field in ("frozen_commit", "db_sha256", "attestation_sha256"):
        value = expected.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}" if field != "frozen_commit" else r"[0-9a-f]{40}", value):
            raise ValueError(f"{manifest_run['run_id']}: Mode C packet lacks valid expected {field}")
        if evidence.get(field) != value:
            raise ValueError(f"{manifest_run['run_id']}: environment evidence {field} does not match RUN.json pin")
    if evidence.get("actual_mcp_package_version") != expected["mcp_package_version"]:
        raise ValueError(f"{manifest_run['run_id']}: deployed MCP version does not match expected package version")
    if evidence.get("protocol_revision") != MCP_PROTOCOL_REVISION:
        raise ValueError(f"{manifest_run['run_id']}: deployed MCP protocol revision mismatch")

    database = _packet_file(packet, "runtime/exteracontext.sqlite", "frozen SQLite")
    if _sha256(database) != expected["db_sha256"]:
        raise ValueError(f"{manifest_run['run_id']}: packet SQLite hash does not match RUN.json")
    attestation = _packet_file(packet, "runtime/frozen-corpus-attestation.json", "frozen corpus attestation")
    if _sha256(attestation) != expected["attestation_sha256"]:
        raise ValueError(f"{manifest_run['run_id']}: packet attestation digest does not match RUN.json")
    if attestation_key_path is None:
        raise ValueError(f"{manifest_run['run_id']}: Mode C aggregation requires an out-of-packet --attestation-key-file")
    key_path = Path(attestation_key_path).expanduser()
    try:
        resolved_key = key_path.resolve(strict=True)
        if resolved_key.is_relative_to(packet.resolve()):
            raise ValueError("attestation key must be stored outside the prepared packet")
        attestation_payload = verify_attestation_signature(attestation, key_path)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{manifest_run['run_id']}: Mode C operator signature verification failed: {exc}") from exc
    if attestation_payload.get("source_commit") != expected["frozen_commit"] or attestation_payload.get("db_sha256") != expected["db_sha256"]:
        raise ValueError(f"{manifest_run['run_id']}: in-packet attestation does not bind the pinned corpus")

    doctor_path = _packet_file(packet, evidence.get("doctor_artifact_path"), "MCP doctor")
    try:
        doctor = json.loads(doctor_path.read_text(encoding="utf-8"))
        structured = doctor["structuredContent"]
        meta = structured["meta"]
        data = structured["data"]
        index = data["index"]
        if not isinstance(meta, dict):
            raise TypeError("doctor meta must be an object")
        if not isinstance(data, dict) or not isinstance(index, dict):
            raise TypeError("doctor data.index must be an object")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"{manifest_run['run_id']}: doctor artifact lacks structured MCP metadata or data.index") from exc
    if meta.get("server_version") != expected["mcp_package_version"] or meta.get("protocol_revision") != MCP_PROTOCOL_REVISION:
        raise ValueError(f"{manifest_run['run_id']}: doctor artifact does not confirm expected server version/protocol")
    doctor_db_path = index.get("db_path")
    if not isinstance(doctor_db_path, str) or not doctor_db_path:
        raise ValueError(f"{manifest_run['run_id']}: doctor index lacks db_path")
    # Paths in captured evidence belong to the server's launch filesystem, not
    # the evaluator's. Compare with the archived launch environment literally;
    # current packet bytes are independently bound by the signed hash above.
    launch_path = _packet_file(packet, "MCP_BENCHMARK_ENV.json", "MCP launch environment")
    try:
        launch = json.loads(launch_path.read_text(encoding="utf-8"))
        launched_db_path = launch.get("EXTERACONTEXT_DB") if isinstance(launch, dict) else None
    except (OSError, ValueError) as exc:
        raise ValueError(f"{manifest_run['run_id']}: invalid MCP launch environment") from exc
    if not isinstance(launched_db_path, str) or not launched_db_path.strip():
        raise ValueError(f"{manifest_run['run_id']}: MCP launch environment lacks EXTERACONTEXT_DB")
    if doctor_db_path != launched_db_path:
        raise ValueError(f"{manifest_run['run_id']}: doctor index db_path does not match recorded MCP launch environment")
    if index.get("db_sha256") != expected["db_sha256"]:
        raise ValueError(f"{manifest_run['run_id']}: doctor index db_sha256 does not match pinned database hash")
    signed_db_size = attestation_payload.get("db_size_bytes")
    doctor_db_size = index.get("db_size_bytes")
    if (not isinstance(signed_db_size, int) or isinstance(signed_db_size, bool) or
            not isinstance(doctor_db_size, int) or isinstance(doctor_db_size, bool) or
            doctor_db_size != signed_db_size):
        raise ValueError(f"{manifest_run['run_id']}: doctor index db_size_bytes does not match signed attestation")


def check_assessment(row: dict, *, path: Path | None = None,
                     attestation_key_path: Path | None = None) -> None:
    """Reject stale, ungrounded and internally inconsistent evaluator records."""
    validate_assessment_schema(row)
    if path is None:
        raise ValueError("assessment path is required for prepared packet integrity checks")
    packet, packet_run = _packet_root(path)
    if row.get("protocol_version") != PROTOCOL or row.get("assessment_schema_version") != SCHEMA_VERSION:
        raise ValueError("assessment requires protocol 1.1 and schema version 2")
    run_id = row.get("run_id")
    if run_id not in RUNS:
        raise ValueError(f"unknown run_id: {run_id}")
    run = RUNS[run_id]
    _check_environment(row, packet, packet_run, run, attestation_key_path)
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
                try:
                    _packet_file(packet, test["artifact_path"], "reported test")
                except ValueError as exc:
                    raise ValueError(f"{run_id}: missing or external test artifact") from exc
        elif test.get("verified") is True:
            raise ValueError(f"{run_id}: only PASS may be verified")


def rate(rows: list[dict], key: str) -> float | None:
    values = [row[key] for row in rows if row.get(key) is not None]
    return sum(values) / len(values) if values else None


def summarize(records: list[tuple[Path, dict]], *, attestation_key_path: Path | None = None,
              _allow_incomplete_for_tests: bool = False) -> dict:
    """Validate and summarize assessments against the entire frozen schedule.

    `_allow_incomplete_for_tests` is a private test-only escape hatch for rate-math
    unit tests. Production callers and the CLI must leave it false.
    """
    counts: dict[str, int] = {}
    for _, row in records:
        run_id = row.get("run_id") if isinstance(row, dict) else None
        if isinstance(run_id, str):
            counts[run_id] = counts.get(run_id, 0) + 1
    duplicate_ids = sorted(run_id for run_id, count in counts.items() if count > 1)
    missing_ids = sorted(set(RUNS) - set(counts))
    unexpected_ids = sorted(set(counts) - set(RUNS))
    if duplicate_ids:
        raise ValueError("duplicate run_id(s): " + ", ".join(duplicate_ids))
    if unexpected_ids:
        raise ValueError("unexpected run_id(s): " + ", ".join(unexpected_ids))
    if missing_ids and not _allow_incomplete_for_tests:
        raise ValueError("incomplete benchmark schedule; missing run_id(s): " + ", ".join(missing_ids))
    if not records:
        raise ValueError("no eligible protocol 1.1 assessments")
    has_mode_c = any(row.get("mode") == "C" for _, row in records)
    if has_mode_c and attestation_key_path is None:
        raise ValueError("Mode C aggregation requires an out-of-packet --attestation-key-file")
    if has_mode_c and attestation_key_path is not None:
        try:
            resolved_key = Path(attestation_key_path).expanduser().resolve(strict=True)
            packet_roots = {_packet_root(path)[0] for path, _ in records}
        except (OSError, ValueError) as exc:
            raise ValueError(f"could not resolve Mode C attestation key or packet roots: {exc}") from exc
        if any(resolved_key.is_relative_to(packet_root) for packet_root in packet_roots):
            raise ValueError("Mode C attestation key must be stored outside every prepared run packet in the aggregation set")
    by_mode = {mode: [] for mode in "ABC"}
    for path, row in records:
        check_assessment(row, path=path, attestation_key_path=attestation_key_path)
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
    if not missing_ids:
        result["clean_success_uncertainty"] = clean_success_uncertainty(
            [row for _, row in records], RUNS
        )
    return result
