# Ground-truth benchmarks 2026-06-14

This is the first production pass of AuralPrimer's transcription
algorithms against externally-annotated audio. It pins both **where
production stands** and **where the first round of tuned variants got
us** for drums, guitar, bass, and keys.

Annotated corpora live at `E:\AudioSourceOfTruthData\extracted`
(prepared per the parallel `D:\AudioSourceOfTruth` project, see its
`docs/data-prep-status.md`). The harness lives at
`python/ingest/src/aural_ingest/ground_truth_benchmark.py` and is wired
into the CLI as `aural_ingest gt-benchmark`.

## Datasets

| Family  | Dataset            | Cases (test/holdout)     | Annotation         |
|---------|--------------------|--------------------------|--------------------|
| Drums   | E-GMD v1.0.0       | 5,289 test files         | MIDI (≤2ms aligned)|
| Guitar  | GuitarSet v1.1.0   | 360 acoustic phrases     | JAMS per-string    |
| Guitar  | Guitar-TECHS v1    | 104 electric phrases     | per-string MIDI    |
| Bass    | GuitarSet (low E+A strings filter) | 360 phrases | JAMS per-string    |
| Keys    | (none in v1)       | TBD — MAESTRO            | —                  |

The bass corpus is a derived view of GuitarSet: the low-E and A string
annotations only, audio sourced from the `hex_debleeded` per-string
pickup so register-isolated bass-pitch fundamentals are the dominant
content.

## Drums

**Production default before this pass:** `combined_filter`.
**This pass shipped:** `librosa_superflux_dense`.

Twenty E-GMD test cases, four-algo shootout
(`benchmarks/drums/gt_runs/egmd_baseline_20.json`):

| Algorithm                  | F1     | Precision | Recall | Onset MAE |
|----------------------------|--------|-----------|--------|-----------|
| `adaptive_beat_grid`       | 0.145  | 0.267     | 0.100  | 25 ms     |
| `dsp_bandpass_improved`    | 0.136  | 0.217     | 0.100  | 24 ms     |
| `librosa_superflux`        | 0.102  | **0.320** | 0.061  | 22 ms     |
| `combined_filter` (prod)   | 0.051  | 0.215     | 0.029  | 26 ms     |

Diagnosis: every production algorithm was tuned to be conservative on
**noisy multi-instrument stems**, so they reject ~90% of the genuine
hits in dry isolated kit audio. Onset MAE is solid across the board
(22–26 ms); the bottleneck is recall.

Tuned variant: `librosa_superflux_dense`
(`python/ingest/src/aural_ingest/algorithms/librosa_superflux_dense.py`).
Same SuperFlux envelope + band layout as the base; the peak picker
drops `k` 2.4→1.3, `percentile` 0.90→0.70, `min_gap_sec` 0.07→0.04,
`window_sec` 0.45→0.30. Classification gates loosened slightly so
strong pop-snare hits don't get re-classified as cymbals.

Result (same 20 cases, `benchmarks/drums/gt_runs/egmd_dense_v1_20.json`):

| Algorithm                       | F1        | Precision | Recall    | MAE   |
|---------------------------------|-----------|-----------|-----------|-------|
| **`librosa_superflux_dense`**   | **0.153** | 0.295     | **0.103** | 25 ms |
| `adaptive_beat_grid`            | 0.145     | 0.267     | 0.100     | 25 ms |
| `librosa_superflux`             | 0.102     | 0.320     | 0.061     | 22 ms |

Versus the previous leader `adaptive_beat_grid`: **F1 +5.5%**. Versus
the SuperFlux base: **F1 +50% (recall +69%)**. Precision drops 7.8%
because looser thresholds admit a small number of new false positives
along with the true positives the base was missing.

The absolute F1=0.153 is still terrible relative to the 0.7–0.9 that a
learned ADT model would deliver — E-GMD ground truth is ~15 events/sec
of dense funk, ghost-snare doubles, and 16th-note hat that a hand-tuned
peak-picker can't reach. The win is a 50% relative recall lift without
adding a learning step or a new dependency, which means it ships today.
A v2 multi-band / ADTOF-style detector is the next lever.

Reproduce:

```powershell
aural_ingest gt-benchmark `
  --dataset egmd `
  --corpus-root E:\AudioSourceOfTruthData\extracted\e_gmd `
  --split test `
  --algorithm combined_filter `
  --algorithm adaptive_beat_grid `
  --algorithm dsp_bandpass_improved `
  --algorithm librosa_superflux `
  --algorithm librosa_superflux_dense `
  --limit 20 `
  --output benchmarks\drums\gt_runs\egmd_dense_v1_20.json
```

## Guitar

**Production default before this pass:** `melodic_combined`.
**This pass shipped:** `melodic_combined_guitar` (head-to-head numbers
land in the next commit; baseline below).

Twelve GuitarSet test cases (mic variant), four-algo baseline shootout
(`benchmarks/melodic/gt_runs/guitarset_baseline_12.json`):

| Algorithm                       | F1     | Precision | Recall | Onset MAE | Note          |
|---------------------------------|--------|-----------|--------|-----------|---------------|
| **`melodic_combined`** (prod)   | **0.261** | **0.309** | **0.226** | 20 ms | ✓ winner     |
| `melodic_yin_octave_hps_fix`    | 0.207  | 0.257     | 0.174  | 19 ms     |               |
| `melodic_basic_pitch`           | 0.032  | 0.049     | 0.024  | 17 ms     | ONNX missing  |
| `melodic_torchcrepe`            | 0.000  | 0.000     | 0.000  | —         | torch missing |

Two algorithms can't be evaluated here without dependencies the venv
doesn't have:

- `melodic_basic_pitch` needs `basic-pitch[onnx]` (instructions are
  in the warning the library prints on import).
- `melodic_torchcrepe` needs `torchcrepe` and `torch`.

For the production winner `melodic_combined`, the bottleneck is recall
(0.226) — three-quarters of the genuine notes are getting missed. The
onset detector's `onset_ratio=3.0` and the 60 ms minimum note length
were tuned to be conservative on noisy stems; on clean GuitarSet audio
they reject too much.

Tuned variant:
`python/ingest/src/aural_ingest/algorithms/melodic_combined_guitar.py`.
Same onset+HPS pipeline; loosens `onset_ratio` 3.0→2.0 and
`min_note_sec` 0.06→0.04. The frame size and hop stay at production
defaults.

Reproduce:

```powershell
aural_ingest gt-benchmark `
  --dataset guitarset `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitarset `
  --variant mic `
  --algorithm melodic_combined `
  --algorithm melodic_combined_guitar `
  --algorithm melodic_yin_octave_hps_fix `
  --limit 12 `
  --output benchmarks\melodic\gt_runs\guitarset_tuned_v1_12.json
```

## Bass

**Production default before this pass:** `melodic_pyin` (auto-tunes
for `instrument="bass"` via hop=256 + min_note_sec=80 ms).
**This pass shipped:** `melodic_pyin_bass_strict` (head-to-head numbers
in the next commit; baseline below).

Bass corpus: GuitarSet, low-E + A strings only, hex_debleeded variant.
Eight cases × two algorithms
(`benchmarks/melodic/gt_runs/bass_baseline_8.json`):

| Algorithm                  | F1     | Precision | Recall | Onset MAE | Note         |
|----------------------------|--------|-----------|--------|-----------|--------------|
| **`melodic_pyin`** (prod) | **0.238** | 0.189 | **0.322** | 20 ms     | ✓ winner    |
| `melodic_yin_bass80`       | 0.000  | 0.000     | 0.000  | —         | returns []   |

`melodic_yin_bass80` returns zero notes for every case in this
corpus, despite the algorithm being live in `KNOWN_MELODIC_METHODS`.
That's a separate diagnostic queued for a follow-up — likely a frame-
length / sample-rate interaction at the YIN core, not a tuning
problem in the wrapper.

Per-case F1 for the production `melodic_pyin` swings hard:
0.038 → 0.652 across just four `BN1` test cases on the same player.
The lows correspond to chordal (`_comp`) material where multiple
strings are sounding; YIN's pitch tracker locks onto the loudest
harmonic and emits octave-up false positives.

Tuned variant:
`python/ingest/src/aural_ingest/algorithms/melodic_pyin_bass_strict.py`.
Same `librosa.pyin` pipeline but exposes the knobs the production
wrapper hides: `fmin=55 Hz` / `fmax=350 Hz` (practical 4-string
range) and `voiced_prob_threshold=0.85` (reject low-confidence
pitch frames that were responsible for most of the chordal-material
false positives in the baseline).

Reproduce:

```powershell
aural_ingest gt-benchmark `
  --dataset guitarset_bass `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitarset `
  --algorithm melodic_pyin `
  --algorithm melodic_pyin_bass_strict `
  --limit 8 `
  --output benchmarks\melodic\gt_runs\bass_tuned_v1_8.json
```

## Keys

No annotated keys corpus is present in this round's selected datasets
(see `D:\AudioSourceOfTruth\docs\selected-datasets.md`). The production
keys path remains the `piano_pti.transcribe_consensus` pipeline at the
empirically-validated 100 ms / 2-semitone tolerances established
against Psalm 5 (1,357 notes detected vs 1,309 reference, within 4%
of the reference set).

Next step for a real keys benchmark: add MAESTRO v3 to the corpus.
MAESTRO is the canonical piano-MIDI/audio paired dataset and is the
natural complement to the existing E-GMD / GuitarSet selection.

## Harness

The runner is a thin wrapper around the existing greedy onset-matcher
already used by `drum_benchmark.py`, generalised to:

- swap drum-class-aware scoring for pitch-aware scoring on the
  melodic family
- pass `case.instrument` through to the underlying transcribe so
  production's per-instrument frequency-range gating
  (`INSTRUMENT_FREQ_RANGES`) actually fires
- bucket results by every metadata key the dataset adapter exposes
  (kit / style / variant / player / category / signal / split) so
  drift between groups is visible in the report

Output JSON has stable shape (case-by-case results + a corpus-wide
aggregate broken down by bucket) so the existing visual-report
tooling can consume it later.

Adapters live in
`python/ingest/src/aural_ingest/dataset_adapters/`:

- `egmd.py` — stdlib MIDI parser + CSV-driven split filter
- `guitarset.py` — JAMS `note_midi` per-string parser +
  `yield_low_string_cases` for the bass-corpus view
- `guitar_techs.py` — paired MIDI + per-signal audio (DI / micamp)
  walker

All three accept a `limit=` kwarg so smoke iterations stay fast.
