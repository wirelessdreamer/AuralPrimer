# Python Ingest Pipeline

## Objective
Convert source audio (mp3/wav/flac) and/or MIDI into a **SongPack**.

Key properties:
- staged pipeline (DAG)
- deterministic outputs
- caching and incremental rebuild
- pluggable stage implementations

## CLI contract (host ↔ sidecar)

### Primary command
```
aural_ingest import <input-audio-path>
  --out <output-songpack-dir>
  --profile <full|guitar|bass|drums|vocals|theory>
  --config <json>
```

### Additional commands
```
aural_ingest validate <songpack-dir>
aural_ingest info <songpack-dir>
aural_ingest stages
```

### Progress reporting
- stdout: **structured JSON lines** events for progress
- stderr: human-readable logs

Example JSONL:
```json
{"type":"stage_start","id":"beats","progress":0.2}
{"type":"stage_progress","id":"beats","progress":0.35,"message":"estimating tempo"}
{"type":"stage_done","id":"beats","progress":0.4,"artifact":"features/beats.json"}
```

The host UI can render progress without parsing arbitrary log strings.

---
## Pipeline overview (recommended MVP → v1)

### Stage 0: `init_songpack`
- create output folder structure
- write initial `manifest.json`

### Stage 1: `decode_audio`
Input: source audio file
Output:
- `audio/mix.wav` (preferred)
- optionally a preview mp3 for library browsing

Implementation notes:
- for “no external runtime deps”, bundle a decoder (ffmpeg sidecar or library)
- normalize sample rate (e.g., 48k)

### Stage 2: `beats_tempo`
Input: `audio/mix.wav`
Output:
- `features/beats.json`
- `features/tempo_map.json`

### Stage 3: `sections`
Input: wav + beats
Output: `features/sections.json`

MVP can start with naive energy-based segmentation; later use embedding models.

### Stage 4 (optional): `separate_stems`
Input: wav
Output: `audio/stems/*.wav`

This is optional due to size/compute.

### Stage 5: transcription stages (profile-dependent)

#### 5a) `transcribe_drums`
Input: stem or mix
Output:
- `features/events.json` (adds `onsets[]`)

#### 5b) `transcribe_bass`
Input: bass stem or mix
Output:
- adds `notes[]` for bass track

#### 5c) `transcribe_guitar`
Input: guitar stem or mix
Output:
- notes events (monophonic first; polyphonic later)

#### 5d) `extract_vocal_pitch`
Input: vocal stem or mix
Output:
- `pitch_contours[]`

### Stage 6: `harmony`
Input: wav + beats (+ notes if available)
Output:
- `features/chords.json` and/or `events.json` (adds `chords[]`)

Includes:
- key detection
- chord inference
- mapping to Nashville numbers (relative to key)

### Stage 7: `chart_generation`
Input: events + beats + sections
Output: `charts/*.json`

---
## Plugin architecture for stages

### Stage interface (concept)
Each stage is:
- identified (`id`) and versioned (`version`)
- declares its input artifacts (by path + schema)
- declares its output artifacts
- has a config that is hashable

Pseudo-code:
```python
@dataclass(frozen=True)
class ArtifactSpec:
    path: str
    kind: str              # e.g. "wav", "beats_json", "events_json"
    schema_version: str | None

class Stage(Protocol):
    id: str
    version: str
    def inputs(self) -> list[ArtifactSpec]: ...
    def outputs(self) -> list[ArtifactSpec]: ...
    def fingerprint(self, config: dict) -> str: ...
    def run(self, ctx: PipelineContext) -> None: ...
```

### PipelineContext
Provides:
- paths
- config
- cached artifact store
- logger that emits JSONL progress

---
## Caching and incremental rebuild

### Artifact store
- output directory is the store
- each produced artifact includes metadata:
  - producer stage id/version
  - config fingerprint
  - input hashes

### Rebuild rules
A stage is skipped if:
- all outputs exist
- and metadata indicates same stage version + same config fingerprint
- and all declared inputs have matching hashes

This enables:
- re-importing with different profiles
- fast iterations during ML stage tuning

---
## Determinism and quality gates

## TDD requirement (non-negotiable)
All new pipeline stages and importers must be developed TDD-first:
- add unit tests for stage fingerprinting, cache invalidation, and artifact validation
- add/update golden fixtures when outputs change intentionally
- no new stage is considered “done” without tests

### Determinism
- quantize floats before writing
- stable json key ordering
- stable random seeds (if used)

### Validation
`aural_ingest validate` should:
- ensure required files exist
- validate json schema versions
- ensure event times are within duration
- ensure beats are strictly increasing

---
## Phased implementation strategy (avoid MP3→MIDI trap)

### MVP
- decode + beats/tempo + sections + basic charts
- MIDI import supported (user provides midi) → populate `notes[]`

### v1
- add instrument-specific transcription stages as *optional*, replaceable modules
- models are obtained post-install (in-app download or manual import) and stored under `assets/models/`
- transcription features remain “optional/experimental” until compatible model packs are present
