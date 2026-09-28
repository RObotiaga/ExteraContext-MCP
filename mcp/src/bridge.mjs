import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
export const ROOT = resolve(here, '..', '..');
export const PYTHON = process.env.EXTERACONTEXT_PYTHON || 'python3';
const DEFAULT_TIMEOUT_MS = Number(process.env.EXTERACONTEXT_MCP_TIMEOUT_MS || 45_000);

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

export function runPython(script, args = [], { timeoutMs = DEFAULT_TIMEOUT_MS, env = {} } = {}) {
  const command = [PYTHON, script, ...args];
  return new Promise((resolvePromise, rejectPromise) => {
    const child = spawn(PYTHON, [script, ...args], {
      cwd: ROOT,
      env: { ...process.env, ...env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
      stdio: ['ignore', 'pipe', 'pipe']
    });
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', chunk => { stdout += chunk; });
    child.stderr.on('data', chunk => { stderr += chunk; });
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill('SIGKILL');
    }, timeoutMs);
    child.once('error', error => {
      clearTimeout(timer);
      rejectPromise(new BridgeError(`Failed to launch Python bridge: ${error.message}`, {
        command, stderr, stdout
      }));
    });
    child.once('close', code => {
      clearTimeout(timer);
      if (timedOut) {
        rejectPromise(new BridgeError(`Python bridge timed out after ${timeoutMs} ms`, {
          command, exitCode: code, stderr, stdout
        }));
        return;
      }
      if (code !== 0) {
        rejectPromise(new BridgeError(stderr.trim() || `Python bridge exited with code ${code}`, {
          command, exitCode: code, stderr, stdout
        }));
        return;
      }
      resolvePromise({ stdout, stderr, command });
    });
  });
}

export async function runPythonJson(script, args = [], options = {}) {
  const { stdout } = await runPython(script, args, options);
  const text = stdout.trim();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new BridgeError(`Python bridge returned non-JSON output: ${error.message}`, {
      command: [PYTHON, script, ...args], stdout: text
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
