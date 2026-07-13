# Beat This DBN refresh-meter smoke

Date: 2026-07-07

Purpose: verify the T1.1 Beat This DBN post-processor in the active ingest venv
and through the production `refresh-meter` CLI path.

## Environment

- Python: `D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe`
- `madmom`: 0.17.dev0, built from pinned CPJKU commit
  `27f032e8947204902c675e5e341a3faf5dc86dae`
- `museval`: 0.4.1
- `jsonschema`: 4.26.0
- Beat This checkpoint:
  `C:\Users\dreamer\.cache\torch\hub\checkpoints\beat_this-final0.ckpt`

## Direct `track_meter()` A/B

Input:
`AuralPrimerPortable/data/songs/psalm_121_my_help.feedpak/audio/mix.wav`

| Post-processor | OK | Runtime | BPM | Beats | Downbeats | Beats/bar | Time signature |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| DBN | yes | 4.90 s | 136.364 | 252 | 63 | 4 | 4/4 |
| Minimal | yes | 2.39 s | 136.364 | 250 | 63 | 4 | 4/4 |

DBN and minimal agreed on the first downbeat phase for this smoke:
first model downbeat at `2.62s`, then `6.06s`.

## `refresh-meter` CLI

The command was run on temporary copies of the feedpaks to avoid mutating the
local sample packs:

```powershell
$env:PYTHONPATH='D:\AuralPrimer\python\ingest\src'
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -m aural_ingest.cli refresh-meter <temp>\psalm_121_my_help.feedpak
```

Primary result:

```json
{
  "ok": true,
  "beat_source": "beat_this",
  "postprocessor": "dbn",
  "bpm": 136.364,
  "time_signature": "4/4",
  "beats_per_bar": 4,
  "beats": 252,
  "downbeats": 63,
  "meter_denominator_provisional": true,
  "backup": "song_timeline.json.bak"
}
```

Additional temp-copy smokes:

| Pack | OK | Post-processor | BPM | Beats | Downbeats | Beats/bar | Time signature |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| `psalm_121_my_help.feedpak` | yes | DBN | 136.364 | 252 | 63 | 4 | 4/4 |
| `psalm_130_please_hear_me.feedpak` | yes | DBN | 71.429 | 218 | 55 | 4 | 4/4 |
| `psalm_5_every_morning.feedpak` | yes | DBN | 88.235 | 415 | 104 | 4 | 4/4 |

## Production-path fix from this smoke

The first `refresh-meter` run exposed a pack-audio resolver issue: this feedpak
has `audio/mix.wav`, but its manifest also marks `bass` as `default: true`.
The refresh path was therefore analyzing the bass stem through
`resolve_mix_path()` and produced a 93.75 BPM grid starting near `41s`.

`_pack_audio_for_analysis()` now prefers explicit `audio/mix.*` files before
manifest default stems, so meter refresh analyzes the full song mix when it is
present.

## Partial human review

Partial notes from the refreshed AuralStudio review build are recorded in:

`benchmarks/meter/beat_this_dbn_barline_listening_review.partial.json`

Current status:

- `psalm_121_my_help.feedpak`: pass; beat identification accounted for the
  freeform piano pause.
- `psalm_130_please_hear_me.feedpak`: pass.
- `psalm_5_every_morning.feedpak`: not approved; slower, quieter sections still
  have multiple trouble spots.

Do not write the final strict evidence file until Psalm 5 is corrected or
explicitly re-reviewed as acceptable.

## Manual review evidence contract

`aural_ingest runtime-check` keeps the
`model_upgrade_gates.beat_this_barline_listening_review` gate pending until a
reviewer records explicit bar-line and listening approval in:

`benchmarks/meter/beat_this_dbn_barline_listening_review.json`

Minimum shape:

```json
{
  "version": 1,
  "gate": "beat_this_barline_listening_review",
  "reviewed_by": "name-or-handle",
  "reviewed_at_utc": "<ISO-8601 UTC timestamp ending in Z>",
  "source_smoke_report": "benchmarks/meter/beat_this_dbn_refresh_meter_smoke.md",
  "cases": {
    "psalm_121_my_help.feedpak": { "barlines_ok": true, "listening_ok": true },
    "psalm_130_please_hear_me.feedpak": { "barlines_ok": true, "listening_ok": true },
    "psalm_5_every_morning.feedpak": { "barlines_ok": true, "listening_ok": true }
  }
}
```

The gate requires the correct gate id, version, source smoke report, non-`TODO`
`reviewed_by` metadata, and an ISO-8601 UTC `reviewed_at_utc` value ending in `Z`
as well as all approval flags.
The generated template starts with `"reviewed_at_utc": "TODO"`; replace that
value only after the review is complete.
Generate the review timestamp at review time, for example:

```powershell
(Get-Date).ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'")
```

Do not add this file until all three timelines have been checked in-game or
through an equivalent bar-line/listening review path.

Helper:

```powershell
$env:AURAL_MODEL_UPGRADE_EVIDENCE_ROOT = "D:\AuralPrimer"
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\meter\beat_this_review_evidence.py --write-template
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\meter\beat_this_review_evidence.py --validate
```

The helper defaults to the same `AURAL_MODEL_UPGRADE_EVIDENCE_ROOT` that
`runtime-check --require-model-upgrade-gates` uses. When that variable is blank
or unset, it uses the current working directory if it contains the model-upgrade
gate checklist, otherwise it falls back to this source checkout. The first command writes
`benchmarks/meter/beat_this_dbn_barline_listening_review.template.json` with the
required cases and both approval flags set to `false`. After review, copy that
shape to `benchmarks/meter/beat_this_dbn_barline_listening_review.json`, fill in
the reviewer metadata, set only reviewed approvals to `true`, and run the
validation command before relying on `runtime-check --require-model-upgrade-gates`.

Alternatively, after the review is complete, write the final evidence file
without manual JSON editing:

```powershell
$reviewedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'")
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\meter\beat_this_review_evidence.py `
  --write-evidence `
  --reviewed-by "reviewer-name-or-handle" `
  --reviewed-at-utc $reviewedAt `
  --approve-case psalm_121_my_help.feedpak `
  --approve-case psalm_130_please_hear_me.feedpak `
  --approve-case psalm_5_every_morning.feedpak
```

The `--write-evidence` path refuses to write the final gate file unless all
three required cases are explicitly approved and the reviewer metadata validates.
