import process from 'node:process';
import { dirname, resolve } from 'node:path';
import { homedir } from 'node:os';
import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { VERSION } from './version.mjs';

export class DeviceRunnerError extends Error {
  constructor(message, detail = {}) {
    super(message);
    this.name = 'DeviceRunnerError';
    this.detail = detail;
  }
}

const DEFAULT_EXPECTED_COMPILED_SHA256 = '240ab1d6d164b1e14b04d47289c7e6e17690d84ac78b72997c7767a2c2f742cc';
const REQUIRED_TOOLS = new Set([
  'test_list_devices','test_doctor','plugin_get_sdk_info','plugin_doctor',
  'plugin_list_installed','plugin_inspect_installed','plugin_get_status','plugin_install','plugin_update','plugin_reload','plugin_enable','plugin_disable','plugin_get_logs','plugin_capture_settings','plugin_open_chat','plugin_open_dialogs',
  'plugin_capture_screen','plugin_assert','plugin_get_diagnostics','test_reset_state','test_run','test_collect_evidence',
  'development_start','development_submit_revision','development_execute_iteration','development_get','knowledge_propose_from_test'
]);

let shared = null;

function parseArgsEnv(value) {
  if (!value) return null;
  let parsed;
  try { parsed = JSON.parse(value); }
  catch { throw new DeviceRunnerError('EXTERACONTEXT_DEVICE_MCP_ARGS must be a JSON array of strings'); }
  if (!Array.isArray(parsed) || parsed.some(item => typeof item !== 'string')) {
    throw new DeviceRunnerError('EXTERACONTEXT_DEVICE_MCP_ARGS must be a JSON array of strings');
  }
  return parsed;
}

export function resolveDeviceRunnerConfig() {
  const configuredEntry = process.env.EXTERACONTEXT_DEVICE_MCP_ENTRY?.trim();
  const defaultEntry = resolve(homedir(), '.codex', 'mcp-servers', 'extera-plugin-test-mcp', 'dist', 'src', 'server.js');
  const entry = configuredEntry || defaultEntry;
  const command = process.env.EXTERACONTEXT_DEVICE_MCP_COMMAND?.trim() || process.execPath;
  const args = parseArgsEnv(process.env.EXTERACONTEXT_DEVICE_MCP_ARGS) || [entry];
  const cwd = process.env.EXTERACONTEXT_DEVICE_MCP_CWD?.trim() || dirname(dirname(dirname(entry)));
  return { command, args, cwd, entry, configured: Boolean(configuredEntry), entry_exists: existsSync(entry) };
}

async function verifyRunnerBuild(config) {
  const manifestPath = resolve(config.cwd, 'dist', 'runner-build.json');
  let manifest;
  try { manifest = JSON.parse(await readFile(manifestPath, 'utf8')); }
  catch (error) {
    throw new DeviceRunnerError(`Trusted runner build manifest is missing or invalid: ${manifestPath}`, { cause: error?.message || String(error) });
  }
  const expected = process.env.EXTERACONTEXT_DEVICE_MCP_EXPECTED_COMPILED_SHA256?.trim() || DEFAULT_EXPECTED_COMPILED_SHA256;
  if (manifest.compiled_sha256 !== expected) {
    throw new DeviceRunnerError('Trusted runner compiled fingerprint mismatch', {
      expected_compiled_sha256: expected,
      actual_compiled_sha256: manifest.compiled_sha256,
      manifest_path: manifestPath
    });
  }
  return manifest;
}

async function verifyRunnerProtocol(client, manifest) {
  const server = client.getServerVersion?.();
  if (server?.name && server.name !== 'extera-plugin-test-mcp') {
    throw new DeviceRunnerError(`Unexpected device runner server name: ${server.name}`);
  }
  if (server?.version && manifest?.version && server.version !== manifest.version) {
    throw new DeviceRunnerError('Device runner version does not match its build manifest', { server_version: server.version, manifest_version: manifest.version });
  }
  const listed = await client.listTools();
  const names = new Set((listed?.tools ?? []).map(tool => tool.name));
  const missing = [...REQUIRED_TOOLS].filter(name => !names.has(name));
  if (missing.length) throw new DeviceRunnerError('Trusted device runner is missing required closed-loop tools', { missing_tools: missing });
}

async function connect() {
  const config = resolveDeviceRunnerConfig();
  if (!config.entry_exists && config.args.length === 1 && config.args[0] === config.entry) {
    throw new DeviceRunnerError(
      `Trusted device runner not found at ${config.entry}. Set EXTERACONTEXT_DEVICE_MCP_ENTRY or install extera-plugin-test-mcp there.`,
      { config }
    );
  }

  const manifest = await verifyRunnerBuild(config);

  let Client, StdioClientTransport;
  try {
    [{ Client }, { StdioClientTransport }] = await Promise.all([
      import('@modelcontextprotocol/client'),
      import('@modelcontextprotocol/client/stdio')
    ]);
  } catch (error) {
    throw new DeviceRunnerError('Official MCP client package is required for device-runner proxy calls; run npm install in mcp/', { cause: error?.message || String(error) });
  }

  const client = new Client(
    { name: 'exteracontext-device-runner-proxy', version: VERSION },
    {}
  );
  const transport = new StdioClientTransport({
    command: config.command,
    args: config.args,
    cwd: config.cwd,
    env: { ...process.env },
    stderr: 'pipe'
  });

  try {
    await client.connect(transport);
    await verifyRunnerProtocol(client, manifest);
  } catch (error) {
    await client.close().catch(() => {});
    throw new DeviceRunnerError(`Failed to connect trusted device runner: ${error?.message || error}`, { config });
  }
  return { client, transport, config };
}

async function getShared() {
  if (!shared) {
    shared = connect().catch(error => {
      shared = null;
      throw error;
    });
  }
  return shared;
}

function parseTextContent(result) {
  const text = result?.content?.find?.(item => item?.type === 'text')?.text;
  if (typeof text !== 'string') return null;
  try { return JSON.parse(text); }
  catch { return { ok: !result?.isError, data: text }; }
}

export async function callDeviceRunnerTool(name, args = {}) {
  const { client, config } = await getShared();
  let result;
  try {
    result = await client.callTool({ name, arguments: args });
  } catch (error) {
    shared = null;
    await client.close().catch(() => {});
    throw new DeviceRunnerError(`Device runner tool ${name} failed: ${error?.message || error}`, { tool: name, config });
  }

  const payload = result?.structuredContent ?? parseTextContent(result);
  if (result?.isError || payload?.ok === false) {
    const detail = payload?.error?.detail || payload?.data?.message || payload?.data || payload || result;
    throw new DeviceRunnerError(`Device runner tool ${name} returned an error`, { tool: name, detail, config });
  }
  return {
    data: payload?.data ?? payload,
    warnings: Array.isArray(payload?.warnings) ? payload.warnings : [],
    backend_meta: payload?.meta ?? null,
    config: { command: config.command, cwd: config.cwd, entry: config.entry }
  };
}

export async function closeDeviceRunner() {
  if (!shared) return;
  const current = await shared.catch(() => null);
  shared = null;
  await current?.client?.close?.().catch(() => {});
}
