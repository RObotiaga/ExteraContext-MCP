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

test('target resolver binds versions to their labels and leaves ambiguity unknown', async () => {
  const temporary = mkdtempSync(join(tmpdir(), 'extera-target-'));
  const db = join(temporary, 'base.sqlite');
  const prepared = spawnSync(PYTHON, ['-B', 'scripts/prepare_ci_fixture.py', db], { cwd: root, encoding: 'utf8' });
  assert.equal(prepared.status, 0, prepared.stderr);
  const client = new Client({ name: 'target-regression', version: '1' }, { versionNegotiation: { mode: { pin: '2026-07-28' } } });
  try {
    await client.connect(new StdioClientTransport({ command: process.execPath,
      args: ['mcp/src/index.mjs', '--transport', 'stdio', '--modern-only'], cwd: root,
      env: { ...process.env, EXTERACONTEXT_DB: db, EXTERACONTEXT_KNOWLEDGE_DB: join(temporary, 'overlay.sqlite'), EXTERACONTEXT_AUTO_SYNC: '0', EXTERACONTEXT_REQUIRE_MANIFEST: '0' }, stderr: 'pipe' }));
    const cases = [
      ['ExteraGram Android Python SDK 2.4.0', undefined, '2.4.0'],
      ['ExteraGram Android SDK 2.4.0 client 11.9.0', '11.9.0', '2.4.0'],
      ['ExteraGram 11.9.0 Android SDK 2.4.0', '11.9.0', '2.4.0'],
      ['AyuGram v11.9.0 SDK v2.4.0', '11.9.0', '2.4.0'],
      ['ExteraGram Android build 123 11.9.0', undefined, undefined],
      ['ExteraGram client 11.9.0 client 12.0.0 SDK 2.4.0', undefined, '2.4.0'],
      ['ExteraGram client 11.9.0 SDK 2.4.0 SDK 2.5.0', '11.9.0', undefined],
      ['ExteraGram client 11.9.0 client 11.9.0', '11.9.0', undefined],
      ['ExteraGram client 11.9.0-beta SDK 2.4.0', undefined, '2.4.0'],
      ['ExteraGram SDK >=2.4.0', undefined, undefined],
      ['ExteraGram client 11.9.0 or client >=12.0.0', undefined, undefined],
      ['ExteraGram SDK 2.4.0 or SDK >=3.0.0', undefined, undefined],
      ['ExteraGram client 11.9.0 or client latest', undefined, undefined],
      ['ExteraGram 11.9.0 or ExteraGram version latest', undefined, undefined],
      ['ExteraGram version 11.9.0 SDK version 2.4.0', '11.9.0', '2.4.0'],
      ['ExteraGram SDK 2.4.0 or SDK ~3.0.0', undefined, undefined],
      ['ExteraGram client 11.9.0.1.2', undefined, undefined],
    ];
    for (const [target_text, clientVersion, sdkVersion] of cases) {
      const result = await client.callTool({ name: 'resolve_target', arguments: { target_text } });
      assert.notEqual(result.isError, true, JSON.stringify(result));
      const data = result.structuredContent.data;
      assert.equal(data.target.client_version, clientVersion, target_text);
      assert.equal(data.target.sdk_version, sdkVersion, target_text);
      assert.equal(data.unknown_fields.includes('client_version'), clientVersion === undefined, target_text);
    }
    const explicit = await client.callTool({ name: 'resolve_target', arguments: {
      target_text: 'ExteraGram SDK 2.4.0 client 11.9.0 client 12.0.0',
      target: { client_version: '13.0.0', sdk_version: '3.0.0' }
    } });
    assert.equal(explicit.structuredContent.data.target.client_version, '13.0.0');
    assert.equal(explicit.structuredContent.data.target.sdk_version, '3.0.0');
  } finally {
    await client.close();
    rmSync(temporary, { recursive: true, force: true });
  }
});
