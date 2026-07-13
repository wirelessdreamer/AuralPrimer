# feedpak - vendored format support

AuralPrimer's native pack format is migrating from `.auralsong` to **feedpak**
(https://github.com/got-feedback/feedpak-spec).

- `schemas/` is a vendored copy of the feedpak JSON Schemas, pinned to spec
  **v1.11.0**. It is used for offline conformance validation of packs we
  write and read. Re-sync from the upstream `schemas/` directory on a spec
  bump.
- `fixtures/minimal.feedpak/` is the local contract fixture. It should stay
  schema-valid and include every AuralPrimer extension key that host/runtime
  code treats as first-class availability.

## Migration decisions

- Native extension: `.feedpak` (standard, interop-friendly).
- Existing `.auralsong` packs: clean re-import; no compatibility converter.
- Note source of truth: feedpak `notation_<id>.json` for notation, with
  `aural_notes_mid` retained as an AuralPrimer extension so current game and
  desktop chart paths can use tempo-aware MIDI during the transition.
- Hard cutover: no `.auralsong` read compatibility once migrated.
- Authoring artifacts ride as namespaced `aural_*` manifest extension keys.
  Feedpak preserves unknown keys, and the local manifest schema validates the
  AuralPrimer paths we currently rely on.

## Current AuralPrimer manifest pointers

The minimal fixture and local schemas cover these first-class pointers:

- Feedpak core/media: `arrangements[].notation`, `arrangements[].file`,
  `stems[].file`, `lyrics`, `song_timeline`, and `drum_tab`.
- Model artifacts: `keys`, `harmony`, `vocal_pitch`,
  `vocal_pitch_contour`, and `pitch_extraction` provenance.
- AuralPrimer extensions: `aural_notes_mid`, `aural_fingering`,
  `aural_refine_candidates`, `aural_spectrogram`, and `aural_benchmark`.
  `aural_fingering` keys are limited to the loader-supported roles: `bass`,
  `guitar`, `rhythm_guitar`, `lead_guitar`, `keys`, `vocals`, and `melodic`.

All manifest paths must be safe container-relative paths: no absolute paths,
drive prefixes, backslashes, empty values, repeated slashes, or `..`
components. Readers and validators should only report an artifact as available
after the target exists inside the same feedpak container.

Vocal pitch notes require positive `d` durations, vocal pitch contour samples
require positive `hz` values, and harmony events require positive `duration`
when an authored duration is present. Harmony `key`/`tonic`, `mode`/`scale`,
`confidence`, and `score` metadata are explicit schema fields because HUD and
Nashville visualizers consume them directly.

## Sidecar schema coverage

`aural_ingest validate` parses every declared JSON sidecar and schema-validates
the known ones:

- `notation.schema.json` and `arrangement.schema.json`
- `lyrics.schema.json`
- `song-timeline.schema.json`
- `drum-tab.schema.json`
- `keys.schema.json`
- `harmony.schema.json`
- `vocal-pitch.schema.json`
- `vocal-pitch-contour.schema.json`
- `aural-fingering.schema.json`

`aural_refine_candidates` is currently parse-only in the Python CLI validator;
the TypeScript package owns the stricter refine-candidate note contract tests.
