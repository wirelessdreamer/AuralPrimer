# Model upgrade progress checkpoint - 2026-07-07

This is the implementation checkpoint for
`docs/model-upgrades-plan-2026-07-07.md`. It tracks the current worktree
state, not a shipped release.

Recent local close-outs: `npm run portable:verify-sidecar` now covers the
generated sidecar audit by hashing every staged sidecar copy, validating
sidecar/portable manifest freshness against ingest source metadata, and proving
frozen `runtime-check` resolves model-upgrade evidence from the repo root, the
portable root, and an explicit `AURAL_MODEL_UPGRADE_EVIDENCE_ROOT`. Strict
model-upgrade evidence handling is explicit: default runtime-check reports the
evidence root/checklist, while strict mode still exits nonzero until
manual/external promotion gates have durable reports. The external runtime
validators and benchmark runners can now write those durable gate reports
directly with `--write-gate-evidence`, respecting
`AURAL_MODEL_UPGRADE_EVIDENCE_ROOT` and the
`benchmarks/runtime/runs/*_..._runtime.json`,
`benchmarks/quality/runs/*_musdb_separation_sdr.json`, and
`benchmarks/vocals/gt_runs/*_mir_st500_vocals.json` patterns strict
`runtime-check` consumes. Benchmark runners now keep their default reports in
exploratory output directories outside the strict gate globs unless
`--write-gate-evidence` or an explicit `--output` is supplied. Gate-evidence
writes now also fail fast unless the command includes the checks strict mode
will consume: MUSDB requires `--split test`, ADTOF/DrumSep require
`--require-events`, QMUL requires `--require-notes`, and RoFormer requires the
four MUSDB roles.
When the env var is blank or unset, those writers
and the Beat This manual-review helper now mirror runtime-check root
resolution by accepting a cwd that contains the gate checklist and otherwise
falling back to the repo root, instead of writing durable evidence under an
arbitrary caller cwd. Runtime validators also write failed same-identity gate
evidence for missing input audio when `--write-gate-evidence` is requested, so
a failed retry can supersede an older passing runtime validation. Source
`runtime-check` now uses the same checklist-based
root detection, while the frozen sidecar keeps its portable cwd fallback for
packaged runs. Beat This review evidence now requires the correct
gate/version/source metadata, non-`TODO` reviewer metadata, an ISO-8601 UTC
`reviewed_at_utc` value ending in `Z`, and all reviewed approval flags before
strict runtime-check can clear the gate. Evidence glob scans now skip unreadable or non-regular candidate
paths before sorting and order stamped reports by the UTC timestamp in the
evidence filename before falling back to filesystem modified time, so touching
or copying an older success cannot outrank a newer failed report and a stale or
bad report match cannot crash `runtime-check` before the gate can fail closed.
Runtime evidence tests now reject malformed `events[]` payloads across
JSON-number `time`, integer `note`, integer `velocity`, and optional
JSON-number `duration` fields, plus malformed `notes[]` payloads across
JSON-number `t_on`/`t_off`, integer `pitch`, integer `velocity`,
non-empty `instrument`, and optional `string`/`fret` fields.
Runtime reports are
latest-authoritative per engine-specific glob; MUSDB and MIR-ST500 benchmark
reports skip unrelated providers/modelpacks/algorithms, then treat the newest
matching identity report as authoritative so a newer same-identity failure
keeps that gate pending. MUSDB gate reports must also record
`dataset="musdb18_or_musdb18_hq"` and `split="test"` before they can satisfy
strict runtime-check, and the runner now rejects gate-evidence writes that
omit the test split instead of producing a report strict mode will later
reject. Runtime validators now do the same for missing promotion checks
(`--require-events`, `--require-notes`, or the RoFormer four-role set) before
writing strict gate evidence. The sidecar artifact verifier
now also asserts the frozen runtime's model-upgrade gate detail map and
`ready`/`pending` lists cover the complete expected gate set in normal and
strict packaged checks, so a gate cannot silently disappear from packaged
enforcement.
Game and desktop now
synthesize melodic availability/visualizer tracks from validated
`aural_fingering` sidecars when MIDI is missing, accept generic `guitar`
fingering fallbacks for concrete guitar lanes, pass host note `role` and
`instrument` metadata through visualizer contexts, and `viz-drum-highway`
filters host notes to drum-channel or drum-named tracks before GM drum mapping.
`npm run ci:verify:model-upgrade-gates` now guards the repo-side evidence
contract by checking the gate checklist, runtime-check constants, Beat This
review helper/template/smoke report, and this live progress doc stay aligned.
It also executes source `runtime-check` in normal and
`--require-model-upgrade-gates` modes, proving the emitted
`model_upgrade_gates` root/checklist fields, gate ID set, ready/pending
partition, and strict-mode exit behavior against the current checkout.
The 2026-07-10 local gate snapshot still exits nonzero in strict mode, but
now has durable runtime/benchmark evidence for `adtof_external_runtime`,
`demucs_ft_drums_sdr`, `drum_stemsep_external_runtime`,
`musdb_sdr_baseline`, `qmul_hr_guitar_external_runtime`, and
`roformer_musdb_comparison`; the remaining pending gate IDs are
`beat_this_barline_listening_review` and `rmvpe_mir_st500_vocals`.
`benchmarks/thresholds.yml` also now carries warn-mode model-upgrade decision
checks for the completed benchmark conclusions: YourMT3 drums useful but not
default-promoted, ADTOF drums useful but not default-promoted, YourMT3 guitar
positive on GuitarSet but negative as a broad Guitar-TECHS directinput
default, QMUL guitar positive on both GuitarSet and Guitar-TECHS as an
external research candidate, torchcrepe's strict bass proxy lead, and the
current deterministic GuitarSet key/chord baselines. The threshold checker has focused tests for
accepting matching model-upgrade decision reports and reporting contradictory
benchmark evidence with decision/check context.

Current CLI feedpak validation resolves the model-upgrade artifact pointers
(`arrangements[].file`, `keys`, `harmony`, `vocal_pitch`,
`vocal_pitch_contour`, `aural_notes_mid`, `aural_fingering`,
`aural_refine_candidates`, spectrogram outputs, and benchmark outputs) and
rejects missing, unparsable, absolute, or escaping references. It also
schema-validates the manifest plus known JSON sidecars for arrangements,
lyrics, timeline, drum tabs, keys, harmony, vocal pitch, vocal pitch contour,
and `aural_fingering`.
The TypeScript zip feedpak loader and Rust `auralsong-core` zip scanner now
have parity tests against the same minimal feedpak fixture, covering
manifest-declared `song_timeline`, `drum_tab`, `keys`, `harmony`,
`vocal_pitch`, `vocal_pitch_contour`, `aural_notes_mid`, and
`aural_fingering` pointers for zipped packages as well as directory packages.
Game/desktop native reads and the standalone TypeScript feedpak loader now use
the same safe relative-path contract for manifest pointers, so unsafe paths are
not reported as present and cannot be read through helper APIs; repeated-slash
and `.` path segments are rejected consistently across all three. Desktop
feedpak details/readiness flags now require safe, existing manifest targets for
lyrics, notes MIDI, timeline, `drum_tab`, model artifacts, and default-stem
audio. The feedpak manifest schema also validates the AuralPrimer `aural_*`
extension pointers against the same relative-path rule, and the shared
`auralsong-core` feedpak scan summary uses safe, existing targets for its
availability flags, including keys, harmony, vocal pitch, vocal pitch contour,
and `aural_fingering`. The schema-level sidecar contract now rejects
unsupported fingering manifest roles, requires positive note/sample durations
for vocal pitch and contour sidecars, and the RMVPE contour writer drops
zero-Hz frames instead of emitting schema-invalid contour samples.

Current benchmark runners fail closed on empty evidence: the MUSDB SDR runner
exits nonzero when no track reaches a successful SDR evaluation, and the
GuitarSet key/chord scripts exit before writing reports when corpus discovery
or filters produce zero cases. MUSDB role-level SDR evaluation also fails the
track when any declared required role cannot be loaded as audio, rather than
scoring a partial stem set.
External research-runtime wrappers now preflight configured paths before
launching subprocesses: ADTOF validates its official repo module and TensorFlow
checkpoint files, DrumSep rejects checkpoint directories and invalid optional
repo paths, and RoFormer/QMUL reject Python paths that are not files or repo
paths that are not directories.
The ADTOF setup path pins the ADTOF, tapcorrect, and madmom Git revisions used
by this scaffold, with a source test guarding against a return to an unpinned
tapcorrect install.
RMVPE setup now pins the reviewed official repo commit, the checkpoint
installer requires a reviewed SHA-256 plus HTTPS for URL sources, writes
`rmvpe.checkpoint.json`, and the runtime refuses to call PyTorch inference
unless the checkpoint digest matches that manifest or
`AURAL_RMVPE_CHECKPOINT_SHA256`.

Packaging verification has been refreshed after the ingest-sidecar changes:
PyInstaller produced a new frozen `python/ingest/dist/aural_ingest.exe`
(`sha256=21f4dab54f708358facc227ea483d7151ac80a4514376c058add071e4691de59`,
`source_last_write_utc=2026-07-09T06:08:45.6525235Z`,
`source_size_bytes=3130976821`). The frozen sidecar `runtime-check`
completed successfully in default mode, while
`runtime-check --require-model-upgrade-gates` exits nonzero with the expected
pending promotion gates and reports `evidence_root`,
`evidence_root_env_var`, and the evidence checklist at
`benchmarks/runtime/model_upgrade_gate_evidence.md`. Running the frozen
sidecar from outside the repo honors `AURAL_MODEL_UPGRADE_EVIDENCE_ROOT`.
Then
`build_sidecar.ps1 -SyncTauriBinaries` regenerated
`dist/sidecar/build_manifest.json`
while syncing desktop/game Tauri
binaries with the same hash. The final sidecar manifest after the portable
skip-build refresh records `skip_build=true`,
`built_at_utc=2026-07-09T06:15:15.6875697Z`,
`sha256=21f4dab54f708358facc227ea483d7151ac80a4514376c058add071e4691de59`,
and ingest-source freshness metadata
(`ingest_source_last_write_utc=2026-07-09T05:52:55.6208728Z`,
latest source `python/ingest/src/aural_ingest/cli.py`). The packaging scripts
now fail `-SkipBuild` / `-SkipSidecarBuild` when ingest source is newer than
the existing sidecar.
`create_portable.ps1 -SkipGameBuild
-SkipStudioBuild -SkipSidecarBuild` repacked `AuralPrimerPortable`; both
portable sidecar entrypoints and both Tauri runtime copies match the same hash,
and portable `runtime-check` passes from the portable root while resolving the
portable Demucs modelpack, ffmpeg, and MT3 assets. Portable strict gate mode
also exits nonzero with the expected pending model-upgrade gates and evidence
checklist path. The portable
package now includes `THIRD_PARTY_NOTICES.md` for the bundled FFmpeg runtime; the root
notice, portable copy, and `portable_manifest.json` `third_party_notices`
entry all match
`sha256=d62af7f35ff022ea0ab64ec9ca8886a0a74e97fc2be7a66c1d6499b215a04179`.
It also includes the project `LICENSE`; the root license, portable copy, and
`portable_manifest.json` `project_license` entry all match
`sha256=3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986`.
The final portable manifest was refreshed at
`built_at_utc=2026-07-09T06:16:24.4761589Z`, records sidecar
`build_manifest_sha256=5df9338261b0fa43797f8851a78d38344219a9607b8753fc776dfc10751b51ce`,
and has manifest hash
`sha256=5e8afd5d9c2026be6193883964afaa4d8aeff8edc989a2555b44f21926c62f29`,
and records license metadata for the staged Demucs, MT3-family, drum-CRNN,
PTI, and D3RM checkpoint assets (D3RM remains explicitly marked
artifact-license-review-required).
The refreshed binaries/manifests are build artifacts and remain outside the
tracked source diff.
`npm run portable:verify-sidecar` now closes the generated-artifact audit gap
by hashing every staged sidecar copy, checking `build_manifest.json` and
`portable_manifest.json` freshness metadata against the current ingest source
tree, and proving frozen `runtime-check` resolves and enforces model-upgrade
evidence from the repo root, the portable root, and an explicit
`AURAL_MODEL_UPGRADE_EVIDENCE_ROOT` override in both normal and strict modes.
Local packaging guards now also cover the repo-completable audit gaps:
`scripts/verify-transcription-recovery.mjs` checks the current
`beat_conditioned_multiband_decoder` quality-default drum chain and the
desktop import UI defaults to `auto` profile routing; `runtime-check` reports
RoFormer runtime diagnostics and the optional `demucs_ft_drums` modelpack
asset; `build_sidecar.ps1` installs the ingest package with `pyproject.toml`
runtime dependencies mirrored in `python/ingest/requirements-runtime.txt`
before PyInstaller, handles Basic Pitch separately for the ONNX path on
Python 3.13, and emits a platform-specific sidecar executable name; and
`create_portable.ps1` now requires staged
`modelpack.json` assets to declare a non-empty `license` before recording that
metadata in `portable_manifest.json`. README attribution now includes the
optional PTI and D3RM piano assets.
Current OaF/drum-CRNN setup guidance and source docstrings now match the
updated model policy: ADTOF's CC BY-NC-SA assets remain available as explicit
research adapters with non-commercial/ShareAlike obligations, while E-GMD
trained in-house weights remain the clean production-default path.

Workspace-level TypeScript verification now passes with `npx tsc -p
tsconfig.json --noEmit`. The root package barrel avoids the legacy
`ValidationResult` / refinement `ValidationResult` export collision by
exporting the refinement result as `RefinementValidationResult`, and the game
song-library runtime tests now have local `jsdom` typing plus matcher syntax
that matches the installed Vitest types.

Game chart loading now resolves manifest-declared `aural_notes_mid` and
`drum_tab` paths before falling back to legacy `features/notes.mid`,
`aural/notes.mid`, and `drum_tab.json`, so plugin/UI gates and the actual
runtime chart reads agree for non-default feedpak paths. If the declared or
legacy notes-MIDI reads fail or return an invalid blob, the game still falls
back to manifest/root `drum_tab` before returning an empty chart, preserving
drum-tab-only gameplay for stale or missing notes MIDI pointers.

Desktop chart loading now resolves manifest-declared `drum_tab` before falling
back to `aural_notes_mid` or legacy `features/notes.mid`, refreshes
capability/plugin gates during song selection, and passes drum-tab-derived
velocity through the visualizer song context. Desktop MIDI parsing now matches
the game host's tempo-aware seconds timing, note-off handling, and same-key
retrigger duration behavior, so visualizer plugins receive melodic notes from
`notes.mid` rather than only drums or sidecar-only fingering notes. Desktop
player instrument availability now derives from those parsed MIDI melodic
roles, keeps repicked player state in sync with the DOM, and applies the
song's initial `song_timeline` meter to the transport like the game host.
Studio cleanup readiness, the Refine drum lane editor, and the snap-to-onsets
sidecar path now use manifest-declared `drum_tab` paths before falling back to
root `drum_tab.json`, so custom feedpak drum-tab pointers do not silently
switch back to the legacy filename during cleanup. Desktop Refine now also
follows manifest-declared `song_timeline` paths for beat grid/snap/metronome
loading instead of hard-coding root `song_timeline.json`.

Game now resolves manifest-declared `song_timeline` paths, falls back to the
legacy root `song_timeline.json` only when no manifest pointer exists, and
passes the loaded timeline through the visualizer song context. The beat
visualizer renders explicit `song_timeline.beats[]` / downbeats when present
and otherwise uses the host transport BPM/time signature instead of a fixed
60 BPM placeholder grid. Desktop now follows the same visualizer-context path
for manifest-declared `song_timeline` so the bundled beat visualizer can use
real timeline beats in either host.

Game Tauri artifact reads now enforce safe in-container relative paths for
manifest JSON/MIDI pointers and legacy `features/*` reads, rejecting empty,
absolute, drive-prefixed, backslash, or parent-traversal paths before container
I/O.

Game and desktop visualizer contexts now carry manifest-referenced `keys.json`
and `harmony.json`; the HUD and Nashville visualizer prefer those
model/authored key documents before falling back to note-derived key inference.
`viz-nashville` also renders a top chord band from `harmony.events[]`,
preferring Roman-numeral labels when present, including harmony-only packs with
no melodic note stream.
Game and desktop host-provided visualizer notes now also include explicit
`role` and `instrument` metadata, so plugins do not have to infer instrument
identity from channel numbers or track-name text.

The bundled lyrics visualizer now consumes `vocal_pitch.json` and
`vocal_pitch_contour.json` from the visualizer song context, drawing a compact
vocal pitch lane from note blocks and contour samples even when timed lyrics
are absent.

Game and desktop song details/capability surfaces now report model-artifact
availability for `keys.json`, `harmony.json`, `vocal_pitch.json`,
`vocal_pitch_contour.json`, and `aural/fingering.<role>.json` based on native
detail flags. Desktop also passes loaded vocal pitch and contour artifacts to
visualizers, merges loaded `aural_fingering` sidecars into MIDI-derived
visualizer note metadata, and enables `viz-lyrics` for lyrics, vocal-pitch, or
contour-only packs.

Python package metadata has been regenerated so versioned egg-info files in the
worktree include the new model-upgrade dependencies/modules. Direct runtime
imports used by FeedPak writing, YAML manifests, MT3 MIDI decoding, and
evaluation metrics are now declared in `pyproject.toml` and attributed in
README (`pretty_midi`, `PyYAML`, `mido`, and `mir_eval`), with tests guarding
those declarations. Feedpak writer tests now validate emitted `drum_tab.json`
against `drum-tab.schema.json`, and the QMUL setup smoke command uses the
GuitarSet adapter's expected `guitarset_mono_mic` directory layout. The
PyInstaller sidecar spec now explicitly collects the model-upgrade runtime
packages used by feedpak validation, internal source separation benchmarking,
MIDI/YAML artifact handling, and ONNX-backed transcription (`jsonschema`,
`museval`, `mir_eval`, `mido`, `pretty_midi`, `yaml`, `onnx`, and
`onnxruntime`). The README attribution tables now also distinguish optional
external scaffolds (`RMVPE`, `ADTOF`, RoFormer/MSST, and QMUL high-resolution
guitar) from bundled/runtime dependencies, with explicit notes that their
runtimes/checkpoints are not bundled and still require artifact-level license
review where applicable. Portable packaging now requires and copies the root
GPL `LICENSE`, records it under `portable_manifest.json` `project_license`,
and the packaging CI notes now describe reviewed/pinned modelpack staging
rather than claiming all model weights are post-install-only.

Feedpak artifact schema coverage now validates the minimal fixture's
`song_timeline`, `drum_tab`, `keys`, `harmony`, `vocal_pitch`,
`vocal_pitch_contour`, and `aural_fingering` sidecars, including a local
AuralPrimer schema for `aural/fingering.<role>.json`; that fingering schema
now accepts the generic `guitar` role used by the writer and the Python writer
tests validate copied fingering sidecars against it. Game and desktop
fingering loaders now also accept generic `guitar` sidecars as fallbacks for
concrete lead/rhythm guitar roles, including manifest-declared
`aural_fingering.guitar` paths. The game chart loader can now synthesize
melodic track selections directly from validated `aural_fingering` sidecars
when a FeedPak has no usable `aural_notes_mid`, mapping generic `guitar`
sidecars to the lead-guitar lane for gameplay/visualizer availability; the
desktop host uses the same sidecar-to-track fallback when MIDI does not
produce melodic tracks, so Studio capability/player availability and
visualizers agree for fingering-only packs. Manifest role hints now also map
generic `guitar` to the lead-guitar lane, matching the generic fingering
fallback.
`viz-drum-highway` now filters host song notes to channel-9 or drum-named
tracks before applying GM drum pitch mapping, so melodic bass/keys notes at
drum MIDI pitches do not render as false drum hits.
`packages/feedpak` README and the AuralSong spec now document the active
FeedPak migration overlay, current model-artifact manifest pointers, and safe
relative-path availability contract.

Feedpak writer output now clears the target `<song>.feedpak` directory before
rewriting, preventing stale sidecars such as old `drum_tab.json` or fingering
files from surviving a later conversion whose manifest no longer declares
them. Refine-candidate note contracts now accept and preserve optional
`string`/`fret` and compact `s`/`f` metadata, and Python refine precompute
accepts vocals with an opt-in RMVPE candidate palette.

GuitarSet string/fret extraction now has a reusable corpus validator:
`benchmarks/guitar/validate_guitarset_fingering.py`. On the local GuitarSet
corpus it passed all four variants (`mic`, `pickup_mix`, `hex_original`, and
`hex_debleeded`) with 1,440 cases, 249,904 notes, aligned metadata for every
note, and zero invalid string/fret/open-string entries. The captured report is
`benchmarks/guitar/gt_runs/guitarset_fingering_validation_all.json`.

Guitar-TECHS adapter discovery and MIDI parsing now also have a reusable
corpus validator: `benchmarks/guitar/validate_guitar_techs_adapter.py`. On the
local corpus it passed both `directinput` and `micamp` signals with 208
signal-specific cases, 37,882 parsed reference notes, complete audio pairings,
the expected category/player buckets, and zero invalid notes or durations. The
captured report is
`benchmarks/guitar/gt_runs/guitar_techs_adapter_validation_all.json`.

Bass default evidence is now summarized in
`benchmarks/bass/bass_torchcrepe_eval.md`. On the GuitarSet low-string
`hex_debleeded` proxy, `melodic_torchcrepe` leads the 60-case strict-pitch
run (F1 0.214 vs 0.144 for `melodic_pyin_bass_strict` and 0.174 for
`melodic_combined`) while running much faster than the combined chain. The
adapter-local 200 Hz bass `fmax` clamp is now covered by a regression test.

## Status by item

| Item | Status | Current result | Remaining gate |
| --- | --- | --- | --- |
| T1.1 Beat This DBN | Implemented and locally verified | `meter_tracker.py` now defaults Beat This to DBN with env/config fallback and persists `postprocessor`; tests cover fallback behavior. The active ingest venv now imports pinned `madmom`, and `refresh-meter` DBN smokes passed on temp copies of three psalms: Psalm 121, Psalm 130, and Psalm 5. The local Beat This modelpack is installed under `assets/models/beat_this/1.0.0` from the cached `beat_this-final0.ckpt` checkpoint with SHA-256 `8c328b45f59d8dd3dff219253ff6a8d6482be57d0133a29140e2febbf8eb8331`; `meter_tracker.resolve_checkpoint({})` resolves that installed checkpoint and `available({})` returns true. Game runtime now resolves manifest `song_timeline` paths and `viz-beats` renders real timeline beats/downbeats when available. The Beat This review helper can now write the final evidence file with `--write-evidence`, but only when reviewer metadata and all three required case approvals are explicitly supplied. | Still needs human bar-line/listening review before treating the meter grid as promotion-complete. |
| T1.2 YourMT3/MR-MT3 drums | Implemented and benchmarked | `yourmt3_drums` and `mr_mt3_drums` adapters run through `gt-benchmark`; YourMT3 aggregate F1 0.590, macro-5 F1 0.668 on E-GMD test-30, while MR-MT3 trails badly. `gameplay_default` has been returned to the safe local drum chain (`beat_conditioned_multiband_decoder` first); MT3 engines remain explicitly selectable and in A/B profiles. | No default neural/modelpack promotion: drum-CRNN run-4 still leads macro-5 at 0.707 but remains gameplay/listening-review gated, and YourMT3 needs psalm listening/gameplay review before any profile reorder. |
| T1.3 ADTOF drums | Runtime validated and benchmarked | Import-safe subprocess adapter, setup docs/script, runner contract, and registry entry are present; an audit found the subprocess contract complete, and the isolated runner now has fake official-model/PrettyMIDI contract coverage. The setup script pins the ADTOF, tapcorrect, and madmom source commits. The official ADTOF repo/runtime is installed under `D:\AuralPrimer\.external\adtof`, and `python/ingest/scripts/validate_adtof_runtime.py --require-events --write-gate-evidence` passed on `assets/test_fixtures/drum_benchmark_midis/01_jrock_verse_chorus_160.wav` with 149 events, writing `benchmarks/runtime/runs/20260709_174906_782267_adtof_runtime.json`. Strict `runtime-check` now marks `adtof_external_runtime` ready. `benchmarks/drums/gt_runs/adtof_test30.json` scored the same E-GMD test-30 sample with 30/30 cases OK, aggregate F1 0.422, macro-5 F1 0.539, precision/recall 0.487/0.372, and mean runtime 11.238 s/case. That beats the current DSP default but trails YourMT3 and drum-CRNN run-4, so `benchmarks/drums/adtof_test30_eval.md` and `benchmarks/thresholds.yml` record ADTOF as useful research-only evidence, not a profile/default promotion. | External-runtime and E-GMD test-30 evidence are clear; before any profile/default promotion, still schedule psalm listening/gameplay review against the current drum default. |
| T2.4 Drum class split/velocity | Runtime validated | Drum hit velocity is threaded through desktop chart loading, game MIDI/drum-tab chart selection, and visualizer song contexts so drum visualizers receive real dynamics. Fine-class diagnostics and `drum_stemsep` opt-in scaffold are present. The adapter now exposes runtime diagnostics, rejects invalid checkpoint/repo path types before spawning, and `python/ingest/scripts/validate_drum_stemsep_runtime.py` validates the external runner/checkpoint contract before benchmarks. `python/ingest/scripts/run_drum_stemsep_msst.py` now wraps MSST `mdx23c`, writes temporary kick/snare/toms/hi-hat/ride/crash stems, detects energy onsets per separated stem, and emits the adapter's accepted `{"events": [...]}` contract; focused tests cover that request/output contract without loading the real checkpoint. A review-pending DrumSep MDX23C mirror is present under `D:\AuralPrimer\.external\drumsep\review_pending\politrees_uvr_resources` with checkpoint SHA-256 `d2a4aa53eb584d21eead358a4e66d1882ad182911be018f052b5da73be9096d0`; the canonical jarredou GitHub release URLs still return 404, and checked HF mirrors report conflicting repo-level licenses for the same binary. `validate_drum_stemsep_runtime.py --require-events --write-gate-evidence` passed on `assets/test_fixtures/drum_benchmark_midis/06_metal_blast_220.wav` with 241 events, writing `benchmarks/runtime/runs/20260709_223101_937032_drum_stemsep_runtime.json`; strict `runtime-check` now marks `drum_stemsep_external_runtime` ready. The TS feedpak loader now exposes the manifest `drum_tab` pointer, the minimal feedpak fixture covers drum hit velocity, game and desktop details/capability surfaces report `has_drum_tab`, and the drum-highway plugin is enabled for drum-tab-only packs even when `aural/notes.mid` is absent. | Runtime evidence is clear, but license/provenance review remains unresolved; keep the review-pending mirror out of sidecar/modelpacks/releases before any shipping decision, then benchmark crash/ride/tom separation quality. |
| T2.5 RMVPE vocals | Runtime assets validated | `melodic_rmvpe`, vocals role/channel plumbing, setup docs, runtime validator, checkpoint installer, MIR-ST500 vocal GT adapter/runner, and tests are present; the runner now has synthetic CLI coverage and inert no-checkpoint smoke returns `[]` cleanly. RMVPE now carries its raw F0 frames through melodic result metadata, raw `.auralsong` import writes `features/vocal_pitch.json` from vocal notes plus `features/vocal_pitch_contour.json` when contour samples are available, and contour metadata survives even if note segmentation produces no accepted notes. The adapter now reports checkpoint/repo/module readiness in `last_run.meta["runtime"]`, requires an explicit reviewed RMVPE repo path instead of ambient `src` imports, and fails before inference when the repo is missing, not a directory, or lacks `src/inference.py`. The checkpoint installer now requires reviewed `--expected-sha256`, rejects non-HTTPS URL sources, passes a download timeout, writes a checkpoint review manifest, and leaves the installed target untouched on hash mismatch; runtime inference also requires the current checkpoint digest to match that manifest or `AURAL_RMVPE_CHECKPOINT_SHA256`. The official RMVPE repo is installed at `D:\AuralPrimer\.external\RMVPE`, the reviewed checkpoint is installed at `assets/models/rmvpe/rmvpe.pt` with SHA-256 `6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193`, and `validate_rmvpe_runtime.py --write-gate-evidence` wrote `benchmarks/runtime/runs/20260709_174839_911045_rmvpe_runtime.json` with runtime ready. The MIR-ST500 adapter/runner now reports discovery diagnostics for missing `MIR-ST500_corrected.json`, split/case filters that match no annotations, missing `Vocal.wav`/mixture audio, invalid variants, and annotations with no valid notes instead of only exiting with a generic empty-corpus error. `python/ingest/scripts/prepare_mir_st500_root.py` now copies the official metadata into the local dataset root and writes a machine-readable preparation report; the current report `benchmarks/vocals/mir_st500_preparation_status.json` shows 500 annotations staged at `E:\AudioSourceOfTruthData\extracted\mir_st500`, 100/100 test vocal files missing, 100/100 test mixture files missing, active ingest venv intentionally lacking the heavy reconstruction-only packages, and the isolated prep Python at `D:\AuralPrimer\.external\mir-st500\.venv\Scripts\python.exe` reporting `yt_dlp`, `youtube_dl`, `spleeter`, and `tensorflow` available. Explicit `aural_ingest spectrogram --instrument vocals`, `aural_ingest refine-candidates --instrument vocals`, and `aural_ingest benchmark-transcribers --instrument vocals` are allowed; Studio readiness/picker logic and post-import candidate precompute can surface vocals when vocal artifacts are present, while `prep-arrangements` keeps authored vocal arrangements as a `Vocals` track instead of dropping them. Feedpak conversion emits schema-valid `vocal_pitch.json` from a Vocals MIDI track, preserves `features/vocal_pitch_contour.json` / legacy `features/pitch_contour.json`, stamps `pitch_extraction`, and the typed TS/Rust feedpak loaders expose the vocal pitch fields. Game and desktop now load manifest-referenced `vocal_pitch` / `vocal_pitch_contour` JSON, pass both documents through the visualizer song context, and enable `viz-lyrics` for lyrics, vocal-pitch, or contour artifacts so pitch-only packs can render note/contour lanes. `SETUP-RMVPE.md` now points verification at the notes MIDI, vocal-pitch contour sidecar, FeedPak `vocal_pitch` / `vocal_pitch_contour` fields, vocals spectrogram, and the full MIR-ST500 test/vocal gate-evidence command. | RMVPE runtime evidence and reconstruction dependencies are ready; the strict gate is still blocked on reviewed MIR-ST500 test audio and an unbounded `melodic_rmvpe` test/vocal benchmark under `AURAL_MIR_ST500_ROOT`. |
| T2.6a MUSDB SDR harness | Gate evidence ready | `quality_benchmark.py` has MUSDB discovery/evaluation helpers, `benchmarks/quality/run_musdb_separation_sdr.py` starts, and `test_musdb_separation_sdr_runner.py` exercises an end-to-end synthetic MUSDB-style fixture through a provider path with real `museval` SDR metrics. The evaluator now fails partial estimate sets missing required MUSDB roles or containing unloadable required-role audio instead of averaging incomplete tracks, and crops small per-role length drift to a common frame count before stacking. The active ingest venv imports `museval` 0.4.1. The official MUSDB18-HQ Zenodo archive was downloaded to `E:\AudioSourceOfTruthData\raw_datasets\musdb18_hq\musdb18hq.zip`, verified with MD5 `12d4f2ecd55245a4688754dd76363103`, and extracted to `E:\AudioSourceOfTruthData\extracted\musdb18_hq` with 100 train and 50 test tracks. The default Demucs test-split gate run wrote `benchmarks/quality/runs/20260709_232250_434347_demucs_musdb_separation_sdr.json`: 10/10 tracks OK, zero failed/skipped, aggregate median SDR mean 7.324, bass 7.117, drums 8.959, other 4.727, vocals 8.492. Strict `runtime-check` now marks `musdb_sdr_baseline` ready. | Baseline evidence is clear; keep using the verified local MUSDB18-HQ root for candidate comparisons. |
| T2.6b htdemucs_ft drums-only separator | Benchmarked, not promoted | The Demucs modelpack resolver now supports opt-in `demucs_ft_drums` discovery/selection, `htdemucs_ft` aliases, CLI/config selection, selected-id-aware cache keys, and manifest/runtime metadata reporting. Explicit `demucs_modelpack_zip_path` is authoritative instead of falling back to defaults when invalid. Demucs zip validation now requires safe single-weight paths, a present source URL, declared stem roles, a 64-hex `sha256`, and a zip-entry hash match before runtime use; `demucs_ft_drums` also requires manifest license metadata and a LICENSE file. The local `dist/modelpacks/demucs_ft_drums.zip` contains the upstream `f7e0c4bc-ba3fe64a.th` weight, Demucs MIT license text, and manifest SHA-256 `ba3fe64ae8ef66ac9a4857222ce48efbdc5eb3ad375cb79dd13debee5aaa4066`; `runtime-check` validates the modelpack as `ok=true`. Runtime extraction/cache keys no longer have a `nocheck` branch, and cached stem reuse now requires current mix/modelpack/weight metadata plus safe in-cache `.wav` filenames. The opt-in `demucs_ft_drums` separation path now runs a default `demucs_6` full-stem baseline, then replaces only `drums` with the fine-tuned output so MUSDB SDR reports can include bass, drums, other, and vocals. `create_portable.ps1` mirrors the modelpack weight/hash/license validation, accepts or auto-discovers an optional `demucs_ft_drums.zip`, stages it beside `demucs_6.zip`, and records it in `portable_manifest.json` when present. The default `auto` path still prefers the existing `demucs_6` modelpack unless a fine-tuned modelpack id/path is explicitly configured. `benchmarks/quality/configs/demucs_ft_drums.json` records the strict-gate config, and `benchmarks/quality/runs/20260709_234848_965718_demucs_demucs_ft_drums_musdb_separation_sdr.json` passed 10/10 MUSDB18-HQ test tracks with zero failed/skipped, aggregate median SDR mean 4.398, bass 4.129, drums 8.143, other -0.232, vocals 5.553. Strict `runtime-check` now marks `demucs_ft_drums_sdr` ready. | Evidence is clear, but the candidate underperforms the default Demucs baseline overall and on drums for this sample; do not promote it as a default/profile quality improvement. |
| T2.6c RoFormer separator | Comparison gate ready | Built-in `roformer` stem-separation provider is registered as a research external-command wrapper. It is inert when `AURAL_ROFORMER_*`/config are absent, preserves protected user-supplied stems during copy, and can be driven by the MUSDB SDR runner once a local MSST/RoFormer runtime and checkpoint are configured. The provider now exposes runtime diagnostics plus `validate_roformer_runtime`, and `python/ingest/scripts/validate_roformer_runtime.py` can prove a configured command emits required role-named stems before scheduling MUSDB SDR. `python/ingest/scripts/run_roformer_msst.py` adapts current MSST `inference.py` to AuralPrimer's single-mix command contract. The local MSST checkout is at `D:\AuralPrimer\.external\msst\Music-Source-Separation-Training` commit `ccf86c105f55a03e4df3b294e8d27613fef80c1f`; BS RoFormer MUSDB18HQ config/checkpoint are hashed in `.external\msst\models\bs_roformer_musdb18hq`, and `validate_roformer_runtime.py --require-role bass --require-role drums --require-role other --require-role vocals --write-gate-evidence` wrote `benchmarks/runtime/runs/20260709_180543_097966_roformer_runtime.json`. The first 10-track MUSDB attempt timed out two long tracks at the earlier 900 s per-track timeout; rerunning with `AURAL_ROFORMER_TIMEOUT_SEC=2400` wrote `benchmarks/quality/runs/20260710_024833_512208_roformer_musdb_separation_sdr.json` with 10/10 tracks OK, zero failed/skipped, aggregate median SDR mean 9.027, bass 7.940, drums 10.490, other 6.656, vocals 11.021. This is above the default Demucs baseline aggregate 7.324, so strict `runtime-check` now marks `roformer_musdb_comparison` ready. | Runtime and MUSDB comparison evidence are clear for research; decide separately whether RoFormer should influence any default/profile after runtime-cost and licensing/shipping review. |
| T3.7 guitar eval fix | Implemented and runtime-validated | 24-bit WAV read/write support fixes Guitar-TECHS directinput empty-audio failures; 4-case smoke and all 104 directinput cases now produce non-zero metrics through the real `gt-benchmark` path. `gt-benchmark` has `--min-duration-sec` / `--max-duration-sec`, and case-id filters now defer adapter `--limit` so GuitarSet/Guitar-TECHS/key/chord stratified samples do not collapse onto first-N corpus rows. `benchmarks/guitar/combine_gt_shards.py` combines non-overlapping shard JSONs into a full aggregate. `validate_guitar_techs_adapter.py` now validates both local Guitar-TECHS signals with 208 signal-specific cases, 37,882 reference notes, expected category/player buckets, and zero invalid items. A research-only `qmul_hr_guitar` command-wrapper adapter is now registered for the QMUL high-resolution guitar benchmark path. The adapter now exposes runtime diagnostics plus `validate_runtime`, `python/ingest/scripts/validate_qmul_hr_guitar_runtime.py` can prove the external command contract on one guitar stem before scheduling GuitarSet/Guitar-TECHS benchmarks, and QMUL JSON parsing now drops explicit string/fret metadata unless both members form a valid pair. `python/ingest/scripts/run_qmul_hf_midi.py` wraps the public `hf_midi_transcription` runtime to avoid Windows console encoding failures. The local runtime is installed at `D:\AuralPrimer\.external\qmul-hf-midi`, `guitar-fl.pth` is pinned at revision `689e773723bcafd8c81015b10c03f12675ce16ec` with SHA-256 `50d93dba89bdd3401849bc735614478e83d9f46d21fa3f71d8aca5acc0a52028`, and `validate_qmul_hr_guitar_runtime.py --require-notes --write-gate-evidence` passed on the local GuitarSet mic sample with 128 notes, writing `benchmarks/runtime/runs/20260709_181628_362263_qmul_hr_guitar_runtime.json`. Strict `runtime-check` now marks `qmul_hr_guitar_external_runtime` ready. `benchmarks/guitar/gt_runs/guitarset_mic_limit40_qmul_hr_guitar.json` scored 40/40 GuitarSet mic cases OK with F1 0.880, and `benchmarks/guitar/gt_runs/guitar_techs_directinput_qmul_hr_guitar.json` scored the full 104-case Guitar-TECHS directinput suite OK with F1 0.861. `benchmarks/guitar/qmul_hr_guitar_eval.md` and `benchmarks/thresholds.yml` now record QMUL as a strong external research candidate on both datasets. Tab renderer note types now accept `string`/`fret` and compact `s`/`f` metadata, `viz-tab` prefers explicit fingering before pitch-derived positions, and `viz-fretboard` now renders host-provided fretted metadata instead of a placeholder cursor. Import can emit `features/fingering.<role>.json`, feedpak conversion copies it to `aural/fingering.<role>.json`, writes schema-valid `arrangements/tab_<role>.json`, and both game and desktop visualizer contexts merge fingering onto melodic notes. The `aural_fingering` sidecar schema now accepts generic `guitar` as well as lead/rhythm roles, and writer tests validate copied fingering sidecars against that schema. Game and desktop loading now honor manifest `aural_fingering` pointer paths before falling back to conventional sidecar filenames, and the desktop Tauri read/write guard allows safe custom manifest JSON/MIDI pointer paths, so valid feedpaks with nonstandard fingering or note paths render correctly instead of failing at the native read boundary. | QMUL runtime and local benchmark evidence are clear for research; final license/shipping review plus gameplay/listening policy remain pending before any broader fretting-quality or default/profile claim. |
| T3.7.1 YourMT3 guitar | Implemented and benchmarked | `yourmt3_guitar` beats current guitar baselines on GuitarSet mic fast-5 (F1 0.708 vs 0.219), GuitarSet mic limit-40 (F1 0.688 vs 0.222/0.227), Guitar-TECHS DI smoke-4 (F1 0.524 vs 0.298), and Guitar-TECHS DI short-20 overall (F1 0.438 vs 0.208). On GuitarSet limit-40 it leads both comp (F1 0.676 vs 0.096/0.106) and solo (F1 0.725 vs 0.528/0.521). On the full 104-case Guitar-TECHS directinput aggregate, the current baseline leads overall (F1 0.347 vs 0.287) and on chords (F1 0.169 vs 0.121), scales (F1 0.663 vs 0.433), single-note drills (F1 0.851 vs 0.037), and techniques (F1 0.367 vs 0.028). YourMT3 still leads only the 12 P3 music cases (F1 0.479 vs 0.231). `research_ab` now exposes `yourmt3_guitar` for lead/rhythm guitar A/B review while `gameplay_default` remains unchanged. | Do not promote YourMT3 as the broad Guitar-TECHS directinput default. Any gameplay/default profile change needs a narrower hybrid policy plus listening/gameplay review. |
| T3.7 GT extensions | Implemented + runtime pass-through | GuitarSet adapter now exposes string/fret note metadata, chord events, and key events. Refinement note schema, game chart note types, plugin SDK note context, `viz-tab`, and `viz-fretboard` now preserve/use optional `string`/`fret` or `s`/`f` metadata so exact authored fingering can render when present. The non-MIDI fingering sidecar now survives import, feedpak conversion, game loading, and feedpak arrangement export for generated or adapter-provided metadata. Ordinary bass/guitar imports now also get a conservative sequence/chord-aware standard-tuning sidecar that preserves authored string/fret metadata, infers positions for notes without metadata, and avoids duplicate strings inside same-onset groups when valid alternatives exist. `validate_guitarset_fingering.py` now validates real GuitarSet string/fret/open-string metadata across all four local variants; the current report covers 1,440 cases and 249,904 notes with zero invalid entries. | Needs a higher-quality or externally validated fretting producer and/or non-GuitarSet authored tab review if deterministic placement is not musically acceptable; QMUL runtime/checkpoint validation remains external. |
| T3.8 key | Implemented and benchmarked | Deterministic Krumhansl note-profile key pass writes `keys.json`; GuitarSet mic full key+mode accuracy is 0.658. Game and desktop HUDs now prefer explicit manifest/key-artifact metadata over client-side note inference, and both hosts pass loaded key documents through the visualizer song context. | Audio-key model route remains optional/future. |
| T3.8 chords | Baseline implemented and benchmarked | Deterministic note-profile chord pass writes non-empty `harmony.json`; GuitarSet mireval full root+quality accuracy is 0.266. Game and desktop read manifest-referenced `harmony.json`, pass it through the visualizer song context, and let `viz-nashville` render authored chord events when present. | Audio chord model/segmentation remains future work. |
| T3.9 piano | No action | Intentionally untouched per plan. | Coordinate separately with piano PTI/D3RM work. |

Current remaining-gate handoff: Beat This still needs real human
bar-line/listening review, but the helper can now write the final gate evidence
with `--write-evidence` only when reviewer metadata and all three required case
approvals are supplied. MIR-ST500 still needs reviewed test-set audio
preparation; `benchmarks/vocals/mir_st500_preparation_status.json` now records
`external_dependencies_ready=true`, `reconstruction_dependencies_ready=true`,
`audio_source_review_required=true`, and
`audio_reconstruction_status=missing_test_audio`.

## Primary artifacts

- Drums: `benchmarks/drums/yourmt3_mr_mt3_test30_eval.md`
- Drums ADTOF: `benchmarks/drums/adtof_test30_eval.md`
- Meter: `benchmarks/meter/beat_this_dbn_refresh_meter_smoke.md`
- Bass torchcrepe: `benchmarks/bass/bass_torchcrepe_eval.md`
- Guitar 24-bit/YourMT3: `benchmarks/guitar/yourmt3_guitar_smoke_eval.md`
- Guitar shard combiner: `benchmarks/guitar/combine_gt_shards.py`
- QMUL guitar setup: `python/ingest/scripts/SETUP-QMUL-HR-GUITAR.md`
- QMUL public-runtime wrapper: `python/ingest/scripts/run_qmul_hf_midi.py`
- QMUL guitar runtime validator:
  `python/ingest/scripts/validate_qmul_hr_guitar_runtime.py`
- QMUL guitar benchmark eval: `benchmarks/guitar/qmul_hr_guitar_eval.md`
- Guitar-TECHS adapter validator: `benchmarks/guitar/validate_guitar_techs_adapter.py`
- Guitar-TECHS adapter validation report: `benchmarks/guitar/gt_runs/guitar_techs_adapter_validation_all.json`
- GuitarSet metadata: `benchmarks/guitar/guitarset_gt_extensions.md`
- GuitarSet fingering validator: `benchmarks/guitar/validate_guitarset_fingering.py`
- GuitarSet fingering validation report: `benchmarks/guitar/gt_runs/guitarset_fingering_validation_all.json`
- GuitarSet key: `benchmarks/guitar/guitarset_key_eval.md`
- GuitarSet chords: `benchmarks/guitar/guitarset_chord_eval.md`
- MUSDB setup helper: `python/ingest/scripts/SETUP-MUSDB18-HQ.md` and
  `python/ingest/scripts/setup_musdb18_hq.ps1`
- MUSDB runner: `benchmarks/quality/run_musdb_separation_sdr.py`
- Demucs FT MUSDB config: `benchmarks/quality/configs/demucs_ft_drums.json`
- Default Demucs MUSDB gate report:
  `benchmarks/quality/runs/20260709_232250_434347_demucs_musdb_separation_sdr.json`
- Demucs FT MUSDB gate report:
  `benchmarks/quality/runs/20260709_234848_965718_demucs_demucs_ft_drums_musdb_separation_sdr.json`
- RoFormer MUSDB gate report:
  `benchmarks/quality/runs/20260710_024833_512208_roformer_musdb_separation_sdr.json`
- MUSDB separation eval: `benchmarks/quality/musdb_separation_eval.md`
- MUSDB synthetic runner smoke: `python/ingest/tests/test_musdb_separation_sdr_runner.py`
- RoFormer setup: `python/ingest/scripts/SETUP-ROFORMER-SEPARATION.md`
- RoFormer/MSST wrapper: `python/ingest/scripts/run_roformer_msst.py`
- RoFormer runtime validator:
  `python/ingest/scripts/validate_roformer_runtime.py`
- ADTOF setup: `python/ingest/scripts/SETUP-ADTOF.md`
- ADTOF runtime validator: `python/ingest/scripts/validate_adtof_runtime.py`
- RMVPE setup: `python/ingest/scripts/SETUP-RMVPE.md`
- MIR-ST500 setup: `python/ingest/scripts/SETUP-MIR-ST500.md`
- MIR-ST500 preparation helper: `python/ingest/scripts/prepare_mir_st500_root.py`
- MIR-ST500 preparation status:
  `benchmarks/vocals/mir_st500_preparation_status.json`
- RMVPE runtime validator: `python/ingest/scripts/validate_rmvpe_runtime.py`
- DrumSep setup: `python/ingest/scripts/SETUP-DRUM-STEMSEP.md`
- DrumSep/MSST runner: `python/ingest/scripts/run_drum_stemsep_msst.py`
- DrumSep runtime validator: `python/ingest/scripts/validate_drum_stemsep_runtime.py`
- MIR-ST500 vocals: `benchmarks/vocals/mir_st500_vocals_eval.md`
- Model-upgrade gate evidence checklist:
  `benchmarks/runtime/model_upgrade_gate_evidence.md`

## Current Environment Notes

- Active ingest venv now imports `madmom` 0.17.dev0, `museval` 0.4.1,
  `jsonschema` 4.26.0, `pretty_midi` 0.2.11, `PyYAML` 6.0.3, `mido`
  1.3.3, `mir_eval` 0.8.2, `onnx` 1.18.0, and `onnxruntime` 1.26.0.
- `aural_ingest runtime-check` now reports Basic Pitch runtime features
  separately from package health. The active venv resolves
  `basic_pitch/saved_models/icassp_2022/nmp.onnx`, imports
  `basic_pitch.inference`, imports `onnxruntime`, and a strict tiny-tone
  `melodic_basic_pitch` smoke returned one A4 note through the ONNX path.
- `aural_ingest runtime-check` also reports RoFormer/MSST configuration
  diagnostics and an optional `demucs_ft_drums_modelpack` asset snapshot
  separately from the default `demucs_6` modelpack.
- `aural_ingest runtime-check` now emits an informational
  `model_upgrade_gates` section that consolidates the remaining promotion
  blockers without launching model subprocesses: Beat This manual review,
  MUSDB SDR data, `demucs_ft_drums`, RoFormer/MSST, RMVPE+MIR-ST500, ADTOF,
  DrumSep, and QMUL high-resolution guitar. The section does not affect the
  runtime-check exit code for normal app startup; release/CI gates can opt in
  to enforcement with `runtime-check --require-model-upgrade-gates`, which
  exits nonzero until every entry in `model_upgrade_gates.pending` clears.
  The snapshot also reports
  `model_upgrade_gates.evidence_root`,
  `model_upgrade_gates.evidence_root_env_var`, and
  `model_upgrade_gates.evidence_checklist_relative_path` so operators can
  jump directly to `benchmarks/runtime/model_upgrade_gate_evidence.md` or set
  `AURAL_MODEL_UPGRADE_EVIDENCE_ROOT` when running the frozen sidecar outside
  the repo root.
  The Beat This manual-review gate clears only when
  `benchmarks/meter/beat_this_dbn_barline_listening_review.json` exists and
  declares the correct gate/version/source metadata, non-`TODO` reviewer
  metadata, an ISO-8601 UTC `reviewed_at_utc` value ending in `Z`, and marks
  the Psalm 121, Psalm 130, and Psalm 5 DBN refresh-meter smokes with both
  `barlines_ok=true` and `listening_ok=true`.
  The MUSDB gates now clear from successful
  `benchmarks/quality/runs/*_musdb_separation_sdr.json` reports, not merely
  from dataset/runtime presence: the default baseline requires a successful
  default Demucs report (`modelpack_id` absent or `demucs_6`), while the
  `demucs_ft_drums` gate requires a successful Demucs report whose recorded
  config selects `stem_separation_modelpack_id: demucs_ft_drums`. Both MUSDB
  gates now require `dataset="musdb18_or_musdb18_hq"`, `split="test"`,
  at least 10 successful tracks, zero failed/skipped tracks, plus finite aggregate and
  per-role SDR evidence for bass, drums, other, and vocals, with every role
  represented for every successful track. The MUSDB selector skips unrelated
  providers/modelpacks and then treats the newest matching provider/modelpack
  report as authoritative, so a newer failed Demucs or RoFormer benchmark
  keeps the relevant gate pending until a newer passing report is written.
  The remaining external-runtime gates also clear from durable evidence files,
  not merely configured paths: ADTOF and DrumSep require successful
  `benchmarks/runtime/runs/*_adtof_runtime.json` and
  `benchmarks/runtime/runs/*_drum_stemsep_runtime.json` reports with
  `--require-events`, `runtime.configured=true`, integer event counts, and matching
  generated `events[]` entries carrying normalized JSON-number `time`,
  integer `note`, and integer `velocity` payloads; QMUL requires a successful
  `benchmarks/runtime/runs/*_qmul_hr_guitar_runtime.json` report with
  `--require-notes`, `runtime.configured=true`, an integer note count, and matching
  generated `notes[]` entries carrying normalized JSON-number `t_on`/`t_off`,
  integer `pitch`, integer `velocity`, and non-empty `instrument` payloads.
  Runtime validation evidence is latest-authoritative per
  engine-specific glob, so a newer failed validation keeps the gate pending
  until a newer successful report replaces it. RMVPE+MIR-ST500 requires a successful
  `benchmarks/runtime/runs/*_rmvpe_runtime.json` `runtime.ready=true` report plus a
  `benchmarks/vocals/gt_runs/*_mir_st500_vocals.json` benchmark report for
  `melodic_rmvpe` test/vocal run with top-level `ok=true`, full case coverage,
  `extra.limit=null`, successful cases, zero case errors, and finite aggregate precision/recall/F1;
  the MIR-ST500 selector skips unrelated algorithms and treats the newest
  matching `melodic_rmvpe` test/vocal report as authoritative;
  and
  RoFormer/MSST comparison requires a successful
  `benchmarks/runtime/runs/*_roformer_runtime.json` contract report for the
  four MUSDB roles with non-empty `stem_paths` plus its own successful
  `benchmarks/quality/runs/*_musdb_separation_sdr.json` report whose aggregate
  `median_sdr_mean` is not below the default Demucs MUSDB baseline.
- `pip check` still reports one pre-existing runtime-health issue:
  `basic-pitch 0.4.0 requires tensorflow, which is not installed`. This is now
  treated as a TensorFlow SavedModel/package-health warning, not an ONNX Basic
  Pitch blocker. A compatible TensorFlow plan is still needed only if the
  packaged route moves back to TensorFlow SavedModel/TFLite.
- External gate audit on 2026-07-10: local external assets now exist under
  `D:\AuralPrimer\.external` for ADTOF, RMVPE, RoFormer/MSST, QMUL
  `hf_midi_transcription`, and a review-pending DrumSep mirror; Beat This is
  installed as a local ignored modelpack under `assets/models/beat_this`.
  Durable
  runtime/benchmark evidence clears `adtof_external_runtime`,
  `demucs_ft_drums_sdr`, `drum_stemsep_external_runtime`,
  `musdb_sdr_baseline`, `qmul_hr_guitar_external_runtime`, and
  `roformer_musdb_comparison`; RMVPE runtime evidence is ready but its combined
  gate still requires MIR-ST500 benchmark evidence. `AURAL_MIR_ST500_ROOT`
  remains unset locally. Runtime env vars used for
  ADTOF/RMVPE/RoFormer/DrumSep/QMUL validation were process-scoped; set them again
  before ad-hoc runtime checks that need live diagnostics. `E:\AudioSourceOfTruthData\extracted\guitarset`
  `E:\AudioSourceOfTruthData\extracted\guitar_techs`, and
  `E:\AudioSourceOfTruthData\extracted\musdb18_hq` are present; MIR-ST500,
  Beat This manual review, DrumSep license/provenance review, and the remaining
  RMVPE vocal benchmark report are the active external blockers.

## Next critical path

1. Do human bar-line/listening review of the three Beat This DBN refresh-meter
   smokes before treating T1.1 as promotion-complete.
2. RMVPE runtime validation is ready locally. The official MIR-ST500 metadata
   repo is cloned under
   `D:\AuralPrimer\.external\singing_transcription_ICASSP2021` at commit
   `680313740ec6792dc6358c3c722c63bd7d03159e`, but the official source
   provides YouTube links and annotations rather than a direct audio archive.
   `prepare_mir_st500_root.py --copy-metadata` has staged the annotation files
   under `E:\AudioSourceOfTruthData\extracted\mir_st500`; the status report
   still shows all 100 test mixtures/vocals missing and `yt_dlp`, `spleeter`,
   and `tensorflow` unavailable in the active ingest venv. After explicit
   audio-source review, prepare MIR-ST500 vocal audio under
   `AURAL_MIR_ST500_ROOT` and run
   `D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\vocals\run_mir_st500_vocals.py --split test --variant vocal --algorithm melodic_rmvpe --write-gate-evidence`
   before widening vocals work.
3. The three MUSDB separation reports are ready. Use the results to make a
   product decision separately from strict gates: default Demucs remains the
   safer packaged baseline, `demucs_ft_drums` underperforms the default on this
   10-track sample, and RoFormer beats the baseline but has much higher CPU
   runtime and still needs shipping/license review before any profile/default
   effect.
4. ADTOF runtime validation and E-GMD test-30 are ready locally. Schedule a
   psalm listening/gameplay review before any ADTOF drum profile/default
   promotion.
5. DrumSep runtime evidence is ready via
   `benchmarks/runtime/runs/20260709_223101_937032_drum_stemsep_runtime.json`.
   Clear the review-pending checkpoint provenance/license question before any
   sidecar/modelpack/release decision, then schedule crash/ride/tom quality
   benchmarks.
6. For guitar, treat the full Guitar-TECHS directinput benchmark as a negative
   broad-promotion gate for YourMT3 and a positive research signal for QMUL.
   The QMUL external runtime plus GuitarSet/Guitar-TECHS benchmark reports are
   ready locally; next useful guitar work is license/shipping review plus
   gameplay/listening policy for whether QMUL can influence any profile.
7. For tab/string-fret, the sidecar-backed ingest/feedpak/runtime path is now
   present, including a deterministic sequence/chord-aware standard-tuning
   fallback for bass/guitar notes without explicit string/fret metadata.
   GuitarSet authored string/fret metadata has a full local corpus validation
   pass. The next useful work is a higher-quality or externally validated
   fretting producer, non-GuitarSet authored tab review, or the QMUL review
   work above.
