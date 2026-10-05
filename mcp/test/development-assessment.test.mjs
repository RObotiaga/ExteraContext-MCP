import test from 'node:test';
import assert from 'node:assert/strict';
import { assessEvidence } from '../src/development.mjs';
const target={client:'AyuGram',platform:'Android',client_version:'12.9.0',package:'com.radolyn.ayugram',version_code:70079,apk_sha256:'8'.repeat(64)};
test('source assertions without exact build/hash remain references',()=>{
  const fact={client:'AyuGram',platform:'Android',version:'client 12.9.0',status:'code',claim:'API available'};
  const result=assessEvidence([fact],target);
  assert.equal(result.groups.reference.length,1);
  assert.equal(result.groups.exact_target.length,0);
  assert.equal(result.runtime_verified,false);
  assert.equal(assessEvidence([{...fact,...target}],target).groups.exact_target.length,1);
  assert.equal(assessEvidence([{...fact,...target,version_code:70080}],target).groups.mismatched.length,1);
});
test('documented Class and observed wrapper discrepancy cannot silently become a recommendation',()=>{
  const result=assessEvidence([{claim:'find_class returns Java Class; getDeclaredMethod is available',status:'docs'},
    {claim:'find_class returned a Python wrapper without reflection methods',status:'docs'}],target,'find_class');
  assert.equal(result.compatibility,'conflict');
  assert.ok(result.next_checks.some(c=>c.id==='probe_bridge_contract'));
});
