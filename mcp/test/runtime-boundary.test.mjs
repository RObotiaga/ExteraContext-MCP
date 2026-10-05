import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

// Exercise the actual registrations and callbacks without installing MCP/Zod or
// launching Python. This small schema double checks the limits under test.
const source = readFileSync(new URL('../src/server.mjs', import.meta.url), 'utf8');
const packageMetadata = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
function schema(kind, shape) {
  const checks = [];
  const s = {
    shape,
    min(n) { checks.push(v => (typeof v === 'number' ? v : v.length) >= n); return s; },
    max(n) { checks.push(v => (typeof v === 'number' ? v : v.length) <= n); return s; },
    int() { checks.push(v => Number.isInteger(v)); return s; },
    positive() { checks.push(v => v > 0); return s; },
    extend(extra) { return schema('object', {...shape,...extra}); },
    refine(fn) { checks.push(fn); return s; },
    regex(pattern) { checks.push(v => pattern.test(v)); return s; },
    optional() { const prior = s.parse; return { ...s, parse: v => v === undefined ? v : prior(v) }; },
    nullable() { const prior = s.parse; return { ...s, parse: v => v === null ? v : prior(v) }; },
    strict() { return s; },
    passthrough() { return s; },
    default() { return s; },
    parse(v) {
      if (kind === 'string') assert.equal(typeof v, 'string');
      if (kind === 'array') { assert.ok(Array.isArray(v)); for (const item of v) shape.parse(item); }
      if (kind === 'object') { assert.ok(v && typeof v === 'object'); for (const [key, field] of Object.entries(shape)) field.parse(v[key]); }
      if (kind === 'record') { assert.ok(v && typeof v === 'object'); for (const item of Object.values(v)) shape.parse(item); }
      for (const check of checks) assert.ok(check(v), `Rejected ${kind} limit`);
      return v;
    }
  };
  return s;
}
const z = {
  string: () => schema('string'),
  number: () => ({ ...schema('number'), int() { return this; } }),
  object: shape => schema('object', shape),
  array: element => schema('array', element),
  record: (_key, value) => schema('record', value),
  enum: () => schema('enum'),
  unknown: () => schema('unknown'),
  boolean: () => schema('boolean')
};
const tools = new Map();
let pythonCalls = 0;
const pythonInvocations = [];
class McpServer {
  registerTool(name, config, callback) { tools.set(name, { config, callback }); }
}
const executable = source.replace(/^import .*;\r?\n/gm, '').replaceAll('export function ', 'function ');
runInNewContext(`${executable}\n buildServer();`, {
  McpServer, z, Buffer, VERSION: packageMetadata.version,
  developmentTools: () => {}, // Development registrations are covered over the actual MCP wire.
  BridgeError: class extends Error {},
  compactTarget: value => value,
  jsonArg: JSON.stringify,
  targetLabel: () => '',
  runPythonJson: async (script, args, options) => {
    pythonCalls++;
    pythonInvocations.push({ script, args, options });
    return {};
  }
});

function runtimeInput(result) {
  return { subject_type: 'claim', subject_id: 'claim-1', result, test_id: 'test-1',
    runs: 1, model: 'trusted-looking-name', metadata: { attested: true }, log_excerpt: 'PASS' };
}

test('runtime PASS and FAIL are rejected before any Python provenance or write', async () => {
  const { callback, config } = tools.get('record_runtime_result');
  for (const result of ['pass', 'fail']) {
    const output = await callback(runtimeInput(result));
    assert.equal(output.isError, true);
    assert.equal(output.structuredContent.ok, false);
    assert.match(output.structuredContent.data.message, /disabled.*trusted runtime attestation/i);
    assert.equal(output.structuredContent.meta.tool, 'record_runtime_result');
    assert.equal(output.structuredContent.warnings.length, 0);
    assert.equal(JSON.parse(output.content[0].text).ok, false);
  }
  assert.equal(pythonCalls, 0);
  assert.ok(config.outputSchema.shape.ok, 'error response retains registered output envelope');
});

test('actor token schema accepts generated tokens and rejects oversized or malformed tokens pre-callback', () => {
  const actor = tools.get('submit_collector_result').config.inputSchema.shape.actor_token;
  actor.parse(`kc_col_${'A'.repeat(43)}`);
  actor.parse('A'.repeat(256)); // bounded alphabetic input reaches capability authorization
  assert.throws(() => actor.parse('A'.repeat(257)));
  assert.throws(() => actor.parse('token with spaces and +/'));
  assert.throws(() => actor.parse('🙂'.repeat(16)));
  assert.equal(pythonCalls, 0);
});

test('other tools retain functional callbacks and expected envelope', async () => {
  const output = await tools.get('resolve_target').callback({ target_text: 'ExteraGram Android Python' });
  assert.equal(output.structuredContent.ok, true);
  assert.equal(output.structuredContent.data.target.client, 'ExteraGram');
  assert.equal(output.structuredContent.meta.tool, 'resolve_target');

  // Verify submit_collector_result passes actor token via stdin and not in argv
  const colToken = `kc_col_${'A'.repeat(43)}`;
  pythonInvocations.length = 0;
  await tools.get('submit_collector_result').callback({
    orchestration_id: 'orch-1',
    actor_token: colToken,
    result: { action: 'skip', claim: 'c', kind: 'behavior', scope: 'target', target: {}, evidence_status: 'code', evidence_refs: [], existing_claim: null, conflicts_with: [] }
  });
  const colCall = pythonInvocations[pythonInvocations.length - 1];
  assert.equal(colCall.script, 'scripts/orchestrate.py');
  assert.ok(colCall.args.includes('--actor-token-stdin'), 'args must include --actor-token-stdin');
  assert.ok(!colCall.args.includes('--actor-token'), 'args must not include --actor-token');
  assert.ok(!colCall.args.includes(colToken), 'args must not include capability token');
  assert.equal(colCall.options?.stdin, colToken, 'capability token must be supplied via stdin option');

  // Verify submit_verifier_phase_a passes actor token via stdin and not in argv
  const verTokenA = `kc_ver_${'B'.repeat(43)}`;
  await tools.get('submit_verifier_phase_a').callback({
    orchestration_id: 'orch-1',
    actor_token: verTokenA,
    result: { statement: 's', scope: {}, evidence_status: 'code', uncertainty: null }
  });
  const phaseACall = pythonInvocations[pythonInvocations.length - 1];
  assert.ok(phaseACall.args.includes('--actor-token-stdin'));
  assert.ok(!phaseACall.args.includes('--actor-token'));
  assert.ok(!phaseACall.args.includes(verTokenA));
  assert.equal(phaseACall.options?.stdin, verTokenA);

  // Verify submit_verifier_phase_b passes actor token via stdin and not in argv
  const verTokenB = `kc_ver_${'C'.repeat(43)}`;
  await tools.get('submit_verifier_phase_b').callback({
    orchestration_id: 'orch-1',
    actor_token: verTokenB,
    result: { verdict: 'accept', final_statement: null, existing_subject_type: null, existing_subject_id: null, notes: null }
  });
  const phaseBCall = pythonInvocations[pythonInvocations.length - 1];
  assert.ok(phaseBCall.args.includes('--actor-token-stdin'));
  assert.ok(!phaseBCall.args.includes('--actor-token'));
  assert.ok(!phaseBCall.args.includes(verTokenB));
  assert.equal(phaseBCall.options?.stdin, verTokenB);
});

test('task, evidence excerpt, passthrough evidence, aggregate evidence, logs and metadata are bounded', () => {
  const reflect = tools.get('reflect_on_task').config.inputSchema.shape;
  const runtime = tools.get('record_runtime_result').config.inputSchema.shape;
  reflect.task.parse('x'.repeat(4096));
  assert.throws(() => reflect.task.parse('x'.repeat(4097)));
  const evidence = { excerpt: 'x'.repeat(4096) };
  reflect.evidence.parse([evidence]);
  assert.throws(() => reflect.evidence.parse([{ excerpt: 'x'.repeat(4097) }]));
  assert.throws(() => reflect.evidence.parse([{ excerpt: 'ok', arbitrary: 'x'.repeat(8192) }]));
  assert.throws(() => reflect.evidence.parse(Array(17).fill({ excerpt: 'x' })));
  // UTF-8 JSON length matters even where the JS string has few code units.
  assert.throws(() => reflect.evidence.parse(Array(16).fill({ excerpt: '🙂'.repeat(2048) })));
  runtime.log_excerpt.parse('x'.repeat(4096));
  assert.throws(() => runtime.log_excerpt.parse('x'.repeat(4097)));
  runtime.metadata.parse({ key: 'x'.repeat(8000) });
  assert.throws(() => runtime.metadata.parse({ key: '🙂'.repeat(2500) }));
});
