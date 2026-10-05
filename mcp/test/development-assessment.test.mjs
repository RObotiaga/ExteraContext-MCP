import test from 'node:test';
import assert from 'node:assert/strict';
import { assessEvidence } from '../src/development.mjs';
const target={client:'AyuGram',platform:'Android',client_version:'12.9.0',package:'com.radolyn.ayugram',version_code:70079,apk_sha256:'8'.repeat(64)};

test('Russian and English token negation detects opposing claims without joining words',()=>{
  for (const [yes,no] of [['API поддерживается','API не поддерживается'],['поддерживается API','не поддерживается API'],['API   поддерживается!','API НЕ  поддерживается.'],['API supported','API not supported']]) {
    const facts=[yes,no].map((claim,i)=>({id:String(i),api:'API',claim}));
    assert.equal(assessEvidence(facts).semantics_conflicts.length,1,JSON.stringify(facts));
  }
  assert.equal(assessEvidence([{api:'API',claim:'API некий supported'},{api:'API',claim:'API кий supported'}]).semantics_conflicts.length,0);
});
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
test('version alone is not exact build evidence and opposing assertions are conflicts',()=>{
  const weak={client:'AyuGram',platform:'Android',client_version:'12.9.0'};
  assert.equal(assessEvidence([{...weak,status:'docs'}],weak).groups.exact_target.length,0);
  const facts=[{id:'yes',...target,status:'code',api:'testApi',claim:'testApi is supported',assertion:{key:'available',value:true}},
    {id:'no',...target,status:'code',api:'testApi',claim:'testApi is not supported',assertion:{key:'available',value:false}}];
  assert.equal(assessEvidence(facts,target,'testApi').compatibility,'conflict');
});

test('opposite assertions for disjoint versions are not contradictions',()=>{
  const facts=[{id:'old',client:'AyuGram',version:'client 12.8.0',api:'testApi',claim:'testApi is not supported'},
    {id:'new',client:'AyuGram',version:'client 12.9.0',api:'testApi',claim:'testApi is supported'}];
  assert.equal(assessEvidence(facts,{client:'AyuGram'},'testApi').semantics_conflicts.length,0);
  assert.equal(assessEvidence(facts.map(f=>({...f,version:'client 12.9.0'})),{client:'AyuGram'},'testApi').semantics_conflicts.length,1);
});

test('Class/wrapper evidence for disjoint targets is not a find_class conflict',()=>{
  const facts=[{...target,claim:'find_class returns Java Class'},
    {...target,client_version:'12.8.0',claim:'find_class returns Python wrapper'}];
  assert.equal(assessEvidence(facts,target,'find_class').semantics_conflicts.length,0);
  assert.equal(assessEvidence(facts,{client:'AyuGram'},'find_class').semantics_conflicts.length,0);
});

test('corroborating Java Class wrapper records are not a conflict',()=>{
  const facts=['first','second'].map(id=>({id,...target,status:'code',claim:'find_class returns a Java Class wrapper with getDeclaredMethod available'}));
  assert.equal(assessEvidence(facts,target,'find_class').compatibility,'exact-target-evidence');
  assert.equal(assessEvidence(facts,target,'find_class').semantics_conflicts.length,0);
});
