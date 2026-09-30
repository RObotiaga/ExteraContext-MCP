"""Unit tests for paired task-cluster uncertainty estimates."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "benchmark" / "agent-v1"
sys.path.insert(0, str(MODULE_DIR))
from uncertainty import clean_success_uncertainty  # noqa: E402


TASKS = tuple(f"task-{index:02d}" for index in range(10))
MODES = ("A", "B", "C")


def make_schedule():
    manifest = {}
    for task_index, task_id in enumerate(TASKS):
        for mode in MODES:
            for repeat in (1, 2):
                run_id = f"run-{task_index:02d}-{mode}-{repeat}"
                manifest[run_id] = {
                    "run_id": run_id,
                    "task_id": task_id,
                    "mode": mode,
                    "repeat": repeat,
                }
    return manifest


def make_rows(manifest):
    rows = []
    for run_id, run in manifest.items():
        task_index = TASKS.index(run["task_id"])
        mode, repeat = run["mode"], run["repeat"]
        # A: 6/10 tasks pass both repeats = .60.
        # B: 8/10 tasks pass both repeats = .80.
        # C: 5 tasks pass both, task 6 passes one repeat = .55.
        if mode == "A":
            success = task_index < 6
        elif mode == "B":
            success = task_index < 8
        else:
            success = task_index < 5 or (task_index == 5 and repeat == 1)
        rows.append({
            "run_id": run_id,
            "task_id": run["task_id"],
            "mode": mode,
            "clean_success": success,
        })
    return rows


class CleanSuccessUncertaintyTests(unittest.TestCase):
    def setUp(self):
        self.manifest = make_schedule()
        self.rows = make_rows(self.manifest)

    def test_point_estimates_and_paired_differences(self):
        result = clean_success_uncertainty(self.rows, self.manifest, resamples=2000)
        self.assertEqual(result["point_estimates"], {"A": 0.6, "B": 0.8, "C": 0.55})
        self.assertAlmostEqual(result["paired_differences"]["A-B"]["point_estimate"], -0.2)
        self.assertAlmostEqual(result["paired_differences"]["A-C"]["point_estimate"], 0.05)
        self.assertAlmostEqual(result["paired_differences"]["B-C"]["point_estimate"], 0.25)
        self.assertEqual(result["task_clusters"], 10)
        self.assertEqual(result["repeats_per_mode_per_task"], 2)
        self.assertEqual(result["resamples"], 2000)
        self.assertEqual(result["seed"], 20260728)
        self.assertEqual(result["confidence_level"], 0.95)
        self.assertIn("paired task-cluster", result["method"])
        for mode in MODES:
            ci = result["confidence_intervals"][mode]
            self.assertLessEqual(ci["lower"], result["point_estimates"][mode])
            self.assertGreaterEqual(ci["upper"], result["point_estimates"][mode])
            self.assertGreaterEqual(ci["lower"], 0.0)
            self.assertLessEqual(ci["upper"], 1.0)
        for contrast in ("A-B", "A-C", "B-C"):
            ci = result["paired_differences"][contrast]["confidence_interval_95"]
            point = result["paired_differences"][contrast]["point_estimate"]
            self.assertLessEqual(ci["lower"], point)
            self.assertGreaterEqual(ci["upper"], point)
            self.assertGreaterEqual(ci["lower"], -1.0)
            self.assertLessEqual(ci["upper"], 1.0)
        self.assertTrue(any("not significance tests" in limitation for limitation in result["limitations"]))

    def test_seed_reproducibility(self):
        first = clean_success_uncertainty(self.rows, self.manifest, resamples=500, seed=17)
        second = clean_success_uncertainty(self.rows, self.manifest, resamples=500, seed=17)
        self.assertEqual(first, second)
        other_seed = clean_success_uncertainty(self.rows, self.manifest, resamples=500, seed=18)
        self.assertNotEqual(first["confidence_intervals"], other_seed["confidence_intervals"])

    def test_rejects_duplicate_and_missing_run(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            clean_success_uncertainty(self.rows + [self.rows[0]], self.manifest, resamples=10)
        with self.assertRaisesRegex(ValueError, "do not cover complete manifest"):
            clean_success_uncertainty(self.rows[:-1], self.manifest, resamples=10)

    def test_rejects_unknown_run_and_manifest_mismatches(self):
        unknown = [dict(row) for row in self.rows]
        unknown[0]["run_id"] = "not-in-manifest"
        with self.assertRaisesRegex(ValueError, "not in the manifest"):
            clean_success_uncertainty(unknown, self.manifest, resamples=10)

        wrong_task = [dict(row) for row in self.rows]
        wrong_task[0]["task_id"] = "wrong-task"
        with self.assertRaisesRegex(ValueError, "task_id does not match"):
            clean_success_uncertainty(wrong_task, self.manifest, resamples=10)

        wrong_mode = [dict(row) for row in self.rows]
        wrong_mode[0]["mode"] = "C" if wrong_mode[0]["mode"] != "C" else "A"
        with self.assertRaisesRegex(ValueError, "mode does not match"):
            clean_success_uncertainty(wrong_mode, self.manifest, resamples=10)

    def test_rejects_malformed_success_values_and_arguments(self):
        bad = [dict(row) for row in self.rows]
        bad[0]["clean_success"] = 1  # bool is required; integers are not accepted.
        with self.assertRaisesRegex(ValueError, "clean_success must be a boolean"):
            clean_success_uncertainty(bad, self.manifest, resamples=10)
        with self.assertRaisesRegex(ValueError, "positive integer"):
            clean_success_uncertainty(self.rows, self.manifest, resamples=0)
        with self.assertRaisesRegex(ValueError, "seed must be an integer"):
            clean_success_uncertainty(self.rows, self.manifest, resamples=10, seed=True)

    def test_rejects_noncanonical_schedule(self):
        incomplete = dict(self.manifest)
        del incomplete["run-00-A-2"]
        with self.assertRaisesRegex(ValueError, r"repeats \[1, 2\]"):
            clean_success_uncertainty(self.rows, incomplete, resamples=10)
        too_few_tasks = {rid: entry for rid, entry in self.manifest.items() if entry["task_id"] != TASKS[-1]}
        with self.assertRaisesRegex(ValueError, "expected 10 task clusters"):
            clean_success_uncertainty(self.rows, too_few_tasks, resamples=10)


if __name__ == "__main__":
    unittest.main()
