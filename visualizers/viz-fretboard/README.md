# @auralprimer/viz-fretboard

Reference visualization plugin for a **guitar-style fretboard** view.

## Current behavior (MVP)

- Renders a 6-string fretboard (frets 0–12) on Canvas2D.
- Since the host does not yet provide note/chord events to plugins, it uses a **placeholder “note cursor”**
  that moves deterministically over time.

## Planned upgrades

Once the Viz SDK exposes song queries (e.g. events/notes), this plugin should render:

- detected notes (string + fret)
- chord shapes (boxes / intervals)
- current key/mode overlays

Lifecycle contract validated:
`init → onResize → update → render → dispose`.

