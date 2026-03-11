## Drum Benchmark MIDI Fixtures

This folder contains curated drum-only MIDI fixtures for transcription shootout benchmarks.

Files:

- `manifest.json`: case metadata, expected lane coverage, and high-level stress areas
- `*.mid`: GM drum references using the gameplay lane map

Current cases:

- `01_jrock_verse_chorus_160.mid`
- `02_jrock_gallop_172.mid`
- `03_mathrock_7_8_138.mid`
- `04_mathrock_linear_5_4_122.mid`
- `05_metal_double_bass_190.mid`
- `06_metal_blast_220.mid`
- `07_funk_ghost_notes_102.mid`
- `08_blues_shuffle_92.mid`
- `09_pop_anthem_124.mid`
- `10_rnb_pocket_88.mid`

Generation:

```powershell
py -3 python/ingest/scripts/generate_drum_benchmark_midis.py
```

Example benchmark use:

```powershell
py -3 -m aural_ingest.cli benchmark-drums path\to\drums.wav assets\test_fixtures\drum_benchmark_midis\05_metal_double_bass_190.mid --algorithm combined_filter --algorithm dsp_bandpass_improved
```

Full-suite shootout generation:

```powershell
py -3 python/ingest/scripts/run_drum_benchmark_suite.py --label baseline
```

Benchmark process and visualization requirement:

- `benchmarks/drums/PROCESS.md`

These fixtures are intended to be rendered to audio externally for transcription shootouts, then used as the ground-truth MIDI references during scoring.
