import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { runPython, runPythonJson, ROOT } from '../src/bridge.mjs';

const db = process.env.EXTERACONTEXT_DB || join(ROOT, 'data', 'exteracontext.sqlite');
let hasOfflineDb = true;
try { await access(db); } catch { hasOfflineDb = false; }

test('Python bridge reaches ExteraContext core', { timeout: 30_000, skip: !hasOfflineDb && 'Offline base DB is absent' }, async () => {
  const doctor = await runPythonJson('scripts/query.py', ['doctor']);
  assert.ok(Number(doctor?.facts) >= 2000, JSON.stringify(doctor));
  assert.ok(Number(doctor?.docs) >= 100, JSON.stringify(doctor));
});

test('Python bridge retrieves a known API', { timeout: 30_000, skip: !hasOfflineDb && 'Offline base DB is absent' }, async () => {
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
    assert.deepEqual(status, { orchestration_id: started.orchestration_id, stage: 'collector-ready' });
    const { stdout: collectorPrompt } = await runPython('scripts/orchestrate.py', [
      '--run-root', runRoot, 'prompt', '--id', started.orchestration_id, '--phase', 'collector'
    ], { env: { PYTHONUTF8: '0', PYTHONIOENCODING: 'cp1251' } });
    assert.ok(collectorPrompt.includes(text), 'collector prompt must preserve Unicode task and evidence');
  } finally {
    await rm(runRoot, { recursive: true, force: true });
  }
});

test('rejects oversized argv without starting Python', async () => {
  await assert.rejects(runPython('-c', ['x'.repeat(256 * 1024)]), /arguments exceed size limit/);
  await assert.rejects(runPython('-c', ['print(1)'], { timeoutMs: Infinity }), /Invalid Python bridge timeout/);
});

test('bounds stdin by UTF-8 bytes before queueing/spawning and never echoes oversized secrets', async () => {
  const exactLimit = '🙂'.repeat(16_384); // exactly 64 KiB in UTF-8, but fewer JS characters
  const exact = await runPython('-c', ['import sys; print(len(sys.stdin.readline().encode("utf-8")) - 1)'], { stdin: exactLimit });
  assert.equal(Number(exact.stdout.trim()), Buffer.byteLength(exactLimit, 'utf8'));

  const oversizedSecret = 's'.repeat(64 * 1024 + 1);
  await assert.rejects(
    runPython('this-python-script-must-not-be-spawned.py', [], { stdin: oversizedSecret }),
    error => {
      assert.match(error.message, /stdin exceeds 65536 byte limit/);
      assert.ok(!String(error.message).includes(oversizedSecret));
      assert.ok(!JSON.stringify(error.command ?? []).includes(oversizedSecret));
      return true;
    }
  );
  await assert.rejects(runPython('-c', ['pass'], { stdin: '🙂'.repeat(17_000) }), /stdin exceeds 65536 byte limit/);
  await assert.rejects(runPython('-c', ['pass'], { stdin: Buffer.from('not a string') }), /stdin must be a string/);
});

test('bounds stdout and stderr separately, terminating noisy subprocesses', { timeout: 20_000 }, async () => {
  for (const stream of ['stdout', 'stderr']) {
    const code = `import sys; sys.${stream}.write('x' * 3000000); sys.${stream}.flush()`;
    await assert.rejects(runPython('-c', [code]), /output exceeds size limit/);
  }
});

test('times out active subprocesses and supports cancellation of queued work', { timeout: 20_000 }, async () => {
  await assert.rejects(runPython('-c', ['import time; time.sleep(5)'], { timeoutMs: 200 }), /timed out/);
  const running = Array.from({ length: 4 }, () => runPython('-c', ['import time; time.sleep(0.7)']));
  const controller = new AbortController();
  const queued = runPython('-c', ['print("should not run")'], { signal: controller.signal });
  controller.abort();
  await assert.rejects(queued, /cancelled/);
  await Promise.all(running);
  const activeController = new AbortController();
  const active = runPython('-c', ['import time; time.sleep(5)'], { signal: activeController.signal });
  activeController.abort();
  await assert.rejects(active, /cancelled/);
});

test('redacts capability tokens from bridge result and error commands', async () => {
  const token = 'secret-never-in-command-0123456789';
  const outcome = await runPython('-c', ['print("ok")', '--actor-token', token]);
  assert.ok(!JSON.stringify(outcome.command).includes(token));
  await assert.rejects(runPython('-c', ['import sys; sys.exit(7)', '--actor-token', token]), error => {
    assert.ok(!JSON.stringify(error.command).includes(token));
    return true;
  });

  // Even a successful child must not echo an argv capability into returned output.
  const argvEcho = await runPython('-c', [
    'import sys; print(sys.argv[-1]); print(sys.argv[-1], file=sys.stderr)',
    '--actor-token', token
  ]);
  assert.equal(argvEcho.stdout.trim(), '[REDACTED]');
  assert.equal(argvEcho.stderr.trim(), '[REDACTED]');
  assert.ok(!JSON.stringify(argvEcho).includes(token));

  // Capability token passed via stdin is received by subprocess and never present in argv or command.
  const stdinSecret = 'secret-via-stdin-9876543210';
  const stdinOutcome = await runPython('-c', ['import sys; print("token=" + sys.stdin.readline().strip())'], { stdin: stdinSecret });
  assert.equal(stdinOutcome.stdout.trim(), 'token=[REDACTED]');
  assert.equal(stdinOutcome.stderr, '');
  assert.ok(!JSON.stringify(stdinOutcome).includes(stdinSecret));
  assert.ok(!JSON.stringify(stdinOutcome.command).includes(stdinSecret));
  assert.ok(!stdinOutcome.command.some(arg => arg.includes(stdinSecret)));
});
