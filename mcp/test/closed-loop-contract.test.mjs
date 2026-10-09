import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const mcp = resolve(here, '..');
const text = path => readFile(resolve(mcp, path), 'utf8');

const expectedDeviceTools = [
  'test_list_devices','test_doctor','plugin_get_sdk_info','plugin_doctor',
  'plugin_list_installed','plugin_inspect_installed','plugin_get_status','plugin_install','plugin_update','plugin_reload','plugin_enable','plugin_disable','plugin_get_logs','plugin_capture_settings','plugin_open_chat','plugin_open_dialogs',
  'plugin_capture_screen','plugin_assert','plugin_get_diagnostics','test_reset_state','test_run','test_collect_evidence',
  'development_start','development_submit_revision','development_execute_iteration','development_get',
  'knowledge_propose_from_test'
];

test('closed-loop server exposes all 27 trusted device/development tools', async () => {
  const source = await text('src/device-tools.mjs');
  for (const name of expectedDeviceTools) {
    assert.match(source, new RegExp(`(?:proxy\\(server,\\s*|registerTool\\(\\s*)'${name}'`), `missing ${name}`);
  }
  assert.equal(expectedDeviceTools.length, 27);
});

test('runtime knowledge bridge starts guarded ExteraContext capture rather than promoting directly', async () => {
  const source = await text('src/device-tools.mjs');
  assert.match(source, /evidence_status:\s*'runtime-verified'/);
  assert.match(source, /scripts\/orchestrate\.py/);
  assert.match(source, /\['reflect',\s*\.\.\.args\]/);
  assert.match(source, /next_stage:\s*'collector'/);
  assert.doesNotMatch(source, /runtime_verified_promotion.*status:\s*'PASS'/);
});

test('device runner is an isolated configurable stdio subprocess', async () => {
  const source = await text('src/device-runner-client.mjs');
  assert.match(source, /EXTERACONTEXT_DEVICE_MCP_ENTRY/);
  assert.match(source, /EXTERACONTEXT_DEVICE_MCP_EXPECTED_COMPILED_SHA256/);
  assert.match(source, /240ab1d6d164b1e14b04d47289c7e6e17690d84ac78b72997c7767a2c2f742cc/);
  assert.match(source, /StdioClientTransport/);
});

test('closed-loop entrypoint wraps the existing knowledge server', async () => {
  const wrapper = await text('src/closed-loop-server.mjs');
  const entry = await text('src/index-closed-loop.mjs');
  assert.match(wrapper, /buildServer\(options\)/);
  assert.match(wrapper, /registerDeviceTools\(server, options\)/);
  assert.match(entry, /buildClosedLoopServer/);
});
