# Model Upgrade Gate Evidence

`aural_ingest runtime-check --require-model-upgrade-gates` reads durable JSON
evidence from the paths below. A configured external runtime is not enough to
clear a promotion gate; each gate requires the matching successful report.
The selected root is reported by `model_upgrade_gates.evidence_root`, and the
same checklist path is reported by `model_upgrade_gates.evidence_checklist` in
`runtime-check` output.

Run commands from `D:\AuralPrimer` unless noted. When running the frozen
sidecar from another working directory, set
`AURAL_MODEL_UPGRADE_EVIDENCE_ROOT=D:\AuralPrimer` to force the evidence root.
The external runtime validator and benchmark runner scripts accept
`--write-gate-evidence` to write their JSON report directly under the matching
evidence directory with the filename pattern strict `runtime-check` consumes.
Without `--write-gate-evidence`, benchmark runners write exploratory defaults
outside the strict gate globs; pass `--output` only when intentionally writing
a custom report path.
The MUSDB runner refuses `--write-gate-evidence` unless `--split test` is
explicitly present, matching the strict gate's test-split evidence contract.
The ADTOF and DrumSep validators likewise refuse `--write-gate-evidence`
without `--require-events`; the QMUL validator requires `--require-notes`; and
the RoFormer validator requires all four MUSDB roles (`bass`, `drums`,
`other`, `vocals`) before it writes a strict gate-evidence report.
Runtime validators also write failed same-identity reports for missing input
audio when `--write-gate-evidence` is used, so a failed retry can supersede an
older passing runtime validation instead of leaving it authoritative.
Evidence candidates are ordered by the UTC timestamp prefix written into those
filenames; filesystem modified time is only a fallback for unstamped files.
For runtime validation reports, the newest report for each engine-specific glob
is authoritative: a newer failed validation keeps that runtime gate pending
until a newer successful report is written. Benchmark report globs may contain
multiple providers/modelpacks/algorithms and are filtered by the gate's expected
identity before selection; after that filtering, the newest matching benchmark
report is authoritative, so a newer same-identity failure keeps the gate
pending until a newer success is written.

## Gate Evidence Map

| Gate | Required evidence | Output path pattern |
| --- | --- | --- |
| `beat_this_barline_listening_review` | Human review JSON with correct gate/version/source metadata, non-`TODO` reviewer metadata, an ISO-8601 UTC `reviewed_at_utc` value ending in `Z`, and all required Psalm DBN smokes marked `barlines_ok=true` and `listening_ok=true` | `benchmarks/meter/beat_this_dbn_barline_listening_review.json` |
| `musdb_sdr_baseline` | Successful default Demucs MUSDB SDR report (`dataset="musdb18_or_musdb18_hq"`, `split="test"`, and `modelpack_id` absent or `demucs_6`) with `ok=true`, `summary.tracks_ok >= 10`, zero failed/skipped tracks, finite aggregate/per-role SDR, and every required role counted for every successful track | `benchmarks/quality/runs/*_musdb_separation_sdr.json` |
| `demucs_ft_drums_sdr` | License-verified `demucs_ft_drums` modelpack plus successful Demucs MUSDB SDR report whose config selects `stem_separation_modelpack_id: demucs_ft_drums`, records `dataset="musdb18_or_musdb18_hq"` and `split="test"`, has at least 10 successful tracks, zero failed/skipped tracks, and reports finite aggregate/per-role SDR for every successful track | `benchmarks/quality/runs/*_musdb_separation_sdr.json` |
| `roformer_musdb_comparison` | Successful RoFormer runtime validation for bass/drums/other/vocals with non-empty `stem_paths` plus successful default Demucs and RoFormer MUSDB test-split SDR reports where RoFormer aggregate `median_sdr_mean` is not below the Demucs baseline | `benchmarks/runtime/runs/*_roformer_runtime.json` and `benchmarks/quality/runs/*_musdb_separation_sdr.json` |
| `rmvpe_mir_st500_vocals` | Successful RMVPE runtime readiness report plus successful unbounded MIR-ST500 test/vocal benchmark report for `melodic_rmvpe` with `ok=true`, `extra.limit=null`, full case coverage, `cases_ok == case_count`, `cases_err == 0`, and finite aggregate precision/recall/F1 | `benchmarks/runtime/runs/*_rmvpe_runtime.json` and `benchmarks/vocals/gt_runs/*_mir_st500_vocals.json` |
| `adtof_external_runtime` | Successful ADTOF runtime validation with `runtime.configured=true`, `status="ok"`, `--require-events`, integer `event_count > 0`, and an `events[]` array of matching length whose entries carry normalized JSON-number `time`, integer `note`, and integer `velocity` payloads | `benchmarks/runtime/runs/*_adtof_runtime.json` |
| `drum_stemsep_external_runtime` | Successful DrumSep runtime validation with `runtime.configured=true`, `status="ok"`, `--require-events`, integer `event_count > 0`, and an `events[]` array of matching length whose entries carry normalized JSON-number `time`, integer `note`, and integer `velocity` payloads | `benchmarks/runtime/runs/*_drum_stemsep_runtime.json` |
| `qmul_hr_guitar_external_runtime` | Successful QMUL guitar runtime validation with `runtime.configured=true`, `status="ok"`, `--require-notes`, integer `note_count > 0`, and a `notes[]` array of matching length whose entries carry normalized JSON-number `t_on`/`t_off`, integer `pitch`, integer `velocity`, and non-empty `instrument` payloads | `benchmarks/runtime/runs/*_qmul_hr_guitar_runtime.json` |

## Commands

### Beat This Review

Create a non-passing template:

```powershell
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\meter\beat_this_review_evidence.py --write-template
```

After manual review, copy the template shape to
`benchmarks/meter/beat_this_dbn_barline_listening_review.json`, fill reviewer
metadata, replace the template's `"reviewed_at_utc": "TODO"` value with the
current UTC review time, and set only reviewed approvals to `true`.
For PowerShell, generate that value with:

```powershell
(Get-Date).ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'")
```

Validate it with:

Required reviewed cases:

- `psalm_121_my_help.feedpak`
- `psalm_130_please_hear_me.feedpak`
- `psalm_5_every_morning.feedpak`

```powershell
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\meter\beat_this_review_evidence.py --validate
```

Or, after the review is complete, generate the final evidence file directly:

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

The helper refuses to write the final evidence file unless all three required
cases are explicitly approved and the reviewer metadata passes the same
validation used by strict `runtime-check`.

The helper defaults to `AURAL_MODEL_UPGRADE_EVIDENCE_ROOT` when that variable is
set. When the variable is blank or unset, it uses the current working directory
if it contains this checklist, otherwise it falls back to this source checkout,
matching the root strict `runtime-check` reads.

### MUSDB Demucs Baseline

```powershell
$env:PYTHONPATH = "D:\AuralPrimer\python\ingest\src"
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\quality\run_musdb_separation_sdr.py `
  --provider demucs `
  --split test `
  --limit 10 `
  --shifts 1 `
  --write-gate-evidence
```

### Demucs Fine-Tuned Drums

After staging a license-verified `demucs_ft_drums` modelpack, write a config:

```json
{
  "stem_separation_provider": "demucs",
  "stem_separation_modelpack_id": "demucs_ft_drums"
}
```

Then run:

```powershell
$env:PYTHONPATH = "D:\AuralPrimer\python\ingest\src"
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\quality\run_musdb_separation_sdr.py `
  --provider demucs `
  --split test `
  --limit 10 `
  --shifts 1 `
  --config-json path\to\demucs_ft_drums_config.json `
  --write-gate-evidence
```

The opt-in `demucs_ft_drums` path first runs the default `demucs_6` separator
for the complete MUSDB stem set, then replaces only `drums.wav` with the
fine-tuned drums output. That composite output is what the MUSDB SDR report
scores, so bass/other/vocals remain present for full-role gate evidence.

### RoFormer MUSDB Comparison

First validate the external command contract:

```powershell
$env:PYTHONPATH = "D:\AuralPrimer\python\ingest\src"
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe python\ingest\scripts\validate_roformer_runtime.py `
  path\to\mix.wav `
  --stems-dir tmp\roformer-smoke-stems `
  --require-role drums `
  --require-role bass `
  --require-role vocals `
  --require-role other `
  --shifts 1 `
  --write-gate-evidence
```

Then run the MUSDB report:

```powershell
$env:PYTHONPATH = "D:\AuralPrimer\python\ingest\src"
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\quality\run_musdb_separation_sdr.py `
  --provider roformer `
  --split test `
  --limit 10 `
  --shifts 1 `
  --write-gate-evidence
```

### RMVPE And MIR-ST500

```powershell
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe python\ingest\scripts\validate_rmvpe_runtime.py `
  --write-gate-evidence

D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe benchmarks\vocals\run_mir_st500_vocals.py `
  --split test `
  --variant vocal `
  --algorithm melodic_rmvpe `
  --write-gate-evidence `
  --progress
```

### ADTOF

```powershell
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe python\ingest\scripts\validate_adtof_runtime.py `
  path\to\drums.wav `
  --require-events `
  --write-gate-evidence
```

### DrumSep

```powershell
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe python\ingest\scripts\validate_drum_stemsep_runtime.py `
  path\to\drums.wav `
  --require-events `
  --write-gate-evidence
```

### QMUL Guitar

```powershell
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe python\ingest\scripts\validate_qmul_hr_guitar_runtime.py `
  path\to\lead_guitar.wav `
  --instrument lead_guitar `
  --require-notes `
  --write-gate-evidence
```

## Final Check

Before relying on this checklist in CI or packaging review, run the source
contract guard:

```powershell
npm run ci:verify:model-upgrade-gates
```

That verifier checks this checklist, the runtime-check source constants, the
Beat This review helper/template/smoke report, and the live progress doc stay
aligned. It does not clear any promotion gate; it only guards the evidence
contract itself.

After adding any evidence report, run:

```powershell
D:\AuralPrimer\python\ingest\.venv\Scripts\python.exe -m aural_ingest.cli runtime-check --require-model-upgrade-gates
```

Normal `runtime-check` remains startup-safe and does not fail on pending
promotion gates. Strict mode is the release/promotion check.
