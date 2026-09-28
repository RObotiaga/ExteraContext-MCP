#!/usr/bin/env node
import { createServer as createHttpServer } from 'node:http';
import process from 'node:process';
import { createMcpHandler } from '@modelcontextprotocol/server';
import { serveStdio } from '@modelcontextprotocol/server/stdio';
import { localhostHostValidation, localhostOriginValidation, toNodeHandler } from '@modelcontextprotocol/node';
import { buildServer } from './server.mjs';

function parseArgs(argv) {
  const out = { transport: 'stdio', host: '127.0.0.1', port: 7357, path: '/mcp', modernOnly: false, responseMode: 'auto' };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const next = () => argv[++i];
    if (arg === '--transport') out.transport = next();
    else if (arg === '--host') out.host = next();
    else if (arg === '--port') out.port = Number(next());
    else if (arg === '--path') out.path = next();
    else if (arg === '--modern-only') out.modernOnly = true;
    else if (arg === '--response-mode') out.responseMode = next();
    else if (arg === '-h' || arg === '--help') out.help = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }
  if (!['stdio', 'http'].includes(out.transport)) throw new Error('--transport must be stdio or http');
  if (!Number.isInteger(out.port) || out.port < 1 || out.port > 65535) throw new Error('--port must be 1..65535');
  if (!['auto', 'json', 'sse'].includes(out.responseMode)) throw new Error('--response-mode must be auto|json|sse');
  if (!out.path.startsWith('/')) throw new Error('--path must begin with /');
  return out;
}

function usage() {
  return `ExteraContext MCP 0.6.1\n\n` +
    `Usage:\n` +
    `  node mcp/src/index.mjs --transport stdio [--modern-only]\n` +
    `  node mcp/src/index.mjs --transport http [--host 127.0.0.1] [--port 7357] [--path /mcp] [--modern-only] [--response-mode auto|json|sse]\n\n` +
    `Default mode serves MCP 2026-07-28 and legacy 2025-era clients. --modern-only rejects legacy openings.\n`;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    process.stdout.write(usage());
    return;
  }
  const legacy = args.modernOnly ? 'reject' : 'serve';

  if (args.transport === 'stdio') {
    const handle = serveStdio(({ era }) => buildServer({ era, legacyAllowed: !args.modernOnly }), {
      legacy,
      onerror: error => console.error(`[ExteraContext MCP] ${error.stack || error.message}`)
    });
    const shutdown = async signal => {
      console.error(`[ExteraContext MCP] ${signal}; closing`);
      await handle.close().catch(error => console.error(error));
      process.exit(0);
    };
    process.once('SIGINT', () => void shutdown('SIGINT'));
    process.once('SIGTERM', () => void shutdown('SIGTERM'));
    return;
  }

  if (!['127.0.0.1', '::1', 'localhost'].includes(args.host)) {
    throw new Error('HTTP transport is loopback-only in v0.6.1. Put a trusted reverse proxy/auth layer in front instead of binding ExteraContext directly to a public interface.');
  }

  const handler = createMcpHandler(({ era }) => buildServer({ era, legacyAllowed: !args.modernOnly }), {
    legacy: args.modernOnly ? 'reject' : 'stateless',
    responseMode: args.responseMode,
    onerror: error => console.error(`[ExteraContext MCP] ${error.stack || error.message}`)
  });
  const nodeHandler = toNodeHandler(handler);
  const validateHost = localhostHostValidation();
  const validateOrigin = localhostOriginValidation();

  const http = createHttpServer((req, res) => {
    const url = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);
    if (url.pathname !== args.path) {
      res.statusCode = 404;
      res.end('Not found');
      return;
    }
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'POST,GET,DELETE,OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, MCP-Session-Id, MCP-Protocol-Version, Mcp-Method, Mcp-Name');
    if (req.method === 'OPTIONS') {
      res.statusCode = 204;
      res.end();
      return;
    }
    if (!validateHost(req, res) || !validateOrigin(req, res)) return;
    void nodeHandler(req, res);
  });

  await new Promise((resolve, reject) => {
    http.once('error', reject);
    http.listen(args.port, args.host, resolve);
  });
  console.error(`[ExteraContext MCP] listening on http://${args.host}:${args.port}${args.path} (modern 2026-07-28${args.modernOnly ? ', legacy rejected' : ' + legacy fallback'})`);

  const shutdown = async signal => {
    console.error(`[ExteraContext MCP] ${signal}; closing`);
    try { await handler.close?.(); } catch (error) { console.error(error); }
    await new Promise(resolve => http.close(resolve));
    process.exit(0);
  };
  process.once('SIGINT', () => void shutdown('SIGINT'));
  process.once('SIGTERM', () => void shutdown('SIGTERM'));
}

main().catch(error => {
  console.error(`[ExteraContext MCP] fatal: ${error.stack || error.message}`);
  process.exit(1);
});
