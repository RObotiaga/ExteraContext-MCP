import test from 'node:test';
import assert from 'node:assert/strict';
import { dirname, resolve } from 'node:path';
import { createServer as createNetServer } from 'node:net';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..', '..');

async function loadSdk(t) {
  try {
    const [{ Client, StreamableHTTPClientTransport }, { StdioClientTransport }] = await Promise.all([
      import('@modelcontextprotocol/client'),
      import('@modelcontextprotocol/client/stdio')
    ]);
    return { Client, StreamableHTTPClientTransport, StdioClientTransport };
  } catch (error) {
    if (error?.code === 'ERR_MODULE_NOT_FOUND' || /Cannot find package/.test(String(error?.message))) {
      t.skip('Official MCP client SDK is not installed; run `cd mcp && npm install` to enable wire integration tests.');
      return null;
    }
    throw error;
  }
}

test('strict stdio negotiates MCP 2026-07-28 and exposes structured tools', { timeout: 90_000 }, async t => {
  const sdk = await loadSdk(t);
  if (!sdk) return;
  const { Client, StdioClientTransport } = sdk;

  const client = new Client(
    { name: 'exteracontext-integration-test', version: '1.0.0' },
    { versionNegotiation: { mode: { pin: '2026-07-28' } } }
  );
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [resolve(root, 'mcp/src/index.mjs'), '--transport', 'stdio', '--modern-only'],
    cwd: root,
    stderr: 'pipe'
  });

  try {
    await client.connect(transport);
    assert.equal(client.getProtocolEra(), 'modern');
    assert.equal(client.getNegotiatedProtocolVersion(), '2026-07-28');

    const listed = await client.listTools();
    const names = new Set(listed.tools.map(tool => tool.name));
    for (const name of ['doctor', 'search_knowledge', 'find_api', 'get_recipe', 'reflect_on_task', 'submit_collector_result']) {
      assert.equal(names.has(name), true, `missing MCP tool: ${name}`);
    }
    const collectorTool = listed.tools.find(tool => tool.name === 'submit_collector_result');
    assert.ok(collectorTool?.inputSchema?.properties?.actor_token, JSON.stringify(collectorTool));
    assert.equal(Boolean(collectorTool?.inputSchema?.properties?.subagent_id), false, JSON.stringify(collectorTool));

    const doctor = await client.callTool({ name: 'doctor', arguments: {} });
    assert.notEqual(doctor.isError, true, JSON.stringify(doctor.content));
    assert.equal(doctor.structuredContent?.ok, true);
    assert.equal(doctor.structuredContent?.meta?.protocol_era, 'modern');
    assert.equal(doctor.structuredContent?.meta?.protocol_revision, '2026-07-28');
    assert.equal(doctor.structuredContent?.meta?.server_version, '0.6.1');

    const api = await client.callTool({ name: 'find_api', arguments: { symbol: 'send_request', limit: 6 } });
    assert.notEqual(api.isError, true, JSON.stringify(api.content));
    assert.equal(api.structuredContent?.ok, true);

    const recipe = await client.callTool({ name: 'get_recipe', arguments: { query: 'cleanup → reload русский', limit: 3 } });
    assert.notEqual(recipe.isError, true, JSON.stringify(recipe.content));
    assert.equal(recipe.structuredContent?.ok, true);
    assert.doesNotMatch(JSON.stringify(recipe.structuredContent), /����|UnicodeEncodeError/);
  } finally {
    await client.close().catch(() => {});
  }
});


async function freePort() {
  const server = createNetServer();
  await new Promise((resolvePromise, rejectPromise) => {
    server.once('error', rejectPromise);
    server.listen(0, '127.0.0.1', resolvePromise);
  });
  const address = server.address();
  const port = typeof address === 'object' && address ? address.port : 0;
  await new Promise(resolvePromise => server.close(resolvePromise));
  return port;
}

function waitForListening(child, timeoutMs = 15_000) {
  return new Promise((resolvePromise, rejectPromise) => {
    let stderr = '';
    const timer = setTimeout(() => rejectPromise(new Error(`HTTP MCP did not start: ${stderr}`)), timeoutMs);
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', chunk => {
      stderr += chunk;
      if (/listening on http:\/\//.test(stderr)) {
        clearTimeout(timer);
        resolvePromise(stderr);
      }
    });
    child.once('exit', code => {
      clearTimeout(timer);
      rejectPromise(new Error(`HTTP MCP exited before listen, code=${code}: ${stderr}`));
    });
  });
}

test('strict Streamable HTTP negotiates MCP 2026-07-28', { timeout: 90_000 }, async t => {
  const sdk = await loadSdk(t);
  if (!sdk) return;
  const { Client, StreamableHTTPClientTransport } = sdk;
  const port = await freePort();
  const child = spawn(process.execPath, [resolve(root, 'mcp/src/index.mjs'), '--transport', 'http', '--host', '127.0.0.1', '--port', String(port), '--modern-only', '--response-mode', 'json'], {
    cwd: root,
    stdio: ['ignore', 'ignore', 'pipe']
  });

  try {
    await waitForListening(child);
    const client = new Client(
      { name: 'exteracontext-http-integration-test', version: '1.0.0' },
      { versionNegotiation: { mode: { pin: '2026-07-28' } } }
    );
    try {
      await client.connect(new StreamableHTTPClientTransport(new URL(`http://127.0.0.1:${port}/mcp`)));
      assert.equal(client.getProtocolEra(), 'modern');
      assert.equal(client.getNegotiatedProtocolVersion(), '2026-07-28');
      const result = await client.callTool({ name: 'doctor', arguments: {} });
      assert.equal(result.structuredContent?.ok, true);
      assert.equal(result.structuredContent?.meta?.protocol_era, 'modern');
    } finally {
      await client.close().catch(() => {});
    }
  } finally {
    child.kill('SIGTERM');
    await new Promise(resolvePromise => {
      if (child.exitCode !== null) return resolvePromise();
      child.once('exit', resolvePromise);
      setTimeout(() => { child.kill('SIGKILL'); resolvePromise(); }, 5000).unref();
    });
  }
});
