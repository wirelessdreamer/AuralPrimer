# Benchmark Hardware Profiles

Capture the current machine profile before or alongside benchmark runs:

```powershell
npm run bench:hardware
```

The command writes `benchmarks/hardware/local-profile.latest.json`.

## Concrete Baselines

`minimum_modern` is the support floor for perf gates:

- 8 logical CPU threads
- 16 GB RAM
- x64/x86_64/arm64/aarch64
- no GPU required for default import/playback

`recommended_model_workstation` is the preferred local A/B environment for model-backed transcription:

- 12 logical CPU threads
- 32 GB RAM
- GPU acceleration recommended for heavy model-backed transcription
- CPU fallback still required

The threshold checker reads `benchmarks/thresholds.yml`. It remains warn-only until representative baselines and strict thresholds are frozen.
