# Drum Reference Shootout

- Generated: `2026-03-24T15:58:35Z`
- Tolerance: `60.0` ms
- Trusted corpus: `Synthetic rendered fixture suite` (10 case(s), trust=`trusted`)
- Suspect corpus: `Suno suspect MIDI corpus` (1 case(s), trust=`suspect`)

Interpretation: negative F1 deltas mean the algorithm scored worse against the suspect Suno references than it did on the trusted synthetic fixture corpus. Positive timing deltas mean worse timing error on the suspect corpus.

## Delta (Suspect - Trusted)

| Algorithm | Trusted Rank | Suspect Rank | Rank Shift | Overall Δ | Core Δ | Kick Δ | Snare Δ | Hi-Hat Δ | Timing Δ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| spectral_flux_multiband | 10 | 14 | 4 | -0.078 | -0.075 | -0.236 | +0.010 | +0.000 | +11.1 ms |
| beat_conditioned_multiband_decoder | 1 | 12 | 11 | -0.167 | -0.146 | -0.289 | +0.032 | -0.181 | +12.2 ms |
| adaptive_beat_grid | 9 | 8 | -1 | -0.070 | -0.019 | -0.147 | +0.083 | +0.006 | +9.9 ms |
| spectral_template_multipass | 5 | 9 | 4 | -0.146 | -0.118 | -0.186 | -0.004 | -0.163 | +13.5 ms |
| spectral_template_with_grid | 3 | 7 | 4 | -0.142 | -0.105 | -0.128 | -0.003 | -0.184 | +10.3 ms |
| multi_resolution | 7 | 13 | 6 | -0.135 | -0.102 | +0.117 | -0.146 | -0.276 | +7.6 ms |
| template_xcorr | 8 | 16 | 8 | -0.141 | -0.172 | -0.070 | -0.215 | -0.231 | +7.7 ms |
| probabilistic_pattern | 4 | 6 | 2 | -0.133 | -0.109 | -0.097 | -0.046 | -0.184 | +10.1 ms |
| onset_aligned | 2 | 5 | 3 | -0.142 | -0.105 | -0.128 | -0.003 | -0.184 | +10.3 ms |
| multi_resolution_template | 6 | 3 | -3 | -0.107 | -0.124 | +0.030 | -0.150 | -0.252 | +4.4 ms |

## Trusted Synthetic Corpus

| Rank | Algorithm | Overall | Core | Kick | Snare | Hi-Hat | Timing |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | spectral_flux_multiband | 0.230 | 0.210 | 0.403 | 0.227 | 0.000 | 28.6 ms |
| 1 | beat_conditioned_multiband_decoder | 0.328 | 0.323 | 0.424 | 0.246 | 0.298 | 27.1 ms |
| 9 | adaptive_beat_grid | 0.247 | 0.202 | 0.430 | 0.072 | 0.104 | 25.6 ms |
| 5 | spectral_template_multipass | 0.310 | 0.322 | 0.429 | 0.249 | 0.287 | 26.6 ms |
| 3 | spectral_template_with_grid | 0.320 | 0.325 | 0.427 | 0.242 | 0.305 | 26.7 ms |
| 7 | multi_resolution | 0.288 | 0.282 | 0.248 | 0.212 | 0.384 | 29.5 ms |
| 8 | template_xcorr | 0.275 | 0.295 | 0.316 | 0.282 | 0.287 | 31.8 ms |
| 4 | probabilistic_pattern | 0.312 | 0.329 | 0.395 | 0.285 | 0.305 | 26.9 ms |
| 2 | onset_aligned | 0.320 | 0.325 | 0.427 | 0.242 | 0.305 | 26.7 ms |
| 6 | multi_resolution_template | 0.290 | 0.299 | 0.328 | 0.250 | 0.319 | 31.2 ms |

## Suspect Suno Corpus

| Rank | Algorithm | Overall | Core | Kick | Snare | Hi-Hat | Timing |
| --- | --- | --- | --- | --- | --- | --- | --- |
| - | combined_filter | 0.140 | 0.155 | 0.200 | 0.263 | 0.000 | 40.4 ms |
| - | dsp_bandpass_improved | 0.184 | 0.201 | 0.208 | 0.273 | 0.123 | 36.7 ms |
| - | dsp_spectral_flux | 0.181 | 0.201 | 0.419 | 0.080 | 0.105 | 35.6 ms |
| - | aural_onset | 0.164 | 0.096 | 0.020 | 0.269 | 0.000 | 42.3 ms |
| 8 | adaptive_beat_grid | 0.177 | 0.183 | 0.284 | 0.154 | 0.110 | 35.4 ms |
| 12 | beat_conditioned_multiband_decoder | 0.161 | 0.177 | 0.135 | 0.278 | 0.117 | 39.3 ms |
| 14 | spectral_flux_multiband | 0.153 | 0.135 | 0.167 | 0.237 | 0.000 | 39.7 ms |
| - | dsp_bandpass | 0.106 | 0.150 | 0.141 | 0.150 | 0.158 | 31.1 ms |
| - | librosa_superflux | 0.050 | 0.032 | 0.004 | 0.092 | 0.000 | 42.2 ms |
| 9 | spectral_template_multipass | 0.165 | 0.204 | 0.243 | 0.245 | 0.124 | 40.1 ms |
| 7 | spectral_template_with_grid | 0.179 | 0.220 | 0.299 | 0.239 | 0.122 | 37.0 ms |
| 13 | multi_resolution | 0.154 | 0.180 | 0.365 | 0.067 | 0.108 | 37.1 ms |
| 16 | template_xcorr | 0.134 | 0.123 | 0.246 | 0.067 | 0.055 | 39.5 ms |
| 6 | probabilistic_pattern | 0.179 | 0.220 | 0.299 | 0.239 | 0.122 | 37.0 ms |
| 5 | onset_aligned | 0.179 | 0.220 | 0.299 | 0.239 | 0.122 | 37.0 ms |
| 3 | multi_resolution_template | 0.183 | 0.175 | 0.358 | 0.100 | 0.068 | 35.6 ms |
| - | hybrid_kick_grid | 0.164 | 0.204 | 0.243 | 0.247 | 0.122 | 39.2 ms |
| - | adaptive_beat_grid_multilabel | 0.202 | 0.208 | 0.336 | 0.154 | 0.134 | 36.0 ms |

## Suspect Cases

- `king_in_zion_suno`: King in Zion (Suno export)  (audio=`D:\Psalms\Psalm 2\Book of Psalms - Psalm 2 - King in Zion Stems\Book of Psalms - Psalm 2 - King in Zion (Drums).wav`, reference=`D:\Psalms\Psalm 2\Book of Psalms - Psalm 2 - King in Zion Stems\Book of Psalms - Psalm 2 - King in Zion (Drums).mid`)
  Direct Suno drum stem and exported drum MIDI from King in Zion.