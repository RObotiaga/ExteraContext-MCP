import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { targetValueRelation } from '../src/target-values.mjs';

// Exercise the production implementation without requiring optional MCP SDK packages.
// The checkout can run this regression suite before dependencies are installed.
const source = readFileSync(new URL('../src/server.mjs', import.meta.url), 'utf8');
const start = source.indexOf('function exactVersionField(');
const end = source.indexOf('\nasync function query(', start);
assert.ok(start >= 0 && end > start, 'compatibility implementation must be present');
const compatibilityFromFacts = runInNewContext(
  `${source.slice(start, end).replace('export function compatibilityFromFacts', 'function compatibilityFromFacts')}\ncompatibilityFromFacts;`, { targetValueRelation }
);

const target = { client: 'ExteraGram', platform: 'Android', client_version: '12.5.1', sdk_version: '1.4.5.3' };
const fact = {
  api: 'send_request(account, request)', claim: 'send_request is supported on this exact build.',
  client: 'ExteraGram', platform: 'Android', version: 'client 12.5.1; SDK 1.4.5.3',
  status: 'docs', directness: 'official', source_id: 'exteragram-docs'
};
const check = (facts, t = target, symbol = 'send_request') => compatibilityFromFacts(facts, t, symbol).verdict;

test('identity fields compare equivalent scalar representations and preserve unknown',()=>{
  const t={...target,package:'com.example.client',version_code:70079,android_api:36,apk_sha256:'a'.repeat(64),abi:'arm64-v8a'};
  const f={...fact,...t,version_code:'70079',android_api:'36',apk_sha256:'A'.repeat(64),abi:'ARM64-V8A'};
  assert.equal(check([f],t),'compatible');
  assert.equal(check([{...f,version_code:'70080'}],t),'unknown');
  assert.equal(check([{...f,apk_sha256:undefined}],t),'unknown');
});

test('Russian availability assertions respect Unicode token boundaries',()=>{
  for (const claim of ['send_request поддерживается.','send_request доступен!']) assert.equal(check([{...fact,claim}]),'compatible');
  for (const claim of ['send_request не поддерживается.','send_request недоступен!']) assert.equal(check([{...fact,claim}]),'incompatible');
  assert.equal(check([{...fact,claim:'send_request суперподдерживается'}]),'unknown');
});

test('separate negation before availability words cannot imply compatibility',()=>{
  for (const suffix of ['не доступен','не доступна','не реализован','не совместим','not available','not implemented','not compatible']) {
    assert.equal(check([{...fact,claim:`send_request ${suffix}.`}]),'incompatible',suffix);
  }
  assert.equal(check([{...fact,claim:'send_request доступен, но не совместим.'}]),'unknown');
});

test('requested APK identity cannot be inferred from a matching version; exact AyuGram evidence is eligible',()=>{
  const exact={...target,package:'com.exteragram.messenger',version_code:70079,apk_sha256:'a'.repeat(64)};
  assert.equal(check([fact],exact),'unknown');
  assert.equal(check([{...fact,...exact}],exact),'compatible');
  const ayu={...target,client:'AyuGram'};
  assert.equal(check([{...fact,client:'AyuGram',directness:'donor',source_id:'ayugram-code'}],ayu),'compatible');
});

test('requires explicit relevant API assertion and exact client plus SDK versions', () => {
  assert.equal(check([fact]), 'compatible');
  assert.equal(check([{ ...fact, claim: 'send_request is not supported on this exact build.' }]), 'incompatible');
  assert.equal(check([{ ...fact, version: 'client 12.5.10; SDK 1.4.5.3' }]), 'unknown');
  assert.equal(check([{ ...fact, version: 'client 12.5.1; SDK 1.4.5.30' }]), 'unknown');
  assert.equal(check([{ ...fact, version: 'client >=12.5.1; SDK 1.4.5.3' }]), 'unknown');
  assert.equal(check([{ ...fact, version: 'client 12.5.1; SDK >=1.4.5.3' }]), 'unknown');
  assert.equal(check([{ ...fact, version: 'client 12.5.1; SDK 1.4.5.3; SDK >=1.4.6.0' }]), 'unknown');
  assert.equal(check([{ ...fact, version: 'SDK 1.4.5.3' }]), 'unknown');
  assert.equal(check([{ ...fact, version: 'client 12.5.1' }]), 'unknown');
  assert.equal(check([{ ...fact, version: 'SDK page shows 1.4.5.3; app >=12.5.1' }]), 'unknown');
});

test('does not conflate SDK with client version or infer unspecified version', () => {
  assert.equal(check([{ ...fact, version: 'SDK 12.5.1; client 1.4.5.3' }]), 'unknown');
  assert.equal(check([fact], { ...target, client_version: null }), 'unknown');
  assert.equal(check([fact], { ...target, sdk_version: null }), 'unknown');
  assert.equal(check([{ ...fact, version: 'SDK 1.4.5.3' }], { ...target, client_version: null }), 'compatible');
  assert.equal(check([{ ...fact, version: 'client 12.5.1' }], { ...target, sdk_version: null }), 'compatible');
  assert.equal(check([fact], { ...target, client_version: null, sdk_version: null }), 'unknown');
  assert.equal(check([fact], { ...target, client_version: '12.5' }), 'unknown');
});

test('requires explicit matching client and platform; donor/unknown evidence cannot establish support', () => {
  for (const changed of [
    { client: 'AyuGram' }, { platform: null }, { platform: 'iOS' },
    { directness: 'donor' }, { directness: 'other' }, { source_id: 'ayugram-code', client: null }
  ]) assert.equal(check([{ ...fact, ...changed }]), 'unknown', JSON.stringify(changed));
  for (const changed of [{ client: null }, { platform: null }, { client: 'AyuGram' }, { platform: 'iOS' }]) {
    assert.equal(check([fact], { ...target, ...changed }), 'unknown', JSON.stringify(changed));
  }
  assert.equal(check([{ ...fact, client: null }]), 'compatible', 'explicit exteragram-docs source identifies client');
  assert.equal(check([{ ...fact, source_id: 'official-sdk', client: null }]), 'unknown');
});

test('LIKE matches, prefixes, unrelated claims and weak negative evidence remain unknown', () => {
  assert.equal(check([{ ...fact, api: 'send_request_extra()' }]), 'unknown');
  assert.equal(check([{ ...fact, api: 'another_api()', claim: 'send_request is supported.' }]), 'unknown');
  assert.equal(check([{ ...fact, api: '', claim: 'send_request is supported.' }]), 'unknown');
  assert.equal(check([{ ...fact, claim: 'another_api is supported.' }]), 'unknown');
  assert.equal(check([{ ...fact, claim: 'send_request was not found in search results.', topic: 'negative-evidence' }]), 'unknown');
  assert.equal(check([{ ...fact, claim: 'Documentation mentions send_request.' }]), 'unknown');
  assert.equal(check([{ ...fact, status: 'inference' }]), 'unknown');
  assert.equal(check([{ ...fact, knowledge_state: 'conflicting' }]), 'unknown');
  assert.equal(check([{ ...fact, review_status: 'conflicting' }]), 'unknown');
});

test('conflicting explicit assertions return unknown regardless of result order', () => {
  const negative = { ...fact, claim: 'send_request is unavailable.' };
  assert.equal(check([fact, negative]), 'unknown');
  assert.equal(check([negative, fact]), 'unknown');
});
