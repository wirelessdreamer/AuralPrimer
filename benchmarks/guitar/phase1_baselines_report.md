# Guitar Phase 1 baselines — production reference numbers

Locks the production baseline for two melodic algorithms
(`melodic_combined` production default; `melodic_combined_guitar`
the recall-tuned variant) on the three signals wired into
`gt-benchmark`. Runs are *measurement only*, no algorithm changes.

**Harness:** `aural_ingest gt-benchmark`, onset tolerance 50 ms,
pitch tolerance 0 semitones (pitch-exact).

**Datasets:**
- `guitar_techs` **directinput** — all 104 electric-guitar phrases,
  24-bit DI recording, categories chords / scales / singlenotes /
  techniques / music.
- `guitar_techs` **micamp** — **deferred to Phase 1.5.** The
  amp-mic signal predicts (unlike DI, see below) but micamp phrases
  average 160+ s of audio each and ``melodic_combined`` runs at
  ~1:1 real-time on this box, so a full-104 run × 2 algorithms
  needs 90+ minutes. Attempted a 41-case stratified sample and a
  10-case P1 chords slice; both overran the 60-minute time budget
  on this pass. Stratified sampler + planned caps are committed
  as ``gt_runs/_stratified_micamp.py`` so the Phase 1.5 rerun is
  push-button. GuitarSet mic (below) still provides an isolated
  guitar reference; the electric micamp story lands next pass.
- `guitarset` **mic** — first 40 of the 360 GuitarSet mic phrases
  (acoustic control / regression guard). Sampled for the same
  budget reason; full 360-case run at ~8.6 s/case × 2 algorithms
  ≈ 103 minutes.

## Overall numbers (locked)

| Dataset | Signal | Algorithm | Cases | F1 | Precision | Recall | Onset MAE (s) | Runtime (s/case) |
|---|---|---|---:|---:|---:|---:|---:|---:|
| guitar_techs | directinput | `melodic_combined` | 104 | 0.000 | 0.000 | 0.000 |   -    | 0.264 |
| guitar_techs | directinput | `melodic_combined_guitar` | 104 | 0.000 | 0.000 | 0.000 |   -    | 0.022 |
| guitarset | mic | `melodic_combined` | 40 | 0.222 | 0.345 | 0.163 | 0.019 | 11.197 |
| guitarset | mic | `melodic_combined_guitar` | 40 | 0.227 | 0.301 | 0.182 | 0.020 | 11.166 |

## Guitar-TECHS — per-category breakdown

| Signal | Algorithm | Category | Cases | F1 | Precision | Recall | Onset MAE (s) |
|---|---|---|---:|---:|---:|---:|---:|
| directinput | `melodic_combined` | chords | 56 | 0.000 | 0.000 | 0.000 |   -    |
| directinput | `melodic_combined` | scales | 24 | 0.000 | 0.000 | 0.000 |   -    |
| directinput | `melodic_combined` | singlenotes | 2 | 0.000 | 0.000 | 0.000 |   -    |
| directinput | `melodic_combined` | techniques | 10 | 0.000 | 0.000 | 0.000 |   -    |
| directinput | `melodic_combined` | music | 12 | 0.000 | 0.000 | 0.000 |   -    |
| directinput | `melodic_combined_guitar` | chords | 56 | 0.000 | 0.000 | 0.000 |   -    |
| directinput | `melodic_combined_guitar` | scales | 24 | 0.000 | 0.000 | 0.000 |   -    |
| directinput | `melodic_combined_guitar` | singlenotes | 2 | 0.000 | 0.000 | 0.000 |   -    |
| directinput | `melodic_combined_guitar` | techniques | 10 | 0.000 | 0.000 | 0.000 |   -    |
| directinput | `melodic_combined_guitar` | music | 12 | 0.000 | 0.000 | 0.000 |   -    |

## GuitarSet mic (acoustic control)

| Algorithm | Style | Cases | F1 | Precision | Recall | Onset MAE (s) |
|---|---|---:|---:|---:|---:|---:|
| `melodic_combined` | overall | 40 | 0.222 | 0.345 | 0.163 | 0.019 |
| `melodic_combined` | comp | 20 | 0.096 | 0.183 | 0.065 | 0.019 |
| `melodic_combined` | solo | 20 | 0.528 | 0.569 | 0.493 | 0.020 |
| `melodic_combined_guitar` | overall | 40 | 0.227 | 0.301 | 0.182 | 0.020 |
| `melodic_combined_guitar` | comp | 20 | 0.106 | 0.165 | 0.078 | 0.022 |
| `melodic_combined_guitar` | solo | 20 | 0.521 | 0.510 | 0.531 | 0.019 |

## Interpretation

### (a) Winner by dataset

- **Electric DI (guitar_techs directinput):** tie at F1 0.000.
- **Electric amp-mic (guitar_techs micamp):** deferred to Phase 1.5 (see dataset notes above).
- **Acoustic (guitarset mic):** `melodic_combined_guitar` wins F1 0.227 vs `melodic_combined` 0.222 (Δ=+0.005).

### (b) Hardest bucket

On the acoustic **guitarset mic** with `melodic_combined`:
- **Hardest:** comp (rhythm/chord) — F1 0.096 (precision 0.183, recall 0.065).
- **Easiest:** solo (lead/solo) — F1 0.528.
- **Ratio:** rhythm/chord phrases are ~5.5× harder than solo lead. This is the
  same chord-vs-lead pattern the epic tracker identifies as
  the highest-leverage Phase-4 target (rhythm chord
  supplement, mirroring piano's 0.82→0.93 jump).

### (c) The DI blocker — a 24-bit-WAV bug in the mono reader

The DI baseline is **F1 = 0.000 across every category and both
algorithms**. All 208 runs produced *zero predictions* (tp = 0,
fp = 0, fn = every reference note). This is not an algorithm
failure — it is a reader failure that silently upstream-affects
every melodic algorithm in the suite.

**Root cause:** ``aural_ingest.algorithms._common``:

- Guitar-TECHS ``directinput`` files are **24-bit mono PCM WAV**
  (``sampwidth = 3``).
- ``read_wav_mono_normalized`` calls ``_lin2lin(raw, sampwidth,
  2)`` to convert to 16-bit.
- ``_lin2lin`` only supports ``sampwidth`` ∈ {1, 2, 4} — a 3-byte
  input raises ``ValueError('unsupported sample widths: 3 -> 2')``.
- The exception is swallowed by the outer ``try/except``
  wrapping the whole reader, which returns ``([], 0)``.
- ``melodic_combined.transcribe`` sees empty samples and short-
  circuits to ``return []`` — a clean, silent zero-output.

Confirmed by direct probe: on Guitar-TECHS DI ``P1_chords/
Drop3_7.wav`` (24-bit, 204 s, RMS 0.014), ``melodic_combined``
returns 0 notes; on the paired **micamp** file (16-bit, same
phrase, RMS 0.016), it returns 273 notes.

**Blast radius:** every algorithm that goes through
``read_wav_mono_normalized`` — this includes ``melodic_pyin``,
``melodic_onset_yin``, ``melodic_fft_hps``, ``melodic_yin``, the
guitar/piano wrappers, and the drum energy paths. Any user pack
with 24-bit source stems silently transcribes to empty MIDI.

**Phase 1.5 fix:** extend ``_lin2lin`` to handle ``sampwidth = 3``
(24-bit int → shift-left one byte then reduce), or route through
``soundfile.read`` which handles all common widths natively.
Only after that fix does the DI 0.000 baseline become a
meaningful reference for Phase 2 gains to be measured against.

### Baseline is now locked

Reference numbers are frozen at these values. Every Phase 2+
proposal (Basic Pitch wrapper, gated cleanup, chord supplement)
must be scored against this baseline; regressions on the
dominant metric (F1) are not permitted per the discipline rules
in the epic tracker.

Raw run JSON dumps live under `benchmarks/guitar/gt_runs/`:

- `benchmarks/guitar/gt_runs/gt_directinput_full.json`
- `benchmarks/guitar/gt_runs/guitarset_mic_limit40.json`
