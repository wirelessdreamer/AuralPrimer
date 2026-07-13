"""Install an operator-reviewed RMVPE checkpoint.

The official Dream-High/RMVPE code is Apache-2.0, but checkpoint files are
commonly redistributed through mirrors whose license metadata varies. This
helper therefore requires an explicit ``--license-confirmed`` flag and either a
local source file or an operator-reviewed URL.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import shutil
import sys
from urllib.parse import urlparse
import urllib.request
from pathlib import Path


DEFAULT_FILENAME = "rmvpe.pt"
DEFAULT_TIMEOUT_SEC = 60.0 * 10.0
MANIFEST_FILENAME = "rmvpe.checkpoint.json"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _default_destination() -> Path:
    here = Path(__file__).resolve()
    return here.parents[3] / "assets" / "models" / "rmvpe"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_expected_sha256(path: Path, expected_sha256: str | None) -> str:
    actual = _sha256(path)
    if expected_sha256 and actual.lower() != expected_sha256.lower():
        raise ValueError(f"sha256 mismatch for {path}: expected {expected_sha256.lower()}, got {actual}")
    return actual


def _normalize_expected_sha256(expected_sha256: str | None) -> str:
    value = (expected_sha256 or "").strip().lower()
    if not _SHA256_RE.fullmatch(value):
        raise ValueError("--expected-sha256 is required and must be a 64-character hex digest")
    return value


def _validate_source_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError("--source-url must be an HTTPS URL from a reviewed checkpoint source")


def _write_manifest(
    target: Path,
    *,
    digest: str,
    source_kind: str,
    source: str,
) -> None:
    manifest = {
        "version": 1,
        "filename": target.name,
        "sha256": digest,
        "license_confirmed": True,
        "source_kind": source_kind,
        "source": source,
        "installed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (target.parent / MANIFEST_FILENAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _install_source_file(source: Path, target: Path, expected_sha256: str) -> str:
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        if not source.is_file():
            raise FileNotFoundError(f"source checkpoint not found: {source}")
        shutil.copyfile(source, tmp)
        digest = _validate_expected_sha256(tmp, expected_sha256)
        os.replace(tmp, target)
        return digest
    finally:
        if tmp.exists():
            tmp.unlink()


def _download(url: str, target: Path, *, timeout_sec: float, expected_sha256: str) -> str:
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        _validate_source_url(url)
        with urllib.request.urlopen(url, timeout=timeout_sec) as response, tmp.open("wb") as handle:  # noqa: S310
            shutil.copyfileobj(response, handle)
        digest = _validate_expected_sha256(tmp, expected_sha256)
        os.replace(tmp, target)
        return digest
    finally:
        if tmp.exists():
            tmp.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--source-file", type=Path)
    src.add_argument("--source-url")
    parser.add_argument("--dest", type=Path, default=None, help="destination directory")
    parser.add_argument("--filename", default=DEFAULT_FILENAME)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--expected-sha256",
        default=None,
        help="required reviewed SHA-256; install fails and leaves target untouched on mismatch",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=DEFAULT_TIMEOUT_SEC,
        help="download timeout for --source-url",
    )
    parser.add_argument(
        "--license-confirmed",
        action="store_true",
        help="required: you reviewed the checkpoint source/license",
    )
    args = parser.parse_args(argv)

    if not args.license_confirmed:
        parser.error("--license-confirmed is required before installing RMVPE weights")
    try:
        expected_sha256 = _normalize_expected_sha256(args.expected_sha256)
    except ValueError as exc:
        parser.error(str(exc))

    dest_dir = args.dest or _default_destination()
    target = dest_dir / args.filename
    if target.exists() and not args.force:
        print(f"Already present: {target}")
        try:
            digest = _validate_expected_sha256(target, expected_sha256)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        _write_manifest(target, digest=digest, source_kind="existing", source=str(target))
        print(f"sha256: {digest}")
        return 0

    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        if args.source_file:
            source = str(args.source_file)
            digest = _install_source_file(args.source_file, target, expected_sha256)
            source_kind = "file"
        else:
            source = str(args.source_url)
            digest = _download(
                source,
                target,
                timeout_sec=max(1.0, float(args.timeout_sec)),
                expected_sha256=expected_sha256,
            )
            source_kind = "url"
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    _write_manifest(target, digest=digest, source_kind=source_kind, source=source)
    print(f"OK: {target}")
    print(f"sha256: {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
