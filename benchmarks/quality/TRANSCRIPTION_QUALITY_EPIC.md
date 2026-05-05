# Full Transcription Quality Epic

Status: experimental infrastructure implemented; default promotion still gated by full-corpus benchmark and listening/gameplay review.

## Done

- Added transcription profiles: `gameplay_default`, `fidelity_midi`, and `research_ab`.
- Added unified quality benchmark CLI and helper script with corpus scanning, profile metadata, optional model backend reporting, gameplay metrics, JSON/Markdown/HTML reports, and SVG heatmaps.
- Added generated quality manifest support from scanned SongPacks and pre-split stem folders, including role labels, reference MIDI availability, stem provenance, duration, and current method metadata.
- Added promotion-candidate labels to quality reports. These identify benchmark winners but still require listening/in-game review and never auto-promote `gameplay_default`.
- Added bounded guard-run filters (`--role`, `--case-filter`, `--max-cases`) so local quality runs can target representative cases before launching the full manifest.
- Added role-filtering for combined reference MIDI so keys/guitar/bass benchmarks do not compare a stem against unrelated tracks in `features/notes.mid`.
- Saved first local guard smoke report: `benchmarks/quality/runs/20260502_100131_psalm-130-keys-guard-role-filtered`.
- Added a self-contained `classifier_performance.html` explorer for full classifier coverage: per-class metrics, confusions, pitch error summaries, gameplay risk filters, and TP/FP/FN timeline buckets.
- Added gameplay metrics for melodic roles and drums: density, duplicates/chatter, polyphony, lane coverage, overlap rate, piano hand distribution, and start-offset quarantine.
- Recorded selected transcription profile in import metadata and stable song-id fingerprints.
- Added role playability cleanup for bass, lead guitar, and rhythm guitar so gameplay profiles can cap obvious clutter.
- Added a fail-safe `torchcrepe` research method for monophonic stems. It is only selected explicitly or through `research_ab`, not by legacy/default fallback.
- Hardened optional adapter reporting for MT3/YourMT3, Basic Pitch, Transkun, PTI, hFT, torchcrepe, BeatNet, and Omnizart. Missing optional models are visible in reports and do not break portable imports.
- Added drum overlap decoding coverage for hats over confident kick/snare hits.
- Added optional standards-backed quality metric coverage for `mir_eval.transcription` note scoring, fail-safe `museval` separation scoring, and internal-only research dataset root reporting for MUSDB18/MUSDB18-HQ/ENST/IDMT.

## Still Gated

- No new gameplay default has been promoted. The current policy is still to keep stable heuristics as fallback until a full benchmark plus in-game/listening review proves a replacement.
- BeatNet and Omnizart are reported as optional research backends but are not yet active import transcribers. They need adapter implementation only if the benchmark plan chooses to test them directly.
- MT3/YourMT3 remain optional modelpack-backed candidates. They require local checkpoints under the existing model search roots before they can participate in a real benchmark run.
- Full-corpus benchmark results are not committed here because they depend on local song/stem/reference availability. Use `--scan-root ... --write-manifest ...` to generate the local manifest, then review it before running a promotion gate.
- External research datasets are not bundled. Configure dataset roots with `AURAL_MUSDB18_ROOT`, `AURAL_MUSDB18_HQ_ROOT`, `AURAL_ENST_DRUMS_ROOT`, or `AURAL_IDMT_SMT_DRUMS_ROOT` for internal-only benchmark runs.

## Promotion Checklist

- Run the quality suite with representative drums, bass, guitar, keys/piano, and split-folder cases.
- Confirm `mir_eval` note metrics are present for referenced melodic/piano cases and `museval` separation metrics are present only when local reference/estimate stems are configured.
- Expand beyond the first Psalm 130 keys smoke before treating any result as a full promotion gate.
- Reject any candidate that improves F1 while worsening gameplay density, duplicate/chatter, sync quarantine, or piano hand/polyphony sanity on guard cases.
- Treat `promotion_candidates` as benchmark labels only, not default changes.
- Review generated MIDI in-game or as listening artifacts before changing `gameplay_default`.
- Build portable only after targeted tests, full benchmark, import smoke tests, model-absence behavior, sidecar runtime check, and packaging checks pass.
