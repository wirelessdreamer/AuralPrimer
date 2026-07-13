# @auralprimer/viz-fretboard

Reference visualization plugin for a **guitar-style fretboard** view.

## Current behavior

- Renders a 6-string fretboard (frets 0-12) on Canvas2D.
- Reads host-provided melodic notes from `ctx.song.notes`.
- Uses explicit `string`/`fret` metadata, including compact `s`/`f` aliases,
  when the feedpak provides fingering sidecars or authored tab metadata.
- Shows active and upcoming fretted notes against the host transport time.

## Planned upgrades

Next useful upgrades:

- chord shapes (boxes / intervals)
- current key/mode overlays

Lifecycle contract validated:
`init → onResize → update → render → dispose`.
