import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const fixture = resolve(here, 'fixtures', 'fake-device-runner.mjs');
const TEST_SHA = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';

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

test('trusted runner proxy verifies build/tool contract and forwards a real MCP call', { timeout: 30_000 }, async t => {
  try {
    await Promise.all([
      import('@modelcontextprotocol/client'),
      import('@modelcontextprotocol/server'),
      import('zod/v4')
    ]);
  } catch (error) {
    if (error?.code === 'ERR_MODULE_NOT_FOUND' || /Cannot find package/.test(String(error?.message))) {
      t.skip(`MCP SDK dependencies are missing: ${error.message}`);
      return;
    }
    throw error;
  }

  const root = await mkdtemp(resolve(tmpdir(), 'exteracontext-runner-wire-'));
  await mkdir(resolve(root, 'dist'), { recursive: true });
  await writeFile(resolve(root, 'dist', 'runner-build.json'), JSON.stringify({
    version: '0.1.0',
    compiled_sha256: TEST_SHA
  }), 'utf8');

  const previous = {
    entry: process.env.EXTERACONTEXT_DEVICE_MCP_ENTRY,
    command: process.env.EXTERACONTEXT_DEVICE_MCP_COMMAND,
    args: process.env.EXTERACONTEXT_DEVICE_MCP_ARGS,
    cwd: process.env.EXTERACONTEXT_DEVICE_MCP_CWD,
    expected: process.env.EXTERACONTEXT_DEVICE_MCP_EXPECTED_COMPILED_SHA256
  };

  process.env.EXTERACONTEXT_DEVICE_MCP_ENTRY = fixture;
  process.env.EXTERACONTEXT_DEVICE_MCP_COMMAND = process.execPath;
  process.env.EXTERACONTEXT_DEVICE_MCP_ARGS = JSON.stringify([fixture]);
  process.env.EXTERACONTEXT_DEVICE_MCP_CWD = root;
  process.env.EXTERACONTEXT_DEVICE_MCP_EXPECTED_COMPILED_SHA256 = TEST_SHA;

  const mod = await loadClientModule(t);
  if (!mod) return;
  try {
    const result = await mod.callDeviceRunnerTool('test_list_devices', {});
    assert.deepEqual(result.data, [{ serial: 'fixture-device', state: 'device', details: 'fixture' }]);
    assert.equal(result.backend_meta, null);
    assert.equal(result.config.command, process.execPath);
  } finally {
    await mod.closeDeviceRunner();
    const restore = (key, value) => value === undefined ? delete process.env[key] : process.env[key] = value;
    restore('EXTERACONTEXT_DEVICE_MCP_ENTRY', previous.entry);
    restore('EXTERACONTEXT_DEVICE_MCP_COMMAND', previous.command);
    restore('EXTERACONTEXT_DEVICE_MCP_ARGS', previous.args);
    restore('EXTERACONTEXT_DEVICE_MCP_CWD', previous.cwd);
    restore('EXTERACONTEXT_DEVICE_MCP_EXPECTED_COMPILED_SHA256', previous.expected);
    await rm(root, { recursive: true, force: true });
  }
});

test('trusted runner proxy fails closed on build fingerprint mismatch', { timeout: 30_000 }, async t => {
  try {
    await import('@modelcontextprotocol/client');
  } catch (error) {
    if (error?.code === 'ERR_MODULE_NOT_FOUND' || /Cannot find package/.test(String(error?.message))) {
      t.skip(`MCP SDK dependencies are missing: ${error.message}`);
      return;
    }
    throw error;
  }

  const root = await mkdtemp(resolve(tmpdir(), 'exteracontext-runner-pin-'));
  await mkdir(resolve(root, 'dist'), { recursive: true });
  await writeFile(resolve(root, 'dist', 'runner-build.json'), JSON.stringify({ version: '0.1.0', compiled_sha256: TEST_SHA }), 'utf8');
  process.env.EXTERACONTEXT_DEVICE_MCP_ENTRY = fixture;
  process.env.EXTERACONTEXT_DEVICE_MCP_COMMAND = process.execPath;
  process.env.EXTERACONTEXT_DEVICE_MCP_ARGS = JSON.stringify([fixture]);
  process.env.EXTERACONTEXT_DEVICE_MCP_CWD = root;
  process.env.EXTERACONTEXT_DEVICE_MCP_EXPECTED_COMPILED_SHA256 = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';

  const mod = await loadClientModule(t);
  if (!mod) return;
  try {
    await assert.rejects(() => mod.callDeviceRunnerTool('test_list_devices', {}), /compiled fingerprint mismatch/);
  } finally {
    await mod.closeDeviceRunner();
    delete process.env.EXTERACONTEXT_DEVICE_MCP_ENTRY;
    delete process.env.EXTERACONTEXT_DEVICE_MCP_COMMAND;
    delete process.env.EXTERACONTEXT_DEVICE_MCP_ARGS;
    delete process.env.EXTERACONTEXT_DEVICE_MCP_CWD;
    delete process.env.EXTERACONTEXT_DEVICE_MCP_EXPECTED_COMPILED_SHA256;
    await rm(root, { recursive: true, force: true });
  }
});
