#!/usr/bin/env python3
"""Target-aware context retrieval over disposable, offline FTS fixtures."""
from __future__ import annotations

import sqlite3
import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import query  # noqa: E402


class TargetRetrievalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "base.sqlite"
        c = sqlite3.connect(self.db)
        c.executescript("""
            CREATE TABLE facts (id TEXT PRIMARY KEY, claim TEXT, api TEXT, recipe TEXT,
                topic TEXT, canonical_topic TEXT, source_id TEXT, version TEXT,
                platform TEXT, status TEXT, evidence_url TEXT, evidence_path TEXT);
            CREATE VIRTUAL TABLE facts_fts USING fts5(id UNINDEXED, claim, api, recipe,
                topic, canonical_topic, source_id, version, platform);
            CREATE TABLE docs (path TEXT PRIMARY KEY, title TEXT, kind TEXT, content TEXT);
            CREATE VIRTUAL TABLE docs_fts USING fts5(path UNINDEXED, title, content);
        """)
        self.c = c
        self.addCleanup(c.close)
        self.db_patch = patch.object(query, "DB", self.db)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.overlay_patch = patch.object(query, "search_dynamic_facts", return_value=[])
        self.overlay_patch.start()
        self.addCleanup(self.overlay_patch.stop)

    def add(self, id, claim, api, version="", status="code"):
        source = id.split(":")[0]
        vals = (id, claim, api, "", "", "", source, version, "Android", status, "", "")
        self.c.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", vals)
        self.c.execute("INSERT INTO facts_fts VALUES (?,?,?,?,?,?,?,?,?)",
                       (id, claim, api, "", "", "", source, version, "Android"))
        self.c.commit()

    def packet(self, q, client=None, sdk=None):
        return query.context_packet(q, "ExteraGram Android", client, sdk, 10)

    def test_generic_donor_evidence_does_not_become_target_api(self):
        self.add("nagram:x", "network queue message handler", "DonorMessageHandler", status="runtime-verified")
        self.add("official-sdk:x", "network queue message handler", "run_on_queue", status="docs")
        facts = query.search_facts("network queue message handler", 10)
        self.assertTrue(any(f["id"] == "nagram:x" for f in facts))
        p = self.packet("network queue message handler")
        self.assertEqual(p["facts"][0]["id"], "official-sdk:x")
        self.assertNotIn("DonorMessageHandler", p["api_candidates"])
        self.assertIn("DonorMessageHandler", p["donor_api_candidates"])
        self.assertIn("donor", p["facts"][1]["directness"])
        self.assertIn("Донорские API", query.render_context_md(p))

    def test_explicit_donor_symbol_kept_and_labeled(self):
        self.add("nagram:x", "ObserversGroup removes observers", "ObserversGroup")
        self.add("official-sdk:x", "ObserversGroup generic observer", "NotificationCenter")
        p = self.packet("ObserversGroup")
        self.assertEqual(p["facts"][0]["id"], query.search_facts("ObserversGroup", 10)[0]["id"])
        self.assertIn("ObserversGroup", p["api_candidates"])
        self.assertTrue(any("Явный запрос донорского символа" in w for w in p["warnings"]))

    def test_exact_structured_versions_not_substrings_or_unstructured(self):
        self.add("official-sdk:exact", "route account message", "account", "client 12.10.1; SDK 1.4.5.5")
        self.add("official-sdk:other", "route account message", "account", "client 12.10.10; SDK 1.4.5.50")
        self.add("official-sdk:unknown", "route account message", "account", "commit 12.10.1; requested radar 1.4.5.5")
        p = self.packet("route account message", "12.10.1", "1.4.5.5")
        relations = {f["id"]: f["target_version_match"] for f in p["facts"]}
        self.assertEqual(relations["official-sdk:exact"], {"client": "match", "sdk": "match"})
        self.assertEqual(relations["official-sdk:other"], {"client": "mismatch", "sdk": "mismatch"})
        self.assertEqual(relations["official-sdk:unknown"], {"client": "unknown", "sdk": "unknown"})
        self.assertEqual(p["facts"][0]["id"], "official-sdk:exact")
        fake = self.packet("route account message", "1.0", "0.0.1")
        self.assertFalse(any("match" in f["target_version_match"].values() for f in fake["facts"]))
        self.assertTrue(any("поддержку целевой сборки считать неизвестной" in w for w in fake["warnings"]))

    def test_missing_versions_never_imply_compatibility(self):
        self.add("official-sdk:x", "queue account", "run_on_queue", "SDK page version unstated")
        p = self.packet("queue account", "1.0", "0.0.1")
        self.assertEqual(p["facts"][0]["target_version_match"], {"client": "unknown", "sdk": "unknown"})
        self.assertTrue(any("unknown" in w for w in p["warnings"]))

    def test_full_target_precedes_context_limit_and_keeps_opposing_claim(self):
        target={'client':'AyuGram','package':'com.radolyn.ayugram','version_code':70079,'apk_sha256':'8'*64}
        donors=[{'id':str(i),'score':100-i,'package':'other.client','status':'code'} for i in range(40)]
        exact={'id':'exact',**target,'score':1,'api':'testApi','claim':'testApi is supported','status':'code'}
        opposite=dict(exact,id='opposite',claim='testApi is not supported')
        output=io.StringIO()
        with patch.object(query,'search_facts',return_value=donors+[exact,opposite]) as search, contextlib.redirect_stdout(output):
            query.command_target_lookup(argparse.Namespace(query='testApi',target_json=json.dumps(target),mode='context',limit=1))
        packet=json.loads(output.getvalue())
        self.assertEqual(packet['context']['facts'][0]['id'],'exact')
        self.assertIn('opposite',[f['id'] for f in packet['assessment_facts']])
        self.assertEqual(search.call_args.args[1],501)

    def test_unspecified_target_fields_do_not_demote_detailed_evidence(self):
        target={'client':'AyuGram','package':None,'abi':''}
        detailed={'id':'detailed','client':'AyuGram','package':'com.radolyn.ayugram','abi':'arm64-v8a','score':2}
        reference={'id':'reference','client':'AyuGram','score':1}
        output=io.StringIO()
        with patch.object(query,'search_facts',return_value=[reference,detailed]), contextlib.redirect_stdout(output):
            query.command_target_lookup(argparse.Namespace(query='API',target_json=json.dumps(target),mode='facts',limit=1))
        self.assertEqual(json.loads(output.getvalue())['items'][0]['id'],'detailed')


if __name__ == "__main__":
    unittest.main()
