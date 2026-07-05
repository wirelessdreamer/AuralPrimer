# Deferred & blocked work — meter / grid / drums (handoff 2026-07-05)

**Context.** The 2026-07-05 push landed neural meter tracking (Beat This!),
the cleanup beat-grid + metronome, onset-driven drum timing (with a hi-hat
exclusion fix), and the Studio **Refresh meter** button.

**What shipped (the baseline these deferrals sit on):**

- Beat This! (MIT, CPJKU) meter tracking in the sidecar, modelpack-gated.
  Both import and the Refresh-meter button use it. Verified on the shipped
  onefile: `refresh-meter` returns `beat_source: beat_this` (not a librosa
  fallback).
- Cleanup editor beat grid uses the **detected** beat times (they track the
  audio to ~12–23 ms; a regularized/uniform grid drifted 46–81 ms and grew).
  Includes a downbeat nudge + LOD zoom + a latency-compensated metronome.
- Drum onset snapping excludes hi-hats/cymbals (high-band onsets are
  smeared/late and the window grabbed neighboring hits, dragging them ~+30 ms).
- Game reads the song's real tempo + first time signature (commit `758f924`).

Everything below was consciously **not** done this push. Each item lists why,
the interim state, and concrete resume steps so the next session can pick it up
cold.

---

## C — Compound-meter denominator (6/8, 12/8, …)  — *deferred: cosmetic + risky*

**What.** Beat This! gives a reliable beats-per-bar count (the numerator) but
not the beat *note value* (the denominator). `meter_tracker` emits
`time_signature` as `"<N>/4"` with `meter_denominator_provisional: true`. So a
compound song such as Psalm 4 "Trouble Again" (6/8) is *labeled* with a `/4`
denominator even though its grid and downbeats are correct.

**Why deferred.** The beat grid and downbeats are already right — the metronome
and bar alignment for Beat It and Trouble Again were confirmed "spot on". Only
the displayed denominator label is provisional. Auto-inferring 4 vs 8 needs
simple-vs-compound detection (are beats subdivided in 2s or 3s), which is
error-prone; a wrong denominator is worse than an honest `provisional` flag.

**Interim state.** `meter_denominator_provisional: true` rides on every
timeline; the numerator + beat times are trustworthy. UI can render the TS as
tentative.

**Resume.** In `python/ingest/src/aural_ingest/meter_tracker.py`:
- After `derive_beats_per_bar`, add compound detection — histogram the beat
  subdivisions (Beat This! tatums, or a librosa onset-subdivision count) to
  decide simple (`/4`) vs compound (`/8`).
- For compound, use the conventional label (e.g. 6 eighth-pulses → `"6/8"`).
- Clear `meter_denominator_provisional` once the denominator is inferred.
- First ground-truth check: Psalm 4 (6/8) — its metrical *level* was
  ear-confirmed at 103.4 bpm (see `docs/research-meter-tracking-2026-07-05.md`
  §5).

---

## D — Game-side metronome/bars from detected beat *times*  — *deferred: big refactor*

**What.** The Studio cleanup editor renders bars + metronome from the
**detected** beat times in `song_timeline.beats` (they track the audio's
micro-timing to ~12–23 ms). The game (`apps/game`) still derives its bars from a
**uniform** grid built off `bpm` + time signature (via
`transportController.setSongMeter`, landed this push in `758f924`). So the
game's bar lines can drift from the audio the way the editor's old regularized
grid did — even though tempo/TS are now correct.

**Why deferred.** Plumbing a beat-times array through the game transport +
viz-sdk bar renderer is a sizable refactor: the viz-sdk plugins render bars from
the transport's bpm/TS, not from a per-beat time list. Scope + regression risk
on the gameplay renderer.

**Interim state.** Game uses the correct tempo + first time signature, so bar
count/feel is right; only sub-bar micro-timing can drift.

**Resume.** Mirror the editor's `applyBeatGrid` approach in the game:
- Plumb `song_timeline.beats` (detected times + measure indices) into
  `apps/game/src/transportController.ts` alongside bpm/TS.
- Update viz-sdk bar/metronome rendering to key off the beat-times array when
  present, falling back to the uniform bpm grid when absent.
- Reuse the editor's proven logic: `apps/desktop/src/beatGrid.ts`
  (`buildGridTimes`, `downbeatTimes`) and `apps/desktop/src/gridMetronome.ts`.

---

## E — Multi-segment tempo / time-signature changes  — *deferred: no impact on current corpus*

**What.** `song_timeline.json` already carries `tempos[]` and
`time_signatures[]` as time-indexed arrays, but the game and editor use only the
**first** entry (`tempos[0]`, `time_signatures[0]`). A song that changes meter
or tempo mid-piece (e.g. a bridge in a different signature) is rendered with its
opening meter throughout.

**Why deferred.** The current corpus (psalms + Beat It) is single-meter, so this
has no user-visible effect today. It adds time-indexed lookup complexity across
transport, grid, and metronome for a case that doesn't occur yet.

**Interim state.** First tempo/TS is used everywhere; correct for single-meter
songs.

**Resume.**
- `apps/game/src/main.ts` (the meter read after `readSongChartSelection`) +
  `transportController.setSongMeter`: accept the full arrays and switch the
  active tempo/TS at each entry's `time`.
- Editor `apps/desktop/src/refineWorkspace.ts` `applyBeatGrid` +
  `beatsPerBarFromTimeSignatures` already reads `time_signatures[]`; extend it
  to switch beats-per-bar at segment boundaries instead of taking the mode.

---

## G / H / I — Real ground-truth benchmark sweeps  — *BLOCKED: dataset drive offline*

**What.**
- **G** — E-GMD real drum benchmark: the decisive `mr_mt3`-vs-heuristic number.
- **H** — OaF / MAESTRO piano ground-truth sweeps (scaffolded).
- **I** — Guitar amp-tone baseline sweep.

**Why blocked.** The datasets/checkpoints drive is not mounted on this machine,
so the real ground-truth corpora (E-GMD, MAESTRO, OaF) and some checkpoints are
unavailable. Only synthetic suites can run here.

**Interim state / what's known.** On the *synthetic* drum suite, the production
`mr_mt3` default measured F1 ≈ 0.387 vs the DSP heuristic 0.507 — but synthetic
is not decisive; the real E-GMD sweep is the number that settles it. Scaffolding
exists: `python/ingest/src/aural_ingest/drum_benchmark_suite.py`,
`ground_truth_benchmark.py`, `melodic_benchmark_suite.py`,
`piano_benchmark_suite.py`.

**Resume.**
- Mount the datasets drive; point the benchmark suites at E-GMD / MAESTRO / OaF.
- Run the scaffolded suites and record head-to-head F1 vs the current defaults.
- Gate any production-default flip on the real numbers (per
  `docs/research-decision-gates.md`).

---

**Cross-references.**
`docs/research-meter-tracking-2026-07-05.md` (meter plan + Beat This! adoption,
the license gate that rejected madmom's NC weights, and the Psalm-4 ear-check),
plus the drum/bass/guitar research docs under `docs/research-*`.
