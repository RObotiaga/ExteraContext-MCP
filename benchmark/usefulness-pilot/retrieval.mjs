import {Client} from '../../mcp/node_modules/@modelcontextprotocol/client/dist/index.mjs';
import {StdioClientTransport} from '../../mcp/node_modules/@modelcontextprotocol/client/dist/stdio.mjs';
import {readFileSync,writeFileSync,mkdirSync,copyFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const here=dirname(fileURLToPath(import.meta.url));
const root=resolve(here,'../..');
const label=process.argv[2];
assert(label && /^[a-z0-9-]+$/.test(label));
const out=resolve('/home/aptem/.hermes/cache/scratch/extera-usefulness-pilot',label);
// Parent must already exist. Exclusive leaf creation refuses stale/partial runs.
mkdirSync(out);
const raw=readFileSync(here+'/dataset.json');
const sha=b=>createHash('sha256').update(b).digest('hex');
assert.equal(sha(raw),readFileSync(here+'/dataset.sha256','utf8').split(' ')[0]);
const dataset=JSON.parse(raw);
assert.equal(sha(readFileSync(dataset.corpus_path)),dataset.corpus_sha256);
const base=out+'/exteracontext.sqlite';
copyFileSync(dataset.corpus_path,base);
copyFileSync(dirname(dataset.corpus_path)+'/.knowledge-manifest.json',out+'/.knowledge-manifest.json');
const env={PATH:'/usr/bin:/bin',HOME:out,LANG:'C.UTF-8',TMPDIR:out,
 EXTERACONTEXT_DB:base,EXTERACONTEXT_KNOWLEDGE_DB:out+'/overlay.sqlite',
 EXTERACONTEXT_AUTO_SYNC:'0',EXTERACONTEXT_AUTO_BUILD:'0',EXTERACONTEXT_REQUIRE_MANIFEST:'1'};
const c=new Client({name:'frozen-usefulness-pilot',version:'1.0'},{versionNegotiation:{mode:{pin:'2026-07-28'}}});
const traces=[];
try{
 const t=new StdioClientTransport({command:process.execPath,args:[root+'/mcp/src/index.mjs','--transport','stdio','--modern-only'],cwd:root,env,stderr:'pipe'});
 let stderr=''; t.stderr?.on('data',d=>stderr+=d);
 await c.connect(t);
 const tools=(await c.listTools()).tools;
 writeFileSync(out+'/tools.json',JSON.stringify(tools,null,2));
 const doctor=await c.callTool({name:'doctor',arguments:{}});
 assert.equal(doctor.structuredContent.ok,true);
 writeFileSync(out+'/doctor.json',JSON.stringify(doctor,null,2));
 for(const task of dataset.cases){
   const args={query:task.query,target:task.target,limit:task.limit};
   const start=performance.now();
   const response=await c.callTool({name:'search_knowledge',arguments:args});
   assert.equal(response.isError,undefined);
   assert.equal(response.structuredContent.ok,true);
   const ms=performance.now()-start;
   const facts=response.structuredContent.data.context.facts;
   const ids=new Set(facts.map(f=>f.id));
   const covered=task.groups.filter(g=>g.some(id=>ids.has(id))).length;
   traces.push({id:task.id,split:task.split,args,ms,ids:[...ids],covered,total:task.groups.length,response});
   console.log(task.id,`${covered}/${task.groups.length}`,ms.toFixed(1)+'ms',[...ids].join(','));
 }
 writeFileSync(out+'/stderr.log',stderr);
}finally{
 await c.close();
 writeFileSync(out+'/retrieval.json',JSON.stringify({label,dataset_sha256:sha(raw),corpus_sha256:dataset.corpus_sha256,traces},null,2));
 assert.equal(sha(readFileSync(base)),dataset.corpus_sha256);
}
