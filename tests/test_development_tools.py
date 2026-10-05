from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import development_tools as dev

def trace(duration=100_298_000,last=59_988_288):
    measurements={'bytesBefore':9168733,'bytesAfter':9168733,'tracks':[
        {'mime':'video/avc','samples':1800,'firstPtsUs':0,'maxPtsUs':last},
        {'mime':'audio/mp4a-latm','samples':2815,'firstPtsUs':0,'maxPtsUs':last+50_000}]}
    events=[{'event':'plugin.load.start','version':'0.1.19','dex_sha256':'3'*64},
        {'event':'camera.source.audit','job':'job','phase':'camera_file','expected_us':duration,'measurement':measurements},
        {'event':'camera.source.audit','job':'job','phase':'saved_copy','expected_us':duration,'measurement':measurements},
        {'event':'split.part','job':'job','part':1,'duration_us':59_000_000},
        {'event':'split.part','job':'job','part':2,'duration_us':duration-59_000_000},
        {'event':'send.ack','job':'job','acknowledged_part':1},
        {'event':'send.ack','job':'job','acknowledged_part':2},
        {'event':'queue.done','job':'job'}]
    return '\n'.join(json.dumps(dict(e,session='one',seq=i+1,version='0.1.19')) for i,e in enumerate(events))

class DevelopmentToolsTests(unittest.TestCase):
    def test_channel_metadata_does_not_claim_download_or_verified_build(self):
        html='''<div class="tgme_widget_message" data-post="exteraReleases/158"><a class="tgme_widget_message_document_wrap" href="https://t.me/exteraReleases/158?single"><div class="tgme_widget_message_document_title">exteraGram-full-universal-20261004.apk</div><div class="tgme_widget_message_document_extra">111.4 MB</div></a><time datetime="2026-10-04T18:07:34Z"></time></div>'''
        found=dev.parse_releases(html,'exteraReleases')
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]['variant'],'full')
        self.assertIsNone(found[0]['download_url'])
        self.assertFalse(found[0]['apk_identity_verified'])
        self.assertEqual(dev.parse_releases(html.replace('https://t.me/exteraReleases/158','https://localhost/private'),'exteraReleases'),[])
        self.assertEqual(dev.parse_releases(html,'AyuGramReleases'),[])

    def test_packet_loss_is_failure_despite_queue_done_and_identical_copy(self):
        result=dev.analyze_run({'log':trace()})
        job=result['sessions'][0]['jobs'][0]
        self.assertTrue(job['queue_done'])
        self.assertEqual(job['copy_status'],'PASS')
        self.assertEqual(job['media_status'],'FAIL')
        feature=dev.verify_feature({'feature':'long_round_camera','log':trace()})
        self.assertEqual(feature['status'],'FAIL')
        self.assertFalse(feature['runtime_verified'])

    def test_packet_coverage_and_caller_pass_do_not_attest_runtime(self):
        result=dev.verify_feature({'feature':'long_round_camera','log':trace(last=100_280_000),
            'expected_artifact':{'version':'0.1.19','dex_sha256':'3'*64},
            'observations':{'motion_after_minute':True,'speech_after_minute':True,'continuous_part_boundary':True}})
        self.assertEqual(result['status'],'EVIDENCE_CONSISTENT')
        self.assertFalse(result['runtime_verified'])
        pending=dev.verify_feature({'feature':'long_round_camera','log':trace(last=100_280_000)})
        self.assertEqual(pending['status'],'PENDING')
        self.assertIn('artifact_identity',pending['missing_checks'])

    def test_truncated_and_missing_sequences_are_explicit(self):
        result=dev.analyze_run({'log':trace().replace('"seq": 3','"seq": 99')+'\n{"truncated'})
        self.assertEqual(result['invalid_lines'],[9])
        self.assertFalse(result['trace_complete'])
        self.assertTrue(result['sessions'][0]['sequence_gaps'])

    def test_source_is_not_executed_and_dex_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker=Path(tmp)/'executed'
            plugin=Path(tmp)/'artifact.eaf'
            dex=b'dex\n035\x00fixture'
            source=f"__version__='1.2.3'\nEMBEDDED_DEX={base64.b64encode(dex).decode()!r}\nDEX_SHA256={'0'*64!r}\nopen({str(marker)!r},'w').write('bad')\n"
            with zipfile.ZipFile(plugin,'w') as archive:
                archive.writestr('main.py',source)
                archive.writestr('classes.dex',dex)
            result=dev.inspect_plugin_artifact({'path':str(plugin)})
            self.assertEqual(result['artifact_status'],'FAIL')
            self.assertEqual(result['dex_sha256'],hashlib.sha256(dex).hexdigest())
            self.assertFalse(marker.exists())
            self.assertEqual(result['runtime_status'],'PENDING')

    def test_malformed_audit_is_reported_not_executed_or_hidden(self):
        log=json.dumps({'session':'bad','event':'camera.source.audit','measurement':{'tracks':[None]}})
        self.assertEqual(dev.analyze_run({'log':log})['invalid_lines'],[1])

    def test_missing_tail_parts_and_ack_prevent_acceptance(self):
        log=trace(last=100_280_000).replace('"duration_us": 41298000','"duration_us": 1000000')
        result=dev.verify_feature({'feature':'long_round_camera','log':log})
        self.assertEqual(result['status'],'FAIL')

    def test_preparing_probe_never_claims_client_execution(self):
        prepared=dev.probe_bridge_contract({})
        self.assertEqual(prepared['status'],'PENDING')
        self.assertFalse(prepared['runtime_verified'])
        self.assertIn('owner.unhook_method(handle)',prepared['probe_source'])
        partial=dev.probe_bridge_contract({'report':{'schema':1,'checks':{'reflection_class':True}}})
        self.assertEqual(partial['status'],'PENDING')
        failed=dev.probe_bridge_contract({'report':{'schema':1,'checks':{'reflection_class':False}}})
        self.assertEqual(failed['status'],'FAIL')

if __name__=='__main__': unittest.main()
