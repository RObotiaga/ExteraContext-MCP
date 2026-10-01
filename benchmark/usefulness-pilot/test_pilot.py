import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pilot

class PilotTests(unittest.TestCase):
    def test_groups_require_every_obligation_but_accept_alternatives(self):
        case = {'groups': [['a','b'],['c']]}
        self.assertEqual(pilot.score(case, [{'id':'b'}]), {'covered':1,'total':2,'complete':False})
        self.assertEqual(pilot.score(case, [{'id':'b'},{'id':'c'}]), {'covered':2,'total':2,'complete':True})

    def test_trial_refuses_stale_results_before_authentication(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / 'prompt.txt').write_text('A test prompt')
            (workspace / 'answer.txt').write_text('preserve old answer')
            env = {'PATH': os.environ['PATH'], 'HOME': tmp, 'HERMES_HOME': tmp, 'LANG': 'C.UTF-8'}
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('agent_trial.py')), tmp],
                                    env=env, text=True, capture_output=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('refusing stale trial workspace', result.stdout + result.stderr)
            self.assertEqual((workspace / 'answer.txt').read_text(), 'preserve old answer')
            self.assertFalse((workspace / 'blocker.json').exists())

    def test_frozen_source_labels_and_corpus_are_intact(self):
        here = Path(__file__).resolve().parent
        raw = (here / 'dataset.json').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), (here / 'dataset.sha256').read_text().split()[0])
        dataset = json.loads(raw)
        source = Path(dataset['source_root'])
        self.assertEqual(hashlib.sha256(Path(dataset['corpus_path']).read_bytes()).hexdigest(), dataset['corpus_sha256'])
        dev = {c['family'] for c in dataset['cases'] if c['split'] == 'development'}
        held = {c['family'] for c in dataset['cases'] if c['split'] == 'heldout'}
        self.assertFalse(dev.intersection(held))
        self.assertEqual(len(dataset['cases']), 8)
        facts = {}
        for file in (source / 'data/wiki/facts').glob('*.json'):
            facts.update({f['id']: f for f in json.loads(file.read_text())})
        for case in dataset['cases']:
            self.assertTrue(case['groups'])
            self.assertTrue(case['must_handle'])
            self.assertTrue(case['hard_fail'])
            for group in case['groups']:
                self.assertTrue(group)
                for id in group:
                    self.assertIn(id, facts)
            for ref in case['evidence']:
                content = (source / ref['path']).read_bytes()
                self.assertEqual(hashlib.sha256(content).hexdigest(), ref['file_sha256'])
                start, end = ref['lines']
                self.assertEqual('\n'.join(content.decode().splitlines()[start-1:end]), ref['excerpt'])

if __name__ == '__main__':
    unittest.main()
