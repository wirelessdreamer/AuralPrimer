# Psalm 19 Drum Transcription Requirements

Status: goal definition for the Psalm 19 "drum notes with no stem audio" investigation.

## Goal

Fix Psalm 19 drum chart ghost notes.

## Current Evidence

Local auralsong under review:

`D:\AuralPrimer\AuralPrimerPortable\data\songs\psalm_19_heaven_whispered.auralsong`

Observed from the current auralsong:

- `manifest.json` says `profile: full` and `transcription_profile: gameplay_default`.
- The drum source is `audio/stems/drums.wav` from Demucs (`drum_source_kind: separated_drums`).
- The actual drum engine used was `combined_filter`.
- The drum stem-silence gate metadata is absent from the manifest, so this pack was built before or without the current gate being applied.
- `features/events.json` and `features/notes.mid` agree on `1735` drum events.
- Drum lane distribution is heavily cymbal/ride weighted:
  - kick / BD: `583`
  - snare / SD: `301`
  - crash / CY: `505`
  - ride / RD: `326`
  - hi-hat / HH: `4`
  - high tom / HT: `8`
  - low tom / LT: `8`
- Local stem-energy checks show the quiet false-positive pressure is concentrated in snare, crash, and ride events:
  - default silence gate shape (`-50 dBFS`, +/- `30 ms`) drops `83 / 1735` events on a legacy `combined_filter` rerun.
  - a stricter `-40 dBFS` local-RMS check would flag about `350 / 1735` events, almost all crash/ride/snare.
  - kicks are effectively untouched by the default gate on this stem.

Relevant current code:

- `python/ingest/src/aural_ingest/transcription.py` contains `validate_drum_events_against_stem_silence`.
- `python/ingest/src/aural_ingest/cli.py` contains Stage 6b that applies the gate for non-mix drum sources.
- `python -m aural_ingest.cli audit-drums <auralsong-dir>` reports drum event counts, lane/note distribution, local stem energy, and gate metadata presence.
- `python/ingest/tests/test_drum_stem_silence_gate.py` names Psalm 19 as the target failure shape.
- `TRANSCRIPTION_PROFILES["gameplay_default"].drum_engines` starts with `beat_conditioned_multiband_decoder`; default/`auto` imports now walk that profile chain instead of silently forcing legacy `combined_filter`.

Recognizer debug baseline on the Psalm 19 drum stem after the recognizer-level fix:

- Raw child-detector candidates: `8858`.
- Fused `combined_filter` candidates emitted: `1433`.
- Rejections are concentrated in cymbal/snare/tail classes:
  - `no_local_hit_body`: `1000`
  - `weak_cymbal_body`: `206`
  - `cymbal_tail_without_attack`: `171`
  - `weak_class_band`: `27`
- Rejected selected classes:
  - ride: `725`
  - crash: `449`
  - snare: `185`
  - kick: `41`
  - tom/high-hat residuals: `4`

That points to detector over-fire on separator residue and cymbal tails, plus fusion accepting high-band residuals as crash/ride/snare notes. It does not point first to a gameplay parser or lane-mapping duplication issue.

## Root Cause To Prove

This goal is not complete until we can classify each Psalm 19 ghost-note source into one of these buckets:

1. Stale content: the current portable Psalm 19 pack was generated before the stem-silence gate and needs regeneration.
2. Engine selection bug: the import records `gameplay_default` but still uses legacy `combined_filter` instead of the profile's drum-engine chain.
3. Engine false positives: the selected drum engine emits crash/ride/snare events from separator residual, cymbal tails, or broadband noise that do not correspond to a local drum-stem hit.
4. Gate weakness: the silence gate removes only objectively silent events but is too conservative for the user's audible false positives.
5. Sync or parser error: MIDI event times or drum-lane parsing are shifted relative to the drum stem or game playback.

The first fix must explain the recognizer's pre-gate decision, not just remove its output afterward. The silence gate is useful as an audit signal and safety net, but it is not sufficient as the root-cause fix.

## Functional Requirements

1. Psalm 19 must be reproducibly auditable.
   - Provide a repeatable command or script that reads a auralsong, the selected drum MIDI/events, and the drum stem.
   - The audit must report event counts by lane/note, local RMS/peak percentiles, low-energy event counts by lane, and whether manifest gate metadata exists.
   - The audit must not require committing copyrighted audio or generated portable artifacts.

2. The recognizer's false positives must be explainable.
   - For a representative set of Psalm 19 ghost notes, capture raw candidates from `dsp_bandpass_improved`, `dsp_spectral_flux`, and `aural_onset`.
   - Capture the fused `combined_filter` cluster, source weights, class scores, margin, fallback class, local timbral features, and final emitted class.
   - Classify each false positive as detector over-fire, fusion weighting error, feature boost error, multi-label duplicate, fallback-grid emission, or timing drift.
   - Produce a compact debug artifact that lets us inspect why the recognizer believed a hit existed.

3. Default drum engine behavior must match the selected transcription profile.
   - If no explicit drum engine is requested, or the request is `auto`, the import must walk `transcribe_drums_with_profile(profile=...)`.
   - `gameplay_default` must try `beat_conditioned_multiband_decoder` before legacy `combined_filter`.
   - An explicit `--drum-filter combined_filter` or equivalent config must still force `combined_filter`.
   - Manifest metadata must distinguish requested engine, normalized engine, profile chain, attempted engines, and used engine.

4. False candidates must be rejected inside the recognizer path where practical.
   - Prefer detector threshold, candidate validation, fusion scoring, or profile-default fixes over post-hoc event deletion.
   - If `combined_filter` is retained, it must require independent onset evidence strong enough to distinguish real hits from residual tails and separator leakage.
   - Expanded-kit outputs such as crash and ride must have stronger evidence requirements than kick/snare backbone events.
   - The multi-label emitter must not create secondary crash/ride events unless a real detector and local class-specific audio evidence support that class.

5. The stem-silence gate must remain as a defensive audit and safety check.
   - It must run after drum transcription and before writing `features/events.json` and `features/notes.mid`.
   - It must skip only when the drum source is a mix fallback, when there are no events, or when the stem cannot be loaded.
   - It must fail open on missing/unreadable stems and record the skip reason.
   - Manifest metadata must include `stem_silence_gate` with `gate_dbfs`, `window_ms`, `events_in`, `events_out`, `dropped`, `stem_load_ok`, and `quietest_dropped_dbfs`.

6. Psalm 19 regenerated output must be traceable.
   - A newly generated Psalm 19 pack must include `stem_silence_gate` metadata.
   - If built with default settings, its manifest must not silently claim `gameplay_default` while using legacy `combined_filter` unless `combined_filter` was explicitly requested.
   - The generated MIDI and `events.json` must contain the post-gate event set, not the raw pre-gate event set.

7. False positives must be measurable as an ingest quality metric.
   - Add a drum quality metric for local stem energy at each emitted event.
   - Report at least these bands: below `-50 dBFS`, below `-45 dBFS`, and below `-40 dBFS`.
   - Report counts by lane so crash/ride/snare false-positive pressure is visible.
   - Keep the metric as a guard/warning until listening review sets a stricter production threshold.

8. Gameplay review must use the same artifact the player sees.
   - The A/B review must compare the regenerated `features/notes.mid` against `audio/stems/drums.wav`, not a separate analysis-only JSON.
   - Verify in AuralPrimer that the drum highway uses the dedicated `Drums` track and the post-gate notes.
   - Verify there is no independent visualizer/parser duplication of dropped events.

## Acceptance Criteria

Minimum acceptance for the first fix:

- A Psalm 19 audit can be run from the repo and produces a compact JSON or Markdown report.
- The current stale pack is identified as missing `stem_silence_gate` metadata.
- A recognizer debug report identifies which child detector and fusion rule caused representative ghost notes.
- At least one failing recognizer-level regression reproduces the failure without depending on a post-hoc silence filter.
- A regenerated Psalm 19 pack includes gate metadata and records the actual used drum engine.
- With a legacy `combined_filter` rerun on the current Psalm 19 stem, the default gate drops the known objectively quiet events (`~83` events at `-50 dBFS`) without dropping kicks.
- Default import no longer records `gameplay_default` while bypassing that profile's drum-engine chain.
- Focused tests cover:
  - profile-driven drum engine selection on default/auto import,
  - explicit engine override,
  - recognizer rejection of residual/tail candidates,
  - stem-silence gate metadata written into the manifest,
  - dropped events absent from both `events.json` and `notes.mid`.

Stretch acceptance before promoting a new drum default:

- Psalm 19 low-energy crash/ride/snare pressure is reduced relative to the current `1735`-event baseline.
- Crash+ride dominance is justified by the audio or reduced by engine/profile changes.
- The quality benchmark has a local Psalm 19 guard case or equivalent stem-energy guard case.
- In-game listening review confirms the regenerated chart no longer shows repeated drum notes where the solo drum stem has no audible hit.

## Non-Goals

- Do not treat the silence gate as the final drum-transcription solution. It is a defensive post-filter.
- Do not call the issue fixed unless we can explain why the recognizer emitted the bad notes before the gate.
- Do not promote a new ML-backed drum model without model-absence behavior, portable packaging behavior, and benchmark evidence.
- Do not change drum visualizer lane mapping unless the audit proves parser/visualizer duplication or lane conversion is part of the bug.
