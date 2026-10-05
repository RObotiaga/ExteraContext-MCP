"""Anonymous publisher-owned release reads and bounded APK acquisition.

The publisher repository is verified, not equivalence to a Telegram attachment.
No token, browser session or Telegram authentication is used.
"""
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

MIRRORS = {
    'exteraReleases': [('exteraSquad/exteraGram', 'historical-stable'), ('exteraSquad/exteraGram-Beta', 'beta')],
    'AyuGramReleases': [('AyuGram/AyuGram4A', 'stable')],
}
PROVENANCE = {
    'exteraReleases': 'https://github.com/exteraSquad/exteraGram/blob/main/README.md',
    'AyuGramReleases': 'https://github.com/AyuGram',
}
MAX_APK = 1024 * 1024 * 1024

class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def validate_url(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in {'api.github.com','github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'} or parsed.username or parsed.password or parsed.port not in {None,443}:
        raise ValueError('Refused URL outside publisher release infrastructure')

def fetch(url):
    validate_url(url)
    opener=urllib.request.build_opener(PublicRedirect())
    return opener.open(urllib.request.Request(url, headers={'User-Agent':'ExteraContext-Public-Releases/1.0','Accept':'application/vnd.github+json'}),timeout=30)

def api(path):
    with fetch('https://api.github.com/'+path) as response:
        raw=response.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:
            raise ValueError('Release metadata exceeds limit')
        return json.loads(raw)

def repositories(channel):
    if channel not in MIRRORS:
        raise ValueError('Unknown release channel')
    return MIRRORS[channel]

def asset_record(asset, repo, channel, release=None, stream=None):
    url=asset.get('browser_download_url','')
    p=urllib.parse.urlsplit(url)
    name=asset.get('name','')
    if not name.lower().endswith('.apk') or not re.fullmatch(r'[A-Za-z0-9._+-]+',name) or p.scheme!='https' or p.hostname!='github.com' or not p.path.startswith('/'+repo+'/releases/download/') or p.username or p.password or p.query or p.fragment:
        return None
    if type(asset.get('size')) is not int or not 0<asset['size']<=MAX_APK or type(asset.get('id')) is not int:
        return None
    return {'channel':channel,'repository':repo,'asset_id':asset['id'],'filename':name,'bytes':asset['size'],
        'download_url':url,'publisher_digest':asset.get('digest'),'release_tag':release.get('tag_name') if release else None,
        'release_date':release.get('published_at') if release else None,'stream':stream,
        'provenance_url':PROVENANCE[channel],'publisher_repository_verified':True,
        'telegram_attachment_equivalence':'unverified','apk_identity_verified':False}

def list_public_apk_mirrors(data):
    channel=data['channel']
    assets, sources=[],[]
    for repo,stream in repositories(channel):
        releases=api('repos/'+repo+'/releases?per_page=10')
        if not isinstance(releases,list):
            raise ValueError('Unexpected release list')
        sources.append({'repository':repo,'stream':stream,'releases_observed':len(releases)})
        for release in releases:
            if release.get('draft'): continue
            for asset in release.get('assets',[]):
                record=asset_record(asset,repo,channel,release,stream)
                if record: assets.append(record)
    return {'channel':channel,'sources':sources,'assets':assets,'auth_used':False,
        'warnings':['Publisher release assets may be older or beta. No equality with Telegram attachment bytes or current client build is inferred.']}

def download_client_apk(data):
    channel,repo,asset_id=data['channel'],data['repository'],data['asset_id']
    if repo not in {r for r,_ in repositories(channel)} or type(asset_id) is not int or asset_id<1:
        raise ValueError('Asset must belong to an approved publisher repository')
    # Resolve the asset from the publisher API again; never trust a supplied URL.
    asset=api(f'repos/{repo}/releases/assets/{asset_id}')
    record=asset_record(asset,repo,channel)
    if not record or record['asset_id']!=asset_id:
        raise ValueError('Publisher response does not identify the selected APK')
    expected=data.get('expected_sha256')
    published=record['publisher_digest']
    if published is not None and not re.fullmatch(r'sha256:[a-f0-9]{64}',published):
        raise ValueError('Unsupported publisher digest')
    published=published.split(':',1)[1] if published else None
    if expected is not None and not re.fullmatch(r'[a-f0-9]{64}',expected):
        raise ValueError('Invalid expected APK hash')
    cache=Path(__file__).resolve().parents[1]/'.cache/public-apks'
    cache.mkdir(parents=True,exist_ok=True)
    deadline=time.monotonic()+80
    digest=hashlib.sha256()
    count=0
    # Owned temporary file only; failures cannot overwrite a prior download.
    with tempfile.NamedTemporaryFile(dir=cache,suffix='.apk',delete=False) as out:
        path=Path(out.name)
        try:
            with fetch(record['download_url']) as response:
                while True:
                    if time.monotonic()>deadline: raise ValueError('APK acquisition timed out')
                    block=response.read(1024*1024)
                    if not block: break
                    count+=len(block)
                    if count>record['bytes'] or count>MAX_APK: raise ValueError('APK exceeds publisher size')
                    out.write(block)
                    digest.update(block)
            if count!=record['bytes'] or any(h and h!=digest.hexdigest() for h in [expected,published]):
                raise ValueError('APK size or digest does not match selected asset')
        except BaseException:
            out.close()
            path.unlink(missing_ok=True)
            raise
    try:
        with zipfile.ZipFile(path) as archive:
            if 'AndroidManifest.xml' not in archive.namelist() or 'classes.dex' not in archive.namelist():
                raise ValueError('Downloaded asset is not an APK')
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {**record,'path':str(path),'apk_sha256':digest.hexdigest(),'downloaded_bytes':count,'download_status':'DOWNLOADED',
        'auth_used':False,'publisher_hash_verified':bool(published),'expected_hash_verified':bool(expected),
        'next_check':'inspect_client_apk','boundary':'Bytes acquired from the publisher repository; package/build/signature and Telegram equivalence still require inspection.'}
