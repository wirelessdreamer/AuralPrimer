# @auralprimer/viz-beats

Reference visualization plugin for beat, downbeat, and section context.

- Renders a Canvas2D beat grid.
- Uses explicit `ctx.song.songTimeline.beats[]` and downbeat markers when a
  feedpak provides a `song_timeline` artifact.
- Falls back to host `TransportState` BPM and time signature when no timeline
  artifact is present.

This exists to validate the plugin lifecycle contract:
`init -> onResize -> update -> render -> dispose`.
