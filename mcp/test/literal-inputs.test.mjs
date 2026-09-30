import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { runPythonJson } from '../src/bridge.mjs';

const orchestration = 'scripts/orchestrate.py';
const evidence = JSON.stringify([{ evidence_type: 'note', evidence_status: 'docs', excerpt: 'harmless fixture' }]);

async function rejectsLiteralJson(args, secret) {
  await assert.rejects(runPythonJson(orchestration, args), error => {
    assert.doesNotMatch(String(error.message), new RegExp(secret));
    assert.doesNotMatch(String(error.stderr), new RegExp(secret));
    return true;
  });
}

test('bridge forces literal inputs despite parent and caller environment overrides', async () => {
  const previous = process.env.EXTERACONTEXT_LITERAL_INPUTS;
  process.env.EXTERACONTEXT_LITERAL_INPUTS = '0';
  try {
    for (const override of [undefined, { EXTERACONTEXT_LITERAL_INPUTS: '0' }]) {
      const result = await runPythonJson('-c', [
        'import json, os; print(json.dumps(os.environ.get("EXTERACONTEXT_LITERAL_INPUTS")))'
      ], override ? { env: override } : {});
      assert.equal(result, '1');
    }
  } finally {
    if (previous === undefined) delete process.env.EXTERACONTEXT_LITERAL_INPUTS;
    else process.env.EXTERACONTEXT_LITERAL_INPUTS = previous;
  }
});

test('MCP orchestration treats @task literally and never reads @evidence, @target or @result', { timeout: 30_000 }, async () => {
  const temp = await mkdtemp(join(tmpdir(), 'exteracontext-literal-'));
  const runRoot = join(temp, 'runs');
  const secret = 'HARMLESS_SECRET_FIXTURE_73924';
  const secretFile = join(temp, 'secret.txt');
  const jsonFile = join(temp, 'secret.json');
  const resultFile = join(temp, 'result.json');
  const task = `@${secretFile}`;
  try {
    await writeFile(secretFile, secret, 'utf8');
    await writeFile(jsonFile, JSON.stringify([{ evidence_type: 'note', excerpt: secret }]), 'utf8');
    await writeFile(resultFile, JSON.stringify({ action: 'skip', claim: secret, kind: 'behavior', scope: 'project', target: {}, evidence_status: 'docs', evidence_refs: [], existing_claim: null, conflicts_with: [] }), 'utf8');

    const started = await runPythonJson(orchestration, [
      '--run-root', runRoot, 'reflect', '--task', task, '--evidence', evidence
    ], { env: { EXTERACONTEXT_LITERAL_INPUTS: '0' } });
    const statusArgs = ['--run-root', runRoot, 'status', '--id', started.orchestration_id];
    const status = await runPythonJson(orchestration, statusArgs);
    const state = JSON.parse(await readFile(join(started.dir, 'state.json'), 'utf8'));
    assert.equal(state.task, task);
    assert.doesNotMatch(JSON.stringify(status), /HARMLESS_SECRET_FIXTURE_73924/);
    assert.equal(status.stage, 'collector-ready');

    await rejectsLiteralJson(['--run-root', runRoot, 'reflect', '--task', 'ordinary task', '--evidence', `@${jsonFile}`], secret);
    await rejectsLiteralJson(['--run-root', runRoot, 'reflect', '--task', 'ordinary task', '--evidence', evidence, '--target', `@${jsonFile}`], secret);
    await rejectsLiteralJson(['--run-root', runRoot, 'collector-result', '--id', started.orchestration_id,
      '--actor-token', started.collector_token, '--result', `@${resultFile}`], secret);
    const unchanged = await runPythonJson(orchestration, statusArgs);
    assert.equal(unchanged.stage, 'collector-ready');
    assert.doesNotMatch(JSON.stringify(unchanged), /HARMLESS_SECRET_FIXTURE_73924/);
    assert.equal(await readFile(secretFile, 'utf8'), secret);
  } finally {
    await rm(temp, { recursive: true, force: true });
  }
});
