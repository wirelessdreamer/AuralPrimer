# @auralprimer/viz-sdk

Minimal visualization SDK contract between host (desktop) and visualization plugins.

This is intentionally tiny for the host skeleton:
- lifecycle: `init → onResize → update → render → dispose`
- rendering surface: Canvas2D
- timebase: `TransportState`

The contract is expected to evolve with **TDD-first** contract tests.
