"""Trusted worker, invoked ONLY inside bwrap by contract_probes.py."""
import json
import os
import resource
import sys
import types
from typing import Any

if __file__ != '/runner.py' or os.path.exists('/home'):
    raise SystemExit('worker requires the private bwrap filesystem; refusing host execution')

for limit, value in ((resource.RLIMIT_AS, 256 * 1024 * 1024),
                     (resource.RLIMIT_CPU, 2), (resource.RLIMIT_NPROC, 0),
                     (resource.RLIMIT_NOFILE, 32), (resource.RLIMIT_FSIZE, 1024 * 1024),
                     (resource.RLIMIT_CORE, 0)):
    resource.setrlimit(limit, (value, value))
if sys.argv[1] == 'preflight':
    print('ISOLATION_READY')
    sys.exit(0)

class HookResult:
    def __init__(self, strategy=None, params=None):
        self.strategy, self.params = strategy, params

class BasePlugin:
    def add_on_send_message_hook(self):
        self.registered = True

sdk = types.ModuleType('base_plugin')
setattr(sdk, 'BasePlugin', BasePlugin)
setattr(sdk, 'HookResult', HookResult)
setattr(sdk, 'HookStrategy', types.SimpleNamespace(MODIFY='modify'))
sys.modules['base_plugin'] = sdk
namespace = {}
exec(compile(sys.stdin.read(), '/candidate.py', 'exec'), namespace)
if sys.argv[1] == 'checks':
    sys.exit(0)
cls = next(v for v in namespace.values() if isinstance(v, type)
           and issubclass(v, BasePlugin) and v is not BasePlugin)
plugin: Any = cls()
plugin.on_plugin_load()
assert plugin.registered
outcomes = []
for label, fields, expected in [
    ('documented-minimal-text', {'message': '.hello'}, 'Hello'),
    ('unrelated-text', {'message': 'unchanged'}, 'unchanged'),
    ('no-string-media', {'document': object()}, None),
    ('captioned-document', {'message': '.hello', 'document': object(), 'caption': '.hello'}, '.hello'),
    ('known-null-media-fields', dict(message='.hello', photo=None, document=None,
     location=None, user=None, game=None, invoice=None, poll=None), 'Hello')]:
    params = types.SimpleNamespace(**fields)
    result = plugin.on_send_message_hook(2, params)
    actual = getattr(params, 'message', None)
    outcomes.append(dict(case=label, expected=expected, actual=actual,
                         **{'pass': actual == expected}, strategy=result.strategy))
print(json.dumps(outcomes))
