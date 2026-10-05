#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import knowledge_store as ks

SKILL_ROOT = Path(__file__).resolve().parents[1]
WIKI = Path(os.environ.get("EXTERACONTEXT_WIKI", SKILL_ROOT / "data" / "wiki"))
DB = Path(os.environ.get("EXTERACONTEXT_DB", SKILL_ROOT / "data" / "exteracontext.sqlite"))

STATUS_SCORE = {
    "runtime-verified": 8.0,
    "code": 4.0,
    "docs": 3.0,
    "inference": 1.5,
    "secondary": 0.5,
    "unavailable": -4.0,
}

DIRECT_SOURCES = {
    "official-sdk", "exteragram-docs", "exteragram-utils", "exteragram-mcp",
    "exteragram-mcp-npm", "for-vibecoders", "extcli", "altylib", "catalib",
    "gradle-plugin", "template-n08", "template-robotiaga", "plugins-store",
    "plugins-store-codeberg", "plugins-store-gitverse", "vestr-plugins",
}
DONOR_PREFIXES = (
    "ayugram", "nagram", "nekogram", "nekox", "niagram", "novagram", "nullgram",
    "nullcoregram", "octogram", "cherrygram", "mercurygram", "miogram", "amegram",
    "vibogram", "reqgram", "opexgram", "televip",
)
OFFICIAL_SOURCES = {"official-sdk", "exteragram-docs", "exteragram-utils"}

COMMON_CROSS_CLIENT_IDENTIFIERS = {
    "messageobject", "tlobject", "notificationcenter", "chatactivity", "launchactivity",
    "sendmessageshelper", "tlrpc", "android", "telegram", "java", "python",
}

STOP = {
    "как", "что", "для", "или", "это", "при", "над", "под", "мне", "нужно", "сделать",
    "the", "and", "for", "with", "from", "into", "how", "use", "using", "plugin", "плагин",
    "exteragram", "exteragramm", "exteragram", "ayugram",
}

# Query expansion is deliberately small and domain-specific. FTS still sees the user's original terms.
ALIASES = {
    "исход": ["outgoing", "send", "message"],
    "отправ": ["send", "outgoing", "request"],
    "сообщ": ["message", "MessageObject"],
    "контекст": ["context", "menu"],
    "меню": ["menu", "action"],
    "перехват": ["hook", "intercept"],
    "хук": ["hook", "xposed"],
    "hook": ["xposed", "callback"],
    "аккаунт": ["account", "multi-account"],
    "поток": ["thread", "ui", "background"],
    "интерфейс": ["ui", "view"],
    "настрой": ["settings", "preferences"],
    "медиа": ["media", "document", "photo"],
    "файл": ["file", "document"],
    "скач": ["download", "file"],
    "загруз": ["load", "download", "upload"],
    "выгруз": ["unload", "cleanup", "lifecycle"],
    "перезагруз": ["reload", "unload", "load"],
    "рефлек": ["reflection", "java", "xposed"],
    "java": ["reflection", "xposed", "Member"],
    "dex": ["dex", "jvm", "classloader"],
    "запрос": ["request", "send_request", "TLRPC"],
    "сеть": ["network", "request"],
    "хранил": ["storage", "cache"],
    "кэш": ["cache", "ttl"],
    "кеш": ["cache", "ttl"],
    "ошиб": ["debug", "error", "exception"],
    "сбор": ["build", "package", "elyx"],
    "elyx": ["archive", "entry", "builder"],
    "фон": ["background", "queue", "run_on_queue"],
    "обнов": ["ui", "run_on_ui_thread"],
    "интерфейс": ["ui", "view", "run_on_ui_thread"],
    "измен": ["modify", "HookResult", "HookStrategy"],
    "импорт": ["import", "import_module"],
    "обработ": ["handler", "callback"],
    "расшир": ["extension", "FileInfo", "FilesController"],
    "metadata": ["метадан", "__id__", "__name__"],
    "метадан": ["metadata", "__id__", "__name__"],
    "permission": ["permissions", "разреш"],
    "разреш": ["permission", "permissions"],
    "intent": ["IntentsManager", "handler", "path"],
    "reload": ["lifecycle", "unload", "cleanup"],
    "приостан": ["pause", "AppEvent"],
    "возобнов": ["resume", "AppEvent"],
    "background": ["background", "pause", "queue"],
    "live": ["sync", "reload", "elyx_changes"],
}


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="strict")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="strict")


_AUTO_SYNC_DONE = False


def ensure_db() -> None:
    global _AUTO_SYNC_DONE
    auto_sync = os.environ.get("EXTERACONTEXT_AUTO_SYNC", "").strip().lower()
    if auto_sync in {"1", "true", "yes", "on"} and not _AUTO_SYNC_DONE:
        updater = SKILL_ROOT / "scripts" / "update_knowledge.py"
        lock = Path(os.environ.get("EXTERACONTEXT_KNOWLEDGE_LOCK", SKILL_ROOT / "KNOWLEDGE_LOCK"))
        try:
            subprocess.run(
                [sys.executable, str(updater), "--db", str(DB), "--lock", str(lock)],
                check=True,
                stdout=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "EXTERACONTEXT_AUTO_SYNC requested the trusted, hash-pinned Knowledge updater, "
                f"but it failed closed (exit {exc.returncode}); check KNOWLEDGE_LOCK and the "
                "protected per-commit release artifact."
            ) from exc
        _AUTO_SYNC_DONE = True

    if DB.exists():
        return

    # Explicit development-only compatibility for monolithic wiki checkouts.
    # Production/split-repository deployments must provision the SQLite artifact
    # deliberately with scripts/sync_knowledge.py; query must not build implicitly.
    auto_build = os.environ.get("EXTERACONTEXT_AUTO_BUILD", "").strip().lower()
    if auto_build in {"1", "true", "yes", "on"}:
        build = SKILL_ROOT / "scripts" / "build_index.py"
        if build.is_file() and WIKI.is_dir():
            subprocess.run(
                [sys.executable, str(build), "--wiki", str(WIKI), "--db", str(DB)],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            return
        raise FileNotFoundError(
            "EXTERACONTEXT_AUTO_BUILD is enabled, but this checkout has no local "
            f"build_index.py/WIKI source ({build}, {WIKI})."
        )

    raise FileNotFoundError(
        f"ExteraContext base database not found at {DB}. Provision an existing corpus offline with "
        "`python scripts/sync_knowledge.py --source <existing.sqlite> --db <destination.sqlite>`. "
        "This command only validates and copies an existing database; it does not download or build "
        "knowledge. For local monolithic development only, opt into a build with "
        "EXTERACONTEXT_AUTO_BUILD=1."
    )



def con() -> sqlite3.Connection:
    ensure_db()
    from corpus_preflight import identity, verify_base
    path = DB.expanduser().absolute()
    generation = identity(path.stat())
    verify_base(path)
    # This is a verified quiescent static corpus. immutable=1 avoids SQLite WAL
    # sidecar writes; mode=ro also refuses a file removed before connect().
    c = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        c.execute("PRAGMA query_only=ON")
        c.row_factory = sqlite3.Row
        # Establish the read snapshot before checking the path again. An atomic
        # deployment between verification and connect must not return another DB.
        c.execute("BEGIN")
        c.execute("SELECT rootpage FROM sqlite_schema LIMIT 1").fetchone()
        if identity(path.stat()) != generation:
            raise RuntimeError("ExteraContext base database changed between verification and connection")
        return c
    except BaseException:
        c.close()
        raise


def words(q: str) -> list[str]:
    raw = re.findall(r"[A-Za-zА-Яа-яЁё0-9_.$:@+-]+", q.lower())
    out: list[str] = []
    for w in raw:
        if len(w) < 2 or w in STOP:
            continue
        out.append(w)
        for stem, vals in ALIASES.items():
            if w.startswith(stem):
                out.extend(v.lower() for v in vals)
    # Preserve order while deduplicating.
    seen = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def fts_query(q: str) -> str:
    toks = words(q)
    if not toks:
        toks = [q.strip().lower()] if q.strip() else []
    safe = []
    for t in toks[:24]:
        t = re.sub(r"[^A-Za-zА-Яа-яЁё0-9_]", "", t)
        if len(t) >= 2:
            safe.append(f'"{t}"*')
    return " OR ".join(safe)


def source_bonus(source_id: str) -> float:
    s = (source_id or "").lower()
    if s in OFFICIAL_SOURCES:
        return 5.0
    if s in DIRECT_SOURCES:
        return 2.5
    if s.startswith(DONOR_PREFIXES):
        return -0.5
    return 0.5


def directness(source_id: str) -> str:
    s = (source_id or "").lower()
    if s in OFFICIAL_SOURCES:
        return "official"
    if s in DIRECT_SOURCES:
        return "target-ecosystem"
    if s.startswith(DONOR_PREFIXES):
        return "donor"
    return "other"


def row_dict(r: sqlite3.Row) -> dict[str, Any]:
    return {k: r[k] for k in r.keys()}


def source_provenance(source_id: str) -> dict[str, Any] | None:
    if not source_id:
        return None
    c = con()
    try:
        summary = c.execute("SELECT * FROM source_provenance WHERE source_id = ?", (source_id,)).fetchone()
        if not summary:
            return None
        runs = c.execute(
            "SELECT call_id,role,task_name,started_at,model,reasoning,fork_turns,role_attempt,prompt_reconstructed,prompt_file "
            "FROM source_runs WHERE source_id = ? ORDER BY run_order",
            (source_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        c.close()
    d = row_dict(summary)
    d["models"] = json.loads(d.pop("models_json", "[]") or "[]")
    d["reasoning_levels"] = json.loads(d.pop("reasoning_levels_json", "[]") or "[]")
    d["runs"] = [row_dict(r) for r in runs]
    return d


def dynamic_directness(source_type: str | None, client: str | None) -> str:
    st = (source_type or "").lower()
    if st in {"official", "official-sdk", "official-docs", "official-source"}:
        return "official"
    if st in {"donor", "donor-code", "ayugram", "nagram"}:
        return "donor"
    if (client or "").lower() == "exteragram" or st in {"target", "target-code", "runtime", "project"}:
        return "target-ecosystem"
    return "other"


def dynamic_fact(d: dict[str, Any], q: str) -> dict[str, Any]:
    ev = d.get("top_evidence") or {}
    version_parts = []
    if d.get("client_version"):
        version_parts.append(f"client {d['client_version']}")
    if d.get("sdk_version"):
        version_parts.append(f"SDK {d['sdk_version']}")
    source_type = ev.get("source_type")
    out = {
        "id": f"knowledge:{d['id']}",
        "knowledge_id": d["id"],
        "topic": d.get("kind") or "agent-knowledge",
        "claim": d.get("statement") or "",
        "api": d.get("api_symbol") or "",
        "evidence_url": ev.get("url") or "",
        "evidence_path": ":".join(x for x in [ev.get("repository"), ev.get("path"), ev.get("line_range")] if x),
        "version": "; ".join(version_parts),
        "status": d.get("effective_evidence_status") or "inference",
        "recipe": d.get("statement") if d.get("kind") == "recipe" else "",
        "source_id": f"agent-knowledge:{source_type or 'unknown'}",
        "source_fact_id": d["id"],
        "original_status": d.get("effective_evidence_status") or "inference",
        "canonical_topic": d.get("kind") or "agent-knowledge",
        "review_status": d.get("state"),
        "platform": d.get("platform") or "",
        "knowledge_state": d.get("state"),
        "scope": d.get("scope"),
        "client": d.get("client"),
    }
    direct = dynamic_directness(source_type, d.get("client"))
    out["directness"] = direct
    toks = words(q)
    hay = " ".join(str(out.get(k, "")) for k in ("claim", "api", "topic", "version", "platform", "client")).lower()
    coverage = sum(1 for t in toks if t in hay)
    exact_api = 0.0
    api = (out.get("api") or "").lower()
    qlow = q.lower()
    raw_identifiers = [x.lower() for x in re.findall(r"\b[A-Za-z_][A-Za-z0-9_.]*\b", q)
                       if len(x) >= 5 and ("_" in x or "." in x or any(ch.isupper() for ch in x[1:]))]
    if api and (qlow in api or any(t in api for t in toks if len(t) >= 4)):
        exact_api += 2.0
    if api and any(ident in api for ident in raw_identifiers):
        exact_api += 8.0
    authority = 5.0 if direct == "official" else 2.5 if direct == "target-ecosystem" else -0.5 if direct == "donor" else 0.5
    state_penalty = -1.0 if d.get("state") == "conflicting" else 0.0
    out["score"] = round(coverage * 0.65 + STATUS_SCORE.get(out["status"], 0) + authority + exact_api + state_penalty, 3)
    return out


def search_dynamic_facts(q: str, limit: int = 12) -> list[dict[str, Any]]:
    rows = ks.search_verified(q, max(limit * 2, 20))
    out = [dynamic_fact(r, q) for r in rows]
    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    return out[:limit]


def search_facts(q: str, limit: int = 12) -> list[dict[str, Any]]:
    match = fts_query(q)
    if not match:
        return []
    c = con()
    rows = c.execute(
        """
        SELECT f.*, bm25(facts_fts, 4.0, 7.0, 3.0, 1.5, 1.5, 1.0, 1.0, 1.0) AS bm
        FROM facts_fts JOIN facts f ON f.id = facts_fts.id
        WHERE facts_fts MATCH ?
        ORDER BY bm LIMIT ?
        """,
        (match, max(limit * 8, 60)),
    ).fetchall()
    c.close()
    qlow = q.lower()
    toks = words(q)
    raw_identifiers = [x.lower() for x in re.findall(r"\b[A-Za-z_][A-Za-z0-9_.]*\b", q)
                       if len(x) >= 5 and ("_" in x or "." in x or any(ch.isupper() for ch in x[1:]))]
    out = []
    for r in rows:
        d = row_dict(r)
        hay = " ".join(str(d.get(k, "")) for k in ("claim", "api", "recipe", "topic", "canonical_topic", "source_id", "version", "platform")).lower()
        coverage = sum(1 for t in toks if t in hay)
        exact_api = 0.0
        api = (d.get("api") or "").lower()
        if api and (qlow in api or any(t in api for t in toks if len(t) >= 4)):
            exact_api = 2.0
        # A user-named concrete symbol is stronger evidence of intent than source preference.
        # Donor symbols should surface prominently, but remain labeled as donor evidence.
        if api and any(ident in api and ident not in COMMON_CROSS_CLIENT_IDENTIFIERS for ident in raw_identifiers):
            exact_api += 8.0
        d["score"] = round(coverage * 0.65 + STATUS_SCORE.get(d.get("status", ""), 0) + source_bonus(d.get("source_id", "")) + exact_api, 3)
        d["directness"] = directness(d.get("source_id", ""))
        out.append(d)
    out.sort(key=lambda x: (x["score"], -float(x.get("bm") or 0)), reverse=True)
    dynamic = search_dynamic_facts(q, limit)
    merged = out + dynamic
    seen_ids = set()
    deduped = []
    for item in sorted(merged, key=lambda x: (x.get("score", 0), -float(x.get("bm") or 0)), reverse=True):
        if item.get("id") in seen_ids:
            continue
        seen_ids.add(item.get("id"))
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


def search_docs(q: str, limit: int = 6, kind: str | None = None) -> list[dict[str, Any]]:
    match = fts_query(q)
    if not match:
        return []
    c = con()
    if kind:
        rows = c.execute(
            """
            SELECT d.*, bm25(docs_fts, 5.0, 1.0, 1.0) AS bm
            FROM docs_fts JOIN docs d ON d.path = docs_fts.path
            WHERE docs_fts MATCH ? AND d.kind = ?
            ORDER BY bm LIMIT ?
            """, (match, kind, limit)
        ).fetchall()
    else:
        rows = c.execute(
            """
            SELECT d.*, bm25(docs_fts, 5.0, 1.0, 1.0) AS bm
            FROM docs_fts JOIN docs d ON d.path = docs_fts.path
            WHERE docs_fts MATCH ?
            ORDER BY bm LIMIT ?
            """, (match, limit)
        ).fetchall()
    c.close()
    return [row_dict(r) for r in rows]


def api_lookup(symbol: str, limit: int = 30) -> list[dict[str, Any]]:
    c = con()
    like = f"%{symbol}%"
    rows = c.execute(
        """
        SELECT * FROM facts
        WHERE api LIKE ? COLLATE NOCASE OR claim LIKE ? COLLATE NOCASE
        LIMIT ?
        """, (like, like, max(limit * 4, 60))
    ).fetchall()
    c.close()
    out = [row_dict(r) for r in rows]
    for d in out:
        d["score"] = STATUS_SCORE.get(d.get("status", ""), 0) + source_bonus(d.get("source_id", "")) + (4 if symbol.lower() in (d.get("api") or "").lower() else 0)
        d["directness"] = directness(d.get("source_id", ""))
    out.sort(key=lambda x: x["score"], reverse=True)
    dyn = [dynamic_fact(r, symbol) for r in ks.api_search(symbol, limit)]
    merged = sorted(out + dyn, key=lambda x: x.get("score", 0), reverse=True)
    return merged[:limit]


def runtime_summary(facts: list[dict[str, Any]]) -> str:
    n = sum(1 for f in facts if f.get("status") == "runtime-verified")
    if n:
        return f"Есть runtime-подтверждения среди результатов: {n}."
    return "Runtime-подтверждений среди результатов нет; code/docs не считать доказательством работы на целевой сборке."


def clip(text: str, n: int = 520) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def emit_fact(f: dict[str, Any], i: int) -> str:
    api = f.get("api") or "—"
    ver = f.get("version") or "—"
    platform = f.get("platform") or "—"
    evidence = f.get("evidence_url") or f.get("evidence_path") or "—"
    lifecycle = f" / {f.get('knowledge_state')}" if f.get("knowledge_state") else ""
    return (
        f"{i}. `{f.get('id')}` — **{f.get('status')} / {f.get('directness')}{lifecycle}**\n"
        f"   - API: `{api}`\n"
        f"   - Факт: {clip(f.get('claim',''), 700)}\n"
        f"   - Версия/платформа: {ver}; {platform}\n"
        f"   - Практика: {clip(f.get('recipe',''), 420) or '—'}\n"
        f"   - Доказательство: {evidence}"
    )


# A version is evidence only when attached to a precise field, not when the same
# digits occur in a commit, prose about a future SDK, or a different version.
_VERSION_LABEL = {
    "client": re.compile(r"(?<![\w])(?:client|app)\s+(\d+(?:\.\d+)+(?:[-+][\w.]+)?)(?![\w.])", re.I),
    "sdk": re.compile(r"(?<![\w])(?:sdk|elyx)\s+(\d+(?:\.\d+)+(?:[-+][\w.]+)?)(?![\w.])", re.I),
}


def _version_relation(fact: dict[str, Any], field: str, requested: str | None) -> str:
    if not requested:
        return "not-requested"
    structured = fact.get(field + "_version")
    if structured:
        return "match" if structured == requested else "mismatch"
    labels = _VERSION_LABEL[field].findall(fact.get("version") or "")
    if len(labels) != 1:
        return "unknown"
    return "match" if requested in labels else "mismatch"


def _explicit_donor_lookup(q: str, pool: list[dict[str, Any]]) -> bool:
    # Naming a donor client or a concrete donor-only symbol is intentional.
    low = q.lower()
    if any(re.search(r"(?<![a-z])" + re.escape(name) + r"[a-z0-9]*(?![a-z])", low)
           for name in DONOR_PREFIXES):
        return True
    identifiers = {x.lower() for x in re.findall(r"\b[A-Za-z_][A-Za-z0-9_.]*\b", q)
                   if len(x) >= 5 and ("_" in x or "." in x or any(c.isupper() for c in x[1:]))}
    identifiers -= COMMON_CROSS_CLIENT_IDENTIFIERS
    return any(f.get("directness") == "donor" and
               any(ident in (f.get("api") or "").lower() for ident in identifiers)
               for f in pool)


def _target_facts(q: str, target: str, client_version: str | None,
                  sdk_version: str | None, limit: int) -> tuple[list[dict[str, Any]], bool]:
    target_client = any(client in target.lower() for client in ("exteragram", "ayugram"))
    # Keep the historical top-k candidate set (including its dynamic overlay
    # quota); a larger search changes that quota and loses proven benchmark hits.
    pool = search_facts(q, limit=limit)
    explicit_donor = _explicit_donor_lookup(q, pool)
    if not target_client or explicit_donor:
        return pool[:limit], explicit_donor
    for fact in pool:
        client_match = _version_relation(fact, "client", client_version)
        sdk_match = _version_relation(fact, "sdk", sdk_version)
        fact["target_version_match"] = {"client": client_match, "sdk": sdk_match}
        # Unknown versions remain unknown, never promoted to compatible.
        # Explicit versions break close ties, but must not displace stronger
        # historical official evidence merely because its version is unstated.
        match_bonus = 0.25 * (client_match == "match") + 0.25 * (sdk_match == "match")
        mismatch_penalty = 4 * (client_match == "mismatch") + 4 * (sdk_match == "mismatch")
        donor_penalty = 7 if fact.get("directness") == "donor" else 0
        fact["target_rank_score"] = fact["score"] + match_bonus - mismatch_penalty - donor_penalty
    pool.sort(key=lambda f: f["target_rank_score"], reverse=True)
    return pool[:limit], False


def context_packet(q: str, target: str, client_version: str | None, sdk_version: str | None, limit: int,
                   facts_override: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if facts_override is None:
        facts, explicit_donor = _target_facts(q, target, client_version, sdk_version, limit)
    else:
        facts=facts_override
        explicit_donor=_explicit_donor_lookup(q,facts)
        for fact in facts:
            fact['target_version_match']={'client':_version_relation(fact,'client',client_version),'sdk':_version_relation(fact,'sdk',sdk_version)}
    recipes = search_docs(q, limit=4, kind="recipe")
    topics = search_docs(q, limit=4, kind="topic")
    api_candidates = []
    donor_api_candidates = []
    seen = set()
    for f in facts:
        api = (f.get("api") or "").strip()
        if api and api != "—" and api not in seen:
            seen.add(api)
            if f.get("directness") == "donor" and not explicit_donor and "exteragram" in target.lower():
                donor_api_candidates.append(api)
            else:
                api_candidates.append(api)
        if len(api_candidates) >= 8:
            break
    direct = [f for f in facts if f.get("directness") in {"official", "target-ecosystem"} and f.get("status") not in {"unavailable", "secondary"}]
    donors = [f for f in facts if f.get("directness") == "donor"]
    warnings = []
    if not any(f.get("status") == "runtime-verified" for f in facts):
        warnings.append("Нет runtime-verified результата для этой выборки.")
    if donors:
        warnings.append("В релевантной выборке есть донорские реализации; их символы нельзя переносить в ExteraGram без отдельного подтверждения целевого клиента.")
    if any(f.get("knowledge_state") == "conflicting" for f in facts):
        warnings.append("В агентской базе есть конфликтующее подтверждённое наблюдение; не выбирайте одну сторону без проверки target/version и evidence trail.")
    if not direct and donors:
        warnings.append("Прямых доказательств ExteraGram для этой задачи не найдено; найденные доноры использовать только как архитектурные ориентиры.")
    if explicit_donor and "exteragram" in target.lower():
        warnings.append("Явный запрос донорского символа: результаты доноров сохранены для справки, не подтверждают поддержку ExteraGram.")
    if donor_api_candidates:
        warnings.append("Донорские API-кандидаты вынесены отдельно и не подтверждают поддержку целевого клиента.")
    if client_version or sdk_version:
        warnings.append("Совпадение версии учитывается только по точной метке client/app или SDK/Elyx; отсутствие метки означает unknown, не совместимость.")
        if any("mismatch" in f.get("target_version_match", {}).values() for f in facts):
            warnings.append("Некоторые факты содержат явно несовпадающую версию; они понижены в выдаче, но оставлены как контекст.")
        if not any("match" in f.get("target_version_match", {}).values() for f in facts):
            warnings.append("Точных подтверждений запрошенной версии в выдаче нет; поддержку целевой сборки считать неизвестной.")
    return {
        "query": q,
        "target": target,
        "client_version": client_version,
        "sdk_version": sdk_version,
        "api_candidates": api_candidates,
        "donor_api_candidates": donor_api_candidates,
        "facts": facts,
        "recipes": [{"path": d["path"], "title": d["title"], "excerpt": clip(d["content"], 900)} for d in recipes],
        "topics": [{"path": d["path"], "title": d["title"], "excerpt": clip(d["content"], 700)} for d in topics],
        "warnings": warnings,
        "runtime_summary": runtime_summary(facts),
    }


def render_context_md(p: dict[str, Any]) -> str:
    lines = [
        "# ExteraContext task packet",
        "",
        f"**Задача:** {p['query']}",
        f"**Цель:** {p['target']}" + (f" {p['client_version']}" if p.get('client_version') else "") + (f"; SDK {p['sdk_version']}" if p.get('sdk_version') else ""),
        "",
        "## API-кандидаты",
    ]
    if p["api_candidates"]:
        lines.extend(f"- `{x}`" for x in p["api_candidates"])
    else:
        lines.append("- Явных API-кандидатов не найдено.")
    if p.get("donor_api_candidates"):
        lines += ["", "## Донорские API (не подтверждены для целевого клиента)"]
        lines.extend(f"- `{x}`" for x in p["donor_api_candidates"])
    lines += ["", "## Доказательства"]
    if p["facts"]:
        for i, f in enumerate(p["facts"], 1):
            lines.append(emit_fact(f, i))
    else:
        lines.append("Релевантных фактов не найдено.")
    lines += ["", "## Рецепты"]
    if p["recipes"]:
        for d in p["recipes"]:
            lines.append(f"- `{d['path']}` — **{d['title']}**: {d['excerpt']}")
    else:
        lines.append("- Релевантных рецептов не найдено.")
    lines += ["", "## Тематические страницы"]
    if p["topics"]:
        for d in p["topics"]:
            lines.append(f"- `{d['path']}` — **{d['title']}**: {d['excerpt']}")
    else:
        lines.append("- Релевантных тематических страниц не найдено.")
    lines += ["", "## Ограничения", f"- {p['runtime_summary']}"]
    lines.extend(f"- {w}" for w in p["warnings"])
    return "\n".join(lines)


def command_search(args: argparse.Namespace) -> None:
    rows = search_facts(args.query, args.limit)
    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for i, r in enumerate(rows, 1):
            print(emit_fact(r, i), "\n")


def command_target_lookup(args: argparse.Namespace) -> None:
    """Rank target evidence before the presentation limit; retain conflict context.

    Bounded retrieval is reported explicitly and never treated as complete coverage.
    Existing unscoped commands and benchmark quotas are unchanged.
    """
    target=json.loads(args.target_json)
    if not isinstance(target,dict):
        raise ValueError('Target must be an object')
    cap=500
    if args.mode=='api':
        pool=api_lookup(args.query,cap+1)
    elif args.mode=='recipe':
        pool=search_docs(args.query,cap+1,kind='recipe')
    else:
        pool=search_facts(args.query,cap+1)
    def rank(f):
        matches=0
        mismatches=0
        for field,value in target.items():
            if field in {'client_version','sdk_version'}:
                relation=_version_relation(f,field.removesuffix('_version'),value)
            else:
                actual=f.get(field)
                relation='unknown' if actual is None or actual=='' else 'match' if str(actual).lower()==str(value).lower() else 'mismatch'
            matches+=relation=='match'
            mismatches+=relation=='mismatch'
        return (mismatches==0,matches,f.get('score',0))
    pool.sort(key=rank,reverse=True)
    selected=pool[:args.limit]
    result={'items':selected,'assessment_facts':pool[:cap],'candidate_count':len(pool),
        'candidate_limit_hit':len(pool)>cap,'coverage':'bounded retrieval; not complete corpus coverage'}
    if args.mode=='context':
        result['context']=context_packet(args.query,' '.join(str(target.get(k,'')) for k in ['client','platform']),target.get('client_version'),target.get('sdk_version'),args.limit,selected)
    print(json.dumps(result,ensure_ascii=False))


def command_api(args: argparse.Namespace) -> None:
    rows = api_lookup(args.symbol, args.limit)
    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        if not rows:
            print(f"API/symbol `{args.symbol}` не найден в индексированной базе.")
            return
        for i, r in enumerate(rows, 1):
            print(emit_fact(r, i), "\n")


def command_recipe(args: argparse.Namespace) -> None:
    rows = search_docs(args.query, args.limit, kind="recipe")
    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            print(f"# {r['title']}\nPath: {r['path']}\n\n{r['content']}\n")


def command_evidence(args: argparse.Namespace) -> None:
    knowledge_key = args.key[len("knowledge:"):] if args.key.startswith("knowledge:") else args.key
    kclaim = ks.get_claim(knowledge_key)
    if kclaim:
        if args.format == "json":
            print(json.dumps(kclaim, ensure_ascii=False, indent=2))
        else:
            print(f"# knowledge:{knowledge_key}\n")
            print(f"State: {kclaim['state']} | Evidence: {kclaim['effective_evidence_status']} | Scope: {kclaim['scope']}")
            print(f"Claim: {kclaim['statement']}")
            print(f"Target: {kclaim.get('client') or '—'} {kclaim.get('client_version') or ''}; SDK {kclaim.get('sdk_version') or '—'}")
            print("\nEvidence:")
            for e in kclaim.get('evidence', []):
                loc = e.get('url') or ':'.join(x for x in [e.get('repository'), e.get('path'), e.get('line_range')] if x) or '—'
                print(f"- {e.get('evidence_status')} / {e.get('relation')}: {loc}")
            print("\nVerifications:")
            for v in kclaim.get('verifications', []):
                print(f"- {v.get('verdict') or 'phase-a-only'} by {v.get('verifier_run_id')}: {clip(v.get('phase_a_statement',''), 400)}")
        return
    c = con()
    row = c.execute("SELECT * FROM facts WHERE id = ?", (args.key,)).fetchone()
    c.close()
    rows = [row_dict(row)] if row else api_lookup(args.key, args.limit)
    for r in rows:
        prov = source_provenance(r.get("source_id", ""))
        if prov:
            r["source_provenance"] = prov
    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        if not rows:
            print(f"Доказательства для `{args.key}` не найдены.")
            return
        for i, r in enumerate(rows, 1):
            r["directness"] = directness(r.get("source_id", ""))
            print(emit_fact(r, i))
            prov = r.get("source_provenance")
            if prov:
                print(
                    f"   - Legacy review provenance: {prov.get('collector_runs',0)} collector run(s), "
                    f"{prov.get('reviewer_runs',0)} reviewer run(s); mode={prov.get('review_mode')}; "
                    f"fork_turns_none={bool(prov.get('all_fork_turns_none'))}."
                )
                print("   - Caveat: legacy reviewer independently re-read source material but was not blind to collector output; this does not imply runtime verification.")
            print()


def command_context(args: argparse.Namespace) -> None:
    p = context_packet(args.query, args.target, args.client_version, args.sdk_version, args.limit)
    if args.format == "json":
        print(json.dumps(p, ensure_ascii=False, indent=2))
    else:
        print(render_context_md(p))


def _identity_from_stat(stat: os.stat_result) -> tuple[int, int, int, int, int]:
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _stat_identity(path: Path) -> tuple[int, int, int, int, int]:
    return _identity_from_stat(path.stat())


def _same_file_identity(left: tuple[int, int, int, int, int], right: tuple[int, int, int, int, int]) -> bool:
    # Windows exposes st_ctime differently through path stat and fstat; stable
    # file-object identity is device, inode/file-id, size and modification time.
    return left[:4] == right[:4]


def _hash_database(path: Path, expected_identity: tuple[int, int, int, int, int] | None = None) -> tuple[int, str]:
    """Hash stable bytes from one open file; reject path replacement or file mutation.

    The digest describes bytes read from the opened descriptor. Identity checks bind
    that descriptor to the SQLite-read snapshot and ensure the path still names it.
    This does not protect against arbitrary writers that bypass quiescence controls.
    """
    resolved = path.expanduser().resolve(strict=True)
    path_before_open = _stat_identity(resolved)
    if expected_identity is not None and path_before_open != expected_identity:
        raise RuntimeError(f"ExteraContext base database changed after it was read: {resolved}")

    digest = hashlib.sha256()
    bytes_read = 0
    with resolved.open("rb") as source:
        descriptor_before = _identity_from_stat(os.fstat(source.fileno()))
        path_before_stream = _stat_identity(resolved)
        identity_before = expected_identity if expected_identity is not None else path_before_open
        if (
            not _same_file_identity(descriptor_before, identity_before)
            or path_before_stream != identity_before
            or not _same_file_identity(descriptor_before, path_before_stream)
        ):
            raise RuntimeError(f"ExteraContext base database changed while hashing: {resolved}")

        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            bytes_read += len(chunk)

        descriptor_after = _identity_from_stat(os.fstat(source.fileno()))
        path_after_stream = _stat_identity(resolved)
        if (
            descriptor_before != descriptor_after
            or not _same_file_identity(descriptor_after, identity_before)
            or path_after_stream != identity_before
            or not _same_file_identity(descriptor_after, path_after_stream)
            or bytes_read != descriptor_before[2]
            or bytes_read != descriptor_after[2]
        ):
            raise RuntimeError(f"ExteraContext base database changed while hashing: {resolved}")
    return bytes_read, digest.hexdigest()


def command_doctor(args: argparse.Namespace) -> None:
    # Complete any explicitly opted-in update before sampling file identity.
    ensure_db()
    configured_db = Path(os.environ.get("EXTERACONTEXT_DB", str(DB))).expanduser().resolve(strict=True)
    from sync_knowledge import require_quiescent

    require_quiescent(configured_db)
    identity_before_open = _stat_identity(configured_db)
    c = con()
    try:
        meta = {r[0]: json.loads(r[1]) for r in c.execute("SELECT key,value FROM meta")}
        runtime = c.execute("SELECT COUNT(*) FROM facts WHERE status='runtime-verified'").fetchone()[0]
        direct = c.execute("SELECT COUNT(*) FROM facts WHERE source_id IN (%s)" % ",".join("?"*len(DIRECT_SOURCES)), tuple(sorted(DIRECT_SOURCES))).fetchone()[0]
        try:
            meta["legacy_source_runs"] = c.execute("SELECT COUNT(*) FROM source_runs").fetchone()[0]
            meta["legacy_sources_with_reviewers"] = c.execute("SELECT COUNT(*) FROM source_provenance WHERE has_reviewer=1").fetchone()[0]
        except sqlite3.OperationalError:
            meta["legacy_source_runs"] = 0
            meta["legacy_sources_with_reviewers"] = 0
        opened_db = Path(c.execute("PRAGMA database_list").fetchone()[2]).resolve(strict=True)
        identity_after_read = _stat_identity(opened_db)
    finally:
        c.close()

    if configured_db != opened_db:
        raise RuntimeError(f"EXTERACONTEXT_DB changed after the corpus was opened: {configured_db} != {opened_db}")
    if identity_before_open != identity_after_read:
        raise RuntimeError(f"ExteraContext base database changed while it was being read: {configured_db}")

    size_bytes, sha256 = _hash_database(configured_db, expected_identity=identity_after_read)
    meta["runtime_verified"] = runtime
    meta["direct_ecosystem_facts"] = direct
    meta["db"] = str(DB)
    meta["db_path"] = str(configured_db)
    meta["db_size_bytes"] = size_bytes
    meta["db_sha256"] = sha256
    from corpus_preflight import verify_base
    meta["corpus_verification"] = verify_base(configured_db)
    meta["agent_knowledge"] = ks.stats()
    require_quiescent(configured_db)
    print(json.dumps(meta, ensure_ascii=False, indent=2))


def command_preflight(args: argparse.Namespace) -> None:
    ensure_db()
    from corpus_preflight import verify_base
    print(json.dumps(verify_base(DB, full=True), ensure_ascii=False))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Local evidence-aware retrieval over the ExteraGram plugin wiki")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser('target-lookup',help='Target-aware ranking before presentation limits')
    s.add_argument('query')
    s.add_argument('--target-json',required=True)
    s.add_argument('--mode',choices=['api','usage','recipe','context'],required=True)
    s.add_argument('--limit',type=int,default=12)
    s.add_argument('--format',choices=['json'],default='json')
    s.set_defaults(func=command_target_lookup)

    s = sp.add_parser("context", help="Build a compact task-specific context packet")
    s.add_argument("query")
    s.add_argument("--target", default="ExteraGram Android")
    s.add_argument("--client-version")
    s.add_argument("--sdk-version")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--format", choices=["md", "json"], default="md")
    s.set_defaults(func=command_context)

    s = sp.add_parser("search", help="Search atomic facts")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=12)
    s.add_argument("--format", choices=["md", "json"], default="md")
    s.set_defaults(func=command_search)

    s = sp.add_parser("api", help="Look up a concrete API/symbol")
    s.add_argument("symbol")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--format", choices=["md", "json"], default="md")
    s.set_defaults(func=command_api)

    s = sp.add_parser("recipe", help="Find synthesized recipes")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=3)
    s.add_argument("--format", choices=["md", "json"], default="md")
    s.set_defaults(func=command_recipe)

    s = sp.add_parser("evidence", help="Show provenance for a fact id or symbol")
    s.add_argument("key")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--format", choices=["md", "json"], default="md")
    s.set_defaults(func=command_evidence)

    s = sp.add_parser("doctor", help="Check index and evidence coverage")
    s.set_defaults(func=command_doctor)
    s = sp.add_parser("preflight", help="Validate static corpus and deployment manifest before serving")
    s.set_defaults(func=command_preflight)
    return p


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
