# @auralprimer/viz-nashville

Reference visualization plugin for a Nashville / chord-lane style view.

## Current behavior

- Renders a scrolling Nashville-number piano roll from host-provided melodic notes.
- Reads `ctx.song.harmony` / `ctx.song.keys` when available to choose the key
  before falling back to note-derived key inference.
- Renders a top chord band from `harmony.events[]`, preferring Roman-numeral
  labels when the ingest pipeline provides them.
- Uses host-provided `TransportState` for timing, tempo, and time signature.

## Planned upgrades

- Richer chord voicing/quality display.
- Section-aware chord grouping.

Lifecycle contract validated:
`init -> onResize -> update -> render -> dispose`.
