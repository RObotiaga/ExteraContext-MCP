#!/usr/bin/env python3
from __future__ import annotations

import argparse
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


def ensure_db() -> None:
    if DB.exists():
        return
    build = SKILL_ROOT / "scripts" / "build_index.py"
    if build.exists() and WIKI.exists():
        subprocess.run([sys.executable, str(build), "--wiki", str(WIKI), "--db", str(DB)], check=True, stdout=subprocess.DEVNULL)
        return
    if os.environ.get("EXTERACONTEXT_AUTO_SYNC", "").lower() in {"1", "true", "yes", "on"}:
        sync = SKILL_ROOT / "scripts" / "sync_knowledge.py"
        subprocess.run([sys.executable, str(sync), "--db", str(DB)], check=True, stdout=subprocess.DEVNULL)
        return
    raise FileNotFoundError(
        f"ExteraContext base database not found at {DB}. "
        "Run `python scripts/sync_knowledge.py` or set EXTERACONTEXT_DB."
    )


def con() -> sqlite3.Connection:
    ensure_db()
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


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
    try:
        rows = ks.search_verified(q, max(limit * 2, 20))
    except Exception:
        return []
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
    try:
        dyn = [dynamic_fact(r, symbol) for r in ks.api_search(symbol, limit)]
    except Exception:
        dyn = []
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


def context_packet(q: str, target: str, client_version: str | None, sdk_version: str | None, limit: int) -> dict[str, Any]:
    facts = search_facts(q, limit=limit)
    recipes = search_docs(q, limit=4, kind="recipe")
    topics = search_docs(q, limit=4, kind="topic")
    api_candidates = []
    seen = set()
    for f in facts:
        api = (f.get("api") or "").strip()
        if api and api != "—" and api not in seen:
            seen.add(api)
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
    if client_version:
        warnings.append(f"Фильтрация по версии клиента {client_version} пока эвристическая: сравнивайте поле version каждого факта и compatibility.md.")
    if sdk_version:
        warnings.append(f"Фильтрация по SDK {sdk_version} пока эвристическая: нижние версии отдельных API могут отличаться от baseline документации.")
    return {
        "query": q,
        "target": target,
        "client_version": client_version,
        "sdk_version": sdk_version,
        "api_candidates": api_candidates,
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
    try:
        kclaim = ks.get_claim(knowledge_key)
    except Exception:
        kclaim = None
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


def command_doctor(args: argparse.Namespace) -> None:
    c = con()
    meta = {r[0]: json.loads(r[1]) for r in c.execute("SELECT key,value FROM meta")}
    runtime = c.execute("SELECT COUNT(*) FROM facts WHERE status='runtime-verified'").fetchone()[0]
    direct = c.execute("SELECT COUNT(*) FROM facts WHERE source_id IN (%s)" % ",".join("?"*len(DIRECT_SOURCES)), tuple(sorted(DIRECT_SOURCES))).fetchone()[0]
    try:
        meta["legacy_source_runs"] = c.execute("SELECT COUNT(*) FROM source_runs").fetchone()[0]
        meta["legacy_sources_with_reviewers"] = c.execute("SELECT COUNT(*) FROM source_provenance WHERE has_reviewer=1").fetchone()[0]
    except sqlite3.OperationalError:
        meta["legacy_source_runs"] = 0
        meta["legacy_sources_with_reviewers"] = 0
    c.close()
    meta["runtime_verified"] = runtime
    meta["direct_ecosystem_facts"] = direct
    meta["db"] = str(DB)
    try:
        meta["agent_knowledge"] = ks.stats()
    except Exception as e:
        meta["agent_knowledge"] = {"error": str(e)}
    print(json.dumps(meta, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Local evidence-aware retrieval over the ExteraGram plugin wiki")
    sp = p.add_subparsers(dest="cmd", required=True)

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
    return p


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
