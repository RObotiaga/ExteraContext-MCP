import { runPythonJson } from './bridge.mjs';

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
    else if (target.client && target.platform && (target.client_version || target.apk_sha256) &&
      !Object.values(matches).includes('unknown') && ['code','docs','runtime-verified'].includes(fact.status)) groups.exact_target.push(annotated);
    else groups.reference.push(annotated);
  }
  const semantics_conflicts=[];
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
  const tools=[
    ['list_client_releases','List public Telegram APK announcements without login. Reading a post does not establish access to APK bytes.',z.object({channel:z.enum(['exteraReleases','AyuGramReleases']),before:z.number().int().positive().optional(),pages:z.number().int().min(1).max(5).default(1)})],
    ['inspect_client_apk','Inspect a local APK hash, package, version, native ABI and exact DEX class via Android SDK apkanalyzer. Does not execute or install it.',z.object({path:z.string().min(1),class_name:z.string().regex(/^[\w.$]+$/).optional()})],
    ['inspect_plugin_artifact','Validate the exact EAF or standalone plugin, Python syntax, embedded/archived DEX agreement and hashes. Does not execute plugin code.',z.object({path:z.string().min(1)})],
    ['probe_bridge_contract','Prepare a minimal diagnostic plugin to execute in the target client. With an imported report, evaluate observed contracts; server preparation is never device PASS.',z.object({report:z.record(z.string(),z.unknown()).optional()})],
    ['analyze_hook_impact','Inspect an exact APK class and caller classes for method and field references. Static references are partial and cannot prove runtime threading or complete call coverage.',z.object({path:z.string().min(1),class_name:z.string().regex(/^[\w.$]+$/),method:z.string().regex(/^[\w$<>]+$/),callers:z.array(z.string().regex(/^[\w.$]+$/)).max(8).default([])})],
    ['analyze_run','Analyze bounded JSONL logs by session/job/version, identify missing/truncated evidence, media loss and artifact identity. No caller PASS is promoted to runtime verification.',z.object({log:z.string().min(1).max(60000),expected_artifact:z.object({version:z.string().optional(),dex_sha256:z.string().regex(/^[a-f0-9]{64}$/).optional()}).optional()})],
    ['verify_feature','Evaluate explicit feature evidence and missing gates. Media PTS alone cannot establish visual/audio continuity; returns PENDING until all criteria are observed.',z.object({log:z.string().min(1).max(60000),feature:z.enum(['long_round_camera','import_round']),expected_artifact:z.object({version:z.string().optional(),dex_sha256:z.string().regex(/^[a-f0-9]{64}$/).optional()}).optional(),observations:z.object({motion_after_minute:z.boolean().optional(),speech_after_minute:z.boolean().optional(),continuous_part_boundary:z.boolean().optional()}).optional()})]
  ];
  for (const [name,description,inputSchema] of tools) server.registerTool(name,{
    title:name,description,inputSchema,outputSchema,annotations:{...readAnnotations,openWorldHint:name==='list_client_releases',idempotentHint:name!=='list_client_releases'}
  }, async input=>guarded(name,async()=>toolResponse(name,await runPythonJson('scripts/development_tools.py',[name],{stdin:JSON.stringify(input),timeoutMs:120000}),[],protocolMeta)));
}
