#!/usr/bin/env node
import { createServer as createHttpServer } from 'node:http';
import process from 'node:process';
import { createHash, timingSafeEqual } from 'node:crypto';
import { createMcpHandler } from '@modelcontextprotocol/server';
import { serveStdio } from '@modelcontextprotocol/server/stdio';
import { localhostHostValidation, localhostOriginValidation, toNodeHandler } from '@modelcontextprotocol/node';
import { buildServer } from './server.mjs';
import { VERSION } from './version.mjs';

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
  if (!/^\/(?!\/)[A-Za-z0-9/_-]*$/.test(out.path)) throw new Error('--path must be a plain absolute URL path');
  return out;
}

function usage() {
  return `ExteraContext MCP ${VERSION}\n\n` +
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
    throw new Error(`HTTP transport is loopback-only in ${VERSION}. Put a trusted reverse proxy/auth layer in front instead of binding ExteraContext directly to a public interface.`);
  }

  // Explicit secret is mandatory for HTTP; stdio has no bearer requirement.
  const token = process.env.EXTERACONTEXT_MCP_HTTP_TOKEN;
  if (!token || Buffer.byteLength(token) < 32) {
    throw new Error('HTTP transport requires EXTERACONTEXT_MCP_HTTP_TOKEN (at least 32 bytes)');
  }
  const expectedTokenHash = createHash('sha256').update(token).digest();
  const authenticated = req => {
    const auth = req.headers.authorization;
    if (typeof auth !== 'string' || !/^Bearer [^\s]+$/.test(auth)) return false;
    const suppliedHash = createHash('sha256').update(auth.slice(7)).digest();
    return timingSafeEqual(expectedTokenHash, suppliedHash);
  };

  const handler = createMcpHandler(({ era }) => buildServer({ era, legacyAllowed: !args.modernOnly }), {
    legacy: args.modernOnly ? 'reject' : 'stateless',
    responseMode: args.responseMode,
    onerror: error => console.error(`[ExteraContext MCP] ${error.stack || error.message}`)
  });
  const nodeHandler = toNodeHandler(handler);
  const validateHost = localhostHostValidation();
  const validateOrigin = localhostOriginValidation();

  const http = createHttpServer((req, res) => {
    // Check the raw authority and origin before any preflight response or CORS headers.
    const host = req.headers.host;
    const hostMatch = typeof host === 'string' && /^(localhost|127\.0\.0\.1|\[::1\])(?::([1-9][0-9]{0,4}))?$/i.exec(host);
    const countHeader = name => req.rawHeaders.filter((_, i) => i % 2 === 0 && req.rawHeaders[i].toLowerCase() === name).length;
    if (!hostMatch || countHeader('host') !== 1 || Number(hostMatch[2] || 80) !== args.port) {
      res.writeHead(400).end('Invalid Host');
      return;
    }
    // The SDK localhost guard allows any loopback hostname/port and accepts some
    // non-origin URLs. A browser Origin must instead match this request authority.
    const origin = req.headers.origin;
    if (countHeader('origin') > 1 || (origin !== undefined && (() => {
      try {
        const parsed = new URL(origin);
        return parsed.protocol !== 'http:' || parsed.username !== '' || parsed.password !== '' ||
          parsed.pathname !== '/' || parsed.search !== '' || parsed.hash !== '' ||
          parsed.hostname.toLowerCase() !== hostMatch[1].toLowerCase() ||
          Number(parsed.port || 80) !== args.port;
      } catch { return true; }
    })())) {
      res.writeHead(403).end('Invalid Origin');
      return;
    }
    try {
      if (!validateHost(req, res) || !validateOrigin(req, res)) return;
    } catch {
      if (!res.headersSent) res.writeHead(400).end('Invalid Host or Origin');
      else res.end();
      return;
    }
    const raw = req.url;
    if (typeof raw !== 'string' || !/^\/(?!\/)/.test(raw) || /[\\#\x00-\x20\x7f]/.test(raw)) {
      res.writeHead(400).end('Malformed URL');
      return;
    }
    let url;
    try { url = new URL(raw, 'http://localhost'); }
    catch { res.writeHead(400).end('Malformed URL'); return; }
    if (url.pathname !== args.path) {
      res.writeHead(404).end('Not found');
      return;
    }
    if (typeof origin === 'string') {
      res.setHeader('Access-Control-Allow-Origin', origin);
      res.setHeader('Vary', 'Origin');
      res.setHeader('Access-Control-Allow-Methods', 'POST,GET,DELETE,OPTIONS');
      res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, MCP-Session-Id, MCP-Protocol-Version, Mcp-Method, Mcp-Name');
    }
    if (req.method === 'OPTIONS') {
      res.writeHead(204).end();
      return;
    }
    if (!authenticated(req)) {
      res.setHeader('WWW-Authenticate', 'Bearer');
      res.writeHead(401).end('Unauthorized');
      return;
    }
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
