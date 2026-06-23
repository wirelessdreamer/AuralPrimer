# feedpak — vendored format support

AuralPrimer's native pack format is migrating from `.auralsong` to **feedpak**
(https://github.com/got-feedback/feedpak-spec).

- `schemas/` — vendored copy of the feedpak JSON Schemas, pinned to spec
  **v1.11.0**. Used for offline conformance validation of packs we write/read.
  Re-sync from the upstream `schemas/` directory on a spec bump.

Migration decisions (locked):
- Native extension: `.feedpak` (standard, interop-friendly).
- Existing `.auralsong` packs: clean re-import (no converter).
- Note source of truth: feedpak `notation_<id>.json` (midi pitch), with
  `notes.mid` retained as an `aural_notes_mid` extension so the game's chart
  path keeps working during the transition.
- Hard cutover: no `.auralsong` read compatibility once migrated.
- Our authoring artifacts (spectrogram / refine_candidates / benchmark /
  pipeline provenance) ride as namespaced `aural_*` manifest extension keys
  (feedpak guarantees unknown keys are preserved).
