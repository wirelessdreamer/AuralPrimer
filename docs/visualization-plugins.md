# Visualization Plugins

## Goal
Make the visualization system **highly pluggable** so you can experiment with:
- instrument metaphors (fretboard, drum lanes, vocal pitch lane)
- theory metaphors (Nashville numbers, Roman numerals, patterns)
- game metaphors (targets, hit windows, scoring overlays)

The core host provides timing, data access, and rendering surfaces; plugins focus on visualization logic.

---
## Plugin packaging
A visualization plugin is a folder (dev) or zip (distribution) with:

```
manifest.json
dist/index.js            # ESM bundle
assets/*                 # optional
```

### `manifest.json`
```json
{
  "id": "viz-nashville",
  "name": "Nashville Numbers",
  "version": "0.1.0",
  "description": "Chord blocks rendered as Nashville numbers",
  "entry": "dist/index.js",
  "supported_schema_versions": ["1.x"],
  "capabilities": {
    "render": ["canvas2d"],
    "needs": ["chords", "beats", "sections"]
  }
}
```

---
## Loading model

### Where plugins are discovered
- built-in: bundled under `resources/visualizers/*`
- user plugins: `~/AuralPrimer/visualizers/*` (configurable)

### Security/sandboxing
Start simple:
- plugins run as JS modules in the same renderer process
- only access host services through SDK-provided objects

Later hardening options:
- load plugin in an `iframe` with a message bridge
- isolate heavy work to a `WebWorker`

---
## Viz SDK API

### Host-provided objects
- `SongHandle` (read-only SongPack access)
- `Transport` (time, tempo, play/pause/seek)
- `Renderer` (canvas/webgl context + resize)
- `Input` (keyboard/controller abstractions)
- `Settings` (per-plugin persistent settings)

### Visualizer interface
```ts
export interface Visualizer {
  init(ctx: VizInitContext): Promise<void>;
  onResize(width: number, height: number, dpr: number): void;
  update(dt: number, state: TransportState): void;
  render(frame: FrameContext): void;
  dispose(): void;
}
```

### Minimal `TransportState`
```ts
export interface TransportState {
  t: number;                 // seconds
  isPlaying: boolean;
  bpm: number;
  timeSignature: [number, number];
  loop?: { t0: number; t1: number };
}
```

### Data access pattern
Provide a query layer optimized for real-time rendering:

```ts
// time window queries
song.getBeats(t0, t1)
song.getNotes(trackId, t0, t1)
song.getChords(t0, t1)

// lookups
song.getSections()
song.getTracks()
```

Implementation detail: internally, store events in time-sorted arrays + binary search.

---
## Standard rendering conventions

To keep plugins consistent, define conventions:
- time runs left→right or bottom→top; plugin declares orientation
- beat lines and bar lines styling
- colors by track role
- target window visualization (hit lanes)

---
## Reference plugins (to build early)

### 1) `viz-nashville`
- renders chord blocks labeled by Nashville number and quality
- overlays bar/beat grid

### 2) `viz-fretboard`
- configurable tuning and number of strings
- highlights target notes and scale degree labels

### 3) `viz-drum-grid`
- lanes for kick/snare/hat + subdivision grid
- shows onset targets and timing windows

### 4) `viz-vocal-lane`
- displays pitch contour (detected) + target contour if present

---
## Compatibility tests for plugins (TDD expectation)
A plugin should be validated by the host with:
- schema compatibility check
- `init → resize → update → render → dispose` smoke test
- performance budget checks (frame time)

Plugins (and the host SDK) are expected to be developed TDD-first:
- changes to the Viz SDK API come with contract tests
- plugins include at least smoke tests against a reference SongPack
