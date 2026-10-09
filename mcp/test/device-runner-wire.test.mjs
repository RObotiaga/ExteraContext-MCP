import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const fixture = resolve(here, 'fixtures', 'fake-device-runner.mjs');
const TEST_SHA = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const ENV_KEYS = [
  'EXTERACONTEXT_DEVICE_MCP_ENTRY',
  'EXTERACONTEXT_DEVICE_MCP_COMMAND',
  'EXTERACONTEXT_DEVICE_MCP_ARGS',
  'EXTERACONTEXT_DEVICE_MCP_CWD',
  'EXTERACONTEXT_DEVICE_MCP_EXPECTED_COMPILED_SHA256'
];

function snapshotEnv() {
  return Object.fromEntries(ENV_KEYS.map(key => [key, process.env[key]]));
}

function restoreEnv(previous) {
  for (const key of ENV_KEYS) {
    if (previous[key] === undefined) delete process.env[key];
    else process.env[key] = previous[key];
  }
}

function configureRunner(root, expectedSha) {
  process.env.EXTERACONTEXT_DEVICE_MCP_ENTRY = fixture;
  process.env.EXTERACONTEXT_DEVICE_MCP_COMMAND = process.execPath;
  process.env.EXTERACONTEXT_DEVICE_MCP_ARGS = JSON.stringify([fixture]);
  process.env.EXTERACONTEXT_DEVICE_MCP_CWD = root;
  process.env.EXTERACONTEXT_DEVICE_MCP_EXPECTED_COMPILED_SHA256 = expectedSha;
}

async function loadClientModule(t) {
  try {
    return await import('../src/device-runner-client.mjs');
  } catch (error) {
    if (error?.code === 'ERR_MODULE_NOT_FOUND' || /Cannot find package/.test(String(error?.message))) {
      t.skip(`Official MCP client SDK is not installed: ${error.message}`);
      return null;
    }
    throw error;
  }
}

async function ensureSdk(t) {
  try {
    await Promise.all([
      import('@modelcontextprotocol/client'),
      import('@modelcontextprotocol/server'),
      import('zod/v4')
    ]);
    return true;
  } catch (error) {
    if (error?.code === 'ERR_MODULE_NOT_FOUND' || /Cannot find package/.test(String(error?.message))) {
      t.skip(`MCP SDK dependencies are missing: ${error.message}`);
      return false;
    }
    throw error;
  }
}

test('trusted runner proxy verifies build/tool contract and forwards a real MCP call', { timeout: 30_000 }, async t => {
  if (!await ensureSdk(t)) return;

  const root = await mkdtemp(resolve(tmpdir(), 'exteracontext-runner-wire-'));
  const previous = snapshotEnv();
  let mod;
  try {
    await mkdir(resolve(root, 'dist'), { recursive: true });
    await writeFile(resolve(root, 'dist', 'runner-build.json'), JSON.stringify({
      version: '0.1.0',
      compiled_sha256: TEST_SHA
    }), 'utf8');
    configureRunner(root, TEST_SHA);

    mod = await loadClientModule(t);
    if (!mod) return;
    const result = await mod.callDeviceRunnerTool('test_list_devices', {});
    assert.deepEqual(result.data, [{ serial: 'fixture-device', state: 'device', details: 'fixture' }]);
    assert.equal(result.backend_meta, null);
    assert.equal(result.config.command, process.execPath);
  } finally {
    await mod?.closeDeviceRunner?.().catch(() => {});
    restoreEnv(previous);
    await rm(root, { recursive: true, force: true });
  }
});

test('trusted runner proxy fails closed on build fingerprint mismatch', { timeout: 30_000 }, async t => {
  if (!await ensureSdk(t)) return;

  const root = await mkdtemp(resolve(tmpdir(), 'exteracontext-runner-pin-'));
  const previous = snapshotEnv();
  let mod;
  try {
    await mkdir(resolve(root, 'dist'), { recursive: true });
    await writeFile(resolve(root, 'dist', 'runner-build.json'), JSON.stringify({ version: '0.1.0', compiled_sha256: TEST_SHA }), 'utf8');
    configureRunner(root, 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb');

    mod = await loadClientModule(t);
    if (!mod) return;
    await assert.rejects(() => mod.callDeviceRunnerTool('test_list_devices', {}), /compiled fingerprint mismatch/);
  } finally {
    await mod?.closeDeviceRunner?.().catch(() => {});
    restoreEnv(previous);
    await rm(root, { recursive: true, force: true });
  }
});
