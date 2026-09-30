"""Descriptive task-cluster bootstrap intervals for benchmark protocol 1.1.

The intervals are descriptive small-sample summaries, not significance tests
and not evidence of causal effects or model superiority.
"""
from __future__ import annotations

import random
import statistics
from typing import Any, Iterable, Mapping

_MODES = ("A", "B", "C")
_TASK_CLUSTERS = 10
_REPEATS_PER_MODE = 2


def _validate_schedule(run_manifest: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, dict[str, Any]], tuple[str, ...]]:
    if not isinstance(run_manifest, Mapping) or not run_manifest:
        raise ValueError("run_manifest must be a non-empty run_id mapping")

    schedule: dict[str, dict[str, Any]] = {}
    tasks: set[str] = set()
    cells: dict[tuple[str, str], list[int]] = {}
    for run_id, raw in run_manifest.items():
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("manifest run_id keys must be non-empty strings")
        if not isinstance(raw, Mapping):
            raise ValueError(f"manifest entry {run_id!r} must be a mapping")
        task_id, mode = raw.get("task_id"), raw.get("mode")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError(f"manifest entry {run_id!r} has invalid task_id")
        if mode not in _MODES:
            raise ValueError(f"manifest entry {run_id!r} has invalid mode")
        repeat = raw.get("repeat")
        if type(repeat) is not int or repeat < 1:
            raise ValueError(f"manifest entry {run_id!r} has invalid repeat")
        schedule[run_id] = {"task_id": task_id, "mode": mode, "repeat": repeat}
        tasks.add(task_id)
        cells.setdefault((task_id, mode), []).append(repeat)

    if len(tasks) != _TASK_CLUSTERS:
        raise ValueError(f"expected {_TASK_CLUSTERS} task clusters, got {len(tasks)}")
    if {mode for _, mode in cells} != set(_MODES):
        raise ValueError("manifest must contain all modes A, B, and C")
    expected_repeats = list(range(1, _REPEATS_PER_MODE + 1))
    for task_id in tasks:
        for mode in _MODES:
            if sorted(cells.get((task_id, mode), [])) != expected_repeats:
                raise ValueError(
                    f"task {task_id!r}, mode {mode} must have repeats {expected_repeats} exactly once"
                )
    return schedule, tuple(sorted(tasks))


def _percentile(sorted_values: list[float], probability: float) -> float:
    """Return a linearly interpolated percentile from sorted bootstrap values."""
    position = (len(sorted_values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction


def clean_success_uncertainty(
    rows: Iterable[Mapping[str, Any]],
    run_manifest: Mapping[str, Mapping[str, Any]],
    *,
    resamples: int = 10000,
    seed: int = 20260728,
) -> dict[str, Any]:
    """Compute mode rates and paired task-cluster percentile bootstrap intervals.

    Every manifest run must appear exactly once. Each bootstrap replicate samples
    the 10 task IDs with replacement once and uses that same sample for A, B,
    and C. Repeats for a selected task/mode stay together, preserving pairing.
    """
    if type(resamples) is not int or resamples < 1:
        raise ValueError("resamples must be a positive integer")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")

    schedule, task_ids = _validate_schedule(run_manifest)
    try:
        assessments = list(rows)
    except TypeError as exc:
        raise ValueError("rows must be an iterable of assessment mappings") from exc
    if any(not isinstance(row, Mapping) for row in assessments):
        raise ValueError("every assessment row must be a mapping")

    seen: set[str] = set()
    outcomes: dict[str, dict[str, dict[int, bool]]] = {
        task_id: {mode: {} for mode in _MODES} for task_id in task_ids
    }
    for row in assessments:
        run_id = row.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("each assessment row requires a non-empty run_id")
        if run_id in seen:
            raise ValueError(f"duplicate assessment run_id: {run_id}")
        seen.add(run_id)
        expected = schedule.get(run_id)
        if expected is None:
            raise ValueError(f"assessment run_id is not in the manifest: {run_id}")
        if row.get("task_id") != expected["task_id"]:
            raise ValueError(f"{run_id}: task_id does not match manifest")
        if row.get("mode") != expected["mode"]:
            raise ValueError(f"{run_id}: mode does not match manifest")
        if type(row.get("clean_success")) is not bool:
            raise ValueError(f"{run_id}: clean_success must be a boolean")
        outcomes[expected["task_id"]][expected["mode"]][expected["repeat"]] = row["clean_success"]

    missing = set(schedule) - seen
    if missing:
        raise ValueError(f"assessment rows do not cover complete manifest; missing {len(missing)} run(s)")

    task_rates = {
        task_id: {
            mode: statistics.mean(outcomes[task_id][mode].values())
            for mode in _MODES
        }
        for task_id in task_ids
    }
    point_estimates = {
        mode: statistics.mean(task_rates[task_id][mode] for task_id in task_ids)
        for mode in _MODES
    }
    contrasts = {"A-B": ("A", "B"), "A-C": ("A", "C"), "B-C": ("B", "C")}

    rng = random.Random(seed)
    mode_samples = {mode: [] for mode in _MODES}
    contrast_samples = {name: [] for name in contrasts}
    for _ in range(resamples):
        # One cluster draw shared across all modes preserves the paired design.
        sampled_tasks = [task_ids[rng.randrange(len(task_ids))] for _ in task_ids]
        rates = {
            mode: statistics.mean(task_rates[task_id][mode] for task_id in sampled_tasks)
            for mode in _MODES
        }
        for mode in _MODES:
            mode_samples[mode].append(rates[mode])
        for name, (left, right) in contrasts.items():
            contrast_samples[name].append(rates[left] - rates[right])

    def interval(values: list[float]) -> dict[str, float]:
        ordered = sorted(values)
        return {"lower": _percentile(ordered, 0.025), "upper": _percentile(ordered, 0.975)}

    return {
        "method": "paired task-cluster percentile bootstrap",
        "estimand": "descriptive clean_success rate (mean task-level rate; repeats kept within task)",
        "task_clusters": len(task_ids),
        "repeats_per_mode_per_task": _REPEATS_PER_MODE,
        "resamples": resamples,
        "seed": seed,
        "confidence_level": 0.95,
        "limitations": [
            "Only 10 task clusters are available; percentile intervals may be unstable in this small sample.",
            "Intervals are descriptive, not significance tests and not claims of superiority.",
            "Interpretation is conditional on the frozen tasks, schedule, and assessment rubric.",
        ],
        "point_estimates": point_estimates,
        "confidence_intervals": {mode: interval(mode_samples[mode]) for mode in _MODES},
        "paired_differences": {
            name: {
                "point_estimate": point_estimates[left] - point_estimates[right],
                "confidence_interval_95": interval(contrast_samples[name]),
                "contrast": f"{left} minus {right}",
            }
            for name, (left, right) in contrasts.items()
        },
    }


__all__ = ["clean_success_uncertainty"]
