// Run only inside a fresh offline container after extracting the reviewed artifact.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, readdirSync, readlinkSync, chmodSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';

const root = `${process.env.HOME}/.local/share/exteracontext`;
const env = { HOME: process.env.HOME, PATH: process.env.PATH, LANG: 'C.UTF-8' };
const installer = '/work/extracted/install.py';
const run = (command, args) => execFileSync(command, args, { env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
const hash = path => createHash('sha256').update(readFileSync(path)).digest('hex');
const results = [];
const traces = [];
const record = (name, details) => { results.push({ name, pass: true, details }); console.log(`PASS ${name}: ${JSON.stringify(details)}`); };
const redact = value => JSON.parse(JSON.stringify(value, (key, val) => /token|capability/i.test(key) ? '[REDACTED]' : val));
const sdkRoot = '/work/extracted/payload/mcp/node_modules/@modelcontextprotocol/client/dist';
const { Client } = await import(pathToFileURL(`${sdkRoot}/index.mjs`));
const { StdioClientTransport } = await import(pathToFileURL(`${sdkRoot}/stdio.mjs`));
const base = `${root}/current/data/exteracontext.sqlite`;
const baseHash = hash('/work/extracted/payload/data/exteracontext.sqlite');
let evidenceKey;
async function session(label, capture = false) {
  const client = new Client({ name: 'local-artifact-acceptance', version: '1.0.0' },
    { versionNegotiation: { mode: { pin: '2026-07-28' } } });
  const args = capture ? [installer, '--start', '--modern-only'] : [`${root}/exteracontext`, '--modern-only'];
  const transport = new StdioClientTransport({ command: 'python3', args, env, cwd: '/work', stderr: 'pipe' });
  let stderr = '';
  transport.stderr?.on('data', data => { stderr += data; });
  try {
    await client.connect(transport);
    if (capture) record('fresh one-command default install/start', { root, release: readlinkSync(`${root}/current`) });
    assert.equal(client.getNegotiatedProtocolVersion(), '2026-07-28');
    const tools = (await client.listTools()).tools;
    for (const name of ['doctor', 'search_knowledge', 'find_api', 'get_evidence']) assert(tools.some(tool => tool.name === name));
    record(`${label} initialize/list`, { protocol: client.getNegotiatedProtocolVersion(), tools: tools.length });
    async function call(name, args) {
      const result = await client.callTool({ name, arguments: args });
      assert.notEqual(result.isError, true, JSON.stringify(result));
      assert.equal(result.structuredContent?.ok, true);
      traces.push({ label, name, result: redact(result.structuredContent) });
      return result.structuredContent.data;
    }
    const doctor = (await call('doctor', {})).index;
    assert.equal(doctor.corpus_verification.manifest_status, 'verified');
    assert.equal(doctor.db_sha256, baseHash);
    assert(Number(doctor.facts) > 2000 && Number(doctor.docs) >= 200, 'not the actual public corpus');
    record(`${label} doctor`, { facts: doctor.facts, docs: doctor.docs, sha256: doctor.db_sha256, verification: doctor.corpus_verification });
    const found = await call('find_api', { symbol: 'send_request', limit: 6 });
    assert(Array.isArray(found) && found.length > 0);
    const official = found.find(fact => fact.id === 'official-sdk:send-request');
    assert(official && official.evidence_url.startsWith('https://plugins.exteragram.app/'));
    evidenceKey = official.id;
    record(`${label} find_api`, { count: found.length, fact: official });
    const search = await call('search_knowledge', { query: 'send_request', limit: 6 });
    assert(JSON.stringify(search.context).includes('official-sdk:send-request'));
    record(`${label} search`, { officialFactFound: true });
    const evidence = await call('get_evidence', { key: evidenceKey, limit: 6 });
    assert(JSON.stringify(evidence).includes(official.evidence_url));
    record(`${label} evidence`, { key: evidenceKey, actualUrlFound: true });
    if (capture) {
      await call('reflect_on_task', { task: 'Local installer persistence acceptance: retain official send_request evidence', evidence: [{ evidence_status: 'docs', source: 'official-sdk', url: official.evidence_url, excerpt: official.claim }] });
      record('real MCP capture persisted', { runFiles: readdirSync(`${root}/state/runs`).length });
    }
  } catch (error) {
    throw new Error(`${label}: ${error.stack}\nserver stderr: ${stderr}`);
  } finally { await client.close(); }
}
try {
  await session('first', true);
  // SQLite sentinel is local user state only, never an evidence/corpus fallback.
  const overlay = `${root}/state/overlay.sqlite`;
  run('python3', ['-B', '-c', 'import sqlite3,sys; from pathlib import Path; sys.path.insert(0,sys.argv[2]); import knowledge_store as ks; ks.init_db(Path(sys.argv[1])); c=sqlite3.connect(sys.argv[1]); c.execute("CREATE TABLE install_acceptance_sentinel(value TEXT)"); c.execute("INSERT INTO install_acceptance_sentinel VALUES (?)", ("preserved",)); c.commit(); c.close()', overlay, `${root}/current/scripts`]);
  const stateHash = hash(overlay);
  const runNames = readdirSync(`${root}/state/runs`).sort();
  await session('restart');
  assert.equal(hash(overlay), stateHash);
  run('python3', [installer]);
  assert.equal(hash(overlay), stateHash);
  assert.deepEqual(readdirSync(`${root}/state/runs`).sort(), runNames);
  await session('reinstall');
  assert.equal(hash(overlay), stateHash);
  assert.equal(hash(base), baseHash);
  record('restart/reinstall preserve overlay and runs', { overlaySha256: stateHash, runNames, baseUnchanged: true });
  const oldRelease = readlinkSync(`${root}/current`);
  const payload = '/work/extracted/payload';
  const bundlePath = `${payload}/BUNDLE.json`;
  const bundleOriginal = readFileSync(bundlePath);
  const candidateManifest = `${payload}/data/.knowledge-manifest.json`;
  const candidateManifestOriginal = readFileSync(candidateManifest);
  const badBundle = JSON.parse(bundleOriginal);
  writeFileSync(candidateManifest, '{}');
  badBundle.files['data/.knowledge-manifest.json'] = hash(candidateManifest);
  writeFileSync(bundlePath, JSON.stringify(badBundle));
  assert.throws(() => run('python3', [installer]), /returned non-zero exit status/);
  assert.equal(readlinkSync(`${root}/current`), oldRelease);
  writeFileSync(candidateManifest, candidateManifestOriginal);
  writeFileSync(bundlePath, bundleOriginal);
  record('invalid candidate inner manifest refuses activation', { activeUnchanged: true });
  const updatedBundle = JSON.parse(bundleOriginal);
  writeFileSync(`${payload}/VERSION`, 'local-lifecycle-update-test\n');
  updatedBundle.files.VERSION = hash(`${payload}/VERSION`);
  writeFileSync(bundlePath, JSON.stringify(updatedBundle));
  run('python3', [installer]);
  assert.notEqual(readlinkSync(`${root}/current`), oldRelease);
  assert.equal(hash(overlay), stateHash);
  await session('update');
  run('python3', [`${root}/exteracontext`, '--rollback']);
  assert.equal(readlinkSync(`${root}/current`), oldRelease);
  assert.equal(hash(overlay), stateHash);
  await session('rollback');
  record('update/rollback preserve state and real retrieval', { active: oldRelease, overlaySha256: stateHash, baseUnchanged: hash(base) === baseHash });
  // Corrupted installed payload must fail closed even with manifest enforcement disabled in user env.
  const version = `${root}/current/VERSION`;
  const original = readFileSync(version);
  chmodSync(version, 0o600); writeFileSync(version, 'tampered');
  assert.throws(() => run('python3', [`${root}/exteracontext`, '--check']), /inventory\/hash mismatch/);
  writeFileSync(version, original); chmodSync(version, 0o444);
  const manifestPath = `${root}/current/data/.knowledge-manifest.json`;
  const manifestOriginal = readFileSync(manifestPath);
  chmodSync(manifestPath, 0o600); writeFileSync(manifestPath, '{}');
  assert.throws(() => run('python3', [`${root}/exteracontext`, '--check']), /inventory\/hash mismatch/);
  writeFileSync(manifestPath, manifestOriginal); chmodSync(manifestPath, 0o444);
  run('python3', [`${root}/exteracontext`, '--check']);
  record('strict runtime tamper rejection', { runtimeTamper: 'refused', corpusManifestTamper: 'refused' });
  run('python3', ['-c', 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); assert c.execute("SELECT value FROM install_acceptance_sentinel").fetchone()[0]=="preserved"; assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"; c.close()', overlay]);
  record('overlay integrity', { integrity: 'ok', sentinel: 'preserved' });
} finally {
  writeFileSync('/out/acceptance-results.json', JSON.stringify(results, null, 2));
  writeFileSync('/out/acceptance-trace.json', JSON.stringify(traces, null, 2));
}
