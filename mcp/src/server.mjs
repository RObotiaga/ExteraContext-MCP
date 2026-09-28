import { McpServer } from '@modelcontextprotocol/server';
import * as z from 'zod/v4';
import { BridgeError, compactTarget, jsonArg, runPythonJson, targetLabel } from './bridge.mjs';

const VERSION = '0.6.1';

const TargetSchema = z.object({
  client: z.string().nullable().optional(),
  platform: z.string().nullable().optional(),
  client_version: z.string().nullable().optional(),
  sdk_version: z.string().nullable().optional(),
  language: z.string().nullable().optional(),
  plugin_format: z.string().nullable().optional()
}).strict();

const EvidenceSchema = z.object({
  evidence_id: z.string().optional(),
  evidence_status: z.enum(['runtime-verified', 'code', 'docs', 'inference', 'secondary', 'unavailable']).optional(),
  evidence_type: z.string().optional(),
  source_type: z.string().nullable().optional(),
  source: z.string().nullable().optional(),
  repository: z.string().nullable().optional(),
  commit: z.string().nullable().optional(),
  path: z.string().nullable().optional(),
  lines: z.string().nullable().optional(),
  url: z.string().nullable().optional(),
  excerpt: z.string().nullable().optional()
}).passthrough();

const CollectorResultSchema = z.object({
  action: z.enum(['propose', 'skip']),
  claim: z.string(),
  api_symbol: z.string().nullable().optional(),
  kind: z.enum(['api', 'behavior', 'compatibility', 'recipe', 'negative-evidence', 'runtime-result', 'other']),
  scope: z.enum(['project', 'target', 'global']),
  target: TargetSchema,
  evidence_status: z.enum(['runtime-verified', 'code', 'docs', 'inference', 'secondary', 'unavailable']),
  evidence_refs: z.array(z.string()),
  existing_claim: z.string().nullable(),
  conflicts_with: z.array(z.string()),
  notes: z.string().nullable().optional()
}).strict();

const PhaseAResultSchema = z.object({
  statement: z.string().min(1),
  scope: z.record(z.string(), z.unknown()),
  evidence_status: z.enum(['runtime-verified', 'code', 'docs', 'inference', 'secondary', 'unavailable']),
  uncertainty: z.string().nullable(),
  conflicts_seen: z.array(z.string()).optional()
}).strict();

const PhaseBResultSchema = z.object({
  verdict: z.enum(['accept', 'accept-with-changes', 'attach-evidence', 'conflict', 'reject', 'needs-runtime']),
  final_statement: z.string().nullable(),
  existing_subject_type: z.enum(['claim', 'legacy_fact']).nullable(),
  existing_subject_id: z.string().nullable(),
  notes: z.string().nullable()
}).strict();

const RuntimeActorSchema = z.object({
  provider: z.string().optional(),
  child_id: z.string().optional(),
  session_id: z.string().optional(),
  invocation_id: z.string().optional(),
  run_id: z.string().optional()
}).passthrough();

const EnvelopeSchema = z.object({
  ok: z.boolean(),
  data: z.unknown().optional(),
  warnings: z.array(z.string()),
  meta: z.object({
    tool: z.string(),
    server_version: z.string(),
    evidence_boundary: z.string().optional(),
    protocol_era: z.string().optional(),
    protocol_revision: z.string().optional(),
    server_target_revision: z.string().optional()
  })
});

const READ_ANNOTATIONS = {
  readOnlyHint: true,
  destructiveHint: false,
  openWorldHint: false,
  idempotentHint: true
};

const APPEND_ANNOTATIONS = {
  readOnlyHint: false,
  destructiveHint: false,
  openWorldHint: false,
  idempotentHint: false
};

function envelope(tool, data, warnings = [], extraMeta = {}) {
  return {
    ok: true,
    data,
    warnings,
    meta: {
      tool,
      server_version: VERSION,
      evidence_boundary: 'Static evidence is not runtime verification; donor evidence is not target-client evidence.',
      server_target_revision: '2026-07-28',
      ...extraMeta
    }
  };
}

function toolResponse(tool, data, warnings = [], extraMeta = {}) {
  const structuredContent = envelope(tool, data, warnings, extraMeta);
  return {
    content: [{ type: 'text', text: JSON.stringify(structuredContent, null, 2) }],
    structuredContent
  };
}

function errorResponse(tool, error) {
  const detail = error instanceof BridgeError
    ? { message: error.message, stderr: error.stderr?.trim() || undefined, exitCode: error.exitCode }
    : { message: error instanceof Error ? error.message : String(error) };
  const structuredContent = {
    ok: false,
    data: detail,
    warnings: [],
    meta: { tool, server_version: VERSION, server_target_revision: '2026-07-28' }
  };
  return {
    isError: true,
    content: [{ type: 'text', text: JSON.stringify(structuredContent, null, 2) }],
    structuredContent
  };
}

async function guarded(tool, fn) {
  try {
    return await fn();
  } catch (error) {
    return errorResponse(tool, error);
  }
}

function normalizeTarget(input) {
  const explicit = compactTarget(input.target || {});
  const text = input.target_text || '';
  const lower = text.toLowerCase();
  const target = { ...explicit };
  if (!target.client) {
    if (lower.includes('exteragram')) target.client = 'ExteraGram';
    else if (lower.includes('ayugram')) target.client = 'AyuGram';
  }
  if (!target.platform && lower.includes('android')) target.platform = 'Android';
  if (!target.language) {
    if (lower.includes('python') || lower.includes('pysdk')) target.language = 'Python';
    else if (lower.includes('java')) target.language = 'Java';
  }
  if (!target.client_version) {
    const m = text.match(/(?:client|exteragram|ayugram)?\s*v?(\d+\.\d+(?:\.\d+){0,2})/i);
    if (m) target.client_version = m[1];
  }
  if (!target.sdk_version) {
    const m = text.match(/(?:sdk|pysdk)\s*v?(\d+\.\d+(?:\.\d+){0,3})/i);
    if (m) target.sdk_version = m[1];
  }
  const unknown = ['client', 'platform', 'client_version', 'sdk_version', 'language', 'plugin_format'].filter(k => !target[k]);
  return { target, unknown_fields: unknown };
}

function versionMatch(fact, target) {
  const version = String(fact.version || '').toLowerCase();
  const clientVersion = String(target.client_version || '').toLowerCase();
  const sdkVersion = String(target.sdk_version || '').toLowerCase();
  const requested = [clientVersion, sdkVersion].filter(Boolean);
  if (!requested.length) return false;
  return requested.every(v => version.includes(v));
}

function compatibilityFromFacts(facts, target) {
  const exact = facts.filter(f => versionMatch(f, target) && ['official', 'target-ecosystem'].includes(f.directness));
  const negative = exact.find(f => f.topic === 'negative-evidence' || /\b(not found|unavailable|removed|does not exist|не найден|недоступ)/i.test(String(f.claim || '')));
  if (negative) {
    return { verdict: 'incompatible', confidence: 'evidence-supported', reason: 'Exact-target negative evidence exists.', evidence: exact.slice(0, 8) };
  }
  if (exact.length) {
    return { verdict: 'compatible', confidence: 'evidence-supported', reason: 'Exact requested version appears in direct target/official evidence.', evidence: exact.slice(0, 8) };
  }
  return { verdict: 'unknown', confidence: 'conservative', reason: 'No exact-version direct evidence establishes compatibility or incompatibility.', evidence: facts.slice(0, 8) };
}

async function query(command, value, options = {}) {
  const args = [`scripts/query.py`, command];
  if (value !== undefined) args.push(value);
  if (options.target) args.push('--target', options.target);
  if (options.clientVersion) args.push('--client-version', options.clientVersion);
  if (options.sdkVersion) args.push('--sdk-version', options.sdkVersion);
  if (options.limit !== undefined) args.push('--limit', String(options.limit));
  if (command !== 'doctor') args.push('--format', 'json');
  return runPythonJson(args.shift(), args);
}

async function orchestrate(command, args) {
  return runPythonJson('scripts/orchestrate.py', [command, ...args]);
}

async function knowledge(command, args) {
  return runPythonJson('scripts/knowledge.py', [command, ...args]);
}

export function buildServer({ era = 'unknown', legacyAllowed = true } = {}) {
  const protocolMeta = {
    protocol_era: era,
    ...(era === 'modern' ? { protocol_revision: '2026-07-28' } : {})
  };
  const server = new McpServer(
    {
      name: 'ExteraContext',
      version: VERSION
    },
    {
      capabilities: { tools: { listChanged: false } },
      instructions: 'Use ExteraContext before relying on ExteraGram/AyuGram plugin APIs not defined in the current repository. Prefer exact target/version evidence. Never promote donor-only evidence to target support. New knowledge must pass collector plus blind verifier write-back.'
    }
  );

  server.registerTool('resolve_target', {
    title: 'Resolve ExteraContext Target',
    description: 'Normalize an ExteraGram/AyuGram plugin target. Unknown fields remain unknown; this tool never invents versions.',
    inputSchema: z.object({
      target_text: z.string().optional(),
      target: TargetSchema.optional()
    }),
    outputSchema: EnvelopeSchema,
    annotations: READ_ANNOTATIONS
  }, async input => guarded('resolve_target', async () => toolResponse('resolve_target', normalizeTarget(input), [], protocolMeta)));

  server.registerTool('search_knowledge', {
    title: 'Search ExteraContext Knowledge',
    description: 'Build a task-specific evidence packet. Keep each query scoped to one technical concept for best retrieval quality.',
    inputSchema: z.object({
      query: z.string().min(2),
      target: TargetSchema.optional(),
      target_text: z.string().optional(),
      limit: z.number().int().min(1).max(30).default(10)
    }),
    outputSchema: EnvelopeSchema,
    annotations: READ_ANNOTATIONS
  }, async input => guarded('search_knowledge', async () => {
    const resolved = normalizeTarget(input);
    const data = await query('context', input.query, {
      target: targetLabel(resolved.target),
      clientVersion: resolved.target.client_version,
      sdkVersion: resolved.target.sdk_version,
      limit: input.limit
    });
    const warnings = [];
    if (resolved.unknown_fields.includes('client_version')) warnings.push('Client version is unknown; version compatibility is not established.');
    if (resolved.unknown_fields.includes('sdk_version')) warnings.push('SDK version is unknown; SDK compatibility is not established.');
    return toolResponse('search_knowledge', { resolved_target: resolved, context: data }, warnings, protocolMeta);
  }));

  server.registerTool('find_api', {
    title: 'Find Plugin API',
    description: 'Look up a concrete ExteraGram/AyuGram API symbol and its evidence.',
    inputSchema: z.object({ symbol: z.string().min(1), limit: z.number().int().min(1).max(30).default(12) }),
    outputSchema: EnvelopeSchema,
    annotations: READ_ANNOTATIONS
  }, async input => guarded('find_api', async () => toolResponse('find_api', await query('api', input.symbol, { limit: input.limit }), [], protocolMeta)));

  server.registerTool('find_usage', {
    title: 'Find Existing API Usage',
    description: 'Find source-backed usages/examples for an API or behavior. Results are ranked evidence, not a guarantee of target compatibility.',
    inputSchema: z.object({ query: z.string().min(1), limit: z.number().int().min(1).max(30).default(12) }),
    outputSchema: EnvelopeSchema,
    annotations: READ_ANNOTATIONS
  }, async input => guarded('find_usage', async () => {
    const all = await query('search', input.query, { limit: Math.min(input.limit * 3, 60) });
    const list = Array.isArray(all) ? all : [];
    const usages = list.filter(item => ['code', 'runtime-verified'].includes(item.status) || item.directness === 'target-ecosystem').slice(0, input.limit);
    return toolResponse('find_usage', { usages, searched: list.length }, usages.length ? [] : ['No code/runtime usage was found; do not infer that the API is supported.'], protocolMeta);
  }));

  server.registerTool('get_recipe', {
    title: 'Get Development Recipe',
    description: 'Retrieve task recipes such as hook lifecycle cleanup, account routing, UI-thread work, or packaging.',
    inputSchema: z.object({ query: z.string().min(1), limit: z.number().int().min(1).max(20).default(8) }),
    outputSchema: EnvelopeSchema,
    annotations: READ_ANNOTATIONS
  }, async input => guarded('get_recipe', async () => toolResponse('get_recipe', await query('recipe', input.query, { limit: input.limit }), [], protocolMeta)));

  server.registerTool('get_evidence', {
    title: 'Get Fact Evidence',
    description: 'Show provenance for a legacy fact, agent claim, or API symbol, including historical collector/reviewer provenance when available.',
    inputSchema: z.object({ key: z.string().min(1), limit: z.number().int().min(1).max(30).default(12) }),
    outputSchema: EnvelopeSchema,
    annotations: READ_ANNOTATIONS
  }, async input => guarded('get_evidence', async () => toolResponse('get_evidence', await query('evidence', input.key, { limit: input.limit }), [], protocolMeta)));

  server.registerTool('check_compatibility', {
    title: 'Check Target Compatibility',
    description: 'Conservatively check whether evidence establishes an API for an exact client/SDK version. Returns unknown instead of guessing.',
    inputSchema: z.object({
      symbol: z.string().min(1),
      target: TargetSchema,
      limit: z.number().int().min(1).max(30).default(16)
    }),
    outputSchema: EnvelopeSchema,
    annotations: READ_ANNOTATIONS
  }, async input => guarded('check_compatibility', async () => {
    const facts = await query('api', input.symbol, { limit: input.limit });
    const list = Array.isArray(facts) ? facts : [];
    const result = compatibilityFromFacts(list, compactTarget(input.target));
    const warnings = result.verdict === 'unknown' ? ['Unknown means evidence is insufficient; it is not a compatibility failure.'] : [];
    return toolResponse('check_compatibility', { symbol: input.symbol, target: compactTarget(input.target), ...result }, warnings, protocolMeta);
  }));

  server.registerTool('reflect_on_task', {
    title: 'Start Knowledge Capture',
    description: 'Start the two-subagent write-back protocol from original evidence and produce a collector prompt/schema. This only creates a candidate workflow; it does not trust new knowledge.',
    inputSchema: z.object({
      task: z.string().min(1),
      task_id: z.string().optional(),
      discovery: z.string().optional(),
      target: TargetSchema.optional(),
      evidence: z.array(EvidenceSchema).min(1)
    }),
    outputSchema: EnvelopeSchema,
    annotations: APPEND_ANNOTATIONS
  }, async input => guarded('reflect_on_task', async () => {
    const args = ['--task', input.task, '--evidence', jsonArg(input.evidence)];
    if (input.task_id) args.push('--task-id', input.task_id);
    if (input.discovery) args.push('--discovery', input.discovery);
    if (input.target) args.push('--target', jsonArg(compactTarget(input.target)));
    return toolResponse('reflect_on_task', await orchestrate('reflect', args), [], protocolMeta);
  }));

  server.registerTool('submit_collector_result', {
    title: 'Submit Collector Result',
    description: 'Persist a collector candidate using the one-time MCP-issued collector capability token, then issue a verifier capability token and blind Phase-A prompt. DSH child IDs are optional provenance, not authorization.',
    inputSchema: z.object({
      orchestration_id: z.string().min(1),
      actor_token: z.string().min(16),
      result: CollectorResultSchema,
      model: z.string().optional(),
      runtime_actor: RuntimeActorSchema.optional()
    }),
    outputSchema: EnvelopeSchema,
    annotations: APPEND_ANNOTATIONS
  }, async input => guarded('submit_collector_result', async () => {
    const args = ['--id', input.orchestration_id, '--actor-token', input.actor_token, '--result', jsonArg(input.result)];
    if (input.model) args.push('--model', input.model);
    if (input.runtime_actor) args.push('--runtime-actor', jsonArg(input.runtime_actor));
    return toolResponse('submit_collector_result', await orchestrate('collector-result', args), [], protocolMeta);
  }));

  server.registerTool('submit_verifier_phase_a', {
    title: 'Submit Blind Verifier Phase A',
    description: 'Persist the independent blind extraction with the MCP-issued verifier capability token. The collector candidate is revealed only after this transition.',
    inputSchema: z.object({
      orchestration_id: z.string().min(1),
      actor_token: z.string().min(16),
      result: PhaseAResultSchema,
      model: z.string().optional(),
      runtime_actor: RuntimeActorSchema.optional()
    }),
    outputSchema: EnvelopeSchema,
    annotations: APPEND_ANNOTATIONS
  }, async input => guarded('submit_verifier_phase_a', async () => {
    const args = ['--id', input.orchestration_id, '--actor-token', input.actor_token, '--result', jsonArg(input.result)];
    if (input.model) args.push('--model', input.model);
    if (input.runtime_actor) args.push('--runtime-actor', jsonArg(input.runtime_actor));
    return toolResponse('submit_verifier_phase_a', await orchestrate('phase-a-result', args), [], protocolMeta);
  }));

  server.registerTool('submit_verifier_phase_b', {
    title: 'Submit Verifier Phase B',
    description: 'Submit the comparison verdict using the same verifier capability token used for Phase A. If runtime child identity was supplied in Phase A, matching identity is required here. SQLite guards remain the final promotion gate.',
    inputSchema: z.object({
      orchestration_id: z.string().min(1),
      actor_token: z.string().min(16),
      result: PhaseBResultSchema,
      runtime_actor: RuntimeActorSchema.optional()
    }),
    outputSchema: EnvelopeSchema,
    annotations: APPEND_ANNOTATIONS
  }, async input => guarded('submit_verifier_phase_b', async () => {
    const args = ['--id', input.orchestration_id, '--actor-token', input.actor_token, '--result', jsonArg(input.result)];
    if (input.runtime_actor) args.push('--runtime-actor', jsonArg(input.runtime_actor));
    return toolResponse('submit_verifier_phase_b', await orchestrate('phase-b-result', args), [], protocolMeta);
  }));

  server.registerTool('get_capture_status', {
    title: 'Get Knowledge Capture Status',
    description: 'Read the current stage/provenance of a knowledge-capture orchestration.',
    inputSchema: z.object({ orchestration_id: z.string().min(1) }),
    outputSchema: EnvelopeSchema,
    annotations: READ_ANNOTATIONS
  }, async input => guarded('get_capture_status', async () => toolResponse('get_capture_status', await orchestrate('status', ['--id', input.orchestration_id]), [], protocolMeta)));

  server.registerTool('record_runtime_result', {
    title: 'Record Runtime Verification',
    description: 'Append a machine/runtime PASS or FAIL to an existing trusted claim or legacy fact. It cannot promote an unverified candidate by itself.',
    inputSchema: z.object({
      subject_type: z.enum(['claim', 'legacy_fact']),
      subject_id: z.string().min(1),
      result: z.enum(['pass', 'fail']),
      test_id: z.string().min(1),
      runs: z.number().int().min(1).max(10000).default(1),
      model: z.string().default('runtime-harness'),
      session_id: z.string().optional(),
      target: TargetSchema.optional(),
      log_excerpt: z.string().optional(),
      metadata: z.record(z.string(), z.unknown()).optional()
    }),
    outputSchema: EnvelopeSchema,
    annotations: APPEND_ANNOTATIONS
  }, async input => guarded('record_runtime_result', async () => {
    const createArgs = ['--role', 'runtime', '--model', input.model];
    if (input.session_id) createArgs.push('--session-id', input.session_id);
    if (input.metadata) createArgs.push('--metadata', jsonArg(input.metadata));
    const run = await knowledge('run-create', createArgs);
    const runId = run?.run_id;
    if (!runId) throw new Error('Runtime provenance run was not created.');
    const args = ['--run-id', runId, '--subject-type', input.subject_type, '--subject-id', input.subject_id, '--result', input.result, '--test-id', input.test_id, '--runs', String(input.runs)];
    const target = compactTarget(input.target || {});
    if (target.client) args.push('--client', target.client);
    if (target.platform) args.push('--platform', target.platform);
    if (target.client_version) args.push('--client-version', target.client_version);
    if (target.sdk_version) args.push('--sdk-version', target.sdk_version);
    if (input.log_excerpt) args.push('--log-excerpt', input.log_excerpt);
    if (input.metadata) args.push('--metadata', jsonArg(input.metadata));
    const recorded = await knowledge('record-runtime', args);
    return toolResponse('record_runtime_result', { run, recorded }, [], protocolMeta);
  }));

  server.registerTool('doctor', {
    title: 'ExteraContext Doctor',
    description: 'Check immutable index coverage plus mutable knowledge-store state.',
    inputSchema: z.object({}),
    outputSchema: EnvelopeSchema,
    annotations: READ_ANNOTATIONS
  }, async () => guarded('doctor', async () => {
    const [index, mutable] = await Promise.all([query('doctor'), knowledge('doctor', [])]);
    return toolResponse('doctor', { index, mutable, mcp: { server_version: VERSION, server_target_revision: '2026-07-28', protocol_era: era, protocol_revision: era === 'modern' ? '2026-07-28' : null, dual_era: legacyAllowed } }, [], protocolMeta);
  }));

  return server;
}
