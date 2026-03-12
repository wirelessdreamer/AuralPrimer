"""Multi-song real-world regression suite for melodic transcription.

Uses human-authored MIDI references from the Psalms project for
bass, guitar, synth, and keyboard instruments. Run this after every
algorithm change to prevent overfitting to one song.

Usage:
    py -3 benchmarks/melodic/run_melodic_regression.py
    py -3 benchmarks/melodic/run_melodic_regression.py --algorithm melodic_yin --algorithm melodic_onset_yin
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\AuralPrimer")
SRC = ROOT / "python" / "ingest" / "src"
sys.path.insert(0, str(SRC))

from aural_ingest.melodic_benchmark import (
    MELODIC_ALGORITHMS,
    format_melodic_summary,
)
from aural_ingest.melodic_benchmark_suite import (
    run_melodic_benchmark_suite,
    write_melodic_suite_outputs,
)

# -----------------------------------------------------------------------
# Song manifest: per-instrument entries with sync-corrected offsets.
# Each entry has a MIDI reference and a WAV audio stem.
# -----------------------------------------------------------------------
SONGS = [
    # --- Psalm 1 ---
    {"id": "psalm_1_bass", "name": "Psalm 1 — Bass", "instrument": "bass",
     "midi": r"D:\Psalms\Psalm 1\Psalm1_Stems\Psalm 1 (Bass).mid",
     "wav":  r"D:\Psalms\Psalm 1\Psalm1_Stems\Psalm 1 (Bass).wav",
     "offset_sec": -0.540},
    {"id": "psalm_1_guitar", "name": "Psalm 1 — Guitar", "instrument": "lead_guitar",
     "midi": r"D:\Psalms\Psalm 1\Psalm1_Stems\Psalm 1 (Guitar).mid",
     "wav":  r"D:\Psalms\Psalm 1\Psalm1_Stems\Psalm 1 (Guitar).wav",
     "offset_sec": -0.540},
    {"id": "psalm_1_synth", "name": "Psalm 1 — Synth", "instrument": "keys",
     "midi": r"D:\Psalms\Psalm 1\Psalm1_Stems\Psalm 1 (Synth).mid",
     "wav":  r"D:\Psalms\Psalm 1\Psalm1_Stems\Psalm 1 (Synth).wav",
     "offset_sec": -0.540},

    # --- Psalm 2 ---
    {"id": "psalm_2_bass", "name": "Psalm 2 — Bass", "instrument": "bass",
     "midi": r"D:\Psalms\Psalm 2\Book of Psalms - Psalm 2 - King in Zion Stems\Book of Psalms - Psalm 2 - King in Zion (Bass).mid",
     "wav":  r"D:\Psalms\Psalm 2\Book of Psalms - Psalm 2 - King in Zion Stems\Book of Psalms - Psalm 2 - King in Zion (Bass).wav",
     "offset_sec": -0.480},
    {"id": "psalm_2_guitar", "name": "Psalm 2 — Guitar", "instrument": "lead_guitar",
     "midi": r"D:\Psalms\Psalm 2\Book of Psalms - Psalm 2 - King in Zion Stems\Book of Psalms - Psalm 2 - King in Zion (Guitar).mid",
     "wav":  r"D:\Psalms\Psalm 2\Book of Psalms - Psalm 2 - King in Zion Stems\Book of Psalms - Psalm 2 - King in Zion (Guitar).wav",
     "offset_sec": -0.480},
    {"id": "psalm_2_synth", "name": "Psalm 2 — Synth", "instrument": "keys",
     "midi": r"D:\Psalms\Psalm 2\Book of Psalms - Psalm 2 - King in Zion Stems\Book of Psalms - Psalm 2 - King in Zion (Synth).mid",
     "wav":  r"D:\Psalms\Psalm 2\Book of Psalms - Psalm 2 - King in Zion Stems\Book of Psalms - Psalm 2 - King in Zion (Synth).wav",
     "offset_sec": -0.480},

    # --- Psalm 3 ---
    {"id": "psalm_3_bass", "name": "Psalm 3 — Bass", "instrument": "bass",
     "midi": r"D:\Psalms\Psalm 3\Book of Psalms - Psalms 3 - Shield Me On All Sides (Bass).mid",
     "wav":  r"D:\Psalms\Psalm 3\Book of Psalms - Psalms 3 - Shield Me On All Sides (Bass).wav",
     "offset_sec": -0.540},
    {"id": "psalm_3_guitar", "name": "Psalm 3 — Guitar", "instrument": "lead_guitar",
     "midi": r"D:\Psalms\Psalm 3\Book of Psalms - Psalms 3 - Shield Me On All Sides (Guitar).mid",
     "wav":  r"D:\Psalms\Psalm 3\Book of Psalms - Psalms 3 - Shield Me On All Sides (Guitar).wav",
     "offset_sec": -0.540},

    # --- Psalm 4 ---
    {"id": "psalm_4_bass", "name": "Psalm 4 — Bass", "instrument": "bass",
     "midi": r"D:\Psalms\Psalm 4\Book of Psalms - Psalm 4 - Trouble Again Stems\Book of Psalms - Psalm 4 - Trouble Again (Bass).mid",
     "wav":  r"D:\Psalms\Psalm 4\Book of Psalms - Psalm 4 - Trouble Again Stems\Book of Psalms - Psalm 4 - Trouble Again (Bass).wav",
     "offset_sec": -0.540},
    {"id": "psalm_4_guitar", "name": "Psalm 4 — Guitar", "instrument": "lead_guitar",
     "midi": r"D:\Psalms\Psalm 4\Book of Psalms - Psalm 4 - Trouble Again Stems\Book of Psalms - Psalm 4 - Trouble Again (Guitar).mid",
     "wav":  r"D:\Psalms\Psalm 4\Book of Psalms - Psalm 4 - Trouble Again Stems\Book of Psalms - Psalm 4 - Trouble Again (Guitar).wav",
     "offset_sec": -0.540},
    {"id": "psalm_4_synth", "name": "Psalm 4 — Synth", "instrument": "keys",
     "midi": r"D:\Psalms\Psalm 4\Book of Psalms - Psalm 4 - Trouble Again Stems\Book of Psalms - Psalm 4 - Trouble Again (Synth).mid",
     "wav":  r"D:\Psalms\Psalm 4\Book of Psalms - Psalm 4 - Trouble Again Stems\Book of Psalms - Psalm 4 - Trouble Again (Synth).wav",
     "offset_sec": -0.540},

    # --- Psalm 5 ---
    {"id": "psalm_5_bass", "name": "Psalm 5 — Bass", "instrument": "bass",
     "midi": r"D:\Psalms\Psalm 5\Book of Psalms - Psalm 5 - Every Morning Stems\Book of Psalms - Psalm 5 - Every Morning (Bass).mid",
     "wav":  r"D:\Psalms\Psalm 5\Book of Psalms - Psalm 5 - Every Morning Stems\Book of Psalms - Psalm 5 - Every Morning (Bass).wav",
     "offset_sec": -0.550},
    {"id": "psalm_5_guitar", "name": "Psalm 5 — Guitar", "instrument": "lead_guitar",
     "midi": r"D:\Psalms\Psalm 5\Book of Psalms - Psalm 5 - Every Morning Stems\Book of Psalms - Psalm 5 - Every Morning (Guitar).mid",
     "wav":  r"D:\Psalms\Psalm 5\Book of Psalms - Psalm 5 - Every Morning Stems\Book of Psalms - Psalm 5 - Every Morning (Guitar).wav",
     "offset_sec": -0.550},
    {"id": "psalm_5_synth", "name": "Psalm 5 — Synth", "instrument": "keys",
     "midi": r"D:\Psalms\Psalm 5\Book of Psalms - Psalm 5 - Every Morning Stems\Book of Psalms - Psalm 5 - Every Morning (Synth).mid",
     "wav":  r"D:\Psalms\Psalm 5\Book of Psalms - Psalm 5 - Every Morning Stems\Book of Psalms - Psalm 5 - Every Morning (Synth).wav",
     "offset_sec": -0.550},

    # --- Psalm 6 ---
    {"id": "psalm_6_bass", "name": "Psalm 6 — Bass", "instrument": "bass",
     "midi": r"D:\Psalms\Psalm 6\Book of Psalms - Psalm 6 - Break In Stems\Book of Psalms - Psalm 6 - Break In (Bass).mid",
     "wav":  r"D:\Psalms\Psalm 6\Book of Psalms - Psalm 6 - Break In Stems\Book of Psalms - Psalm 6 - Break In (Bass).wav",
     "offset_sec": -0.490},
    {"id": "psalm_6_guitar", "name": "Psalm 6 — Guitar", "instrument": "lead_guitar",
     "midi": r"D:\Psalms\Psalm 6\Book of Psalms - Psalm 6 - Break In Stems\Book of Psalms - Psalm 6 - Break In (Guitar).mid",
     "wav":  r"D:\Psalms\Psalm 6\Book of Psalms - Psalm 6 - Break In Stems\Book of Psalms - Psalm 6 - Break In (Guitar).wav",
     "offset_sec": -0.490},

    # --- Psalm 7 ---
    {"id": "psalm_7_bass", "name": "Psalm 7 — Bass", "instrument": "bass",
     "midi": r"D:\Psalms\Psalm 7\Psalm 7 - The Chase (Edit) Stems\Psalm 7 - The Chase (Edit) (Bass).mid",
     "wav":  r"D:\Psalms\Psalm 7\Psalm 7 - The Chase (Edit) Stems\Psalm 7 - The Chase (Edit) (Bass).wav",
     "offset_sec": -0.540},
    {"id": "psalm_7_guitar", "name": "Psalm 7 — Guitar", "instrument": "lead_guitar",
     "midi": r"D:\Psalms\Psalm 7\Psalm 7 - The Chase (Edit) Stems\Psalm 7 - The Chase (Edit) (Guitar).mid",
     "wav":  r"D:\Psalms\Psalm 7\Psalm 7 - The Chase (Edit) Stems\Psalm 7 - The Chase (Edit) (Guitar).wav",
     "offset_sec": -0.540},
    {"id": "psalm_7_synth", "name": "Psalm 7 — Synth", "instrument": "keys",
     "midi": r"D:\Psalms\Psalm 7\Psalm 7 - The Chase (Edit) Stems\Psalm 7 - The Chase (Edit) (Synth).mid",
     "wav":  r"D:\Psalms\Psalm 7\Psalm 7 - The Chase (Edit) Stems\Psalm 7 - The Chase (Edit) (Synth).wav",
     "offset_sec": -0.540},
]


def main():
    parser = argparse.ArgumentParser(prog="run_melodic_regression")
    parser.add_argument("--algorithm", dest="algorithms", action="append", default=None)
    parser.add_argument("--tolerance-ms", type=float, default=60.0)
    parser.add_argument("--label", default="melodic-regression")
    parser.add_argument("--instrument", default=None, help="Filter to one instrument (bass, lead_guitar, keys)")
    args = parser.parse_args()

    songs = SONGS
    if args.instrument:
        songs = [s for s in songs if s["instrument"] == args.instrument]

    algorithms = args.algorithms or None

    payload = run_melodic_benchmark_suite(
        songs,
        algorithms=algorithms,
        tolerance_ms=args.tolerance_ms,
    )

    out_dir = write_melodic_suite_outputs(
        payload,
        output_root=ROOT / "benchmarks" / "melodic" / "runs",
        label=args.label,
    )
    print(f"\nOutputs saved to: {out_dir}")


if __name__ == "__main__":
    main()
