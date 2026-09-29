# Benchmark mode C — ExteraContext MCP (protocol 1.1)

You are participating in a controlled plugin-development benchmark.

Use only the benchmark MCP instance configured by the operator. That instance must use the frozen Knowledge commit:

`70f6f614227c8b02d241e7b1e72a0b6691442fd1`

Do not use the repository's current `KNOWLEDGE_LOCK` if it points to a newer corpus.

Do not directly read the Knowledge repository, SQLite databases, query.py, previous benchmark answers, or web search for ExteraGram/AyuGram API facts.

Decompose multi-part technical questions into focused MCP retrievals. Preserve target, donor, version and runtime-evidence boundaries. Do not fabricate an API to make the implementation look complete.

At the end create `BENCHMARK_RESULT.json` matching the supplied result schema and set `protocol_revision` to `1.1`. Record every non-local ExteraGram/AyuGram API symbol you relied on and its status.

Do not report a test as `pass` unless you provide reproducible evidence in that test entry: either a command the evaluator can rerun or an artifact/log path that exists in the run workspace.
