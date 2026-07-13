# DrumSep Route A Scaffold

`drum_stemsep` is an opt-in research engine for T2.4 of the model upgrade plan.
It does not ship a DrumSep runtime or checkpoint and it is not used by any
profile. Without explicit runtime configuration, the adapter returns `[]`.

Route A idea:

1. Run DrumSep MDX23C on the drums stem to produce kick, snare, toms, hi-hat,
   crash, and ride stems.
2. Detect onsets per separated stem.
3. Use stem identity as the drum class.
4. Use local stem peak/RMS as velocity, so dynamics come from audio energy
   rather than classifier confidence.

The expected checkpoint is:

```text
aufr33-jarredou_DrumSep_model_mdx23c_ep_141_sdr_10.8059.ckpt
```

Current local source audit (2026-07-09):

- The canonical GitHub release URLs under
  `https://github.com/jarredou/models/releases/download/aufr33-jarredou_MDX23C_DrumSep_model_v0.1/`
  return 404 for both the `.yaml` and `.ckpt`.
- A review-pending mirror copy is parked at
  `D:\AuralPrimer\.external\drumsep\review_pending\politrees_uvr_resources`.
  The checkpoint SHA-256 is
  `d2a4aa53eb584d21eead358a4e66d1882ad182911be018f052b5da73be9096d0` and
  the config SHA-256 is
  `17d1649a227f841165bdb4c11a42082898192a1ea3ceab7e7e0b9293d6589dd6`.
- HF mirrors with the exact expected checkpoint filename report the same
  SHA-256, but their repo-level licenses conflict (`unknown`,
  `cc-by-nc-sa-4.0`, and `MIT`). Intel's derived OpenVINO model card lists
  CC BY-NC-SA for this DrumSep model and points back to the now-404 canonical
  GitHub release.

Keep the mirror out of the sidecar and packaged releases unless licensing and
provenance are resolved.

## Environment Variables

- `AURAL_DRUM_STEMSEP_PYTHON`: required. Python executable for a dedicated
  external runtime environment with the separator/onset dependencies installed.
- `AURAL_DRUM_STEMSEP_RUNNER`: required unless supplied by a modelpack. Python
  script that accepts the JSON request path as its only argument.
- `AURAL_DRUM_STEMSEP_CHECKPOINT`: required unless supplied by a modelpack.
  Path to the DrumSep MDX23C checkpoint.
- `AURAL_DRUM_STEMSEP_REPO`: optional. Runtime repository directory. When set,
  the adapter uses it as the subprocess working directory and prepends it to
  `PYTHONPATH`.
- `AURAL_DRUM_STEMSEP_CONFIG`: optional for
  `python/ingest/scripts/run_drum_stemsep_msst.py`. When unset, the helper
  searches beside the checkpoint for `config_drumsep_mdx23c.yaml`.
- `AURAL_DRUM_STEMSEP_FORCE_CPU`: optional for the MSST helper. Defaults to
  CPU (`1`). Set to `0` to allow CUDA/MPS auto-detection.
- `AURAL_DRUM_STEMSEP_BIGSHIFTS`: optional for the MSST helper. Defaults to
  `1`.

Do not install MSST, torch, TensorFlow, or separator dependencies into the
frozen ingest sidecar just to enable this scaffold.

## Validation

After setting the environment variables, validate the external runner contract
before scheduling any E-GMD or psalm benchmark:

```powershell
$env:PYTHONPATH = "D:\AuralPrimer\python\ingest\src"
$env:AURAL_MODEL_UPGRADE_EVIDENCE_ROOT = "D:\AuralPrimer"
$env:AURAL_DRUM_STEMSEP_PYTHON = "D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe"
$env:AURAL_DRUM_STEMSEP_REPO = "D:\AuralPrimer\.external\msst\Music-Source-Separation-Training"
$env:AURAL_DRUM_STEMSEP_RUNNER = "D:\AuralPrimer\python\ingest\scripts\run_drum_stemsep_msst.py"
$env:AURAL_DRUM_STEMSEP_CHECKPOINT = "D:\AuralPrimer\.external\drumsep\review_pending\politrees_uvr_resources\MDX23C-DrumSep-aufr33-jarredou.ckpt"
$env:AURAL_DRUM_STEMSEP_CONFIG = "D:\AuralPrimer\.external\drumsep\review_pending\politrees_uvr_resources\config_drumsep_mdx23c.yaml"
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe `
  python\ingest\scripts\validate_drum_stemsep_runtime.py `
  path\to\drums.wav `
  --require-events `
  --write-gate-evidence
```

Without `--require-events`, the validator only requires a configured runtime
and a valid runner JSON response. With `--require-events`, it also fails when
the runner returns zero mapped drum events. The report includes resolved
runtime paths, missing configuration items, return code, stdout/stderr tails,
and mapped event payloads. `--write-gate-evidence` writes the report directly
to `benchmarks/runtime/runs/*_drum_stemsep_runtime.json`, which strict
`runtime-check` consumes.

Exit codes:

- `0`: configured runtime ran and met the event requirement.
- `1`: runner contract succeeded but `--require-events` was not satisfied.
- `2`: missing WAV, missing runtime config, runner failure, timeout, or
  malformed/missing output.

## Modelpack Layout

The adapter also looks under the standard model search roots for a
`drum_stemsep` modelpack:

```text
<model-root>/drum_stemsep/<version>/
  modelpack.json
  files/
    checkpoints/
      drum_stemsep/
        aufr33-jarredou_DrumSep_model_mdx23c_ep_141_sdr_10.8059.ckpt
    bin/
      run_drum_stemsep.py
```

`AURAL_DRUM_STEMSEP_PYTHON` is still required even when using a modelpack,
because the runner is expected to execute in a separate environment.

## Runner Contract

The adapter invokes:

```text
<AURAL_DRUM_STEMSEP_PYTHON> <runner> <request.json>
```

The request contains:

```json
{
  "engine": "drum_stemsep",
  "wav_path": "path/to/drums.wav",
  "out_json": "path/to/events.json",
  "checkpoint_path": "path/to/checkpoint.ckpt",
  "stems": ["kick", "snare", "toms", "hi_hat", "crash", "ride"]
}
```

The runner should write `out_json` as either an event list or
`{"events": [...]}`. Accepted event fields:

```json
{"time": 0.123, "stem": "ride", "velocity": 0.78, "duration": 0.05}
```

Aliases are accepted for `time` (`onset`, `start`), class (`stem`, `label`,
`instrument`, `note`, `pitch`, `midi_note`, `class`), and velocity (`velocity`,
`rms`, `peak`, `confidence`). Float velocities in `0..1` are scaled to MIDI
`1..127`.

Class mapping:

- `kick` -> MIDI 36
- `snare` -> MIDI 38
- `hi_hat` -> MIDI 42
- `toms` -> MIDI 47
- `tom_low` -> MIDI 41
- `tom_high` -> MIDI 48
- `crash` -> MIDI 49
- `ride` -> MIDI 51

Any runtime failure, non-zero exit, missing output, or malformed output returns
`[]` so fallback chains remain unaffected.

## MSST Helper

`python/ingest/scripts/run_drum_stemsep_msst.py` is a thin external-runner
implementation for local research validation. It calls MSST `inference.py`
with `--model_type mdx23c`, writes temporary kick/snare/toms/hi-hat/ride/crash
stems, detects energy onsets per separated stem, and writes the adapter's
accepted `{"events": [...]}` JSON contract. It is intentionally outside the
frozen ingest sidecar and does not change fallback behavior.

This helper does not resolve the checkpoint license/provenance review. Keep the
review-pending mirror out of sidecars, packaged modelpacks, and releases until
that review is complete.
