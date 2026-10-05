// Explicit manual integration smoke: includes public HTTPS reads, never run by npm test.
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { fileURLToPath } from 'node:url';
import { writeFile } from 'node:fs/promises';
const root=fileURLToPath(new URL('../../',import.meta.url));
const client=new Client({name:'development-live-smoke',version:'1'},{versionNegotiation:{mode:{pin:'2026-07-28'}}});
const results=[];
try {
  await client.connect(new StdioClientTransport({command:process.execPath,args:['mcp/src/index.mjs','--transport','stdio','--modern-only'],cwd:root,
    env:{...process.env,EXTERACONTEXT_AUTO_SYNC:'0'},stderr:'pipe'}));
  const cases=[['doctor',{}],['list_client_releases',{channel:'exteraReleases'}],['list_client_releases',{channel:'AyuGramReleases'}],['list_public_apk_mirrors',{channel:'exteraReleases'}],['list_public_apk_mirrors',{channel:'AyuGramReleases'}],['probe_bridge_contract',{}]];
  if (process.env.EXTERACONTEXT_TEST_APK) {
    cases.push(['inspect_client_apk',{path:process.env.EXTERACONTEXT_TEST_APK,class_name:'com.exteragram.messenger.camera.RoundVideoEncoder'}]);
    cases.push(['request_client_apk',{target:{client:'AyuGram',platform:'Android',package:'com.radolyn.ayugram',client_version:'12.9.0',version_code:70079,apk_sha256:'8b97298a318f344a5bb5f5c5430a6e2c9d192fb52b2b1a56a49c9adcf096e478'},path:process.env.EXTERACONTEXT_TEST_APK}]);
    cases.push(['analyze_hook_impact',{path:process.env.EXTERACONTEXT_TEST_APK,class_name:'com.exteragram.messenger.utils.system.SystemUtils',method:'getRoundVideoMaxDurationMs',callers:['com.exteragram.messenger.camera.RoundVideoEncoder','org.telegram.ui.Components.InstantCameraView']}]);
  }
  if (process.env.EXTERACONTEXT_TEST_PLUGIN) cases.push(['inspect_plugin_artifact',{path:process.env.EXTERACONTEXT_TEST_PLUGIN}]);
  if (process.env.EXTERACONTEXT_TEST_MEDIA) cases.push(['inspect_media_packets',{path:process.env.EXTERACONTEXT_TEST_MEDIA}]);
  for (const [name,args] of cases) {
    const result=await client.callTool({name,arguments:args},{},{timeout:120000});
    const envelope=result.structuredContent;
    if (result.isError || !envelope?.ok) throw new Error(`${name}: ${JSON.stringify(envelope || result)}`);
    const data=envelope.data;
    const summary={tool:name,server_version:envelope.meta.server_version,ok:true};
    if (name==='list_client_releases') Object.assign(summary,{channel:data.channel,releases:data.releases.length,auth_used:data.auth_used,downloads_exposed:data.releases.filter(r=>r.download_url).length});
    if (name==='list_public_apk_mirrors') Object.assign(summary,{channel:data.channel,assets:data.assets.length,auth_used:data.auth_used});
    if (name==='request_client_apk') {
      if (data.status!=='APK_INSPECTED' || !data.target_identity_verified) throw new Error('Supplied exact APK did not satisfy request');
      Object.assign(summary,{status:data.status,target_identity_verified:data.target_identity_verified});
    }
    if (name==='inspect_media_packets') Object.assign(summary,{media_sha256:data.media_sha256,tracks:data.measurement.tracks});
    if (name==='inspect_client_apk') Object.assign(summary,{package:data.package,version_code:data.version_code,apk_sha256:data.apk_sha256,bytecode_chars:data.bytecode.length});
    if (name==='analyze_hook_impact') Object.assign(summary,{declarations:data.declarations.length,references:data.callers.map(c=>({class_name:c.class_name,count:c.references.length})),coverage:data.coverage});
    if (name==='inspect_plugin_artifact') Object.assign(summary,{artifact_status:data.artifact_status,artifact_sha256:data.artifact_sha256,dex_sha256:data.dex_sha256,runtime_status:data.runtime_status});
    if (name==='probe_bridge_contract') Object.assign(summary,{status:data.status,probe_sha256:data.probe_sha256});
    results.push(summary);
    console.log(JSON.stringify(summary));
  }
  await writeFile(new URL('../../run-v1-001/development-live-smoke.json',import.meta.url),JSON.stringify(results,null,2));
} finally { await client.close(); }
