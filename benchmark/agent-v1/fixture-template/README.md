# ExteraContext agent benchmark fixture

This directory is the isolated target project for one benchmark run.

The fixture intentionally contains no ExteraGram/AyuGram API examples. The tested agent must determine the required API surface from only the resources permitted by the benchmark mode.

Start with:

- `BENCHMARK_MODE.md` — resource/tool restrictions for this run.
- `BENCHMARK_TASK.md` — the task and target metadata.
- `result.schema.json` — required structure for `BENCHMARK_RESULT.json`.

Implement the requested work in this directory. Do not access files outside this run packet except resources explicitly allowed by `BENCHMARK_MODE.md`.
