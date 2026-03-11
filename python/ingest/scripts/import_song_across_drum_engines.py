from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import sys


ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT / "python" / "ingest" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from aural_ingest import cli  # noqa: E402
from aural_ingest.transcription import KNOWN_DRUM_FILTERS  # noqa: E402


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "song"


def _variant_title(base_title: str, requested_engine: str, used_engine: str | None) -> str:
    if used_engine and used_engine != requested_engine:
        return f"{base_title} [drums requested: {requested_engine}, used: {used_engine}]"
    return f"{base_title} [drums: {used_engine or requested_engine}]"


def _build_args(
    *,
    source_path: Path,
    out_path: Path,
    title: str,
    artist: str | None,
    profile: str,
    drum_filter: str,
    drum_stem_path: Path | None,
    melodic_method: str,
) -> object:
    args = type("Args", (), {})()
    args.input_audio_path = str(source_path)
    args.out = str(out_path)
    args.profile = profile
    args.config = "{}"
    args.title = title
    args.artist = artist
    args.duration_sec = None
    args.drum_filter = drum_filter
    args.drum_stem_path = str(drum_stem_path) if drum_stem_path is not None else None
    args.melodic_method = melodic_method
    args.shifts = 1
    args.multi_filter = False
    return args


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="import_song_across_drum_engines")
    parser.add_argument("source_path")
    parser.add_argument("--songs-dir", default=str(ROOT / "AuralPrimerPortable" / "data" / "songs"))
    parser.add_argument("--title", required=True)
    parser.add_argument("--artist", default="")
    parser.add_argument("--profile", default="full")
    parser.add_argument("--drum-stem-path")
    parser.add_argument("--melodic-method", default="auto")
    parser.add_argument("--engine", action="append", dest="engines")
    parser.add_argument("--prefix", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source_path = Path(args.source_path)
    songs_dir = Path(args.songs_dir)
    drum_stem_path = Path(args.drum_stem_path) if args.drum_stem_path else None
    if not source_path.is_file():
        raise SystemExit(f"missing source audio: {source_path}")
    if drum_stem_path is not None and not drum_stem_path.is_file():
        raise SystemExit(f"missing drum stem: {drum_stem_path}")

    songs_dir.mkdir(parents=True, exist_ok=True)
    engines = list(args.engines or KNOWN_DRUM_FILTERS)

    base_slug = _slugify(args.prefix or args.title)
    results: list[dict[str, str]] = []

    for requested_engine in engines:
        variant_slug = f"{base_slug}__drums_{_slugify(requested_engine)}"
        out_path = songs_dir / f"{variant_slug}.songpack"
        if out_path.exists():
            shutil.rmtree(out_path)

        import_title = f"{args.title} [drums: {requested_engine}]"
        rc = cli.cmd_import(
            _build_args(
                source_path=source_path,
                out_path=out_path,
                title=import_title,
                artist=args.artist,
                profile=args.profile,
                drum_filter=requested_engine,
                drum_stem_path=drum_stem_path,
                melodic_method=args.melodic_method,
            )
        )
        if rc != 0:
            raise SystemExit(f"import failed for {requested_engine}: rc={rc}")

        manifest_path = out_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text("utf-8"))
        used_engine = (
            manifest.get("recognition", {})
            .get("drums", {})
            .get("used_engine")
        )
        manifest["title"] = _variant_title(args.title, requested_engine, used_engine)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        results.append(
            {
                "requested_engine": requested_engine,
                "used_engine": str(used_engine or ""),
                "songpack_path": str(out_path),
                "title": str(manifest["title"]),
                "song_id": str(manifest.get("song_id", "")),
            }
        )

    print(json.dumps({"ok": True, "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
