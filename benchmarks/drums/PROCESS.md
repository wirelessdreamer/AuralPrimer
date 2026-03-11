# Drum Benchmark Process

This folder is the home for drum transcription shootout runs.

## Requirement

Every benchmark run must generate static visualizations and those visualizations must be reviewed after the run.

A run is not complete unless all of these files exist in the run output folder:

- `summary.json`
- `report.md`
- `report.html`
- `overall_f1_heatmap.svg`
- `kick_f1_heatmap.svg`
- `snare_f1_heatmap.svg`
- `hi_hat_f1_heatmap.svg`
- `algorithm_summary.svg`
- `core_lane_summary.svg`
- `timing_mae.svg`
- `snare_confusion_heatmap.svg`
- `hi_hat_confusion_heatmap.svg`

No webserver is required. The outputs are designed to be opened directly from disk.

See `benchmarks/drums/HYBRID_RESEARCH_AND_TRACKER.md` for the current hybrid-research direction and the longer-term experiment queue.

## Fixture source

- rendered audio + MIDI references: `assets/test_fixtures/drum_benchmark_midis`
- suite runner: `python/ingest/scripts/run_drum_benchmark_suite.py`

## Standard run

```powershell
py -3 python/ingest/scripts/run_drum_benchmark_suite.py --label baseline
```

This writes a timestamped folder under `benchmarks/drums/runs/` and updates `benchmarks/drums/LATEST_RUN.txt`.

## Focus when reviewing

1. `overall_f1_heatmap.svg`
   Use this to spot broad regressions or wins across the full fixture set.
2. `kick_f1_heatmap.svg`
   Treat this as mandatory review for double-bass, gallop, and syncopated pocket stability.
3. `snare_f1_heatmap.svg`
   Treat this as mandatory review because snare classification remains a primary complaint.
4. `hi_hat_f1_heatmap.svg`
   Treat this as mandatory review because hats are currently the weakest lane and are collapsing into snare or kick.
5. `algorithm_summary.svg`
   Compare mean overall F1 against mean core-lane F1; broad gains that hide kick, snare, or hi-hat regressions are not acceptable.
6. `core_lane_summary.svg`
   Use this to compare `kick`, `snare`, and `hi-hat` directly across algorithms.
7. `timing_mae.svg`
   Confirm that an F1 gain is not coming from worse onset timing.
8. `snare_confusion_heatmap.svg`
   Look for `snare -> tom*` and `snare -> cymbal` failure patterns.
9. `hi_hat_confusion_heatmap.svg`
   Look for `hi_hat -> snare` and `hi_hat -> kick` collapse patterns.

## Rules

- Do not benchmark against generated transcription output from the same run.
- Do not treat console text alone as sufficient; visual output review is required.
- Do not remove cases from the manifest without regenerating the fixture pack.
- If a fixture is added or removed, rerun `python/ingest/scripts/generate_drum_benchmark_midis.py`.

## Optional filters

Run a subset of algorithms:

```powershell
py -3 python/ingest/scripts/run_drum_benchmark_suite.py --algorithm combined_filter --algorithm dsp_bandpass_improved
```

Run a subset of cases:

```powershell
py -3 python/ingest/scripts/run_drum_benchmark_suite.py --case 05_metal_double_bass_190 --case 10_rnb_pocket_88
```
