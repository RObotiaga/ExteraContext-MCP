import ast
import importlib.util
import json
from pathlib import Path
import os
import sys
import tempfile
import time
import types
import unittest

HERE = Path(__file__).resolve().parent


def load_trial():
    tree = ast.parse((HERE / 'agent_trial.py').read_text())
    assert any(isinstance(n, ast.If) and '__name__' in ast.unparse(n.test)
               for n in tree.body), 'trial must be import-safe for offline deadline tests'
    spec = importlib.util.spec_from_file_location('agent_trial', HERE / 'agent_trial.py')
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(self, delay: float=0, calls=None):
        self.delay, self.calls = delay, calls
        self.chat = types.SimpleNamespace(completions=self)
    def create(self, **kwargs):
        time.sleep(self.delay)  # Deliberately ignores SDK timeout; supervisor must enforce it.
        message = types.SimpleNamespace(content='offline fixture', tool_calls=self.calls)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)],
                                     usage=None, model='offline-fixture')
    def close(self):
        pass


class DeadlineTests(unittest.TestCase):
    def run_fixture(self, client, broker=None, budget=0.4):
        module = load_trial()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workspace = Path(tmp.name)
        (workspace / 'prompt.txt').write_text('offline fixture')
        start = time.monotonic()
        module.run_trial(workspace, client, 'offline-fixture', 'fixture-secret',
                         broker_command=broker, wall_seconds=budget)
        self.assertLess(time.monotonic() - start, budget + 0.6)
        return workspace

    def assert_incomplete(self, workspace):
        self.assertEqual(json.loads((workspace / 'blocker.json').read_text())['status'], 'INCOMPLETE')
        self.assertFalse((workspace / 'answer.txt').exists())
        self.assertFalse((workspace / 'result.json').exists())

    def test_late_final_response_never_complete(self):
        self.assert_incomplete(self.run_fixture(FakeClient(delay=2)))

    def test_broker_startup_partial_line_and_stderr_are_bounded(self):
        broker = [sys.executable, '-c', "import os,time; os.write(1,b'{'); os.write(2,b'x'*200000); time.sleep(10)"]
        self.assert_incomplete(self.run_fixture(FakeClient(), broker))

    def test_broker_tool_response_deadline_kills_descendant(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / 'pid'
            tools = [{'name': n, 'description': '', 'inputSchema': {}} for n in
                     ['search_knowledge','find_api','get_evidence','check_compatibility']]
            script = f'''import subprocess,time,json,sys
p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
open({str(pidfile)!r}, 'w').write(str(p.pid))
print({json.dumps({'tools': tools})!r}, flush=True)
sys.stdin.readline()
time.sleep(30)
'''
            call = types.SimpleNamespace(id='fixture', function=types.SimpleNamespace(
                name='search_knowledge', arguments='{}'))
            self.assert_incomplete(self.run_fixture(FakeClient(calls=[call]),
                                   [sys.executable, '-c', script]))
            pid = int(pidfile.read_text())
            status = Path(f'/proc/{pid}/stat')
            # A killed orphan may briefly remain a zombie until init reaps it.
            self.assertTrue(not status.exists() or status.read_text().split()[2] == 'Z')

    def test_broker_write_to_nonreader_is_bounded(self):
        tools = [{'name': n, 'description': '', 'inputSchema': {}} for n in
                 ['search_knowledge','find_api','get_evidence','check_compatibility']]
        script = f"import time; print({json.dumps({'tools': tools})!r}, flush=True); time.sleep(30)"
        call = types.SimpleNamespace(id='fixture', function=types.SimpleNamespace(
            name='search_knowledge', arguments=json.dumps({'query': 'x' * 500000})))
        self.assert_incomplete(self.run_fixture(FakeClient(calls=[call]), [sys.executable, '-c', script]))

    def test_fast_final_is_complete_with_real_elapsed(self):
        workspace = self.run_fixture(FakeClient(delay=0.03), budget=1)
        result = json.loads((workspace / 'result.json').read_text())
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertGreaterEqual(result['seconds'], 0.03)
        self.assertLess(result['seconds'], 1)
        self.assertFalse((workspace / 'blocker.json').exists())


if __name__ == '__main__':
    unittest.main()
