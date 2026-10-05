import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
export const ROOT = resolve(here, '..', '..');
export function resolvePython(platform = process.platform, env = process.env) {
  return env.EXTERACONTEXT_PYTHON || (platform === 'win32' ? 'python' : 'python3');
}
export const PYTHON = resolvePython();
const configuredTimeout = Number(process.env.EXTERACONTEXT_MCP_TIMEOUT_MS);
const DEFAULT_TIMEOUT_MS = Number.isSafeInteger(configuredTimeout) && configuredTimeout > 0 ? configuredTimeout : 45_000;
const MAX_OUTPUT_BYTES = 2 * 1024 * 1024;
const MAX_ARG_BYTES = 256 * 1024;
const MAX_ARG_COUNT = 128;
const MAX_STDIN_BYTES = 64 * 1024;
const MAX_CONCURRENT = 4;
const MAX_PENDING = 64;
const pending = [];
let active = 0;

export class BridgeError extends Error {
  constructor(message, { command, exitCode, stderr, stdout } = {}) {
    super(message);
    this.name = 'BridgeError';
    this.command = command;
    this.exitCode = exitCode;
    this.stderr = stderr;
    this.stdout = stdout;
  }
}

// Never echo capability tokens in the command attached to errors or successful results.
function safeCommand(script, args) {
  return [PYTHON, script, ...args.map((arg, i) => args[i - 1] === '--actor-token' ? '[REDACTED]' : arg)];
}

function drain() {
  while (active < MAX_CONCURRENT && pending.length) {
    const job = pending.shift();
    if (!job.done) job.start();
  }
}

export function runPython(script, args = [], { timeoutMs = DEFAULT_TIMEOUT_MS, env = {}, signal, stdin = null } = {}) {
  if (typeof script !== 'string' || !script || !Array.isArray(args) || args.length > MAX_ARG_COUNT ||
      args.some(arg => typeof arg !== 'string' || arg.includes('\0')) || script.includes('\0')) {
    return Promise.reject(new BridgeError('Invalid Python bridge arguments'));
  }
  const bytes = [script, ...args].reduce((sum, arg) => sum + Buffer.byteLength(arg), 0);
  if (bytes > MAX_ARG_BYTES) return Promise.reject(new BridgeError('Python bridge arguments exceed size limit'));
  if (stdin !== null && typeof stdin !== 'string') return Promise.reject(new BridgeError('Python bridge stdin must be a string'));
  if (stdin !== null && Buffer.byteLength(stdin, 'utf8') > MAX_STDIN_BYTES) {
    return Promise.reject(new BridgeError(`Python bridge stdin exceeds ${MAX_STDIN_BYTES} byte limit`));
  }
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 300_000) {
    return Promise.reject(new BridgeError('Invalid Python bridge timeout'));
  }
  if (pending.length >= MAX_PENDING) return Promise.reject(new BridgeError('Python bridge queue is full'));
  const command = safeCommand(script, args);
  const secrets = args.filter((arg, i) => args[i - 1] === '--actor-token' && arg);
  if (stdin && typeof stdin === 'string' && stdin.length > 5) secrets.push(stdin.trim());
  const redact = text => secrets.reduce((value, secret) => value.replaceAll(secret, '[REDACTED]'), text);
  return new Promise((resolvePromise, rejectPromise) => {
    const job = { done: false, start: null };
    let child;
    let timer;
    let stdout = '';
    let stderr = '';
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let failure;
    let running = false;
    const finish = (error, result) => {
      if (job.done) return;
      job.done = true;
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
      if (running) { active--; drain(); }
      else {
        const at = pending.indexOf(job);
        if (at !== -1) pending.splice(at, 1);
      }
      if (error) rejectPromise(error);
      else resolvePromise(result);
    };
    const abort = message => {
      failure ||= message;
      if (child) child.kill('SIGKILL');
      else finish(new BridgeError(message, { command }));
    };
    const onAbort = () => abort('Python bridge cancelled');
    if (signal?.aborted) { finish(new BridgeError('Python bridge cancelled', { command })); return; }
    signal?.addEventListener('abort', onAbort, { once: true });
    timer = setTimeout(() => abort(`Python bridge timed out after ${timeoutMs} ms`), timeoutMs);
    job.start = () => {
      running = true;
      active++;
      try {
        child = spawn(PYTHON, [script, ...args], {
          cwd: ROOT,
          env: { ...process.env, ...env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', EXTERACONTEXT_LITERAL_INPUTS: '1' },
          stdio: stdin !== null ? ['pipe', 'pipe', 'pipe'] : ['ignore', 'pipe', 'pipe'],
          windowsHide: true
        });
        if (stdin !== null) {
          child.stdin.on('error', () => {});
          child.stdin.write(stdin + '\n');
          child.stdin.end();
        }
      } catch (error) {
        finish(new BridgeError(`Failed to launch Python bridge: ${error.message}`, { command }));
        return;
      }
      const collect = (chunk, stream) => {
        const count = Buffer.byteLength(chunk);
        if (stream === 'stdout') {
          stdoutBytes += count;
          if (stdoutBytes <= MAX_OUTPUT_BYTES) stdout += chunk;
        } else {
          stderrBytes += count;
          if (stderrBytes <= MAX_OUTPUT_BYTES) stderr += chunk;
        }
        if (stdoutBytes > MAX_OUTPUT_BYTES || stderrBytes > MAX_OUTPUT_BYTES) {
          abort('Python bridge output exceeds size limit');
        }
      };
      child.stdout.setEncoding('utf8');
      child.stderr.setEncoding('utf8');
      child.stdout.on('data', chunk => collect(chunk, 'stdout'));
      child.stderr.on('data', chunk => collect(chunk, 'stderr'));
      child.once('error', error => {
        failure ||= `Failed to launch Python bridge: ${error.message}`;
      });
      child.once('close', code => {
        if (failure || code !== 0) {
          finish(new BridgeError(redact(failure || stderr.trim() || `Python bridge exited with code ${code}`), {
            command, exitCode: code, stdout: redact(stdout), stderr: redact(stderr)
          }));
        } else finish(null, { stdout: redact(stdout), stderr: redact(stderr), command });
      });
      if (signal?.aborted) onAbort();
    };
    pending.push(job);
    drain();
  });
}

export async function runPythonJson(script, args = [], options = {}) {
  const { stdout, command } = await runPython(script, args, options);
  const text = stdout.trim();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new BridgeError(`Python bridge returned non-JSON output: ${error.message}`, {
      command, stdout: text
    });
  }
}

export function compactTarget(target = {}) {
  return Object.fromEntries(Object.entries(target).filter(([, value]) => value !== undefined && value !== null && value !== ''));
}

export function targetLabel(target = {}) {
  return [target.client, target.platform].filter(Boolean).join(' ') || undefined;
}

export function jsonArg(value) {
  return JSON.stringify(value ?? null);
}
