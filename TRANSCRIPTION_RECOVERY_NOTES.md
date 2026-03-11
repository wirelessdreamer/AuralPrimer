# AuralPrimer Transcription Recovery Notes (Memory Snapshot)

Date written: 2026-03-03  
Source: direct troubleshooting + build/debug history from the lost workspace.

Related detailed reference:

- `DRUM_TRANSCRIPTION_ALGORITHM_NOTES.md` (defaults, IDs, fallback ordering, King in Zion behavior profile)

## 1. High-level transcription flow

Last known architecture was:

1. Studio/Desktop frontend (TypeScript, Tauri webview) calls Rust commands.
2. Rust backend spawns a Python sidecar executable (`aural_ingest-...exe`).
3. Sidecar performs audio separation + transcription and writes SongPack outputs.
4. Frontend loads `charts/notes.mid` and `features/*.json` for gameplay.

## 2. Sidecar CLI shape (remembered)

The sidecar command was `aural_ingest` with subcommands:

- `stages`
- `validate <songpack_dir>`
- `info <songpack_dir>`
- `import <input_audio_path> --out <songpack>`
- `import-dtx <dtx_path> --out <songpack>`
- `import-dir <dir_path> --out <songpack>`

Important options:

- `--drum-filter` (drum algorithm selector)
- `--melodic-method` (`auto`, `pyin`, `basic_pitch`)
- `--shifts` (Demucs shifting count)
- `--multi-filter` (optional)

## 3. SongPack outputs (transcription products)

Expected outputs after import:

- `manifest.json`
- `audio/mix.wav`
- `audio/stems/*.wav` (drums, bass, guitar, keys, vocals, other when available)
- `charts/notes.mid`
- `features/beats.json`
- `features/tempo_map.json`
- `features/sections.json`
- `features/events.json`

## 4. Drum transcription behavior

### 4.1 Known algorithms

Algorithms referenced in code and UI:

- `combined_filter` (expanded kit target, preferred default)
- `dsp_bandpass_improved`
- `dsp_spectral_flux`
- `aural_onset`
- `adaptive_beat_grid` (core kit behavior)
- `dsp_bandpass`
- `librosa_superflux`

### 4.2 Default and fallback strategy

Last working default was `combined_filter` (not `adaptive_beat_grid`).

Remembered fallback behavior in `transcribe_drums_dsp`:

- If request is `auto` / `combined_filter` / `aural_onset` / `adaptive_beat_grid`, it tries an ordered candidate list.
- `combined_filter` path tried expanded-kit algorithms before adaptive fallback.
- Unknown algorithm names fell back to adaptive in algorithm factory, so passing wrong names could silently degrade to core-kit output.

### 4.3 Practical difference seen in debugging

- `adaptive_beat_grid` produced mostly core notes: `36`, `38`, `42`.
- `combined_filter` produced expanded notes (examples seen): `36`, `38`, `41`, `42`, `46`, `47`, `49`, `50`, `51`.

This difference was key to diagnosing the regression.

## 5. Melodic transcription behavior

### 5.1 Methods

- `auto`: route by instrument
  - Guitar/Keys preferred Basic Pitch
  - Bass often used pYIN fallback path
- `basic_pitch`: force Basic Pitch inference
- `pyin`: force pYIN monophonic path

### 5.2 Basic Pitch runtime details

Recovered model path logic in frozen runtime checked:

- `_MEIPASS/basic_pitch/saved_models/icassp_2022/nmp.onnx` (preferred)
- then `.tflite`
- then SavedModel dir `nmp`

If no model artifact found, transcription returned gracefully and fell back (`auto` path).

### 5.3 Known non-fatal warnings

Warnings were common and not necessarily failures:

- `Coremltools is not installed`
- `tflite-runtime is not installed`
- TensorFlow oneDNN info/deprecation messages

The true failure signature was:

- `basic_pitch.predict failed: ... nmp cannot be loaded ...`

This was fixed by bundling model assets and robust model-path selection.

## 6. Major regression we diagnosed (King in Zion)

Symptom:

- Retrancribed drums lost expanded kit and looked core-only again.

Primary root cause:

- Portable app was running an old sidecar binary, not the newly built one.
- UI defaults could look correct but old sidecar behavior still dominated output.

Observed proof pattern:

- New regressed output had drum notes mainly `36/38/42`.
- Running `combined_filter` directly on same drum stem produced expanded kit.

## 7. Critical fixes remembered

1. UI algorithm defaults set to `combined_filter` in Studio selectors and fallbacks.
2. Sidecar argument forwarding verified (`--drum-filter` passed through Rust command layer).
3. Build pipeline changed so Studio `tauri:build` runs sidecar build first.
4. Portable packaging ensured freshest sidecar copied into portable root.
5. Chart loader guard added to avoid dropping sparse dedicated drum tracks when melodic tracks are denser.
6. `import-dir` ordering bug fixed so `sections` exists before `events.json` export call.
7. Basic Pitch packaging improved with PyInstaller `--collect-data basic_pitch` and path fallback logic.

## 8. Regression tests that existed in last known state

Desktop chart parsing tests:

- `apps/desktop/tests/chartLoader.kingInZionRegression.test.ts`
  - fixture-based real regression check
- `apps/desktop/tests/chartLoader.test.ts`
  - synthetic strict/relaxed mapping behavior
  - dedicated sparse drums-track preservation case

Python ingest tests also included resilience/import tests for:

- drum fallback behavior
- melodic fallback behavior
- import pipeline continuity
- import-dir events export

## 9. Build/release details relevant to transcription

Remembered scripts:

- Root scripts called app-specific `tauri:build` and then portable pack script.
- Studio build script eventually chained sidecar build first.

Portable pack script behavior (important):

- Preserve existing `data` folder.
- Copy Desktop exe as `AuralPrimer.exe`.
- Copy Studio exe as `AuralStudio.exe`.
- Copy `resources`.
- Copy newest sidecar from app binaries into portable root.

If portable still showed regression after a code fix, stale sidecar timestamp/hash was first thing to verify.

## 10. If rebuilding from scratch

Recreate these components first:

1. Python sidecar CLI (`aural_ingest`) with `import`/`import-dir` flow.
2. Drum transcription module with selectable algorithm and fallback ordering.
3. Melodic transcription module with Basic Pitch + pYIN + fallback.
4. Tauri Rust command that forwards `--drum-filter` and `--melodic-method`.
5. Frontend selectors defaulting to `combined_filter`.
6. Portable packaging that copies the latest sidecar every build.
7. Regression tests for:
   - dedicated drum track not dropped
   - King in Zion style expanded-kit preservation

## 11. Loss-aware recovery plan (when codebase is restored)

If repo files are recovered from backup, replay this order:

1. Restore Python ingest defaults and fallback ordering first.
2. Restore desktop chart parser strict/relaxed guard.
3. Restore regression tests (desktop + python).
4. Rebuild sidecar binary.
5. Repack portable and confirm runtime sidecar freshness.
6. Reimport a known fixture song and compare note distribution.

Reason: UI-only changes are not sufficient if sidecar/runtime is stale.

## 12. Quick verification scripts to rerun

### 12.1 Algorithm A/B on one drums stem

Expected signature on King in Zion drums stem:

- adaptive: only `36/38/42`
- combined: includes `41/46/47/49/50/51` in addition to core notes

### 12.2 Generated MIDI sanity check

Parse `charts/notes.mid` and report channel-9 unique notes:

- bad profile: `[36, 38, 42]`
- good profile: includes crash/ride/toms as well

### 12.3 End-to-end import-dir default check

Run `import-dir` without explicitly passing `--drum-filter` and verify output behaves as combined_filter path.

## 13. Configuration and runtime traps to remember

1. Portable app can bypass `%APPDATA%` settings and use local `data/`.
2. Missing/unknown algorithm IDs may silently degrade behavior if factory fallback remains adaptive.
3. Old sidecar in portable root can completely hide code fixes.
4. A successful import can still hide partial failures in logs (for example `events.json` export issue).

## 14. Minimum evidence to collect in any future regression ticket

1. Exact command invocation (or UI import mode used).
2. Runtime sidecar path + timestamp/hash.
3. `notes.mid` channel-9 unique-note histogram.
4. Same-stem algorithm comparison (`adaptive` vs `combined`).
5. Test outputs for ingest + chart parser suites.

Without these five artifacts, regressions tended to be misattributed to the wrong layer.
