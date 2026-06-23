"""Surgically fix the vocals.wav in the 3 piano-psalm packs that imported
the quieter "Backing Vocals" stem instead of the louder "Vocals" stem,
then re-sum mix.wav so the in-game playback actually contains the lead
vocal.

The bug was in ``find_stems_in_psalm_dir`` in import_piano_psalms.py
where both "Vocals" and "Backing Vocals" Suno labels mapped to the
"vocals" role and ``setdefault`` picked the alphabetically-first
candidate ("Backing Vocals", typically 5-10x quieter than "Vocals").
The fix in that script is now in place; this script just patches the
output packs that were already produced.

Skips packs that already have a high-RMS vocals.wav (likely already
correct or only ever had one vocal stem in the source).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

try:
    import numpy as np
    import soundfile as sf
except ImportError as e:  # pragma: no cover -- script-level
    print(f"missing dep: {e}", file=sys.stderr)
    sys.exit(2)


CORPUS_ROOT = Path("D:/Psalms/Piano Psalms")
PACKS_ROOT = Path("D:/AuralPrimer/AuralPrimerPortable/data/songs")


def find_source_vocal_stem(src_dir: Path) -> Path | None:
    """Return the LEAD vocal stem (the one with "Vocals" not "Backing Vocals")."""
    candidates = [f for f in sorted(src_dir.glob("*.wav")) if "(Vocals)" in f.name and "Backing" not in f.name]
    if candidates:
        return candidates[0]
    return None


def map_pack_to_source(pack_name: str) -> Path | None:
    """Reverse the pack-naming convention back to the source folder name."""
    # Strip the .auralsong suffix
    stem = pack_name.removesuffix(".auralsong").removesuffix(".songpack")
    # The user has 5 with-vocals psalms (not instrumental):
    by_pack: dict[str, str] = {
        "psalm_10_why":               "Psalm 10 - Why - piano Stems",
        "psalm_121_my_help":          "Psalm 121 - My Help - Piano Stems",
        "psalm_130_please_hear_me":   "psalm 130 - Please Hear Me Stems",
        "psalm_5_every_morning":      "Psalm 5 - Every Morning - Piano Stems",
        "psalm_6_how_long":           "Psalm 6 - How Long - piano Stems",
    }
    src_name = by_pack.get(stem)
    if src_name is None:
        return None
    p = CORPUS_ROOT / src_name
    return p if p.is_dir() else None


def rms(path: Path) -> float:
    if not path.is_file():
        return 0.0
    audio, _ = sf.read(str(path))
    return float(np.sqrt((audio ** 2).mean()))


def synthesize_mix_from_stems(pack: Path) -> None:
    """Sum all stems in audio/stems/ into audio/mix.wav at the input sr.

    Mirrors what aural_ingest's _synthesize_mix_wav_from_input_stems does
    but simplified to what this fix needs: read all stems, sum,
    normalize, write back as 16-bit stereo.
    """
    stems_dir = pack / "audio" / "stems"
    mix_path = pack / "audio" / "mix.wav"

    stem_paths = sorted(stems_dir.glob("*.wav"))
    if not stem_paths:
        return

    info = sf.info(str(stem_paths[0]))
    sr = info.samplerate
    n_samples = 0
    for sp in stem_paths:
        info_p = sf.info(str(sp))
        n_samples = max(n_samples, info_p.frames)

    summed = np.zeros((n_samples, 2), dtype=np.float64)
    for sp in stem_paths:
        audio, sr_local = sf.read(str(sp))
        if sr_local != sr:
            continue
        if audio.ndim == 1:
            audio = np.column_stack([audio, audio])
        elif audio.shape[1] == 1:
            audio = np.column_stack([audio[:, 0], audio[:, 0]])
        pad = n_samples - audio.shape[0]
        if pad > 0:
            audio = np.vstack([audio, np.zeros((pad, audio.shape[1]), dtype=audio.dtype)])
        elif pad < 0:
            audio = audio[:n_samples]
        summed += audio[:, :2].astype(np.float64)

    # Normalize to peak -1 dBFS to match what the ingest pipeline does.
    peak = float(np.abs(summed).max())
    if peak > 0:
        summed *= 0.891 / peak
    summed = np.clip(summed, -1.0, 1.0)
    sf.write(str(mix_path), summed.astype(np.float32), sr, subtype="PCM_16")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--min-broken-rms",
        type=float,
        default=0.005,
        help="A pack's vocals.wav is treated as broken if its RMS is below this threshold.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    fixed: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []

    for pack in sorted(PACKS_ROOT.iterdir()):
        if not pack.is_dir():
            continue
        if "instrumental" in pack.name:
            continue
        if not pack.name.endswith(".auralsong"):
            continue

        vocals_path = pack / "audio" / "stems" / "vocals.wav"
        cur_rms = rms(vocals_path)
        if cur_rms >= args.min_broken_rms:
            print(f"[ok      ] {pack.name}: vocals.wav already healthy (rms={cur_rms:.4f})")
            skipped.append(pack.name)
            continue

        src_dir = map_pack_to_source(pack.name)
        if src_dir is None:
            print(f"[skip    ] {pack.name}: no source mapping known")
            skipped.append(pack.name)
            continue

        src_vocal = find_source_vocal_stem(src_dir)
        if src_vocal is None:
            print(f"[skip    ] {pack.name}: no lead Vocals stem found in source {src_dir.name}")
            skipped.append(pack.name)
            continue

        src_rms = rms(src_vocal)
        if src_rms <= args.min_broken_rms:
            print(f"[skip    ] {pack.name}: source vocals also quiet (rms={src_rms:.4f})")
            skipped.append(pack.name)
            continue

        print(f"[fixing  ] {pack.name}: replacing vocals (rms {cur_rms:.4f} -> {src_rms:.4f})")
        if args.dry_run:
            fixed.append(pack.name)
            continue

        try:
            shutil.copyfile(str(src_vocal), str(vocals_path))
            synthesize_mix_from_stems(pack)
            new_rms = rms(vocals_path)
            print(f"[done    ] {pack.name}: vocals.wav rms={new_rms:.4f}, mix.wav re-summed")
            fixed.append(pack.name)
        except Exception as e:
            print(f"[FAIL    ] {pack.name}: {e}")
            failed.append(pack.name)

    print()
    print(f"Summary: fixed={len(fixed)}, skipped={len(skipped)}, failed={len(failed)}")
    for n in fixed:
        print(f"  fixed: {n}")
    for n in failed:
        print(f"  FAIL:  {n}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
