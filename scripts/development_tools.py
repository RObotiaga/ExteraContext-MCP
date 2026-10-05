#!/usr/bin/env python3
"""Bounded, non-executing artifact/trace inspection and explicit public release reads.

None of these tools mutates the knowledge store or attests target runtime success.
"""
from __future__ import annotations
import ast
import base64
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal, InvalidOperation
import urllib.parse
import urllib.request
import zipfile
from public_apks import list_public_apk_mirrors, download_client_apk

CHANNELS = {'exteraReleases': 'ExteraGram', 'AyuGramReleases': 'AyuGram'}
MAX_HTML = 2 * 1024 * 1024
MAX_ARTIFACT = 16 * 1024 * 1024
BOUNDARY = 'Inspection only; not trusted runtime attestation. Caller reports and channel text are untrusted data.'

class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []
    def has(self, cls):
        return cls in self.attrs.get('class', '').split()
    def nodes(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.nodes()
    def text(self):
        return ''.join(c.text() if isinstance(c, Node) else c for c in self.children).strip()

class Page(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in {'br','hr','img','meta','link','input','source','wbr','area','base','embed','param','track','col'}:
            self.stack.append(node)
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break
    def handle_data(self, data):
        self.stack[-1].children.append(data)

def parse_releases(html, channel):
    if channel not in CHANNELS:
        raise ValueError('Unknown release channel')
    page = Page(html)
    entries = []
    for post in page.root.nodes():
        if not post.has('tgme_widget_message'):
            continue
        identity = post.attrs.get('data-post', '')
        if not re.fullmatch(re.escape(channel)+r'/\d+', identity, re.I):
            continue
        descendants = list(post.nodes())
        posted_at = next((n.attrs.get('datetime') for n in descendants if n.tag == 'time'), None)
        for document in descendants:
            if not document.has('tgme_widget_message_document_wrap'):
                continue
            names = [n.text() for n in document.nodes() if n.has('tgme_widget_message_document_title')]
            if len(names) != 1 or not names[0].lower().endswith('.apk'):
                continue
            filename = names[0]
            link = document.attrs.get('href', '')
            parsed = urllib.parse.urlsplit(link)
            if parsed.scheme != 'https' or parsed.hostname != 't.me' or not re.fullmatch('/'+re.escape(channel)+r'/\d+', parsed.path, re.I):
                continue
            variant = 'full' if re.search(r'(?:^|[-_])full(?:[-_.]|$)', filename, re.I) else 'lite' if re.search(r'(?:^|[-_])lite(?:[-_.]|$)', filename, re.I) else 'unknown'
            entries.append({'client':CHANNELS[channel], 'channel':channel,'post_id':int(parsed.path.rsplit('/',1)[1]),
                'post_url':'https://t.me'+parsed.path,'filename':filename,'posted_at':posted_at,'variant':variant,
                'size_label':next((n.text() for n in document.nodes() if n.has('tgme_widget_message_document_extra')),None),
                'download_url':None,'download_status':'not_exposed_by_public_preview','auth_used':False,
                'apk_identity_verified':False,'plugin_support':'requires_apk_inspection'})
    unique = {(e['post_id'],e['filename']):e for e in entries}
    return sorted(unique.values(), key=lambda e:e['post_id'], reverse=True)

class TelegramRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p=urllib.parse.urlsplit(newurl)
        if p.scheme!='https' or p.hostname!='t.me' or p.username or p.password:
            raise ValueError('Refused release source redirect outside t.me HTTPS')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def list_client_releases(data):
    channel = data['channel']
    if channel not in CHANNELS:
        raise ValueError('Unknown release channel')
    pages=data.get('pages',1)
    before=data.get('before')
    if type(pages) is not int or not 1<=pages<=5 or (before is not None and (type(before) is not int or before<1)):
        raise ValueError('Invalid pagination')
    opener=urllib.request.build_opener(TelegramRedirect())
    releases, fetched=[],[]
    for _ in range(pages):
        url='https://t.me/s/'+channel+(f'?before={before}' if before else '')
        req=urllib.request.Request(url,headers={'User-Agent':'ExteraContext-Public-Releases/1.0','Accept':'text/html'})
        with opener.open(req,timeout=15) as response:
            raw=response.read(MAX_HTML+1)
            if len(raw)>MAX_HTML:
                raise ValueError('Public preview exceeds size limit')
            if 'html' not in response.headers.get('Content-Type','').lower():
                raise ValueError('Release source did not return HTML')
        html=raw.decode('utf-8',errors='strict')
        found=parse_releases(html,channel)
        fetched.append({'url':url,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)})
        releases.extend(found)
        ids=[int(x) for x in re.findall(r'data-post="'+re.escape(channel)+r'/(\d+)"',html,re.I)]
        if not ids or (before and min(ids)>=before):
            break
        before=min(ids)
    return {'channel':channel,'releases':list({(e['post_id'],e['filename']):e for e in releases}.values()),
        'sources':fetched,'auth_used':False,'next_before':before,'boundary':BOUNDARY,
        'warnings':['Public Telegram previews expose announcement metadata, not document bytes. No APK has been downloaded or version-verified.']}

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()

def local_file(value, limit):
    path=Path(value).expanduser().resolve(strict=True)
    if not path.is_file() or path.stat().st_size>limit:
        raise ValueError('Input must be a bounded regular file')
    return path

def analyzer_command():
    sdk=Path(os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT') or Path.home()/'AppData/Local/Android/Sdk')
    candidates=list((sdk/'cmdline-tools').glob('*/bin/apkanalyzer.bat'))+list((sdk/'cmdline-tools').glob('*/bin/apkanalyzer'))
    if candidates:
        selected=next((p for p in candidates if p.parent.parent.name=='latest'),candidates[0])
        if os.name=='nt':
            java=os.environ.get('JAVA_HOME')
            executable=str(Path(java)/'bin/java.exe') if java else shutil.which('java')
            if not executable:
                bundled=Path('C:/Program Files/Android/Android Studio/jbr/bin/java.exe')
                executable=str(bundled) if bundled.exists() else None
            if not executable:
                raise ValueError('Java is required for APK inspection')
            return [executable,'-Dcom.android.sdklib.toolsdir='+str(selected.parent.parent),'-classpath',str(selected.parent.parent/'lib/apkanalyzer-classpath.jar'),'com.android.tools.apk.analyzer.ApkAnalyzerCli']
        return [str(selected)]
    executable=shutil.which('apkanalyzer')
    if not executable:
        raise ValueError('Android SDK apkanalyzer not available; APK identity remains unknown')
    if executable.lower().endswith(('.bat','.cmd')):
        raise ValueError('Configure ANDROID_HOME to use the analyzer without shell execution')
    return [executable]

def analyzer(args):
    result=subprocess.run(analyzer_command()+args,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45,shell=False)
    if result.returncode:
        raise ValueError('apkanalyzer failed: '+result.stderr[-1800:])
    if len(result.stdout.encode('utf-8'))>MAX_HTML:
        raise ValueError('Analyzer output exceeds inspection limit')
    return result.stdout

def inspect_client_apk(data):
    path=local_file(data['path'],1024*1024*1024)
    digest=sha(path)
    with zipfile.ZipFile(path) as archive:
        abis=sorted({n.split('/')[1] for n in archive.namelist() if n.startswith('lib/') and n.count('/')>=2})
    summary=analyzer(['apk','summary',str(path)]).strip().split()
    if len(summary)!=3:
        raise ValueError('Unexpected APK summary; refusing to infer identity')
    out={'path':str(path),'package':summary[0],'version_code':int(summary[1]),'client_version':summary[2],
        'apk_sha256':digest,'supported_abis':abis,'sdk_version':None,'sdk_origin':'unknown','boundary':BOUNDARY}
    if data.get('class_name'):
        if not re.fullmatch(r'[\w.$]+',data['class_name']):
            raise ValueError('Invalid class name')
        code=analyzer(['dex','code','--class',data['class_name'],str(path)])
        out.update(class_name=data['class_name'],bytecode=code,bytecode_sha256=hashlib.sha256(code.encode()).hexdigest())
    if sha(path)!=digest:
        raise ValueError('APK changed during inspection')
    return out


def request_client_apk(data):
    target=data.get('target') or {}
    requirements={k:target[k] for k in ['client','client_version','package','version_code','apk_sha256','abi','variant'] if target.get(k) is not None}
    result={'status':'USER_APK_REQUIRED','requirements':requirements,'reason':data.get('reason','Exact client APK is required for build-specific inspection.'),
        'message':'Прикрепите APK нужного клиента к этому чату или укажите абсолютный путь к локальному файлу. Нужная сборка: '+json.dumps(requirements,ensure_ascii=False)+'. Имя файла не считается доказательством версии.',
        'next_tool':'inspect_client_apk','authentication_required':False,'runtime_verified':False}
    if data.get('path'):
        actual=inspect_client_apk({'path':data['path']})
        checked={k:actual.get(k)==target[k] for k in ['client_version','package','version_code','apk_sha256'] if target.get(k) is not None}
        if target.get('abi') and target['abi']!='universal': checked['abi']=target['abi'] in actual['supported_abis']
        missing=[k for k in ['package','version_code','apk_sha256'] if target.get(k) is None]
        # Build variants are not established by filenames or manifest versions.
        if target.get('variant') is not None: missing.append('variant')
        result.update(status='APK_MISMATCH' if any(v is False for v in checked.values()) else 'APK_INSPECTED',actual=actual,matching_fields=checked,
            target_identity_verified=not missing and all(checked.values()),unbound_fields=missing)
        result.pop('message',None)
    return result


def inspect_media_packets(data):
    """Measure actual packet timestamps including first PTS and sorted gaps."""
    path=local_file(data['path'],1024*1024*1024)
    digest=sha(path)
    executable=shutil.which('ffprobe')
    if not executable:
        raise ValueError('ffprobe unavailable; packet continuity remains unknown')
    streams=subprocess.run([executable,'-v','error','-show_streams','-show_entries','stream=index,codec_type,codec_name,duration','-of','json',str(path)],capture_output=True,timeout=30,shell=False)
    if streams.returncode or len(streams.stdout)>64000:
        raise ValueError('Cannot obtain bounded media stream identity')
    stream_data=json.loads(streams.stdout)['streams']
    timestamps={s['index']:[] for s in stream_data if s.get('codec_type') in {'video','audio'}}
    with tempfile.TemporaryFile() as output:
        result=subprocess.run([executable,'-v','error','-show_packets','-show_entries','packet=stream_index,pts_time','-of','csv=p=0',str(path)],stdout=output,stderr=subprocess.DEVNULL,timeout=45,shell=False)
        if result.returncode or output.tell()>64*1024*1024:
            raise ValueError('Packet inspection failed or exceeded bounds')
        output.seek(0)
        for line in output:
            parts=line.decode('ascii',errors='strict').strip().split(',')
            if len(parts)<2 or not parts[0].isdigit(): continue
            index=int(parts[0])
            if index not in timestamps: continue
            try: pts=int(Decimal(parts[1])*1_000_000)
            except (InvalidOperation,ValueError,OverflowError):
                raise ValueError('Media has packets without valid PTS') from None
            timestamps[index].append(pts)
    tracks=[]
    for s in stream_data:
        if s['index'] not in timestamps: continue
        pts=sorted(timestamps[s['index']])
        track={'mime':s['codec_type']+'/'+str(s.get('codec_name','unknown')),'samples':len(pts)}
        if pts:
            track.update(firstPtsUs=pts[0],maxPtsUs=pts[-1],maxGapUs=max((b-a for a,b in zip(pts,pts[1:])),default=0))
        tracks.append(track)
    if sha(path)!=digest:
        raise ValueError('Media changed during packet inspection')
    return {'path':str(path),'media_sha256':digest,'measurement':{'bytesBefore':path.stat().st_size,'bytesAfter':path.stat().st_size,'tracks':tracks},
        'boundary':'Actual local file packets measured; frozen images, silence and target application execution are not verified.'}

def inspect_plugin_artifact(data):
    path=local_file(data['path'],MAX_ARTIFACT)
    artifact_digest=sha(path)
    errors, warnings=[],[]
    archived_dex=[]
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            entries=archive.infolist()
            if len(entries)>200 or sum(e.file_size for e in entries)>MAX_ARTIFACT or len({e.filename for e in entries})!=len(entries):
                raise ValueError('Archive has duplicate entries or exceeds limits')
            mains=[e for e in entries if e.filename=='main.py']
            if len(mains)!=1:
                raise ValueError('Expected exactly one archive main.py')
            source=archive.read(mains[0]).decode('utf-8')
            archived_dex=[hashlib.sha256(archive.read(e)).hexdigest() for e in entries if e.filename.endswith('.dex')]
    else:
        source=path.read_text(encoding='utf-8')
    tree=ast.parse(source)
    constants={}
    for node in tree.body:
        if isinstance(node,ast.Assign) and isinstance(node.value,ast.Constant):
            for target in node.targets:
                if isinstance(target,ast.Name):
                    constants[target.id]=node.value.value
    digest=None
    if 'EMBEDDED_DEX' in constants:
        try:
            dex=base64.b64decode(constants['EMBEDDED_DEX'],validate=True)
            digest=hashlib.sha256(dex).hexdigest()
            if not dex.startswith(b'dex\n'):
                errors.append('Embedded bytes lack DEX magic')
            expected=constants.get('DEX_SHA256') or constants.get('EMBEDDED_DEX_SHA256') or constants.get('EMBEDDED_SHA256')
            if not expected:
                warnings.append('Embedded DEX has no recognized declared checksum')
            if expected and digest!=expected:
                errors.append('Embedded DEX hash differs from declared hash')
            if archived_dex and digest not in archived_dex:
                errors.append('Embedded DEX differs from archived DEX')
        except (ValueError,TypeError) as exc:
            errors.append('Invalid embedded DEX: '+str(exc))
    else:
        warnings.append('No recognized embedded DEX constant; DEX loading not validated')
    if '__file__' in source:
        warnings.append('Source references __file__; validate installer environment without that variable')
    if re.search(r'from\s+elyx\s+import\s+.*\bassets\b',source):
        warnings.append('elyx.assets availability requires target SDK probe')
    if sha(path)!=artifact_digest:
        raise ValueError('Plugin artifact changed during inspection')
    return {'path':str(path),'artifact_sha256':artifact_digest,'python_sha256':hashlib.sha256(source.encode()).hexdigest(),'version':constants.get('__version__'),'dex_sha256':digest,
        'syntax':'PASS','artifact_status':'FAIL' if errors else 'PASS','errors':errors,'warnings':warnings,
        'runtime_status':'PENDING','boundary':'Static artifact checks only; no imports, installer or target client executed.'}

def probe_bridge_contract(data):
    required=['reflection_class','find_class_metadata','primitive_int','primitive_long','dynamic_proxy','primitive_boolean_notify','hook_invocation','independent_dex_reload','actual_sdk_identity']
    report=data.get('report')
    if report is None:
        source=Path(__file__).with_name('bridge_probe.plugin').read_text(encoding='utf-8')
        fixture=json.loads(Path(__file__).with_name('fixtures').joinpath('bridge-probe.json').read_text(encoding='utf-8'))
        dex=base64.b64decode(fixture['dex_base64'],validate=True)
        java=Path(__file__).with_name('fixtures').joinpath('BridgeProbe.java').read_text(encoding='utf-8').encode()
        if hashlib.sha256(dex).hexdigest()!=fixture['dex_sha256'] or hashlib.sha256(java).hexdigest()!=fixture['source_sha256']:
            raise ValueError('Diagnostic DEX fixture changed; rebuild before preparing probe')
        source=source.replace('EMBEDDED_DEX = None','EMBEDDED_DEX = '+repr(fixture['dex_base64'])).replace('EMBEDDED_SHA256 = None','EMBEDDED_SHA256 = '+repr(fixture['dex_sha256']))
        ast.parse(source)
        return {'status':'PENDING','probe_source':source,'probe_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'required_checks':required,'instructions':'Install this standalone diagnostic plugin through the native client flow, enable it once, and import the EXTERACONTEXT_BRIDGE_PROBE JSON. Unload it afterwards.',
            'remaining_checks':['execute_in_exact_client','actual_sdk_identity','application_feature_scenario'], 'runtime_verified':False,'boundary':BOUNDARY}
    if not isinstance(report,dict) or report.get('schema')!=1 or not isinstance(report.get('checks'),dict):
        raise ValueError('Invalid bridge probe report')
    checks=report['checks']
    return {'status':'FAIL' if report.get('bootstrap_failure') or any(checks.get(k) is False for k in required) else 'EVIDENCE_CONSISTENT' if all(checks.get(k) is True for k in required) and report.get('sdk_runtime') else 'PENDING',
        'missing_checks':[k for k in required if k not in checks], 'observed_checks':{k:checks.get(k) for k in required},
        'parameter_methods':report.get('parameter_methods',{}),'runtime_verified':False,'boundary':BOUNDARY}

def analyze_hook_impact(data):
    inspected=inspect_client_apk({'path':data['path'],'class_name':data['class_name']})
    method=data['method']
    if not re.fullmatch(r'[\w$<>]+',method):
        raise ValueError('Invalid method name')
    callers=data.get('callers',[])
    if not isinstance(callers,list) or len(callers)>8:
        raise ValueError('At most eight explicit caller classes are inspected')
    code=inspected.pop('bytecode')
    found=[]
    for line_no,line in enumerate(code.splitlines(),1):
        if line.startswith('.method ') and re.search(re.escape(method)+r'\(',line):
            found.append({'line':line_no,'signature':line})
    refs=[]
    owner='L'+data['class_name'].replace('.','/')+';->'+method+'('
    path=local_file(data['path'],1024*1024*1024)
    for caller in callers:
        if not isinstance(caller,str) or not re.fullmatch(r'[\w.$]+',caller):
            raise ValueError('Invalid caller class')
        text=code if caller==data['class_name'] else analyzer(['dex','code','--class',caller,str(path)])
        matches=[{'line':n,'instruction':line.strip()} for n,line in enumerate(text.splitlines(),1) if owner in line]
        refs.append({'class_name':caller,'bytecode_sha256':hashlib.sha256(text.encode()).hexdigest(),'references':matches})
    if sha(path)!=inspected['apk_sha256']:
        raise ValueError('APK changed during caller analysis')
    return {'target':inspected,'method':method,'declarations':found,'callers':refs,'coverage':'explicit caller classes only',
        'threading':'unknown','runtime_verified':False,'next_checks':['Establish invocation thread and before/after ordering in the client.','Check all identified UI, recorder and lifecycle callers before selecting hook scope.'], 'boundary':BOUNDARY}

def analyze_run(data):
    log=data['log']
    if not isinstance(log,str) or len(log.encode('utf-8'))>64000:
        raise ValueError('Trace must fit in 64000 UTF-8 bytes')
    parsed, invalid=[],[]
    for line_no,line in enumerate(log.splitlines(),1):
        if not line.strip(): continue
        try:
            event=json.loads(line[line.index('{'):])
            if not isinstance(event,dict) or not isinstance(event.get('session'),str) or not isinstance(event.get('event'),str) or type(event.get('seq')) is not int or event['seq']<1:
                raise ValueError('Invalid event envelope')
            if event.get('event') in {'camera.source.audit','media.source.audit'}:
                measurement=event.get('measurement')
                if not isinstance(measurement,dict) or not isinstance(measurement.get('tracks'),list) or any(not isinstance(t,dict) for t in measurement['tracks']):
                    raise ValueError('Invalid media audit')
            parsed.append(event)
        except (ValueError,TypeError): invalid.append(line_no)
    sessions={}
    for event in parsed:
        sessions.setdefault(event['session'],[]).append(event)
    results=[]
    for session,events in sessions.items():
        versions=sorted({str(e['version']) for e in events if e.get('version')})
        seq=[e.get('seq') for e in events if type(e.get('seq')) is int]
        gaps=([{'after':0,'before':seq[0]}] if seq and seq[0]!=1 else [])+[{'after':a,'before':b} for a,b in zip(seq,seq[1:]) if b!=a+1]
        expected=data.get('expected_artifact') or {}
        loads=[e for e in events if e.get('event')=='plugin.load.start']
        identity='PENDING'
        requested=[k for k in ['version','dex_sha256','python_sha256','artifact_sha256','package','version_code','apk_sha256'] if expected.get(k) is not None]
        if loads and requested:
            if any(e.get(k) is not None and e[k]!=expected[k] for e in loads for k in requested) or (expected.get('version') and versions!=[expected['version']]):
                identity='FAIL'
            elif all(e.get(k)==expected[k] for e in loads for k in requested) and 'version' in requested and 'dex_sha256' in requested:
                identity='PASS'
        installed_identity=identity if all(expected.get(k) is not None for k in ['artifact_sha256','python_sha256','package','version_code','apk_sha256']) else 'PENDING'
        jobs=[]
        for job in sorted({e['job'] for e in events if isinstance(e.get('job'),str)}):
            trace=[e for e in events if e.get('job')==job]
            source=next((e for e in trace if e.get('event') in {'camera.source.audit','media.source.audit'} and e.get('phase') in {'camera_file','source_file'}),None)
            copy=next((e for e in trace if e.get('event') in {'camera.source.audit','media.source.audit'} and e.get('phase')=='saved_copy'),None)
            finding={'job':job,'source_kind':next((e.get('source') for e in trace if e.get('event')=='queue.created'),None),'queue_done':any(e.get('event')=='queue.done' for e in trace),'media_status':'PENDING','findings':[]}
            if source:
                tracks=source.get('measurement',{}).get('tracks',[])
                video=next((t for t in tracks if str(t.get('mime','')).startswith('video/')),None)
                audio=next((t for t in tracks if str(t.get('mime','')).startswith('audio/')),None)
                expected_us=source.get('expected_us')
                if type(expected_us) is int and expected_us>0 and video and audio and all(type(t.get('samples')) is int and t['samples']>0 for t in [video,audio]):
                    # AAC encoder priming can put the first packet slightly before
                    # zero. Keep a bounded tolerance without accepting late starts.
                    bad=any(type(t.get('firstPtsUs')) is not int or not -1_000_000<=t['firstPtsUs']<=1_000_000 or type(t.get('maxPtsUs')) is not int or not expected_us-1_000_000<=t['maxPtsUs']<=expected_us+1_000_000 or t['samples']<2 for t in [video,audio])
                    gap_known=all(type(t.get('maxGapUs')) is int and 0<=t['maxGapUs']<=1_000_000 for t in [video,audio])
                    gap_bad=any(type(t.get('maxGapUs')) is int and t['maxGapUs']>1_000_000 for t in [video,audio])
                    finding['media_status']='FAIL' if bad or gap_bad else 'PACKET_COVERAGE_OBSERVED' if gap_known else 'PENDING'
                    if finding['media_status']=='FAIL': finding['findings'].append('Original camera packets do not cover expected recording; loss occurred before saved copy/split/send.')
                if copy:
                    a,b=source.get('measurement',{}),copy.get('measurement',{})
                    finding['copy_status']='PASS' if type(a.get('bytesAfter')) is int and a['bytesAfter']>0 and a.get('tracks')==b.get('tracks') and a.get('bytesAfter')==b.get('bytesAfter') and a.get('bytesBefore')==a.get('bytesAfter') and b.get('bytesBefore')==b.get('bytesAfter') else 'FAIL'
            part_events=[e for e in trace if e.get('event')=='split.part']
            parts={e.get('part'):e.get('duration_us') for e in part_events if type(e.get('part')) is int and type(e.get('duration_us')) is int}
            acknowledged={e.get('acknowledged_part') for e in trace if e.get('event')=='send.ack' and type(e.get('acknowledged_part')) is int}
            finding['parts_status']='PENDING'
            if parts:
                expected_us=source.get('expected_us') if source else None
                totals=[e.get('count') if e.get('event')=='split.done' else e.get('total') for e in trace if e.get('event') in {'split.done','queue.done'}]
                sequential=len(parts)==len(part_events) and set(parts)==set(range(1,len(parts)+1)) and bool(totals) and all(type(n) is int and n==len(parts) for n in totals)
                if type(expected_us) is int and expected_us>0:
                    finding['parts_status']='PASS' if sequential and abs(sum(parts.values())-expected_us)<=1_000_000 and all(0<d<=60_000_000 for d in parts.values()) else 'FAIL'
                finding['ack_status']='PASS' if sequential and set(parts).issubset(acknowledged) else 'PENDING'
            if finding['queue_done'] and finding['media_status']=='PENDING': finding['findings'].append('queue.done establishes queue completion only; source media preservation remains unknown.')
            jobs.append(finding)
        results.append({'session':session,'versions':versions,'sequence_gaps':gaps,'artifact_identity':identity,'installed_identity':installed_identity,'load_generations':len(loads),'jobs':jobs})
    return {'sessions':results,'invalid_lines':invalid,'trace_complete':not invalid and bool(results) and all(not s['sequence_gaps'] for s in results), 'runtime_verified':False,'boundary':BOUNDARY}

def verify_feature(data):
    result=analyze_run(data)
    observations=data.get('observations') or {}
    required=['motion_after_minute','speech_after_minute','continuous_part_boundary']
    gates={'trace_integrity':result['trace_complete'], 'artifact_identity':bool(result['sessions']) and all(s['artifact_identity']=='PASS' for s in result['sessions']),
        'installed_identity':bool(result['sessions']) and all(s['installed_identity']=='PASS' for s in result['sessions']),
        'packet_coverage':False,'copy_preservation':False,'queue_completion':False,
        'parts_coverage':False,'parts_acknowledged':False,**{key:observations.get(key) is True for key in required}}
    jobs=[j for s in result['sessions'] for j in s['jobs']]
    if data['feature']=='import_round':
        required=['square_crop_matches_preview','sound_preserved','continuous_part_boundary','gallery_route','message_route','fallback_route']
        for key in ['motion_after_minute','speech_after_minute']:
            gates.pop(key,None)
        gates.update({key:observations.get(key) is True for key in required})
        gates['import_source']=bool(jobs) and all(j['source_kind']=='imported' for j in jobs)
    elif data['feature']=='long_round_camera':
        gates['camera_source']=bool(jobs) and all(j['source_kind']=='camera' for j in jobs)
    else:
        raise ValueError('Unknown feature')
    if jobs:
        gates.update(packet_coverage=all(j['media_status']=='PACKET_COVERAGE_OBSERVED' for j in jobs),
            copy_preservation=all(j.get('copy_status')=='PASS' for j in jobs),queue_completion=all(j['queue_done'] for j in jobs),
            parts_coverage=all(j['parts_status']=='PASS' for j in jobs),parts_acknowledged=all(j.get('ack_status')=='PASS' for j in jobs))
    failed=any(j['media_status']=='FAIL' or j.get('copy_status')=='FAIL' or j.get('parts_status')=='FAIL' for j in jobs) or any(s['artifact_identity']=='FAIL' for s in result['sessions']) or any(observations.get(k) is False for k in required)
    return {'feature':data['feature'],'status':'FAIL' if failed else 'EVIDENCE_CONSISTENT' if all(gates.values()) else 'PENDING',
        'gates':gates,'missing_checks':[k for k,v in gates.items() if not v],'analysis':result,'runtime_verified':False,
        'boundary':'Caller-supplied logs and observations are evaluated, not independently attested. No runtime-verified knowledge is created.'}

def main():
    methods={f.__name__:f for f in [request_client_apk,list_client_releases,list_public_apk_mirrors,download_client_apk,inspect_client_apk,inspect_media_packets,inspect_plugin_artifact,probe_bridge_contract,analyze_hook_impact,analyze_run,verify_feature]}
    raw=sys.stdin.buffer.read(64001)
    if len(raw)>64000: raise ValueError('Request exceeds 64000 bytes')
    data=json.loads(raw)
    output=methods[sys.argv[1]](data)
    print(json.dumps(output,ensure_ascii=False))

if __name__=='__main__':
    main()
