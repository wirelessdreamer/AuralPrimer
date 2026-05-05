# Performance Baselines

The project owner clarified that "anything remotely modern" should be supported. The concrete baseline is now:

- `minimum_modern`: 8 logical CPU threads, 16 GB RAM, x64/x86_64/arm64/aarch64, no GPU required for default import or playback.
- `recommended_model_workstation`: 12 logical CPU threads, 32 GB RAM, GPU recommended for model-backed transcription A/B work.

Run `npm run bench:hardware` to write `benchmarks/hardware/local-profile.latest.json`. CI benchmark jobs also capture this profile and upload it with benchmark artifacts.

GPU acceleration is first-class for model-backed transcription when available, but CPU fallback and model-absence safety remain required for portable import.

Thresholds are defined in `benchmarks/thresholds.yml`. They remain warn-only until representative baselines, role-specific quality thresholds, hardware profiles, and reviewed fixtures are frozen.
