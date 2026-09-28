#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SKILL_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DB = Path(os.environ.get("EXTERACONTEXT_KNOWLEDGE_DB", SKILL_ROOT / "data" / "knowledge.sqlite"))

CLAIM_KINDS = {"api", "behavior", "compatibility", "recipe", "negative-evidence", "runtime-result", "other"}
SCOPES = {"project", "target", "global"}
EVIDENCE_STATUSES = {"runtime-verified", "code", "docs", "inference", "secondary", "unavailable"}
CLAIM_STATES = {"candidate", "verified", "conflicting", "rejected", "needs-runtime", "superseded"}
VERDICTS = {"accept", "accept-with-changes", "attach-evidence", "conflict", "reject", "needs-runtime"}
RUN_ROLES = {"collector", "verifier", "main", "runtime"}
RELATIONS = {"supports", "conflicts", "refutes", "usage", "runtime"}

STATUS_RANK = {
    "unavailable": 0,
    "secondary": 1,
    "inference": 2,
    "docs": 3,
    "code": 4,
    "runtime-verified": 5,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def normalize_statement(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip().lower())
    text = re.sub(r"[^a-zа-яё0-9_.$:@+\-/ ]", "", text)
    return text


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def parse_json(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _validate(value: str, allowed: set[str], name: str) -> str:
    if value not in allowed:
        raise ValueError(f"invalid {name}: {value!r}; expected one of {sorted(allowed)}")
    return value


def connect(path: Path | None = None) -> sqlite3.Connection:
    db = path or KNOWLEDGE_DB
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


SCHEMA = r"""
CREATE TABLE IF NOT EXISTS knowledge_meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_runs(
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK(role IN ('collector','verifier','main','runtime')),
    model TEXT,
    task_id TEXT,
    session_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_claims(
    id TEXT PRIMARY KEY,
    statement TEXT NOT NULL,
    normalized_statement TEXT NOT NULL,
    api_symbol TEXT,
    kind TEXT NOT NULL CHECK(kind IN ('api','behavior','compatibility','recipe','negative-evidence','runtime-result','other')),
    scope TEXT NOT NULL CHECK(scope IN ('project','target','global')),
    client TEXT,
    platform TEXT,
    client_version TEXT,
    sdk_version TEXT,
    language TEXT,
    plugin_format TEXT,
    state TEXT NOT NULL DEFAULT 'candidate' CHECK(state IN ('candidate','verified','conflicting','rejected','needs-runtime','superseded')),
    effective_evidence_status TEXT NOT NULL CHECK(effective_evidence_status IN ('runtime-verified','code','docs','inference','secondary','unavailable')),
    created_by_run TEXT NOT NULL REFERENCES knowledge_runs(id),
    verified_by_run TEXT REFERENCES knowledge_runs(id),
    supersedes_claim_id TEXT REFERENCES knowledge_claims(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS kc_norm_idx ON knowledge_claims(normalized_statement);
CREATE INDEX IF NOT EXISTS kc_api_idx ON knowledge_claims(api_symbol);
CREATE INDEX IF NOT EXISTS kc_state_idx ON knowledge_claims(state);
CREATE INDEX IF NOT EXISTS kc_target_idx ON knowledge_claims(client, platform, client_version, sdk_version);

CREATE TABLE IF NOT EXISTS knowledge_evidence(
    id TEXT PRIMARY KEY,
    evidence_status TEXT NOT NULL CHECK(evidence_status IN ('runtime-verified','code','docs','inference','secondary','unavailable')),
    evidence_type TEXT NOT NULL,
    source_type TEXT,
    source TEXT,
    repository TEXT,
    commit_sha TEXT,
    path TEXT,
    line_range TEXT,
    url TEXT,
    excerpt TEXT,
    content_sha256 TEXT,
    client TEXT,
    platform TEXT,
    client_version TEXT,
    sdk_version TEXT,
    created_by_run TEXT NOT NULL REFERENCES knowledge_runs(id),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ke_status_idx ON knowledge_evidence(evidence_status);
CREATE INDEX IF NOT EXISTS ke_source_idx ON knowledge_evidence(source_type, repository, path);

CREATE TABLE IF NOT EXISTS knowledge_links(
    subject_type TEXT NOT NULL CHECK(subject_type IN ('claim','legacy_fact')),
    subject_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL REFERENCES knowledge_evidence(id),
    relation TEXT NOT NULL CHECK(relation IN ('supports','conflicts','refutes','usage','runtime')),
    created_by_run TEXT NOT NULL REFERENCES knowledge_runs(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY(subject_type, subject_id, evidence_id, relation)
);
CREATE INDEX IF NOT EXISTS kl_subject_idx ON knowledge_links(subject_type, subject_id);

CREATE TABLE IF NOT EXISTS knowledge_verifications(
    id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES knowledge_claims(id),
    verifier_run_id TEXT NOT NULL REFERENCES knowledge_runs(id),
    phase_a_statement TEXT NOT NULL,
    phase_a_scope_json TEXT NOT NULL DEFAULT '{}',
    phase_a_evidence_status TEXT NOT NULL CHECK(phase_a_evidence_status IN ('runtime-verified','code','docs','inference','secondary','unavailable')),
    phase_a_uncertainty TEXT,
    verdict TEXT CHECK(verdict IN ('accept','accept-with-changes','attach-evidence','conflict','reject','needs-runtime')),
    final_statement TEXT,
    existing_subject_type TEXT CHECK(existing_subject_type IN ('claim','legacy_fact')),
    existing_subject_id TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(claim_id, verifier_run_id)
);
CREATE INDEX IF NOT EXISTS kv_claim_idx ON knowledge_verifications(claim_id);

CREATE TABLE IF NOT EXISTS knowledge_conflicts(
    id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES knowledge_claims(id),
    other_subject_type TEXT NOT NULL CHECK(other_subject_type IN ('claim','legacy_fact')),
    other_subject_id TEXT NOT NULL,
    evidence_id TEXT REFERENCES knowledge_evidence(id),
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
    notes TEXT,
    created_by_run TEXT NOT NULL REFERENCES knowledge_runs(id),
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS knowledge_revisions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL,
    old_statement TEXT,
    new_statement TEXT,
    old_state TEXT,
    new_state TEXT,
    actor_run_id TEXT,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_claims_fts USING fts5(
    id UNINDEXED,
    statement,
    api_symbol,
    kind,
    client,
    platform,
    client_version,
    sdk_version,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS kc_candidate_insert_guard
BEFORE INSERT ON knowledge_claims
WHEN NEW.state <> 'candidate'
BEGIN
    SELECT RAISE(ABORT, 'new knowledge claims must start as candidate');
END;

CREATE TRIGGER IF NOT EXISTS kv_independent_verifier_insert_guard
BEFORE INSERT ON knowledge_verifications
BEGIN
    SELECT CASE WHEN (SELECT role FROM knowledge_runs WHERE id=NEW.verifier_run_id) <> 'verifier'
        THEN RAISE(ABORT, 'verification run must have verifier role') END;
    SELECT CASE WHEN NEW.verifier_run_id = (SELECT created_by_run FROM knowledge_claims WHERE id=NEW.claim_id)
        THEN RAISE(ABORT, 'collector cannot verify its own claim') END;
END;

CREATE TRIGGER IF NOT EXISTS kv_independent_verifier_update_guard
BEFORE UPDATE OF verdict ON knowledge_verifications
WHEN NEW.verdict IS NOT NULL
BEGIN
    SELECT CASE WHEN length(trim(COALESCE(NEW.phase_a_statement,''))) = 0
        THEN RAISE(ABORT, 'phase A blind extraction is required before verdict') END;
    SELECT CASE WHEN NEW.verifier_run_id = (SELECT created_by_run FROM knowledge_claims WHERE id=NEW.claim_id)
        THEN RAISE(ABORT, 'collector cannot verify its own claim') END;
END;

CREATE TRIGGER IF NOT EXISTS kc_verified_guard
BEFORE UPDATE OF state ON knowledge_claims
WHEN NEW.state='verified' AND OLD.state <> 'verified'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM knowledge_verifications v
        JOIN knowledge_runs r ON r.id=v.verifier_run_id
        WHERE v.claim_id=NEW.id
          AND v.verdict IN ('accept','accept-with-changes')
          AND length(trim(v.phase_a_statement)) > 0
          AND r.role='verifier'
          AND v.verifier_run_id <> NEW.created_by_run
    ) THEN RAISE(ABORT, 'verified state requires independent accepted verification') END;
END;

CREATE TRIGGER IF NOT EXISTS kc_conflict_guard
BEFORE UPDATE OF state ON knowledge_claims
WHEN NEW.state='conflicting' AND OLD.state <> 'conflicting'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM knowledge_verifications v
        WHERE v.claim_id=NEW.id AND v.verdict='conflict'
          AND v.verifier_run_id <> NEW.created_by_run
    ) THEN RAISE(ABORT, 'conflicting state requires verifier conflict verdict') END;
END;

CREATE TRIGGER IF NOT EXISTS kc_runtime_guard
BEFORE UPDATE OF state ON knowledge_claims
WHEN NEW.state='needs-runtime' AND OLD.state <> 'needs-runtime'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM knowledge_verifications v
        WHERE v.claim_id=NEW.id AND v.verdict='needs-runtime'
          AND v.verifier_run_id <> NEW.created_by_run
    ) THEN RAISE(ABORT, 'needs-runtime state requires verifier verdict') END;
END;

CREATE TRIGGER IF NOT EXISTS kc_reject_guard
BEFORE UPDATE OF state ON knowledge_claims
WHEN NEW.state='rejected' AND OLD.state <> 'rejected'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM knowledge_verifications v
        WHERE v.claim_id=NEW.id AND v.verdict='reject'
          AND v.verifier_run_id <> NEW.created_by_run
    ) THEN RAISE(ABORT, 'rejected state requires verifier verdict') END;
END;

CREATE TRIGGER IF NOT EXISTS kc_superseded_guard
BEFORE UPDATE OF state ON knowledge_claims
WHEN NEW.state='superseded' AND OLD.state <> 'superseded'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM knowledge_verifications v
        WHERE v.claim_id=NEW.id AND v.verdict='attach-evidence'
          AND v.verifier_run_id <> NEW.created_by_run
    ) THEN RAISE(ABORT, 'superseded state requires attach-evidence verdict') END;
END;

CREATE TRIGGER IF NOT EXISTS kl_write_guard
BEFORE INSERT ON knowledge_links
BEGIN
    -- Evidence linked to a fresh candidate must come from that candidate's collector run.
    SELECT CASE WHEN NEW.subject_type='claim'
        AND (SELECT state FROM knowledge_claims WHERE id=NEW.subject_id)='candidate'
        AND NEW.created_by_run <> (SELECT created_by_run FROM knowledge_claims WHERE id=NEW.subject_id)
        THEN RAISE(ABORT, 'candidate evidence must be linked by its collector run') END;

    -- Evidence linked directly to a trusted/conflicting claim or legacy fact is allowed
    -- only for a runtime harness, or after an explicit attach-evidence verifier verdict.
    SELECT CASE WHEN (
          NEW.subject_type='legacy_fact'
          OR (NEW.subject_type='claim' AND (SELECT state FROM knowledge_claims WHERE id=NEW.subject_id) IN ('verified','conflicting'))
        )
        AND NOT (
          ((SELECT role FROM knowledge_runs WHERE id=NEW.created_by_run)='runtime'
           AND (SELECT evidence_type FROM knowledge_evidence WHERE id=NEW.evidence_id)='runtime-test')
          OR EXISTS (
              SELECT 1 FROM knowledge_verifications v
              JOIN knowledge_links src
                ON src.subject_type='claim' AND src.subject_id=v.claim_id AND src.evidence_id=NEW.evidence_id
              WHERE v.verdict='attach-evidence'
                AND v.verifier_run_id=NEW.created_by_run
                AND v.existing_subject_type=NEW.subject_type
                AND v.existing_subject_id=NEW.subject_id
          )
        )
        THEN RAISE(ABORT, 'trusted evidence links require runtime provenance or attach-evidence verification') END;
END;

CREATE TRIGGER IF NOT EXISTS kc_evidence_status_guard
BEFORE UPDATE OF effective_evidence_status ON knowledge_claims
WHEN NEW.effective_evidence_status <> OLD.effective_evidence_status
BEGIN
    SELECT CASE WHEN
        (CASE NEW.effective_evidence_status
            WHEN 'unavailable' THEN 0 WHEN 'secondary' THEN 1 WHEN 'inference' THEN 2
            WHEN 'docs' THEN 3 WHEN 'code' THEN 4 WHEN 'runtime-verified' THEN 5 ELSE 99 END)
        > COALESCE((
            SELECT MAX(CASE e.evidence_status
                WHEN 'unavailable' THEN 0 WHEN 'secondary' THEN 1 WHEN 'inference' THEN 2
                WHEN 'docs' THEN 3 WHEN 'code' THEN 4 WHEN 'runtime-verified' THEN 5 ELSE -1 END)
            FROM knowledge_links l JOIN knowledge_evidence e ON e.id=l.evidence_id
            WHERE l.subject_type='claim' AND l.subject_id=NEW.id
        ), -1)
        THEN RAISE(ABORT, 'claim evidence status cannot exceed linked evidence') END;
END;

CREATE TRIGGER IF NOT EXISTS kc_revision_insert
AFTER INSERT ON knowledge_claims
BEGIN
    INSERT INTO knowledge_revisions(claim_id,new_statement,new_state,actor_run_id,reason,created_at)
    VALUES(NEW.id,NEW.statement,NEW.state,NEW.created_by_run,'candidate-created',NEW.created_at);
END;

CREATE TRIGGER IF NOT EXISTS kc_revision_update
AFTER UPDATE OF statement,state ON knowledge_claims
WHEN OLD.statement <> NEW.statement OR OLD.state <> NEW.state
BEGIN
    INSERT INTO knowledge_revisions(claim_id,old_statement,new_statement,old_state,new_state,actor_run_id,reason,created_at)
    VALUES(NEW.id,OLD.statement,NEW.statement,OLD.state,NEW.state,COALESCE(NEW.verified_by_run,NEW.created_by_run),'verification-transition',NEW.updated_at);
END;

CREATE TRIGGER IF NOT EXISTS kc_fts_insert
AFTER INSERT ON knowledge_claims
WHEN NEW.state IN ('verified','conflicting')
BEGIN
    INSERT INTO knowledge_claims_fts(id,statement,api_symbol,kind,client,platform,client_version,sdk_version)
    VALUES(NEW.id,NEW.statement,COALESCE(NEW.api_symbol,''),NEW.kind,COALESCE(NEW.client,''),COALESCE(NEW.platform,''),COALESCE(NEW.client_version,''),COALESCE(NEW.sdk_version,''));
END;

CREATE TRIGGER IF NOT EXISTS kc_fts_update
AFTER UPDATE OF statement,api_symbol,kind,client,platform,client_version,sdk_version,state ON knowledge_claims
BEGIN
    DELETE FROM knowledge_claims_fts WHERE id=OLD.id;
    INSERT INTO knowledge_claims_fts(id,statement,api_symbol,kind,client,platform,client_version,sdk_version)
    SELECT NEW.id,NEW.statement,COALESCE(NEW.api_symbol,''),NEW.kind,COALESCE(NEW.client,''),COALESCE(NEW.platform,''),COALESCE(NEW.client_version,''),COALESCE(NEW.sdk_version,'')
    WHERE NEW.state IN ('verified','conflicting');
END;
"""


def init_db(path: Path | None = None) -> Path:
    db = path or KNOWLEDGE_DB
    c = connect(db)
    c.executescript(SCHEMA)
    c.execute("INSERT OR IGNORE INTO knowledge_meta(key,value) VALUES('schema_version','1')")
    c.commit()
    c.close()
    return db


def create_run(role: str, model: str | None = None, task_id: str | None = None,
               session_id: str | None = None, metadata: dict[str, Any] | None = None,
               db_path: Path | None = None) -> str:
    _validate(role, RUN_ROLES, "role")
    init_db(db_path)
    rid = new_id("run")
    c = connect(db_path)
    c.execute(
        "INSERT INTO knowledge_runs(id,role,model,task_id,session_id,metadata_json,created_at) VALUES(?,?,?,?,?,?,?)",
        (rid, role, model, task_id, session_id, json_text(metadata or {}), now_iso()),
    )
    c.commit(); c.close()
    return rid


def _run(c: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    row = c.execute("SELECT * FROM knowledge_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise ValueError(f"unknown run: {run_id}")
    return row


def _claim(c: sqlite3.Connection, claim_id: str) -> sqlite3.Row:
    row = c.execute("SELECT * FROM knowledge_claims WHERE id=?", (claim_id,)).fetchone()
    if not row:
        raise ValueError(f"unknown claim: {claim_id}")
    return row


def _evidence_digest(e: dict[str, Any]) -> str:
    selected = {k: e.get(k) for k in ("source", "repository", "commit_sha", "path", "line_range", "url", "excerpt")}
    return hashlib.sha256(json_text(selected).encode("utf-8")).hexdigest()


def propose_claim(*, run_id: str, statement: str, kind: str, scope: str,
                  evidence_status: str, api_symbol: str | None = None,
                  client: str | None = None, platform: str | None = None,
                  client_version: str | None = None, sdk_version: str | None = None,
                  language: str | None = None, plugin_format: str | None = None,
                  evidence: Iterable[dict[str, Any]] = (), db_path: Path | None = None) -> str:
    _validate(kind, CLAIM_KINDS, "kind")
    _validate(scope, SCOPES, "scope")
    _validate(evidence_status, EVIDENCE_STATUSES, "evidence_status")
    if not statement.strip():
        raise ValueError("claim statement cannot be empty")
    init_db(db_path)
    c = connect(db_path)
    run = _run(c, run_id)
    if run["role"] != "collector":
        raise ValueError("propose_claim requires a collector run")
    cid = new_id("claim")
    ts = now_iso()
    c.execute(
        """INSERT INTO knowledge_claims(
            id,statement,normalized_statement,api_symbol,kind,scope,client,platform,client_version,sdk_version,language,plugin_format,
            state,effective_evidence_status,created_by_run,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, statement.strip(), normalize_statement(statement), api_symbol, kind, scope, client, platform,
         client_version, sdk_version, language, plugin_format, "candidate", evidence_status, run_id, ts, ts),
    )
    for ev in evidence:
        ev_status = ev.get("evidence_status") or evidence_status
        _validate(ev_status, EVIDENCE_STATUSES, "evidence.evidence_status")
        eid = new_id("ev")
        excerpt = ev.get("excerpt")
        c.execute(
            """INSERT INTO knowledge_evidence(
                id,evidence_status,evidence_type,source_type,source,repository,commit_sha,path,line_range,url,excerpt,content_sha256,
                client,platform,client_version,sdk_version,created_by_run,metadata_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (eid, ev_status, ev.get("evidence_type") or "source", ev.get("source_type"), ev.get("source"), ev.get("repository"),
             ev.get("commit_sha") or ev.get("commit"), ev.get("path"), ev.get("line_range") or ev.get("lines"), ev.get("url"), excerpt,
             ev.get("content_sha256") or _evidence_digest(ev), ev.get("client") or client, ev.get("platform") or platform,
             ev.get("client_version") or client_version, ev.get("sdk_version") or sdk_version, run_id, json_text(ev.get("metadata") or {}), ts),
        )
        c.execute(
            "INSERT INTO knowledge_links(subject_type,subject_id,evidence_id,relation,created_by_run,created_at) VALUES('claim',?,?,?,?,?)",
            (cid, eid, ev.get("relation") or "supports", run_id, ts),
        )
    c.commit(); c.close()
    return cid


def phase_a(*, verifier_run_id: str, claim_id: str, statement: str,
            scope: dict[str, Any] | None, evidence_status: str,
            uncertainty: str | None = None, db_path: Path | None = None) -> str:
    _validate(evidence_status, EVIDENCE_STATUSES, "evidence_status")
    init_db(db_path)
    c = connect(db_path)
    run = _run(c, verifier_run_id)
    claim = _claim(c, claim_id)
    if verifier_run_id == claim["created_by_run"]:
        raise ValueError("collector cannot verify its own claim")
    if run["role"] != "verifier":
        raise ValueError("phase_a requires a verifier run")
    if not statement.strip():
        raise ValueError("phase A statement cannot be empty")
    vid = new_id("verify")
    ts = now_iso()
    c.execute(
        """INSERT INTO knowledge_verifications(
            id,claim_id,verifier_run_id,phase_a_statement,phase_a_scope_json,phase_a_evidence_status,phase_a_uncertainty,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (vid, claim_id, verifier_run_id, statement.strip(), json_text(scope or {}), evidence_status, uncertainty, ts, ts),
    )
    c.commit(); c.close()
    return vid


def phase_b(*, verifier_run_id: str, claim_id: str, verdict: str,
            final_statement: str | None = None,
            existing_subject_type: str | None = None, existing_subject_id: str | None = None,
            notes: str | None = None, db_path: Path | None = None) -> str:
    _validate(verdict, VERDICTS, "verdict")
    if existing_subject_type and existing_subject_type not in {"claim", "legacy_fact"}:
        raise ValueError("existing_subject_type must be claim or legacy_fact")
    init_db(db_path)
    c = connect(db_path)
    _run(c, verifier_run_id)
    claim = _claim(c, claim_id)
    if verifier_run_id == claim["created_by_run"]:
        raise ValueError("collector cannot verify its own claim")
    row = c.execute(
        "SELECT * FROM knowledge_verifications WHERE claim_id=? AND verifier_run_id=?",
        (claim_id, verifier_run_id),
    ).fetchone()
    if not row:
        raise ValueError("phase A must be recorded before phase B")
    if row["verdict"] is not None:
        raise ValueError("phase B verdict already recorded")
    if verdict in {"attach-evidence", "conflict"} and not (existing_subject_type and existing_subject_id):
        raise ValueError(f"{verdict} requires existing_subject_type and existing_subject_id")
    c.execute(
        """UPDATE knowledge_verifications
           SET verdict=?, final_statement=?, existing_subject_type=?, existing_subject_id=?, notes=?, updated_at=?
           WHERE id=?""",
        (verdict, final_statement, existing_subject_type, existing_subject_id, notes, now_iso(), row["id"]),
    )
    c.commit(); c.close()
    return row["id"]


def _best_status(c: sqlite3.Connection, subject_type: str, subject_id: str) -> str:
    rows = c.execute(
        """SELECT e.evidence_status FROM knowledge_links l JOIN knowledge_evidence e ON e.id=l.evidence_id
           WHERE l.subject_type=? AND l.subject_id=?""", (subject_type, subject_id)
    ).fetchall()
    if not rows:
        return "inference"
    return max((r[0] for r in rows), key=lambda s: STATUS_RANK.get(s, -1))


def commit_claim(claim_id: str, verifier_run_id: str | None = None, db_path: Path | None = None) -> dict[str, Any]:
    init_db(db_path)
    c = connect(db_path)
    claim = _claim(c, claim_id)
    q = "SELECT * FROM knowledge_verifications WHERE claim_id=? AND verdict IS NOT NULL"
    params: list[Any] = [claim_id]
    if verifier_run_id:
        q += " AND verifier_run_id=?"; params.append(verifier_run_id)
    q += " ORDER BY updated_at DESC LIMIT 1"
    v = c.execute(q, params).fetchone()
    if not v:
        raise ValueError("claim has no completed independent verification")
    verdict = v["verdict"]
    ts = now_iso()
    result: dict[str, Any] = {"claim_id": claim_id, "verdict": verdict}
    if verdict in {"accept", "accept-with-changes"}:
        statement = v["final_statement"] if verdict == "accept-with-changes" and v["final_statement"] else claim["statement"]
        phase_status = v["phase_a_evidence_status"]
        current_status = _best_status(c, "claim", claim_id)
        # Never let the verifier upgrade evidence beyond what either its blind extraction or linked evidence supports.
        effective = min((phase_status, current_status), key=lambda s: STATUS_RANK.get(s, 0))
        c.execute(
            """UPDATE knowledge_claims SET statement=?, normalized_statement=?, state='verified', effective_evidence_status=?,
               verified_by_run=?, updated_at=? WHERE id=?""",
            (statement, normalize_statement(statement), effective, v["verifier_run_id"], ts, claim_id),
        )
        result.update({"state": "verified", "statement": statement, "effective_evidence_status": effective})
    elif verdict == "needs-runtime":
        c.execute("UPDATE knowledge_claims SET state='needs-runtime', verified_by_run=?, updated_at=? WHERE id=?",
                  (v["verifier_run_id"], ts, claim_id))
        result["state"] = "needs-runtime"
    elif verdict == "reject":
        c.execute("UPDATE knowledge_claims SET state='rejected', verified_by_run=?, updated_at=? WHERE id=?",
                  (v["verifier_run_id"], ts, claim_id))
        result["state"] = "rejected"
    elif verdict == "attach-evidence":
        target_type, target_id = v["existing_subject_type"], v["existing_subject_id"]
        links = c.execute("SELECT evidence_id,relation FROM knowledge_links WHERE subject_type='claim' AND subject_id=?", (claim_id,)).fetchall()
        for link in links:
            c.execute(
                "INSERT OR IGNORE INTO knowledge_links(subject_type,subject_id,evidence_id,relation,created_by_run,created_at) VALUES(?,?,?,?,?,?)",
                (target_type, target_id, link["evidence_id"], link["relation"], v["verifier_run_id"], ts),
            )
        c.execute("UPDATE knowledge_claims SET state='superseded', verified_by_run=?, updated_at=? WHERE id=?",
                  (v["verifier_run_id"], ts, claim_id))
        result.update({"state": "superseded", "attached_to": f"{target_type}:{target_id}", "evidence_count": len(links)})
    elif verdict == "conflict":
        conflict_id = new_id("conflict")
        c.execute(
            """INSERT INTO knowledge_conflicts(id,claim_id,other_subject_type,other_subject_id,status,notes,created_by_run,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (conflict_id, claim_id, v["existing_subject_type"], v["existing_subject_id"], "open", v["notes"], v["verifier_run_id"], ts),
        )
        c.execute("UPDATE knowledge_claims SET state='conflicting', verified_by_run=?, updated_at=? WHERE id=?",
                  (v["verifier_run_id"], ts, claim_id))
        result.update({"state": "conflicting", "conflict_id": conflict_id})
    c.commit(); c.close()
    return result


def add_evidence(*, run_id: str, subject_type: str, subject_id: str, evidence_status: str,
                 evidence_type: str = "source", source_type: str | None = None,
                 source: str | None = None, repository: str | None = None, commit_sha: str | None = None,
                 path: str | None = None, line_range: str | None = None, url: str | None = None,
                 excerpt: str | None = None, relation: str = "supports",
                 client: str | None = None, platform: str | None = None,
                 client_version: str | None = None, sdk_version: str | None = None,
                 metadata: dict[str, Any] | None = None, db_path: Path | None = None) -> str:
    _validate(evidence_status, EVIDENCE_STATUSES, "evidence_status")
    _validate(relation, RELATIONS, "relation")
    if subject_type not in {"claim", "legacy_fact"}:
        raise ValueError("subject_type must be claim or legacy_fact")
    init_db(db_path)
    c = connect(db_path)
    run = _run(c, run_id)
    if run["role"] != "runtime" or evidence_type != "runtime-test":
        c.close()
        raise ValueError("direct evidence append is reserved for runtime-test evidence from a runtime run; model/human evidence must use propose + independent verification")
    if subject_type == "claim":
        _claim(c, subject_id)
    ev = {
        "source": source, "repository": repository, "commit_sha": commit_sha,
        "path": path, "line_range": line_range, "url": url, "excerpt": excerpt,
    }
    eid = new_id("ev"); ts = now_iso()
    c.execute(
        """INSERT INTO knowledge_evidence(id,evidence_status,evidence_type,source_type,source,repository,commit_sha,path,line_range,url,excerpt,content_sha256,
           client,platform,client_version,sdk_version,created_by_run,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (eid,evidence_status,evidence_type,source_type,source,repository,commit_sha,path,line_range,url,excerpt,_evidence_digest(ev),
         client,platform,client_version,sdk_version,run_id,json_text(metadata or {}),ts),
    )
    c.execute("INSERT INTO knowledge_links(subject_type,subject_id,evidence_id,relation,created_by_run,created_at) VALUES(?,?,?,?,?,?)",
              (subject_type,subject_id,eid,relation,run_id,ts))
    # Evidence can strengthen a verified claim but never silently promote a candidate.
    if subject_type == "claim":
        claim = _claim(c, subject_id)
        if claim["state"] in {"verified", "conflicting"}:
            best = _best_status(c, "claim", subject_id)
            c.execute("UPDATE knowledge_claims SET effective_evidence_status=?, updated_at=? WHERE id=?", (best, ts, subject_id))
    c.commit(); c.close()
    return eid


def record_runtime_result(*, run_id: str, subject_type: str, subject_id: str, passed: bool,
                          test_id: str, runs: int = 1, client: str | None = None,
                          platform: str | None = None, client_version: str | None = None,
                          sdk_version: str | None = None, log_excerpt: str | None = None,
                          metadata: dict[str, Any] | None = None, db_path: Path | None = None) -> str:
    init_db(db_path)
    c = connect(db_path)
    run = _run(c, run_id)
    c.close()
    if run["role"] != "runtime":
        raise ValueError("record_runtime_result requires a runtime run; human/model observations must use the collector+verifier protocol")
    relation = "runtime" if passed else "conflicts"
    return add_evidence(
        run_id=run_id, subject_type=subject_type, subject_id=subject_id,
        evidence_status="runtime-verified" if passed else "code",
        evidence_type="runtime-test", source_type="runtime", source=test_id,
        excerpt=log_excerpt, relation=relation, client=client, platform=platform,
        client_version=client_version, sdk_version=sdk_version,
        metadata={**(metadata or {}), "passed": passed, "runs": runs, "test_id": test_id},
        db_path=db_path,
    )


def claim_row_to_dict(c: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["evidence"] = [dict(r) for r in c.execute(
        """SELECT e.*, l.relation FROM knowledge_links l JOIN knowledge_evidence e ON e.id=l.evidence_id
           WHERE l.subject_type='claim' AND l.subject_id=? ORDER BY e.created_at""", (row["id"],)
    ).fetchall()]
    d["verifications"] = [dict(r) for r in c.execute(
        "SELECT * FROM knowledge_verifications WHERE claim_id=? ORDER BY created_at", (row["id"],)
    ).fetchall()]
    d["conflicts"] = [dict(r) for r in c.execute(
        "SELECT * FROM knowledge_conflicts WHERE claim_id=? ORDER BY created_at", (row["id"],)
    ).fetchall()]
    return d


def get_claim(claim_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    init_db(db_path)
    c = connect(db_path)
    row = c.execute("SELECT * FROM knowledge_claims WHERE id=?", (claim_id,)).fetchone()
    out = claim_row_to_dict(c, row) if row else None
    c.close(); return out


def find_duplicates(statement: str, limit: int = 10, db_path: Path | None = None) -> list[dict[str, Any]]:
    init_db(db_path)
    norm = normalize_statement(statement)
    c = connect(db_path)
    exact = c.execute(
        "SELECT * FROM knowledge_claims WHERE normalized_statement=? ORDER BY updated_at DESC LIMIT ?", (norm, limit)
    ).fetchall()
    if exact:
        out = [dict(r) for r in exact]; c.close(); return out
    toks = [t for t in re.findall(r"[A-Za-zА-Яа-яЁё0-9_.$:@+-]+", statement.lower()) if len(t) >= 3][:12]
    if not toks:
        c.close(); return []
    match = " OR ".join(f'"{re.sub(r"[^A-Za-zА-Яа-яЁё0-9_]", "", t)}"*' for t in toks)
    rows = c.execute(
        """SELECT c.*, bm25(knowledge_claims_fts,4.0,6.0,1.0,1.0,1.0,1.0,1.0) bm
           FROM knowledge_claims_fts f JOIN knowledge_claims c ON c.id=f.id
           WHERE knowledge_claims_fts MATCH ? ORDER BY bm LIMIT ?""", (match, limit)
    ).fetchall()
    out = [dict(r) for r in rows]; c.close(); return out


def search_verified(query: str, limit: int = 12, db_path: Path | None = None) -> list[dict[str, Any]]:
    init_db(db_path)
    toks = [t for t in re.findall(r"[A-Za-zА-Яа-яЁё0-9_.$:@+-]+", query.lower()) if len(t) >= 2][:20]
    if not toks:
        return []
    safe = [re.sub(r"[^A-Za-zА-Яа-яЁё0-9_]", "", t) for t in toks]
    safe = [t for t in safe if len(t) >= 2]
    if not safe:
        return []
    match = " OR ".join(f'"{t}"*' for t in safe)
    c = connect(db_path)
    rows = c.execute(
        """SELECT c.*, bm25(knowledge_claims_fts,4.0,7.0,1.0,1.0,1.0,1.0,1.0) AS bm
           FROM knowledge_claims_fts f JOIN knowledge_claims c ON c.id=f.id
           WHERE knowledge_claims_fts MATCH ? AND c.state IN ('verified','conflicting')
           ORDER BY bm LIMIT ?""", (match, max(limit * 3, 30))
    ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        ev = c.execute(
            """SELECT e.*, l.relation FROM knowledge_links l JOIN knowledge_evidence e ON e.id=l.evidence_id
               WHERE l.subject_type='claim' AND l.subject_id=? ORDER BY e.created_at DESC LIMIT 1""", (row["id"],)
        ).fetchone()
        if ev:
            d["top_evidence"] = dict(ev)
        out.append(d)
    c.close(); return out[:limit]


def api_search(symbol: str, limit: int = 20, db_path: Path | None = None) -> list[dict[str, Any]]:
    init_db(db_path)
    c = connect(db_path)
    like = f"%{symbol}%"
    rows = c.execute(
        """SELECT * FROM knowledge_claims WHERE state IN ('verified','conflicting')
           AND (api_symbol LIKE ? COLLATE NOCASE OR statement LIKE ? COLLATE NOCASE)
           ORDER BY updated_at DESC LIMIT ?""", (like, like, limit)
    ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        ev = c.execute(
            """SELECT e.*, l.relation FROM knowledge_links l JOIN knowledge_evidence e ON e.id=l.evidence_id
               WHERE l.subject_type='claim' AND l.subject_id=? ORDER BY e.created_at DESC LIMIT 1""", (row["id"],)
        ).fetchone()
        if ev:
            d["top_evidence"] = dict(ev)
        out.append(d)
    c.close(); return out


def stats(db_path: Path | None = None) -> dict[str, Any]:
    init_db(db_path)
    c = connect(db_path)
    states = {r[0]: r[1] for r in c.execute("SELECT state,COUNT(*) FROM knowledge_claims GROUP BY state")}
    verdicts = {r[0]: r[1] for r in c.execute("SELECT COALESCE(verdict,'phase-a-only'),COUNT(*) FROM knowledge_verifications GROUP BY verdict")}
    out = {
        "db": str(db_path or KNOWLEDGE_DB),
        "schema_version": c.execute("SELECT value FROM knowledge_meta WHERE key='schema_version'").fetchone()[0],
        "runs": c.execute("SELECT COUNT(*) FROM knowledge_runs").fetchone()[0],
        "claims": c.execute("SELECT COUNT(*) FROM knowledge_claims").fetchone()[0],
        "evidence": c.execute("SELECT COUNT(*) FROM knowledge_evidence").fetchone()[0],
        "conflicts_open": c.execute("SELECT COUNT(*) FROM knowledge_conflicts WHERE status='open'").fetchone()[0],
        "states": states,
        "verdicts": verdicts,
    }
    c.close(); return out
