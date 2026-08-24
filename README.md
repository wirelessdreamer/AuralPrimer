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
> attack, the fading tail above is the sustain. The displayed key signature
> (`F# minor, 3 sharps`) comes from manifest key artifacts when present, with
> the in-process Krumhansl–Schmuckler analyzer as the note-distribution
> fallback. Press
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
npm ci              # installs the JS workspace
cargo test --workspace
npm test
python -m pip install -e python/ingest    # only needed if you want to call the pipeline from source

# Build a portable bundle (Windows): produces AuralPrimerPortable/{AuralPrimer.exe, AuralStudio.exe, ...}
pwsh ./create_portable.ps1 -PortableRoot ./AuralPrimerPortable
```

See [`BUILDING.md`](BUILDING.md) for the full install / test / package
matrix and [`docs/local-dev-prereqs.md`](docs/local-dev-prereqs.md) for
OS-level prerequisites.

## Playing with a MIDI keyboard

Live MIDI input is native (Rust `midir`: WinRT on Windows, CoreMIDI on macOS,
ALSA on Linux). Browser-only `vite` mode cannot see MIDI devices — use the
Tauri app or the portable build.

**Connect once.** `Configure -> MIDI -> MIDI Input`: *Refresh*, pick the port,
*Connect*. The choice persists in `settings.json`, and the app reconnects it at
launch — the status line says `auto-connected` when it did. The **MIDI IN**
readout above the *Start* button reports what the app is hearing on every
instrument and display mode, and names the case where nothing is connected, so
a silent device is never mistaken for a silent passage.

### Transport buttons (MIDI learn)

`Configure -> MIDI -> Transport control` binds your controller's transport
strip. Click **Learn**, press the button, and whatever it sends becomes the
binding — CC or note, any number, any channel. Controllers disagree wildly
here, so nothing is assumed. Bindings persist in `settings.json`, and learning
a button already in use reassigns it rather than letting one press drive two
actions.

| Action | Behaviour |
| --- | --- |
| Start song over | Return to zero and play |
| Rewind / Fast forward | Jog while held, accelerating 0.25 s -> 4 s per 100 ms |
| Stop | Halt and return to zero |
| Play / pause | Toggle; starts the song if it has not started |
| Wait mode on/off | Toggle advance-on-note-play (no default binding) |

The log under those rows shows every incoming message **and how it was read**
(`CC 21 = 127 ch1 -> Rewind: PRESS`). That is the first thing to check when a
button misbehaves: it distinguishes a silent device from a mis-bound one.

### Hold-to-jog needs a controller that sends a release

A button can only express *held* if it sends something on release. Two
families exist, and the app handles both:

- **Momentary** — a value on press, `0` on release. Rewind and fast forward
  jog for exactly as long as you hold them.
- **Toggle / one-shot** — one message per press, nothing on release. A hold is
  physically undetectable, so pressing the same button again stops the jog.

Momentary is worth setting up if your controller supports it. On an
**M-Audio Axiom 49/61**, whose buttons ship in toggle mode, assign each one to
controller **146** ("MIDI CC on/off"):

```text
Ctrl Assign 146  ->  Data 1 <cc>  ->  Data 2 000  ->  Data 3 127
```

Those are dedicated buttons on the 49/61 — there is no Edit/Advanced key
(that is the 25-note model) and no Enter to confirm. `scripts/axiom_tool.py`
prints the procedure (`steps`), reports momentary-vs-toggle per button as you
press it (`check`), and backs up the device's presets first (`backup`), since
restoring a dump overwrites all of them. Changing the mode does not change the
CC number, so learned bindings keep working.

### Keyboard shortcuts (play route)

| Key | Action |
| --- | --- |
| <kbd>Space</kbd> | Start / pause / resume |
| <kbd>&larr;</kbd> <kbd>&rarr;</kbd> | Jog 5 s (hold <kbd>Shift</kbd> for 1 s) |
| <kbd>[</kbd> <kbd>]</kbd> | Spread / compress note spacing |
| <kbd>Ctrl</kbd>+<kbd>[</kbd> <kbd>]</kbd> <kbd>0</kbd> | Nudge / reset the A/V sync offset |
| <kbd>Esc</kbd> | Pause menu |

### Practice toggles

Both sit directly above the *Start* button:

- **Wait mode — advance on note play**: holds at each note or chord until you
  play it, then advances. Needs a connected keyboard; the MIDI IN readout
  names the note it is waiting for.
- **Nashville numbers**: stamps the scale degree (1-7) on each falling note
  instead of note names.

## Design principles

- **Test-driven development.** Tests first, implementation second; CI
  stays green.
- **AuralSong-first runtime.** AuralPrimer consumes `.auralsong` packs as
  canonical content. The folder watcher pulls in new packs without a
  restart.
- **Deterministic imports.** Cacheable, reproducible, versioned outputs.
- **Local-first shipping.** Required tooling lives in the desktop
  artifact. ML model weights are not auto-fetched by packaging; reviewed,
  pinned modelpacks/checkpoints may be staged into portable/release artifacts,
  and otherwise download/import into `assets/models/` after install.
- **Plugin-first visualization.** Visualizers stay decoupled from the
  runtime via the `viz-sdk` plugin SDK.
- **Rights-neutral importer scope.** PRs for source-specific proprietary
  game/DLC archive importers are out of scope.

## Third-party components & attribution

AuralPrimer is free software ([GPL-3.0-or-later](LICENSE)) with no commercial
aspirations — the goal is integrating the best open solutions in a
user-friendly way. It stands on open-source music-ML research and tooling, and
the attribution list below stays complete both because the licenses require it
and because credit is owed. **For every neural model we verify the trained
*weights* license separately from the code license** (they frequently differ —
open-code / unlicensed-weights splits are common). The license gate for what
ships:

- **Code dependencies** must be GPLv3-compatible. MIT / Apache-2.0 / BSD /
  ISC / MPL-2.0 / CC0 / LGPL / GPL all qualify — this is nearly every
  open-source license in practice.
- **Model weights & datasets** must carry an *explicit* redistributable
  license. Non-commercial terms (e.g. CC BY-NC-SA) are acceptable for this
  project; weights with **no stated license at all** remain excluded — with no
  grant, nobody has redistribution rights, non-commercial or otherwise.

### Who makes what we use

One row per maker, ordered by layer — models first, then ML runtime, app
shell, and native audio/MIDI:

| Layer | Organization / maintainer | What AuralPrimer uses from them | License |
|---|---|---|---|
| Model | **Meta AI · FAIR** — Défossez et al. | [**Demucs**](https://github.com/facebookresearch/demucs) — stem separation | MIT (code + weights) |
| Model | **Sony AI** — Tan · Cheuk · Mitsufuji | [**MR-MT3**](https://github.com/gudgud96/MR-MT3) — neural drum transcription (+ [`mt3-infer`](https://github.com/openmirlab/mt3-infer) wrapper) | MIT (code + weights) |
| Model | **Google Research · Magenta** | [**MT3**](https://github.com/magenta/mt3) — the architecture MR-MT3 is fine-tuned from | Apache-2.0 |
| Model | **mimbres / YourMT3 authors** | [**YourMT3**](https://github.com/mimbres/yourmt3) — MT3-family research/A-B transcription candidate, with the reviewed [Hugging Face checkpoint](https://huggingface.co/mimbres/YourMT3) staged as a modelpack | GPL-3.0 code / Apache-2.0 weights |
| Model | **Spotify · Audio Intelligence Lab** | [**Basic Pitch**](https://github.com/spotify/basic-pitch) — piano / polyphonic melodic transcription | Apache-2.0 |
| Model | **Qiuqiang Kong / ByteDance + Edwards et al.** | [**piano_transcription_inference**](https://github.com/qiuqiangkong/piano_transcription_inference) plus Edwards robust piano checkpoint — optional PTI piano cleanup path | MIT code / CC BY 4.0 checkpoint |
| Model | **Kim · Kwon · Nam** | [**D3RM**](https://github.com/hanshounsu/d3rm) — optional external piano transcription research candidate | MIT code / checkpoint license per artifact |
| Benchmark | **International Audio Laboratories Erlangen** — Müller · Zalkow · Özer · Krause et al. | [**synctoolbox**](https://github.com/meinardmueller/synctoolbox) — MrMsDTW + DLNCO score↔audio alignment for sheet-derived piano references (with [**libfmp**](https://github.com/meinardmueller/libfmp)) | MIT |
| Benchmark | **MIT Music & Theater Arts** — cuthbertLab | [**music21**](https://github.com/cuthbertLab/music21) — score toolkit pulled in by synctoolbox | BSD-3-Clause |
| Model | **CPJKU · JKU Linz** — Foscarin · Schlüter · Widmer | [**Beat This!**](https://github.com/CPJKU/beat_this) — beat / downbeat / meter (drives the editor grid) | MIT (code + weights) |
| Model | **Dream-High / RMVPE contributors** | [**RMVPE**](https://github.com/Dream-High/RMVPE) — optional external vocal F0 scaffold; checkpoint not bundled | Apache-2.0 code / checkpoint license pending review |
| Model | **MZehren / ADTOF contributors** | [**ADTOF**](https://github.com/MZehren/ADTOF) — optional external drum benchmark engine; not sidecar-bundled | CC BY-NC-SA 4.0 |
| Runtime | **ZFTurbo / MSST contributors** | [**Music-Source-Separation-Training**](https://github.com/ZFTurbo/Music-Source-Separation-Training) — optional external RoFormer/MSST separation command wrapper | MIT code / checkpoint license per artifact |
| Model | **Riley / Edwards / Dixon** | [**High-Resolution Guitar Transcription**](https://xavriley.github.io/HighResolutionGuitarTranscription/) — optional external guitar benchmark adapter; runtime/checkpoint not bundled | license review required before use |
| Model | **Kyutai × Mirelo** | [**MuScriptor**](https://github.com/muscriptor/muscriptor) — opt-in whole-mix multi-instrument transcription; MIT engine ships in the sidecar, gated weights are **not** bundled (fetched after the user accepts the license) | MIT code / CC BY-NC-4.0 weights (gated) |
| Runtime | **CPJKU · JKU Linz** — Böck et al. | [**madmom**](https://github.com/CPJKU/madmom) — DBN beat/downbeat post-processing for Beat This! | BSD-3-Clause code path |
| Model | **NYU MARL · Northwestern** — Kim · Salamon · Bello · Morrison | [**CREPE**](https://github.com/marl/crepe) + [**torchcrepe**](https://github.com/maxrmorrison/torchcrepe) — bass / guitar pitch | MIT |
| Runtime | **Meta Platforms** — PyTorch project | [**PyTorch**](https://github.com/pytorch/pytorch) (`torch` · `torchaudio` · `torchvision`) — ML runtime | BSD |
| Runtime | **Hugging Face** | [**transformers**](https://github.com/huggingface/transformers) — model architectures | Apache-2.0 |
| Runtime | **Lightning AI** — William Falcon | [**PyTorch Lightning**](https://github.com/Lightning-AI/pytorch-lightning) — inference scaffolding | Apache-2.0 |
| Runtime | **Phil Wang** (lucidrains) | [**x-transformers**](https://github.com/lucidrains/x-transformers) · [**rotary-embedding-torch**](https://github.com/lucidrains/rotary-embedding-torch) | MIT |
| Runtime | **NumFOCUS community** — Brian McFee et al. | [**NumPy**](https://github.com/numpy/numpy) · [**SciPy**](https://github.com/scipy/scipy) · [**scikit-learn**](https://github.com/scikit-learn/scikit-learn) · [**librosa**](https://github.com/librosa/librosa) (onset / CQT / tempo) | BSD · ISC |
| Runtime | **python-jsonschema project** — Julian Berman et al. | [**jsonschema**](https://github.com/python-jsonschema/jsonschema) — feedpak JSON Schema validation | MIT |
| Runtime | **Bastian Bechtold** · libsndfile team | [**soundfile**](https://github.com/bastibe/python-soundfile) — audio I/O | BSD (LGPL backend, dynamic) |
| Runtime | **Colin Raffel / pretty_midi project** | [**pretty_midi**](https://github.com/craffel/pretty-midi) — MIDI read/write for FeedPak and benchmark artifacts | MIT |
| Runtime | **Mido contributors** | [**mido**](https://github.com/mido/mido) — MIDI decoding for MT3-family outputs | MIT |
| Runtime | **PyYAML project** | [**PyYAML**](https://pyyaml.org/) — YAML manifest read/write | MIT |
| Runtime | **MIR Evaluation project** | [**mir_eval**](https://github.com/mir-evaluation/mir_eval) — transcription and music-evaluation metrics | MIT |
| Runtime | **SigSep community** | [**museval**](https://github.com/sigsep/sigsep-mus-eval) — internal MUSDB source-separation SDR benchmark | MIT |
| Runtime | **The FFmpeg project** | [**ffmpeg**](https://ffmpeg.org) — decode binary | LGPL-2.1+ (dynamic) |
| Runtime | **Microsoft** · **ONNX / LF AI & Data** | [**onnxruntime**](https://github.com/microsoft/onnxruntime) + [**onnx**](https://github.com/onnx/onnx) — Basic Pitch ONNX inference and opt-in drum-CRNN inference / export | MIT · Apache-2.0 |
| App | **Tauri WG · Commons Conservancy** | [**Tauri v2**](https://github.com/tauri-apps/tauri) — desktop shell + dialog/shell plugins | MIT / Apache-2.0 |
| App | **VoidZero** — Evan You · Anthony Fu | [**Vite**](https://github.com/vitejs/vite) · [**Vitest**](https://github.com/vitest-dev/vitest) — build & test | MIT |
| App | **Microsoft** | [**TypeScript**](https://github.com/microsoft/TypeScript) · [**Playwright**](https://github.com/microsoft/playwright) | Apache-2.0 |
| Native | **RustAudio + independent Rust** | [**cpal**](https://github.com/RustAudio/cpal) · [**rtrb**](https://github.com/mgeier/rtrb) · [**symphonia**](https://github.com/pdeljanov/Symphonia) · [**midir**](https://github.com/Boddlnagg/midir) · [**midly**](https://github.com/kovaxis/midly) — native audio/MIDI | Apache / MIT / MPL-2.0 |
| MR | **Meta Platforms** | [**Meta XR Core SDK**](https://developers.meta.com/horizon/documentation/unity/unity-package-manager/) — body / face / eye tracking for the Quest client's performance capture | Oculus SDK License (proprietary) |
| MR | **Cadson Demak** — Chakra Petch project authors | [**Chakra Petch**](https://github.com/google/fonts/tree/main/ofl/chakrapetch) — the MR client's interface typeface | SIL OFL 1.1 |
| MR | **Unity Technologies** | [**XR Interaction Toolkit**](https://docs.unity3d.com/Packages/com.unity.xr.interaction.toolkit@3.5/manual/index.html) · [**XR Hands**](https://docs.unity3d.com/Packages/com.unity.xr.hands@1.5/manual/index.html) · [**OpenXR**](https://docs.unity3d.com/Packages/com.unity.xr.openxr@1.14/manual/index.html) · [**AR Foundation**](https://docs.unity3d.com/Packages/com.unity.xr.arfoundation@6.1/manual/index.html) · [**Input System**](https://docs.unity3d.com/Packages/com.unity.inputsystem@1.14/manual/index.html) — the Quest client's runtime and interaction stack | Unity Companion / Package Distribution License |

*Lineage: Google/Magenta's **MT3** → Sony's **MR-MT3** (fine-tuned for drums).
Full per-component detail — including build/test-only deps — is in the tables
below.*

### Music-ML models & audio analysis (the differentiating stack)

| Component | Role in AuralPrimer | Made by | License (code / weights) |
|---|---|---|---|
| [**Demucs**](https://github.com/facebookresearch/demucs) (`htdemucs_6s`) | Stem separation (vocals/drums/bass/guitar/keys/other) | Meta AI / FAIR — Alexandre Défossez | MIT / **MIT** |
| [**MR-MT3**](https://github.com/gudgud96/MR-MT3) (`mr_mt3` ckpt) + [**mt3-infer**](https://github.com/openmirlab/mt3-infer) | Neural drum transcription (which drum + velocity) | Sony AI — Hao Hao Tan, Kin Wai Cheuk, Yuki Mitsufuji et al.; wrapper by *openmirlab* | MIT / **MIT** |
| [**MT3**](https://github.com/magenta/mt3) (lineage) | Base multi-track transcription architecture MR-MT3 derives from | Google Research · Magenta | Apache-2.0 / Apache-2.0 |
| [**YourMT3**](https://github.com/mimbres/yourmt3) + [checkpoint](https://huggingface.co/mimbres/YourMT3) (`yourmt3` ckpt) | Research/A-B drum and guitar transcription candidate; not the gameplay default | mimbres / YourMT3 authors | GPL-3.0 / **Apache-2.0** |
| [**Basic Pitch**](https://github.com/spotify/basic-pitch) | Piano / polyphonic melodic transcription | Spotify — Audio Intelligence Lab | Apache-2.0 / Apache-2.0 |
| [**piano_transcription_inference**](https://github.com/qiuqiangkong/piano_transcription_inference) + [Edwards robust checkpoint](https://zenodo.org/records/10610212) | PTI piano transcription and cleanup fallback for keys | Qiuqiang Kong / ByteDance; Edwards et al. robust checkpoint | MIT / **CC BY 4.0** |
| [**D3RM**](https://github.com/hanshounsu/d3rm) | Optional external piano transcription research candidate; not required for default imports | Kim, Kwon, Nam | MIT / checkpoint license per artifact |
| [**Beat This!**](https://github.com/CPJKU/beat_this) | Beat / downbeat / **meter** tracking (drives the editor grid) | CPJKU, JKU Linz — Foscarin, Schlüter, Widmer | MIT / **MIT** |
| [**madmom**](https://github.com/CPJKU/madmom) | Beat This! DBN post-processing (no madmom model files loaded) | CPJKU, JKU Linz — Sebastian Böck et al. | BSD-3-Clause / n/a |
| [**torchcrepe**](https://github.com/maxrmorrison/torchcrepe) + [**CREPE**](https://github.com/marl/crepe) | Monophonic bass / guitar pitch tracking | Max Morrison (Northwestern) · CREPE by NYU MARL (Kim, Salamon, Bello) | MIT / **MIT** |
| [**librosa**](https://github.com/librosa/librosa) | DSP: onset detection, CQT/spectrogram, tempo analysis | librosa dev team (Brian McFee, NYU) | ISC |
| [**RMVPE**](https://github.com/Dream-High/RMVPE) | Optional external vocal pitch/F0 scaffold; no checkpoint is bundled or hardcoded | Dream-High / RMVPE contributors | Apache-2.0 / checkpoint source license must be reviewed |
| [**ADTOF**](https://github.com/MZehren/ADTOF) | Optional external automatic drum transcription benchmark engine; not part of the frozen sidecar | MZehren / ADTOF contributors | CC BY-NC-SA 4.0 / CC BY-NC-SA 4.0 |
| [**Music-Source-Separation-Training**](https://github.com/ZFTurbo/Music-Source-Separation-Training) | Optional external RoFormer/MSST source-separation provider for MUSDB benchmarking | ZFTurbo / MSST contributors | MIT / checkpoint license per artifact |
| [**High-Resolution Guitar Transcription**](https://xavriley.github.io/HighResolutionGuitarTranscription/) | Optional external guitar transcription benchmark adapter; no runtime/checkpoint is bundled | Riley, Edwards, Dixon | license review required before use |
| [**MuScriptor**](https://github.com/muscriptor/muscriptor) | Opt-in whole-mix multi-instrument transcription (one pass over the full mix → per-instrument notes). The MIT engine is bundled in the sidecar; its **weights are gated on HuggingFace and never bundled** — each user accepts the license, then the weights download to their own HF cache (defaults to the `large` variant, ~5.5 GB; set `AURAL_MUSCRIPTOR_SIZE=medium|small` for smaller) | Kyutai × Mirelo | MIT / **CC BY-NC-4.0** (gated) |

### ML runtime & Python libraries

| Component | Made by | License |
|---|---|---|
| [**PyTorch**](https://github.com/pytorch/pytorch) (`torch`, `torchaudio`, `torchvision`) | Meta Platforms (PyTorch project) | BSD-2/3-Clause |
| [**PyTorch Lightning**](https://github.com/Lightning-AI/pytorch-lightning) | Lightning AI (William Falcon) | Apache-2.0 |
| [**transformers**](https://github.com/huggingface/transformers) | Hugging Face, Inc. | Apache-2.0 |
| [**onnxruntime**](https://github.com/microsoft/onnxruntime) (Basic Pitch ONNX inference; opt-in drum-CRNN inference) | Microsoft | MIT |
| [**onnx**](https://github.com/onnx/onnx) (drum-CRNN ONNX export; pinned 1.18.0 for ml-dtypes compat) | ONNX community (LF AI & Data Foundation) | Apache-2.0 |
| [**x-transformers**](https://github.com/lucidrains/x-transformers), [**rotary-embedding-torch**](https://github.com/lucidrains/rotary-embedding-torch) | Phil Wang (lucidrains) | MIT |
| [**NumPy**](https://github.com/numpy/numpy), [**SciPy**](https://github.com/scipy/scipy), [**scikit-learn**](https://github.com/scikit-learn/scikit-learn) | NumFOCUS-sponsored communities | BSD-3-Clause |
| [**jsonschema**](https://github.com/python-jsonschema/jsonschema) | python-jsonschema project (Julian Berman et al.) | MIT |
| [**soundfile**](https://github.com/bastibe/python-soundfile) (+ [**libsndfile**](https://github.com/libsndfile/libsndfile)) | Bastian Bechtold · libsndfile team | BSD-3-Clause (LGPL backend, dynamic) |
| [**pretty_midi**](https://github.com/craffel/pretty-midi) | Colin Raffel / pretty_midi contributors | MIT |
| [**mido**](https://github.com/mido/mido) | Ole Martin Bjørndalen / Mido contributors | MIT |
| [**PyYAML**](https://pyyaml.org/) | PyYAML project | MIT |
| [**mir_eval**](https://github.com/mir-evaluation/mir_eval) | MIR Evaluation project | MIT |
| [**museval**](https://github.com/sigsep/sigsep-mus-eval) | SigSep community | MIT |
| [**beartype**](https://github.com/beartype/beartype) | Cecil Curry | MIT |
| [**ffmpeg**](https://ffmpeg.org) (bundled binary) | The FFmpeg project | LGPL-2.1+ (dynamic) |
| [**synctoolbox**](https://github.com/meinardmueller/synctoolbox) (benchmark tool — score↔audio alignment) | International Audio Laboratories Erlangen (Müller · Zalkow · Özer · Krause · Prätzlich · Driedger) | MIT |
| [**libfmp**](https://github.com/meinardmueller/libfmp) (benchmark tool — synctoolbox dependency) | Meinard Müller · Frank Zalkow | MIT |
| [**music21**](https://github.com/cuthbertLab/music21) (benchmark tool — synctoolbox dependency) | cuthbertLab, MIT Music & Theater Arts | BSD-3-Clause |
| [**PyInstaller**](https://github.com/pyinstaller/pyinstaller) (build tool) | PyInstaller Development Team | GPL-2.0+ w/ bootloader exception |

### Desktop app, frontend & build

| Component | Made by | License |
|---|---|---|
| [**Tauri v2**](https://github.com/tauri-apps/tauri) (+ `@tauri-apps/api`, dialog/shell plugins) | Tauri Working Group / Commons Conservancy | MIT / Apache-2.0 |
| [**Vite**](https://github.com/vitejs/vite), [**Vitest**](https://github.com/vitest-dev/vitest) | VoidZero (Evan You / Anthony Fu) | MIT |
| [**TypeScript**](https://github.com/microsoft/TypeScript), [**Playwright**](https://github.com/microsoft/playwright) | Microsoft | Apache-2.0 |
| [**ajv**](https://github.com/ajv-validator/ajv) (JSON Schema) | Evgeny Poberezkin | MIT |
| [**fflate**](https://github.com/101arrowz/fflate) (in-browser zip) | Arjun Barrett | MIT |
| [**jsdom**](https://github.com/jsdom/jsdom) (test DOM) | jsdom project (Domenic Denicola et al.) | MIT |

### Native (Rust) crates

| Component | Made by | License |
|---|---|---|
| [**cpal**](https://github.com/RustAudio/cpal) (native audio out) | RustAudio | Apache-2.0 |
| [**rtrb**](https://github.com/mgeier/rtrb) (realtime ring buffer) | Matthias Geier | MIT / Apache-2.0 |
| [**symphonia**](https://github.com/pdeljanov/Symphonia) (pure-Rust decode) | Philip Deljanov | MPL-2.0 |
| [**midir**](https://github.com/Boddlnagg/midir) / [**midly**](https://github.com/kovaxis/midly) (MIDI I/O + `.mid` parse) | Patrick Reisert · Martín Andrighetti | MIT · Unlicense |
| [**tokio**](https://github.com/tokio-rs/tokio) (async runtime) | tokio-rs | MIT |
| [**socket2**](https://github.com/rust-lang/socket2) (multicast socket options) | rust-lang | MIT / Apache-2.0 |
| [**serde**](https://github.com/serde-rs/serde)(+`json`/`yaml`) | David Tolnay | MIT / Apache-2.0 |
| [**zip**](https://github.com/zip-rs/zip2), [**sha2**](https://github.com/RustCrypto/hashes), [**hex**](https://github.com/KokaKiwi/rust-hex), [**notify**](https://github.com/notify-rs/notify) | zip-rs · RustCrypto · rust-hex · notify-rs | MIT / Apache-2.0 / CC0 |

### Mixed-reality client (Unity packages)

Shipped inside the Quest APK built from `UnityClient/Aural Primer`. Unity's own
packages stay under Unity's terms — the [Unity Companion
License](https://unity.com/legal/licenses/unity-companion-license) (UCL) and,
for redistributed binaries, the [Unity Package Distribution
License](https://unity.com/legal/licenses/unity-package-distribution-license)
(UPDL). Both permit distributing a Unity-dependent project; neither is
GPL-compatible, which is exactly why the MR client is Apache-2.0 and sits
outside the copyleft boundary described under *Licensing* below.

| Component | Role in the MR client | License |
|---|---|---|
| [**XR Interaction Toolkit**](https://docs.unity3d.com/Packages/com.unity.xr.interaction.toolkit@3.5/manual/index.html) | ray and grab interaction — the grabbable menu, and every pointer press | UCL |
| [**Input System**](https://docs.unity3d.com/Packages/com.unity.inputsystem@1.14/manual/index.html) | the hand/controller action bindings XRI reads | UCL |
| [**XR Hands**](https://docs.unity3d.com/Packages/com.unity.xr.hands@1.5/manual/index.html) | joint poses for marking keyboard edges during calibration | UPDL |
| [**OpenXR Plugin**](https://docs.unity3d.com/Packages/com.unity.xr.openxr@1.14/manual/index.html) | the runtime, and the predicted display time the note lane is drawn against | UCL / UPDL |
| [**XR Plugin Management**](https://docs.unity3d.com/Packages/com.unity.xr.management@4.5/manual/index.html) | loader startup | UCL |
| [**AR Foundation**](https://docs.unity3d.com/Packages/com.unity.xr.arfoundation@6.1/manual/index.html) | passthrough session and camera, so the real keyboard stays visible | UCL |
| [**Meta OpenXR**](https://docs.unity3d.com/Packages/com.unity.xr.meta-openxr@2.1/manual/index.html) | Quest 3 / 3S / Pro feature set | UCL / UPDL |
| [**Android XR OpenXR**](https://docs.unity3d.com/Packages/com.unity.xr.androidxr-openxr@1.0/manual/index.html) | keeps the build multiplatform beyond Quest | UCL |
| [**XR Core Utilities**](https://docs.unity3d.com/Packages/com.unity.xr.core-utils@2.5/manual/index.html) | XR Origin and shared XR math | UCL |
| [**Universal Render Pipeline**](https://docs.unity3d.com/Packages/com.unity.render-pipelines.universal@17.3/manual/index.html) | the mobile-XR renderer | UCL |
| [**Meta XR Core SDK**](https://developers.meta.com/horizon/documentation/unity/unity-package-manager/) | body, face and eye tracking for performance capture — the only source for these on Quest; `OVRPlugin` reads them under the `com.meta.openxr.feature.metaxr` OpenXR feature | [Oculus SDK License](https://developers.meta.com/horizon/licenses/oculussdk/) (proprietary) |
| [**Chakra Petch**](https://fonts.google.com/specimen/Chakra+Petch) | the MR client's interface typeface — Bold for caps, SemiBold for reading; TTFs bundled and baked into TMP SDF atlases | [SIL OFL 1.1](https://openfontlicense.org/) |

> **Why a proprietary SDK is admissible here.** The Meta XR Core SDK is under
> the Oculus SDK License, which is *not* GPLv3-compatible and so would fail the
> gate above. It ships only inside `UnityClient/`, which is
> [Apache-2.0](UnityClient/Aural%20Primer/LICENSE) precisely because the Unity
> runtime cannot be sublicensed under the GPL — the same carve-out, for the same
> reason, and documented in that subtree's
> [NOTICE](UnityClient/Aural%20Primer/NOTICE). It must not be referenced from
> the GPL-3.0-or-later trees (`apps/`, `visualizers/`, `python/`, `crates/`).
>
> Body, face and eye tracking have no cross-vendor OpenXR equivalent on Quest,
> so the alternative was hand-binding the `XR_FB_*` extensions rather than a
> different open library.

Present from the Unity project template but **not used** by AuralPrimer code,
and listed here so the manifest and this table can be reconciled line by line:
XR Composition Layers, Newtonsoft Json (the client decodes with `JsonUtility`),
Test Framework, Android Logcat, AI Assistant, AI Inference, IET Framework and
Multiplayer Center. The last six are editor-only and never reach the APK.

> **License-gate note.** Checked against primary sources: (1) the *Demucs*
> `htdemucs_*` weights are **MIT** (the CC-BY-NC claim circulating in
> third-party repackagings conflates them with the 2019 Conv-TasNet research
> models); (2) `madmom`'s trained models are CC-BY-NC-SA — that once
> disqualified them under the project's former commercial gate, but under the
> current policy (open source, non-commercial acceptable) they are admissible.
> The Beat This! DBN path uses madmom's BSD-licensed processor code and does
> not load those madmom model files. Beat This! (MIT) remains the
> meter-tracking choice on quality and maintenance grounds, not licensing.
> Pin exact model revisions
> (HF/checkpoint) so a future card relicense can't change terms silently.

## Research & methodology — first class

We treat transcription quality as a research problem and publish the
numbers, the corpora, the algorithms, and the reproducibility commands
alongside the code. The work directly informs production defaults —
nothing in the ingest pipeline is shipped without head-to-head benchmark
evidence captured in a document below.

### Transcription performance snapshot

Current headline results from the checked-in benchmark evidence. F1 is the
greedy onset-matching transcription score used by
`aural_ingest gt-benchmark`; `P` and `R` are precision and recall. Some best
scores are research-only because model licensing, runtime packaging, or
human gameplay/listening review is still pending.

| Area | Best current result | Corpus / split | Cases | P | R | F1 | Status |
|---|---|---:|---:|---:|---:|---:|---|
| Keys / piano | [`piano_chord_supplement`](benchmarks/melodic/gt_runs/piano_synthetic_chord_supp_v1.json) | `piano_synthetic` | 4 | 0.981 | 0.880 | **0.928** | strongest scored piano path; small synthetic corpus |
| Guitar | [`qmul_hr_guitar`](benchmarks/guitar/gt_runs/guitarset_mic_limit40_qmul_hr_guitar.json) | GuitarSet mic test | 40 | 0.866 | 0.894 | **0.880** | best guitar F1; external research runtime |
| Guitar | [`qmul_hr_guitar`](benchmarks/guitar/gt_runs/guitar_techs_directinput_qmul_hr_guitar.json) | Guitar-TECHS direct-input | 104 | 0.908 | 0.819 | **0.861** | full-suite confirmation; external research runtime |
| Guitar | [`yourmt3_guitar`](benchmarks/guitar/gt_runs/guitarset_mic_limit40_yourmt3_vs_baselines.json) | GuitarSet mic test | 40 | 0.723 | 0.657 | **0.688** | available in `research_ab`, not default |
| Drums | [`yourmt3_drums`](benchmarks/drums/gt_runs/yourmt3_mr_mt3_test30.json) | E-GMD test | 30 | 0.619 | 0.564 | **0.590** | best drum corpus F1; still needs gameplay/listening review |
| Drums | [`drum_crnn` run-4](benchmarks/drums/gt_runs/egmd_stratified_30_drum_crnn_run4_calibrated.json) | E-GMD stratified test | 30 | 0.741 | 0.473 | **0.577** | best local neural reference; promotion still gated |
| Drums | [`adtof_drums`](benchmarks/drums/gt_runs/adtof_test30.json) | E-GMD test | 30 | 0.487 | 0.372 | **0.422** | runtime validated; research-only |
| Bass | [`melodic_torchcrepe`](benchmarks/bass/gt_runs/bass_hexdebleed_60_strict.json) | GuitarSet low-string strict | 60 | 0.452 | 0.140 | **0.214** | best strict-pitch bass proxy |
| Bass | [`melodic_combined`](benchmarks/bass/gt_runs/bass_hexdebleed_60_octaveforgiving.json) | GuitarSet low-string octave-forgiving | 60 | 0.326 | 0.538 | **0.406** | useful octave-forgiving sanity check |
| Vocals | `melodic_rmvpe` | MIR-ST500 test/vocal | pending | - | - | - | runtime/checkpoint ready; benchmark blocked on reviewed test audio |

Source-separation quality uses SDR rather than F1: default Demucs reaches
7.324 median SDR on the 10-track MUSDB test sample, while RoFormer reaches
9.027 and beats that baseline by 1.703 SDR. Those reports live under
[`benchmarks/quality/runs`](benchmarks/quality/runs).

### Top-tier transcription stack (per instrument)

Ingest auto-selects a **primary transcriber per instrument** — the
`gameplay_default` profile (`transcription.py`) — backed by a fail-safe
fallback chain so machines without the neural checkpoints still import.
The current production picks and research challengers:

| Instrument | Top-tier transcriber | Why it leads | Fallback chain |
|------------|----------------------|--------------|----------------|
| **Drums** | **`beat_conditioned_multiband_decoder`** — safe local gameplay default | stable density and no external checkpoint requirement; YourMT3 now leads the E-GMD test-30 corpus at **F1 0.590**, but profile/default promotion still needs psalm gameplay + listening review | spectral-flux multiband → adaptive-beat-grid → combined filter → DSP bandpass; YourMT3, MR-MT3, ADTOF, and drum-CRNN remain explicitly selectable for A/B |
| **Bass** | **`torchcrepe`** — neural monophonic pitch (MIT) | octave-clean and best strict low-string proxy among current bass candidates (**F1 0.214** strict; `melodic_combined` reaches **F1 0.406** only in octave-forgiving scoring) | YIN octave+HPS fix → adaptive → YIN-bass80 → octave-fix |
| **Guitar — lead** | **`torchcrepe`** — monophonic gameplay default | fast, local, and license-clean; `qmul_hr_guitar` is the current benchmark leader (**F1 0.880** GuitarSet, **0.861** Guitar-TECHS) but remains external/research-only | melodic-adaptive → octave-fix → combined → Basic Pitch; QMUL/YourMT3 are selectable for A/B |
| **Guitar — rhythm** | **`melodic_hpss_combined`** — HPSS + onset gameplay default | keeps the chord voices a monophonic tracker drops; external QMUL is the current quality leader where its runtime is configured | melodic-adaptive → octave-fix → combined |
| **Keys / piano** | **`piano_auto`** — scored gate: Basic Pitch → PTI (Edwards/Kong) cleanup | picks the best-scoring engine per stem; the tuned `piano_chord_supplement` path benchmarks **F1 0.928 / precision 0.981** on the synthetic piano corpus | PTI-consensus-clean → PTI-clean → polyphonic-clean |
| **Vocals** | **`melodic_rmvpe`** scaffold *(runtime/checkpoint validated; MIR-ST500 F1 pending)* | first pass at a karaoke-useful vocal pitch lane; lyrics remain the primary vocal surface; the strict gate is waiting on reviewed MIR-ST500 test audio and a full test/vocal benchmark | torchcrepe → pyin when RMVPE assets are absent |

Alternate profiles are selectable per import: `fidelity_midi` (denser
symbolic output for A/B review) and `research_ab` (every local candidate,
defaults unchanged). Distorted/electric guitar is a known frontier
(rendered-tone onset-F1 ceiling ~0.78–0.84) — see the amp-tone research doc.

Per-case breakdowns, JSON reports, reproducibility commands, and the
"what didn't work and why" notes live in the docs below.

### Benchmark corpora (what the numbers above are measured against)

Every shipped pick is scored against annotated ground truth, not vibes.
The corpora below back the head-to-head numbers; all are freely
redistributable and every link resolves. Adapters that turn each into the
canonical event list the `gt-benchmark` runner consumes live under
[`python/ingest/src/aural_ingest/dataset_adapters/`](python/ingest/src/aural_ingest/dataset_adapters/).

| Instrument | Benchmark corpus | License | What it validates |
|------------|------------------|---------|-------------------|
| **Drums** | [**E-GMD v1.0.0**](https://magenta.withgoogle.com/datasets/e-gmd) — Expanded Groove MIDI Dataset (Google Magenta; 444.5 h, 45k sequences, 43 kits) | CC BY 4.0 | per-class onset F1 (kick / snare / hi-hat / toms / cymbals), stratified across style · drummer · tempo |
| **Bass** | [**GuitarSet**](https://zenodo.org/records/3371780) — low strings (hex-debleeded E/A), used as a bass proxy | CC BY 4.0 | monophonic low-register pitch F1 |
| **Guitar** | [**GuitarSet**](https://zenodo.org/records/3371780) (mic) + [**Guitar-TECHS**](https://zenodo.org/records/14963133) (direct-input + amp-mic electric) | CC BY 4.0 | acoustic + electric note F1, lead vs rhythm |
| **Keys / piano** | in-house synthetic corpus (authored MIDI → rendered WAV) · paired Suno keyboard stems with reference MIDI · [**MAESTRO v3.0.0**](https://magenta.tensorflow.org/datasets/maestro) test subset (10 pieces, 19.7 min, Disklavier-aligned) | project-owned · CC BY-NC-SA 4.0 | isolated-note lower bound (synthetic) → worship-idiom accuracy (Suno stems) → canonical solo-piano accuracy comparable to published numbers (MAESTRO) |

Datasets carry attribution obligations (the CC BY family) — this table
is that attribution; keep it in sync when a benchmark round adds a corpus.
**Vocals** and **stem separation** currently ship without an in-house
benchmark; the intended corpora (MIR-ST500 for vocal pitch, MUSDB18-HQ for
separation SDR) and the harness to run them are specced in the
[model-upgrades plan](docs/model-upgrades-plan-2026-07-07.md).

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
  [E-GMD](https://magenta.withgoogle.com/datasets/e-gmd) /
  [GuitarSet](https://zenodo.org/records/3371780) /
  [Guitar-TECHS](https://zenodo.org/records/14963133), documents every tuned variant we
  tried (the wins AND the failures), and pins reproducibility commands
  so anyone can re-run a sweep with one `aural_ingest gt-benchmark`
  invocation. Covers drums, bass, guitar, and the round summary for
  keys (deep-dive doc above expands the keys section).

- [**ADT architecture deep-dive — 2026-05-07**](docs/research-deep-dive-adt-2026-05-07.md)
  2024–2025 ADT / transcription literature scan that revised 10
  architectural assumptions baked into the original pipeline. Each
  assumption is checked against published work, the resulting paths-
  forward list is the source of the current production-default trail.

- [**Electric-guitar amp-tone transcription — 2026-06-27**](docs/research-guitar-amptone-2026-06-27.md)
  Adversarially-verified research on the distorted/electric-guitar
  frontier: why general models collapse on real amp tone, the amp-tone
  augmentation lever (the dominant fix), license-clean datasets/assets,
  and a ranked plan. Sets the ~0.78–0.84 onset-F1 ceiling cited above.

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
   on F1 / precision / recall), mark it as a candidate; default/profile
   promotion still requires the relevant gameplay metrics and human
   in-game/listening review.
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
        /dataset_adapters   # E-GMD / GuitarSet / Guitar-TECHS / MIR-ST500 / piano_synthetic
        /ground_truth_benchmark.py
  /visualizers
    /viz-beats              # beat/section grid
    /viz-drum-highway       # drum lanes from host-provided MIDI events
    /viz-fretboard          # fretboard view using host-provided fingering metadata
    /viz-lyrics             # data-driven karaoke lyrics
    /viz-nashville          # chord/key lane from host-provided harmony artifacts
    /viz-tab                # piano-roll + tab renderer for keys/bass/guitar
  /benchmarks               # frontend/python/rust benches + thresholds.yml + reports
  /scripts                  # Node + PowerShell launchers for build/bench/portable
  /assets
    /models                 # reviewed local modelpacks/checkpoints; normally populated post-install
    /test_fixtures
  /docs
    /assets/screenshots
```

## Packaging stance ("no external runtime dependencies")

At runtime, users should not need a separate Python / FFmpeg / runtime
install:

- ingest tools ship as PyInstaller sidecar executables
- decoder binaries are bundled when needed
- ML model packs normally download / import post-install into `assets/models/`;
  reviewed, pinned packs can be staged into portable/release artifacts with
  id/version/hash/license metadata in the packaging manifest

See [`docs/packaging-ci.md`](docs/packaging-ci.md) for the full CI build
matrix.

## License

AuralPrimer is free software: you can redistribute it and/or modify it under
the terms of the [GNU General Public License](LICENSE) as published by the
Free Software Foundation, either version 3 of the License, or (at your
option) any later version. It is distributed in the hope that it will be
useful, but WITHOUT ANY WARRANTY — see the LICENSE file for details.

Third-party components, model weights, and datasets retain their own
licenses, catalogued in
[Third-party components & attribution](#third-party-components--attribution).

### Libraries permissive, applications copyleft

| Tree | Licence |
| --- | --- |
| `apps/`, `visualizers/`, `python/`, `crates/` | GPL-3.0-or-later |
| [`packages/`](packages/README.md) — shared libraries | **Apache-2.0** |
| `UnityClient/` — mixed-reality client | **Apache-2.0** |

The forcing constraint is the MR client: the Unity runtime is proprietary and
cannot be sublicensed under the GPL, so a GPL-licensed Unity build would be a
combined work that could not be conveyed under the GPL in full. Apache-2.0
removes that conflict outright rather than working around it with a GPLv3 §7
linking exception, and it avoids the same friction with app-store terms.

Because **licence compatibility runs one way** — Apache-2.0 code may be used by
a GPL work, but not the reverse — the shared libraries under `packages/` are
Apache-2.0 too. Otherwise every piece of logic both clients need would raise a
relicensing question on the way across. Nothing about the desktop client changes:
a GPL application consuming Apache-2.0 libraries is the direction that works.

`packages/feedpak/schemas/` is MIT per-file, set deliberately so the container
format stays unencumbered for interoperating tools.
