# Piano Benchmark Process

This folder is the home for piano-focused transcription benchmark runs.

## Requirement

Every run must generate static visualizations and those visualizations must be reviewed after the run.

A run is not complete unless all of these files exist in the run output folder:

- `summary.json`
- `report.md`
- `report.html`
- `overall_f1_heatmap.svg`
- `offset_f1_heatmap.svg`
- `pitch_accuracy_heatmap.svg`
- `algorithm_summary.svg`
- `instrument_summary.svg`
- `velocity_mae.svg`
- `duplicate_rate.svg`

No webserver is required. The outputs are designed to be opened directly from disk.

See `PIANO_TRANSCRIPTION_RESEARCH_NOTES.md` for the current research direction and experiment queue.

## Standard run

1. Copy `piano_suite_manifest.template.json` to a real manifest path.
2. Replace the placeholder WAV and MIDI paths with your solo-piano songs or piano stems.
3. Run:

```powershell
py -3 benchmarks/piano/run_piano_regression.py --manifest benchmarks/piano/piano_suite_manifest.json --label baseline
```

This writes a timestamped folder under `benchmarks/piano/runs/` and updates `benchmarks/piano/LATEST_RUN.txt`.

## Focus when reviewing

1. `overall_f1_heatmap.svg`
   Use this to spot broad wins or regressions in exact note recovery.
2. `offset_f1_heatmap.svg`
   Confirm that a model is not cheating with decent onsets but obviously wrong sustains.
3. `pitch_accuracy_heatmap.svg`
   Look for octave doubling, missing inner voices, and chord-voicing drift.
4. `algorithm_summary.svg`
   Compare mean exact-note F1 against note+offset F1. Large gaps usually mean sustain modeling is weak.
5. `velocity_mae.svg`
   Track whether cleaner note boundaries are coming at the cost of flat, unplayable dynamics.
6. `duplicate_rate.svg`
   This is the main visual for double-trigger and note-doubling cleanup.

## Rules

- Do not benchmark against transcription output generated from the same run.
- Do not treat console text alone as sufficient; visual output review is required.
- Do not remove cases from the manifest without noting why in the research tracker.
- Prefer your own piano-only songs and piano stems over synthetic-only fixtures when picking winners.

## Optional filters

Run a subset of algorithms:

```powershell
py -3 benchmarks/piano/run_piano_regression.py --algorithm piano_auto --algorithm piano_pti_clean
```

Run one instrument bucket:

```powershell
py -3 benchmarks/piano/run_piano_regression.py --instrument keys
```
