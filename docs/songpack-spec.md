# SongPack Specification

## Purpose
A **SongPack** is the only runtime input the game needs.

- Ingestion converts source audio (and optionally MIDI) into a SongPack.
- The desktop app + visualizers consume SongPacks deterministically.

## Container
Two interchangeable container forms:

1) **Directory SongPack** (for development)
- `MySong.songpack/` as a folder.

2) **Zip SongPack** (for distribution)
- `MySong.songpack` is a zip file.

The host must support both.

### Deliverable
The distribution deliverable is the deterministic **zip SongPack** (`*.songpack` file).
See: `docs/songpack-deliverable.md`.

### Library scanning convention
- The host treats any `*.songpack` file as a zip SongPack.
- The host treats any `*.songpack/` directory as a directory SongPack.
- Recommended UX: scan a configured songs folder on startup and index any SongPacks found.

## Required top-level layout

```
manifest.json
audio/
  mix.wav                 # preferred for timing accuracy
  mix.mp3                 # optional compressed audio
  mix.ogg                 # optional compressed audio
features/
  beats.json
  tempo_map.json
  sections.json
  events.json
charts/
  easy.json
meta/
  cover.png (optional)
  license.json (optional)
```

Optional:
```
audio/stems/*.wav
features/chords.json
features/key.json
features/notes.mid
features/pitch_contour.json
features/lyrics.json
```

### Audio codec policy
- SongPacks may include compressed audio (`mix.mp3` and/or `mix.ogg`) to reduce size.
- The host is responsible for decoding playback audio via a **local codec layer** (bundled decoder/sidecar).
- Ingestion may normalize to `mix.wav` when needed for analysis/determinism.

## `manifest.json`

### Required fields
```json
{
  "schema_version": "1.0.0",
  "song_id": "uuid-or-stable-hash",
  "title": "...",
  "artist": "...",
  "duration_sec": 123.45,

  "source": {
    "original_filename": "...",
    "original_sha256": "...",
    "ingest_timestamp": "2025-12-21T12:00:00Z"
  },

  "timing": {
    "audio_sample_rate_hz": 48000,
    "audio_start_offset_sec": 0.0,
    "timebase": "audio" 
  },

  "pipeline": {
    "pipeline_id": "aural_ingest",
    "pipeline_version": "0.1.0",
    "profile": "full",
    "stage_fingerprints": {
      "decode": "...",
      "beats": "..."
    }
  },

  "assets": {
    "audio": {
      "mix_path": "audio/mix.wav"
    }
  }
}
```

### Notes
- `schema_version` uses SemVer.
- `song_id` should be stable for same source+pipeline config.
- `audio_start_offset_sec` allows aligning event timestamps if audio has encoder delay.

---
## Canonical event model

### Key goal
Visualizers should not need to interpret MIDI, raw ML, or instrument-specific internals.

All time-based content is represented as a set of normalized events in `features/events.json`.

### Event timestamp convention
- All times are **seconds** in the audio timebase.
- `t=0` corresponds to the start of `audio/mix.wav` after any configured `audio_start_offset_sec`.

### `features/events.json` schema (v1)
```json
{
  "events_version": "1.0.0",
  "tracks": [
    {
      "track_id": "t1",
      "role": "guitar|bass|drums|vocals|keys|other",
      "name": "Guitar",
      "tuning": {"type": "guitar_standard", "strings": [40,45,50,55,59,64]} 
    }
  ],

  "beats": [
    {"t": 0.50, "bar": 0, "beat": 0, "strength": 1.0},
    {"t": 1.00, "bar": 0, "beat": 1, "strength": 0.5}
  ],

  "sections": [
    {"t0": 0.0, "t1": 12.3, "label": "intro"},
    {"t0": 12.3, "t1": 44.0, "label": "verse"}
  ],

  "notes": [
    {
      "track_id": "t1",
      "t_on": 10.25,
      "t_off": 10.70,
      "pitch": {"type": "midi", "value": 64},
      "velocity": 0.8,
      "confidence": 0.72,
      "source": "midi|transcription"
    }
  ],

  "onsets": [
    {
      "track_id": "drums",
      "t": 5.12,
      "class": "kick|snare|hat_closed|hat_open|tom|clap|other",
      "confidence": 0.9
    }
  ],

  "pitch_contours": [
    {
      "track_id": "vocals",
      "points": [
        {"t": 1.00, "pitch_hz": 220.0, "confidence": 0.7}
      ]
    }
  ],

  "chords": [
    {
      "t": 20.0,
      "t1": 22.0,
      "root": "C",
      "quality": "maj|min|dim|aug|sus|7|maj7|min7",
      "key_context": {"tonic": "C", "mode": "major"},
      "nashville": "1",
      "roman": "I"
    }
  ]
}

---
## Lyrics / karaoke timings

SongPacks may include timed lyrics for karaoke-style playback.

### `features/lyrics.json`
This format intentionally mirrors the structured JSON output from **PsalmsKaraoke** (not the ASS file itself), because:
- it is deterministic and easy to consume in a visualizer
- it supports per-word or per-syllable highlighting via character ranges

Minimum shape:
```json
{
  "format": "psalms_karaoke_json_v1",
  "granularity": "syllable",
  "lines": [
    {
      "start": 12.34,
      "end": 15.67,
      "text": "Amazing grace",
      "chunks": [
        {"start": 12.34, "end": 12.80, "text": "A-", "char_start": 0, "char_end": 2},
        {"start": 12.80, "end": 13.10, "text": "maz", "char_start": 2, "char_end": 5}
      ]
    }
  ]
}
```

Notes:
- `start/end` are in seconds in the audio timebase.
- `chunks` are optional; if missing, a visualizer may fall back to line-level highlighting.
```

### Design notes
- All lists are optional but `beats` and `sections` should exist for most gameplay modes.
- `notes` are used for fretted instruments and for theory views.
- `onsets` are used for drums.
- `pitch_contours` are used for vocal lane.

---
## Beat and tempo files

### `features/beats.json`
A lightweight file for beat grid only (useful for streaming / quick validation).

### `features/tempo_map.json`
Piecewise tempo representation:
```json
{
  "tempo_version": "1.0.0",
  "segments": [
    {"t0": 0.0, "bpm": 120.0, "time_signature": "4/4"}
  ]
}
```

---
## Charts
Charts are *derived*, game-mode-specific projections of events.

### Example `charts/easy.json`
```json
{
  "chart_version": "1.0.0",
  "mode": "drums_groove",
  "difficulty": "easy",
  "targets": [
    {"t": 5.12, "lane": "kick"}
  ]
}
```

---
## Versioning and migrations

### Schema versioning rules
- **MAJOR**: breaking changes to file layout or meaning.
- **MINOR**: additive changes (new optional fields).
- **PATCH**: bugfix/clarification, no schema change.

### Migration strategy
- Desktop host includes migrations from older `schema_version` → current.
- Keep `manifest.json` as the entry point for selecting a migration path.

---
## Determinism requirements
To keep imports reproducible:
- ingestion outputs must be stable given identical inputs, stage versions, and configs
- floating-point values should be quantized (e.g., 1e-6) before serialization
- json is serialized with stable key ordering
