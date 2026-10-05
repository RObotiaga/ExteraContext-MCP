import test from 'node:test';
import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';

const root = fileURLToPath(new URL('../../', import.meta.url));
test('exact build passport and target-aware lookup survive MCP wire validation', async () => {
  const temporary = mkdtempSync(join(tmpdir(), 'extera-development-'));
  const db = join(temporary, 'base.sqlite');
  const prepared = spawnSync(process.env.EXTERACONTEXT_PYTHON || 'python3', ['-B', 'scripts/prepare_ci_fixture.py', db], { cwd: root, encoding: 'utf8' });
  assert.equal(prepared.status, 0, prepared.stderr);
  const client = new Client({ name: 'development-regression', version: '1' }, { versionNegotiation: { mode: { pin: '2026-07-28' } } });
  const target = {client:'AyuGram',platform:'Android',client_version:'12.9.0',package:'com.radolyn.ayugram',version_code:70079,android_api:36,apk_sha256:'8'.repeat(64),abi:'arm64-v8a'};
  try {
    await client.connect(new StdioClientTransport({command:process.execPath,args:['mcp/src/index.mjs','--transport','stdio','--modern-only'],cwd:root,env:{...process.env,EXTERACONTEXT_DB:db,EXTERACONTEXT_KNOWLEDGE_DB:join(temporary,'overlay.sqlite'),EXTERACONTEXT_AUTO_SYNC:'0',EXTERACONTEXT_REQUIRE_MANIFEST:'0'},stderr:'pipe'}));
    const resolved = await client.callTool({name:'resolve_target',arguments:{target}});
    assert.notEqual(resolved.isError,true,JSON.stringify(resolved));
    assert.equal(resolved.structuredContent.data.target.apk_sha256,target.apk_sha256);
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
  } finally { await client.close(); rmSync(temporary,{recursive:true,force:true}); }
});
