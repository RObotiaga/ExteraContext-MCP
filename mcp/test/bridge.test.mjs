import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { runPythonJson } from '../src/bridge.mjs';

test('Python bridge reaches ExteraContext core', { timeout: 30_000 }, async () => {
  const doctor = await runPythonJson('scripts/query.py', ['doctor']);
  assert.ok(Number(doctor?.facts) >= 2000, JSON.stringify(doctor));
  assert.ok(Number(doctor?.docs) >= 100, JSON.stringify(doctor));
});

test('Python bridge retrieves a known API', { timeout: 30_000 }, async () => {
  const result = await runPythonJson('scripts/query.py', ['api', 'send_request', '--limit', '6', '--format', 'json']);
  assert.ok(Array.isArray(result));
  assert.ok(result.some(item => /send_request/i.test(JSON.stringify(item))), JSON.stringify(result).slice(0, 1500));
});


test('Python bridge preserves UTF-8 regardless of host code page', { timeout: 30_000 }, async () => {
  const runRoot = await mkdtemp(join(tmpdir(), 'exteracontext-utf8-'));
  const text = 'Проверка → русский текст ✓';
  try {
    const started = await runPythonJson('scripts/orchestrate.py', [
      '--run-root', runRoot, 'reflect',
      '--task', text,
      '--evidence', JSON.stringify([{ evidence_type: 'note', evidence_status: 'docs', excerpt: text }])
    ], { env: { PYTHONUTF8: '0', PYTHONIOENCODING: 'cp1251' } });
    const status = await runPythonJson('scripts/orchestrate.py', [
      '--run-root', runRoot, 'status', '--id', started.orchestration_id
    ]);
    assert.equal(status.task, text);
  } finally {
    await rm(runRoot, { recursive: true, force: true });
  }
});
