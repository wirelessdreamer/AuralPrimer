# Drum Transcription Algorithm Rebuild Notes (Implementation-Oriented)

Date written: 2026-03-03  
Goal: enough detail to recreate all drum algorithms from scratch.

## 1. Common contract used by all algorithms

Implement each algorithm class as:

```python
class TranscriptionAlgorithm:
    name: str
    def transcribe(self, stem_path: Path) -> list[DrumEvent]:
        ...
```

Common event type:

```python
@dataclass
class DrumEvent:
    time: float         # seconds
    note: int           # GM MIDI note
    velocity: int       # 1..127
    duration: float = 0.05
```

## 2. Shared preprocessing (all DSP algorithms)

1. Load mono audio (`soundfile` or `librosa`).
2. Resample to `sr=44100` (or `22050` if algorithm expects librosa defaults).
3. Normalize peak to `<= 1.0`.
4. Optional transient emphasis:
   - pre-emphasis `y[n] = x[n] - 0.97*x[n-1]`
   - or high-pass around `30-40 Hz`.
5. Use hop sizes in `256-512` sample range.

## 3. Shared post-processing

### 3.1 Peak picking

- Use local maxima with adaptive threshold:
  - `thr = median(env_window) + k * mad(env_window)` or rolling percentile.
- Enforce refractory window per class:
  - kick/snare/tom: `80-110 ms`
  - hats/cymbals: `45-70 ms`

### 3.2 Note-class to MIDI map (target map)

Use this map to stay compatible with downstream parser:

- Kick -> `36`
- Snare -> `38`
- Closed HH -> `42`
- Open HH -> `46`
- Crash -> `49`
- Ride -> `51`
- High tom -> `50` (or `48`)
- Low tom -> `47` (or `45`)
- Floor tom -> `41` or `43`

### 3.3 De-dup merge

After event generation, sort by `time`, then collapse events within `20-35 ms` if same class.

### 3.4 Velocity mapping

Recommended:

- `v = clip(int(35 + 92 * normalized_peak), 25, 127)`
- boosted classes:
  - kick/snare +8
  - cymbals +4

## 4. Algorithm rebuild recipes

## 4.1 `dsp_bandpass` (baseline)

Simple filterbank + envelope peaks.

Band suggestions:

- Kick: `35-140 Hz`
- Snare body: `140-320 Hz`
- Snare crack: `1.6-4.0 kHz`
- Hats: `5.5-12 kHz`
- Cymbal: `3.5-10 kHz`
- Toms: `70-220 Hz` + harmonic support up to `1.2 kHz`

Steps:

1. Butterworth bandpass each class band.
2. Rectify `abs(band)` and low-pass envelope (`8-20 Hz`).
3. Peak-pick per envelope.
4. Assign classes by maximum normalized envelope at onset.
5. Emit MIDI via class map.

Use this as "always works" fallback, not best quality.

## 4.2 `dsp_bandpass_improved` (strong non-fusion default)

Same base as `dsp_bandpass`, plus:

1. Multi-feature onset strength per class:
   - envelope slope
   - short-window energy jump
   - spectral centroid delta
2. Adaptive threshold by local density (louder sections increase threshold).
3. Class conflict resolver:
   - if kick and bassy tom collide, prefer kick when `<120 Hz` dominates.
   - snare favored when `2-4 kHz` transient is strong.
4. Hi-hat open/closed split:
   - long decay + high-frequency tail -> open (`46`)
   - short decay -> closed (`42`)
5. Cymbal split:
   - broader high-band burst with slower decay -> crash (`49`)
   - periodic/steady high-band near beat pulses -> ride (`51`)

This algorithm should produce expanded kit on many tracks.

## 4.3 `dsp_spectral_flux`

STFT-diff-driven onset detector.

Steps:

1. STFT magnitude (`n_fft=1024` or `2048`, `hop=256/512`).
2. Positive spectral flux:
   - `flux[t] = sum(max(0, mag[:,t]-mag[:,t-1]))`
3. Band-limited flux streams:
   - low, mid, high bands for kick/snare/hat-cym separation.
4. Peak-pick flux envelopes.
5. Classify each peak by per-band flux ratio + centroid.
6. Optional beat snapping (small, `<= 30 ms`) if tempo confidence high.

Good for sharp attacks; can overfire on noisy stems unless thresholds are tuned.

## 4.4 `librosa_superflux`

Librosa-native onset variant.

Suggested recipe:

1. Load with librosa default sample rate.
2. Mel spectrogram -> log power.
3. Onset envelope with superflux-style params:
   - `lag=2`
   - `max_size=3`
4. `librosa.onset.onset_detect(...)` with conservative wait/delta.
5. For each onset, classify using short-frame band energies and decay.
6. Emit mapped notes.

Use as legacy fallback; easy to maintain, medium precision.

## 4.5 `adaptive_beat_grid`

Quantized/core-kit algorithm (stable, but less expressive).

Design intent from behavior:

- Prioritizes robust beat-aligned kick/snare/hat.
- Produces mostly `36/38/42` on difficult stems.

Recipe:

1. Estimate tempo + beat frames from drum stem onset envelope.
2. Build beat subdivision grid (1/8 or 1/16).
3. Detect raw onsets.
4. Snap onsets to nearest subdivision within tolerance (`30-45 ms`).
5. Classify primarily by low/mid/high energy:
   - low -> kick
   - mid transient -> snare
   - high -> hat
6. Suppress rare tom/cym classes unless confidence is very high.

This is your "safe but simplified" mode.

## 4.6 `aural_onset`

Onset-first heuristic algorithm.

Recipe:

1. Broad onset detection (time-domain + spectral novelty blend).
2. Around each onset, extract a short feature vector:
   - low-band RMS
   - high-band RMS
   - spectral centroid
   - zero-crossing or transient sharpness
3. Rule-based classification:
   - low-heavy + sharp -> kick
   - mid transient -> snare
   - high-heavy short -> closed HH
   - high-heavy longer decay -> tom/cym fallback

Usually denser than adaptive, sparser than combined fusion.

## 4.7 `combined_filter` (fusion algorithm, pre-loss preferred default)

This was the key expanded-kit path.

Rebuild as weighted fusion of 2-3 detectors:

Inputs:

- candidate events from `dsp_bandpass_improved`
- candidate events from `dsp_spectral_flux`
- optional support from `aural_onset`

Fusion procedure:

1. Cluster all candidate hits in `+-30 ms` windows.
2. For each cluster, compute class vote score:
   - bandpass_improved vote weight: `1.0`
   - spectral_flux vote weight: `0.8`
   - aural_onset vote weight: `0.6`
3. Add timbral priors from local audio features:
   - low-band dominance boosts kick
   - `2-4 kHz` transient boosts snare
   - `>6 kHz` decay boosts cymbal/hat
4. Select top class if score margin above threshold; otherwise fallback to best base detector class.
5. For hats/cymbals/toms, keep class only if confidence over class-specific floor to avoid noise.
6. Emit final events and run shared de-dup pass.

Expected output profile:

- broader note set (not only core kit)
- retains kick/snare backbone
- more visual lane diversity in gameplay.

## 5. Orchestration and fallback chain to recreate

For request `auto` or `combined_filter`:

1. `combined_filter`
2. `dsp_bandpass_improved`
3. `adaptive_beat_grid`
4. `dsp_spectral_flux`
5. `dsp_bandpass`
6. `aural_onset`

For request `adaptive_beat_grid`:

1. `adaptive_beat_grid`
2. `combined_filter`
3. `dsp_bandpass_improved`
4. `dsp_spectral_flux`
5. `dsp_bandpass`
6. `aural_onset`

For request `aural_onset`:

1. `aural_onset`
2. `combined_filter`
3. `adaptive_beat_grid`
4. `dsp_bandpass_improved`
5. `dsp_spectral_flux`
6. `dsp_bandpass`

Important: do not silently map unknown algorithm names to adaptive without logging; this hid regressions.

## 6. Reference behavior used in debugging (King in Zion)

On same stem:

- `combined_filter` looked like expanded-kit distribution (example notes seen):
  - `36, 38, 41, 42, 46, 47, 49, 50, 51`
- `adaptive_beat_grid` looked core-only:
  - `36, 38, 42`

Use this as a smoke benchmark when rebuilding.

## 7. Minimal regression suite to rebuild now

1. Unit: parser keeps dedicated drum track when melodic tracks are denser.
2. Integration: same stem, compare algorithm note diversity:
   - `combined_filter` must include non-core classes.
   - `adaptive_beat_grid` may remain core-heavy.
3. End-to-end: fixture song (King in Zion) reproduces expanded drum lanes.

## 8. Hard evidence snapshot (captured before project data loss)

### 8.1 Direct algorithm comparison on same stem

Stem used:

- `.../Unknown___Book_of_Psalms___Psalm_2___King_in_Zion.songpack/audio/stems/Drums.wav`

Observed outputs from `transcribe_drums_dsp`:

- `adaptive_beat_grid`:
  - count: `1451`
  - unique notes: `[36, 38, 42]`
  - top distribution: `42:615`, `36:575`, `38:261`
- `combined_filter`:
  - count: `4394`
  - unique notes: `[36, 38, 41, 42, 46, 47, 49, 50, 51]`
  - top distribution: `42:782`, `41:760`, `36:706`, `46:704`, `38:419`
- `dsp_bandpass_improved`:
  - count: `2769`
  - unique notes: `[36, 38, 42, 43, 47, 49, 50, 51]`
- `dsp_spectral_flux`:
  - count: `3366`
  - unique notes: `[36, 38, 41, 42, 46, 47, 49, 50, 51]`

Conclusion: expanded lane loss was consistent with adaptive defaulting, not with combined_filter behavior.

### 8.2 Existing generated songpacks before fix

Across multiple Psalms songpacks (`notes.mid` in portable data), drum channel (ch=9) showed only:

- `[36, 38, 42]`

This matched the visual symptom "kick/snare/hi-hat only".

## 9. Desktop parser behavior that was in-flight pre-loss

### 9.1 Lane map used in parser

- Kick: `35/36 -> BD`
- Snare: `37/38/39/40 -> SD`
- Hi-hat: `42/44/46 -> HH`
- Crash: `49/52/55/57 -> CY`
- Ride: `51/53/59 -> RD`
- Toms: `48/50 -> HT`, `45/47 -> LT`, `41/43 -> FT`

### 9.2 Strict vs relaxed selection heuristic

Parser had two passes:

- strict: only ch=9 or drum-named tracks
- relaxed: all tracks with drum-note mapping

Relaxed was used only when:

- strict had zero notes, or
- relaxed had at least `1.4x` note count and more unique lanes than strict

Intent: recover from split-track MIDIs without letting melodic tracks always dominate drums.

## 10. Known open issue seen during final sanity run

While testing `import-dir` with drums-only input, transcription succeeded and produced expanded notes, but progress log showed:

- `events.json export failed: cannot access local variable 'sections' where it is not associated with a value`

Interpretation: `sections` initialization order bug in `cmd_import_dir` remained and should be fixed next.

## 11. Benchmark harness to use now

Use a real event benchmark instead of lane-diversity smoke checks:

- command: `aural_ingest benchmark-drums <stem.wav> <reference.json|reference.mid>`
- optional: `--algorithm <id>` (repeatable), `--tolerance-ms 60`, `--json`

Benchmark rules:

- reference must be human-authored or otherwise curated; do not benchmark against the transcription's own generated `notes.mid`
- normalize both prediction and reference into gameplay lanes:
  - `kick`, `snare`, `hi-hat`, `crash`, `ride`, `tom1`, `tom2`, `tom3`
- score with one-to-one matching inside tolerance
- report:
  - overall micro precision / recall / F1
  - per-lane precision / recall / F1
  - matched timing MAE in ms
  - confusion pairs (for example `snare -> tom2`)

Reference formats supported by the harness:

- JSON onsets/events with `t` plus `class` or `note`
- MIDI drum chart (`.mid` / `.midi`) using channel 10 or a drum-named track

When debugging the current complaint, inspect these first:

1. snare precision / recall / F1
2. `snare -> tom*` confusions
3. `snare -> crash/ride` confusions
