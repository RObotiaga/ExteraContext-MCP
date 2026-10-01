"""Synthetic SDK probes. Model code runs ONLY in a fail-closed bwrap process."""
import argparse
import ast
import json
import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class IsolationUnavailable(RuntimeError):
    pass


def run_candidate(code, mode='contract', *, bwrap='/usr/sbin/bwrap', timeout=5) -> dict[str, Any]:
    worker = Path(__file__).with_name('probe_worker.py').resolve()
    command = [bwrap, '--unshare-all', '--unshare-user', '--disable-userns', '--die-with-parent', '--new-session',
               '--cap-drop', 'ALL', '--clearenv', '--setenv', 'PATH', '/usr/bin',
               '--setenv', 'HOME', '/nonexistent', '--setenv', 'TMPDIR', '/tmp',
               '--ro-bind', '/usr/lib', '/usr/lib',
               '--ro-bind', '/usr/lib64', '/usr/lib64',
               '--ro-bind', str(Path('/usr/bin/python3').resolve()), '/usr/bin/python3',
               '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
               '--proc', '/proc', '--dev', '/dev', '--size', '16777216', '--tmpfs', '/tmp',
               '--remount-ro', '/proc', '--remount-ro', '/dev',
               '--ro-bind', str(worker), '/runner.py', '--remount-ro', '/', '--chdir', '/tmp',
               '/usr/bin/python3', '-I', '/runner.py']
    # No fallback. Verify the actual runtime before supplying any candidate code.
    try:
        ready = subprocess.run(command + ['preflight'], env={}, capture_output=True,
                               timeout=5, check=True)
        if ready.stdout != b'ISOLATION_READY\n':
            raise IsolationUnavailable('isolation preflight did not attest readiness')
    except (OSError, subprocess.SubprocessError) as exc:
        raise IsolationUnavailable('bwrap isolation unavailable; candidate NOT executed') from exc
    # Regular files plus worker RLIMIT_FSIZE bound malicious output, unlike communicate pipes.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(command + [mode], env={}, stdin=subprocess.PIPE,
                                   stdout=out, stderr=err, start_new_session=True)
        timed_out = False
        try:
            process.communicate(code.encode(), timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        out.seek(0)
        err.seek(0)
        return dict(returncode=process.returncode, timed_out=timed_out,
                    stdout=out.read(1024 * 1024).decode(errors='replace'),
                    stderr=err.read(1024 * 1024).decode(errors='replace'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('agents', type=Path)
    parser.add_argument('--output', type=Path, required=True,
                        help='NEW report file; existing reports are never overwritten')
    args = parser.parse_args()
    # Reserve before any candidate runs; do not overwrite historic pilot reports.
    with args.output.open('x') as output:
        report = []
        for name in ['r17', 'r42', 'r08', 'r63']:
            text = (args.agents / name / 'answer.txt').read_text()
            snippets = re.findall(r'```python\n(.*?)```', text, re.S)
            assert len(snippets) == 1
            code = snippets[0]
            ast.parse(code)
            item: dict[str, Any] = dict(id=name, syntax='PASS', runtime='NOT CLAIMED',
                        probe_kind='synthetic SDK contract probes in bwrap; NOT Android runtime')
            if name in ['r17', 'r42']:
                result = run_candidate(code)
                item['process'] = result
                if result['returncode'] == 0 and not result['timed_out']:
                    item['outcomes'] = json.loads(result['stdout'])
            report.append(item)
        output.write(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
