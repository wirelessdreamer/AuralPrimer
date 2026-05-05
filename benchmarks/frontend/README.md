# Frontend Runtime Benchmarks

These benchmarks cover hot paths called out in `wip.md` section `E6`:

- MIDI parser and chart-selection mapping
- melodic-track extraction
- key-signature inference for dense piano rolls
- visualizer update/render loops for built-in visualizers

Run locally:

```powershell
npm run bench:frontend
```

The latest artifact is written to:

```text
benchmarks/frontend/vitest-bench.latest.json
```

Optional comparison:

```powershell
npm run bench:frontend:compare
```

If `benchmarks/frontend/vitest-bench.baseline.json` exists, the compare command passes it to `vitest bench --compare`. Without a baseline, the command still writes the latest artifact and reports that no comparison baseline was available.

Update the local baseline intentionally:

```powershell
npm run bench:frontend:update-baseline
```

Benchmark numbers are machine-sensitive. Treat them as regression signals, not absolute product metrics.
