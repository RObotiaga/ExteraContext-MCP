import ast
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


def load_probe():
    tree = ast.parse((HERE / 'contract_probes.py').read_text())
    # Do not import the old module: its top-level exec would run saved model code.
    assert any(isinstance(n, ast.If) and '__name__' in ast.unparse(n.test)
               for n in tree.body), 'probe must be import-safe; no host execution at import'
    spec = importlib.util.spec_from_file_location('contract_probes', HERE / 'contract_probes.py')
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IsolationTests(unittest.TestCase):
    def test_real_sandbox_denies_host_files_network_writes_and_forks(self):
        probe = load_probe()
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / 'credential'
            secret.write_text('host-only-secret')
            code = f'''import os, socket, json, resource
checks = {{}}
checks['host_hidden'] = not os.path.exists({str(secret)!r}) and not os.path.exists('/home/aptem')
checks['env_clean'] = 'PROBE_SECRET' not in os.environ
try:
 open('/usr/probe-write', 'w').write('bad')
 checks['readonly'] = False
except OSError: checks['readonly'] = True
s = socket.socket()
try:
 s.connect(('1.1.1.1', 443))
 checks['network_denied'] = False
except OSError: checks['network_denied'] = True
try:
 pid = os.fork()
 if pid == 0: os._exit(0)
 os.waitpid(pid, 0)
 checks['fork_denied'] = False
except OSError: checks['fork_denied'] = True
checks['memory_bound'] = resource.getrlimit(resource.RLIMIT_AS)[0] <= 268435456
checks['cpu_bound'] = resource.getrlimit(resource.RLIMIT_CPU) == (2, 2)
checks['output_bound'] = resource.getrlimit(resource.RLIMIT_FSIZE) == (1048576, 1048576)
fs = os.statvfs('/tmp')
checks['scratch_bound'] = fs.f_blocks * fs.f_frsize <= 16777216
print(json.dumps(checks))
'''
            os.environ['PROBE_SECRET'] = 'do-not-leak'
            try:
                result = probe.run_candidate(code, 'checks')
            finally:
                del os.environ['PROBE_SECRET']
            self.assertEqual(result['returncode'], 0, result)
            self.assertTrue(all(json.loads(result['stdout']).values()), result)
            self.assertEqual(secret.read_text(), 'host-only-secret')

    def test_missing_isolation_fails_closed(self):
        probe = load_probe()
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'never'
            with self.assertRaises(probe.IsolationUnavailable):
                probe.run_candidate(f'open({str(marker)!r}, "w").write("bad")', 'checks', bwrap='/does/not/exist')
            self.assertFalse(marker.exists())

    def test_broken_isolation_fails_closed(self):
        probe = load_probe()
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'never'
            with self.assertRaises(probe.IsolationUnavailable):
                probe.run_candidate(f'open({str(marker)!r}, "w").write("bad")', 'checks', bwrap='/usr/bin/false')
            self.assertFalse(marker.exists())

    def test_candidate_output_is_bounded(self):
        result = load_probe().run_candidate("import os\nwhile True: os.write(1, b'x' * 65536)", 'checks')
        self.assertNotEqual(result['returncode'], 0)
        self.assertLessEqual(len(result['stdout']), 1048576)

    def test_synthetic_sdk_contract_executes_inside_sandbox(self):
        code = '''from base_plugin import BasePlugin, HookResult, HookStrategy
class Plugin(BasePlugin):
 def on_plugin_load(self): self.add_on_send_message_hook()
 def on_send_message_hook(self, account, params):
  if getattr(params, 'message', None) == '.hello' and not getattr(params, 'document', None):
   params.message = 'Hello'
  return HookResult(strategy=HookStrategy.MODIFY, params=params)
'''
        result = load_probe().run_candidate(code)
        self.assertEqual(result['returncode'], 0, result)
        outcomes = json.loads(result['stdout'])
        self.assertEqual(len(outcomes), 5)
        self.assertTrue(all(row['pass'] for row in outcomes))

    def test_worker_refuses_direct_host_invocation(self):
        import subprocess
        import sys
        result = subprocess.run([sys.executable, str(HERE / 'probe_worker.py'), 'checks'],
                                input='print("candidate-ran")', capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('candidate-ran', result.stdout)

    def test_infinite_candidate_is_bounded(self):
        result = load_probe().run_candidate('while True: pass', 'checks', timeout=0.3)
        self.assertTrue(result['timed_out'], result)


if __name__ == '__main__':
    unittest.main()
