# Drum Reference Shootout

- Generated: `2026-03-24T16:44:21Z`
- Tolerance: `60.0` ms
- Trusted corpus: `Synthetic rendered fixture suite` (10 case(s), trust=`trusted`)
- Suspect corpus: `Suno suspect MIDI corpus` (1 case(s), trust=`suspect`)

Interpretation: negative F1 deltas mean the algorithm scored worse against the suspect Suno references than it did on the trusted synthetic fixture corpus. Positive timing deltas mean worse timing error on the suspect corpus.

## Delta (Suspect - Trusted)

| Algorithm | Trusted Rank | Suspect Rank | Rank Shift | Overall Δ | Core Δ | Kick Δ | Snare Δ | Hi-Hat Δ | Timing Δ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| combined_filter | 18 | 15 | -3 | +0.083 | +0.091 | +0.156 | +0.120 | -0.002 | +9.3 ms |
| dsp_bandpass_improved | 13 | 2 | -11 | -0.036 | -0.008 | -0.232 | +0.108 | +0.101 | +10.9 ms |
| dsp_spectral_flux | 15 | 4 | -11 | +0.001 | +0.056 | +0.021 | +0.052 | +0.094 | +9.6 ms |
| aural_onset | 14 | 11 | -3 | -0.046 | -0.119 | -0.351 | -0.005 | -0.002 | +14.1 ms |
| adaptive_beat_grid | 11 | 8 | -3 | -0.068 | -0.013 | -0.157 | +0.083 | +0.036 | +10.0 ms |
| beat_conditioned_multiband_decoder | 1 | 12 | 11 | -0.168 | -0.146 | -0.292 | +0.034 | -0.181 | +12.2 ms |
| spectral_flux_multiband | 12 | 14 | 2 | -0.078 | -0.075 | -0.236 | +0.010 | +0.000 | +11.1 ms |
| dsp_bandpass | 16 | 17 | 1 | -0.050 | -0.014 | -0.163 | +0.058 | +0.064 | +5.5 ms |
| librosa_superflux | 17 | 18 | 1 | -0.050 | -0.067 | -0.000 | -0.200 | +0.000 | +9.1 ms |
| spectral_template_multipass | 6 | 9 | 3 | -0.146 | -0.118 | -0.186 | -0.004 | -0.163 | +13.5 ms |
| spectral_template_with_grid | 4 | 7 | 3 | -0.143 | -0.106 | -0.130 | -0.003 | -0.185 | +10.3 ms |
| multi_resolution | 9 | 13 | 4 | -0.135 | -0.102 | +0.117 | -0.146 | -0.276 | +7.6 ms |
| template_xcorr | 10 | 16 | 6 | -0.141 | -0.172 | -0.070 | -0.215 | -0.231 | +7.7 ms |
| probabilistic_pattern | 5 | 6 | 1 | -0.134 | -0.110 | -0.099 | -0.046 | -0.185 | +10.1 ms |
| onset_aligned | 3 | 5 | 2 | -0.143 | -0.106 | -0.130 | -0.003 | -0.185 | +10.3 ms |
| multi_resolution_template | 8 | 3 | -5 | -0.107 | -0.124 | +0.030 | -0.150 | -0.252 | +4.4 ms |
| hybrid_kick_grid | 2 | 10 | 8 | -0.163 | -0.133 | -0.185 | +0.000 | -0.213 | +15.7 ms |
| adaptive_beat_grid_multilabel | 7 | 1 | -6 | -0.088 | -0.055 | -0.091 | +0.083 | -0.156 | +9.3 ms |

## Trusted Synthetic Corpus

| Rank | Algorithm | Overall | Core | Kick | Snare | Hi-Hat | Timing |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 18 | combined_filter | 0.057 | 0.063 | 0.044 | 0.144 | 0.002 | 31.0 ms |
| 13 | dsp_bandpass_improved | 0.220 | 0.209 | 0.440 | 0.165 | 0.022 | 25.8 ms |
| 15 | dsp_spectral_flux | 0.179 | 0.146 | 0.398 | 0.028 | 0.011 | 26.0 ms |
| 14 | aural_onset | 0.209 | 0.216 | 0.372 | 0.274 | 0.002 | 28.2 ms |
| 11 | adaptive_beat_grid | 0.245 | 0.195 | 0.441 | 0.072 | 0.074 | 25.4 ms |
| 1 | beat_conditioned_multiband_decoder | 0.329 | 0.323 | 0.427 | 0.244 | 0.299 | 27.1 ms |
| 12 | spectral_flux_multiband | 0.230 | 0.210 | 0.403 | 0.227 | 0.000 | 28.6 ms |
| 16 | dsp_bandpass | 0.156 | 0.163 | 0.304 | 0.092 | 0.094 | 25.6 ms |
| 17 | librosa_superflux | 0.100 | 0.099 | 0.004 | 0.292 | 0.000 | 33.1 ms |
| 6 | spectral_template_multipass | 0.310 | 0.322 | 0.429 | 0.249 | 0.287 | 26.6 ms |
| 4 | spectral_template_with_grid | 0.322 | 0.326 | 0.429 | 0.242 | 0.306 | 26.7 ms |
| 9 | multi_resolution | 0.288 | 0.282 | 0.248 | 0.212 | 0.384 | 29.5 ms |
| 10 | template_xcorr | 0.275 | 0.295 | 0.316 | 0.282 | 0.287 | 31.8 ms |
| 5 | probabilistic_pattern | 0.313 | 0.330 | 0.397 | 0.285 | 0.306 | 26.8 ms |
| 3 | onset_aligned | 0.322 | 0.326 | 0.429 | 0.242 | 0.306 | 26.7 ms |
| 8 | multi_resolution_template | 0.290 | 0.299 | 0.328 | 0.250 | 0.319 | 31.2 ms |
| 2 | hybrid_kick_grid | 0.328 | 0.337 | 0.428 | 0.246 | 0.335 | 23.5 ms |
| 7 | adaptive_beat_grid_multilabel | 0.290 | 0.263 | 0.427 | 0.072 | 0.290 | 26.7 ms |

## Suspect Suno Corpus

| Rank | Algorithm | Overall | Core | Kick | Snare | Hi-Hat | Timing |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 15 | combined_filter | 0.140 | 0.155 | 0.200 | 0.263 | 0.000 | 40.4 ms |
| 2 | dsp_bandpass_improved | 0.184 | 0.201 | 0.208 | 0.273 | 0.123 | 36.7 ms |
| 4 | dsp_spectral_flux | 0.181 | 0.201 | 0.419 | 0.080 | 0.105 | 35.6 ms |
| 11 | aural_onset | 0.164 | 0.096 | 0.020 | 0.269 | 0.000 | 42.3 ms |
| 8 | adaptive_beat_grid | 0.177 | 0.183 | 0.284 | 0.154 | 0.110 | 35.4 ms |
| 12 | beat_conditioned_multiband_decoder | 0.161 | 0.177 | 0.135 | 0.278 | 0.117 | 39.3 ms |
| 14 | spectral_flux_multiband | 0.153 | 0.135 | 0.167 | 0.237 | 0.000 | 39.7 ms |
| 17 | dsp_bandpass | 0.106 | 0.150 | 0.141 | 0.150 | 0.158 | 31.1 ms |
| 18 | librosa_superflux | 0.050 | 0.032 | 0.004 | 0.092 | 0.000 | 42.2 ms |
| 9 | spectral_template_multipass | 0.165 | 0.204 | 0.243 | 0.245 | 0.124 | 40.1 ms |
| 7 | spectral_template_with_grid | 0.179 | 0.220 | 0.299 | 0.239 | 0.122 | 37.0 ms |
| 13 | multi_resolution | 0.154 | 0.180 | 0.365 | 0.067 | 0.108 | 37.1 ms |
| 16 | template_xcorr | 0.134 | 0.123 | 0.246 | 0.067 | 0.055 | 39.5 ms |
| 6 | probabilistic_pattern | 0.179 | 0.220 | 0.299 | 0.239 | 0.122 | 37.0 ms |
| 5 | onset_aligned | 0.179 | 0.220 | 0.299 | 0.239 | 0.122 | 37.0 ms |
| 3 | multi_resolution_template | 0.183 | 0.175 | 0.358 | 0.100 | 0.068 | 35.6 ms |
| 10 | hybrid_kick_grid | 0.164 | 0.204 | 0.243 | 0.247 | 0.122 | 39.2 ms |
| 1 | adaptive_beat_grid_multilabel | 0.202 | 0.208 | 0.336 | 0.154 | 0.134 | 36.0 ms |

## Suspect Cases

- `king_in_zion_suno`: King in Zion (Suno export)  (audio=`D:\Psalms\Psalm 2\Book of Psalms - Psalm 2 - King in Zion Stems\Book of Psalms - Psalm 2 - King in Zion (Drums).wav`, reference=`D:\Psalms\Psalm 2\Book of Psalms - Psalm 2 - King in Zion Stems\Book of Psalms - Psalm 2 - King in Zion (Drums).mid`)
  Direct Suno drum stem and exported drum MIDI from King in Zion.