import { McpServer } from '@modelcontextprotocol/server';
import * as z from 'zod/v4';
import { BridgeError, compactTarget, jsonArg, runPythonJson, targetLabel } from './bridge.mjs';
import { VERSION } from './version.mjs';
import { assessEvidence, developmentTools } from './development.mjs';
import { targetValueRelation } from './target-values.mjs';

// Keep JSON carried in a single Python argv bounded well below the bridge's
// 256 KiB total-argument ceiling. Measure bytes, not JS UTF-16 code units.
function jsonWithinBytes(value, limit) {
  return Buffer.byteLength(JSON.stringify(value), 'utf8') <= limit;
}

// issue_actor() emits `kc_col_`/`kc_ver_` plus secrets.token_urlsafe(32),
// whose alphabet is exactly base64url and whose length is 43 characters.
// The bounded superset preserves that format while rejecting oversized/non-token input pre-queue.
const ActorTokenSchema = z.string().min(16).max(256)
  .regex(/^[A-Za-z0-9_-]{16,256}$/)
  .refine(token => Buffer.byteLength(token, 'utf8') <= 256, 'Actor token exceeds 256 UTF-8 bytes');

const TargetSchema = z.object({
  client: z.string().nullable().optional(),
  platform: z.string().nullable().optional(),
  client_version: z.string().nullable().optional(),
  sdk_version: z.string().nullable().optional(),
  language: z.string().nullable().optional(),
  plugin_format: z.string().nullable().optional()
}).strict();

const DevelopmentTargetSchema = TargetSchema.extend({
  package: z.string().regex(/^[A-Za-z][\w]*(?:\.[A-Za-z][\w]*)+$/).optional(),
  version_code: z.number().int().positive().optional(),
  android_api: z.number().int().min(1).max(100).optional(),
  apk_sha256: z.string().regex(/^[a-f0-9]{64}$/).optional(),
  abi: z.enum(['arm64-v8a','armeabi-v7a','x86','x86_64','universal']).optional(),
  sdk_origin: z.enum(['runtime','manifest','user-report','unknown']).optional(),
  variant: z.enum(['full','lite','unknown']).optional()
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
  excerpt: z.string().max(4096).nullable().optional()
}).passthrough().refine(value => jsonWithinBytes(value, 8192), 'Evidence item exceeds 8192 UTF-8 JSON bytes');

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

// Only an explicitly labelled, exact version is evidence. Never reuse the first
// number in a sentence (notably an SDK version) as the client version.
function labelledVersion(text, labels, maxComponents) {
  const pattern = new RegExp(`\\b(${labels})\\s+(?:(version)\\s+)?(?=([^\\s,;]+))`, 'ig');
  const versions = new Set();
  let invalid = false;
  const exact = new RegExp(`^\\d+(?:\\.\\d+){1,${maxComponents - 1}}$`);
  for (const match of text.matchAll(pattern)) {
    const candidate = match[3].replace(/^v(?=\d)/i, '');
    if (exact.test(candidate)) versions.add(candidate);
    // Named clients also precede descriptions such as "ExteraGram Android".
    // Explicit version labels, ranges and malformed numeric versions are not
    // descriptions: they make any alternative exact version ambiguous.
    else if (match[2] || !/^(exteragram|ayugram)$/i.test(match[1]) || /^[<>~=^]*v?\d/i.test(candidate)) invalid = true;
  }
  return !invalid && versions.size === 1 ? [...versions][0] : undefined;
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
    const version = labelledVersion(text, 'client|app|exteragram|ayugram', 4);
    if (version) target.client_version = version;
  }
  if (!target.sdk_version) {
    const version = labelledVersion(text, 'sdk|pysdk', 5);
    if (version) target.sdk_version = version;
  }
  const unknown = ['client', 'platform', 'client_version', 'sdk_version', 'language', 'plugin_format'].filter(k => !target[k]);
  return { target, unknown_fields: unknown };
}

// Query.py returns ranked LIKE matches, not compatibility decisions. All four dimensions
// below must be established by structured fact fields, never by ranking or claim substring.
function exactVersionField(fact, label, requested) {
  const version = String(fact.version || '');
  const prefix = label === 'client' ? '(?:client|app)' : 'sdk';
  const pattern = new RegExp(`(?:^|[;,]\\s*)${prefix}\\s+(\\d+(?:\\.\\d+)+)(?=\\s*(?:[;,]|$))`, 'ig');
  const values = [...version.matchAll(pattern)].map(match => match[1]);
  const mentions = [...version.matchAll(new RegExp(`\\b${prefix}\\b`, 'ig'))];
  // A fact scoped to an SDK cannot establish support for an unknown SDK (and
  // vice versa for a client version). Unlabelled or range bounds are not exact.
  if (!requested) return mentions.length === 0;
  if (!/^\d+(?:\.\d+)+$/.test(requested)) return false;
  return mentions.length === 1 && values.length === 1 && values[0] === requested;
}

function exactSymbol(api, symbol) {
  if (!api || !symbol || !/^[\w.$]+$/.test(symbol)) return false;
  // API lists in the immutable index are comma/semicolon separated signatures.
  // A name mentioned only in a claim, or a prefix of another name, is insufficient.
  return String(api).split(/[,;]/).some(entry => {
    const name = entry.trim().match(/^([\w.$]+)(?=\s*(?:\(|$))/)?.[1];
    return name === symbol;
  });
}

function explicitTarget(fact, target) {
  if (!target.client || !target.platform) return false;
  const client = String(fact.client || '').trim();
  const source = String(fact.source_id || '').toLowerCase();
  // A generic 'official' label alone does not identify which client it covers.
  const sourcedClient = source.startsWith('exteragram-') ? 'ExteraGram' : '';
  if ((client || sourcedClient).toLowerCase() !== target.client.toLowerCase()) return false;
  return String(fact.platform || '').trim().toLowerCase() === target.platform.toLowerCase();
}

function assertionPolarity(fact, symbol) {
  if (!['code', 'docs', 'runtime-verified'].includes(fact.status)) return null;
  if (fact.knowledge_state && fact.knowledge_state !== 'verified') return null;
  if (fact.review_status && ['conflicting', 'rejected', 'candidate'].includes(fact.review_status)) return null;
  const claim = String(fact.claim || '').trim();
  const escaped = symbol.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  if (!new RegExp(`(^|[^\\w.$])${escaped}(?![\\w.$])`, 'i').test(claim)) return null;
  // Absence of search results (or a negative-evidence topic) is NOT incompatibility.
  const negativePattern = /(?<![\p{L}\p{N}_])(?:not\s+supported|unsupported|unavailable|removed|does\s+not\s+exist|incompatible|не\s+поддерживается|недоступен|недоступна|удалён|удален|несовместим)(?![\p{L}\p{N}_])/giu;
  const negative = negativePattern.test(claim);
  const positive = /(?<![\p{L}\p{N}_])(?:supported|supports|available|implemented|compatible|works|поддерживается|доступен|доступна|реализован|совместим)(?![\p{L}\p{N}_])/iu.test(claim.replace(negativePattern, ''));
  return negative === positive ? null : negative ? 'incompatible' : 'compatible';
}

export function compatibilityFromFacts(facts, target, symbol) {
  const relevant = facts.filter(f =>
    (['official', 'target-ecosystem'].includes(f.directness) || (f.directness === 'donor' && String(target.client).toLowerCase()==='ayugram' && String(f.source_id || '').toLowerCase().startsWith('ayugram-') && explicitTarget(f,target))) &&
    explicitTarget(f, target) && exactSymbol(f.api, symbol) &&
    (target.client_version || target.sdk_version) &&
    exactVersionField(f, 'client', target.client_version) &&
    exactVersionField(f, 'sdk', target.sdk_version) &&
    ['package','version_code','apk_sha256','android_api','abi','variant'].every(k => ['not-requested','match'].includes(targetValueRelation(f[k],target[k])))
  );
  const assertions = relevant.map(f => ({ fact: f, verdict: assertionPolarity(f, symbol) })).filter(x => x.verdict);
  const verdicts = new Set(assertions.map(x => x.verdict));
  if (verdicts.size === 1) {
    const verdict = assertions[0].verdict;
    return { verdict, confidence: 'evidence-supported', reason: 'Explicit API assertion for exact target, platform and requested version dimensions.', evidence: assertions.map(x => x.fact).slice(0, 8) };
  }
  return { verdict: 'unknown', confidence: 'conservative', reason: verdicts.size > 1 ? 'Conflicting exact-target API assertions.' : 'No explicit exact-target API assertion establishes compatibility or incompatibility.', evidence: facts.slice(0, 8) };
}

async function query(command, value, options = {}) {
  const args = [`scripts/query.py`, command];
  if (value !== undefined) args.push(value);
  if (options.target) args.push('--target', options.target);
  if (options.clientVersion) args.push('--client-version', options.clientVersion);
  if (options.sdkVersion) args.push('--sdk-version', options.sdkVersion);
  if (options.targetJson) args.push('--target-json', JSON.stringify(options.targetJson));
  if (options.mode) args.push('--mode', options.mode);
  if (options.limit !== undefined) args.push('--limit', String(options.limit));
  if (command !== 'doctor') args.push('--format', 'json');
  return runPythonJson(args.shift(), args);
}

async function orchestrate(command, args, options = {}) {
  return runPythonJson('scripts/orchestrate.py', [command, ...args], options);
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
      target: DevelopmentTargetSchema.optional()
    }),
    outputSchema: EnvelopeSchema,
    annotations: READ_ANNOTATIONS
  }, async input => guarded('resolve_target', async () => toolResponse('resolve_target', normalizeTarget(input), [], protocolMeta)));

  server.registerTool('search_knowledge', {
    title: 'Search ExteraContext Knowledge',
    description: 'Build a task-specific evidence packet. Keep each query scoped to one technical concept for best retrieval quality.',
    inputSchema: z.object({
      query: z.string().min(2),
      target: DevelopmentTargetSchema.optional(),
      target_text: z.string().optional(),
      limit: z.number().int().min(1).max(30).default(10)
    }),
    outputSchema: EnvelopeSchema,
    annotations: READ_ANNOTATIONS
  }, async input => guarded('search_knowledge', async () => {
    const resolved = normalizeTarget(input);
    if (input.target || input.target_text) {
      const retrieved=await query('target-lookup',input.query,{limit:input.limit,targetJson:resolved.target,mode:'context'});
      return toolResponse('search_knowledge',{resolved_target:resolved,context:retrieved.context,retrieval:retrieved,assessment:assessEvidence(retrieved.assessment_facts,resolved.target,input.query)},retrieved.context.warnings,protocolMeta);
    }
    const data = await query('context', input.query, {
      target: targetLabel(resolved.target),
      clientVersion: resolved.target.client_version,
      sdkVersion: resolved.target.sdk_version,
      limit: input.limit
    });
    const warnings = [];
    if (resolved.unknown_fields.includes('client_version')) warnings.push('Client version is unknown; version compatibility is not established.');
    if (resolved.unknown_fields.includes('sdk_version')) warnings.push('SDK version is unknown; SDK compatibility is not established.');
    return toolResponse('search_knowledge', { resolved_target: resolved, context: data, assessment: assessEvidence(data.facts || [], resolved.target) }, warnings, protocolMeta);
  }));

  server.registerTool('find_api', {
    title: 'Find Plugin API',
    description: 'Look up a concrete ExteraGram/AyuGram API symbol and its evidence.',
    inputSchema: z.object({ symbol: z.string().min(1), target: DevelopmentTargetSchema.optional(), limit: z.number().int().min(1).max(30).default(12) }),
    outputSchema: EnvelopeSchema,
    annotations: READ_ANNOTATIONS
  }, async input => guarded('find_api', async () => {
    if (!input.target) return toolResponse('find_api', await query('api',input.symbol,{limit:input.limit}), ['No target supplied; these results do not establish target compatibility.'], protocolMeta);
    const resolved = normalizeTarget(input);
    const retrieved=await query('target-lookup',input.symbol,{limit:input.limit,targetJson:resolved.target,mode:'api'});
    return toolResponse('find_api', {resolved_target:resolved, facts:retrieved.items, retrieval:retrieved, assessment:assessEvidence(retrieved.assessment_facts,resolved.target,input.symbol)}, [], protocolMeta);
  }));

  server.registerTool('find_usage', {
    title: 'Find Existing API Usage',
    description: 'Find source-backed usages/examples for an API or behavior. Results are ranked evidence, not a guarantee of target compatibility.',
    inputSchema: z.object({ query: z.string().min(1), target: DevelopmentTargetSchema.optional(), limit: z.number().int().min(1).max(30).default(12) }),
    outputSchema: EnvelopeSchema,
    annotations: READ_ANNOTATIONS
  }, async input => guarded('find_usage', async () => {
    if (input.target) {
      const resolved=normalizeTarget(input);
      const retrieved=await query('target-lookup',input.query,{limit:input.limit,targetJson:resolved.target,mode:'usage'});
      const usages=retrieved.assessment_facts.filter(item=>['code','runtime-verified'].includes(item.status) || item.directness==='target-ecosystem').slice(0,input.limit);
      return toolResponse('find_usage',{usages,searched:retrieved.candidate_count,resolved_target:resolved,retrieval:retrieved,assessment:assessEvidence(retrieved.assessment_facts,resolved.target,input.query)},[],protocolMeta);
    }
    const all = await query('search', input.query, { limit: Math.min(input.limit * 3, 60) });
    const list = Array.isArray(all) ? all : [];
    const usages = list.filter(item => ['code', 'runtime-verified'].includes(item.status) || item.directness === 'target-ecosystem').slice(0, input.limit);
    const resolved = normalizeTarget(input);
    return toolResponse('find_usage', { usages, searched: list.length, resolved_target:resolved, assessment:assessEvidence(usages,resolved.target) }, usages.length ? [] : ['No code/runtime usage was found; do not infer that the API is supported.'], protocolMeta);
  }));

  server.registerTool('get_recipe', {
    title: 'Get Development Recipe',
    description: 'Retrieve task recipes such as hook lifecycle cleanup, account routing, UI-thread work, or packaging.',
    inputSchema: z.object({ query: z.string().min(1), target: DevelopmentTargetSchema.optional(), limit: z.number().int().min(1).max(20).default(8) }),
    outputSchema: EnvelopeSchema,
    annotations: READ_ANNOTATIONS
  }, async input => guarded('get_recipe', async () => {
    if (!input.target) return toolResponse('get_recipe',await query('recipe',input.query,{limit:input.limit}),['Recipes are references, not target runtime verification.'],protocolMeta);
    const resolved=normalizeTarget(input);
    const retrieved=await query('target-lookup',input.query,{limit:input.limit,targetJson:resolved.target,mode:'recipe'});
    return toolResponse('get_recipe',{recipes:retrieved.items,resolved_target:resolved,retrieval:retrieved,assessment:assessEvidence(retrieved.assessment_facts,resolved.target,input.query)},[],protocolMeta);
  }));

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
      target: DevelopmentTargetSchema,
      limit: z.number().int().min(1).max(30).default(16)
    }),
    outputSchema: EnvelopeSchema,
    annotations: READ_ANNOTATIONS
  }, async input => guarded('check_compatibility', async () => {
    const retrieved=await query('target-lookup',input.symbol,{limit:input.limit,targetJson:compactTarget(input.target),mode:'api'});
    const list = retrieved.assessment_facts || [];
    const result = compatibilityFromFacts(list, compactTarget(input.target), input.symbol);
    if (assessEvidence(list,compactTarget(input.target),input.symbol).compatibility==='conflict' || retrieved.candidate_limit_hit) {
      result.verdict='unknown';
      result.reason='Conflicting or incomplete retrieved evidence requires further checks.';
    }
    const warnings = result.verdict === 'unknown' ? ['Unknown means evidence is insufficient; it is not a compatibility failure.'] : [];
    return toolResponse('check_compatibility', { symbol: input.symbol, target: compactTarget(input.target), ...result }, warnings, protocolMeta);
  }));

  server.registerTool('reflect_on_task', {
    title: 'Start Knowledge Capture',
    description: 'Start the two-subagent write-back protocol from original evidence and produce a collector prompt/schema. This only creates a candidate workflow; it does not trust new knowledge.',
    inputSchema: z.object({
      task: z.string().min(1).max(4096),
      task_id: z.string().max(256).optional(),
      discovery: z.string().max(8192).optional(),
      target: TargetSchema.optional(),
      evidence: z.array(EvidenceSchema).min(1).max(16).refine(value => jsonWithinBytes(value, 131072), 'Evidence exceeds 131072 UTF-8 JSON bytes')
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
      actor_token: ActorTokenSchema,
      result: CollectorResultSchema,
      model: z.string().optional(),
      runtime_actor: RuntimeActorSchema.optional()
    }),
    outputSchema: EnvelopeSchema,
    annotations: APPEND_ANNOTATIONS
  }, async input => guarded('submit_collector_result', async () => {
    const args = ['--id', input.orchestration_id, '--actor-token-stdin', '--result', jsonArg(input.result)];
    if (input.model) args.push('--model', input.model);
    if (input.runtime_actor) args.push('--runtime-actor', jsonArg(input.runtime_actor));
    return toolResponse('submit_collector_result', await orchestrate('collector-result', args, { stdin: input.actor_token }), [], protocolMeta);
  }));

  server.registerTool('submit_verifier_phase_a', {
    title: 'Submit Blind Verifier Phase A',
    description: 'Persist the independent blind extraction with the MCP-issued verifier capability token. The collector candidate is revealed only after this transition.',
    inputSchema: z.object({
      orchestration_id: z.string().min(1),
      actor_token: ActorTokenSchema,
      result: PhaseAResultSchema,
      model: z.string().optional(),
      runtime_actor: RuntimeActorSchema.optional()
    }),
    outputSchema: EnvelopeSchema,
    annotations: APPEND_ANNOTATIONS
  }, async input => guarded('submit_verifier_phase_a', async () => {
    const args = ['--id', input.orchestration_id, '--actor-token-stdin', '--result', jsonArg(input.result)];
    if (input.model) args.push('--model', input.model);
    if (input.runtime_actor) args.push('--runtime-actor', jsonArg(input.runtime_actor));
    return toolResponse('submit_verifier_phase_a', await orchestrate('phase-a-result', args, { stdin: input.actor_token }), [], protocolMeta);
  }));

  server.registerTool('submit_verifier_phase_b', {
    title: 'Submit Verifier Phase B',
    description: 'Submit the comparison verdict using the same verifier capability token used for Phase A. If runtime child identity was supplied in Phase A, matching identity is required here. SQLite guards remain the final promotion gate.',
    inputSchema: z.object({
      orchestration_id: z.string().min(1),
      actor_token: ActorTokenSchema,
      result: PhaseBResultSchema,
      runtime_actor: RuntimeActorSchema.optional()
    }),
    outputSchema: EnvelopeSchema,
    annotations: APPEND_ANNOTATIONS
  }, async input => guarded('submit_verifier_phase_b', async () => {
    const args = ['--id', input.orchestration_id, '--actor-token-stdin', '--result', jsonArg(input.result)];
    if (input.runtime_actor) args.push('--runtime-actor', jsonArg(input.runtime_actor));
    return toolResponse('submit_verifier_phase_b', await orchestrate('phase-b-result', args, { stdin: input.actor_token }), [], protocolMeta);
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
    description: 'Disabled until trusted runtime attestation is integrated. Caller-provided PASS or FAIL cannot establish runtime verification.',
    inputSchema: z.object({
      subject_type: z.enum(['claim', 'legacy_fact']),
      subject_id: z.string().min(1),
      result: z.enum(['pass', 'fail']),
      test_id: z.string().min(1),
      runs: z.number().int().min(1).max(10000).default(1),
      model: z.string().default('runtime-harness'),
      session_id: z.string().optional(),
      target: TargetSchema.optional(),
      log_excerpt: z.string().max(4096).optional(),
      metadata: z.record(z.string(), z.unknown()).refine(value => jsonWithinBytes(value, 8192), 'Metadata exceeds 8192 UTF-8 JSON bytes').optional()
    }),
    outputSchema: EnvelopeSchema,
    annotations: APPEND_ANNOTATIONS
  }, async () => guarded('record_runtime_result', async () => {
    // A caller-controlled result, model, log or metadata is not a runtime attestation.
    // Reject both PASS and FAIL before creating a run or mutating the knowledge store.
    throw new Error('Runtime result recording is disabled: trusted runtime attestation is not integrated; caller-provided PASS/FAIL is not accepted.');
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

  developmentTools(server, {z, targetSchema:DevelopmentTargetSchema, outputSchema:EnvelopeSchema, readAnnotations:READ_ANNOTATIONS, toolResponse, guarded, protocolMeta});
  return server;
}
