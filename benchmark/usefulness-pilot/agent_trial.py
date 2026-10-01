"""Trusted model coordinator with a supervised global wall deadline.
No shell/file/network tools exposed to model. Only host coordinator reads OAuth.
"""
import argparse
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ALLOWED = {'search_knowledge', 'find_api', 'get_evidence', 'check_compatibility'}


def remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError('wall budget exceeded')
    return value


class BrokerIO:
    """Nonblocking, bounded newline JSON IO; stderr is discarded, never left in a pipe."""
    def __init__(self, process, deadline):
        self.process, self.deadline = process, deadline
        self.buffer = bytearray()
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)

    def wait(self, stream, event):
        with selectors.DefaultSelector() as selector:
            selector.register(stream, event)
            if not selector.select(remaining(self.deadline)):
                raise TimeoutError('broker IO wall budget exceeded')

    def read(self):
        while b'\n' not in self.buffer:
            self.wait(self.process.stdout, selectors.EVENT_READ)
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError('broker closed stdout before response')
            self.buffer.extend(chunk)
            if len(self.buffer) > 1024 * 1024:
                raise RuntimeError('broker response exceeds 1 MiB')
        line, _, tail = self.buffer.partition(b'\n')
        self.buffer = bytearray(tail)
        remaining(self.deadline)
        return json.loads(line)

    def write(self, value):
        data = memoryview((json.dumps(value) + '\n').encode())
        if len(data) > 1024 * 1024:
            raise RuntimeError('broker request exceeds 1 MiB')
        while data:
            self.wait(self.process.stdin, selectors.EVENT_WRITE)
            try:
                count = os.write(self.process.stdin.fileno(), data[:65536])
            except BlockingIOError:
                continue
            data = data[count:]
        remaining(self.deadline)


def save(workspace, name, obj, secret):
    text = json.dumps(obj, ensure_ascii=False, indent=2).replace(secret, '[REDACTED]')
    (workspace / name).write_text(text + '\n')


def _trial(w, client, model, secret, broker_command, start, deadline):
    broker = None
    io = None
    messages, log = [], []
    try:
        tools = []
        if broker_command:
            remaining(deadline)
            clean = {'PATH': '/usr/bin:/bin', 'HOME': str(w), 'LANG': 'C.UTF-8', 'TMPDIR': str(w)}
            broker = subprocess.Popen(broker_command, stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=clean)
            io = BrokerIO(broker, deadline)
            ready = io.read()
            tools = [{'type': 'function', 'function': {'name': t['name'],
                      'description': t['description'], 'parameters': t['inputSchema']}}
                     for t in ready['tools']]
            assert {t['function']['name'] for t in tools} == ALLOWED
        save(w, 'capabilities.json', {'tools': tools, 'isolation':
             'No file, shell, process, external network, write MCP, or credential capabilities exposed to model. Trusted host coordinator only; NOT a general-purpose OS sandbox claim.'}, secret)
        messages = [{'role': 'system', 'content':
            'You are implementing/reviewing an ExteraGram plugin task. Produce a concrete code/sketch and concise evidence-aware review. Use only the capabilities offered. Do not fabricate test results, source citations or runtime verification. Distinguish known documentation from unknown exact-version support. You have at most 6 model calls and 180 seconds. If tools are available, use them when useful; do not request new capabilities. No Android runtime is available.'},
            {'role': 'user', 'content': (w / 'prompt.txt').read_text()}]
        for turn in range(6):
            response = client.chat.completions.create(model=model, messages=messages,
                tools=tools or None, extra_body={'reasoning': {'effort': 'medium'}},
                timeout=min(remaining(deadline), 160))
            remaining(deadline)  # A late final response is never COMPLETE.
            m = response.choices[0].message
            calls = [{'id': tc.id, 'type': 'function', 'function': {
                      'name': tc.function.name, 'arguments': tc.function.arguments}}
                     for tc in (m.tool_calls or [])]
            msg = {'role': 'assistant', 'content': m.content}
            if calls:
                msg['tool_calls'] = calls
            messages.append(msg)
            usage = response.usage
            log.append({'turn': turn, 'model': response.model, 'message': msg,
                        'usage': usage.model_dump() if hasattr(usage, 'model_dump') else str(usage)})
            save(w, 'model-log.json', log, secret)
            if not calls:
                remaining(deadline)
                (w / 'answer.txt').write_text((m.content or '').replace(secret, '[REDACTED]') + '\n')
                save(w, 'result.json', {'status': 'COMPLETE', 'model': model,
                    'provider': 'openai-codex', 'turns': turn + 1,
                    'seconds': time.monotonic() - start, 'mcp': bool(broker_command),
                    'runtime': 'NOT CLAIMED'}, secret)
                remaining(deadline)
                break
            assert broker is not None, 'model called tool in no-tool arm'
            assert io is not None
            for call in calls:
                name = call['function']['name']
                assert name in ALLOWED, 'tool outside whitelist'
                args = json.loads(call['function']['arguments'])
                io.write({'name': name, 'arguments': args})
                result = io.read()
                log.append({'tool': name, 'arguments': args, 'response': result})
                messages.append({'role': 'tool', 'tool_call_id': call['id'],
                                 'content': json.dumps(result, ensure_ascii=False)})
            save(w, 'model-log.json', log, secret)
        else:
            save(w, 'blocker.json', {'status': 'INCOMPLETE', 'reason': '6 model-call budget exhausted'}, secret)
    except TimeoutError:
        save(w, 'blocker.json', {'status': 'INCOMPLETE', 'reason': 'wall budget exceeded'}, secret)
    except Exception as exc:
        status = 'INCOMPLETE' if time.monotonic() >= deadline else 'BLOCKED'
        save(w, 'blocker.json', {'status': status, 'type': type(exc).__name__,
                               'error': str(exc).replace(secret, '[REDACTED]')}, secret)
    finally:
        save(w, 'conversation.json', messages, secret)
        if broker:
            broker.kill()
            broker.wait(timeout=remaining(deadline))
        client.close()


def run_trial(workspace, client, model, secret, *, broker_command=None, wall_seconds=180):
    """Supervise the whole worker, including blocking SDK/startup/cleanup and its children.
    Linux fork keeps credentials in trusted memory, never in argv/env/workspace.
    """
    start = time.monotonic()
    deadline = start + wall_seconds
    pid = os.fork()
    if pid == 0:
        os.setsid()  # All broker/MCP descendants inherit this process group.
        try:
            _trial(workspace, client, model, secret, broker_command, start, deadline)
        except BaseException:
            os._exit(1)
        os._exit(0)
    expired = False
    status = None
    try:
        while True:
            done, status = os.waitpid(pid, os.WNOHANG)
            if time.monotonic() >= deadline:
                expired = True
                break
            if done:
                break
            time.sleep(min(0.01, remaining(deadline)))
    except TimeoutError:
        expired = True
    finally:
        # Kill the entire group on success too: no broker descendants left behind.
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            # Worker may not have reached setsid when an extremely short budget expired.
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    if expired:
        for name in ('answer.txt', 'result.json'):
            (workspace / name).unlink(missing_ok=True)
        save(workspace, 'blocker.json', {'status': 'INCOMPLETE', 'reason': 'wall budget exceeded',
                                       'seconds': time.monotonic() - start}, secret)
    elif (workspace / 'blocker.json').exists():
        for name in ('answer.txt', 'result.json'):
            (workspace / name).unlink(missing_ok=True)
    elif status != 0:
        save(workspace, 'blocker.json', {'status': 'BLOCKED', 'reason': 'coordinator worker failed'}, secret)
        for name in ('answer.txt', 'result.json'):
            (workspace / name).unlink(missing_ok=True)
    elif (workspace / 'result.json').exists():
        # COMPLETE elapsed covers worker cleanup, not merely the last SDK response.
        result = json.loads((workspace / 'result.json').read_text())
        result['seconds'] = time.monotonic() - start
        if result['seconds'] >= wall_seconds:
            (workspace / 'answer.txt').unlink(missing_ok=True)
            (workspace / 'result.json').unlink()
            save(workspace, 'blocker.json', {'status': 'INCOMPLETE', 'reason': 'wall budget exceeded'}, secret)
        else:
            save(workspace, 'result.json', result, secret)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('workspace', type=Path)
    p.add_argument('--mcp', action='store_true')
    a = p.parse_args()
    w = a.workspace.resolve()
    assert (w / 'prompt.txt').is_file()
    if any((w / name).exists() for name in ('answer.txt', 'result.json', 'blocker.json', 'model-log.json', 'conversation.json')):
        raise SystemExit('refusing stale trial workspace; create a fresh workspace')
    sys.path.insert(0, '/home/aptem/.hermes/hermes-agent')
    from hermes_cli.auth_codex import resolve_codex_runtime_credentials
    try:
        runtime = resolve_codex_runtime_credentials(read_only=True)
    except Exception as exc:
        (w / 'blocker.json').write_text(json.dumps({'status': 'BLOCKED', 'type': type(exc).__name__, 'code': getattr(exc, 'code', None)}))
        raise SystemExit('BLOCKED: readonly openai-codex credential resolution failed (see blocker.json)')
    secret = runtime['api_key']
    os.environ['HERMES_HOME'] = str(w / 'hermes-home')
    os.environ['HOME'] = str(w)
    (w / 'hermes-home').mkdir(exist_ok=True)
    from openai import OpenAI
    from agent.auxiliary_client import CodexAuxiliaryClient
    model = 'gpt-6.1-sol'
    client = CodexAuxiliaryClient(OpenAI(api_key=secret, base_url=runtime['base_url'], max_retries=0, timeout=160), model)
    command = None
    if a.mcp:
        node = os.environ.get('NODE_BINARY') or shutil.which('node')
        assert node
        command = [node, str(Path(__file__).with_name('broker.mjs')), str(w)]
    run_trial(w, client, model, secret, broker_command=command)
    print('artifact', w, 'complete', (w / 'result.json').exists())


if __name__ == '__main__':
    main()
