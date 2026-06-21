# AuralPrimer — data-driven music learning, ear & instrument practice

AuralPrimer turns any song you own into a playable practice surface. Drop
in an audio file or a Suno stem export, and the pipeline does the rest:
stem separation, beat/tempo analysis, per-instrument transcription, and
a falling-note in-game view that scrolls toward a "play here" line you
can adjust on the fly.

![Piano-roll Band Setup with the imported "Psalm 10 - Why" keys stem](docs/assets/screenshots/band-setup-keys.png)

> The shot above is the in-game **Band Setup** view of the Keys/Synth lane
> for a real Suno piano stem the pipeline transcribed. Notes fall toward
> the "PLAY HERE" line; the bright cap at the bottom of each pill is the
> attack, the fading tail above is the sustain. The detected key signature
> (`F# minor, 3 sharps`) is read straight off the note distribution by
> the in-process Krumhansl–Schmuckler analyzer in `viz-tab`. Press
> <kbd>[</kbd> / <kbd>]</kbd> in-game to spread / compress the falling
> notes StepMania-style.

## What's in this repo

This is a two-app desktop suite plus the pipelines, plugins, and research
that feed them.

1. **AuralPrimer** — the gameplay app. Loads `.auralsong` packs, plays
   them, drives the visualizer plugins, and routes live MIDI for
   practice.
2. **AuralStudio** — the authoring app. Imports raw audio / stem folders,
   runs the pipeline, and emits `.auralsong` packs the game can load.
3. **Python sidecar pipeline** (`python/ingest/`) — extraction,
   stem-separation, and per-instrument transcription tooling shipped as
   PyInstaller binaries so users don't need a Python install.
4. **Visualizer SDK + plugins** (`packages/viz-sdk`, `visualizers/`) —
   pluggable canvas renderers (drum highway, beats, tab, piano-roll,
   chord-lane, lyrics) that all consume the same canonical transport
   state.

## Quickstart

```sh
git clone https://github.com/wirelessdreamer/AuralPrimer.git
cd AuralPrimer
npm ci              # installs the JS workspace + downloads model packs that ship in CI
cargo test --workspace
npm test
python -m pip install -e python/ingest    # only needed if you want to call the pipeline from source

# Build a portable bundle (Windows): produces AuralPrimerPortable/{AuralPrimer.exe, AuralStudio.exe, ...}
pwsh ./create_portable.ps1 -PortableRoot ./AuralPrimerPortable
```

See [`BUILDING.md`](BUILDING.md) for the full install / test / package
matrix and [`docs/local-dev-prereqs.md`](docs/local-dev-prereqs.md) for
OS-level prerequisites.

## Design principles

- **Test-driven development.** Tests first, implementation second; CI
  stays green.
- **AuralSong-first runtime.** AuralPrimer consumes `.auralsong` packs as
  canonical content. The folder watcher pulls in new packs without a
  restart.
- **Deterministic imports.** Cacheable, reproducible, versioned outputs.
- **Local-first shipping.** Required tooling lives in the desktop
  artifact. ML model weights are NOT bundled in the installer — they
  download/import into `assets/models/` on first use.
- **Plugin-first visualization.** Visualizers stay decoupled from the
  runtime via the `viz-sdk` plugin SDK.
- **Rights-neutral importer scope.** PRs for source-specific proprietary
  game/DLC archive importers are out of scope.

## Research & methodology — first class

We treat transcription quality as a research problem and publish the
numbers, the corpora, the algorithms, and the reproducibility commands
alongside the code. The work directly informs production defaults —
nothing in the ingest pipeline is shipped without head-to-head benchmark
evidence captured in a document below.

### Headline results (synth + annotated corpora, June 2026 round)

| Instrument | Production default                       | Corpus                                | F1     | Precision | Recall | Δ vs prior |
|------------|------------------------------------------|---------------------------------------|-------:|----------:|-------:|-----------|
| **Drums**  | `librosa_superflux_dense`                | E-GMD test (20 cases)                 | 0.153  |  —        | —      | **+50%** vs base `librosa_superflux`; +5.5% vs prior leader `adaptive_beat_grid` |
| **Bass**   | `melodic_pyin_bass_strict`               | GuitarSet low strings (8 cases)       | 0.270  |  0.271    | 0.268  | **+13%** vs `melodic_pyin`; MAE 20 ms → 13 ms |
| **Guitar** | `melodic_combined` (unchanged) + new `melodic_combined_guitar` workspace candidate | GuitarSet mic (12 cases) | 0.261 → 0.248 | trade   | trade  | precision/recall trade — variant ships as high-recall option only |
| **Keys**   | `piano_chord_supplement` (PTI + cleanup + echo dedup + low-pitch pyin supplement + analytical chord-onset fallback) | Synthetic piano corpus (4 cases, 117 notes) | **0.928** | **0.981** | **0.880** | +0.222 absolute over raw `piano_pti` (0.706); precision +18 pts; recall +27 pts |

Per-case breakdowns, JSON reports, reproducibility commands, and the
"what didn't work and why" notes live in the doc below.

### Research docs (start here)

- [**Piano transcription cleanup deep-dive — 2026-06-20**](docs/research-piano-cleanup-deep-dive-2026-06-20.md)
  Standalone narrative of the keys F1=0.706 → 0.928 climb. Tells each
  of the four steps end-to-end: the failure mode found by inspecting
  the previous step's output, the fix's gating contract, per-case
  numbers, and the "what didn't work" log (naive ensembles, onset-
  threshold sweeps, other engines). The doc to read first if you
  want the story behind the headline number.

- [**Ground-truth benchmarks — 2026-06-14**](docs/research-ground-truth-benchmarks-2026-06-14.md)
  Full per-instrument deep dive across all four instruments. Builds
  the annotated-corpus benchmark harness, ships dataset adapters for
  E-GMD / GuitarSet / Guitar-TECHS, documents every tuned variant we
  tried (the wins AND the failures), and pins reproducibility commands
  so anyone can re-run a sweep with one `aural_ingest gt-benchmark`
  invocation. Covers drums, bass, guitar, and the round summary for
  keys (deep-dive doc above expands the keys section).

- [**ADT architecture deep-dive — 2026-05-07**](docs/research-deep-dive-adt-2026-05-07.md)
  2024–2025 ADT / transcription literature scan that revised 10
  architectural assumptions baked into the original pipeline. Each
  assumption is checked against published work, the resulting paths-
  forward list is the source of the current production-default trail.

- [**Research decision gates**](docs/research-decision-gates.md)
  The locked-in production defaults the rest of the codebase reads from
  (beat/tempo backend, stem-separator policy, benchmark thresholding
  stance). Updated whenever a benchmark round flips a decision.

- [`docs/CLAUDE_CODE_RESUME_PLAN.md`](docs/CLAUDE_CODE_RESUME_PLAN.md)
  Resumable v0.2 task plan (synth corpus, ADTOF integration, Demucs
  gate, real Basic Pitch, multi-label CRNN, real-audio fixtures).

- [`DRUM_TRANSCRIPTION_ALGORITHM_NOTES.md`](DRUM_TRANSCRIPTION_ALGORITHM_NOTES.md),
  [`TRANSCRIPTION_RECOVERY_NOTES.md`](TRANSCRIPTION_RECOVERY_NOTES.md),
  [`TRANSCRIPTION_REGRESSION_HISTORY.md`](TRANSCRIPTION_REGRESSION_HISTORY.md)
  Pre-rewrite recovery context (lost-tree recovery from 2026-03-03);
  preserved because the algorithm choices in `python/ingest/src/aural_ingest/algorithms/`
  trace back through this material.

### How a benchmark round works

1. Pick an annotated corpus and pick / write a dataset adapter under
   `python/ingest/src/aural_ingest/dataset_adapters/`.
2. Run `aural_ingest gt-benchmark --dataset <name> --algorithm <one> --algorithm <other> ...`
   The runner registers the requested algorithms, scores each against
   the corpus's reference events with greedy onset matching, and emits
   a JSON report under `benchmarks/{drums,melodic}/gt_runs/`.
3. Read the per-case breakdown. If a variant Pareto-dominates the
   production default (every case strictly improves or stays unchanged
   on F1 / precision / recall), promote it; otherwise ship it as a
   workspace candidate.
4. Append the round's results table + reproducibility command + the
   "what didn't work" log to
   `docs/research-ground-truth-benchmarks-<date>.md` and update the
   summary scoreboard above.

The harness lives at
[`python/ingest/src/aural_ingest/ground_truth_benchmark.py`](python/ingest/src/aural_ingest/ground_truth_benchmark.py)
and the CLI subcommand is documented in
[`docs/ingest-pipeline.md`](docs/ingest-pipeline.md).

## Docs (full index)

Authoritative requirements + planning:

- [`spec.md`](spec.md) — app boundaries, hard constraints, MIDI/audio rules
- [`wip.md`](wip.md) — living implementation tracker (milestones, in-flight tasks, decisions)
- [`docs/roadmap.md`](docs/roadmap.md) — milestones from MVP to v1
- [`docs/risk-register.md`](docs/risk-register.md) — technical risks and mitigations

Architecture and contracts:

- [`docs/architecture.md`](docs/architecture.md) — system overview, module boundaries, runtime flows
- [`docs/auralsong-spec.md`](docs/auralsong-spec.md) — `.auralsong` format, event model, versioning/migrations
- [`docs/auralsong-deliverable.md`](docs/auralsong-deliverable.md) — deterministic pack build contract
- [`docs/ingest-pipeline.md`](docs/ingest-pipeline.md) — Python pipeline DAG, stage plugins, caching, CLI contract
- [`docs/visualization-plugins.md`](docs/visualization-plugins.md) — visualization plugin API + loading model
- [`docs/audio-codec-policy.md`](docs/audio-codec-policy.md) — host playback uses Rust/Symphonia; FFmpeg stays in the ingest sidecar
- [`docs/midi-keyboard-testing.md`](docs/midi-keyboard-testing.md) — hardware MIDI input verification path

Process, tooling, packaging:

- [`docs/local-dev-prereqs.md`](docs/local-dev-prereqs.md) — OS-level prerequisites
- [`BUILDING.md`](BUILDING.md) — install / test / build / portable-package instructions
- [`docs/testing-strategy.md`](docs/testing-strategy.md) — TDD layers, fixtures, golden tests
- [`docs/packaging-ci.md`](docs/packaging-ci.md) — bundling sidecars / decoders / models, CI build matrix
- [`docs/performance-baselines.md`](docs/performance-baselines.md) — hardware profiles backing benchmark thresholds

## Monorepo layout

```text
/AuralPrimer
  /apps
    /game                   # AuralPrimer gameplay app (Tauri)
    /desktop                # AuralStudio authoring app (Tauri)
  /packages
    /core-music             # shared schema + utilities (TS + Rust)
    /viz-sdk                # visualization plugin SDK (TS)
    /auralsong              # AuralSong reader/writer/validator (TS + Rust)
  /python
    /ingest                 # Python extraction pipeline (built into sidecars)
      /src/aural_ingest
        /algorithms         # per-instrument transcribers + the new piano cleanup family
        /dataset_adapters   # E-GMD / GuitarSet / Guitar-TECHS / piano_synthetic
        /ground_truth_benchmark.py
  /visualizers
    /viz-beats              # beat/section grid
    /viz-drum-highway       # drum lanes from host-provided MIDI events
    /viz-fretboard          # fretboard cursor placeholder
    /viz-lyrics             # data-driven karaoke lyrics
    /viz-nashville          # chord-lane placeholder
    /viz-tab                # piano-roll + tab renderer for keys/bass/guitar
  /benchmarks               # frontend/python/rust benches + thresholds.yml + reports
  /scripts                  # Node + PowerShell launchers for build/bench/portable
  /assets
    /models                 # downloaded on first use; NOT bundled in installer
    /test_fixtures
  /docs
    /assets/screenshots
```

## Packaging stance ("no external runtime dependencies")

At runtime, users should not need a separate Python / FFmpeg / runtime
install:

- ingest tools ship as PyInstaller sidecar executables
- decoder binaries are bundled when needed
- ML model packs download / import post-install into `assets/models/`
  (never bundled in the installer)

See [`docs/packaging-ci.md`](docs/packaging-ci.md) for the full CI build
matrix.
