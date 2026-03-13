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

---

## Run 2: Experiment (2026-03-12)

**Run dir:** `benchmarks/melodic/runs/20260312_192359_experiment-2/`

5 new algorithm variants tested alongside the 7 baselines:

| Variant | Key Change | Result |
|---|---|---|
| `melodic_yin_t020` | YIN threshold 0.20 (stricter voicing) | Marginal improvement over 0.15 default |
| `melodic_yin_bass80` | 80ms frames for bass | **F1=0.598 on Ps2 Bass, 91.5% pitch acc on Ps4 Bass** |
| **`melodic_combined`** | Onset detection + HPS pitch + YIN fallback | 🏆 **New overall leader — wins 16+/19 songs** |
| `melodic_hpss_onset` | HPSS + onset-aware YIN | Strong 2nd — wins on some guitar songs |
| `melodic_pyin_long` | librosa pYIN w/ 4096 frame_length | Improves bass but high octave errors |

### Key Findings

1. **`melodic_combined` is the new overall F1 leader** — onset detection from onset_yin + HPS pitch estimation + YIN fallback gives best of both worlds
2. **`melodic_yin_bass80` achieves 91.5% pitch accuracy** on Ps4 Bass — 80ms frames are critical for bass fundamental detection
3. **Combined approach wins 16+/19 songs** with best F1 range 0.17–0.54 across all instruments
4. **Octave errors drop to ~10–15%** for combined (vs 22–30% for single-method approaches)
5. **`melodic_hpss_onset` is the best single-approach** — HPSS + onset detection consistently 2nd place
6. **Guitar remains the hardest instrument** — best F1 ~0.27 on guitar vs ~0.54 on bass

### Per-Instrument Best F1 (peak per-song values)

| Instrument | Best Algorithm | Best F1 | Song |
|---|---|---:|---|
| Bass | `melodic_yin_bass80` | 0.598 | Psalm 2 |
| Bass | `melodic_combined` | 0.540 | Psalm 2 |
| Bass | `melodic_combined` | 0.508 | Psalm 1 |
| Guitar | `melodic_hpss_onset` | 0.362 | Psalm 1 |
| Guitar | `melodic_combined` | 0.334 | Psalm 1 |
| Keys | `melodic_hpss_onset` | 0.372 | Psalm 1 |
| Keys | `melodic_basic_pitch` | 0.339 | Psalm 5 |

---

## Run 3: Experiment (2026-03-13)

**Run dir:** `benchmarks/melodic/runs/20260313_061007_experiment-3/`

3 new algorithm variants tested alongside the 12 existing:

| Variant | Key Change | Result |
|---|---|---|
| `melodic_octave_fix` | Post-processing octave correction + median filter on `melodic_combined` | 🏆 **Tied leader F1=0.295, lowest octave errors (17.2%)** |
| `melodic_adaptive` | Instrument-specific frame sizes (80ms bass, 50ms guitar, 60ms keys) | Strong 4th (F1=0.272), best on some instruments |
| `melodic_hpss_combined` | HPSS preprocessing + combined pitch estimation | Close 3rd (F1=0.290), lowest octave errors on some songs |

### Aggregate Results

| Algorithm | Mean F1 | Pitch Acc | Octave Err | Timing MAE |
|---|---:|---:|---:|---:|
| **`melodic_combined`** | **0.295** | 17.4% | 17.6% | 31.9ms |
| **`melodic_octave_fix`** | **0.295** | 19.2% | **17.2%** | 31.9ms |
| `melodic_hpss_combined` | 0.290 | 16.8% | **16.7%** | 32.3ms |
| `melodic_adaptive` | 0.272 | 19.1% | 19.8% | 32.2ms |
| `melodic_hpss_onset` | 0.265 | 16.5% | 20.5% | 32.7ms |
| `melodic_fft_hps` | 0.250 | **20.2%** | 17.4% | 32.1ms |

### Key Findings

1. **Octave fix is the safest overall** — same F1 as combined, fewer octave errors
2. **HPSS combined has lowest octave errors** (16.7%) — harmonic separation reduces octave confusion
3. **Adaptive frames help bass** (Ps4: 0.317, Ps2: 0.518) but don't lift guitar/keys enough
4. **Guitar remains hardest** — best peak F1 ~0.28 across all algorithms
5. **Pitch accuracy still low** (~17–22%) — fundamental limitation of current single-pass approaches

---

## Experiment Queue

1. ~~**Baseline** — Run all algorithms on all test cases~~ ✅ Done
2. ~~**YIN threshold tuning** — `yin_threshold=0.20`~~ ✅ Marginal gain
3. ~~**Frame size optimization** — 80ms frames for bass, 4096 pYIN~~ ✅ Major bass improvement
4. ~~**Combined approach** — Onset + HPS pitch + YIN fallback~~ ✅ **New leader**
5. ~~**HPSS + onset** — HPSS preprocessing + onset detection~~ ✅ Strong 2nd
6. ~~**Octave error mitigation** — Post-processor to fix systematic octave doubling~~ ✅ Tied leader
7. ~~**Adaptive frame size** — Instrument-aware frame_sec~~ ✅ Helps bass
8. ~~**HPSS + combined** — HPSS preprocessing + combined pitch estimation~~ ✅ Close 3rd
9. **Template multi-pass** — Learn instrument spectral profile, then re-estimate pitch with song-specific parameters (inspired by drum spectral template approach)
10. **Template + octave fix** — Template multi-pass with octave correction post-processing
11. ~~**YIN-octave HPS-pitch hybrid** — YIN for octave determination, HPS for fine pitch~~ ✅ New F1 leader on bass
12. ~~**Hybrid + octave fix** — YIN-octave HPS-pitch + octave correction post-processing~~ ✅ Best pitch accuracy + octave errors

---

## Run 4: YIN-Octave HPS-Pitch Hybrid (2026-03-13)

### Concept

Use each pitch method where it's strongest: YIN (autocorrelation) determines the correct **octave** — because autocorrelation finds the waveform period which corresponds to the fundamental. HPS (FFT) provides the fine **pitch class** within that octave — because FFT has better frequency resolution.

When YIN and HPS disagree by exactly 12 semitones, trust YIN's octave + HPS's chroma. When they agree, use HPS. Auto-uses 80ms frames for bass.

### Bass Benchmark Results (7 Psalms)

| Song | `yin_octave_hps_fix` F1 | `combined` F1 | Fix PitchAcc | Fix OctErr |
|---|---:|---:|---:|---:|
| **Ps1** | **0.522** ✅ | 0.508 | **53.3%** | **0.0%** |
| Ps2 | 0.522 | **0.540** | 0.8% | 40.2% |
| **Ps3** | **0.273** ✅ | 0.243 | **39.8%** | **9.5%** |
| **Ps4** | **0.342** ✅ | 0.306 | 40.8% | 32.8% |
| Ps5 | 0.284 | **0.308** | **57.6%** | **1.3%** |
| **Ps6** | **0.428** ✅ | 0.405 | **50.0%** | **7.9%** |
| Ps7 | 0.210 | **0.213** | **24.6%** | **17.1%** |

### Key Findings

1. **Hybrid wins F1 on 4/7 bass songs** and has better precision on 6/7
2. **Pitch accuracy dramatically better**: avg ~38% with fix vs ~3% raw hybrid (vs ~19% combined)
3. **Octave errors reduced**: avg ~15.5% with fix vs ~29% raw hybrid
4. **Ps1 breakthrough**: 53.3% pitch accuracy, 0.0% octave errors — best ever on this song
5. **Fix helps 5/7, hurts 1/7** (Ps4 where raw hybrid already had 4% octave errors)
6. **Ps2 remains hard**: 40% octave errors — both YIN and HPS agree on wrong octave (harmonics genuinely louder than fundamental in this recording)


