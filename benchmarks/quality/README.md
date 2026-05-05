# Full Transcription Quality Benchmark

This benchmark is the promotion gate for transcription defaults. It complements the focused drum, melodic, and piano suites by adding gameplay metrics, optional model availability, method profile metadata, and a cross-role worst-failure report.

Project tracking lives in `benchmarks/quality/TRANSCRIPTION_QUALITY_EPIC.md`.

## Commands

```powershell
py -3 benchmarks/quality/run_full_corpus_quality.py --scan-root D:\AuralPrimer
py -3 benchmarks/quality/run_full_corpus_quality.py --scan-root D:\AuralPrimer --write-manifest benchmarks/quality/full_corpus_manifest.local.json
py -3 benchmarks/quality/run_full_corpus_quality.py --manifest benchmarks/quality/full_corpus_manifest.template.json --transcription-profile gameplay_default
py -3 benchmarks/quality/run_full_corpus_quality.py --manifest benchmarks/quality/full_corpus_manifest.local.json --role keys --case-filter psalm --max-cases 3 --algorithm piano_auto
```

Use `--transcription-profile research_ab` to expose optional local research candidates. Missing model-backed methods must fail safely and stay visible in the report.

Each run writes `summary.json`, `report.md`, `report.html`, `f1_heatmap.svg`, `gameplay_risk_heatmap.svg`, and `classifier_performance.html` under `benchmarks/quality/runs/<timestamp>_<label>`.

Open `classifier_performance.html` directly from disk to inspect classifier behavior without a server. The page embeds compact report data and provides role/method/risk filters, F1 heatmaps, per-class precision/recall/F1, near-time confusions, pitch error summaries, and TP/FP/FN timeline buckets.

Use `--referenced-only` with `--write-manifest` when you want a promotion-grade manifest that excludes stems without reference MIDI. Without it, generated manifests include unreferenced cases for gameplay-only metrics.

Reports include `promotion_candidates` to label benchmark winners by role. These labels never change defaults by themselves; listening or in-game review remains required.

When generated manifests point at a combined SongPack `features/notes.mid`, the benchmark filters named MIDI tracks by role before evaluating melodic/keys stems.

Reference-backed melodic and piano cases now also include optional `mir_eval.transcription` metrics in `summary.json` and `report.md`:

- onset-only precision/recall/F1/overlap
- onset+offset precision/recall/F1/overlap

These metrics are standards-backed supplements to the in-repo gameplay metrics. If `mir_eval` is missing, the benchmark still runs and records the omission.

Separation benchmarking is available through the optional `museval` adapter when local reference/estimated stem paths and the `museval` package are present. Research datasets are reported by environment variables and are internal-only:

- `AURAL_MUSDB18_ROOT`
- `AURAL_MUSDB18_HQ_ROOT`
- `AURAL_ENST_DRUMS_ROOT`
- `AURAL_IDMT_SMT_DRUMS_ROOT`

Do not commit or ship dataset content or derived in-game fixtures from those datasets.

## Profiles

- `gameplay_default`: stable CPU-first defaults for import/gameplay.
- `fidelity_midi`: denser piano/MIDI review candidates without changing gameplay defaults.
- `research_ab`: all available local candidates, including optional model-backed methods such as MT3/YourMT3, Basic Pitch, Transkun, PTI, hFT, and torchcrepe.

## Promotion Rule

Do not promote a transcription method to gameplay default unless the full quality report improves benchmark metrics and the generated MIDI passes listening or in-game review on guard cases.
