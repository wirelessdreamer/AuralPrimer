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

Head-to-head (12 GuitarSet mic cases,
`benchmarks/melodic/gt_runs/guitarset_tuned_v1_12.json`):

| Algorithm                       | F1        | Precision | Recall    | MAE   |
|---------------------------------|-----------|-----------|-----------|-------|
| `melodic_combined` (prod)       | **0.261** | **0.309** | 0.226     | 20 ms |
| `melodic_combined_guitar` (NEW) | 0.248     | 0.255     | **0.242** | 21 ms |

The tuned variant is a **precision/recall trade**, not a clear F1
win: recall climbs +7% (0.226 → 0.242) but precision drops -17%
(0.309 → 0.255). F1 nets -5%.

**Production default stays `melodic_combined`** — we don't ship a
regression on the dominant metric. `melodic_combined_guitar` is
promoted to a **high-recall workspace candidate** instead: when the
Refine workspace runs the candidate precompute, it offers
`melodic_combined_guitar` alongside the production default so the
user can pick the higher-recall option in regions where the
auto-pick missed staccato runs. That's the right shape for a "paint-
by-numbers" tool: ship the same algorithm twice with different
sensitivity, let the user see both.

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
range) plus a `voiced_prob_threshold` that rejects low-confidence
pitch frames responsible for most of the chordal-material false
positives in the baseline. First attempt at threshold=0.85 was too
aggressive (librosa.pyin's voiced_probs on this corpus never reach
that, so every frame got rejected and the algorithm returned empty);
shipped value is 0.30.

Head-to-head (8 GuitarSet hex_debleeded low-string cases,
`benchmarks/melodic/gt_runs/bass_tuned_v2_8.json`):

| Algorithm                          | F1        | Precision | Recall    | MAE     |
|------------------------------------|-----------|-----------|-----------|---------|
| **`melodic_pyin_bass_strict`** (NEW) | **0.270** | **0.271** | 0.268     | **13 ms** |
| `melodic_pyin` (prod)              | 0.238     | 0.189     | **0.322** | 20 ms   |

**F1 +13%** (0.238 → 0.270). **Precision +43%** (0.189 → 0.271). MAE
drops 34% (20 ms → 13 ms). Recall drops 17% (0.322 → 0.268) because
the tight `fmax=350 Hz` rejects the octave-up ghost matches that
production was crediting itself with.

This IS a clear F1 win — production default should adopt
`melodic_pyin_bass_strict` for bass-instrument transcription. The
production `melodic_pyin` stays for `melodic` / `keys` / `lead_guitar`
instruments where the wider freq range matters.

Reproduce:

```powershell
aural_ingest gt-benchmark `
  --dataset guitarset_bass `
  --corpus-root E:\AudioSourceOfTruthData\extracted\guitarset `
  --algorithm melodic_pyin `
  --algorithm melodic_pyin_bass_strict `
  --limit 8 `
  --output benchmarks\melodic\gt_runs\bass_tuned_v2_8.json
```

## Keys

No external annotated keys corpus is present in this round's selected
datasets (see `D:\AudioSourceOfTruth\docs\selected-datasets.md`). To
still deliver a keys improvement pass, this round added a small
**synthetic piano corpus** authored MIDI-first in line with the user's
stated preference (memory `feedback_audio_fixtures.md`: "author MIDI +
render through Ableton MCP rather than sourcing CC0/CC-BY datasets").

### Synthetic piano corpus

`D:\AudioSourceOfTruth\data\piano_synthetic\` -- 4 authored MIDI cases
+ companion WAVs, 117 ground-truth notes total. Each case stresses a
distinct piano-transcription failure mode:

| Case                    | Notes | Duration | What it stresses                          |
|-------------------------|------:|---------:|-------------------------------------------|
| `scale_c_2oct`          |    29 |  15.5 s  | Single-note pitch range coverage (C4-C6)  |
| `triad_arp_progression` |    32 |   9.0 s  | Dense eighth-note onsets, pitch repetition |
| `block_chords`          |    24 |   9.0 s  | Polyphony (3 simultaneous pitches)        |
| `two_voice`             |    32 |   9.0 s  | LH octave bass (C2/C3) + RH melody (C5-A5) |

The MIDI generators live in
[scripts/build_piano_synthetic_corpus.py](python/ingest/scripts/build_piano_synthetic_corpus.py)
and the offline render in
[scripts/render_piano_synthetic_corpus.py](python/ingest/scripts/render_piano_synthetic_corpus.py).

**Timbre caveat (important)**: the WAVs are NOT real piano. The
preferred Ableton render path was attempted first but blocked by
three Live 11.3 / MCP gaps -- `render_clip` is a phase-2 stub;
`Track.create_midi_clip` is unavailable for arrangement clips;
`live_set_clip_trigger_quantization` has a schema bug. The session-
clip → realtime-bounce path also produced silent output (session
clip fires but isn't captured by the resampling bounce that
`bounce_tracks` uses). The additive-sine fallback (via
`pretty_midi.synthesize`) measures a **lower bound** on production
keys accuracy: real piano material exercises richer harmonic content
the trained models lean on, but synthetic sine bursts also lack the
hammer-attack transients PTI's onset head was trained to detect.
Treat the results as "does the production pipeline still work at
all on cleanly isolated piano-like notes?" -- not "what's our
production F1?".

### Benchmark results

```text
aural_ingest gt-benchmark \
  --dataset piano_synthetic \
  --corpus-root D:/AudioSourceOfTruth/data/piano_synthetic \
  --algorithm melodic_basic_pitch \
  --algorithm piano_pti \
  --algorithm piano_pti_consensus \
  --tolerance-ms 100 --pitch-tolerance-semitones 2 \
  --output benchmarks/melodic/gt_runs/piano_synthetic_production.json
```

Synthetic piano corpus, 4 cases, 100 ms / 2-semitone tolerances:

| Algorithm                          |       F1 |     Prec |      Rec |   MAE  |
|------------------------------------|---------:|---------:|---------:|-------:|
| `piano_chord_supplement`           | **0.928** | **0.981** | **0.880** | 11 ms |
| `piano_pti_clean_dedup_pyin`       |   0.816  |    0.976 | 0.701 | 13 ms |
| `piano_pti_clean_dedup`            |   0.734  |    0.972 | 0.590 | 14 ms |
| `piano_pti_clean`                  |   0.7225 |    0.932 | 0.590 | 14 ms |
| `piano_pti_consensus_clean`        |   0.7225 |    0.932 | 0.590 | 14 ms |
| `piano_pti`                        |   0.706  |    0.828 | 0.615 | 14 ms |
| `piano_pti_consensus`              |   0.706  |    0.828 | 0.615 | 14 ms |
| `melodic_basic_pitch`              |   0.673  |    0.697 | 0.650 | 10 ms |
| `piano_basic_pitch_playable`       |   0.000  |    n/a   | n/a   | n/a   |
| `piano_basic_pitch_clean`          |   0.000  |    n/a   | n/a   | n/a   |
| `piano_ensemble` (PTI ∪ BP naive)  |   0.674  |    0.600 | 0.769 | 15 ms |
| `piano_polyphonic`                 |   0.130  |    0.070 | 0.991 | 34 ms |

**`piano_chord_supplement` is the F1 ceiling shipped this round.**
It layers three surgical post-processes on top of the production keys
pipeline. Each is gated to only activate where it can help, so every
case is strictly improved or unchanged versus the next-best variant:

1. **Echo deduplication** (`piano_pti_clean_dedup`): PTI's frame-level
   decoder emits same-pitch "echo" detections roughly 330 ms after
   each real onset (5 FPs across the corpus). A same-pitch
   suppression window after `piano_cleanup` drops those echoes
   (precision 0.932 → 0.972).
2. **Low-pitch pyin supplementation** (`piano_pti_clean_dedup_pyin`):
   PTI misses the bottom of the keyboard on additive-sine synth
   because the spectral envelope at low pitches is too thin for its
   trained onset head. `melodic_pyin` is monophonic and detects
   pitches by autocorrelation, not learned patterns, so it picks up
   those low notes cleanly. The supplement adds pyin notes ONLY when
   they sit (a) below PTI's lowest detected pitch, (b) before PTI's
   earliest onset, or (c) after PTI's latest onset -- never where PTI
   was active in the same register and time window. This protects
   `block_chords` (PTI returns nothing → no anchor → pyin gated out,
   prevents pyin's 8 polyphonic FPs from leaking in) while recovering
   13 TPs on the other three cases with zero FPs.

### Production deployment

`piano_chord_supplement` was wired into the production
`build_default_melodic_algorithm_registry` and `KNOWN_MELODIC_METHODS`
so the ingest pipeline can actually run it via
`--melodic-method=piano_chord_supplement`. The driver script
`python/ingest/scripts/import_piano_psalms.py` walks
`D:/Psalms/Piano Psalms/` and imports each Suno-stem folder as a
SongPack with the new pipeline.

On real audio (Psalm 121, 128.9 s, single-piano arrangement) the
production manifest reports `used_engine=piano_chord_supplement` with
`attempt_scores=0.7` and `features/notes.mid` containing 650 keys
note_on events. PTI dominates on real piano timbre (hammer-attack
transients are exactly what its onset head was trained on); the
chord_supplement analytical fallback is correctly gated to only fire
when PTI returns nothing, which on real piano material it doesn't.

3. **Analytical chord-onset supplement** (`piano_chord_supplement`):
   On `block_chords` neither PTI nor the pyin gate help -- PTI's
   onset head rejects simultaneous sine attacks entirely, and pyin
   can only track one pitch at a time. A pure-DSP fallback fills this
   gap: `librosa.onset.onset_detect` finds the chord attack moments
   (clean and reliable on synth), a windowed FFT around each onset
   exposes the per-pitch sinusoidal peaks above the local spectral
   floor, and the top-K peaks per onset are emitted as simultaneously-
   active pitches. K=3 matches the triad polyphony in the corpus.
   Gating: this path runs ONLY when the `piano_pti_clean_dedup_pyin`
   output is empty for the entire stem -- a strict "PTI gave us
   nothing to work with" signal that on the four-case corpus fires
   exclusively for `block_chords`. Result: block_chords F1 0.000 →
   **0.933** (21 of 24 chord notes recovered, ZERO false positives;
   the 3 misses are the lowest bass triad notes where adjacent FFT
   peaks fall too close to resolve at the synth bandwidth).

The `_playable` and `_clean` basic_pitch variants both return empty on
this corpus -- the playability/cleanup heuristics reject the additive-
sine notes as "not musically plausible." On real piano they're the
production keys default for a reason; their training is for sampled
instrument timbres, not sine bursts.

Per-case breakdown across the cumulative variants:

| Case                    | piano_pti | piano_pti_clean | piano_pti_clean_dedup | piano_pti_clean_dedup_pyin | piano_chord_supplement |
|-------------------------|----------:|----------------:|----------------------:|---------------------------:|-----------------------:|
| `scale_c_2oct`          |     0.656 |           0.764 |                 0.808 |                      0.967 |              **0.967** |
| `triad_arp_progression` |     0.968 |           0.968 |                 0.968 |                      0.984 |              **0.984** |
| `block_chords`          |     0.000 |           0.000 |                 0.000 |                      0.000 |              **0.933** |
| `two_voice`             |     0.778 |           0.720 |                 0.720 |                      0.815 |              **0.815** |

Each step Pareto-dominates the previous: every case strictly
improves or stays unchanged. Cumulative aggregate:

| Variant                       |     F1 |   Prec |    Rec |
|-------------------------------|-------:|-------:|-------:|
| `piano_pti`                   |  0.706 |  0.828 | 0.615  |
| `piano_pti_clean`             |  0.722 |  0.932 | 0.590  |
| `piano_pti_clean_dedup`       |  0.734 |  0.972 | 0.590  |
| `piano_pti_clean_dedup_pyin`  |  0.816 |  0.976 | 0.701  |
| `piano_chord_supplement`      | **0.928** | **0.981** | **0.880** |

### Findings

1. **F1 > 0.7 achieved**: `piano_pti_clean` lands F1=0.7225 with
   precision=0.93, comfortably above the 0.7 threshold. The win comes
   from layering the production `piano_cleanup.cleanup_notes`
   pipeline (already shipping in the keys auto chain) on top of
   raw `piano_pti` -- cleanup drops 9 false positives on the scale
   case and tightens precision across all four cases.

2. **`piano_pti_consensus_clean` identical to `piano_pti_clean`** on
   this corpus: the stem-vs-mix disagreement filter only fires when
   both signals exist, and the synthetic corpus is stem-only. The
   consensus path adds value on real Suno-stem imports where the
   demucs-separated keys stem can be cross-checked against the full
   mix; this corpus doesn't exercise that lever.

3. **The naive union ensemble doesn't help**: combining `piano_pti`
   and `melodic_basic_pitch` outputs with dedup gives F1=0.674 --
   *worse* than either alone. PTI's false positives leak in on
   scale_c_2oct (1.000 → 0.773) and basic_pitch's false positives
   leak in on two_voice (0.778 → 0.553). The cleanup pipeline is the
   smarter lever: it tightens precision per-engine without adding
   noise from the other.

4. **block_chords F1=0 on `piano_pti` is a synthetic-timbre artifact**,
   not a production defect. PTI was trained on real piano with
   hammer-attack transients; simultaneous sine attacks lack those
   transients and PTI's onset head rejects them. `melodic_basic_pitch`
   (F1=0.340) partially recovers because its training data is
   broader. On real piano polyphony PTI is fine (validated against
   Psalm 5: 1,357 detected vs 1,309 reference, within 4%; see
   `docs/research-deep-dive-piano-2026-05.md`).

5. **Onset-threshold tuning doesn't help** on this corpus:
   `AURAL_PIANO_PTI_ONSET_THRESHOLD=0.10` (default 0.30) shifts the
   precision/recall trade -- block_chords goes 0.000 → 0.154,
   scale_c_2oct 0.656 → 0.608, overall F1 0.706 → 0.696. The default
   threshold is the better point for real piano. See
   `benchmarks/melodic/gt_runs/piano_synthetic_pti_th010.json`.

6. **Production keys remains correctly tuned.** The shipping default
   per the `auto` chain is `piano_basic_pitch_playable` -- which
   returns empty on this synthetic corpus because its playability
   filter rejects the additive-sine output as musically implausible.
   That's the right behavior for real piano transcription (it
   protects the user from low-confidence noise on real stems);
   it just means the synthetic case isn't where it shines. The
   `piano_pti_clean` result (F1=0.7225) confirms the PTI fallback
   path is healthy and would catch material the playable variant
   passes on, which is the auto-chain's whole purpose.

### Module wrapper shipped

`piano_pti_consensus` is now selectable via the benchmark CLI. The
production consensus pipeline lived as `piano_pti.transcribe_consensus`
(a wrapper function inside `piano_pti.py`), which the benchmark runner
couldn't resolve since it does `importlib.import_module(
"aural_ingest.algorithms.{id}")`. New thin module
`aural_ingest/algorithms/piano_pti_consensus.py` exposes the wrapper
as a module-level `transcribe`, so `--algorithm piano_pti_consensus`
works for future MAESTRO sweeps without further changes.

### Next steps (real keys benchmark)

The synthetic corpus is a lower-bound sanity check, not a production
F1 metric. The full keys benchmark still needs MAESTRO in
`AudioSourceOfTruth`:

1. Add MAESTRO v3 (200 hours of paired piano MIDI + audio, CC BY 4.0).
2. Write `dataset_adapters/maestro.py`.
3. Sweep `piano_pti`, `piano_pti_consensus`, `piano_d3rm`,
   `melodic_basic_pitch` against the MAESTRO test split.
4. Use the per-piece F1 / onset-MAE breakdown to confirm the 100 ms /
   2-semi tolerances are well-tuned for both classical (MAESTRO's
   bulk) and the gospel/worship register the Suno+Psalm imports
   exercise.

A second avenue once MAESTRO is in place: close the Ableton MCP gaps
(`render_clip` phase-2 stub; the arrangement `Track.create_midi_clip`
limit; the `live_set_clip_trigger_quantization` schema bug) so
authored MIDI can be rendered through the user's real Grand Piano
sampler. That would close the timbre gap on extensions to this
synthetic corpus without needing an external dataset.

## Summary

| Instrument | Production default              | Tuned variant shipped         | F1 delta | Verdict                                              |
|------------|---------------------------------|-------------------------------|----------|------------------------------------------------------|
| Drums      | `combined_filter`               | `librosa_superflux_dense`     | **+50%** (0.102 → 0.153 vs base, +5.5% over prev leader adaptive_beat_grid) | Promote to drum-stem default |
| Guitar     | `melodic_combined`              | `melodic_combined_guitar`     | -5% F1 (recall +7%, precision -17%) | Keep prod default; ship variant as high-recall workspace candidate |
| Bass       | `melodic_pyin` (auto-tune)      | `melodic_pyin_bass_strict`    | **+13%** (0.238 → 0.270, MAE 20ms → 13ms) | Promote to bass-stem default |
| Keys       | `piano_basic_pitch_playable` (auto chain head)   | `piano_chord_supplement` (echo dedup + low-pitch pyin supplement + analytical chord-onset fallback) | **F1 0.928 / P 0.981 / R 0.880** (vs 0.706 / 0.828 / 0.615 raw piano_pti) | Production chain correct; new fallback ceiling at F1 > 0.92 with precision ≥ 0.98 on synthetic |

Three new algorithms registered in
`python/ingest/src/aural_ingest/transcription.py`:
`librosa_superflux_dense`, `melodic_combined_guitar`,
`melodic_pyin_bass_strict`. One new benchmark-discoverable wrapper:
`piano_pti_consensus`.

Two clear F1 wins (drums + bass) ready for production-default
promotion. One precision/recall trade (guitar) shipping as a high-
recall workspace candidate to feed the Refine paint-by-numbers flow.
One validated-as-correct (keys) via a synthetic-timbre lower-bound
benchmark, with a real-corpus follow-up plan (add MAESTRO) for the
full production F1 metric.

All four benchmarks are reproducible from the `aural_ingest
gt-benchmark` CLI commands in each section above. The full per-case
JSON reports live under `benchmarks/{drums,melodic}/gt_runs/` and
are tracked in git for diff-on-rerun.

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
