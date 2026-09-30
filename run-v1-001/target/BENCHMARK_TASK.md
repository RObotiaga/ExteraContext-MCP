# Benchmark task outgoing-account-lifecycle

Target:

```json
{
  "client": "ExteraGram",
  "platform": "Android",
  "client_version": "12.10.1",
  "sdk_version": "1.4.5.5"
}
```

Task:

Implement a plugin feature that intercepts an outgoing text message before send, preserves the account that triggered the callback, performs non-UI work off the UI thread, returns to the UI safely when UI work is needed, and does not leave duplicate/stale registrations after plugin disable or reload. Use the highest-level supported API available. If any part is not established for this target, state the uncertainty instead of inventing an API.

Run metadata:

- run_id: v1-001
- mode: A
- repeat: 1

Work only inside this target project unless BENCHMARK_MODE.md explicitly permits an external resource.

Create BENCHMARK_RESULT.json in this directory and make it match result.schema.json.
