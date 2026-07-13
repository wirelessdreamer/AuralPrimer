# MUSDB18-HQ Separation Evaluation - Model Upgrade Gates

Date: 2026-07-10

Corpus:

- Source: MUSDB18-HQ Zenodo archive `musdb18hq.zip`
- MD5: `12d4f2ecd55245a4688754dd76363103`
- Local root: `E:\AudioSourceOfTruthData\extracted\musdb18_hq`
- Split/sample: first 10 `test` tracks discovered by the runner

## Results

| Provider | Gate report | Tracks OK | Failed | Skipped | Median SDR mean | Bass | Drums | Other | Vocals | Decision |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Default Demucs | `benchmarks/quality/runs/20260709_232250_434347_demucs_musdb_separation_sdr.json` | 10 | 0 | 0 | 7.324 | 7.117 | 8.959 | 4.727 | 8.492 | Baseline gate ready |
| `demucs_ft_drums` | `benchmarks/quality/runs/20260709_234848_965718_demucs_demucs_ft_drums_musdb_separation_sdr.json` | 10 | 0 | 0 | 4.398 | 4.129 | 8.143 | -0.232 | 5.553 | Gate evidence ready; do not promote |
| RoFormer/MSST | `benchmarks/quality/runs/20260710_024833_512208_roformer_musdb_separation_sdr.json` | 10 | 0 | 0 | 9.027 | 7.940 | 10.490 | 6.656 | 11.021 | Positive research candidate |

## Notes

- `musdb_sdr_baseline`, `demucs_ft_drums_sdr`, and
  `roformer_musdb_comparison` now clear strict `runtime-check`.
- The first RoFormer attempt used a 900 s per-track timeout and skipped two
  long tracks. The passing report was generated with
  `AURAL_ROFORMER_TIMEOUT_SEC=2400`.
- `demucs_ft_drums` is valid gate evidence, but it underperforms the default
  Demucs baseline overall and on the drums role for this sample. It should not
  be promoted as a default/profile quality improvement from this evidence.
- RoFormer beats the default Demucs baseline on aggregate median SDR mean for
  this sample, but it has much higher CPU runtime and still needs
  shipping/license review before any product-profile effect.
