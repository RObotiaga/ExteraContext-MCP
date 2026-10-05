from __future__ import annotations
import base64
import ast
import hashlib
import json
import io
import re
from types import SimpleNamespace
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import development_tools as dev
import public_apks

def trace(duration=100_298_000,last=59_988_288):
    measurements={'bytesBefore':9168733,'bytesAfter':9168733,'tracks':[
        {'mime':'video/avc','samples':1800,'firstPtsUs':0,'maxPtsUs':last,'maxGapUs':33_334},
        {'mime':'audio/mp4a-latm','samples':2815,'firstPtsUs':0,'maxPtsUs':last+50_000,'maxGapUs':21_334}]}
    events=[{'event':'plugin.load.start','version':'0.1.19','dex_sha256':'3'*64,'python_sha256':'1'*64,'artifact_sha256':'2'*64,'package':'com.example.client','version_code':1,'apk_sha256':'5'*64},
        {'event':'queue.created','job':'job','source':'camera'},
        {'event':'camera.source.audit','job':'job','phase':'camera_file','expected_us':duration,'measurement':measurements},
        {'event':'camera.source.audit','job':'job','phase':'saved_copy','expected_us':duration,'measurement':measurements},
        {'event':'split.part','job':'job','part':1,'duration_us':59_000_000},
        {'event':'split.part','job':'job','part':2,'duration_us':duration-59_000_000},
        {'event':'send.ack','job':'job','acknowledged_part':1},
        {'event':'send.ack','job':'job','acknowledged_part':2},
        {'event':'queue.done','job':'job','total':2}]
    return '\n'.join(json.dumps(dict(e,session='one',seq=i+1,version='0.1.19')) for i,e in enumerate(events))

class DevelopmentToolsTests(unittest.TestCase):
    def test_generated_probe_observes_loaded_sdk_and_keeps_unknown_pending(self):
        prepared=dev.probe_bridge_contract({})
        tree=ast.parse(prepared['probe_source'])
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='observe_sdk_identity')
        sdk=SimpleNamespace(__version__='1.4.5.0',version=(1,4,5,0),version_str='1.4.5.0',__file__='/runtime/_sdk_version.so')
        namespace={'sys':SimpleNamespace(modules={'_sdk_version':sdk}),'re':re}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'owned-probe-helper','exec'),namespace)
        observation=namespace['observe_sdk_identity']()
        self.assertEqual(observation['version'],'1.4.5.0')
        self.assertEqual(observation['source'],'sys.modules._sdk_version.__version__')
        checks={name:True for name in prepared['required_checks']}
        report={'schema':1,'checks':checks,'sdk_runtime':'1.4.5.0','sdk_identity':observation}
        self.assertEqual(dev.probe_bridge_contract({'report':report})['status'],'EVIDENCE_CONSISTENT')
        del namespace['sys'].modules['_sdk_version']
        self.assertIsNone(namespace['observe_sdk_identity']())
        for change in [{'sdk_identity':None},{'sdk_runtime':'different'},{'checks':dict(checks,actual_sdk_identity=None)}]:
            result=dev.probe_bridge_contract({'report':dict(report,**change)})
            self.assertEqual(result['status'],'PENDING')
            self.assertIn('actual_sdk_identity',result['missing_checks'])
        namespace['sys'].modules['_sdk_version']=SimpleNamespace(__version__='1.4.5.0',version_str='1.4.3.1',__file__='/runtime/_sdk_version.so')
        with self.assertRaises(ValueError): namespace['observe_sdk_identity']()

    def test_missing_pts_is_counted_and_cannot_establish_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'video.mp4'
            path.write_bytes(b'media fixture')
            streams={'streams':[{'index':0,'codec_type':'video','codec_name':'h264'}, {'index':1,'codec_type':'audio','codec_name':'aac'}]}
            def probe(args,**kwargs):
                if '-show_streams' in args:
                    return SimpleNamespace(returncode=0,stdout=json.dumps(streams).encode())
                kwargs['stdout'].write(b'0,0\n0,N/A\n0,0.5\n1,0\n1,0.5\n')
                return SimpleNamespace(returncode=0)
            with patch.object(dev.shutil,'which',return_value='ffprobe'), patch.object(dev.subprocess,'run',side_effect=probe):
                measured=dev.inspect_media_packets({'path':str(path)})['measurement']['tracks'][0]
            self.assertEqual(measured['samples'],3)
            self.assertEqual(measured['timestampedSamples'],2)
            self.assertEqual(measured['missingPtsSamples'],1)
            self.assertFalse(measured['ptsComplete'])
        events=[json.loads(line) for line in trace(last=100_280_000).splitlines()]
        for e in events:
            if 'measurement' in e:
                e['measurement']['tracks'][0].update(missingPtsSamples=1,ptsComplete=False)
        log='\n'.join(json.dumps(e) for e in events)
        self.assertEqual(dev.analyze_run({'log':log})['sessions'][0]['jobs'][0]['media_status'],'PENDING')
        for e in events:
            if 'measurement' in e:
                track=e['measurement']['tracks'][0]
                for key in ['firstPtsUs','maxPtsUs','maxGapUs']: track.pop(key,None)
        self.assertEqual(dev.analyze_run({'log':'\n'.join(json.dumps(e) for e in events)})['sessions'][0]['jobs'][0]['media_status'],'PENDING')

    def test_download_budget_allows_slow_valid_transfer_and_cleans_timeout(self):
        data={'channel':'AyuGramReleases','repository':'AyuGram/AyuGram4A','asset_id':1}
        payload=io.BytesIO()
        with zipfile.ZipFile(payload,'w') as archive:
            archive.writestr('AndroidManifest.xml',b'manifest')
            archive.writestr('classes.dex',b'dex')
        raw=payload.getvalue()
        asset={'id':1,'size':len(raw),'name':'release.apk','browser_download_url':'https://github.com/AyuGram/AyuGram4A/releases/download/v1/release.apk'}
        with tempfile.TemporaryDirectory() as tmp:
            taskroot=Path(tmp)
            fake_script=taskroot/'scripts'/'public_apks.py'
            for times,success in [([0,90,100],True),([0,151],False)]:
                with patch.object(public_apks,'__file__',str(fake_script)), patch.object(public_apks,'api',return_value=asset), patch.object(public_apks,'fetch',return_value=io.BytesIO(raw)), patch.object(public_apks.time,'monotonic',side_effect=times):
                    if success:
                        result=public_apks.download_client_apk(data)
                        path=Path(result['path'])
                        self.assertEqual(result['apk_sha256'],hashlib.sha256(raw).hexdigest())
                        path.unlink()
                    else:
                        with self.assertRaisesRegex(ValueError,'timed out'):
                            public_apks.download_client_apk(data)
                        self.assertEqual(list((taskroot/'.cache/public-apks').iterdir()),[])

    def test_apk_summary_preserves_version_name_with_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'client.apk'
            with zipfile.ZipFile(path,'w') as archive:
                archive.writestr('lib/arm64-v8a/libtest.so',b'test')
            with patch.object(dev,'analyzer',return_value='com.example.client 70079 10.0.1 Beta\n'):
                result=dev.inspect_client_apk({'path':str(path)})
            self.assertEqual(result['client_version'],'10.0.1 Beta')
            self.assertEqual(result['version_code'],70079)
            for bad in ['com.example.client 70079','com.example.client invalid 10.0.1 Beta']:
                with patch.object(dev,'analyzer',return_value=bad), self.assertRaises(ValueError):
                    dev.inspect_client_apk({'path':str(path)})

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
            'expected_artifact':{'version':'0.1.19','dex_sha256':'3'*64,'python_sha256':'1'*64,'artifact_sha256':'2'*64,'package':'com.example.client','version_code':1,'apk_sha256':'5'*64},
            'observations':{'motion_after_minute':True,'speech_after_minute':True,'continuous_part_boundary':True}})
        self.assertEqual(result['status'],'EVIDENCE_CONSISTENT')
        self.assertFalse(result['runtime_verified'])
        pending=dev.verify_feature({'feature':'long_round_camera','log':trace(last=100_280_000)})
        self.assertEqual(pending['status'],'PENDING')
        self.assertIn('artifact_identity',pending['missing_checks'])

    def test_truncated_and_missing_sequences_are_explicit(self):
        result=dev.analyze_run({'log':trace().replace('"seq": 3','"seq": 99')+'\n{"truncated'})
        self.assertEqual(result['invalid_lines'],[10])
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

    def test_late_packets_missing_sequences_reload_and_camera_as_import_are_rejected(self):
        good=trace(last=100_280_000)
        args={'feature':'long_round_camera','expected_artifact':{'version':'0.1.19','dex_sha256':'3'*64},
              'observations':{'motion_after_minute':True,'speech_after_minute':True,'continuous_part_boundary':True}}
        late=good.replace('"firstPtsUs": 0','"firstPtsUs": 99000000')
        self.assertEqual(dev.verify_feature(dict(args,log=late))['status'],'FAIL')
        missing='\n'.join(json.dumps({k:v for k,v in json.loads(line).items() if k!='seq'}) for line in good.splitlines())
        self.assertNotEqual(dev.verify_feature(dict(args,log=missing))['status'],'EVIDENCE_CONSISTENT')
        reload=good+'\n'+json.dumps({'session':'one','seq':10,'version':'0.1.19','event':'plugin.load.start','dex_sha256':'4'*64})
        self.assertEqual(dev.verify_feature(dict(args,log=reload))['status'],'FAIL')
        self.assertNotEqual(dev.verify_feature(dict(args,log=good,feature='import_round'))['status'],'EVIDENCE_CONSISTENT')

    def test_apk_request_keeps_exact_requirements_and_rejects_other_build(self):
        target={'client':'AyuGram','package':'com.radolyn.ayugram','version_code':70079,'apk_sha256':'8'*64}
        needed=dev.request_client_apk({'target':target,'reason':'Inspect recorder limit'})
        self.assertEqual(needed['status'],'USER_APK_REQUIRED')
        self.assertEqual(needed['requirements'],target)
        with patch.object(dev,'inspect_client_apk',return_value={'package':'other.client','version_code':70079,'apk_sha256':'8'*64}):
            wrong=dev.request_client_apk({'target':target,'path':'other.apk'})
        self.assertEqual(wrong['status'],'APK_MISMATCH')
        self.assertFalse(wrong['target_identity_verified'])

    def test_apk_request_does_not_infer_variant_from_build_identity(self):
        target={'package':'com.example.client','version_code':1,'apk_sha256':'8'*64,'variant':'full'}
        with patch.object(dev,'inspect_client_apk',return_value=dict(target)):
            inspected=dev.request_client_apk({'target':target,'path':'client.apk'})
        self.assertEqual(inspected['status'],'APK_INSPECTED')
        self.assertFalse(inspected['target_identity_verified'])
        self.assertIn('variant',inspected['unbound_fields'])

    def test_apk_request_rejects_wrong_client_and_keeps_unknown_client_unbound(self):
        actual={'package':'com.exteragram.messenger','version_code':1,'apk_sha256':'8'*64}
        with patch.object(dev,'inspect_client_apk',return_value=actual):
            wrong=dev.request_client_apk({'target':dict(actual,client='AyuGram'),'path':'client.apk'})
            unknown=dev.request_client_apk({'target':dict(actual,client='OtherClient'),'path':'client.apk'})
        self.assertEqual(wrong['status'],'APK_MISMATCH')
        self.assertFalse(wrong['target_identity_verified'])
        self.assertFalse(unknown['target_identity_verified'])
        self.assertIn('client',unknown['unbound_fields'])

    def test_later_contradictory_audit_prevents_feature_acceptance(self):
        log=trace(last=100_280_000)
        expected=json.loads(log.splitlines()[0])
        expected={k:v for k,v in expected.items() if k in {'version','dex_sha256','python_sha256','artifact_sha256','package','version_code','apk_sha256'}}
        args={'feature':'long_round_camera','expected_artifact':expected,'observations':{k:True for k in ['motion_after_minute','speech_after_minute','continuous_part_boundary']}}
        self.assertEqual(dev.verify_feature(dict(args,log=log))['status'],'EVIDENCE_CONSISTENT')
        for phase in ['camera_file','saved_copy']:
            later=json.loads(trace().splitlines()[2])
            later.update(seq=10,phase=phase)
            self.assertEqual(dev.verify_feature(dict(args,log=log+'\n'+json.dumps(later)))['status'],'FAIL')

    def test_job_identifier_is_not_reused_across_load_generations(self):
        events=[json.loads(line) for line in trace(last=100_280_000).splitlines()]
        expected={k:v for k,v in events[0].items() if k in {'version','dex_sha256','python_sha256','artifact_sha256','package','version_code','apk_sha256'}}
        events.extend([dict(expected,event='plugin.load.start'),{'event':'queue.created','job':'job','source':'imported'},
                       {'event':'send.ack','job':'job','acknowledged_part':1},{'event':'queue.done','job':'job','total':2}])
        log='\n'.join(json.dumps(dict(e,session='one',seq=i+1,version='0.1.19')) for i,e in enumerate(events))
        result=dev.verify_feature({'feature':'long_round_camera','log':log,'expected_artifact':expected,
                                 'observations':{k:True for k in ['motion_after_minute','speech_after_minute','continuous_part_boundary']}})
        self.assertEqual(result['status'],'PENDING')
        self.assertEqual([j['generation'] for j in result['analysis']['sessions'][0]['jobs']],[1,2])
        self.assertEqual(result['analysis']['sessions'][0]['jobs'][1]['media_status'],'PENDING')

    def test_later_load_cannot_attest_an_earlier_job(self):
        events=[json.loads(line) for line in trace(last=100_280_000).splitlines()]
        loaded=events.pop(0)
        expected={k:v for k,v in loaded.items() if k in {'version','dex_sha256','python_sha256','artifact_sha256','package','version_code','apk_sha256'}}
        events.append(loaded)
        log='\n'.join(json.dumps(dict(e,session='one',seq=i+1)) for i,e in enumerate(events))
        result=dev.verify_feature({'feature':'long_round_camera','log':log,'expected_artifact':expected,
                                 'observations':{k:True for k in ['motion_after_minute','speech_after_minute','continuous_part_boundary']}})
        self.assertEqual(result['status'],'PENDING')
        self.assertFalse(result['gates']['artifact_identity'])
        self.assertFalse(result['gates']['installed_identity'])
        self.assertFalse(result['gates']['load_attribution'])

    def test_small_negative_encoder_priming_is_not_recording_loss(self):
        events=[json.loads(line) for line in trace(last=100_280_000).splitlines()]
        for event in events:
            if 'measurement' in event:
                event['measurement']['tracks'][1]['firstPtsUs']=-21_333
        log='\n'.join(json.dumps(event) for event in events)
        job=dev.analyze_run({'log':log})['sessions'][0]['jobs'][0]
        self.assertEqual(job['media_status'],'PACKET_COVERAGE_OBSERVED')
        extreme=log.replace('-21333','-10000000')
        self.assertEqual(dev.analyze_run({'log':extreme})['sessions'][0]['jobs'][0]['media_status'],'FAIL')

    def test_import_requires_import_route_and_media_evidence(self):
        log=trace(last=100_280_000).replace('"source": "camera"','"source": "imported"').replace('camera.source.audit','media.source.audit').replace('camera_file','source_file')
        expected=json.loads(log.splitlines()[0])
        expected={k:v for k,v in expected.items() if k in {'version','dex_sha256','python_sha256','artifact_sha256','package','version_code','apk_sha256'}}
        result=dev.verify_feature({'feature':'import_round','log':log,'expected_artifact':expected,'observations':{k:True for k in ['square_crop_matches_preview','sound_preserved','continuous_part_boundary','gallery_route','message_route','fallback_route']}})
        self.assertEqual(result['status'],'EVIDENCE_CONSISTENT')
        self.assertFalse(result['runtime_verified'])
        missing=dev.verify_feature({'feature':'import_round','log':log,'expected_artifact':expected})
        self.assertEqual(missing['status'],'PENDING')
        self.assertIn('gallery_route',missing['missing_checks'])

    def test_anonymous_asset_origin_and_repository_selection_are_bounded(self):
        asset={'id':1,'size':10,'name':'release.apk','browser_download_url':'https://github.com/AyuGram/AyuGram4A/releases/download/v1/release.apk'}
        record=public_apks.asset_record(asset,'AyuGram/AyuGram4A','AyuGramReleases')
        self.assertEqual(record['telegram_attachment_equivalence'],'unverified')
        self.assertIsNone(public_apks.asset_record(dict(asset,browser_download_url='https://evil.invalid/release.apk'),'AyuGram/AyuGram4A','AyuGramReleases'))
        for url in ['http://github.com/file','https://github.com:444/file','https://user:secret@github.com/file','https://localhost/file']:
            with self.assertRaises(ValueError): public_apks.validate_url(url)
        with self.assertRaises(ValueError): public_apks.download_client_apk({'channel':'AyuGramReleases','repository':'exteraSquad/exteraGram','asset_id':1})

if __name__=='__main__': unittest.main()
