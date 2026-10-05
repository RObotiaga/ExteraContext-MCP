import { runPythonJson } from './bridge.mjs';
import { inputRequired, acceptedContent, inputResponse, CLIENT_CAPABILITIES_META_KEY } from '@modelcontextprotocol/server';

const dimensions=['client','platform','client_version','sdk_version','package','version_code','apk_sha256','android_api','abi','variant'];
function relation(fact, key, requested) {
  if (requested === undefined || requested === null || requested === '') return 'not-requested';
  let actual=fact[key];
  if (!actual && ['client_version','sdk_version'].includes(key)) {
    const label=key==='client_version'?'(?:client|app)':'(?:sdk|elyx)';
    const matches=[...String(fact.version || '').matchAll(new RegExp(`(?:^|[;,]\\s*)${label}\\s+(\\d+(?:\\.\\d+)+)(?=\\s*(?:[;,]|$))`,'ig'))];
    if (matches.length===1) actual=matches[0][1];
  }
  if (!actual) return 'unknown';
  return String(actual).toLowerCase()===String(requested).toLowerCase()?'match':'mismatch';
}

export function assessEvidence(facts, target={}, symbol='') {
  const groups={exact_target:[],reference:[],mismatched:[],conflicting:[]};
  for (const fact of Array.isArray(facts)?facts:[]) {
    const matches=Object.fromEntries(dimensions.map(k=>[k,relation(fact,k,target[k])]));
    const annotated={id:fact.id || fact.path || null,status:fact.status || 'unknown',target_match:matches};
    const state=fact.knowledge_state || fact.review_status;
    if (['conflicting','rejected','candidate'].includes(state)) groups.conflicting.push(annotated);
    else if (Object.values(matches).includes('mismatch')) groups.mismatched.push(annotated);
    else if (['client','platform','client_version','package','version_code','apk_sha256'].every(k=>target[k]) &&
      !Object.values(matches).includes('unknown') && ['code','docs','runtime-verified'].includes(fact.status)) groups.exact_target.push(annotated);
    else groups.reference.push(annotated);
  }
  const semantics_conflicts=[];
  const relevant=(Array.isArray(facts)?facts:[]).filter(f=>!dimensions.some(k=>relation(f,k,target[k])==='mismatch'));
  // Structured opposite observations and simple availability claims can establish a
  // conflict. Arbitrary prose is not a logical language: keep it as unknown evidence.
  for (let i=0;i<relevant.length;i++) for (let j=i+1;j<relevant.length;j++) {
    const a=relevant[i],b=relevant[j];
    if (!a.api || a.api!==b.api) continue;
    if (dimensions.some(k=> {
      const scoped=a[k] ?? (['client_version','sdk_version'].includes(k) ? undefined : null);
      return scoped != null && relation(b,k,scoped)==='mismatch';
    }) || ['client_version','sdk_version'].some(k=> {
      const label=k==='client_version'?'(?:client|app)':'(?:sdk|elyx)';
      const scoped=[...String(a.version || '').matchAll(new RegExp(`(?:^|[;,]\\s*)${label}\\s+(\\d+(?:\\.\\d+)+)(?=\\s*(?:[;,]|$))`,'ig'))];
      return !a[k] && scoped.length===1 && relation(b,k,scoped[0][1])==='mismatch';
    })) continue;
    const structured=a.assertion?.key && a.assertion.key===b.assertion?.key &&
      typeof a.assertion.value==='boolean' && typeof b.assertion.value==='boolean' && a.assertion.value!==b.assertion.value;
    const normalize=c=>String(c || '').toLowerCase().replace(/\bnot\s+|\bне\s+/g,'').replace(/[.!?]/g,'').trim();
    const neg=c=>/\bnot\b|(?:^|\s)не\s/.test(String(c || '').toLowerCase());
    const opposite=a.claim && b.claim && normalize(a.claim)===normalize(b.claim) && neg(a.claim)!==neg(b.claim);
    if (structured || opposite) semantics_conflicts.push({symbol:a.api,fact_ids:[a.id,b.id],reason:'Opposing assertions in overlapping target evidence.',check:'probe_bridge_contract'});
  }
  const claims=(Array.isArray(facts)?facts:[]).map(f=>String(f.claim || f.statement || ''));
  if (/find_class/i.test(symbol) && claims.some(c=>/Java Class|getDeclaredMethod|getDeclaredField/i.test(c)) &&
      claims.some(c=>/wrapper|обёрт|оберт|не предоставлял|несоответств/i.test(c))) {
    semantics_conflicts.push({symbol:'find_class',reason:'Class metadata and Python Java wrapper behavior differ across reported environments.',check:'probe_bridge_contract'});
  }
  const next_checks=[];
  if (!target.client || !target.client_version || !target.package || !target.version_code || !target.apk_sha256)
    next_checks.push({id:'bind_target',reason:'Resolve the package, build and APK hash before transferring private API assumptions.'});
  if (!target.sdk_version || target.sdk_origin !== 'runtime')
    next_checks.push({id:'probe_sdk',reason:'A requirement or documentation version is not an observed runtime SDK.'});
  if (groups.conflicting.length || semantics_conflicts.length || /find_class|reflection|proxy|hook/i.test(symbol))
    next_checks.push({id:'probe_bridge_contract',reason:'Check Java Class vs Python wrapper, primitive field access, proxy construction and hook parameter methods in the target client.'});
  next_checks.push({id:'verify_feature',reason:'Test the feature on the installed artifact; a build, stub test, hook installation or queue.done is insufficient.'});
  return {groups, semantics_conflicts, runtime_verified:false, compatibility:groups.conflicting.length || semantics_conflicts.length?'conflict':groups.exact_target.length?'exact-target-evidence':'unknown',
    next_checks, boundary:'These are source assertions and caller-supplied identity fields. No device execution or trusted runtime attestation was performed.'};
}

export function developmentTools(server,{z,targetSchema,outputSchema,readAnnotations,toolResponse,guarded,protocolMeta}) {
  server.registerTool('request_client_apk',{
    title:'Request exact client APK',description:'Ask for the exact client APK when mirrors lack the needed build. Uses MCP form elicitation for a local path if supported, otherwise returns a chat attachment request. Uploaded files are inspected, never installed.',
    inputSchema:z.object({target:targetSchema,reason:z.string().min(1).max(512).optional(),path:z.string().min(1).max(4096).optional()}),outputSchema,annotations:readAnnotations
  },async (input,ctx)=>guarded('request_client_apk',async()=>{
    const formSchema=z.object({apk_path:z.string().min(1).max(4096)});
    const supplied=acceptedContent(ctx.mcpReq.inputResponses,'apk_file',formSchema);
    const response=inputResponse(ctx.mcpReq.inputResponses,'apk_file');
    if (supplied) input={...input,path:supplied.apk_path};
    let data=await runPythonJson('scripts/development_tools.py',['request_client_apk'],{stdin:JSON.stringify(input)});
    const capabilities=ctx.mcpReq.envelope?.[CLIENT_CAPABILITIES_META_KEY] || server.server.getClientCapabilities();
    if (!input.path && capabilities?.elicitation?.form) {
      if (response.kind==='elicit' && response.action!=='accept') data={...data,status:response.action==='decline'?'USER_DECLINED':'USER_CANCELLED'};
      else return inputRequired({inputRequests:{apk_file:inputRequired.elicit({mode:'form',message:data.message,requestedSchema:{type:'object',properties:{apk_path:{type:'string',title:'Абсолютный путь к APK',minLength:1,maxLength:4096}},required:['apk_path']}})}});
    }
    return toolResponse('request_client_apk',data,[],protocolMeta);
  }));
  const identity=z.object({version:z.string().optional(),dex_sha256:z.string().regex(/^[a-f0-9]{64}$/).optional(),python_sha256:z.string().regex(/^[a-f0-9]{64}$/).optional(),artifact_sha256:z.string().regex(/^[a-f0-9]{64}$/).optional(),package:z.string().optional(),version_code:z.number().int().positive().optional(),apk_sha256:z.string().regex(/^[a-f0-9]{64}$/).optional()});
  const tools=[
    ['list_client_releases','List public Telegram APK announcements without login. Reading a post does not establish access to APK bytes.',z.object({channel:z.enum(['exteraReleases','AyuGramReleases']),before:z.number().int().positive().optional(),pages:z.number().int().min(1).max(5).default(1)})],
    ['list_public_apk_mirrors','Read publisher-owned GitHub release assets anonymously. Older/beta builds are labelled; no Telegram attachment equivalence is inferred.',z.object({channel:z.enum(['exteraReleases','AyuGramReleases'])})],
    ['download_client_apk','Download a selected APK from an approved publisher repository without authentication. Creates a new cache file; inspect its actual package/build before use.',z.object({channel:z.enum(['exteraReleases','AyuGramReleases']),repository:z.enum(['exteraSquad/exteraGram','exteraSquad/exteraGram-Beta','AyuGram/AyuGram4A']),asset_id:z.number().int().positive(),expected_sha256:z.string().regex(/^[a-f0-9]{64}$/).optional()})],
    ['inspect_client_apk','Inspect a local APK hash, package, version, native ABI and exact DEX class via Android SDK apkanalyzer. Does not execute or install it.',z.object({path:z.string().min(1),class_name:z.string().regex(/^[\w.$]+$/).optional()})],
    ['inspect_media_packets','Measure actual local video/audio packet count, first/max PTS and largest sorted gap with ffprobe. Does not prove moving images or audible speech.',z.object({path:z.string().min(1)})],
    ['inspect_plugin_artifact','Validate the exact EAF or standalone plugin, Python syntax, embedded/archived DEX agreement and hashes. Does not execute plugin code.',z.object({path:z.string().min(1)})],
    ['probe_bridge_contract','Prepare a minimal diagnostic plugin to execute in the target client. With an imported report, evaluate observed contracts; server preparation is never device PASS.',z.object({report:z.record(z.string(),z.unknown()).optional()})],
    ['analyze_hook_impact','Inspect an exact APK class and caller classes for method and field references. Static references are partial and cannot prove runtime threading or complete call coverage.',z.object({path:z.string().min(1),class_name:z.string().regex(/^[\w.$]+$/),method:z.string().regex(/^[\w$<>]+$/),callers:z.array(z.string().regex(/^[\w.$]+$/)).max(8).default([])})],
    ['analyze_run','Analyze bounded JSONL logs by session/job/version, identify missing/truncated evidence, media loss and artifact identity. No caller PASS is promoted to runtime verification.',z.object({log:z.string().min(1).max(60000),expected_artifact:identity.optional()})],
    ['verify_feature','Evaluate explicit feature evidence and missing gates. Media PTS alone cannot establish visual/audio continuity; returns PENDING until all criteria are observed.',z.object({log:z.string().min(1).max(60000),feature:z.enum(['long_round_camera','import_round']),expected_artifact:identity.optional(),observations:z.object({motion_after_minute:z.boolean().optional(),speech_after_minute:z.boolean().optional(),continuous_part_boundary:z.boolean().optional(),square_crop_matches_preview:z.boolean().optional(),sound_preserved:z.boolean().optional(),gallery_route:z.boolean().optional(),message_route:z.boolean().optional(),fallback_route:z.boolean().optional()}).optional()})]
  ];
  for (const [name,description,inputSchema] of tools) server.registerTool(name,{
    title:name,description,inputSchema,outputSchema,annotations:{...readAnnotations,readOnlyHint:name!=='download_client_apk',openWorldHint:['list_client_releases','list_public_apk_mirrors','download_client_apk'].includes(name),idempotentHint:!['list_client_releases','list_public_apk_mirrors','download_client_apk'].includes(name)}
  }, async input=>guarded(name,async()=>toolResponse(name,await runPythonJson('scripts/development_tools.py',[name],{stdin:JSON.stringify(input),timeoutMs:180000}),[],protocolMeta)));
}
