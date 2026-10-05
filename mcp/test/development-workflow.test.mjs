import test from 'node:test';
import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { PYTHON } from '../src/bridge.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
test('exact build passport and target-aware lookup survive MCP wire validation', async () => {
  const temporary = mkdtempSync(join(tmpdir(), 'extera-development-'));
  const db = join(temporary, 'base.sqlite');
  const prepared = spawnSync(PYTHON, ['-B', 'scripts/prepare_ci_fixture.py', db], { cwd: root, encoding: 'utf8' });
  assert.equal(prepared.status, 0, prepared.stderr);
  const client = new Client({ name: 'development-regression', version: '1' }, { versionNegotiation: { mode: { pin: '2026-07-28' } } });
  const target = {client:'AyuGram',platform:'Android',client_version:'12.9.0',package:'com.radolyn.ayugram',version_code:70079,android_api:36,apk_sha256:'8'.repeat(64),abi:'arm64-v8a'};
  try {
    await client.connect(new StdioClientTransport({command:process.execPath,args:['mcp/src/index.mjs','--transport','stdio','--modern-only'],cwd:root,env:{...process.env,EXTERACONTEXT_DB:db,EXTERACONTEXT_KNOWLEDGE_DB:join(temporary,'overlay.sqlite'),EXTERACONTEXT_AUTO_SYNC:'0',EXTERACONTEXT_REQUIRE_MANIFEST:'0'},stderr:'pipe'}));
    const resolved = await client.callTool({name:'resolve_target',arguments:{target}});
    assert.notEqual(resolved.isError,true,JSON.stringify(resolved));
    assert.equal(resolved.structuredContent.data.target.apk_sha256,target.apk_sha256);
    const packet=await client.callTool({name:'search_knowledge',arguments:{query:'reflection',target,limit:1}});
    assert.notEqual(packet.isError,true,JSON.stringify(packet));
    assert.ok(Array.isArray(packet.structuredContent.data.retrieval.assessment_facts));
    assert.ok(Array.isArray(packet.structuredContent.data.context.facts));
    for (const [name,args] of [['find_api',{symbol:'find_class'}],['find_usage',{query:'reflection'}],['get_recipe',{query:'hooks'}]]) {
      const result=await client.callTool({name,arguments:{...args,target}});
      assert.notEqual(result.isError,true,JSON.stringify(result));
      assert.deepEqual(result.structuredContent.data.resolved_target.target,target);
      assert.equal(result.structuredContent.data.assessment.runtime_verified,false);
      assert.ok(Array.isArray(result.structuredContent.data.assessment.next_checks));
    }
    const names=(await client.listTools()).tools.map(t=>t.name);
    assert.ok(names.includes('list_client_releases'));
    assert.ok(names.includes('analyze_run'));
    assert.ok(names.includes('inspect_plugin_artifact'));
    const requested=await client.callTool({name:'request_client_apk',arguments:{target,reason:'Need exact APK to inspect recorder caller'}});
    assert.equal(requested.structuredContent.data.status,'USER_APK_REQUIRED');
    assert.equal(requested.structuredContent.data.requirements.apk_sha256,target.apk_sha256);
    assert.equal(requested.structuredContent.data.authentication_required,false);
    assert.equal(requested.structuredContent.data.runtime_verified,false);
  } finally { await client.close(); rmSync(temporary,{recursive:true,force:true}); }
});

test('APK request uses modern input_required elicitation and honours user decline',async()=>{
  const temporary=mkdtempSync(join(tmpdir(),'extera-apk-request-'));
  const db=join(temporary,'base.sqlite');
  const prepared=spawnSync(PYTHON,['-B','scripts/prepare_ci_fixture.py',db],{cwd:root,encoding:'utf8'});
  assert.equal(prepared.status,0,prepared.stderr);
  const client=new Client({name:'apk-request-regression',version:'1'},{capabilities:{elicitation:{form:{}}},versionNegotiation:{mode:{pin:'2026-07-28'}}});
  let requests=0;
  client.setRequestHandler('elicitation/create',async request=>{
    requests++;
    assert.equal(request.params.mode,'form');
    assert.deepEqual(request.params.requestedSchema.required,['apk_path']);
    return {action:'decline'};
  });
  try {
    await client.connect(new StdioClientTransport({command:process.execPath,args:['mcp/src/index.mjs','--transport','stdio','--modern-only'],cwd:root,env:{...process.env,EXTERACONTEXT_DB:db,EXTERACONTEXT_KNOWLEDGE_DB:join(temporary,'overlay.sqlite'),EXTERACONTEXT_AUTO_SYNC:'0',EXTERACONTEXT_REQUIRE_MANIFEST:'0'},stderr:'pipe'}));
    const result=await client.callTool({name:'request_client_apk',arguments:{target:{client:'AyuGram',platform:'Android',client_version:'12.9.0'}}});
    assert.notEqual(result.isError,true,JSON.stringify(result));
    assert.equal(result.structuredContent.data.status,'USER_DECLINED');
    assert.equal(requests,1);
  } finally {await client.close();rmSync(temporary,{recursive:true,force:true});}
});
