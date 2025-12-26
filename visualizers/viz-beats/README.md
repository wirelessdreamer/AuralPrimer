# @auralprimer/viz-beats

Reference visualization plugin for the host skeleton.

- Renders a simple beat grid on a Canvas2D surface.
- Uses a placeholder timebase (host provides `TransportState`).

This exists to validate the plugin lifecycle contract:
`init → onResize → update → render → dispose`.
