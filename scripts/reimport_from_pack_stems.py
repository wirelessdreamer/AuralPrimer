#!/usr/bin/env python
"""Re-import every built pack from the stems it already contains.

TESTING TOOL (not wired into the app). When the import/transcription pipeline
changes and the original external Suno stem folders are no longer on disk, this
rebuilds each pack by re-running the CURRENT `aural_ingest import-dir` pipeline
on the stems stored inside the pack — so you re-test transcription + the
stems-only writer without needing the source exports.

For each <name>.feedpak under the songs dir (demo skipped):
  1. Read manifest.yaml -> title, artist, and the stems list.
  2. Keep only BASE stems (drop derived guitar splits: lead_guitar,
     rhythm_guitar, guitar_split_source) and map role -> absolute wav path.
  3. Run `import-dir` with a config of {"input_stem_paths": {...}} pointed at an
     empty temp dir, so the pipeline synthesizes the mix from those stems and
     re-runs the full current import.
  4. Swap the freshly built pack in for the old one.

The whole songs dir is backed up first (unless --no-backup). Missing/unreadable
packs are skipped and logged, never fatal.

Run with the project venv python, e.g.:
  python/ingest/.venv/Scripts/python.exe scripts/reimport_from_pack_stems.py --dry-run
  python/ingest/.venv/Scripts/python.exe scripts/reimport_from_pack_stems.py --only psalm5
  python/ingest/.venv/Scripts/python.exe scripts/reimport_from_pack_stems.py           # all
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import yaml

# Derived stems are produced BY the pipeline (guitar split); never feed them
# back in as inputs or guitar would be double-counted.
DERIVED_ROLES = {"lead_guitar", "rhythm_guitar", "guitar_split_source"}
# The demo regenerates on launch; leave it alone.
SKIP_NAMES = {"demo_sine_440hz"}

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SONGS = REPO_ROOT / "AuralPrimerPortable" / "data" / "songs"
DEFAULT_INGEST_SRC = REPO_ROOT / "python" / "ingest" / "src"


def log(msg: str) -> None:
    print(f"[reimport] {msg}", flush=True)


def read_pack(pack: Path) -> tuple[str, str, dict[str, Path]] | None:
    """Return (title, artist, {role: abs stem path}) for a feedpak, or None."""
    man = pack / "manifest.yaml"
    if not man.is_file():
        log(f"skip {pack.name}: no manifest.yaml")
        return None
    data = yaml.safe_load(man.read_text(encoding="utf-8")) or {}
    title = str(data.get("title") or pack.stem)
    artist = str(data.get("artist") or "")
    stems: dict[str, Path] = {}
    for st in data.get("stems", []) or []:
        role = str(st.get("id") or "").strip()
        rel = st.get("file")
        if not role or role in DERIVED_ROLES or not rel:
            continue
        p = pack / rel
        if p.is_file():
            stems[role] = p.resolve()
    if not stems:
        log(f"skip {pack.name}: no usable base stems in manifest")
        return None
    return title, artist, stems


def reimport_one(pack: Path, songs_dir: Path, venv_python: Path,
                 ingest_src: Path, melodic_method: str, sep_provider: str,
                 dry_run: bool) -> bool:
    info = read_pack(pack)
    if info is None:
        return False
    title, artist, stems = info
    roles = ", ".join(sorted(stems))
    log(f"{pack.name}: title={title!r} stems=[{roles}]")

    # New pack built to a sibling temp name, then swapped in on success.
    staging = songs_dir / (pack.stem + ".reimport.feedpak")
    config = {"input_stem_paths": {r: str(p) for r, p in stems.items()}}

    if dry_run:
        log(f"  would import-dir -> {staging.name}  config={json.dumps(config)}")
        return True

    with tempfile.TemporaryDirectory(prefix="reimport_src_") as empty_src:
        # empty_src has no mix.wav, so import-dir synthesizes the mix from the
        # configured input_stem_paths and re-runs the full current pipeline.
        cfg_path = Path(empty_src) / "config.json"
        cfg_path.write_text(json.dumps(config), encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ingest_src)
        cmd = [
            str(venv_python), "-c",
            "from aural_ingest.cli import main; import sys; sys.exit(main())",
            "import-dir", str(empty_src),
            "--out", str(staging),
            "--title", title,
            "--artist", artist,
            "--config", str(cfg_path),
            "--melodic-method", melodic_method,
            # "none" => use the provided input stems as the complete set and do
            # NOT run Demucs on the synthesized mix. Without this, a keys-only
            # piano psalm gets separated into a fabricated 6-stem band. Pass
            # --separate to override when you genuinely want re-separation.
            "--stem-separation-provider", sep_provider,
        ]
        t0 = time.time()
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
        dt = time.time() - t0
        if proc.returncode != 0:
            log(f"  FAILED in {dt:.1f}s (exit {proc.returncode})")
            log("  STDERR tail: " + proc.stderr[-800:])
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            return False

    # import-dir may normalize the extension; find what it actually wrote.
    produced = staging if staging.exists() else next(
        (p for p in songs_dir.glob(pack.stem + ".reimport.*")), None)
    if produced is None or not produced.exists():
        log(f"  built but output not found for {pack.name}")
        return False
    shutil.rmtree(pack, ignore_errors=True)
    final = songs_dir / pack.name
    produced.rename(final)
    log(f"  ok in {dt:.1f}s -> {final.name}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--songs-dir", default=str(DEFAULT_SONGS))
    ap.add_argument("--venv-python", default=sys.executable,
                    help="python that has aural_ingest importable (default: this one)")
    ap.add_argument("--ingest-src", default=str(DEFAULT_INGEST_SRC))
    ap.add_argument("--melodic-method", default="auto")
    # By default, use the pack's own stems verbatim (no Demucs). --separate
    # forces re-separation of the synthesized mix (rarely what you want here).
    ap.add_argument("--separate", action="store_true",
                    help="re-run Demucs separation instead of using the provided stems as-is")
    ap.add_argument("--only", help="reimport only packs whose name contains this")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    songs_dir = Path(args.songs_dir).resolve()
    if not songs_dir.is_dir():
        log(f"songs dir not found: {songs_dir}")
        return 2

    packs = sorted(
        p for p in songs_dir.glob("*.feedpak")
        if p.is_dir() and p.stem not in SKIP_NAMES
        and (not args.only or args.only in p.name)
    )
    if args.limit:
        packs = packs[: args.limit]
    log(f"songs dir: {songs_dir}")
    log(f"{len(packs)} pack(s) to reimport: {', '.join(p.name for p in packs) or '(none)'}")
    if not packs:
        return 0

    if not args.no_backup and not args.dry_run:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = songs_dir.parent / f"songs_reimport_backup_{stamp}"
        log(f"backup -> {backup}")
        shutil.copytree(songs_dir, backup)

    sep_provider = "auto" if args.separate else "none"
    log(f"stem separation: {sep_provider} (provided stems used {'+ re-separated' if args.separate else 'as-is'})")
    ok = 0
    failed: list[str] = []
    for pack in packs:
        if reimport_one(pack, songs_dir, Path(args.venv_python),
                        Path(args.ingest_src), args.melodic_method, sep_provider, args.dry_run):
            ok += 1
        else:
            failed.append(pack.name)
    log(f"done. reimported: {ok}/{len(packs)}")
    if failed:
        log("failed/skipped: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
