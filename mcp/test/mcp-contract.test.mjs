import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..', '..');

async function text(path) { return readFile(resolve(root, path), 'utf8'); }

test('MCP entry uses 2026-era official serving APIs', async () => {
  const source = await text('mcp/src/index.mjs');
  assert.match(source, /createMcpHandler/);
  assert.match(source, /serveStdio/);
  assert.doesNotMatch(source, /new StdioServerTransport\(\).*connect/);
  assert.match(source, /--modern-only/);
});

test('server exposes read and guarded write-back tools', async () => {
  const source = await text('mcp/src/server.mjs');
  for (const name of [
    'resolve_target','search_knowledge','find_api','find_usage','get_recipe','get_evidence','check_compatibility',
    'reflect_on_task','submit_collector_result','submit_verifier_phase_a','submit_verifier_phase_b','get_capture_status',
    'record_runtime_result','doctor'
  ]) assert.match(source, new RegExp(`registerTool\\('${name}'`), name);
  assert.match(source, /structuredContent/);
  assert.match(source, /outputSchema/);
  assert.match(source, /readOnlyHint/);
  assert.match(source, /idempotentHint/);
});

test('JS sources are syntactically valid without resolving dependencies', () => {
  for (const file of ['mcp/src/bridge.mjs','mcp/src/server.mjs','mcp/src/index.mjs']) {
    const r = spawnSync(process.execPath, ['--check', resolve(root, file)], { encoding: 'utf8' });
    assert.equal(r.status, 0, `${file}: ${r.stderr}`);
  }
});


test('write-back authorization uses MCP capability tokens, not required DSH child IDs', async () => {
  const source = await text('mcp/src/server.mjs');
  assert.match(source, /actor_token/);
  assert.match(source, /RuntimeActorSchema/);
  assert.doesNotMatch(source, /subagent_id:\s*z\.string\(\)\.min\(1\)/);
  assert.doesNotMatch(source, /subagent_provider:\s*z\.string/);
  const orchestrator = await text('scripts/orchestrate.py');
  assert.match(orchestrator, /collector_token_hash/);
  assert.match(orchestrator, /verifier_token_hash/);
  assert.match(orchestrator, /secrets\.compare_digest/);
});

test('Python bridge forces UTF-8 mode', async () => {
  const source = await text('mcp/src/bridge.mjs');
  assert.match(source, /PYTHONUTF8:\s*'1'/);
  assert.match(source, /PYTHONIOENCODING:\s*'utf-8'/);
});
