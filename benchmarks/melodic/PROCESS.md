# Melodic Benchmark Process

This folder is the home for melodic transcription benchmark runs.

## Requirement

Every benchmark run must generate static visualizations and those visualizations must be reviewed after the run.

A run is not complete unless all of these files exist in the run output folder:

- `summary.json`
- `report.md`
- `report.html`
- `overall_f1_heatmap.svg`
- `pitch_accuracy_heatmap.svg`
- `algorithm_summary.svg`
- `instrument_summary.svg`
- `timing_mae.svg`
- `octave_error_heatmap.svg`

No webserver is required. The outputs are designed to be opened directly from disk.

See `MELODIC_TRANSCRIPTION_RESEARCH_NOTES.md` for the current research direction and experiment queue.

## Standard run

```powershell
py -3 benchmarks/melodic/run_melodic_regression.py --label baseline
```

This writes a timestamped folder under `benchmarks/melodic/runs/` and updates `benchmarks/melodic/LATEST_RUN.txt`.

## Focus when reviewing

1. `overall_f1_heatmap.svg`
   Use this to spot broad regressions or wins across the full song/instrument set.
2. `pitch_accuracy_heatmap.svg`
   Fraction of matched notes with correct pitch (within ±1 semitone). This isolates pitch estimation quality from onset detection.
3. `octave_error_heatmap.svg`
   Look for systematic octave doubling/halving. This is the signature failure mode of autocorrelation-based methods.
4. `algorithm_summary.svg`
   Compare mean overall F1 against mean pitch accuracy; algorithms with high F1 but low pitch accuracy are overfitting to onset density.
5. `instrument_summary.svg`
   Per-instrument means broken down by bass, guitar, keys.
6. `timing_mae.svg`
   Confirm that an F1 gain is not coming from worse onset timing.

## Rules

- Do not benchmark against generated transcription output from the same run.
- Do not treat console text alone as sufficient; visual output review is required.
- Do not remove songs from the manifest without noting it in the research tracker.

## Optional filters

Run a subset of algorithms:

```powershell
py -3 benchmarks/melodic/run_melodic_regression.py --algorithm melodic_yin --algorithm melodic_onset_yin
```

Run a single instrument:

```powershell
py -3 benchmarks/melodic/run_melodic_regression.py --instrument bass
```
