"""Download the Edwards et al. robust piano_pti checkpoint.

The checkpoint is a re-train of the Kong et al. (bytedance/piano_transcription)
``Regress_onset_offset_frame_velocity_CRNN`` with pitch-shift and reverb
augmentation. It is a drop-in for the ``piano_transcription_inference``
package's loader.

- Source: https://zenodo.org/records/10610212  (CC-BY 4.0)
- Reference: Edwards et al., "A Data-Driven Analysis of Robust Automatic Piano
  Transcription", arXiv:2402.01424.
- Sizes: ~104 MB

Usage::

    python scripts/download_piano_pti_checkpoint.py [--dest <dir>]

Default destination is ``<repo-root>/assets/models/piano_pti/``. The aural
ingest runtime auto-discovers checkpoints under that path.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path


CHECKPOINT_URL = (
    "https://zenodo.org/records/10610212/files/high_resolution_MAESTRO_augmentations.pth"
)
CHECKPOINT_FILENAME = "high_resolution_MAESTRO_augmentations.pth"
EXPECTED_SIZE_BYTES = 103 * 1024 * 1024  # ~104 MB, used only as a sanity lower bound


def _default_destination() -> Path:
    here = Path(__file__).resolve()
    # python/ingest/scripts/<this> -> repo root is parents[3]
    repo_root = here.parents[3]
    return repo_root / "assets" / "models" / "piano_pti"


def _download(url: str, out: Path) -> None:
    print(f"Downloading {url}")
    print(f"  -> {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    with urllib.request.urlopen(url) as resp, tmp.open("wb") as fh:  # noqa: S310 (trusted URL)
        total = 0
        chunk = 1024 * 256
        while True:
            data = resp.read(chunk)
            if not data:
                break
            fh.write(data)
            total += len(data)
    tmp.replace(out)
    print(f"  wrote {total / (1024*1024):.1f} MB")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=None, help="destination directory")
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    parser.add_argument("--print-sha256", action="store_true")
    args = parser.parse_args(argv)

    dest_dir = args.dest or _default_destination()
    target = dest_dir / CHECKPOINT_FILENAME

    if target.is_file() and not args.force:
        size = target.stat().st_size
        if size >= EXPECTED_SIZE_BYTES:
            print(f"Already present: {target} ({size / (1024*1024):.1f} MB)")
            if args.print_sha256:
                print(f"sha256: {_sha256(target)}")
            return 0
        print(f"Existing file looks truncated ({size} bytes); re-downloading")

    _download(CHECKPOINT_URL, target)
    if args.print_sha256:
        print(f"sha256: {_sha256(target)}")
    print(f"OK: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
