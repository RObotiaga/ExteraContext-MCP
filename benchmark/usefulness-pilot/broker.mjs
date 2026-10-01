// Capability-limited broker: only four read tools, official MCP SDK, scrubbed server env.
import {Client} from '../../mcp/node_modules/@modelcontextprotocol/client/dist/index.mjs';
import {StdioClientTransport} from '../../mcp/node_modules/@modelcontextprotocol/client/dist/stdio.mjs';
import {createInterface} from 'node:readline';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const state=process.argv[2];
const allowed=new Set(['search_knowledge','find_api','get_evidence','check_compatibility']);
const c=new Client({name:'isolated-pilot-agent',version:'1.0'},{versionNegotiation:{mode:{pin:'2026-07-28'}}});
const env={PATH:'/usr/bin:/bin',HOME:state,LANG:'C.UTF-8',TMPDIR:state,
 EXTERACONTEXT_DB:state+'/exteracontext.sqlite',EXTERACONTEXT_KNOWLEDGE_DB:state+'/overlay.sqlite',
 EXTERACONTEXT_AUTO_SYNC:'0',EXTERACONTEXT_AUTO_BUILD:'0',EXTERACONTEXT_REQUIRE_MANIFEST:'1'};
try {
 const t=new StdioClientTransport({command:process.execPath,args:[root+'/mcp/src/index.mjs','--transport','stdio','--modern-only'],cwd:root,env,stderr:'pipe'});
 t.stderr?.on('data',()=>{});
 await c.connect(t);
 const tools=(await c.listTools()).tools.filter(t=>allowed.has(t.name));
 console.log(JSON.stringify({tools}));
 for await(const line of createInterface({input:process.stdin})){
  const input=JSON.parse(line);
  if(!allowed.has(input.name)) {console.log(JSON.stringify({error:'Denied tool capability'}));continue;}
  const result=await c.callTool({name:input.name,arguments:input.arguments});
  console.log(JSON.stringify(result));
 }
}finally{await c.close();}
