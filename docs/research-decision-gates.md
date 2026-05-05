# Research Decision Gates

This document records the implementation-lock decisions for the current transcription recovery work. These are product defaults, not permanent research exclusions.

## Beat And Tempo Default

Decision: use `librosa` first for production-quality beat/tempo analysis.

- Default import mode is `high_accuracy`.
- `high_accuracy` uses `librosa.beat.beat_track` plus onset refinement.
- If `librosa` is unavailable or decoding fails, import degrades to `standard`.
- `standard` remains the deterministic fallback based on energy autocorrelation and a uniform beat grid.
- Essentia remains a research candidate only until it has a local adapter, benchmark data, and packaging evidence.

Rationale: transcription quality is the primary success metric. Beat timing quality affects downstream drum, melodic, and gameplay alignment more than the import speed saved by staying on the standard heuristic path.

## Stem Separation Policy

Decision: ship Demucs as an optional experimental provider, not a mandatory fully supported path.

- Default provider remains `auto`.
- `auto` uses Demucs only when the modelpack and runtime are present.
- If Demucs is absent, normal import must continue using provided stems or mix fallback.
- Demucs modelpack/runtime absence should be reported as capability metadata, not a portable import failure.
- GPU acceleration is first-class when available, but CPU fallback and model-absence safety remain required.

Rationale: Demucs is useful for A/B research and local quality work, but it is too heavy and operationally risky to make required for normal portable import.

## Benchmark Threshold Policy

Decision: CI threshold checks stay warn-only until representative baselines are frozen.

- `benchmarks/thresholds.yml` is the versioned threshold source.
- Strict PR blocking is disabled for now.
- Strict mode requires versioned baselines, role-specific quality thresholds, representative local hardware data, and reviewed synthetic/private quality fixtures.
- After those exist, keep warn-only active for at least 14 days before switching strict failure gates on.

Rationale: artifact collection should start now, but blocking PRs before baselines and fixtures are credible would create noise rather than quality pressure.
