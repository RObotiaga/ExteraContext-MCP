import test from 'node:test';
import assert from 'node:assert/strict';
import { dirname, resolve } from 'node:path';
import { createServer as createNetServer } from 'node:net';
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
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
    env: { ...process.env }, // SDK default environment omits EXTERACONTEXT_DB and EXTERACONTEXT_PYTHON.
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

test('HTTP bearer boundary rejects unauthenticated requests and accepts test token', { timeout: 90_000 }, async t => {
  try {
    await Promise.all([
      import('@modelcontextprotocol/server'),
      import('@modelcontextprotocol/node'),
      import('zod/v4')
    ]);
  } catch (error) {
    if (error?.code === 'ERR_MODULE_NOT_FOUND' || /Cannot find package|Cannot find module/.test(String(error?.message))) {
      t.skip(`MCP server dependencies are missing or incomplete locally (${error.message}); no dependency installation performed.`);
      return;
    }
    throw error;
  }

  const token = randomBytes(32).toString('hex');
  const port = await freePort();
  const child = spawn(process.execPath, [resolve(root, 'mcp/src/index.mjs'), '--transport', 'http', '--host', '127.0.0.1', '--port', String(port), '--modern-only', '--response-mode', 'json'], {
    cwd: root,
    env: { ...process.env, EXTERACONTEXT_MCP_HTTP_TOKEN: token },
    stdio: ['ignore', 'ignore', 'pipe']
  });

  try {
    await waitForListening(child);
    const url = `http://127.0.0.1:${port}/mcp`;
    const request = { method: 'POST', headers: { 'content-type': 'application/json', accept: 'application/json, text/event-stream' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'ping' }) };
    const denied = await fetch(url, request);
    assert.equal(denied.status, 401, 'requests without Authorization must be rejected');
    assert.equal(denied.headers.get('www-authenticate'), 'Bearer');
    const authorized = await fetch(url, { ...request, headers: { ...request.headers, Authorization: `Bearer ${token}` } });
    assert.notEqual(authorized.status, 401, 'the test-only bearer must be accepted');
    assert.ok(authorized.status < 500, `authorized MCP request failed with HTTP ${authorized.status}: ${await authorized.text()}`);

    await t.test('authenticated Streamable HTTP client negotiates MCP 2026-07-28', async clientTest => {
      const sdk = await loadSdk(clientTest);
      if (!sdk) return;
      const client = new sdk.Client(
        { name: 'exteracontext-http-integration-test', version: '1.0.0' },
        { versionNegotiation: { mode: { pin: '2026-07-28' } } }
      );
      try {
        // @modelcontextprotocol/client 2.1.0 dist/index.d.mts documents authProvider.token()
        // as a bearer token source for every StreamableHTTPClientTransport request.
        const transport = new sdk.StreamableHTTPClientTransport(new URL(url), {
          authProvider: { token: async () => token }
        });
        await client.connect(transport);
        assert.equal(client.getProtocolEra(), 'modern');
        assert.equal(client.getNegotiatedProtocolVersion(), '2026-07-28');
        const result = await client.callTool({ name: 'doctor', arguments: {} });
        assert.equal(result.structuredContent?.ok, true);
        assert.equal(result.structuredContent?.meta?.protocol_era, 'modern');
      } finally {
        await client.close().catch(() => {});
      }
    });
  } finally {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
    await new Promise(resolvePromise => {
      if (child.exitCode !== null || child.signalCode !== null) return resolvePromise();
      const timer = setTimeout(() => { child.kill('SIGKILL'); resolvePromise(); }, 5000);
      timer.unref();
      child.once('exit', () => { clearTimeout(timer); resolvePromise(); });
    });
  }
});
