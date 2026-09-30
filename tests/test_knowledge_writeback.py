#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import knowledge_store as ks  # noqa: E402


def expect_error(fn, needle: str):
    try:
        fn()
    except Exception as e:
        assert needle.lower() in str(e).lower(), (needle, str(e))
        return
    raise AssertionError(f"expected error containing: {needle}")


with tempfile.TemporaryDirectory() as td:
    db = Path(td) / "knowledge.sqlite"
    ks.init_db(db)

    collector = ks.create_run("collector", "cheap-model", "task-1", db_path=db)
    verifier = ks.create_run("verifier", "cheap-model", "task-1", db_path=db)
    runtime = ks.create_run("runtime", "device-harness", "task-1", db_path=db)

    claim = ks.propose_claim(
        run_id=collector,
        statement="on_send_message_hook receives the triggering account.",
        api_symbol="on_send_message_hook",
        kind="behavior",
        scope="target",
        evidence_status="code",
        client="ExteraGram",
        platform="Android",
        client_version="12.10.1",
        sdk_version="1.4.5.5",
        evidence=[{
            "evidence_status": "code",
            "evidence_type": "source",
            "source_type": "official-sdk",
            "client": "ExteraGram", "platform": "Android",
            "client_version": "12.10.1", "sdk_version": "1.4.5.5",
            "repository": "exteraSquad/plugins-pysdk-builds",
            "commit": "abc123",
            "path": "base_plugin.py",
            "lines": "10-13",
            "excerpt": "def on_send_message_hook(self, account, params): ...",
        }],
        db_path=db,
    )

    # Candidates never enter ordinary trusted retrieval.
    assert ks.search_verified("on_send_message_hook", db_path=db) == []

    # Same-run self-verification is impossible even if a caller tries to misuse the API.
    expect_error(
        lambda: ks.phase_a(
            verifier_run_id=collector,
            claim_id=claim,
            statement="self review",
            scope={},
            evidence_status="code",
            db_path=db,
        ),
        "cannot verify",
    )

    # A direct SQL state flip is blocked by the database trigger, not just Python code.
    con = ks.connect(db)
    try:
        try:
            con.execute("UPDATE knowledge_claims SET state='verified' WHERE id=?", (claim,))
            con.commit()
        except sqlite3.IntegrityError as e:
            assert "independent accepted verification" in str(e)
            con.rollback()
        else:
            raise AssertionError("direct verified transition unexpectedly succeeded")
    finally:
        con.close()

    # Phase B is impossible until the verifier has committed blind Phase A.
    expect_error(
        lambda: ks.phase_b(
            verifier_run_id=verifier,
            claim_id=claim,
            verdict="accept",
            db_path=db,
        ),
        "phase A",
    )

    ks.phase_a(
        verifier_run_id=verifier,
        claim_id=claim,
        statement="The hook callback exposes an account parameter for the triggering send operation.",
        scope={"client": "ExteraGram", "platform": "Android", "client_version": "12.10.1", "sdk_version": "1.4.5.5"},
        evidence_status="code",
        uncertainty="Source evidence only; runtime behavior is not proven.",
        db_path=db,
    )
    ks.phase_b(
        verifier_run_id=verifier,
        claim_id=claim,
        verdict="accept-with-changes",
        final_statement="on_send_message_hook receives the account for the triggering send operation.",
        notes="Narrowed wording to what the signature directly supports.",
        db_path=db,
    )
    committed = ks.commit_claim(claim, verifier, db_path=db)
    assert committed["state"] == "verified", committed
    assert committed["effective_evidence_status"] == "code", committed

    hits = ks.search_verified("outgoing message account hook", db_path=db)
    assert any(x["id"] == claim for x in hits), hits

    # Only a runtime-role run may append machine runtime evidence directly.
    expect_error(
        lambda: ks.record_runtime_result(
            run_id=collector,
            subject_type="claim",
            subject_id=claim,
            passed=True,
            test_id="bad-bypass",
            db_path=db,
        ),
        "runtime run",
    )

    ks.record_runtime_result(
        run_id=runtime,
        subject_type="claim",
        subject_id=claim,
        passed=True,
        test_id="outgoing-hook-basic",
        runs=3,
        client="ExteraGram",
        platform="Android",
        client_version="12.10.1",
        sdk_version="1.4.5.5",
        log_excerpt="PASS x3",
        db_path=db,
    )
    assert ks.get_claim(claim, db_path=db)["effective_evidence_status"] == "runtime-verified"

    # Duplicate candidate can be independently classified as evidence for the existing claim.
    collector2 = ks.create_run("collector", "cheap-model", "task-2", db_path=db)
    verifier2 = ks.create_run("verifier", "cheap-model", "task-2", db_path=db)
    dup = ks.propose_claim(
        run_id=collector2,
        statement="Outgoing send hook supplies the triggering account.",
        kind="behavior",
        scope="target",
        evidence_status="code",
        api_symbol="on_send_message_hook",
        client="ExteraGram",
        platform="Android",
        client_version="12.10.1",
        sdk_version="1.4.5.5",
        evidence=[{"source_type": "target-code", "repository": "example/plugin", "path": "plugin.py", "lines": "40-45",
                   "client": "ExteraGram", "platform": "Android", "client_version": "12.10.1", "sdk_version": "1.4.5.5"}],
        db_path=db,
    )
    ks.phase_a(
        verifier_run_id=verifier2,
        claim_id=dup,
        statement="The plugin usage independently shows the outgoing hook receiving account.",
        scope={"client": "ExteraGram", "platform": "Android", "client_version": "12.10.1", "sdk_version": "1.4.5.5"},
        evidence_status="code",
        db_path=db,
    )
    ks.phase_b(
        verifier_run_id=verifier2,
        claim_id=dup,
        verdict="attach-evidence",
        existing_subject_type="claim",
        existing_subject_id=claim,
        db_path=db,
    )
    attached = ks.commit_claim(dup, verifier2, db_path=db)
    assert attached["state"] == "superseded", attached
    assert attached["evidence_count"] == 1, attached

    # A conflicting candidate is preserved, not silently merged away.
    collector3 = ks.create_run("collector", "cheap-model", "task-3", db_path=db)
    verifier3 = ks.create_run("verifier", "cheap-model", "task-3", db_path=db)
    conflict_claim = ks.propose_claim(
        run_id=collector3,
        statement="on_send_message_hook does not receive an account parameter on 12.11 beta.",
        kind="compatibility",
        scope="target",
        evidence_status="code",
        api_symbol="on_send_message_hook",
        client="ExteraGram",
        platform="Android",
        client_version="12.11-beta",
        sdk_version="1.4.5.5",
        evidence=[{"source_type": "target-code", "path": "beta.py", "excerpt": "def on_send_message_hook(self, params): ...",
                   "client": "ExteraGram", "platform": "Android", "client_version": "12.11-beta", "sdk_version": "1.4.5.5"}],
        db_path=db,
    )
    ks.phase_a(
        verifier_run_id=verifier3,
        claim_id=conflict_claim,
        statement="The supplied beta source shows a different callback signature without account.",
        scope={"client": "ExteraGram", "platform": "Android", "client_version": "12.11-beta", "sdk_version": "1.4.5.5"},
        evidence_status="code",
        db_path=db,
    )
    ks.phase_b(
        verifier_run_id=verifier3,
        claim_id=conflict_claim,
        verdict="conflict",
        existing_subject_type="claim",
        existing_subject_id=claim,
        notes="Possible version boundary.",
        db_path=db,
    )
    conflict = ks.commit_claim(conflict_claim, verifier3, db_path=db)
    assert conflict["state"] == "conflicting", conflict
    assert ks.stats(db)["conflicts_open"] == 1

    # Integration: normal query.py sees verified agent knowledge via the mutable DB.
    env = dict(os.environ)
    env["EXTERACONTEXT_KNOWLEDGE_DB"] = str(db)
    output = subprocess.check_output(
        [sys.executable, str(SCRIPTS / "query.py"), "api", "on_send_message_hook", "--format", "json"],
        env=env,
        text=True,
        encoding="utf-8",
    )
    query_rows = json.loads(output)
    assert any(x["id"] == f"knowledge:{claim}" for x in query_rows), query_rows
    assert not any(x.get("knowledge_id") == dup for x in query_rows), query_rows

print("knowledge-writeback: ok")
