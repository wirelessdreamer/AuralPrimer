"""Driver to import every Piano Psalm folder using the new piano cleanup
pipeline (piano_chord_supplement / piano_pti_clean_dedup_pyin family).

Each Piano Psalm folder has Suno-exported stems named like:
  "<Title> - Piano (<Role>).wav"
with corresponding .mid files (weak Suno reference). This script:

  1. Walks every "*Piano*Stems*" subfolder under the corpus root.
  2. Maps each stem to one of KNOWN_INPUT_STEM_ROLES via a Suno-role
     mapping table.
  3. Builds a config JSON with input_stem_paths so aural_ingest's
     import-dir flow uses the existing stems directly instead of
     running Demucs.
  4. Invokes aural_ingest import-dir with --melodic-method=
     piano_chord_supplement (production = piano_pti_clean +
     same-pitch echo dedup + low-pitch pyin supplement +
     analytical chord-onset fallback when PTI returns empty).

Output: one SongPack per Psalm folder under the configured songs
directory (default: portable's data/songs/).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


SUNO_ROLE_MAP: dict[str, str | None] = {
    "Keyboard": "keys",
    "Bass": "bass",
    "Drums": "drums",
    "Guitar": "guitar",
    # NOTE: order matters here -- find_stems_in_psalm_dir picks the FIRST
    # match per role, so put the louder/canonical Suno label ("Vocals")
    # before the supplemental ("Backing Vocals") to avoid silent imports
    # when both stems exist.
    "Vocals": "vocals",
    "Backing Vocals": "vocals",
    "Synth": "other",
    "FX": "other",
    "Percussion": "drums",
}

# Per-role priority order. When the source folder has multiple stems for
# the same role, the lower-indexed Suno label wins. This is the lever
# that fixed "Vocals" outranking "Backing Vocals" -- without it, the
# alphabetical scan in find_stems_in_psalm_dir picked the wrong stem
# and the user heard near-silent vocals in the in-game mix.
SUNO_ROLE_PRIORITY: dict[str, list[str]] = {
    "vocals": ["Vocals", "Backing Vocals"],
    "drums": ["Drums", "Percussion"],
    "other": ["Synth", "FX"],
}

DEFAULT_MELODIC_METHOD = "piano_chord_supplement"


def parse_role_from_stem_name(stem_name: str) -> tuple[str, str | None]:
    m = re.search(r"\(([^)]+)\)\s*$", stem_name)
    if not m:
        return stem_name, None
    role_raw = m.group(1).strip()
    normalized = SUNO_ROLE_MAP.get(role_raw)
    return role_raw, normalized


def find_stems_in_psalm_dir(psalm_dir: Path) -> dict[str, Path]:
    """Return {normalized_role: wav_path}.

    For roles that have a SUNO_ROLE_PRIORITY entry (e.g. "vocals" with
    "Vocals" preferred over "Backing Vocals"), pick the highest-priority
    Suno label that actually exists in the folder. For all other roles
    fall back to alphabetical first-match, which is stable when only one
    candidate exists.
    """
    wavs_by_raw_role: dict[str, Path] = {}
    out: dict[str, Path] = {}
    for wav in sorted(psalm_dir.glob("*.wav")):
        raw, normalized = parse_role_from_stem_name(wav.stem)
        if normalized is None:
            continue
        wavs_by_raw_role.setdefault(raw, wav)
        out.setdefault(normalized, wav)

    for role, priority_order in SUNO_ROLE_PRIORITY.items():
        for preferred_label in priority_order:
            if preferred_label in wavs_by_raw_role:
                out[role] = wavs_by_raw_role[preferred_label]
                break
    return out


def derive_title_and_variant(psalm_dir: Path) -> tuple[str, str | None]:
    """Strip trailing ' - [piano] [Instrumental] Stems' but keep variant marker.

    Returns (title, variant_marker). The variant marker is "instrumental"
    for the no-vocals stems folders, None otherwise. This is what
    distinguishes the two Suno exports per Psalm (with + without vocals).
    """
    name = psalm_dir.name
    is_instrumental = bool(re.search(r"instrumental", name, flags=re.IGNORECASE))
    name = re.sub(
        r"\s+-\s+(piano\s+)?Instrumental\s+Stems\s*$",
        "",
        name,
        flags=re.IGNORECASE,
    )
    name = re.sub(r"\s+-\s+(piano\s+)?Stems\s*$", "", name, flags=re.IGNORECASE)
    # Final fallback: trailing " Stems" or " - Stems" without "piano" between.
    name = re.sub(r"\s+-?\s*Stems\s*$", "", name, flags=re.IGNORECASE)
    variant = "instrumental" if is_instrumental else None
    return name.strip(), variant


def derive_pack_dirname(title: str, variant: str | None) -> str:
    """Returns the SongPack directory name.

    Since the 2026-06-22 pipeline overhaul, ``aural_ingest import-dir``
    emits the new feedpak layout (manifest.yaml + arrangements/
    notation_<role>.json + aural/ subdir + drum_tab.json + song_timeline.json)
    rather than the legacy .auralsong layout (manifest.json + features/
    notes.mid + features/{beats,sections,tempo_map}.json). The Studio
    Cleanup readiness check and the game loader both key on the
    container's file extension to pick the right reader, so a feedpak
    payload sitting under .auralsong reads as "Invalid (missing title)"
    in Studio. The .feedpak extension routes through the new reader and
    everything resolves.

    Both Studio and game accept .feedpak (apps/game/src-tauri/src/lib.rs
    around line 1338 + apps/desktop/src/cleanupReadiness.ts:45).
    """
    s = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")
    s = s.lower() or "song"
    if variant:
        s = f"{s}_{variant}"
    return f"{s}.feedpak"


def run_import(
    *,
    psalm_dir: Path,
    out_dir: Path,
    title: str,
    melodic_method: str,
    venv_python: Path,
    ingest_src: Path,
) -> tuple[bool, str]:
    stems = find_stems_in_psalm_dir(psalm_dir)
    if "keys" not in stems:
        return False, "no Keyboard stem found"

    config = {
        "input_stem_paths": {role: str(path) for role, path in stems.items()},
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    config_path = out_dir.parent / f".{out_dir.name}.config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ingest_src)
    cmd = [
        str(venv_python),
        "-c",
        "from aural_ingest.cli import main; import sys; sys.exit(main())",
        "import-dir",
        str(psalm_dir),
        "--out",
        str(out_dir),
        "--title",
        title,
        "--artist",
        "Suno",
        "--config",
        str(config_path),
        "--melodic-method",
        melodic_method,
    ]
    t0 = time.time()
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    elapsed = time.time() - t0
    if proc.returncode != 0:
        msg = (
            f"exit={proc.returncode} in {elapsed:.1f}s\n"
            f"STDOUT tail: {proc.stdout[-1200:]}\n"
            f"STDERR tail: {proc.stderr[-1200:]}"
        )
        return False, msg
    return True, f"ok in {elapsed:.1f}s -> {out_dir}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--corpus-root",
        default="D:/Psalms/Piano Psalms",
        help="Directory containing the per-Psalm stem folders.",
    )
    parser.add_argument(
        "--out-root",
        default="D:/AuralPrimer/AuralPrimerPortable/data/songs",
        help="Destination directory for the produced SongPacks.",
    )
    parser.add_argument(
        "--venv-python",
        default="D:/Code/AbletonFullControlMCP/.venv/Scripts/python.exe",
    )
    parser.add_argument(
        "--ingest-src",
        default="D:/AuralPrimer/python/ingest/src",
    )
    parser.add_argument("--melodic-method", default=DEFAULT_MELODIC_METHOD)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--skip-existing", action="store_true", default=True)
    args = parser.parse_args()

    corpus_root = Path(args.corpus_root)
    out_root = Path(args.out_root)
    venv_python = Path(args.venv_python)
    ingest_src = Path(args.ingest_src)

    if not corpus_root.is_dir():
        print(f"corpus root not found: {corpus_root}", file=sys.stderr)
        return 2

    psalm_dirs = [
        p
        for p in sorted(corpus_root.iterdir())
        if p.is_dir() and "stems" in p.name.lower()
    ]
    if args.limit is not None:
        psalm_dirs = psalm_dirs[: args.limit]

    out_root.mkdir(parents=True, exist_ok=True)

    results: list[tuple[Path, bool, str]] = []
    for psalm_dir in psalm_dirs:
        title, variant = derive_title_and_variant(psalm_dir)
        pack_dirname = derive_pack_dirname(title, variant)
        out_dir = out_root / pack_dirname
        display_title = f"{title} (instrumental)" if variant else title

        if args.skip_existing and (out_dir / "manifest.json").is_file():
            print(f"[skip-existing] {pack_dirname}: already imported")
            results.append((psalm_dir, True, "skip-existing"))
            continue

        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)

        print(f"[importing ] {display_title}  ->  {out_dir}")
        ok, msg = run_import(
            psalm_dir=psalm_dir,
            out_dir=out_dir,
            title=display_title,
            melodic_method=args.melodic_method,
            venv_python=venv_python,
            ingest_src=ingest_src,
        )
        flag = "ok       " if ok else "FAIL     "
        print(f"[{flag}] {display_title}: {msg}")
        results.append((psalm_dir, ok, msg))

    print()
    print("Summary:")
    n_ok = sum(1 for _, ok, _ in results if ok)
    print(f"  {n_ok}/{len(results)} imports ok")
    for psalm_dir, ok, msg in results:
        flag = "ok" if ok else "FAIL"
        print(f"    [{flag}] {psalm_dir.name}: {msg}")

    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
