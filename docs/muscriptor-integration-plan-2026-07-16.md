# MuScriptor whole-mix transcription — integration plan (2026-07-16)

Add [MuScriptor](https://github.com/muscriptor/muscriptor) (Kyutai × Mirelo,
[blog](https://mirelo.ai/blog/turning-audio-to-midi),
[arXiv 2607.08168](https://arxiv.org/abs/2607.08168)) as an **opt-in,
whole-mix, multi-instrument** transcription engine: one pass over the full
mix that emits notes for every instrument at once, mapped onto AuralStudio's
roles. Chosen over per-stem use so the model runs as designed.

## Verified facts (primary sources, 2026-07-16)

| | |
|---|---|
| Task | audio → multi-track MIDI, whole mix, multi-instrument |
| Arch | decoder-only Transformer, MT3-style note tokenizer; 16 kHz mono → mel |
| Sizes | small 103M · **medium 307M (default)** · large 1.4B (safetensors) |
| Code license | **MIT** (`Kyutai x Mirelo`) → GPLv3-compatible ✓ |
| Weights license | **`cc-by-nc-4.0`** (HF model-card YAML, verified) → NC-OK gate ✓ |
| **Weights gating** | **GATED** — HF requires agreeing to share contact info + accepting terms. **Cannot be bundled/redistributed.** User installs + authenticates. |
| Package | `muscriptor` (PyPI/uv), deps: torch≥2, mido, safetensors, huggingface_hub, soundfile, einops, typer |
| Velocity | **not predicted** (fabricate a constant, like other engines) |

**Consequences for the gate (repo policy):** code MIT clears it; weights
CC-BY-NC clear it *as data*; but the HF gating means we ship **no weights** —
MuScriptor is a user-installed external engine (like adtof/qmul/rmvpe). I can
build + mock-test the integration, but **only the user can run it** (their HF
account must accept the model terms). It lands opt-in and unbenchmarked; no
default flip without the usual benchmark + listening review.

## API contract (from the MIT source)

```python
from muscriptor import TranscriptionModel
m = TranscriptionModel.load_model("medium", device=...)   # downloads to ~/.cache/muscriptor
for ev in m.transcribe(audio_path, instruments=None, prelude_forcing=True):
    # NoteStartEvent(instrument: str, start_time: float, pitch: int)
    # NoteEndEvent(... patches offset ...) ; ProgressEvent (ignore)
```

`instrument` is one of 35 fixed MT3 group names
(`MT3_FULL_PLUS_GROUP_NAMES`): acoustic_piano, electric_piano,
chromatic_percussion, organ, acoustic_guitar, clean/distorted_electric_guitar,
acoustic_bass, electric_bass, violin/viola/cello/contrabass, orchestral_harp,
timpani, string_ensemble, synth_strings, **voice**, orchestra_hit, brass +
saxes + woodwinds, synth_lead, synth_pad, **drums**. `instruments=[...]` is a
hard decode constraint (mask out everything else).

## Instrument-group → AuralStudio role map (the crux)

Roles + notes.mid channels (transcription.py:2600): bass 0, rhythm_guitar 1,
lead_guitar 2, keys 3, vocals 5, drums 9.

| MuScriptor group(s) | Role | Channel |
|---|---|---|
| acoustic_bass, electric_bass, contrabass | **bass** | 0 |
| acoustic_guitar, clean_electric_guitar, distorted_electric_guitar | **rhythm_guitar** | 1 |
| acoustic_piano, electric_piano, organ, chromatic_percussion, orchestral_harp, synth_lead, synth_pad | **keys** | 3 |
| voice | **vocals** | 5 |
| drums | **drums** (DrumEvent → drum_tab, ch 9) | 9 |
| strings/brass/sax/woodwind/timpani/orchestra_hit (everything else) | **keys** (pitched catch-all) | 3 |

The map is a module-level dict, overridable via
`AURAL_MUSCRIPTOR_ROLE_MAP_JSON`. Drums route to `DrumEvent` (pitch = GM drum
note, MuScriptor uses `DRUM_PROGRAM=128`); melodic groups route to
`MelodicNote` (velocity fabricated at a constant, e.g. 100, since MuScriptor
emits none). Guitar lands on rhythm_guitar (combined/polyphonic); the
audio-based lead/rhythm split doesn't apply to whole-mix notes.

## Implementation steps

### 1. Adapter — `algorithms/muscriptor.py` (self-contained, testable now)
Mirror `melodic_rmvpe.py`'s import-safe/env-gated/fail-safe shape.
- `ENGINE_ID = "muscriptor"`. Env: `AURAL_MUSCRIPTOR_SIZE` (default `medium`),
  `AURAL_MUSCRIPTOR_DEVICE`, `AURAL_MUSCRIPTOR_DISABLED`,
  `AURAL_MUSCRIPTOR_WEIGHTS` (local safetensors path override),
  `AURAL_MUSCRIPTOR_ROLE_MAP_JSON`, `AURAL_MUSCRIPTOR_INSTRUMENTS` (decode
  filter).
- `available() -> bool`: `importlib.util.find_spec("muscriptor") is not None`
  and not disabled. **Never imports torch/muscriptor at module import.**
- `transcribe_mix(mix_wav_path) -> MuScriptorResult | None`: lazy-import,
  load model (cached), stream events, pair NoteStart/NoteEnd, bucket by role.
  Returns `{melodic: {role: [MelodicNote]}, drums: [DrumEvent], meta: {...}}`.
  Any missing package / gated-weight download failure / inference error →
  return `None` (never raise) so the caller falls back to the normal
  per-stem path.
- Pure-logic helpers split out for unit tests: `role_for_instrument(group)`,
  `events_to_role_buckets(events)` — testable with fake event objects, no
  torch.

### 2. Registration + gate
- New `KNOWN_WHOLEMIX_ENGINES = ("muscriptor",)` in transcription.py (it is
  NOT a per-stem melodic method — do not add to KNOWN_MELODIC_METHODS).
- `_model_upgrade_gates_snapshot` (cli.py:5127): add
  `muscriptor_wholemix` gate — runtime-configured check (package importable +
  weights resolvable) + a benchmark-report slot, `ready:false` until evidence
  exists, mirroring `qmul_hr_guitar_external_runtime` (cli.py:5399).

### 3. Orchestration hook — `cmd_import` (cli.py)
- New flag in `_add_transcription_options`: `--wholemix-transcriber`
  (default none; `muscriptor` to enable), + config key `wholemix_transcriber`.
- When set AND `muscriptor.available()`: after decode+beats, before per-stem
  melodic/drum transcription, run `transcribe_mix(mix)`; feed its per-role
  MelodicNote lists into the same `instrument_tracks` dict that
  `_build_notes_mid_bytes` consumes, and its DrumEvents into the drum path
  (subject to the existing drum silence gate). Roles MuScriptor covered are
  skipped in the per-stem loop; uncovered roles (or a `None` result) fall
  through to the normal pipeline. Record provenance in
  `manifest.pipeline.wholemix_transcription`.
- Import from a MIX is the natural fit; when only stems are provided,
  synthesize the mix (existing `_synthesize_mix_wav_from_input_stems`) or skip
  with a warning.

### 4. Studio UI (apps/desktop)
- Add a "Whole-mix transcriber" selector (None / MuScriptor) near the drum/
  melodic engine controls; when MuScriptor is picked, show a one-time note
  that it requires `pip install muscriptor` + HF login + accepting the model
  terms (gated). Wire the choice into the ingest config the sidecar reads.

### 5. Attribution (CLAUDE.md gate — same commit as the dep)
README "Third-party components & attribution": add MuScriptor to BOTH the org
table and the music-ML table — upstream link, maker (Kyutai × Mirelo), role
(whole-mix multi-instrument transcription), license **MIT (code) /
CC-BY-NC-4.0 (weights, gated — not bundled)**. Note it ships no weights.

### 6. Tests
- `tests/test_muscriptor_adapter.py`: mock the `muscriptor` module
  (`sys.modules`) with a fake `TranscriptionModel` emitting scripted
  NoteStart/NoteEnd events across several instrument groups; assert role
  bucketing (bass/keys/guitar/vocals) + drum routing + velocity fabrication +
  fail-safe `None` when the package is absent or inference raises.
  `role_for_instrument` table tests for every group.
- Import-safety test: `import aural_ingest.algorithms.muscriptor` with no
  `muscriptor` installed must succeed and `available()` return False.

## Can't-verify honestly
No end-to-end run here: the gated CC-BY-NC weights need the user's HF account
to accept terms + download. Ship the integration mock-tested; the user
installs `muscriptor`, authenticates, runs one import, and we benchmark
(new `benchmarks/` report) before any promotion. Roughly: medium 0.3B on GPU
is interactive; CPU is slow — measure on first real run.
