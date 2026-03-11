# Transcription Regression History (From Memory)

Date written: 2026-03-03

## Regression: Drum track looked simplified after retranscription

### Reported symptom

- King in Zion retranscription showed reduced drum variety.
- Visual result looked like mostly kick/snare/hat only.

### What we compared

1. Current generated `notes.mid` from portable data.
2. Known-good baseline `notes.mid` from earlier test output.
3. Direct algorithm runs (`combined_filter` vs `adaptive_beat_grid`) on the same drums stem.

### Findings

- Regressed pattern matched `adaptive_beat_grid` output profile.
- `combined_filter` still produced expanded-kit output on same source audio.
- Therefore algorithm implementation was not the only issue; runtime path mattered.

### Root cause chain

1. Portable folder still contained an older sidecar executable.
2. New frontend defaults did not matter if portable runtime used stale sidecar.
3. In at least one path, this effectively reproduced adaptive/core-kit behavior.

## Fixes applied during troubleshooting

### A. Frontend algorithm defaults

- Studio import selectors set to `combined_filter` by default.
- Fallback value on missing select state also set to `combined_filter`.

### B. Sidecar build ordering

- Studio `tauri:build` changed to run sidecar build before Tauri build.
- Prevented shipping old sidecar with new UI.

### C. Portable repack discipline

- Portable pack script used to refresh exes/resources/sidecar.
- Sidecar timestamp/hash checks were used to confirm runtime freshness.

### D. Chart parser guard (desktop)

- Parser used strict drum-filter pass, with optional relaxed fallback.
- Added protection so sparse but explicitly named drum tracks are not discarded just because relaxed pass is denser.
- This directly targeted "drum track dropping" behavior in mixed MIDI content.

### E. Basic Pitch model load failures

- Sidecar build collected Basic Pitch data assets.
- Frozen model lookup made robust (`onnx -> tflite -> savedmodel dir`).
- Removed hard failure pattern where runtime looked for missing `.../nmp`.

### F. Import-dir event export ordering

- Fixed `sections` initialization order before `events.json` export.
- Removed noisy failure: `events.json export failed: ... sections ...`.

## Tests that protected these fixes

1. Fixture regression test for King in Zion chart parsing.
2. Synthetic parser tests:
   - strict vs relaxed fallback behavior
   - dedicated sparse drum track preserved against dense melodic noise
3. Python ingest tests:
   - fallback resilience
   - import/import-dir pipeline expectations

## Practical debug checklist we used

1. Verify Studio selector value actually passed as `--drum-filter`.
2. Confirm sidecar binary in runtime folder is latest (timestamp/hash).
3. Compare note-number distribution in produced `notes.mid`.
4. Run sidecar directly on drum stem with each algorithm to isolate behavior.
5. Repack portable and retest from fresh executable.

## Detailed timeline snapshot (captured before loss)

1. User reported:
   - keys/piano charts empty (`"no chart for keys"`)
   - drums reduced to kick/snare/hat (ride/crash/toms gone)
2. Desktop parser fix was applied:
   - strict/relaxed dual-pass + sparse-strict fallback heuristic
   - parser tests added and passing
3. Symptom persisted on real imports.
4. Root-cause isolation run against real songpacks showed:
   - drum track in generated `notes.mid` had only `[36, 38, 42]`
   - ride/crash/tom notes were present in other melodic tracks, not drums track
5. Direct algorithm run on same drum stem proved:
   - `combined_filter` emitted expanded kit notes
   - `adaptive_beat_grid` emitted only core kit
6. Ingest defaults were then switched to `combined_filter`.
7. Sanity import confirmed drum channel now included expanded kit:
   - `[36, 38, 41, 42, 46, 47, 49, 50, 51]`

## Pre-loss modified files (last known)

Recovered from final `git status --short` snapshot:

- `apps/desktop/src/gameplay/chartLoader.ts`
- `apps/desktop/tests/chartLoader.test.ts` (new)
- `python/ingest/src/aural_ingest/transcription.py`
- `python/ingest/src/aural_ingest/cli.py`
- `python/ingest/tests/test_transcription_resilience.py`
- `python/ingest/tests/test_import_pipeline.py`
- `apps/desktop/src/main.ts` (unrelated UI edits also present)

## Last known passing checks (before repository loss)

- `py -3 -m pytest python/ingest/tests/test_transcription_resilience.py -q` -> `8 passed`
- `py -3 -m pytest python/ingest/tests/test_import_pipeline.py -q` -> `8 passed`
- `npx vitest run apps/desktop/tests/chartLoader.test.ts` -> passed
- `npm --prefix apps/desktop run typecheck` -> passed

## Known unresolved bug at that point

`import-dir` had a separate issue unrelated to drum-lane collapse:

- `events.json export failed: cannot access local variable 'sections' where it is not associated with a value`

This should be tracked independently so it does not get confused with drum algorithm regressions.
