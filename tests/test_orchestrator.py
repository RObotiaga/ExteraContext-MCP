#!/usr/bin/env python3
from __future__ import annotations
import json, os, subprocess, sys, tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ORCH=ROOT/'scripts'/'orchestrate.py'
PYTHON=sys.executable


def run(args, env, stdin=None):
    cp=subprocess.run([PYTHON,str(ORCH),*args],cwd=ROOT,env=env,text=True,encoding='utf-8',capture_output=True,input=stdin)
    if cp.returncode != 0:
        raise AssertionError(f"command failed: {args}\nstdout={cp.stdout}\nstderr={cp.stderr}")
    return json.loads(cp.stdout)


def run_fail(args, env, contains=None, stdin=None):
    cp=subprocess.run([PYTHON,str(ORCH),*args],cwd=ROOT,env=env,text=True,encoding='utf-8',capture_output=True,input=stdin)
    assert cp.returncode != 0, (args,cp.stdout,cp.stderr)
    if contains:
        assert contains.lower() in (cp.stderr+cp.stdout).lower(), (contains, cp.stdout, cp.stderr)
    return cp


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return '@'+str(path)


def main():
    with tempfile.TemporaryDirectory() as td0:
        td=Path(td0); db=td/'knowledge.sqlite'; rr=td/'runs'
        env=os.environ.copy(); env['EXTERACONTEXT_KNOWLEDGE_DB']=str(db); env['PYTHONUTF8']='1'; env['PYTHONIOENCODING']='utf-8'
        sys.path.insert(0, str(ROOT / 'scripts'))
        from prepare_ci_fixture import create_fixture
        env.update(EXTERACONTEXT_DB=str(create_fixture(td / 'base.sqlite')),
                   EXTERACONTEXT_AUTO_SYNC='0', EXTERACONTEXT_REQUIRE_MANIFEST='0')
        evidence=[{
          "evidence_type":"source","evidence_status":"code","source_type":"target-code",
          "repository":"owner/repo","commit":"abc123","path":"plugin.py","lines":"10-20",
          "client":"ExteraGram","platform":"Android","client_version":"12.10.1","sdk_version":"1.4.5.5",
          "excerpt":"Проверка → def on_send_message_hook(account, params): ... ✓"
        }]
        target={"client":"ExteraGram","platform":"Android","client_version":"12.10.1","sdk_version":"1.4.5.5"}
        ev=write_json(td/'evidence.json', evidence); tp=write_json(td/'target.json', target)

        # Reflect issues a collector capability but stores only its hash.
        x=run(['--run-root',str(rr),'reflect','--task','Проверка → outgoing hook ✓','--target',tp,'--evidence',ev,'--discovery','on_send_message_hook account'],env)
        oid=x['orchestration_id']; od=rr/oid; collector_token=x['collector_token']
        assert collector_token.startswith('kc_col_')
        st=json.loads((od/'state.json').read_text(encoding='utf-8'))
        assert st['evidence'][0]['evidence_id']=='source-001'
        assert 'collector_token_hash' in st and collector_token not in (od/'state.json').read_text(encoding='utf-8')
        assert not (od/'verifier-phase-a.prompt.md').exists()

        col={"action":"propose","claim":"on_send_message_hook receives the triggering account.","api_symbol":"on_send_message_hook","kind":"behavior","scope":"target","target":target,"evidence_status":"code","evidence_refs":["source-001"],"existing_claim":None,"conflicts_with":[],"notes":None}
        cp=write_json(td/'collector.json', col)

        # Wrong orchestration/token and guessed identities must not authorize anything.
        run_fail(['--run-root',str(rr),'collector-result','--id',oid,'--actor-token','kc_col_wrong-token-xxxxxxxx','--result',cp],env,'invalid collector actor token')
        foreign=run(['--run-root',str(rr),'reflect','--task','foreign orchestration','--target',tp,'--evidence',ev],env)
        run_fail(['--run-root',str(rr),'collector-result','--id',oid,'--actor-token',foreign['collector_token'],'--result',cp],env,'invalid collector actor token')

        collector_runtime=write_json(td/'collector-runtime.json', {"provider":"dsh","child_id":"collector-child-real"})
        c=run(['--run-root',str(rr),'collector-result','--id',oid,'--actor-token',collector_token,'--result',cp,'--model','cheap-model','--runtime-actor',collector_runtime],env)
        verifier_token=c['verifier_token']
        assert verifier_token.startswith('kc_ver_') and verifier_token != collector_token
        pa_prompt=(od/'verifier-phase-a.prompt.md').read_text(encoding='utf-8')
        assert 'COLLECTOR CANDIDATE' not in pa_prompt and col['claim'] not in pa_prompt

        # Collector token cannot be replayed after stage transition.
        run_fail(['--run-root',str(rr),'collector-result','--id',oid,'--actor-token',collector_token,'--result',cp],env,'expected collector-ready')

        # Collector cannot invent evidence refs.
        bad=dict(col); bad['evidence_refs']=['made-up-evidence']; bp=write_json(td/'bad.json', bad)
        y=run(['--run-root',str(rr),'reflect','--task','bad ref','--target',tp,'--evidence',ev],env)
        run_fail(['--run-root',str(rr),'collector-result','--id',y['orchestration_id'],'--actor-token',y['collector_token'],'--result',bp],env,'unknown original evidence')
        # Failed validation does not consume the capability; retrying with grounded evidence can proceed.
        yc=run(['--run-root',str(rr),'collector-result','--id',y['orchestration_id'],'--actor-token',y['collector_token'],'--result',cp],env)

        pa={"statement":"The hook signature shows account is passed to the outgoing hook.","scope":target,"evidence_status":"code","uncertainty":None,"conflicts_seen":[]}
        pap=write_json(td/'pa.json', pa)
        pb={"verdict":"accept","final_statement":None,"existing_subject_type":None,"existing_subject_id":None,"notes":"Matches blind extraction."}
        pbp=write_json(td/'pb.json', pb)

        # Phase B cannot happen before Phase A and collector capability cannot act as verifier.
        run_fail(['--run-root',str(rr),'phase-b-result','--id',oid,'--actor-token',verifier_token,'--result',pbp],env,'expected phase-b-ready')
        run_fail(['--run-root',str(rr),'phase-a-result','--id',oid,'--actor-token',collector_token,'--result',pap],env,'invalid verifier actor token')
        run_fail(['--run-root',str(rr),'phase-a-result','--id',oid,'--actor-token',yc['verifier_token'],'--result',pap],env,'invalid verifier actor token')

        # When runtime child metadata exists, collector/verifier must be different.
        run_fail(['--run-root',str(rr),'phase-a-result','--id',oid,'--actor-token',verifier_token,'--result',pap,'--runtime-actor',collector_runtime],env,'different from collector')
        verifier_runtime=write_json(td/'verifier-runtime.json', {"provider":"dsh","child_id":"verifier-child-real"})
        run(['--run-root',str(rr),'phase-a-result','--id',oid,'--actor-token',verifier_token,'--result',pap,'--model','strong-model','--runtime-actor',verifier_runtime],env)

        # Wrong token, missing identity, or a different Phase-B child is rejected when identity was attested in Phase A.
        run_fail(['--run-root',str(rr),'phase-b-result','--id',oid,'--actor-token',collector_token,'--result',pbp,'--runtime-actor',verifier_runtime],env,'invalid verifier actor token')
        run_fail(['--run-root',str(rr),'phase-b-result','--id',oid,'--actor-token',verifier_token,'--result',pbp],env,'must supply matching runtime actor')
        other_runtime=write_json(td/'other-runtime.json', {"provider":"dsh","child_id":"other-verifier"})
        run_fail(['--run-root',str(rr),'phase-b-result','--id',oid,'--actor-token',verifier_token,'--result',pbp,'--runtime-actor',other_runtime],env,'does not match')
        z=run(['--run-root',str(rr),'phase-b-result','--id',oid,'--actor-token',verifier_token,'--result',pbp,'--runtime-actor',verifier_runtime],env)
        assert z['result']['state']=='verified'
        run_fail(['--run-root',str(rr),'phase-b-result','--id',oid,'--actor-token',verifier_token,'--result',pbp,'--runtime-actor',verifier_runtime],env,'expected phase-b-ready')

        # Status never exposes capability secrets/hashes.
        status=run(['--run-root',str(rr),'status','--id',oid],env)
        serialized=json.dumps(status)
        assert collector_token not in serialized and verifier_token not in serialized
        assert 'collector_token_hash' not in status and 'verifier_token_hash' not in status
        assert status['collector_token_consumed'] is True and status['verifier_token_consumed'] is True

        # Persisted provenance came from ORIGINAL evidence, not collector-authored values.
        sys.path.insert(0,str(ROOT/'scripts')); import knowledge_store as ks
        claim=ks.get_claim(z['result']['claim_id'],db_path=db)
        assert claim['evidence'][0]['repository']=='owner/repo'
        assert claim['evidence'][0]['commit_sha']=='abc123'
        assert claim['created_by_run'] != claim['verified_by_run']

        # Real DSH failure mode from acceptance test: no provider/child metadata at all must still complete.
        sentinel_evidence=[{
          "evidence_type":"source","evidence_status":"docs","source_type":"project-fixture",
          "path":"mcp-test-fixtures/writeback-evidence.md",
          "excerpt":"For the ExteraContext MCP acceptance test project only, EXTERACONTEXT_WRITEBACK_SENTINEL has the value 7319."
        }]
        qev=write_json(td/'sentinel-evidence.json',sentinel_evidence)
        q=run(['--run-root',str(rr),'reflect','--task','Project sentinel','--target',tp,'--evidence',qev],env)
        qcol=dict(col); qcol['claim']='EXTERACONTEXT_WRITEBACK_SENTINEL has the value 7319 in this acceptance-test project.'; qcol['api_symbol']=None; qcol['kind']='other'; qcol['scope']='project'; qcol['evidence_status']='docs'
        qcp=write_json(td/'qcol.json',qcol)
        qc=run(['--run-root',str(rr),'collector-result','--id',q['orchestration_id'],'--actor-token',q['collector_token'],'--result',qcp],env)
        qpa=dict(pa); qpa['statement']='The supplied project fixture states that EXTERACONTEXT_WRITEBACK_SENTINEL has the value 7319.'; qpa['scope']={'scope':'project'}; qpa['evidence_status']='docs'
        qpap=write_json(td/'qpa.json',qpa)
        run(['--run-root',str(rr),'phase-a-result','--id',q['orchestration_id'],'--actor-token',qc['verifier_token'],'--result',qpap],env)
        qz=run(['--run-root',str(rr),'phase-b-result','--id',q['orchestration_id'],'--actor-token',qc['verifier_token'],'--result',pbp],env)
        assert qz['result']['state']=='verified'
        # A fresh read process sees the persisted trusted claim through the normal retrieval path.
        qr=subprocess.run([PYTHON,str(ROOT/'scripts'/'query.py'),'search','EXTERACONTEXT_WRITEBACK_SENTINEL','--limit','10','--format','json'],cwd=ROOT,env=env,text=True,encoding='utf-8',capture_output=True)
        assert qr.returncode == 0, qr.stderr
        qdata=json.loads(qr.stdout)
        assert any('7319' in str(item.get('claim','')) for item in qdata), qdata

        # Test stdin token passing across collector-result, phase-a-result, phase-b-result
        s_orch = run(['--run-root',str(rr),'reflect','--task','Stdin orchestration','--target',tp,'--evidence',qev],env)
        s_oid = s_orch['orchestration_id']
        s_col_token = s_orch['collector_token']
        # 1. collector-result with --actor-token-stdin
        s_c = run(['--run-root',str(rr),'collector-result','--id',s_oid,'--actor-token-stdin','--result',qcp],env,stdin=s_col_token)
        s_ver_token = s_c['verifier_token']
        # 2. phase-a-result with --actor-token -
        run(['--run-root',str(rr),'phase-a-result','--id',s_oid,'--actor-token','-','--result',qpap],env,stdin=s_ver_token)
        # 3. phase-b-result with omitted --actor-token
        s_z = run(['--run-root',str(rr),'phase-b-result','--id',s_oid,'--result',pbp],env,stdin=s_ver_token)
        assert s_z['result']['state']=='verified'

        print('orchestrator: ok')

class OrchestratorTests(unittest.TestCase):
    def test_capability_stages_blinding_and_persisted_retrieval(self):
        main()


if __name__ == '__main__':
    unittest.main()
