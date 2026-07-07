# Research: real meter + downbeat tracking (2026-07-05)

**Status: research complete, adversarially verified — ready for implementation handoff.**
**Recommendation: adopt Beat This! (CPJKU, MIT code+weights) as an optional modelpack-gated
beat/downbeat engine, with a mandatory drum-event cross-check and the librosa path as
fallback. One load-bearing question (Psalm 4's metrical level) is OPEN and needs a
10-minute ear-check before the postprocess is designed — see §5.**

---

## 1. Problem

Meter is currently hardcoded, not detected:

- `cli.py _assign_bar_positions(beats_per_bar=4)` (cli.py:652) counts beats
  `0,1,2,3,0,…` from whatever beat librosa found first → **arbitrary downbeat phase**.
- `cli.py _generate_tempo_map(…, time_signature="4/4")` (cli.py:589) → **every pack says 4/4**.
- librosa `beat_track` returns beat **times only** — it has no concept of meter.

Measured consequences on real packs (diagnostics in this session, methodology:
nearest drum-stem onset via `librosa.onset.onset_detect`):

| Song | Meter | Beats vs drum onsets | Bar structure |
|---|---|---|---|
| Beat It (4/4) | correct pulse | ~15–23 ms median (good) | downbeat phase 2 beats off — accent lands on beat 3; pack bars smeared across all 4 phases [33,33,32,49] |
| Psalm 4 "trouble again" (6/8) | wrong | ~35 ms median, gaps 0.22–0.61 s (shaky) | grouped in 4s (structurally wrong); tempo ambiguous: pack says 139.675, fresh librosa says 103.4 |

Also: **both** shipped packs carry the identical bpm `139.675` — a fingerprint of
librosa's discretized tempo-lag bins; the tempo scalar is untrustworthy and should be
derived from the final beat sequence instead.

A related editor-side finding from this session (context for whoever implements):
**do not regularize beat times to a fixed tempo.** An even-grid experiment drifted
46–81 ms off the audio (growing over the song) while raw tracked beats sat at 12–23 ms.
Real tracks breathe; the grid must follow the tracked beats. Meter = *grouping + phase*
on top of tracked beats, never a resampling of them.

## 2. Candidate survey (licenses verified against primary sources)

| Candidate | Code | Weights | Verdict |
|---|---|---|---|
| **Beat This!** (CPJKU, ISMIR 2024) | MIT | **MIT** (README verbatim: "The code and the published model weights are released under the MIT license") | **ADOPT** |
| madmom DBNDownBeat | BSD-2 | **CC BY-NC-SA 4.0** ("You must not use the material for commercial purposes"; "pickled Processors (i.e. saved models) fall into this category") | **REJECT** — ADTOF-class license failure; also unmaintained (last release 2018, py≤3.7, breaks on numpy≥1.24) |
| BeatNet | CC BY 4.0 | CC BY 4.0 (repo-wide) | Shippable but second-choice: online-focused, drags in madmom+pyaudio, offline mode is a madmom-DBN decode — strictly worse than Beat This! offline |
| All-In-One (T. Kim) | MIT | MIT | Reject on integration: NATTEN compiled from source on Windows + madmom + demucs |
| WaveBeat | GPL-3.0 | — | Reject (license) |
| essentia | AGPL-3.0 | — | Reject (license; already named in BEAT_TEMPO_PRODUCTION_POLICY as research candidate — close that gate) |
| BEAST (ICASSP 2024) | — | no public weights story | Watch only |
| "Skip That Beat" (arXiv 2502.12972) | — | — | Not a tracker; a data-augmentation recipe for underrepresented meters. The fine-tuning lever if compound-meter grouping disappoints |

License nuances that matter:
- Beat This! weights are MIT **from the copyright holder**; training data includes
  copyrighted audio and the README shifts that assessment to users. This is the
  industry-standard posture (NOT the ADTOF situation where weights themselves were NC).
  A one-time legal skim is prudent; record it in the license log.
- Beat This!'s optional `--dbn` postprocessor imports madmom. **Ship `dbn=False` only.**
  (Fine print: madmom's DBN *decoder class* is BSD code that loads no NC models — but
  the install is broken on py3.11/numpy≥1.24 anyway. Avoid entirely.)

## 3. Empirical results (run in this session, on the real packs)

Environment verified: beat_this 1.1.0 runs **in the actual sidecar venv**
(Python 3.11.9, torch 2.11.0+cu126, numpy 1.26.4) CPU-only, and byte-identically in a
fresh venv on torch 2.12.1+cpu with numpy 1.26.4. New dependencies: **only `beat_this` +
`rotary-embedding-torch`** (pure-Python MIT wheels; einops/torchaudio/soxr/soundfile are
already shipped — torchaudio is already in `aural_ingest.spec` COLLECT_PACKAGES).
Checkpoint `final0.ckpt` = 81 MB, auto-downloads from JKU's own cloud
(cloud.cp.jku.at → `~/.cache/torch/hub/checkpoints/`) but accepts a plain local path —
**vendor it in a modelpack, don't runtime-download**. CPU inference ~8–15 s per 4-min
song (RTF 0.03–0.06), peak RAM ~0.6–1.1 GB. Model output is quantized to a 20 ms frame
grid (50 fps) — bars/metronome fine; the existing onset-snap lever covers precision.

Repro:
```
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install git+https://github.com/CPJKU/beat_this.git
python -c "from beat_this.inference import File2Beats; f2b=File2Beats(checkpoint_path='final0', device='cpu', dbn=False); print(f2b('mix.wav'))"
```
(Input in tests: peak-normalized mono sum of the pack's stems. Note the import-time
stage has only the original mix — see Open Question Q2.)

**Beat It (4/4): clean win, high confidence.**
- 4/4 confirmed: 142/149 bars have exactly 4 beats; tempo ~136–139 (level agrees with pack).
- Beats ~3x tighter than the shipped grid vs drum onsets (7.9 ms vs 23.2 ms median,
  same-methodology side-by-side; a second track measured 29.9 vs 46.4 with different
  onset params — the *relative* improvement reproduces, absolute numbers are
  methodology-dependent).
- **Downbeat phase: 146/150 predicted downbeats land exactly 2 beats from the pack's**
  — i.e. it puts "the one" where the user's accent complaint says it should be. This is
  independently corroborated by a dependency-free analysis (kick/snare histogram voting
  on a fitted beat lattice picks the same phase: kick [103,48,79,10], snare [2,107,4,102]
  mod-4 — textbook kick-on-1/3 + snare-backbeat). Strongest result in the research.

**Psalm 4 (6/8): large improvement, one open dispute.**
- Beats: 7.0 ms median vs pack's 23.2 ms (3.3x tighter).
- Grouping: modal bar = 4 of its beats (106/126) — a 12/8-style rendering, **no
  3-grouping found**. If the song is 6/8, bars must be halved (which half carries the
  true "1" is undetermined — Psalm 4's phase is unverified by any method; the
  dependency-free phase voting explicitly fell below confidence there).
- ~29% of inter-beat intervals tracked at double-time (0.29 s vs 0.58 s) in
  identifiable sections (e.g. 29.6–34 s, 239–242 s) → occasional 5/6/9-beat bars.
  Whether these are genuine double-time drum feels or model wobble **needs a listen**.
  This matches the paper's admitted weakness (low continuity, occasional non-periodic
  beats) — the bar-assignment layer must tolerate it (mode-filter bar lengths, don't
  blindly increment measures on every detected downbeat).

## 4. The dependency-free track (validated, keep it — as the cross-check)

A pure numpy/librosa analysis using signals the pipeline already has (mr_mt3 drum
events, Demucs stems) was prototyped on both songs. Verified findings:

- **Naive mod-m voting on raw librosa beat indices fails** (near-flat profiles,
  phase flips): one dropped beat permanently scrambles all downstream indices. Fix:
  fit a rigid lattice to tracked beats (re-indexed least squares, ~15 lines numpy)
  *when residuals are small* (Beat It p95 = 35 ms → works; Psalm 4 p95 = 201 ms →
  global lattice invalid, needs windowed analysis; naive window-chaining measurably
  corrupted results — don't).
- **Drum-event autocorrelation solves the tempo-level class of problem**: kick+snare
  impulse trains give a clean tatum comb; testing whether ACF mass sits on 3-tatum vs
  4-tatum multiples resolves 139.7-vs-103.4-style ambiguity from drum events alone.
- Chroma/harmonic-change (the classical downbeat tie-breaker) came out **flat** on
  riff-based rock — weight it near zero.
- Literature calibration: classical meter+downbeat heuristics sit at ~70–85%
  (Klapuri 2006 ~69/78%; Gouyon-Herrera duple/triple 81–95%; Krebs/Böck neural
  77.3% downbeat F with detected beats — and their error analysis says phase errors
  are rare; failures are metrical-level and time-signature errors, exactly what the
  drum-ACF attacks).

## 5. RESOLVED (ear-check, 2026-07-05): Psalm 4's metrical level = 103.4 bpm

**Outcome: the metronome was ear-checked in the Studio on the refreshed packs and
feels spot-on on BOTH Beat It and "trouble again".** So Beat This!'s 103.4 bpm
tactus is the correct level and the accent/downbeat placement is right; the
drum-event ACF heuristic's 139.7 argument (below) was the wrong metrical level.
The tempo-level ambiguity is settled for these songs — do not re-open it. The
only remaining, non-blocking sub-question is the notation *denominator* label for
compound feels (v1 writes "N/4", `meter_denominator_provisional:true`); it does
not affect the metronome or bar lines (which use the beat/downbeat structure) and
can be decided later without re-tracking.

Original open-question analysis (kept for context):

The research tracks **disagreed** about the same song, and this had to be adjudicated by
ear before the level-consistency postprocess is designed — otherwise the postprocess
bakes in the wrong side:

- **Beat This! + fresh librosa**: pulse = 0.58 s (103.4 bpm), i.e. 2 hi-hat units per
  beat; calls the pack's 139.7 a 4:3 metrical-level error.
- **Drum-event ACF**: tatum comb at 0.143 s; kick-to-kick interval mode 0.425 s
  (3 tatums, favored 79:17 over 4-tatum 0.575 s) → concludes 139.7 (= 3-tatum
  grouping, compound feel) is the *correct* dotted-pulse level and 103.4 the wrong one.
- Note neither 139.7 nor 103.4 maps to the natural 6/8 levels if the eighth is the
  0.29 s hi-hat unit (that would put the dotted quarter at ~0.86 s ≈ 70 bpm — a level
  *neither* tracker emitted). The kick evidence (0.425 s spacing) is consistent with a
  *fast* 6/8 whose eighth is the 0.143 s tatum instead.

**Adjudication protocol** (uses the metronome shipped this session): in the Studio
cleanup editor, play Psalm 4 with the grid metronome on at each candidate level
(139.7-beats grid = current pack; 103.4 grid = Beat This! output, raw beats in the
session scratchpad `beat_this_raw_beats.json`) and judge which clicks match the felt
pulse; separately count "how many felt beats per bar" by ear. Whichever level wins,
the drum-ACF re-leveling check (§4) should ship as a **mandatory gate** on
compound-suspect songs, not an optional cross-check.

## 6. Implementation plan (4 decoupled pieces)

**(1) Sidecar meter engine (M).** New module returning per-beat `(bar, beat)`,
downbeat times, TS segments, per-segment bpm **derived from median inter-beat
intervals** (never librosa's tempo scalar). Modelpack-gated exactly like mr_mt3:
clone `resolve_mt3_modelpack` (transcription.py:998), layout
`assets/models/beat_this/<version>/{modelpack.json, files/checkpoints/beat_this/final0.ckpt}`,
env override `AURALPRIMER_BEAT_THIS_CHECKPOINT_PATH`; add "beat_this" to
create_portable.ps1's modelpack id list (~line 518). Engine inert without checkpoint →
current librosa path unchanged (degrade scaffolding template: cli.py:701–737).
Swap cli.py:782–783 (`_assign_bar_positions` / `_generate_tempo_map`) to consume the
engine; write real TS meta in `_build_notes_mid_bytes` (cli.py:1646) **at import time
only**; bump beats_tempo stage to 0.4.0; update BEAT_TEMPO_PRODUCTION_POLICY (close the
essentia gate: AGPL) and RUNTIME_DEPENDENCY_POLICIES. PyInstaller: add `beat_this` +
`rotary_embedding_torch` to COLLECT_PACKAGES (aural_ingest.spec:12–38); torch already
collected. Robustness layer around the model output: mode-filter bar lengths, TS
segments with hysteresis/minimum length, drum-ACF level gate (§4/§5), hard fallback to
today's 4/4-phase-0 with a `downbeat-unknown` marker in beat_tempo_meta when confidence
is low. **A wrong meter is worse than the status-quo wrong phase.**

**(2) `refresh-meter` in-place command (M).** Clone align-drum-onsets end-to-end
(cli.py cmd_align_drum_onsets:3955 + parser 4247; Rust ingest_sidecar.rs:738; Studio
button refineWorkspace.ts:947). Feedpaks carry no beats.json/tempo_map.json (verified) —
recompute from `audio/stems/*.wav` (sum non-derived roles; there is no mix.wav) and
rewrite **only** song_timeline.json beats/time_signatures/tempos (+ optionally
`aural/beats.json` via pack_feature_dirname so refine_precompute's beat-aligned regions
start working on feedpaks — currently a silent miss). **Never rewrite notes.mid tempo/TS
meta in place** — the game converts ticks→seconds via the MIDI tempo events
(chartLoader.ts:174–215); changing meta without re-ticking every note shifts all
gameplay notes. arrangements/notation measures have zero readers today (grep-verified) —
skip or optionally rebuild. Interaction warning: rewriting the timeline invalidates
anything anchored to the old grid (quantized-placement edits, saved drum-lane work) —
prompt or warn in the Studio.

**(3) Game transport meter (M, separable).** Real meter fixes the **Studio only** until
this lands: the game transport hardcodes bpm 120 / [4,4] (apps/game main.ts:345,354,454)
and every visualizer (metronome.ts:29, viz-drum-highway index.ts:203/256, viz-fretboard,
viz-tab/sheetMusic) consumes that synthetic state. Load song_timeline tempos/
time_signatures into TransportState at song load. Reference implementation for
per-TS-segment bar counting already exists in the repo: raw_song.rs:2241–2294.
Also: beatsPerBarFromTimeSignatures (apps/desktop/src/beatGrid.ts) reads only ts[0] —
fine for now, degrade point for mid-song TS changes.

**(4) Verification (S+S+M).**
(a) Audible: Studio metronome accent lands on "the one" without the downbeat nudge on
Beat It; correct grouping on Psalm 4; exact indices on synth_drum_align.feedpak.
(b) Diagnostic: re-run the beats-vs-drum-onset median-error script (session scratchpad
pattern) pre/post.
(c) gt-benchmark "meter" family with a Ballroom adapter (beat+downbeat F via bundled
mir_eval) — **caveat from the critic: Ballroom covers 3/4, not compound 6/8; GTZAN is
mostly 4/4. Certifying 6/8 needs a small curated compound-meter set (6/8 rock/ballads
with known downbeats); budget for hand-annotating ~10 songs.**

## 7. Open questions for the implementer (first-hour list, from the adversarial critic)

- **Q1 — Psalm 4 level adjudication (§5). Blocking for the postprocess design.**
- **Q2 — Input audio at import time**: all empirical runs used stem sums, but the
  beats_tempo stage runs *before* separation. Options: run Beat This! on the raw mix
  (untested — smoke it), or move/repeat the meter pass post-separation (the drum-ACC
  gate needs drum events anyway, arguing for a late meter pass — mirroring how
  drum-onset alignment already runs late).
- **Q3 — Compound-meter unit semantics**: for 6/8, what bpm and beat unit go in
  tempo_map/song_timeline (dotted-quarter ~70? model-beat 103? eighths)? What does the
  gridMetronome accent — 2 dotted beats or 6 eighths? Decide once, encode consistently;
  affects metronome, quantization defaults, notation.
- **Q4 — TS-segment emission rules**: mode window, minimum segment length, hysteresis,
  and the policy for stray 5/6/9-beat bars.
- **Q5 — Checkpoint load mechanics**: `File2Beats(checkpoint_path=<local path>, device='cpu',
  dbn=False)` works (verified); confirm `torch.load` `weights_only` behavior for the
  Lightning-style .ckpt under the shipped torch (classic first-hour failure mode).
- **Q6 — 30 s chunk seams**: beat_this processes fixed windows; nobody verified whether
  Psalm 4's double-time flips coincide with chunk boundaries. Check before designing the
  level-consistency postprocess.
- **Q7 — Anacrusis/pickup semantics**: the synthetic beat at t=0 (cli.py:768) and
  measure-start-at-0 (feedpak_writer.py:200) will fight a real tracker's phase; define
  pickup-bar behavior (measure 0 / partial bar) before wiring.
- **Q8 — Acceptance gating numbers**: define the numeric criterion for rejecting neural
  output per song (e.g. % non-modal bars) → librosa fallback. Psalm 4 tests it
  immediately.

## 8. Killed claims / dead ends (do not re-chase)

- **madmom as the meter engine** — model files CC BY-NC-SA (primary-source verified),
  ADTOF-class failure; also unmaintained/unbuildable on py3.11+ Windows without git+patches.
- **essentia** — AGPL-3.0. Close its BEAT_TEMPO_PRODUCTION_POLICY research gate.
- **Regularizing beat times to an even grid** — measured 46–81 ms drift vs 12–23 ms for
  raw tracked beats (this session, Beat It). Meter is grouping+phase, not resampling.
- **Naive mod-m voting on raw librosa beat indices** — scrambled by single dropped
  beats; requires lattice fit or windowed voting.
- **Chroma/harmonic-change as a primary downbeat cue** — measured flat on riff-based rock.
- **Naive piecewise lattice chaining on drifty songs** — corrupted even the clean song's
  profiles; drift needs windowed voting or DP continuity.

## 9. Artifacts

- Raw Beat This! outputs + measurements: session scratchpad `beat_this_results.json`,
  `beat_this_raw_beats.json` (scratchpad is session-temporary — the numbers that matter
  are inlined above; everything reproduces with the commands in §3).
- DSP spike scripts: scratchpad `meter_spike.py`, `tatum_hist2.py`, `psalm_meter.py`,
  `beatit_phase.py`.
- This session also shipped the Studio-side groundwork this engine plugs into:
  detected-beats grid + zoom LOD, grid metronome with downbeat accent (the adjudication
  tool for §5), and a manual downbeat-phase nudge (becomes the escape hatch / mostly
  unnecessary once real downbeats land).
