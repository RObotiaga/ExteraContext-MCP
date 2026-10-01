import hashlib
from pathlib import Path
import shutil
import subprocess
import uuid
import unittest

HERE = Path(__file__).resolve().parent
OUTPUT = Path('/home/aptem/.hermes/cache/scratch/extera-usefulness-pilot')
NODE = shutil.which('node')
assert NODE, 'node is required for real retrieval CLI regressions'


def snapshot(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob('*') if p.is_file()}


class RetrievalOutputTests(unittest.TestCase):
    def test_existing_label_is_refused_without_changing_any_artifact(self):
        assert NODE
        directory = OUTPUT / ('precommit-' + uuid.uuid4().hex)
        directory.mkdir()
        self.addCleanup(shutil.rmtree, directory)
        for name in ('exteracontext.sqlite', 'retrieval.json', 'tools.json', 'doctor.json', 'stderr.log'):
            (directory / name).write_text('old fixture: ' + name)
        before = snapshot(directory)
        result = subprocess.run([NODE, str(HERE / 'retrieval.mjs'), directory.name],
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0, 'existing label was accepted')
        self.assertIn('EEXIST', result.stderr)
        self.assertEqual(snapshot(directory), before)

    def test_baseline_and_repeat_are_refused_and_preserved(self):
        assert NODE
        for label in ('baseline', 'repeat'):
            directory = OUTPUT / label
            self.assertTrue(directory.is_dir(), f'missing preserved pilot result: {directory}')
            before = snapshot(directory)
            result = subprocess.run([NODE, str(HERE / 'retrieval.mjs'), label],
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('EEXIST', result.stderr)
            self.assertEqual(snapshot(directory), before)


if __name__ == '__main__':
    unittest.main()
