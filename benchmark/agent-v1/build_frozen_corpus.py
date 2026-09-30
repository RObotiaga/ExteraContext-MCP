#!/usr/bin/env python3
"""Build/verify a local Mode-C corpus attestation; never fetch or execute corpus code.

A candidate is inventory, not proof. A trusted benchmark operator seals it with an
out-of-band HMAC key only after independently confirming the corpus/commit relation.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
import subprocess

PIN = "70f6f614227c8b02d241e7b1e72a0b6691442fd1"
SCHEMA_VERSION = 1
PROTOCOL_VERSION = "1.1"
REQUIRED_COLUMNS = {
    "docs": {"path", "title", "kind", "content"},
    "facts": {"id", "topic", "claim", "api", "evidence_url", "evidence_path", "version", "status", "recipe", "source_id", "source_fact_id", "original_status", "canonical_topic", "review_status", "platform"},
    "meta": {"key", "value"},
    "source_runs": {"call_id", "source_id", "role"},
    "source_provenance": {"source_id", "collector_runs", "reviewer_runs", "has_collector", "has_reviewer"},
    "docs_fts": set(),
    "facts_fts": set(),
}
COUNT_TABLES = ("docs", "facts", "source_runs", "source_provenance")


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_identity(path: Path) -> tuple[int, int, int, int, int]:
    s = path.stat()
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def _require_quiescent(db: Path) -> None:
    for suffix in ("-wal", "-journal", "-shm"):
        sidecar = Path(str(db) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError(f"database has nonempty {suffix} sidecar; stop writers/checkpoint before attesting")


def inspect_database(db_path: Path) -> dict[str, object]:
    # Inspect the caller's path before resolving it: resolve() would erase evidence
    # that the final path component itself is a symlink. These checks do not close
    # a hostile parent-directory/path-swap race; keep corpus paths in operator-owned
    # directories (or use descriptor-based no-follow opens for hostile environments).
    requested = Path(db_path).expanduser()
    if requested.is_symlink():
        raise ValueError(f"database must not be a symlink: {requested}")
    if not requested.is_file():
        raise ValueError(f"database must be an existing regular file: {requested}")
    db = requested.resolve()
    if not db.is_file():
        raise ValueError(f"database must be an existing regular file: {db}")
    _require_quiescent(db)
    identity = _stable_identity(db)
    uri = db.as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as con:
        con.execute("PRAGMA query_only=ON")
        if con.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("SQLite integrity_check failed")
        if con.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("SQLite foreign_key_check failed")
        objects = {r[0]: r[1] for r in con.execute("SELECT name,sql FROM sqlite_master WHERE type IN ('table','view')")}
        missing = REQUIRED_COLUMNS.keys() - objects.keys()
        if missing:
            raise ValueError(f"corpus schema missing objects: {', '.join(sorted(missing))}")
        columns: dict[str, list[str]] = {}
        for table, required in REQUIRED_COLUMNS.items():
            actual = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
            if not required.issubset(actual):
                raise ValueError(f"corpus schema {table} missing columns: {', '.join(sorted(required - set(actual)))}")
            columns[table] = actual
        for table in ("docs_fts", "facts_fts"):
            if "using fts5" not in (objects[table] or "").lower():
                raise ValueError(f"{table} is not an FTS5 virtual table")
        counts = {table: con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in COUNT_TABLES}
        if counts["docs"] <= 0 or counts["facts"] <= 0:
            raise ValueError("corpus must contain nonempty docs and facts")
        definitions = {k: v for k, v in objects.items() if k in REQUIRED_COLUMNS}
        schema = {"columns": columns, "sql": definitions}
        schema_hash = hashlib.sha256(canonical(schema)).hexdigest()
    if _stable_identity(db) != identity:
        raise ValueError("database changed while being inspected")
    _require_quiescent(db)
    digest = sha256_file(db)
    if _stable_identity(db) != identity:
        raise ValueError("database changed while its digest was being computed")
    _require_quiescent(db)
    return {
        "db_sha256": digest,
        "db_size_bytes": identity[2],
        "db_schema_sha256": schema_hash,
        "counts": counts,
    }


def inspect_commit(repo_path: Path, commit: str = PIN) -> dict[str, str]:
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or commit != PIN:
        raise ValueError(f"benchmark requires exact frozen commit {PIN}")
    # Check the caller-supplied leaf before resolve(), which would hide a symlink.
    # This does not eliminate path-swap or symlinked-parent races: callers must keep
    # repository paths in operator-owned directories; use descriptor-based no-follow
    # access if the parent directory can be modified by an adversary.
    requested_repo = Path(repo_path).expanduser()
    if requested_repo.is_symlink():
        raise ValueError(f"local Git repository must not be a symlink: {requested_repo}")
    if not requested_repo.is_dir():
        raise ValueError(f"local Git repository must be an existing directory: {requested_repo}")
    repo = requested_repo.resolve()
    if not repo.is_dir():
        raise ValueError(f"local Git repository not found: {repo}")
    # Git reads existing local objects only. No checkout code is run; no network command is used.
    try:
        replacements = subprocess.run(["git", "-C", str(repo), "replace", "-l"],
                                      check=True, capture_output=True, text=True, encoding="utf-8", timeout=10).stdout.strip()
        if replacements:
            raise ValueError("Git replace refs are present; refusing ambiguous local commit verification")
        actual = subprocess.run(["git", "--no-replace-objects", "-C", str(repo), "rev-parse", "--verify", f"{commit}^{{commit}}"],
                                check=True, capture_output=True, text=True, encoding="utf-8", timeout=10).stdout.strip()
        tree = subprocess.run(["git", "--no-replace-objects", "-C", str(repo), "rev-parse", "--verify", f"{commit}^{{tree}}"],
                              check=True, capture_output=True, text=True, encoding="utf-8", timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"frozen commit is not verifiable from local Git objects: {commit}") from exc
    if actual != commit or not re.fullmatch(r"[0-9a-f]{40}", tree):
        raise ValueError("local Git object verification returned an unexpected commit/tree")
    return {"source_commit": actual, "source_tree": tree}


def build_candidate(repo_path: Path, db_path: Path, commit: str = PIN) -> dict[str, object]:
    """Inventory verified local commit object and exact DB bytes; does not assert their relation."""
    commit_info = inspect_commit(repo_path, commit)
    db_info = inspect_database(db_path)
    return {
        "attestation_schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        **commit_info,
        **db_info,
        "eligible": False,
        "operator_attestation_required": True,
        "warning": "Inventory does not prove this DB was built from the commit. A trusted operator must independently attest that relationship and seal this exact payload with an out-of-band key.",
    }


def _key_bytes(path: Path) -> bytes:
    # As with corpus paths, reject a symlink at the supplied leaf before resolve().
    # This is not race-free against concurrent path replacement or a hostile symlinked
    # parent; the key and its parent directory must be operator-controlled.
    requested = Path(path).expanduser()
    if requested.is_symlink():
        raise ValueError("trusted HMAC key must not be a symlink")
    if not requested.is_file():
        raise ValueError("trusted HMAC key must be an existing regular file")
    key_path = requested.resolve()
    if not key_path.is_file():
        raise ValueError("trusted HMAC key must be an existing regular file")
    key = key_path.read_bytes()
    if len(key) < 32:
        raise ValueError("trusted HMAC key must contain at least 32 random bytes")
    return key


def validate_payload(payload: object) -> dict[str, object]:
    expected = {"attestation_schema_version", "protocol_version", "source_commit", "source_tree",
                "db_sha256", "db_size_bytes", "db_schema_sha256", "counts"}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("frozen corpus payload has missing or additional fields")
    if payload["attestation_schema_version"] != SCHEMA_VERSION or payload["protocol_version"] != PROTOCOL_VERSION:
        raise ValueError("unsupported candidate schema/protocol")
    for field in ("source_commit", "source_tree"):
        if not isinstance(payload[field], str) or not re.fullmatch(r"[0-9a-f]{40}", payload[field]):
            raise ValueError(f"invalid {field} in candidate")
    for field in ("db_sha256", "db_schema_sha256"):
        if not isinstance(payload[field], str) or not re.fullmatch(r"[0-9a-f]{64}", payload[field]):
            raise ValueError(f"invalid {field} in candidate")
    if not isinstance(payload["db_size_bytes"], int) or payload["db_size_bytes"] < 1:
        raise ValueError("invalid database size in candidate")
    counts = payload["counts"]
    if not isinstance(counts, dict) or set(counts) != set(COUNT_TABLES):
        raise ValueError("candidate must bind the exact required database row counts")
    if any(type(value) is not int or value < 0 for value in counts.values()) or counts["docs"] < 1 or counts["facts"] < 1:
        raise ValueError("candidate row counts are invalid")
    if payload["source_commit"] != PIN:
        raise ValueError(f"candidate source commit must equal frozen benchmark pin {PIN}")
    return payload


def seal_candidate(candidate: dict[str, object], key: bytes, operator: str, confirmation: str) -> dict[str, object]:
    if len(key) < 32:
        raise ValueError("trusted HMAC key must contain at least 32 random bytes")
    payload = {k: v for k, v in candidate.items() if k not in {"eligible", "operator_attestation_required", "warning"}}
    expected = f"ATTEST {payload.get('source_commit')} {payload.get('db_sha256')}"
    if confirmation != expected:
        raise ValueError("explicit human confirmation must exactly match the frozen commit and database SHA-256")
    validate_payload(payload)
    if not operator.strip():
        raise ValueError("operator identity is required")
    attestation = {
        "operator": operator.strip(),
        "signed_at_utc": datetime.now(timezone.utc).isoformat(),
        "algorithm": "HMAC-SHA256",
        "signature": hmac.new(key, canonical(payload), hashlib.sha256).hexdigest(),
    }
    return {"payload": payload, "attestation": attestation}


def verify_attestation_signature(attestation_path: Path, key_path: Path, commit: str = PIN) -> dict[str, object]:
    """Verify the operator seal only; does not inspect a repository or database."""
    if commit != PIN:
        raise ValueError("benchmark requires the exact frozen commit pin")
    try:
        envelope = json.loads(attestation_path.read_text(encoding="utf-8"))
        if not isinstance(envelope, dict) or set(envelope) != {"payload", "attestation"}:
            raise ValueError("invalid frozen corpus attestation envelope")
        payload, attestation = envelope["payload"], envelope["attestation"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("invalid frozen corpus attestation envelope") from exc
    if not isinstance(payload, dict) or not isinstance(attestation, dict):
        raise ValueError("invalid frozen corpus attestation structure")
    validate_payload(payload)
    if payload.get("source_commit") != commit or payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("attested source commit or protocol does not match the benchmark pin")
    if set(attestation) != {"operator", "signed_at_utc", "algorithm", "signature"}:
        raise ValueError("attestation fields do not match the schema")
    operator = attestation.get("operator")
    timestamp = attestation.get("signed_at_utc")
    signature = attestation.get("signature")
    if (not isinstance(operator, str) or not operator.strip() or attestation.get("algorithm") != "HMAC-SHA256"
            or not isinstance(timestamp, str) or not isinstance(signature, str)
            or not re.fullmatch(r"[0-9a-f]{64}", signature)):
        raise ValueError("attestation lacks valid operator signature metadata")
    try:
        signed_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if signed_at.tzinfo is None or signed_at.utcoffset() is None:
            raise ValueError("timestamp has no UTC offset")
    except ValueError as exc:
        raise ValueError("attestation signed_at_utc must be an ISO-8601 timestamp with UTC offset") from exc
    key = _key_bytes(key_path)
    expected = hmac.new(key, canonical(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("frozen corpus operator signature is invalid")
    return payload


def verify_attestation(attestation_path: Path, key_path: Path, repo_path: Path, db_path: Path,
                       commit: str = PIN) -> dict[str, object]:
    payload = verify_attestation_signature(attestation_path, key_path, commit)
    actual = {**inspect_commit(repo_path, commit), **inspect_database(db_path)}
    for name, value in actual.items():
        if payload.get(name) != value:
            raise ValueError(f"frozen corpus attestation mismatch for {name}")
    if not isinstance(payload.get("counts"), dict) or not isinstance(payload.get("db_schema_sha256"), str):
        raise ValueError("attestation lacks schema/count bindings")
    return payload


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    candidate = sub.add_parser("candidate", help="inventory an existing local commit object and SQLite corpus")
    candidate.add_argument("--repo", type=Path, required=True)
    candidate.add_argument("--db", type=Path, required=True)
    candidate.add_argument("--output", type=Path, required=True)
    candidate.add_argument("--commit", default=PIN)
    seal = sub.add_parser("seal", help="operator-sign a reviewed candidate with an out-of-band HMAC key")
    seal.add_argument("--candidate", type=Path, required=True)
    seal.add_argument("--key-file", type=Path, required=True, help="out-of-repository trusted 32+ random byte key")
    seal.add_argument("--operator", required=True)
    seal.add_argument("--confirm", required=True, help="exactly: ATTEST <PIN> <db_sha256>")
    seal.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "candidate":
            result = build_candidate(args.repo, args.db, args.commit)
        else:
            raw = json.loads(args.candidate.read_text(encoding="utf-8"))
            candidate_payload = raw.get("payload", raw)
            if not isinstance(candidate_payload, dict):
                raise ValueError("candidate must be a JSON object")
            result = seal_candidate(candidate_payload, _key_bytes(args.key_file), args.operator, args.confirm)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"written: {args.output}")
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"frozen corpus attestation refused: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(_main())
