import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { request } from 'node:http';
import { createServer } from 'node:net';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

const entry = resolve(dirname(fileURLToPath(import.meta.url)), '../src/index.mjs');
let sdkAvailable = true;
try { import.meta.resolve('@modelcontextprotocol/server'); import.meta.resolve('@modelcontextprotocol/node'); }
catch { sdkAvailable = false; }
const token = 'offline-http-test-token-32-bytes-long-12345678';

async function freePort() {
  const server = createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function rawOptions(url, headers) {
  return new Promise((resolve, reject) => {
    const req = request(url, { method: 'OPTIONS', headers }, res => {
      res.resume();
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers }));
    });
    req.on('error', reject);
    req.end();
  });
}

async function start(port) {
  const child = spawn(process.execPath, [entry, '--transport', 'http', '--port', String(port)], {
    env: { ...process.env, EXTERACONTEXT_MCP_HTTP_TOKEN: token }, stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true
  });
  let diagnostic = '';
  child.stderr.on('data', chunk => { diagnostic += chunk.toString().slice(0, 2048); });
  const url = `http://127.0.0.1:${port}/mcp`;
  for (let i = 0; i < 60; i++) {
    if (child.exitCode !== null) throw new Error(`HTTP process terminated: ${diagnostic}`);
    try { await fetch(url, { method: 'GET' }); return { child, url }; }
    catch { await delay(50); }
  }
  child.kill();
  throw new Error(`HTTP process failed to listen: ${diagnostic}`);
}

test('HTTP denies unauthenticated traffic, validates preflight and accepts bearer',
  { skip: !sdkAvailable && 'MCP SDK is not installed locally; no installation performed', timeout: 15_000 }, async () => {
    const { child, url } = await start(await freePort());
    try {
      const denied = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
      assert.equal(denied.status, 401);
      assert.notEqual(denied.headers.get('access-control-allow-origin'), '*');
      const authorized = await fetch(url, {
        method: 'POST',
        headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json', accept: 'application/json, text/event-stream' },
        body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'ping' })
      });
      assert.notEqual(authorized.status, 401);
      const evil = await fetch(url, { method: 'OPTIONS', headers: { origin: 'https://attacker.example' } });
      assert.notEqual(evil.status, 204);
      assert.notEqual(evil.headers.get('access-control-allow-origin'), '*');
      const good = await fetch(url, { method: 'OPTIONS', headers: { origin: new URL(url).origin } });
      assert.equal(good.status, 204);
      assert.equal(good.headers.get('access-control-allow-origin'), new URL(url).origin);
      const malformed = await fetch(`http://127.0.0.1:${new URL(url).port}//mcp`, { headers: { authorization: `Bearer ${token}` } });
      assert.equal(malformed.status, 400);
      const authority = new URL(url).host;
      for (const origin of ['http://localhost:8080', 'https://127.0.0.1:' + new URL(url).port,
        'null', 'http://127.0.0.1:' + new URL(url).port + '/other',
        'http://127.0.0.1:' + new URL(url).port + '@attacker.example']) {
        const rejected = await rawOptions(url, { Host: authority, Origin: origin });
        assert.equal(rejected.status, 403, `Origin ${origin} must fail closed`);
        assert.equal(rejected.headers['access-control-allow-origin'], undefined);
      }
      for (const host of ['attacker.example', '127.0.0.1:0', '127.0.0.1:99999',
        '127.0.0.1:123:45', '127.0.0.1@attacker.example', '127.0.0.1']) {
        const rejected = await rawOptions(url, { Host: host, Origin: new URL(url).origin });
        assert.equal(rejected.status, 400, `Host ${host} must fail closed`);
        assert.equal(rejected.headers['access-control-allow-origin'], undefined);
      }
    } finally {
      child.kill();
      await Promise.race([new Promise(resolve => child.once('close', resolve)), delay(2000)]);
    }
  });
