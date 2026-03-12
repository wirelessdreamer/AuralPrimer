# Melodic Transcription Benchmark Report

Generated: 2026-03-12T12:37:51.630629
Tolerance: 60.0ms
Algorithms: melodic_basic_pitch, melodic_pyin, melodic_yin, melodic_onset_yin, melodic_hpss_yin, melodic_fft_hps, melodic_crepe, melodic_librosa_pyin

## Aggregate Summary

| Algorithm | Mean F1 | Pitch Acc | Octave Err | Timing MAE |
|---|---:|---:|---:|---:|
| melodic_basic_pitch | 0.097 | 8.4% | 11.9% | 34.3ms |
| melodic_pyin | 0.082 | 7.3% | 8.7% | 32.4ms |
| melodic_yin | 0.207 | 19.5% | 24.9% | 33.8ms |
| melodic_onset_yin | 0.237 | 16.9% | 22.0% | 33.3ms |
| melodic_hpss_yin | 0.240 | 18.7% | 23.2% | 33.4ms |
| melodic_fft_hps | 0.250 | 20.2% | 17.4% | 32.1ms |
| melodic_crepe | 0.000 | 0.0% | 0.0% | n/a |
| melodic_librosa_pyin | 0.121 | 22.5% | 34.2% | 35.9ms |

## Psalm 1 — Bass
Instrument: bass | Reference notes: 370

  Algorithm                          F1   Prec    Rec PitchAcc OctErr     MAE  Notes
  ------------------------------------------------------------------------------------------
  melodic_basic_pitch             0.097  0.465  0.054    15.0%  10.0%   33.0ms     43
  melodic_pyin                    0.078  0.262  0.046     0.0%   0.0%   30.4ms     65
  melodic_yin                     0.248  0.333  0.197     4.1%  23.3%   41.1ms    219
  melodic_onset_yin               0.326  0.353  0.303     5.4%  16.1%   36.4ms    317
  melodic_hpss_yin                0.251  0.330  0.203     4.0%  22.7%   40.6ms    227
  melodic_fft_hps                 0.421  0.520  0.354     0.0%  18.3%   33.6ms    252
  melodic_crepe                   0.000  0.000  0.000     0.0%   0.0%     n/a      0
  melodic_librosa_pyin            0.091  0.286  0.054    10.0%  55.0%   40.0ms     70

## Psalm 1 — Guitar
Instrument: lead_guitar | Reference notes: 566

  Algorithm                          F1   Prec    Rec PitchAcc OctErr     MAE  Notes
  ------------------------------------------------------------------------------------------
  melodic_basic_pitch             0.003  0.071  0.002     0.0% 100.0%   54.6ms     14
  melodic_pyin                    0.000  0.000  0.000     0.0%   0.0%     n/a      6
  melodic_yin                     0.205  0.362  0.143     4.9%  18.5%   35.5ms    224
  melodic_onset_yin               0.314  0.434  0.246     6.5%  19.4%   34.7ms    320
  melodic_hpss_yin                0.266  0.373  0.207     9.4%  16.2%   35.2ms    314
  melodic_fft_hps                 0.239  0.396  0.171    28.9%  14.4%   31.6ms    245
  melodic_crepe                   0.000  0.000  0.000     0.0%   0.0%     n/a      0
  melodic_librosa_pyin            0.126  0.246  0.085    22.9%  16.7%   28.5ms    195

## Psalm 1 — Synth
Instrument: keys | Reference notes: 482

  Algorithm                          F1   Prec    Rec PitchAcc OctErr     MAE  Notes
  ------------------------------------------------------------------------------------------
  melodic_basic_pitch             0.113  0.600  0.062     3.3%   6.7%   37.2ms     50
  melodic_pyin                    0.123  0.623  0.069     9.1%   6.1%   36.2ms     53
  melodic_yin                     0.283  0.659  0.180    12.6%  19.5%   34.0ms    132
  melodic_onset_yin               0.371  0.680  0.255    10.6%  18.7%   34.4ms    181
  melodic_hpss_yin                0.334  0.714  0.218    12.4%  17.1%   34.4ms    147
  melodic_fft_hps                 0.304  0.686  0.195    14.9%  13.8%   31.3ms    137
  melodic_crepe                   0.000  0.000  0.000     0.0%   0.0%     n/a      0
  melodic_librosa_pyin            0.217  0.524  0.137    30.3%   7.6%   35.4ms    126

## Psalm 2 — Bass
Instrument: bass | Reference notes: 555

  Algorithm                          F1   Prec    Rec PitchAcc OctErr     MAE  Notes
  ------------------------------------------------------------------------------------------
  melodic_basic_pitch             0.264  0.354  0.211    51.3%  12.0%   30.6ms    330
  melodic_pyin                    0.315  0.436  0.247    43.1%   4.4%   27.8ms    314
  melodic_yin                     0.480  0.574  0.413     4.8%  34.5%   24.4ms    399
  melodic_onset_yin               0.520  0.509  0.531     5.1%  28.1%   26.6ms    579
  melodic_hpss_yin                0.476  0.566  0.411     6.1%  32.5%   24.8ms    403
  melodic_fft_hps                 0.531  0.574  0.494     0.4%  10.6%   26.0ms    477
  melodic_crepe                   0.000  0.000  0.000     0.0%   0.0%     n/a      0
  melodic_librosa_pyin            0.239  0.597  0.149     6.0%  56.6%   35.2ms    139

## Psalm 2 — Guitar
Instrument: lead_guitar | Reference notes: 2050

  Algorithm                          F1   Prec    Rec PitchAcc OctErr     MAE  Notes
  ------------------------------------------------------------------------------------------
  melodic_basic_pitch             0.036  0.263  0.019     0.0%   0.0%   35.5ms    152
  melodic_pyin                    0.023  0.375  0.012     0.0%   4.2%   25.7ms     64
  melodic_yin                     0.154  0.593  0.088    14.9%  37.6%   20.5ms    305
  melodic_onset_yin               0.208  0.604  0.126    15.1%  34.5%   22.1ms    427
  melodic_hpss_yin                0.215  0.556  0.133    13.2%  35.2%   23.6ms    491
  melodic_fft_hps                 0.196  0.537  0.119    26.9%  20.8%   24.1ms    456
  melodic_crepe                   0.000  0.000  0.000     0.0%   0.0%     n/a      0
  melodic_librosa_pyin            0.083  0.459  0.046    30.9%  28.7%   23.1ms    205

## Psalm 2 — Synth
Instrument: keys | Reference notes: 310

  Algorithm                          F1   Prec    Rec PitchAcc OctErr     MAE  Notes
  ------------------------------------------------------------------------------------------
  melodic_basic_pitch             0.163  0.134  0.206     0.0%   7.8%   26.1ms    477
  melodic_pyin                    0.180  0.158  0.210     0.0%   4.6%   28.8ms    412
  melodic_yin                     0.183  0.144  0.248    11.7%  26.0%   18.9ms    533
  melodic_onset_yin               0.207  0.147  0.352    13.8%  22.9%   21.5ms    741
  melodic_hpss_yin                0.206  0.153  0.316    13.3%  23.5%   25.7ms    641
  melodic_fft_hps                 0.197  0.144  0.310    32.3%  12.5%   29.6ms    665
  melodic_crepe                   0.000  0.000  0.000     0.0%   0.0%     n/a      0
  melodic_librosa_pyin            0.176  0.190  0.165    41.2%  23.5%   21.0ms    268

## Psalm 3 — Bass
Instrument: bass | Reference notes: 1078

  Algorithm                          F1   Prec    Rec PitchAcc OctErr     MAE  Notes
  ------------------------------------------------------------------------------------------
  melodic_basic_pitch             0.034  0.328  0.018     0.0%   0.0%   35.9ms     58
  melodic_pyin                    0.019  0.207  0.010     0.0%   9.1%   37.7ms     53
  melodic_yin                     0.148  0.266  0.103    27.9%  12.6%   36.7ms    418
  melodic_onset_yin               0.195  0.285  0.148    21.9%  10.6%   32.5ms    562
  melodic_hpss_yin                0.154  0.271  0.108    27.6%  13.8%   37.2ms    428
  melodic_fft_hps                 0.186  0.300  0.135    10.3%   9.0%   35.6ms    483
  melodic_crepe                   0.000  0.000  0.000     0.0%   0.0%     n/a      0
  melodic_librosa_pyin            0.067  0.199  0.040    30.2%  20.9%   40.0ms    216

## Psalm 3 — Guitar
Instrument: lead_guitar | Reference notes: 1140

  Algorithm                          F1   Prec    Rec PitchAcc OctErr     MAE  Notes
  ------------------------------------------------------------------------------------------
  melodic_basic_pitch             0.018  0.115  0.010     0.0%   9.1%   29.6ms     96
  melodic_pyin                    0.010  0.130  0.005     0.0%  16.7%   38.4ms     46
  melodic_yin                     0.133  0.289  0.086    35.7%   9.2%   31.1ms    339
  melodic_onset_yin               0.163  0.283  0.114    30.0%   9.2%   29.8ms    459
  melodic_hpss_yin                0.197  0.342  0.139    31.0%  13.3%   31.8ms    462
  melodic_fft_hps                 0.148  0.299  0.098    24.1%  12.5%   34.4ms    375
  melodic_crepe                   0.000  0.000  0.000     0.0%   0.0%     n/a      0
  melodic_librosa_pyin            0.128  0.327  0.080    34.1%   6.6%   29.5ms    278

## Psalm 4 — Bass
Instrument: bass | Reference notes: 308

  Algorithm                          F1   Prec    Rec PitchAcc OctErr     MAE  Notes
  ------------------------------------------------------------------------------------------
  melodic_basic_pitch             0.017  0.071  0.010     0.0%   0.0%   48.6ms     42
  melodic_pyin                    0.024  0.121  0.013     0.0%   0.0%   36.0ms     33
  melodic_yin                     0.280  0.281  0.279    58.1%  17.4%   39.8ms    306
  melodic_onset_yin               0.269  0.247  0.295    45.1%  17.6%   37.8ms    368
  melodic_hpss_yin                0.291  0.289  0.292    56.7%  13.3%   40.4ms    311
  melodic_fft_hps                 0.291  0.235  0.380    76.9%   0.0%   37.5ms    497
  melodic_crepe                   0.000  0.000  0.000     0.0%   0.0%     n/a      0
  melodic_librosa_pyin            0.132  0.143  0.123    65.8%  18.4%   42.2ms    266

## Psalm 4 — Guitar
Instrument: lead_guitar | Reference notes: 1536

  Algorithm                          F1   Prec    Rec PitchAcc OctErr     MAE  Notes
  ------------------------------------------------------------------------------------------
  melodic_basic_pitch             0.005  0.154  0.003    25.0%   0.0%   10.7ms     26
  melodic_pyin                    0.004  0.231  0.002     0.0%   0.0%   41.4ms     13
  melodic_yin                     0.083  0.249  0.050    22.4%  21.1%   44.5ms    305
  melodic_onset_yin               0.129  0.306  0.081    20.0%  18.4%   42.4ms    408
  melodic_hpss_yin                0.173  0.344  0.116    21.9%  18.0%   38.9ms    517
  melodic_fft_hps                 0.159  0.345  0.103    19.0%  22.8%   38.3ms    458
  melodic_crepe                   0.000  0.000  0.000     0.0%   0.0%     n/a      0
  melodic_librosa_pyin            0.083  0.295  0.048    20.3%  12.2%   43.8ms    251

## Psalm 4 — Synth
Instrument: keys | Reference notes: 367

  Algorithm                          F1   Prec    Rec PitchAcc OctErr     MAE  Notes
  ------------------------------------------------------------------------------------------
  melodic_basic_pitch             0.172  0.147  0.207     1.3%  11.8%   30.2ms    517
  melodic_pyin                    0.142  0.130  0.158     1.7%   5.2%   29.4ms    447
  melodic_yin                     0.195  0.186  0.204    16.0%  26.7%   34.4ms    403
  melodic_onset_yin               0.206  0.172  0.256    11.7%  26.6%   35.7ms    547
  melodic_hpss_yin                0.219  0.203  0.237    14.9%  26.4%   30.9ms    428
  melodic_fft_hps                 0.201  0.184  0.221    27.2%  18.5%   28.8ms    439
  melodic_crepe                   0.000  0.000  0.000     0.0%   0.0%     n/a      0
  melodic_librosa_pyin            0.152  0.151  0.153    14.3%  17.9%   40.6ms    371

## Psalm 5 — Bass
Instrument: bass | Reference notes: 445

  Algorithm                          F1   Prec    Rec PitchAcc OctErr     MAE  Notes
  ------------------------------------------------------------------------------------------
  melodic_basic_pitch             0.066  0.124  0.045    10.0%   0.0%   23.4ms    161
  melodic_pyin                    0.045  0.094  0.029    15.4%   7.7%   34.1ms    138
  melodic_yin                     0.219  0.232  0.207    28.3%  27.2%   34.0ms    396
  melodic_onset_yin               0.253  0.233  0.276    27.6%  20.3%   31.0ms    527
  melodic_hpss_yin                0.205  0.223  0.189    29.8%  26.2%   33.1ms    376
  melodic_fft_hps                 0.228  0.268  0.198     2.3%  15.9%   34.7ms    328
  melodic_crepe                   0.000  0.000  0.000     0.0%   0.0%     n/a      0
  melodic_librosa_pyin            0.141  0.210  0.106     2.1%  55.3%   35.1ms    224

## Psalm 5 — Guitar
Instrument: lead_guitar | Reference notes: 869

  Algorithm                          F1   Prec    Rec PitchAcc OctErr     MAE  Notes
  ------------------------------------------------------------------------------------------
  melodic_basic_pitch             0.064  0.143  0.041     2.8%   8.3%   34.7ms    252
  melodic_pyin                    0.032  0.132  0.018    12.5%  12.5%   37.0ms    121
  melodic_yin                     0.076  0.123  0.055    20.8%  29.2%   39.4ms    390
  melodic_onset_yin               0.095  0.125  0.076    15.2%  21.2%   36.6ms    527
  melodic_hpss_yin                0.129  0.157  0.109    12.6%  15.8%   35.5ms    605
  melodic_fft_hps                 0.137  0.171  0.114    23.2%  12.1%   36.0ms    578
  melodic_crepe                   0.000  0.000  0.000     0.0%   0.0%     n/a      0
  melodic_librosa_pyin            0.059  0.114  0.040     8.6%  28.6%   34.0ms    308

## Psalm 5 — Synth
Instrument: keys | Reference notes: 139

  Algorithm                          F1   Prec    Rec PitchAcc OctErr     MAE  Notes
  ------------------------------------------------------------------------------------------
  melodic_basic_pitch             0.339  0.333  0.345    12.5%   6.2%   31.7ms    144
  melodic_pyin                    0.272  0.297  0.252    14.3%   0.0%   33.0ms    118
  melodic_yin                     0.166  0.211  0.137     5.3%  42.1%   29.6ms     90
  melodic_onset_yin               0.133  0.157  0.115     0.0%  43.8%   31.4ms    102
  melodic_hpss_yin                0.220  0.268  0.187     3.9%  34.6%   33.0ms     97
  melodic_fft_hps                 0.197  0.262  0.158    31.8%  36.4%   33.9ms     84
  melodic_crepe                   0.000  0.000  0.000     0.0%   0.0%     n/a      0
  melodic_librosa_pyin            0.030  0.047  0.022     0.0% 100.0%   44.9ms     64

## Psalm 6 — Bass
Instrument: bass | Reference notes: 485

  Algorithm                          F1   Prec    Rec PitchAcc OctErr     MAE  Notes
  ------------------------------------------------------------------------------------------
  melodic_basic_pitch             0.155  0.317  0.103    32.0%  16.0%   32.0ms    158
  melodic_pyin                    0.161  0.370  0.103    24.0%   8.0%   28.5ms    135
  melodic_yin                     0.324  0.552  0.229    28.8%  19.8%   25.8ms    201
  melodic_onset_yin               0.366  0.507  0.287    28.1%  17.3%   29.0ms    274
  melodic_hpss_yin                0.323  0.547  0.229    31.5%  18.9%   26.6ms    203
  melodic_fft_hps                 0.250  0.557  0.161     7.7%   3.9%   28.7ms    140
  melodic_crepe                   0.000  0.000  0.000     0.0%   0.0%     n/a      0
  melodic_librosa_pyin            0.234  0.529  0.150    35.6%  37.0%   33.0ms    138

## Psalm 6 — Guitar
Instrument: lead_guitar | Reference notes: 2920

  Algorithm                          F1   Prec    Rec PitchAcc OctErr     MAE  Notes
  ------------------------------------------------------------------------------------------
  melodic_basic_pitch             0.018  0.643  0.009     0.0%  29.6%   32.6ms     42
  melodic_pyin                    0.009  0.609  0.005     0.0%  21.4%   33.1ms     23
  melodic_yin                     0.150  0.659  0.085    30.8%  14.2%   30.5ms    375
  melodic_onset_yin               0.178  0.665  0.103    28.3%  12.7%   31.3ms    451
  melodic_hpss_yin                0.203  0.750  0.117    24.5%  14.0%   30.7ms    457
  melodic_fft_hps                 0.200  0.730  0.116    19.8%  15.7%   29.6ms    463
  melodic_crepe                   0.000  0.000  0.000     0.0%   0.0%     n/a      0
  melodic_librosa_pyin            0.106  0.586  0.058    33.5%  11.8%   32.6ms    290

## Psalm 7 — Bass
Instrument: bass | Reference notes: 1349

  Algorithm                          F1   Prec    Rec PitchAcc OctErr     MAE  Notes
  ------------------------------------------------------------------------------------------
  melodic_basic_pitch             0.045  0.206  0.025     5.9%   8.8%   40.3ms    165
  melodic_pyin                    0.043  0.218  0.024    18.8%  15.6%   34.3ms    147
  melodic_yin                     0.148  0.330  0.096    16.3%  27.9%   42.2ms    391
  melodic_onset_yin               0.155  0.314  0.103    15.1%  28.1%   41.5ms    443
  melodic_hpss_yin                0.149  0.328  0.096    17.7%  24.6%   41.0ms    397
  melodic_fft_hps                 0.178  0.367  0.118    10.7%  23.3%   37.2ms    433
  melodic_crepe                   0.000  0.000  0.000     0.0%   0.0%     n/a      0
  melodic_librosa_pyin            0.041  0.108  0.025    14.7%  29.4%   38.2ms    314

## Psalm 7 — Guitar
Instrument: lead_guitar | Reference notes: 862

  Algorithm                          F1   Prec    Rec PitchAcc OctErr     MAE  Notes
  ------------------------------------------------------------------------------------------
  melodic_basic_pitch             0.026  0.162  0.014     0.0%   0.0%   37.7ms     74
  melodic_pyin                    0.004  0.071  0.002     0.0%  50.0%   50.3ms     28
  melodic_yin                     0.122  0.226  0.084    26.4%  26.4%   41.8ms    319
  melodic_onset_yin               0.158  0.247  0.116    22.0%  28.0%   39.5ms    404
  melodic_hpss_yin                0.222  0.331  0.167    25.7%  35.4%   37.6ms    435
  melodic_fft_hps                 0.223  0.357  0.162    27.9%  19.3%   35.0ms    392
  melodic_crepe                   0.000  0.000  0.000     0.0%   0.0%     n/a      0
  melodic_librosa_pyin            0.094  0.176  0.064    27.3%  23.6%   36.6ms    312

## Psalm 7 — Synth
Instrument: keys | Reference notes: 16

  Algorithm                          F1   Prec    Rec PitchAcc OctErr     MAE  Notes
  ------------------------------------------------------------------------------------------
  melodic_basic_pitch             0.200  0.214  0.188     0.0%   0.0%   47.8ms     14
  melodic_pyin                    0.074  0.091  0.062     0.0%   0.0%    1.6ms     11
  melodic_yin                     0.333  0.357  0.312     0.0%  40.0%   38.4ms     14
  melodic_onset_yin               0.258  0.267  0.250     0.0%  25.0%   39.5ms     15
  melodic_hpss_yin                0.323  0.333  0.312     0.0%  40.0%   34.4ms     15
  melodic_fft_hps                 0.462  0.600  0.375     0.0%  50.0%   23.7ms     10
  melodic_crepe                   0.000  0.000  0.000     0.0%   0.0%     n/a      0
  melodic_librosa_pyin            0.100  0.083  0.125     0.0% 100.0%   49.2ms     24
