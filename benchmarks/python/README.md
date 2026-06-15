# Python Ingest Runtime Benchmarks

Run from the repo root:

```powershell
npm run bench:python
npm run bench:python:update-baseline
```

The current shard is an opt-in `pytest-benchmark` harness for decode, beat/section, chart decode, and memory-footprint smoke coverage. It writes:

- `benchmarks/python/pytest-benchmark.latest.json`
- `benchmarks/python/ingest-memory.latest.json`

The benchmark is intentionally artifact-first. Threshold enforcement is controlled by `benchmarks/thresholds.yml`; keep it in `warn` mode until representative baselines are frozen.
