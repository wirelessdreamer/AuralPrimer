# Claude Code resume plan: ADT/transcription quality v0.2

**Status:** ready to resume from `main` (working copy from session
2026-05-07). All Phase 1 + Phase 2 changes from that session are present
on disk but uncommitted. Run `git status` to see them; `git diff` is the
authoritative starting point.

**How to use this plan in Claude Code:**

```bash
cd D:\AuralPrimer
claude
> Read docs/CLAUDE_CODE_RESUME_PLAN.md and start at task #N
```

Each task is self-contained: goal, files touched, steps, verification
command, done-when criteria. Tasks are ordered by leverage (highest
quality lift per unit of work first), but they are also independent —
skip any task you don't want to do.

References:
- `docs/research-deep-dive-adt-2026-05-07.md` — literature scan, 10
  paths forward
- `docs/research-decision-gates.md` — locked decisions + 2026-05-07
  ADT architecture revision
- `wip.md` — top-of-file status snapshot

---

## Task 0: sanity-check the working copy

**Goal:** confirm the Phase 1 + Phase 2 fixes from 2026-05-07 are
healthy on your machine before adding more changes on top.

**Steps:**

```bash
# Make sure deps are installed
npm ci
python -m pip install -r python/ingest/requirements-dev.txt

# Clear stale pycache (the previous session ran in a sandbox where
# pycache invalidation was unreliable).
find python/ingest -name __pycache__ -exec rm -rf {} +

# Run the targeted regression battery
pytest -q python/ingest/tests/test_ingest_quality_improvements.py \
            python/ingest/tests/test_drum_sparse_source_precision.py \
            python/ingest/tests/test_drum_psalm_12_equivalent.py \
            python/ingest/tests/test_drum_5class_taxonomy.py

# Then the full ingest suite to confirm no broader regression
cd python/ingest && pytest -q
```

**Done when:** all the targeted regression tests pass and the broader
ingest suite is no worse than `main`. Two pre-existing
`spectral_flux_multiband_*` failures predate this work and are still
expected — they are tracked separately.

**Touch points if anything is red:** open `wip.md` -> "Phase 2 ADT
fixes landed" snapshot to see exactly what changed, then narrow with
`git log --oneline -20` on the working copy.

---

## Task 1: ship Phase 1 + Phase 2 as their own commit

**Goal:** lock in the working ADT fixes so subsequent tasks can branch
cleanly.

**Files in the diff (verify before committing):**

- `python/ingest/src/aural_ingest/algorithms/combined_filter.py`
  (low-band guard, unanimous-detector boost, multi-label emitter,
  centroid rule dropped, `_PHYSICAL_GROUPS`)
- `python/ingest/src/aural_ingest/algorithms/_common.py`
  (5-class taxonomy scaffolding)
- `python/ingest/src/aural_ingest/transcription.py`
  (`taxonomy` kwarg, `remap_drum_events_to_taxonomy`,
  `KNOWN_DRUM_TAXONOMIES`, `validate_drum_taxonomy`,
  `transcribe_drums_with_profile`)
- `python/ingest/src/aural_ingest/cli.py` (Demucs `auto`-warn)
- `python/ingest/tests/test_ingest_quality_improvements.py`
  (xfail dropped, kick-fraction bound)
- `python/ingest/tests/test_drum_sparse_source_precision.py`
- `python/ingest/tests/test_drum_5class_taxonomy.py`
- `python/ingest/tests/test_drum_psalm_12_equivalent.py`
- `apps/game/src/main.ts`, `apps/game/src/style.css`
  (Studio-only enforcement)
- `apps/game/src/{ingestClient,ingestUi,lyricsGenerator}.ts` (stubs)
- `apps/game/tests/{ingestClient,ingestUi,lyricsGenerator}.test.ts`
  (skip stubs)
- `.github/workflows/build-release.yml`
- `docs/research-deep-dive-adt-2026-05-07.md`
- `docs/research-decision-gates.md` (ADT architecture revision section)
- `docs/CLAUDE_CODE_RESUME_PLAN.md` (this file)
- `wip.md` (status snapshot updated)
- `BUILDING.md` (orientation block)
- `README.md` (docs index, monorepo layout)
- `benchmarks/quality/full_corpus_manifest.guard.template.json`

**Suggested split (two commits):**

1. `feat(ingest): combined_filter low-band guard + unanimous boost +
   multi-label emitter; 5-class taxonomy scaffold; Demucs warn-on-absent`
2. `chore: enforce spec.md §1.1 in apps/game (Studio-only imports);
   refresh build-release.yml; literature scan + decision-gate revision`

**Done when:** `git status` is clean and `pytest` is still green on
the new commit.

---

## Task 2: build the offline synth-render corpus

**Goal:** stop relying on copyrighted real-audio for fixtures by adding
a Fluidsynth-driven render pipeline that turns MIDI patterns into WAV
fixtures the quality benchmark can run against. This is the answer to
"can we use Ableton to automate audio generation" — Ableton has no MCP
connector and isn't headless-friendly, but Fluidsynth + a permissively
licensed GM SoundFont covers the same ground.

**Files to add:**

- `benchmarks/synth/render_corpus.py` — Python script that takes a
  manifest of MIDI patterns and renders each through Fluidsynth with a
  configurable SoundFont, emitting `audio/mix.wav` + a paired reference
  MIDI under each output dir.
- `benchmarks/synth/patterns/` — small library of MIDI patterns
  covering documented failure modes (sparse-kick-only, kick+crash
  overlap, hi-hat density sweep, kit-variability sweep). Hand-author
  these or generate via `pretty_midi`.
- `benchmarks/synth/README.md` — how to run + which SoundFont to point
  at (do NOT commit the SoundFont; document the download).
- `benchmarks/synth/manifest.template.json` — pattern → expected
  outputs map.

**Steps:**

```bash
# 1. Decide the SoundFont. Recommended free GM-compliant options:
#    - GeneralUser GS (S. Christian Collins) — small, MIT-friendly licence
#    - FluidR3 GM — bundled with many distros, GPL
#    Document the choice in benchmarks/synth/README.md, do NOT commit.

# 2. Install Fluidsynth + the Python binding
pip install pyfluidsynth pretty_midi

# 3. Author the script. It takes:
#    --manifest benchmarks/synth/manifest.template.json
#    --soundfont $HOME/SoundFonts/GeneralUser GS.sf2
#    --out benchmarks/synth/runs/<timestamp>
#    For each entry: load MIDI, render to 48kHz mono WAV, copy reference
#    MIDI alongside, write a per-case `manifest.json`.

# 4. Wire the synthesised cases into the existing guard manifest:
#    benchmarks/quality/full_corpus_manifest.guard.template.json
#    Replace the `TBD` placeholders with rendered paths.

# 5. Run the full quality benchmark
py -3 benchmarks/quality/run_full_corpus_quality.py \
    --manifest benchmarks/quality/full_corpus_manifest.guard.template.json \
    --label v0.2-synth-corpus
```

**Failure-mode patterns to include (one MIDI per pattern):**

1. Sparse kick only (4 quarter notes / 2 bars). Mirrors
   `test_drum_sparse_source_precision.py` but with realistic timbre.
2. Kick + crash on the same beat (every 4th beat). Tests the
   multi-label emitter.
3. Dense hi-hat 8th-note pattern with sparse kick/snare. Tests hi-hat
   recall (ISMIR 2025 says cymbals/hi-hats are the hardest classes).
4. Bass + kick polyphonic test (kick on 1/3, bass on 1/2/3/4). Tests
   bleed handling.
5. Kit-variability sweep — same MIDI pattern rendered through 3
   different drum kits in the SoundFont. Tests robustness to kit.

**Done when:**

- `benchmarks/synth/render_corpus.py` produces deterministic WAV +
  MIDI pairs with a fixed seed.
- `benchmarks/quality/full_corpus_manifest.guard.template.json` has all
  five guard cases pointing at synth-rendered fixtures (no `_TBD`
  placeholders left).
- `run_full_corpus_quality.py` runs end-to-end and writes a
  `summary.json` + `report.md` under `benchmarks/quality/runs/`.

**Why this is high-leverage:** every subsequent task (ADTOF
integration, model-pack flow, taxonomy flip) needs a real benchmark
to grade against. The current synthetic-sine fixtures pass too easily;
the real Psalm-12 audio can't ship. Synth-rendered fixtures are the
honest middle ground.

---

## Task 3: integrate ADTOF as the production drum default

**Goal:** flip the production drum engine from `combined_filter` to
ADTOF (5-class CRNN). This is path 2 from
`docs/research-deep-dive-adt-2026-05-07.md` and the highest-quality
single change available.

**Steps:**

```bash
# 1. Add ADTOF as a model-pack
#    - Source: https://github.com/MZehren/ADTOF (Apache 2.0)
#    - Bundle the released CRNN weights as a modelpack:
#      modelpacks/adtof_crnn.zip with modelpack.json
#      {"id": "adtof_crnn", "version": "1.0", "weights": [...]}

# 2. Wire ADTOF as a new algorithm in
#    python/ingest/src/aural_ingest/algorithms/adtof_crnn.py:
#    - Import gated try/except so missing deps fall through
#    - transcribe(stem_path) returns list[DrumEvent] in 5-class
#      MIDI numbers (kick=36, snare=38, hi_hat=42, toms=47, cymbals=49)

# 3. Register in transcription.py:
#    - Add "adtof_crnn" to KNOWN_HEURISTIC_DRUM_FILTERS (it is heuristic
#      only in the sense that it is not MT3-style) or add a new tier
#      KNOWN_NEURAL_DRUM_ENGINES
#    - Add to build_default_drum_algorithm_registry()
#    - Update gameplay_default profile drum_engines to put adtof_crnn first

# 4. Flip default taxonomy to 5class because ADTOF is natively 5-class:
#    - Update DEFAULT_DRUM_TAXONOMY = "5class"
#    - Migrate downstream consumers in apps/desktop and apps/game that
#      hard-code 9-class MIDI numbers (search for `note ==` near drum
#      handling in chartLoader.ts, hud.ts, plugins, etc.)

# 5. Run the synth corpus from Task 2 against the new default
py -3 benchmarks/quality/run_full_corpus_quality.py \
    --manifest benchmarks/quality/full_corpus_manifest.guard.template.json \
    --label v0.2-adtof-default
```

**Done when:**

- Default profile drum_engines starts with `adtof_crnn`.
- `pytest python/ingest/tests/test_drum_psalm_12_equivalent.py` passes
  with tighter bounds than the heuristic baseline (kick_frac >= 0.85
  is realistic with ADTOF on a 65 Hz body kick).
- `benchmarks/quality/runs/<timestamp>_v0.2-adtof-default/summary.json`
  shows higher F1 than `v0.2-synth-corpus` baseline on every guard case.

**If ADTOF doesn't deliver:** YourMT3+ is the alternative (see
`https://github.com/mimbres/YourMT3`). Heavier model, broader
multi-instrument coverage, also Apache 2.0.

**Risks:**

- Model size: ADTOF CRNN is ~5 MB; YourMT3+ is ~100 MB. The
  model-pack flow already supports zip uploads; verify the size
  doesn't break any download timeout.
- CPU inference time: ADTOF on `minimum_modern` (8 logical cores, 16
  GB RAM, no GPU) should be <10× real-time. If higher, the offline
  ingest is fine but interactive A/B in Studio gets uncomfortable.
- Determinism: pin model weights, set torch seeds, use deterministic
  ops. ADTOF reports bit-identical inference across runs with these
  pinned.

---

## Task 4: flip Demucs from "warn-on-absent" to "required"

**Goal:** ship the production drum path as
`Demucs preprocessing → ADTOF transcription`. Currently the Demucs
gate warns when the modelpack is absent (Phase 2 / Task 1); after
Task 3, we want to escalate to a hard requirement for the production
drum default.

**Steps:**

```bash
# 1. In python/ingest/src/aural_ingest/cli.py update the auto-fallthrough
#    to refuse to use the production drum default when Demucs is absent.
#    Concretely: if drum_engine resolves to adtof_crnn AND demucs is
#    missing, abort the import with a clear error pointing at the
#    modelpack download path.

# 2. Add a `--allow-no-separator` opt-out flag for users who deliberately
#    want to import without Demucs (e.g., they already have stems).

# 3. Update docs/research-decision-gates.md:
#    - Move the Stem Separation Policy section under the ADT
#      Architecture Revision umbrella
#    - State that Demucs is required for the production drum default
#      with documented escape hatch via --allow-no-separator
```

**Done when:**

- Importing without `demucs_6.zip` and without `--allow-no-separator`
  fails with a clear error before any DSP runs.
- Existing imports with `--allow-no-separator` succeed and emit a
  prominent warning into the SongPack manifest.

---

## Task 5: real Basic Pitch + multi-pitch CRNN for melodic

**Goal:** path 8 from the deep-dive. The plumbing is already in place
in `algorithms/melodic_basic_pitch.py` (Phase E from the 2026-05-07
session). This task is to validate it end-to-end with the real model
and add a benchmark.

**Steps:**

```bash
# 1. Install Spotify Basic Pitch
pip install basic-pitch

# 2. Verify the import gate works:
python -c "from basic_pitch.inference import predict; print('ok')"

# 3. Run the existing melodic test against a clean A3 sine:
pytest python/ingest/tests/test_ingest_quality_improvements.py::test_quality_07_melodic_pyin_tracks_sustained_sine \
       python/ingest/tests/test_ingest_quality_improvements.py::test_quality_08_melodic_no_blanket_octave_error

# 4. Add a Basic-Pitch-specific test:
#    python/ingest/tests/test_melodic_basic_pitch_real.py
#    - Skip when basic_pitch package not importable
#    - Synthesize a polyphonic stem (C major triad sustained 2s)
#    - Assert basic_pitch returns 3 separate notes (60, 64, 67)
#    - Assert pYIN baseline collapses to 1 note (the documented
#      polyphonic-collapse failure mode)

# 5. Update gameplay_default profile so basic_pitch is first for
#    polyphonic instruments (keys, rhythm_guitar) when the package is
#    importable. The fallback chain already covers the case when it
#    is missing.
```

**Done when:**

- The new `test_melodic_basic_pitch_real.py` passes when basic_pitch
  is installed and skips cleanly when it isn't.
- A guard-run on `benchmarks/quality/full_corpus_manifest.guard.template.json`
  shows basic_pitch outperforms pYIN on the keys + rhythm_guitar cases.

---

## Task 6: multi-label CRNN for overlapping hits (research)

**Goal:** path 9 from the deep-dive — replace the heuristic multi-label
emitter from Phase 2 with a learned model. This is the longest-tail
work on the list; gate it on Tasks 2-3 landing first so we have a real
benchmark to grade against.

**Sketch only** (this task is exploratory):

- Train (or fine-tune) a small CRNN on the synth corpus from Task 2
  with multi-label per-frame outputs (5-class probability vector per
  20 ms frame).
- Loss: weighted BCE per class with class-frequency-rebalancing
  (kick/snare are over-represented vs. cymbals).
- Eval on the same guard manifest as Task 3.
- Promotion gate: only flip the heuristic emitter to the model when
  the model wins on every guard case AND on the synthetic-Psalm-12
  fixture.

**Done when:** the model beats the heuristic multi-label emitter on the
guard manifest by at least 5 percentage points F1 across all 5 guard
cases.

---

## Task 7: real-audio guard fixtures (legal/sourcing)

**Goal:** complement the synth corpus with 1-2 short clips of
real-audio that legally redistributable. Real audio catches
failure modes that synthetic data misses.

**Sourcing options:**

- `MUSDB18-HQ` "free dev" subset (some tracks are CC-licensed; check).
- IRCAM `OneShots` library (CC-BY).
- `freesound.org` recordings under CC-0 / CC-BY (need 30-60 second
  drum loop recordings).
- Self-recorded drum loops (clean redistribution).

**Steps:**

- Source 2-3 short (15-30 s) drum-only clips with permissive licences.
- Hand-annotate the reference MIDI (or accept that some cases will be
  reference-free and use no-reference quality metrics).
- Add to `benchmarks/quality/full_corpus_manifest.guard.template.json`
  alongside the synth corpus.
- Document licences in `benchmarks/quality/LICENSES.md`.

**Done when:** the guard manifest has 2 real-audio cases and 5
synth-rendered cases; the run produces a comparison table in
`report.md`.

---

## Tasks deferred until external resources arrive

These are tracked but do not have a self-serviceable path here:

- **YourMT3+ as alternative drum engine** — needs the released
  weights (`https://github.com/mimbres/YourMT3`) and a benchmark
  comparison vs ADTOF on the synth corpus.
- **Real Psalm-12 audio fixture** — copyrighted; replace with the
  best-permissively-licensed analog from Task 7.
- **STAR Drums dataset evaluation** — internal-only per
  `docs/research-decision-gates.md`; gate via local-only configuration
  in `benchmarks/quality/`.

---

## Quick reference: file-by-file impact map

| File | Tasks that touch it |
|---|---|
| `python/ingest/src/aural_ingest/algorithms/combined_filter.py` | 0 (verify) |
| `python/ingest/src/aural_ingest/algorithms/adtof_crnn.py` | 3 (new) |
| `python/ingest/src/aural_ingest/algorithms/melodic_basic_pitch.py` | 5 (extend tests) |
| `python/ingest/src/aural_ingest/transcription.py` | 3 (register adtof), 4 (require demucs) |
| `python/ingest/src/aural_ingest/cli.py` | 4 (require demucs) |
| `python/ingest/src/aural_ingest/algorithms/_common.py` | 3 (5-class default) |
| `benchmarks/synth/` | 2 (new directory) |
| `benchmarks/quality/full_corpus_manifest.guard.template.json` | 2 (fill cases), 7 (real audio) |
| `apps/desktop/src/chartLoader.ts` | 3 (5-class consumer migration) |
| `apps/game/src/chartLoader.ts` | 3 (5-class consumer migration) |
| `docs/research-decision-gates.md` | 4 (gate flip update) |

---

## Reminders for any session that resumes here

1. The repo enforces TDD (`spec.md §2.0`). Add or update tests before
   the implementation; refresh golden fixtures in the same change.
2. After completing any task, update the "Phase 2 ADT fixes landed"
   snapshot at the top of `wip.md` so the next session sees an honest
   ledger.
3. Do not reintroduce import / song-creation / lyrics-generation flows
   into `apps/game`. Those moved to AuralStudio in
   the 2026-05-07 session per `spec.md §1.1`.
4. The deep-dive (`docs/research-deep-dive-adt-2026-05-07.md`)
   replaces the older single top-10 list in this file. Trust the
   deep-dive when prioritising new work.
5. The portable build (`npm run portable:build`) is the canonical
   end-to-end smoke; run it before tagging a release. It enforces a
   sidecar-freshness guard and stages the `demucs_6` modelpack with
   manifest validation.
