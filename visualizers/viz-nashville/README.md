# @auralprimer/viz-nashville

Reference visualization plugin for a **Nashville / chord-lane** style view.

## Current behavior (MVP)

- Renders a single horizontal lane of **Roman numerals** (I–IV–V–vi loop) aligned to bar boundaries.
- Driven *only* by the host-provided `TransportState` (time + bpm + time signature).

## Why placeholder?

The host does not yet provide chord/key data to plugins (no `SongHandle` / feature query APIs).
Once the Viz SDK grows song queries, this plugin should switch from synthetic chords to real data.

Lifecycle contract validated:
`init → onResize → update → render → dispose`.

