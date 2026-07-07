#!/usr/bin/env python
"""Build the missing refine-candidate sets for every pack — the CLI equivalent
of the Studio "Prep all unbuilt" button.

TESTING TOOL. For each <name>.feedpak under the songs dir (demo skipped), it
looks at the melodic stems actually present (keys/bass/guitar), and runs
`aural_ingest refine-candidates` for any role whose
aural/refine_candidates.<role>.json is missing. Idempotent: roles that already
have candidates are skipped, so it's safe to re-run.

Run with the project venv python:
  python/ingest/.venv/Scripts/python.exe scripts/prep_candidates.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SONGS = REPO / "AuralPrimerPortable" / "data" / "songs"
INGEST_SRC = REPO / "python" / "ingest" / "src"
# Melodic roles that get refine-candidates (drums use drum_tab, not candidates).
MELODIC = ["keys", "bass", "guitar"]
SKIP = {"demo_sine_440hz"}


def main() -> int:
    if not SONGS.is_dir():
        print(f"songs dir not found: {SONGS}")
        return 2
    env = os.environ.copy()
    env["PYTHONPATH"] = str(INGEST_SRC)
    packs = sorted(p for p in SONGS.glob("*.feedpak") if p.is_dir() and p.stem not in SKIP)
    print(f"{len(packs)} pack(s) under {SONGS}", flush=True)
    built = 0
    for pack in packs:
        stems = {p.stem for p in (pack / "audio" / "stems").glob("*.wav")}
        roles = [r for r in MELODIC if r in stems]
        missing = [r for r in roles if not (pack / f"aural/refine_candidates.{r}.json").is_file()]
        if not missing:
            print(f"{pack.name}: candidates present ({', '.join(roles) or 'no melodic stems'})", flush=True)
            continue
        args = [sys.executable, "-c",
                "from aural_ingest.cli import main; import sys; sys.exit(main())",
                "refine-candidates", str(pack)]
        for r in missing:
            args += ["--instrument", r]
        print(f"{pack.name}: building candidates for {missing} …", flush=True)
        t0 = time.time()
        proc = subprocess.run(args, env=env, capture_output=True, text=True)
        dt = time.time() - t0
        if proc.returncode != 0:
            print(f"{pack.name}: FAILED (exit {proc.returncode}) in {dt:.0f}s", flush=True)
            print("  stderr tail: " + proc.stderr[-500:], flush=True)
        else:
            built += 1
            print(f"{pack.name}: ok in {dt:.0f}s -> {missing}", flush=True)
    print(f"DONE. built candidates for {built} pack(s).", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
