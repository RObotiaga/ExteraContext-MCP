#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

import knowledge_store as ks

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="strict")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="strict")


def dump(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def load_json_arg(value: str | None, default: Any) -> Any:
    if not value:
        return default
    if value.startswith("@"):
        return json.loads(Path(value[1:]).read_text(encoding="utf-8"))
    return json.loads(value)


def cmd_init(args):
    dump({"db": str(ks.init_db())})


def require_runtime_attestation(args) -> str:
    """Authenticate a machine-only invocation; neither role nor caller metadata is authority."""
    source = os.environ.get("EXTERACONTEXT_RUNTIME_SOURCE", "")
    secret = os.environ.get("EXTERACONTEXT_RUNTIME_TOKEN", "")
    if not source.strip() or source != source.strip() or len(secret) < 32:
        raise ValueError("runtime attestation is not configured (EXTERACONTEXT_RUNTIME_SOURCE and EXTERACONTEXT_RUNTIME_TOKEN)")
    token = args.attestation_token
    if getattr(args, "attestation_token_stdin", False) or token == "-":
        token = sys.stdin.readline().strip()
    if not args.runtime_source or not token or not (
        secrets.compare_digest(args.runtime_source, source)
        and secrets.compare_digest(token, secret)
    ):
        raise ValueError("invalid runtime attestation")
    return source


def reject_unattested_runtime_status(status: str) -> None:
    if status == "runtime-verified":
        raise ValueError("runtime-verified requires an attested machine runtime result")


def cmd_run_create(args):
    metadata = load_json_arg(args.metadata, {})
    if args.role == "runtime":
        source = require_runtime_attestation(args)
        if not isinstance(metadata, dict):
            raise ValueError("runtime metadata must be a JSON object")
        metadata = {**metadata, "attested_runtime_source": source}
    rid = ks.create_run(args.role, args.model, args.task_id, args.session_id, metadata)
    dump({"run_id": rid, "role": args.role})


def _evidence_from_args(args) -> list[dict[str, Any]]:
    if args.evidence:
        data = load_json_arg(args.evidence, [])
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return data
        raise SystemExit("--evidence must be a JSON object/list or @file")
    if any(getattr(args, x, None) for x in ("source", "repository", "commit", "path", "lines", "url", "excerpt")):
        return [{
            "evidence_status": args.evidence_status,
            "evidence_type": args.evidence_type,
            "source_type": args.source_type,
            "source": args.source,
            "repository": args.repository,
            "commit": args.commit,
            "path": args.path,
            "lines": args.lines,
            "url": args.url,
            "excerpt": args.excerpt,
        }]
    return []


def cmd_propose(args):
    reject_unattested_runtime_status(args.evidence_status)
    evidence = _evidence_from_args(args)
    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError("evidence must contain JSON objects")
        reject_unattested_runtime_status(item.get("evidence_status"))
    duplicates = ks.find_duplicates(args.claim, 5)
    cid = ks.propose_claim(
        run_id=args.run_id, statement=args.claim, kind=args.kind, scope=args.scope,
        evidence_status=args.evidence_status, api_symbol=args.api,
        client=args.client, platform=args.platform, client_version=args.client_version,
        sdk_version=args.sdk_version, language=args.language, plugin_format=args.plugin_format,
        evidence=evidence,
    )
    dump({"claim_id": cid, "state": "candidate", "preexisting_matches": duplicates})


def cmd_phase_a(args):
    reject_unattested_runtime_status(args.evidence_status)
    vid = ks.phase_a(
        verifier_run_id=args.run_id, claim_id=args.claim_id,
        statement=args.statement, scope=load_json_arg(args.scope_json, {}),
        evidence_status=args.evidence_status, uncertainty=args.uncertainty,
    )
    dump({"verification_id": vid, "phase": "A", "claim_id": args.claim_id})


def cmd_verify(args):
    vid = ks.phase_b(
        verifier_run_id=args.run_id, claim_id=args.claim_id, verdict=args.verdict,
        final_statement=args.final_statement,
        existing_subject_type=args.existing_subject_type,
        existing_subject_id=args.existing_subject_id,
        notes=args.notes,
    )
    dump({"verification_id": vid, "phase": "B", "verdict": args.verdict, "claim_id": args.claim_id})


def cmd_commit(args):
    dump(ks.commit_claim(args.claim_id, args.verifier_run_id))


def cmd_add_evidence(args):
    eid = ks.add_evidence(
        run_id=args.run_id, subject_type=args.subject_type, subject_id=args.subject_id,
        evidence_status=args.evidence_status, evidence_type=args.evidence_type,
        source_type=args.source_type, source=args.source, repository=args.repository,
        commit_sha=args.commit, path=args.path, line_range=args.lines, url=args.url,
        excerpt=args.excerpt, relation=args.relation, client=args.client, platform=args.platform,
        client_version=args.client_version, sdk_version=args.sdk_version,
        metadata=load_json_arg(args.metadata, {}),
    )
    dump({"evidence_id": eid})


def cmd_runtime(args):
    source = require_runtime_attestation(args)
    if args.subject_type != "claim":
        raise ValueError("legacy_fact runtime attestation has no trustworthy target binding; refused")
    claim = ks.get_claim(args.subject_id)
    if claim is None or claim["state"] not in {"verified", "conflicting"}:
        raise ValueError("runtime result requires an existing trusted claim")
    for field in ("client", "platform", "client_version", "sdk_version"):
        if getattr(args, field) != claim[field]:
            raise ValueError(f"runtime target mismatch: {field}")
    # A guessed run id or a user-created runtime role is not a capability.
    con = ks.connect()
    try:
        run = con.execute("SELECT role, metadata_json FROM knowledge_runs WHERE id=?", (args.run_id,)).fetchone()
    finally:
        con.close()
    if run is None or run["role"] != "runtime" or json.loads(run["metadata_json"]).get("attested_runtime_source") != source:
        raise ValueError("runtime run was not created by the attested source")
    if args.runs < 1 or not args.test_id.strip():
        raise ValueError("runtime runs and test_id must be positive and nonempty")
    metadata = load_json_arg(args.metadata, {})
    if not isinstance(metadata, dict):
        raise ValueError("runtime metadata must be a JSON object")
    eid = ks.record_runtime_result(
        run_id=args.run_id, subject_type=args.subject_type, subject_id=args.subject_id,
        passed=args.result == "pass", test_id=args.test_id, runs=args.runs,
        client=args.client, platform=args.platform, client_version=args.client_version,
        sdk_version=args.sdk_version, log_excerpt=args.log_excerpt,
        metadata={**metadata, "attested_runtime_source": source},
    )
    dump({"evidence_id": eid, "runtime_result": args.result})


def cmd_show(args):
    obj = ks.get_claim(args.claim_id)
    if obj is None:
        raise SystemExit(f"claim not found: {args.claim_id}")
    dump(obj)


def cmd_find(args):
    dump(ks.find_duplicates(args.statement, args.limit))


def cmd_search(args):
    dump(ks.search_verified(args.query, args.limit))


def cmd_doctor(args):
    dump(ks.stats())


def add_target_args(p):
    p.add_argument("--client")
    p.add_argument("--platform")
    p.add_argument("--client-version")
    p.add_argument("--sdk-version")


def add_evidence_args(p):
    p.add_argument("--evidence-type", default="source")
    p.add_argument("--source-type")
    p.add_argument("--source")
    p.add_argument("--repository")
    p.add_argument("--commit")
    p.add_argument("--path")
    p.add_argument("--lines")
    p.add_argument("--url")
    p.add_argument("--excerpt")


def add_attestation_args(p):
    p.add_argument("--runtime-source", help="machine source id matching EXTERACONTEXT_RUNTIME_SOURCE")
    p.add_argument("--attestation-token", help="machine-only secret matching EXTERACONTEXT_RUNTIME_TOKEN")
    p.add_argument("--attestation-token-stdin", action="store_true", help="read attestation token from stdin")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Append-only ExteraContext knowledge write-back")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("init", help="Initialize the mutable knowledge database")
    s.set_defaults(func=cmd_init)

    s = sp.add_parser("run-create", help="Create a provenance run for a collector/verifier/main/runtime agent")
    s.add_argument("--role", required=True, choices=sorted(ks.RUN_ROLES))
    s.add_argument("--model")
    s.add_argument("--task-id")
    s.add_argument("--session-id")
    s.add_argument("--metadata", help="JSON or @file")
    add_attestation_args(s)
    s.set_defaults(func=cmd_run_create)

    s = sp.add_parser("propose", help="Collector creates a candidate claim")
    s.add_argument("--run-id", required=True)
    s.add_argument("--claim", required=True)
    s.add_argument("--api")
    s.add_argument("--kind", required=True, choices=sorted(ks.CLAIM_KINDS))
    s.add_argument("--scope", required=True, choices=sorted(ks.SCOPES))
    s.add_argument("--evidence-status", required=True, choices=sorted(ks.EVIDENCE_STATUSES))
    s.add_argument("--language")
    s.add_argument("--plugin-format")
    s.add_argument("--evidence", help="JSON object/list or @file; overrides single evidence flags")
    add_target_args(s); add_evidence_args(s)
    s.set_defaults(func=cmd_propose)

    s = sp.add_parser("verify-phase-a", help="Verifier records blind extraction before seeing candidate")
    s.add_argument("--run-id", required=True)
    s.add_argument("--claim-id", required=True)
    s.add_argument("--statement", required=True)
    s.add_argument("--scope-json", help="JSON or @file")
    s.add_argument("--evidence-status", required=True, choices=sorted(ks.EVIDENCE_STATUSES))
    s.add_argument("--uncertainty")
    s.set_defaults(func=cmd_phase_a)

    s = sp.add_parser("verify", help="Verifier compares fixed phase-A extraction to the candidate")
    s.add_argument("--run-id", required=True)
    s.add_argument("--claim-id", required=True)
    s.add_argument("--verdict", required=True, choices=sorted(ks.VERDICTS))
    s.add_argument("--final-statement")
    s.add_argument("--existing-subject-type", choices=["claim", "legacy_fact"])
    s.add_argument("--existing-subject-id")
    s.add_argument("--notes")
    s.set_defaults(func=cmd_verify)

    s = sp.add_parser("commit", help="Apply a completed verifier verdict; DB guards enforce independence")
    s.add_argument("--claim-id", required=True)
    s.add_argument("--verifier-run-id")
    s.set_defaults(func=cmd_commit)

    # Deliberately no generic direct evidence-write command. Human/model evidence
    # must enter as a collector candidate and be independently verified.

    s = sp.add_parser("record-runtime", help="Append a runtime pass/fail result")
    s.add_argument("--run-id", required=True)
    s.add_argument("--subject-type", required=True, choices=["claim", "legacy_fact"])
    s.add_argument("--subject-id", required=True)
    s.add_argument("--result", required=True, choices=["pass", "fail"])
    s.add_argument("--test-id", required=True)
    s.add_argument("--runs", type=int, default=1)
    s.add_argument("--log-excerpt")
    s.add_argument("--metadata", help="JSON or @file")
    add_target_args(s)
    add_attestation_args(s)
    s.set_defaults(func=cmd_runtime)

    s = sp.add_parser("show", help="Show a claim with evidence/verifications/conflicts")
    s.add_argument("claim_id")
    s.set_defaults(func=cmd_show)

    s = sp.add_parser("find-duplicates", help="Find existing trusted claims similar to a proposed statement")
    s.add_argument("statement")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(func=cmd_find)

    s = sp.add_parser("search", help="Search verified/conflicting agent knowledge")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=12)
    s.set_defaults(func=cmd_search)

    s = sp.add_parser("doctor", help="Show mutable knowledge statistics")
    s.set_defaults(func=cmd_doctor)
    return p


def main():
    args = parser().parse_args()
    try:
        args.func(args)
    except (ValueError, RuntimeError) as e:
        raise SystemExit(f"error: {e}")


if __name__ == "__main__":
    main()
