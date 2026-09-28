#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# MCP/CLI output must never inherit a Windows legacy code page.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="strict")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="strict")

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
import knowledge_store as ks  # noqa: E402

DEFAULT_RUN_ROOT = Path(os.environ.get("EXTERACONTEXT_RUN_ROOT", ".exteracontext/knowledge-runs"))
SCHEMAS = ROOT / "schemas"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def dump(v: Any) -> None:
    print(json.dumps(v, ensure_ascii=False, indent=2))


def load_json_arg(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    if value.startswith("@"):
        return json.loads(Path(value[1:]).read_text(encoding="utf-8"))
    return json.loads(value)


def load_text_arg(value: str | None, default: str = "") -> str:
    if value is None:
        return default
    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8")
    return value


def safe_id(value: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return s[:60] or "task"


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_fingerprint(token: str) -> str:
    return _token_digest(token)[:12]


def issue_actor(role: str) -> tuple[str, str, str]:
    prefix = "col" if role == "collector" else "ver"
    token = f"kc_{prefix}_{secrets.token_urlsafe(32)}"
    actor_id = f"{role}-{uuid.uuid4().hex[:12]}"
    return token, _token_digest(token), actor_id


def require_actor_token(state: dict[str, Any], role: str, token: str, *, allow_consumed: bool = False) -> None:
    if not token:
        raise ValueError(f"{role} actor token is required")
    expected = state.get(f"{role}_token_hash")
    if not expected or not secrets.compare_digest(expected, _token_digest(token)):
        raise ValueError(f"invalid {role} actor token for this orchestration")
    if state.get(f"{role}_token_consumed") and not allow_consumed:
        raise ValueError(f"{role} actor token has already been consumed")


def parse_runtime_actor(value: str | None) -> dict[str, Any]:
    actor = load_json_arg(value, {}) if value else {}
    if actor is None:
        return {}
    if not isinstance(actor, dict):
        raise ValueError("runtime actor metadata must be a JSON object")
    # Runtime identity is optional caller-attested provenance, never the authorization primitive.
    return {str(k): v for k, v in actor.items() if v is not None and v != ""}


def _identity_fields(actor: dict[str, Any] | None) -> dict[str, str]:
    actor = actor or {}
    return {k: str(actor[k]) for k in ("child_id", "session_id", "invocation_id", "run_id") if actor.get(k) not in (None, "")}


def runtime_actors_same(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    aa, bb = _identity_fields(a), _identity_fields(b)
    return any(k in bb and bb[k] == v for k, v in aa.items())


def require_runtime_actor_continuity(previous: dict[str, Any] | None, current: dict[str, Any] | None) -> None:
    prev = _identity_fields(previous)
    if not prev:
        return
    cur = _identity_fields(current)
    if not cur:
        raise ValueError("Phase A supplied runtime child identity; Phase B must supply matching runtime actor metadata")
    for key, value in prev.items():
        if key in cur and cur[key] != value:
            raise ValueError(f"Phase B runtime actor does not match Phase A ({key})")
    if not any(key in cur and cur[key] == value for key, value in prev.items()):
        raise ValueError("Phase B runtime actor does not match the verifier child used for Phase A")


def run_dir(run_root: Path, orch_id: str) -> Path:
    p = run_root / orch_id
    if not p.exists():
        raise ValueError(f"unknown orchestration: {orch_id}")
    return p


def read_state(p: Path) -> dict[str, Any]:
    return json.loads((p / "state.json").read_text(encoding="utf-8"))


def write_state(p: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now()
    (p / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def schema_text(name: str) -> str:
    return (SCHEMAS / name).read_text(encoding="utf-8")


def validate_shape(kind: str, obj: Any) -> None:
    if not isinstance(obj, dict):
        raise ValueError(f"{kind} result must be a JSON object")
    if kind == "collector":
        req = {"action","claim","kind","scope","target","evidence_status","evidence_refs","existing_claim","conflicts_with"}
        if missing := req - obj.keys(): raise ValueError(f"collector missing fields: {sorted(missing)}")
        if obj["action"] not in {"propose","skip"}: raise ValueError("collector action must be propose|skip")
        if obj["kind"] not in ks.CLAIM_KINDS: raise ValueError("invalid collector kind")
        if obj["scope"] not in ks.SCOPES: raise ValueError("invalid collector scope")
        if obj["evidence_status"] not in ks.EVIDENCE_STATUSES: raise ValueError("invalid collector evidence_status")
        if not isinstance(obj["target"], dict) or not isinstance(obj["evidence_refs"], list): raise ValueError("invalid collector target/evidence_refs")
    elif kind == "phase_a":
        req = {"statement","scope","evidence_status","uncertainty"}
        if missing := req - obj.keys(): raise ValueError(f"phase A missing fields: {sorted(missing)}")
        if not str(obj["statement"]).strip(): raise ValueError("phase A statement cannot be empty")
        if obj["evidence_status"] not in ks.EVIDENCE_STATUSES: raise ValueError("invalid phase A evidence_status")
    elif kind == "phase_b":
        req = {"verdict","final_statement","existing_subject_type","existing_subject_id","notes"}
        if missing := req - obj.keys(): raise ValueError(f"phase B missing fields: {sorted(missing)}")
        if obj["verdict"] not in ks.VERDICTS: raise ValueError("invalid phase B verdict")
        if obj["verdict"] in {"attach-evidence","conflict"} and not (obj.get("existing_subject_type") and obj.get("existing_subject_id")):
            raise ValueError(f"{obj['verdict']} requires existing subject")


def target_from(state: dict[str, Any]) -> dict[str, Any]:
    return state.get("target") or {}


def trusted_matches(query: str, limit: int = 8) -> list[dict[str, Any]]:
    if not query.strip(): return []
    return ks.find_duplicates(query, limit)


def evidence_bundle(state: dict[str, Any]) -> Any:
    return state.get("evidence") or []

def normalize_evidence(raw: Any) -> list[dict[str, Any]]:
    items = raw if isinstance(raw, list) else [raw]
    out = []
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            item = {"evidence_type": "note", "excerpt": str(item)}
        ev = dict(item)
        ev.setdefault("evidence_id", f"source-{i:03d}")
        ev.setdefault("evidence_type", "source")
        out.append(ev)
    ids = [x["evidence_id"] for x in out]
    if len(ids) != len(set(ids)):
        raise ValueError("original evidence_id values must be unique")
    return out

def selected_original_evidence(state: dict[str, Any], refs: list[str]) -> list[dict[str, Any]]:
    by_id = {e["evidence_id"]: e for e in evidence_bundle(state)}
    unknown = [r for r in refs if r not in by_id]
    if unknown:
        raise ValueError(f"collector referenced unknown original evidence: {unknown}")
    # Remove orchestration-only id before persistence.
    return [{k:v for k,v in by_id[r].items() if k != "evidence_id"} for r in refs]



def render_collector_prompt(state: dict[str, Any]) -> str:
    return f"""You are the ExteraContext knowledge COLLECTOR. Work independently and cheaply. Your job is not to solve the user's task; extract only reusable knowledge supported by the supplied original evidence.

Rules:
- Return one atomic falsifiable claim, or action=skip when nothing reusable is grounded.
- Unknown fields stay null/unknown. Never guess versions, signatures, runtime behavior, or scope.
- Static code/docs are never runtime-verified.
- Donor-client evidence must stay donor evidence and must not be presented as an ExteraGram API.
- Existing matches are hints for duplicate/conflict detection, not authority for the new claim.
- Select evidence only by evidence_refs from ORIGINAL EVIDENCE. Never invent or rewrite repository/path/commit/lines provenance.
- Output JSON only, matching collector-output.schema.json.

TASK CONTEXT:
{state['task']}

TARGET KNOWN BY PARENT:
{json.dumps(target_from(state), ensure_ascii=False, indent=2)}

ORIGINAL EVIDENCE:
{json.dumps(evidence_bundle(state), ensure_ascii=False, indent=2)}

EXISTING TRUSTED MATCHES:
{json.dumps(state.get('preexisting_matches', []), ensure_ascii=False, indent=2)}
"""


def render_phase_a_prompt(state: dict[str, Any], claim: dict[str, Any]) -> str:
    # Deliberately excludes collector_result and candidate statement.
    return f"""You are the ExteraContext independent VERIFIER, PHASE A (BLIND).
You MUST form your own extraction before seeing the collector's candidate. The candidate is intentionally absent from this prompt.

Inspect only the original evidence and relevant pre-existing trusted knowledge. Return one falsifiable sentence stating the narrowest thing the evidence itself supports, its justified scope, evidence status, and uncertainty. Do not infer runtime success from static source. Treat donor implementations as donor evidence only.

Output JSON only, matching verifier-phase-a.schema.json.

TARGET KNOWN INDEPENDENTLY:
{json.dumps(target_from(state), ensure_ascii=False, indent=2)}

ORIGINAL EVIDENCE:
{json.dumps(evidence_bundle(state), ensure_ascii=False, indent=2)}

RELEVANT EXISTING TRUSTED KNOWLEDGE:
{json.dumps(state.get('preexisting_matches', []), ensure_ascii=False, indent=2)}
"""


def render_phase_b_prompt(state: dict[str, Any], claim: dict[str, Any]) -> str:
    return f"""You are the same ExteraContext independent VERIFIER, PHASE B.
Your Phase A extraction was already persisted and MUST NOT be rewritten merely to match the collector. Compare the collector candidate against that fixed extraction, original evidence, and existing trusted matches.

Return exactly one verdict: accept, accept-with-changes, attach-evidence, conflict, reject, or needs-runtime.
- accept-with-changes: provide final_statement.
- attach-evidence/conflict: provide existing_subject_type and existing_subject_id.
- needs-runtime when static evidence cannot justify a runtime claim.
Output JSON only, matching verifier-phase-b.schema.json.

FIXED PHASE A:
{json.dumps(state['phase_a_result'], ensure_ascii=False, indent=2)}

COLLECTOR CANDIDATE (revealed only now):
{json.dumps(state['collector_result'], ensure_ascii=False, indent=2)}

CANDIDATE DB RECORD:
{json.dumps(claim, ensure_ascii=False, indent=2)}

ORIGINAL EVIDENCE:
{json.dumps(evidence_bundle(state), ensure_ascii=False, indent=2)}

EXISTING TRUSTED MATCHES:
{json.dumps(state.get('preexisting_matches', []), ensure_ascii=False, indent=2)}
"""


def cmd_reflect(args: argparse.Namespace) -> None:
    root = Path(args.run_root)
    root.mkdir(parents=True, exist_ok=True)
    task = load_text_arg(args.task)
    evidence = normalize_evidence(load_json_arg(args.evidence, []))
    target = load_json_arg(args.target, {})
    seed = safe_id(args.task_id or task[:32])
    oid = f"{seed}-{uuid.uuid4().hex[:8]}"
    p = root / oid
    p.mkdir(parents=True)
    lookup = args.discovery or task
    matches = trusted_matches(lookup)
    collector_token, collector_hash, collector_actor_id = issue_actor("collector")
    state = {
        "orchestration_id": oid, "task_id": args.task_id or oid, "task": task,
        "target": target, "evidence": evidence, "discovery": args.discovery,
        "preexisting_matches": matches, "stage": "collector-ready",
        "collector_actor_id": collector_actor_id,
        "collector_token_hash": collector_hash, "collector_token_consumed": False,
        "created_at": now(), "updated_at": now(), "history": []
    }
    write_state(p, state)
    prompt = render_collector_prompt(state)
    (p / "collector.prompt.md").write_text(prompt, encoding="utf-8")
    (p / "collector.schema.json").write_text(schema_text("collector-output.schema.json"), encoding="utf-8")
    dump({
        "orchestration_id": oid, "stage": state["stage"], "dir": str(p),
        "collector_actor_id": collector_actor_id, "collector_token": collector_token,
        "prompt": str(p / "collector.prompt.md"), "schema": str(p / "collector.schema.json")
    })


def cmd_collector(args: argparse.Namespace) -> None:
    p = run_dir(Path(args.run_root), args.id)
    state = read_state(p)
    if state["stage"] != "collector-ready":
        raise ValueError(f"expected collector-ready, got {state['stage']}")
    require_actor_token(state, "collector", args.actor_token)
    result = load_json_arg(args.result)
    validate_shape("collector", result)
    runtime_actor = parse_runtime_actor(args.runtime_actor)
    (p / "collector.result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    state["collector_result"] = result
    if result["action"] == "skip":
        state["collector_token_consumed"] = True
        state["stage"] = "skipped"
        state["history"].append({"at": now(), "event": "collector-skip", "actor_id": state["collector_actor_id"]})
        write_state(p, state)
        dump({"orchestration_id": args.id, "stage": "skipped"})
        return
    meta = {
        "orchestration_id": args.id,
        "capability_actor_id": state["collector_actor_id"],
        "capability_token_fingerprint": _token_fingerprint(args.actor_token),
        "runtime_actor": runtime_actor or None,
        "runtime_identity_attested_by_caller": bool(runtime_actor),
        "protocol": "collector-capability-v2"
    }
    model = args.model or "unspecified-subagent"
    rid = ks.create_run("collector", model, state["task_id"], state["collector_actor_id"], meta)
    tgt = result.get("target") or {}
    cid = ks.propose_claim(
        run_id=rid, statement=result["claim"], kind=result["kind"], scope=result["scope"],
        evidence_status=result["evidence_status"], api_symbol=result.get("api_symbol"),
        client=tgt.get("client"), platform=tgt.get("platform"), client_version=tgt.get("client_version"),
        sdk_version=tgt.get("sdk_version"), language=tgt.get("language"), plugin_format=tgt.get("plugin_format"),
        evidence=selected_original_evidence(state, result.get("evidence_refs") or [])
    )
    verifier_token, verifier_hash, verifier_actor_id = issue_actor("verifier")
    state.update({
        "collector_run_id": rid, "claim_id": cid, "collector_model": model,
        "collector_runtime_actor": runtime_actor or None, "collector_token_consumed": True,
        "verifier_actor_id": verifier_actor_id, "verifier_token_hash": verifier_hash,
        "verifier_token_consumed": False, "stage": "phase-a-ready"
    })
    state["history"].append({"at": now(), "event": "collector-ingested", "run_id": rid, "claim_id": cid, "actor_id": state["collector_actor_id"]})
    write_state(p, state)
    claim = ks.get_claim(cid) or {}
    prompt = render_phase_a_prompt(state, claim)
    (p / "verifier-phase-a.prompt.md").write_text(prompt, encoding="utf-8")
    (p / "verifier-phase-a.schema.json").write_text(schema_text("verifier-phase-a.schema.json"), encoding="utf-8")
    dump({
        "orchestration_id": args.id, "stage": "phase-a-ready", "claim_id": cid,
        "collector_run_id": rid, "verifier_actor_id": verifier_actor_id, "verifier_token": verifier_token,
        "prompt": str(p / "verifier-phase-a.prompt.md"), "schema": str(p / "verifier-phase-a.schema.json")
    })


def cmd_phase_a(args: argparse.Namespace) -> None:
    p = run_dir(Path(args.run_root), args.id)
    state = read_state(p)
    if state["stage"] != "phase-a-ready":
        raise ValueError(f"expected phase-a-ready, got {state['stage']}")
    require_actor_token(state, "verifier", args.actor_token)
    result = load_json_arg(args.result)
    validate_shape("phase_a", result)
    runtime_actor = parse_runtime_actor(args.runtime_actor)
    if runtime_actor and runtime_actors_same(runtime_actor, state.get("collector_runtime_actor")):
        raise ValueError("verifier runtime actor must be different from collector runtime actor")
    meta = {
        "orchestration_id": args.id,
        "capability_actor_id": state["verifier_actor_id"],
        "capability_token_fingerprint": _token_fingerprint(args.actor_token),
        "runtime_actor": runtime_actor or None,
        "runtime_identity_attested_by_caller": bool(runtime_actor),
        "protocol": "blind-verifier-capability-v2"
    }
    model = args.model or "unspecified-subagent"
    rid = ks.create_run("verifier", model, state["task_id"], state["verifier_actor_id"], meta)
    ks.phase_a(
        verifier_run_id=rid, claim_id=state["claim_id"], statement=result["statement"],
        scope=result.get("scope") or {}, evidence_status=result["evidence_status"], uncertainty=result.get("uncertainty")
    )
    state.update({
        "verifier_run_id": rid, "verifier_model": model, "verifier_runtime_actor": runtime_actor or None,
        "phase_a_result": result, "stage": "phase-b-ready"
    })
    state["history"].append({"at": now(), "event": "phase-a-ingested", "run_id": rid, "actor_id": state["verifier_actor_id"]})
    write_state(p, state)
    claim = ks.get_claim(state["claim_id"]) or {}
    prompt = render_phase_b_prompt(state, claim)
    (p / "verifier-phase-b.prompt.md").write_text(prompt, encoding="utf-8")
    (p / "verifier-phase-b.schema.json").write_text(schema_text("verifier-phase-b.schema.json"), encoding="utf-8")
    dump({
        "orchestration_id": args.id, "stage": "phase-b-ready", "verifier_run_id": rid,
        "verifier_actor_id": state["verifier_actor_id"],
        "prompt": str(p / "verifier-phase-b.prompt.md"), "schema": str(p / "verifier-phase-b.schema.json")
    })


def cmd_phase_b(args: argparse.Namespace) -> None:
    p = run_dir(Path(args.run_root), args.id)
    state = read_state(p)
    if state["stage"] != "phase-b-ready":
        raise ValueError(f"expected phase-b-ready, got {state['stage']}")
    require_actor_token(state, "verifier", args.actor_token)
    runtime_actor = parse_runtime_actor(args.runtime_actor)
    require_runtime_actor_continuity(state.get("verifier_runtime_actor"), runtime_actor)
    result = load_json_arg(args.result)
    validate_shape("phase_b", result)
    ks.phase_b(
        verifier_run_id=state["verifier_run_id"], claim_id=state["claim_id"], verdict=result["verdict"],
        final_statement=result.get("final_statement"), existing_subject_type=result.get("existing_subject_type"),
        existing_subject_id=result.get("existing_subject_id"), notes=result.get("notes")
    )
    committed = ks.commit_claim(state["claim_id"], state["verifier_run_id"])
    state.update({
        "phase_b_result": result, "phase_b_runtime_actor": runtime_actor or None,
        "commit_result": committed, "verifier_token_consumed": True, "stage": "complete"
    })
    state["history"].append({"at": now(), "event": "phase-b-committed", "result": committed, "actor_id": state["verifier_actor_id"]})
    write_state(p, state)
    (p / "final.json").write_text(json.dumps(committed, ensure_ascii=False, indent=2), encoding="utf-8")
    dump({"orchestration_id": args.id, "stage": "complete", "result": committed})


def cmd_status(args: argparse.Namespace) -> None:
    p = run_dir(Path(args.run_root), args.id)
    state = read_state(p)
    hidden = {"evidence", "collector_token_hash", "verifier_token_hash"}
    out = {k: v for k, v in state.items() if k not in hidden}
    out["dir"] = str(p)
    dump(out)


def cmd_prompt(args: argparse.Namespace) -> None:
    p=run_dir(Path(args.run_root),args.id); state=read_state(p)
    mapping={"collector":"collector.prompt.md","phase-a":"verifier-phase-a.prompt.md","phase-b":"verifier-phase-b.prompt.md"}
    path=p/mapping[args.phase]
    if not path.exists(): raise ValueError(f"prompt not ready for {args.phase}; current stage={state['stage']}")
    print(path.read_text(encoding="utf-8"))


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description="ExteraContext two-subagent knowledge orchestration")
    p.add_argument("--run-root",default=str(DEFAULT_RUN_ROOT))
    sp=p.add_subparsers(dest="cmd",required=True)
    s=sp.add_parser("reflect",help="Create a knowledge-capture orchestration and collector prompt")
    s.add_argument("--task",required=True,help="text or @file")
    s.add_argument("--task-id")
    s.add_argument("--discovery",help="short statement/symbol discovered, for duplicate search")
    s.add_argument("--target",help="JSON or @file")
    s.add_argument("--evidence",required=True,help="JSON list/object or @file with original evidence")
    s.set_defaults(func=cmd_reflect)
    s=sp.add_parser("collector-result",help="Ingest collector JSON using the MCP-issued collector capability token")
    s.add_argument("--id",required=True); s.add_argument("--result",required=True,help="JSON or @file")
    s.add_argument("--actor-token",required=True); s.add_argument("--model")
    s.add_argument("--runtime-actor",help="optional caller-attested runtime identity JSON or @file")
    s.set_defaults(func=cmd_collector)
    s=sp.add_parser("phase-a-result",help="Ingest blind verifier Phase A using the MCP-issued verifier capability token")
    s.add_argument("--id",required=True); s.add_argument("--result",required=True,help="JSON or @file")
    s.add_argument("--actor-token",required=True); s.add_argument("--model")
    s.add_argument("--runtime-actor",help="optional caller-attested runtime identity JSON or @file")
    s.set_defaults(func=cmd_phase_a)
    s=sp.add_parser("phase-b-result",help="Ingest Phase B using the same verifier capability token and commit")
    s.add_argument("--id",required=True); s.add_argument("--result",required=True,help="JSON or @file")
    s.add_argument("--actor-token",required=True); s.add_argument("--runtime-actor",help="optional caller-attested runtime identity JSON or @file")
    s.set_defaults(func=cmd_phase_b)
    s=sp.add_parser("status"); s.add_argument("--id",required=True); s.set_defaults(func=cmd_status)
    s=sp.add_parser("prompt"); s.add_argument("--id",required=True); s.add_argument("--phase",required=True,choices=["collector","phase-a","phase-b"]); s.set_defaults(func=cmd_prompt)
    return p


def main() -> None:
    args=parser().parse_args()
    try: args.func(args)
    except (ValueError,RuntimeError,KeyError,json.JSONDecodeError) as e: raise SystemExit(f"error: {e}")

if __name__ == "__main__": main()
