# Drum Reference Shootout

- Generated: `2026-03-24T16:11:40Z`
- Tolerance: `60.0` ms
- Trusted corpus: `Synthetic rendered fixture suite` (10 case(s), trust=`trusted`)
- Suspect corpus: `Suno suspect MIDI corpus` (1 case(s), trust=`suspect`)

Interpretation: negative F1 deltas mean the algorithm scored worse against the suspect Suno references than it did on the trusted synthetic fixture corpus. Positive timing deltas mean worse timing error on the suspect corpus.

## Delta (Suspect - Trusted)

| Algorithm | Trusted Rank | Suspect Rank | Rank Shift | Overall Δ | Core Δ | Kick Δ | Snare Δ | Hi-Hat Δ | Timing Δ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| spectral_flux_multiband | 10 | 9 | -1 | -0.078 | -0.075 | -0.236 | +0.010 | +0.000 | +11.1 ms |
| beat_conditioned_multiband_decoder | 1 | 7 | 6 | -0.167 | -0.146 | -0.289 | +0.032 | -0.181 | +12.2 ms |
| adaptive_beat_grid | 9 | 5 | -4 | -0.070 | -0.019 | -0.147 | +0.083 | +0.006 | +9.9 ms |
| spectral_template_multipass | 5 | 6 | 1 | -0.146 | -0.118 | -0.186 | -0.004 | -0.163 | +13.5 ms |
| spectral_template_with_grid | 3 | 4 | 1 | -0.142 | -0.105 | -0.128 | -0.003 | -0.184 | +10.3 ms |
| multi_resolution | 7 | 8 | 1 | -0.135 | -0.102 | +0.117 | -0.146 | -0.276 | +7.6 ms |
| template_xcorr | 8 | 10 | 2 | -0.141 | -0.172 | -0.070 | -0.215 | -0.231 | +7.7 ms |
| probabilistic_pattern | 4 | 3 | -1 | -0.133 | -0.109 | -0.097 | -0.046 | -0.184 | +10.1 ms |
| onset_aligned | 2 | 2 | 0 | -0.142 | -0.105 | -0.128 | -0.003 | -0.184 | +10.3 ms |
| multi_resolution_template | 6 | 1 | -5 | -0.107 | -0.124 | +0.030 | -0.150 | -0.252 | +4.4 ms |

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
| 9 | spectral_flux_multiband | 0.153 | 0.135 | 0.167 | 0.237 | 0.000 | 39.7 ms |
| 7 | beat_conditioned_multiband_decoder | 0.161 | 0.177 | 0.135 | 0.278 | 0.117 | 39.3 ms |
| 5 | adaptive_beat_grid | 0.177 | 0.183 | 0.284 | 0.154 | 0.110 | 35.4 ms |
| 6 | spectral_template_multipass | 0.165 | 0.204 | 0.243 | 0.245 | 0.124 | 40.1 ms |
| 4 | spectral_template_with_grid | 0.179 | 0.220 | 0.299 | 0.239 | 0.122 | 37.0 ms |
| 8 | multi_resolution | 0.154 | 0.180 | 0.365 | 0.067 | 0.108 | 37.1 ms |
| 10 | template_xcorr | 0.134 | 0.123 | 0.246 | 0.067 | 0.055 | 39.5 ms |
| 3 | probabilistic_pattern | 0.179 | 0.220 | 0.299 | 0.239 | 0.122 | 37.0 ms |
| 2 | onset_aligned | 0.179 | 0.220 | 0.299 | 0.239 | 0.122 | 37.0 ms |
| 1 | multi_resolution_template | 0.183 | 0.175 | 0.358 | 0.100 | 0.068 | 35.6 ms |

## Suspect Cases

- `king_in_zion_suno`: King in Zion (Suno export)  (audio=`D:\Psalms\Psalm 2\Book of Psalms - Psalm 2 - King in Zion Stems\Book of Psalms - Psalm 2 - King in Zion (Drums).wav`, reference=`D:\Psalms\Psalm 2\Book of Psalms - Psalm 2 - King in Zion Stems\Book of Psalms - Psalm 2 - King in Zion (Drums).mid`)
  Direct Suno drum stem and exported drum MIDI from King in Zion.