"""Build all Psalm songpacks using the correct full-mix WAV files.

Each Psalm folder in D:\Psalms contains stem sub-folders, but the full mix
WAVs live at the parent level (D:\Psalms\*.wav).  We use `import` (not
`import-dir`) so the pipeline gets the real mix audio and derives a clean title.
"""

import os
import subprocess
import sys
from pathlib import Path


# Map psalm number -> (mix wav filename, clean title)
PSALM_MAP: list[tuple[int, str, str]] = [
    (1, "Book of Psalms - Psalm 1 - The Road.wav",                    "Book of Psalms - Psalm 1 - The Road"),
    (2, "Book of Psalms - Psalm 2 - King in Zion.wav",                "Book of Psalms - Psalm 2 - King in Zion"),
    (3, "Book of Psalms - Psalm 3 - Shield Me On All Sides.wav",      "Book of Psalms - Psalm 3 - Shield Me On All Sides"),
    (4, "Book of Psalms - Psalm 4 - Trouble Again.wav",               "Book of Psalms - Psalm 4 - Trouble Again"),
    (5, "Book of Psalms - Psalm 5 - Every Morning.wav",               "Book of Psalms - Psalm 5 - Every Morning"),
    (6, "Book of Psalms - Psalm 6 - Break In.wav",                    "Book of Psalms - Psalm 6 - Break In"),
    (7, "Psalm 7 - The Chase (Edit).wav",                             "Book of Psalms - Psalm 7 - The Chase"),
]

PSALMS_DIR = Path("D:/Psalms")
OUT_DIR = Path("D:/AuralPrimer/AuralPrimerPortable/data/songs")
AURAL_INGEST_EXE = Path("D:/AuralPrimer/python/ingest/dist/aural_ingest.exe")


def build_psalms():
    total = len(PSALM_MAP)
    for i, (num, wav_name, title) in enumerate(PSALM_MAP):
        mix_wav = PSALMS_DIR / wav_name
        out_path = OUT_DIR / f"Psalm {num}.songpack"

        if not mix_wav.is_file():
            print(f"[{i+1}/{total}] SKIP Psalm {num} — mix not found: {mix_wav}")
            continue

        print(f"[{i+1}/{total}] Building Psalm {num} -> {out_path} ...")

        cmd = [
            str(AURAL_INGEST_EXE),
            "import",
            str(mix_wav),
            "--out", str(out_path),
            "--title", title,
        ]

        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        for line in process.stdout:
            print("  |", line.strip())

        process.wait()
        if process.returncode != 0:
            print(f"FAILED on Psalm {num} with code {process.returncode}")
            sys.exit(1)

    print(f"DONE — built {total} Psalm songpacks.")


if __name__ == "__main__":
    build_psalms()
