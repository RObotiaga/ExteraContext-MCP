import * as z from 'zod/v4';
import { VERSION } from './version.mjs';
import { compactTarget, jsonArg, runPythonJson } from './bridge.mjs';
import { callDeviceRunnerTool, DeviceRunnerError } from './device-runner-client.mjs';

const digest = z.string().regex(/^[a-f0-9]{64}$/);
const pluginId = z.string().min(1).max(256);
const targetSchema = z.object({
  client: z.string().nullable().optional(),
  platform: z.string().nullable().optional(),
  client_version: z.string().nullable().optional(),
  sdk_version: z.string().nullable().optional(),
  language: z.string().nullable().optional(),
  plugin_format: z.string().nullable().optional()
}).strict();

const DeviceEnvelopeSchema = z.object({
  ok: z.boolean(),
  data: z.unknown().optional(),
  warnings: z.array(z.string()),
  meta: z.object({
    tool: z.string(),
    server_version: z.string(),
    backend: z.string(),
    backend_meta: z.unknown().nullable().optional(),
    protocol_era: z.string().optional(),
    protocol_revision: z.string().optional(),
    server_target_revision: z.string().optional()
  }).passthrough()
}).passthrough();

const READ = { readOnlyHint: true, destructiveHint: false, openWorldHint: false, idempotentHint: true };
const WRITE = { readOnlyHint: false, destructiveHint: false, openWorldHint: false, idempotentHint: false };

function ok(tool, result, protocolMeta) {
  const structuredContent = {
    ok: true,
    data: result.data,
    warnings: result.warnings ?? [],
    meta: {
      tool,
      server_version: VERSION,
      backend: 'extera-plugin-test-mcp',
      backend_meta: result.backend_meta,
      server_target_revision: '2026-07-28',
      ...protocolMeta
    }
  };
  return { content: [{ type: 'text', text: JSON.stringify(structuredContent, null, 2) }], structuredContent };
}

function fail(tool, error, protocolMeta) {
  const detail = error instanceof DeviceRunnerError
    ? { message: error.message, ...error.detail }
    : { message: error instanceof Error ? error.message : String(error) };
  const structuredContent = {
    ok: false,
    data: detail,
    warnings: [],
    meta: { tool, server_version: VERSION, backend: 'extera-plugin-test-mcp', server_target_revision: '2026-07-28', ...protocolMeta }
  };
  return { isError: true, content: [{ type: 'text', text: JSON.stringify(structuredContent, null, 2) }], structuredContent };
}

function proxy(server, name, config, protocolMeta) {
  server.registerTool(name, { ...config, outputSchema: DeviceEnvelopeSchema }, async input => {
    try { return ok(name, await callDeviceRunnerTool(name, input), protocolMeta); }
    catch (error) { return fail(name, error, protocolMeta); }
  });
}

const assertionSchema = z.object({
  type: z.enum(['log_contains', 'log_absent', 'plugin_enabled', 'plugin_loaded', 'no_crash', 'activity_is', 'plugin_version_is', 'artifact_sha_is', 'log_count']),
  value: z.string().optional()
});

const testStepSchema = z.object({
  action: z.enum(['reset_state', 'reload', 'enable', 'disable', 'open_chat', 'open_dialogs', 'capture_screen']),
  params: z.record(z.string(), z.unknown()).optional(),
  assert: assertionSchema.optional()
});

async function startCaptureFromRuntime(candidate, input) {
  if (!candidate?.candidate_id || !candidate?.claim?.statement || !candidate?.test_run_id || !candidate?.evidence_id) {
    throw new Error('Trusted runner returned an incomplete knowledge candidate');
  }
  const evidence = [{
    evidence_id: candidate.evidence_id,
    evidence_status: 'runtime-verified',
    evidence_type: 'trusted-device-runtime',
    source_type: 'trusted-runtime-runner',
    source: 'extera-plugin-test-mcp',
    excerpt: String(candidate.claim.statement).slice(0, 4096),
    test_run_id: candidate.test_run_id,
    evidence_bundle_sha256: candidate.evidence_bundle_sha256,
    attestation: candidate.attestation,
    provenance: candidate.provenance,
    runtime_candidate_id: candidate.candidate_id
  }];
  const task = `Runtime-backed knowledge proposal for ${candidate.claim.subject}: ${candidate.claim.statement}`;
  const args = ['--task', task.slice(0, 4096), '--evidence', jsonArg(evidence), '--discovery', String(candidate.claim.statement).slice(0, 8192)];
  if (input.task_id) args.push('--task-id', input.task_id);
  if (input.target) args.push('--target', jsonArg(compactTarget(input.target)));
  return runPythonJson('scripts/orchestrate.py', ['reflect', ...args]);
}

export function registerDeviceTools(server, { era = 'unknown' } = {}) {
  const protocolMeta = { protocol_era: era, ...(era === 'modern' ? { protocol_revision: '2026-07-28' } : {}) };

  proxy(server, 'test_list_devices', {
    title: 'List ADB Devices', description: 'List ADB devices without automatically selecting one.', inputSchema: z.object({}), annotations: READ
  }, protocolMeta);
  proxy(server, 'test_doctor', {
    title: 'Device Test Doctor', description: 'Check host test dependencies, selected ADB device and DevServer readiness.', inputSchema: z.object({ serial: z.string().optional() }), annotations: { ...WRITE, idempotentHint: true }
  }, protocolMeta);
  proxy(server, 'plugin_get_sdk_info', {
    title: 'Inspect Plugin SDK Runtime', description: 'Inspect Chaquopy/Python and PySDK health inside the running client.', inputSchema: z.object({ serial: z.string().min(1) }), annotations: READ
  }, protocolMeta);
  proxy(server, 'plugin_doctor', {
    title: 'Plugin Runtime Doctor', description: 'Comprehensive plugin/runtime preflight before acceptance testing.', inputSchema: z.object({ serial: z.string().min(1), plugin_id: pluginId, expected_sha256: digest.optional(), package: z.string().default('org.telegram.messenger').optional() }), annotations: READ
  }, protocolMeta);

  proxy(server, 'plugin_list_installed', {
    title: 'List Installed Plugins', description: 'List installed plugins with ID, version and enabled state.', inputSchema: z.object({ serial: z.string().min(1) }), annotations: READ
  }, protocolMeta);
  proxy(server, 'plugin_inspect_installed', {
    title: 'Inspect Installed Plugin', description: 'Read installed plugin identity, saved container digest and payload manifest.', inputSchema: z.object({ serial: z.string().min(1), plugin_id: pluginId, operation_id: z.string().uuid().optional() }), annotations: { ...READ, readOnlyHint: false }
  }, protocolMeta);
  proxy(server, 'plugin_get_status', {
    title: 'Get Plugin Status', description: 'Read current plugin presence, version and enabled state.', inputSchema: z.object({ serial: z.string().min(1), plugin_id: pluginId }), annotations: READ
  }, protocolMeta);
  proxy(server, 'plugin_install', {
    title: 'Install Plugin', description: 'Install exact EAF bytes through the trusted native installer and verify payload identity.', inputSchema: z.object({ serial: z.string().min(1), path: z.string().min(1), expected_sha256: digest, operation_id: z.string().uuid().optional(), budget_ms: z.number().int().min(1000).max(60000).optional() }), annotations: WRITE
  }, protocolMeta);
  proxy(server, 'plugin_update', {
    title: 'Update Plugin', description: 'Update a disabled installed plugin with exact prior/new artifact identity checks.', inputSchema: z.object({ serial: z.string().min(1), path: z.string().min(1), expected_sha256: digest, expected_installed_sha256: digest, expected_version: z.string().min(1).max(64), operation_id: z.string().uuid().optional(), budget_ms: z.number().int().min(1000).max(60000).optional() }), annotations: { ...WRITE, destructiveHint: true }
  }, protocolMeta);
  proxy(server, 'plugin_reload', {
    title: 'Reload Plugin', description: 'Reload an installed plugin in memory through DevServer.', inputSchema: z.object({ serial: z.string().min(1), plugin_id: pluginId }), annotations: WRITE
  }, protocolMeta);
  proxy(server, 'plugin_enable', {
    title: 'Enable Plugin', description: 'Enable an installed plugin through DevServer.', inputSchema: z.object({ serial: z.string().min(1), plugin_id: pluginId }), annotations: WRITE
  }, protocolMeta);
  proxy(server, 'plugin_disable', {
    title: 'Disable Plugin', description: 'Disable an installed plugin through DevServer.', inputSchema: z.object({ serial: z.string().min(1), plugin_id: pluginId }), annotations: WRITE
  }, protocolMeta);
  proxy(server, 'plugin_get_logs', {
    title: 'Get Plugin Logs', description: 'Read plugin-scoped runtime logs from the running client.', inputSchema: z.object({ serial: z.string().min(1), plugin_id: pluginId, limit: z.number().int().min(1).max(500).default(100).optional(), search: z.string().min(1).optional() }), annotations: READ
  }, protocolMeta);
  proxy(server, 'plugin_capture_settings', {
    title: 'Capture Plugin Settings', description: 'Open plugin settings and create a stitched long screenshot.', inputSchema: z.object({ serial: z.string().min(1), plugin_id: pluginId, max_scrolls: z.number().int().min(0).max(10).default(3).optional(), delay_ms: z.number().int().min(200).max(5000).default(1000).optional(), stitch: z.boolean().default(true).optional(), package: z.string().default('org.telegram.messenger').optional() }), annotations: READ
  }, protocolMeta);
  proxy(server, 'plugin_open_chat', {
    title: 'Open Telegram Chat', description: 'Open a chat/channel/direct dialog by query, username, title or numeric ID.', inputSchema: z.object({ serial: z.string().min(1), query: z.string().min(1).optional(), username: z.string().min(1).optional(), chat_id: z.union([z.number(), z.string()]).optional(), title: z.string().min(1).optional(), package: z.string().default('org.telegram.messenger').optional() }), annotations: WRITE
  }, protocolMeta);
  proxy(server, 'plugin_open_dialogs', {
    title: 'Open Telegram Dialogs', description: 'Return to the dialogs list and select a folder by name or ID.', inputSchema: z.object({ serial: z.string().min(1), folder: z.union([z.string(), z.number()]).default(0).optional(), package: z.string().default('org.telegram.messenger').optional() }), annotations: WRITE
  }, protocolMeta);

  proxy(server, 'plugin_capture_screen', {
    title: 'Capture Device Screen', description: 'Capture a full-resolution screenshot of the current device screen.', inputSchema: z.object({ serial: z.string().min(1), name: z.string().default('screen').optional() }), annotations: READ
  }, protocolMeta);
  proxy(server, 'plugin_assert', {
    title: 'Assert Plugin Runtime State', description: 'Run a deterministic machine assertion against plugin/runtime state.', inputSchema: z.object({ serial: z.string().min(1), plugin_id: pluginId, assertion: assertionSchema }), annotations: READ
  }, protocolMeta);
  proxy(server, 'plugin_get_diagnostics', {
    title: 'Collect Plugin Diagnostics', description: 'Collect plugin state, logs, exceptions, focused activity, SDK health and screenshot after a failure.', inputSchema: z.object({ serial: z.string().min(1), plugin_id: pluginId, package: z.string().default('org.telegram.messenger').optional() }), annotations: READ
  }, protocolMeta);
  proxy(server, 'test_reset_state', {
    title: 'Reset Plugin Test State', description: 'Return the test environment to a known state before an acceptance run.', inputSchema: z.object({ serial: z.string().min(1), plugin_id: pluginId.optional() }), annotations: WRITE
  }, protocolMeta);
  proxy(server, 'test_run', {
    title: 'Run Plugin Acceptance Test', description: 'Execute a declarative acceptance test with machine assertions and persist a trusted test_run_id.', inputSchema: z.object({ serial: z.string().min(1), plugin_id: pluginId, test_name: z.string().default('acceptance_test').optional(), steps: z.array(testStepSchema) }), annotations: WRITE
  }, protocolMeta);
  proxy(server, 'test_collect_evidence', {
    title: 'Collect Trusted Runtime Evidence', description: 'Create the signed canonical EvidenceBundle for an existing test_run_id; caller cannot provide PASS/FAIL.', inputSchema: z.object({ serial: z.string().min(1), test_run_id: z.string().min(1), eaf_path: z.string().optional(), repository_dir: z.string().optional() }), annotations: WRITE
  }, protocolMeta);

  proxy(server, 'development_start', {
    title: 'Start Development Run', description: 'Create a persistent DevelopmentRun for one project task and target device.', inputSchema: z.object({
      project: z.object({ repository: z.string().min(1), workspace: z.string().optional() }),
      task: z.object({ kind: z.string().default('task'), id: z.union([z.string(), z.number()]), title: z.string().optional(), acceptance_plan: z.object({ test_name: z.string(), steps: z.array(z.unknown()) }).optional() }),
      target: z.object({ client: z.string().default('AyuGram'), device_serial: z.string().min(1), package: z.string().default('org.telegram.messenger').optional() })
    }), annotations: WRITE
  }, protocolMeta);
  proxy(server, 'development_submit_revision', {
    title: 'Submit Development Revision', description: 'Bind a new EAF/source revision to a new Iteration in an existing DevelopmentRun.', inputSchema: z.object({ development_run_id: z.string().min(1), eaf_path: z.string().min(1), repository_dir: z.string().optional() }), annotations: WRITE
  }, protocolMeta);
  proxy(server, 'development_execute_iteration', {
    title: 'Execute Development Iteration', description: 'Run preflight, install/update, reload, reset, acceptance, evidence or diagnostics for one Iteration.', inputSchema: z.object({ development_run_id: z.string().min(1), iteration_id: z.string().optional() }), annotations: WRITE
  }, protocolMeta);
  proxy(server, 'development_get', {
    title: 'Get Development Run', description: 'Read current DevelopmentRun state, iteration history and latest repair context.', inputSchema: z.object({ development_run_id: z.string().min(1) }), annotations: READ
  }, protocolMeta);

  server.registerTool('knowledge_propose_from_test', {
    title: 'Propose Knowledge From Trusted Runtime Test',
    description: 'Verify a PASS test through the trusted device runner, create a runtime-backed candidate, and immediately start ExteraContext collector/blind-verifier orchestration. This never promotes knowledge directly.',
    inputSchema: z.object({
      serial: z.string().min(1),
      test_run_id: z.string().min(1),
      claim: z.object({ subject: z.string().min(1), statement: z.string().min(1), scope: z.string().optional() }),
      task_id: z.string().max(256).optional(),
      target: targetSchema.optional()
    }),
    outputSchema: DeviceEnvelopeSchema,
    annotations: WRITE
  }, async input => {
    try {
      const runnerInput = { serial: input.serial, test_run_id: input.test_run_id, claim: input.claim };
      const backend = await callDeviceRunnerTool('knowledge_propose_from_test', runnerInput);
      const candidate = backend.data?.candidate ?? backend.data;
      const capture = await startCaptureFromRuntime(candidate, input);
      return ok('knowledge_propose_from_test', {
        backend_candidate: backend.data,
        knowledge_capture: capture,
        next_stage: 'collector',
        note: 'Runtime evidence is trusted machine evidence; the derived knowledge claim still requires collector and blind verifier stages.'
      }, protocolMeta);
    } catch (error) {
      return fail('knowledge_propose_from_test', error, protocolMeta);
    }
  });
}
