# Full Transcription Quality Report

Generated: 2026-05-02T16:46:01Z
Profile: gameplay_default
Tolerance: 60.0ms

## Artifacts

- `summary.json`: raw payload plus computed summary
- `f1_heatmap.svg`: case/method F1 matrix
- `gameplay_risk_heatmap.svg`: density, duplicate, sync, and error flag matrix
- `classifier_performance.html`: self-contained classifier explorer, no server required

## Optional Model Backends

| Backend | OK | Methods | Failure Mode |
|---|---:|---|---|
| basic_pitch | True | basic_pitch | portable-safe fallback through melodic fallback chain |
| beatnet | False | - | availability-only until wired as a beat/downbeat prior |
| mt3 | True | mr_mt3_drums, yourmt3_drums | clear FileNotFoundError/RuntimeError, no portable requirement |
| omnizart | False | - | availability-only research comparator; never required for portable import |
| piano_hft | False | piano_hft, piano_hft_clean | clear RuntimeError unless checkpoint and command adapter are configured |
| piano_pti | False | piano_pti, piano_pti_clean | clear RuntimeError unless checkpoint or explicit download opt-in is configured |
| piano_transkun | False | piano_transkun, piano_transkun_clean | clear RuntimeError, then melodic fallback when used through import |
| torchcrepe | True | torchcrepe | clear RuntimeError, then melodic fallback when used through import |

## Algorithm Summary

| Algorithm | Mean F1 | Cases |
|---|---:|---:|
| piano_auto | 0.008 | 1 |

## Promotion Candidates

| Role | Benchmark Winner | Mean F1 | Cases | Flags | Status |
|---|---|---:|---:|---:|---|
| keys | piano_auto | 0.008 | 1 | 1 | blocked_by_gameplay_flags |

Benchmark winners are labels only; `gameplay_default` still requires listening/in-game review.

## Worst Failures

| Case | Role | Algorithm | F1 | Flags |
|---|---|---|---:|---|
| e7a87286c29ac243b523e00932ec1303-keys | keys | piano_auto | 0.008 | density |
