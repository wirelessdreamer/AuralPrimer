# sloppak support

[Slopsmith](https://github.com/mikey0000/slopsmith)'s `.sloppak` is the sibling
of AuralPrimer's `.feedpak`: a directory (`*.sloppak/`) or zip with a
`manifest.yaml` index, POSIX-relative file pointers, and forward-compatible
handling of unknown manifest keys. AuralPrimer opens, edits, and cleans up
sloppaks **in place** — it never converts them to feedpaks.

## How AuralPrimer treats a sloppak

- **Same reader path as feedpak.** The auralsong-core manifest parser reads a
  sloppak `manifest.yaml` unchanged; `auralsong_core::container::is_manifest_pack`
  routes both extensions down the feedpak code path in the game and studio.
- **Derived artifacts live under `aural/`** (never `features/`, which is the
  legacy `.auralsong` layout) and are referenced by `aural_*` manifest keys.
  Because Slopsmith ignores unknown keys, these additions are invisible to it:
  - `aural/notes.mid` + `aural_notes_mid` — melodic gameplay charts.
  - `song_timeline.json` + `song_timeline` — beat grid / meter / sections.
  - `aural/spectrogram/…`, `aural/refinement.<role>.json`, etc. — as feedpak.
- **`drum_tab.json` is shared with Slopsmith** (pack root). Studio's drum
  cleanup writes it back in place, preserving each hit's optional
  `g`/`f`/`k` (ghost/flam/choke) and any unknown per-hit fields.

## Melodic notes come from the arrangements, not transcription

A sloppak ships human-authored Rocksmith-style arrangement JSONs, so melodic
notes are derived losslessly rather than transcribed. The sidecar command
`aural_ingest prep-arrangements <pack>` converts `arrangements/<id>.json`
(wire format: `t`, string `s`, fret `f`, sustain `sus`, chord templates) into
`aural/notes.mid`, one named instrument per role
(`Bass`/`Rhythm Guitar`/`Lead Guitar`/`Keys`/`Melodic`), plus
`song_timeline.json` from the first arrangement's `beats`/`sections`. Studio
surfaces this as the **"Prep notes"** action in the Cleanup table (and in
"Prep all unbuilt").

**Pitch / capo rule:** `midi = STANDARD[s] + tuning[s] + (capo if f == 0 else f)`
where `STANDARD = [40, 45, 50, 55, 59, 64]` (E2 A2 D3 G3 B3 E4). A capo raises
open strings (`f == 0`) to the capo fret; fretted notes carry their absolute
fret number. This matches Slopsmith's reading (fret numbers are absolute; the
capo overrides open-string pitch).

## Discovery

Sloppaks are found the same way as feedpaks: drop the `.sloppak` (directory or
zip) into the configured songs folder. Zip sloppaks open read-only (all
in-place writes require the directory form), same as zip feedpaks.
