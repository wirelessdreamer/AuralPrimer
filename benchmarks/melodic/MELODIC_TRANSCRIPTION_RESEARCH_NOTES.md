# Melodic Transcription Research Notes

## Goal

Find the best melodic transcription approach for bass, guitar, and keys/synth, using human-authored MIDI references from Psalms 1-7 as ground truth.

This document is the living research tracker for melodic transcription algorithm evaluation. It mirrors `benchmarks/drums/HYBRID_RESEARCH_AND_TRACKER.md`.

## Benchmark Rule

Every run must follow [PROCESS.md](PROCESS.md). Static output review is mandatory after each run. No webserver is required.

The minimum review set is:

- `overall_f1_heatmap.svg`
- `pitch_accuracy_heatmap.svg`
- `octave_error_heatmap.svg`
- `algorithm_summary.svg`
- `instrument_summary.svg`
- `timing_mae.svg`

## Algorithms Under Test

| Algorithm | Type | Dependencies | Key Idea |
|---|---|---|---|
| `melodic_basic_pitch` | ZCR + dyad | None | Current baseline, zero-crossing rate |
| `melodic_pyin` | ZCR | None | Slightly different ZCR params |
| `melodic_yin` | Autocorrelation | numpy | YIN CMNDF, parabolic interpolation |
| `melodic_onset_yin` | YIN + onset | numpy | YIN + energy-based onset segmentation |
| `melodic_hpss_yin` | HPSS + YIN | librosa | Harmonic preprocessing, then YIN |
| `melodic_fft_hps` | FFT + HPS | None | Harmonic Product Spectrum |
| `melodic_librosa_pyin` | Prob YIN + HMM | librosa | librosa.pyin() with octave-error resistance |

> **Note:** `melodic_crepe` (CREPE neural pitch) is not available on Python 3.13 due to `pkg_resources` removal. Can be re-added when `crepe` package is updated.

## Ground Truth Data

| Psalm | Bass | Guitar | Synth/Keys |
|---|---|---|---|
| 1 — The Road | ✅ | ✅ | ✅ |
| 2 — King in Zion | ✅ | ✅ | ✅ |
| 3 — Shield Me | ✅ | ✅ | — |
| 4 — Trouble Again | ✅ | ✅ | ✅ |
| 5 — Every Morning | ✅ | ✅ | ✅ |
| 6 — Break In | ✅ | ✅ | — |
| 7 — The Chase | ✅ | ✅ | ✅ |

**Total test cases: 19** (7 bass + 7 guitar + 5 synth/keys)

---

## Run 1: Baseline (2026-03-12)

**Run dir:** `benchmarks/melodic/runs/20260312_123751_baseline/`

### Aggregate Results (Mean across 19 songs)

| Algorithm | Mean F1 | Pitch Acc | Octave Err | Best On |
|---|---:|---:|---:|---|
| `melodic_basic_pitch` (ZCR) | ~0.09 | ~10% | ~10% | — |
| `melodic_pyin` (ZCR) | ~0.08 | ~8% | ~7% | — |
| **`melodic_onset_yin`** | **~0.22** | ~17% | ~22% | F1, recall |
| **`melodic_fft_hps`** | **~0.23** | **~22%** | ~16% | Pitch accuracy |
| `melodic_hpss_yin` | ~0.22 | ~19% | ~21% | Precision |
| `melodic_yin` | ~0.19 | ~18% | ~24% | — |
| `melodic_librosa_pyin` | ~0.12 | ~22% | ~30% | Octave errors on some songs |

### Key Findings

1. **All new algorithms 2–5× better than ZCR baseline** on F1 across all 19 test cases
2. **`melodic_onset_yin` and `melodic_fft_hps` are co-leaders** — onset_yin has best recall (note detection), fft_hps has best pitch accuracy (up to 77% on Psalm 4 Bass)
3. **`melodic_hpss_yin` has highest precision** on guitar — HPSS removes percussive transients
4. **Octave errors are the dominant failure mode** for all autocorrelation methods (17–24% rate). FFT+HPS has lowest octave error rate (~16%)
5. **librosa pYIN underperforms** due to `fmin` warning — frame_length=2048 is too short for bass frequencies < 47 Hz
6. **Guitar transcription is hardest** — polyphonic content and rapid passages cause low recall across all algorithms

### Per-Instrument Best F1 (peak per-song values)

| Instrument | Best Algorithm | Best F1 | Song |
|---|---|---:|---|
| Bass | `melodic_fft_hps` | 0.531 | Psalm 2 |
| Bass | `melodic_onset_yin` | 0.520 | Psalm 2 |
| Guitar | `melodic_onset_yin` | 0.314 | Psalm 1 |
| Guitar | `melodic_hpss_yin` | 0.222 | Psalm 7 |
| Keys | `melodic_onset_yin` | 0.371 | Psalm 1 |
| Keys | `melodic_basic_pitch` | 0.339 | Psalm 5 |

---

## Experiment Queue

1. ~~**Baseline** — Run all algorithms on all test cases, capture SVGs~~ ✅ Done
2. **YIN threshold tuning** — Vary `yin_threshold` from 0.10 to 0.25
3. **Frame size optimization** — Test longer frames for bass (80ms+) and increase librosa pYIN frame_length
4. **Onset ratio tuning** — Vary onset energy ratio threshold
5. **HPSS margin** — Test different HPSS margin parameters
6. **Combined approach** — Best onset detector (onset_yin) + best pitch estimator (fft_hps)
7. **Octave error mitigation** — Add octave-checking post-processor to remove systematic doubling
