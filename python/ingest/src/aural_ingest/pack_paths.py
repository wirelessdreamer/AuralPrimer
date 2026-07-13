"""Pack-path resolution helpers shared across the sidecar (pure, light deps).

This module centralizes the "where do artifacts live inside a pack?" logic so
the three container layouts we support agree:

* ``.auralsong`` (legacy) — stems under ``audio/stems/<role>.wav``, artifacts
  under ``features/``.
* ``.feedpak`` — stems listed in ``manifest.yaml`` (typically
  ``audio/stems/<role>.wav``), our authoring artifacts under ``aural/``.
* ``.sloppak`` (Slopsmith) — stems listed in ``manifest.yaml`` (typically
  ``stems/<role>.ogg``), our authoring artifacts under ``aural/``.

CONTRACT C2: ``pack_feature_dirname`` returns ``"aural"`` for a ``.feedpak``
OR ``.sloppak`` pack, else ``"features"``. This mirrors the frontend's
``featureDir()`` and Rust's feature-dir rule so in-place builds land where the
apps read them.

Only depends on ``yaml`` (already a sidecar dependency). No librosa / torch /
pretty_midi imports here so these helpers stay unit-testable without the heavy
transcription env.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

import yaml


# Manifest-listed stem files are the source of truth; when a pack predates a
# manifest (legacy .auralsong) we fall back to globbing these directories for
# these audio extensions. Order is the search preference for a role.
_STEM_GLOB_DIRS = ("audio/stems", "stems")
_AUDIO_EXTS = ("wav", "ogg", "mp3", "flac")


def pack_feature_dirname(pack_root: Path | str) -> str:
    """In-pack features directory name for a pack root.

    Returns ``"aural"`` for a ``.feedpak`` OR ``.sloppak`` pack, else
    ``"features"`` (legacy ``.auralsong``). CONTRACT C2. Mirrors the frontend's
    ``featureDir()`` (apps/desktop/src/cleanupReadiness.ts) + the Rust
    feature-dir rule so in-place spectrogram / candidate / prep builds land
    where the apps read them.
    """
    name = str(pack_root)
    return "aural" if (name.endswith(".feedpak") or name.endswith(".sloppak")) else "features"


def load_pack_manifest(pack_root: Path | str) -> dict[str, Any] | None:
    """Read ``manifest.yaml`` at the pack root, BOM-stripped, ``yaml.safe_load``.

    Returns the parsed mapping, or ``None`` when the file is absent, unreadable,
    empty, or not a mapping. Never raises on a bad manifest — callers fall back
    to filesystem globbing.
    """
    manifest_path = Path(pack_root) / "manifest.yaml"
    if not manifest_path.is_file():
        return None
    try:
        text = manifest_path.read_text(encoding="utf-8-sig")  # strips a BOM if present
        doc = yaml.safe_load(text)
    except Exception:  # noqa: BLE001 — a bad manifest degrades to glob fallback
        return None
    return doc if isinstance(doc, dict) else None


def _is_safe_manifest_rel_path(rel_path: str) -> bool:
    value = rel_path.strip()
    if not value or "\\" in value or ":" in value or value.startswith("/") or "//" in value:
        return False
    return not any(part in {"", ".", ".."} for part in PurePosixPath(value).parts)


def _manifest_rel_path(manifest: dict[str, Any] | None, key: str) -> str | None:
    if manifest is None:
        return None
    raw = manifest.get(key)
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    return value if _is_safe_manifest_rel_path(value) else None


def resolve_drum_tab_path(pack_root: Path | str) -> Path:
    """Path to the pack's drum-tab artifact.

    Manifest packs may declare a non-default ``drum_tab`` pointer; legacy packs
    and older feedpaks fall back to the historical root ``drum_tab.json``.
    """

    root = Path(pack_root)
    rel = _manifest_rel_path(load_pack_manifest(root), "drum_tab") or "drum_tab.json"
    return root / rel


def _iter_manifest_stems(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    stems = manifest.get("stems")
    if not isinstance(stems, list):
        return []
    return [s for s in stems if isinstance(s, dict)]


def resolve_stem_paths(pack_root: Path | str) -> dict[str, Path]:
    """Map ``role -> absolute stem Path`` for a pack.

    Manifest ``stems[]`` (``{id, file}``) is the source of truth and is tried
    FIRST: each entry's ``file`` is a POSIX rel-path resolved under the pack
    root, kept only when the file actually exists. This handles both the
    feedpak layout (``audio/stems/x.wav``) and the sloppak layout
    (``stems/x.ogg``) transparently.

    When there is no manifest (or it lists no existing stems) we fall back to
    globbing ``audio/stems/*`` then ``stems/*`` for the known audio extensions,
    keying by file stem. The manifest result wins over any glob for the same
    role.

    Returns role -> Path for every stem that exists on disk (may be empty).
    """
    root = Path(pack_root)
    out: dict[str, Path] = {}

    manifest = load_pack_manifest(root)
    if manifest is not None:
        for entry in _iter_manifest_stems(manifest):
            role = entry.get("id")
            rel = entry.get("file")
            if not isinstance(role, str) or not isinstance(rel, str) or not rel:
                continue
            candidate = (root / rel).resolve()
            if candidate.is_file():
                out[role] = candidate

    if out:
        return out

    # Fallback: legacy packs without a usable manifest — glob the stem dirs.
    for rel_dir in _STEM_GLOB_DIRS:
        stem_dir = root / rel_dir
        if not stem_dir.is_dir():
            continue
        for ext in _AUDIO_EXTS:
            for path in sorted(stem_dir.glob(f"*.{ext}")):
                role = path.stem
                out.setdefault(role, path.resolve())
    return out


def resolve_mix_path(pack_root: Path | str) -> Path | None:
    """Absolute path to the pack's full-mix audio, or ``None``.

    Preference order:
      1. The manifest stem flagged ``default: true`` (feedpak/sloppak
         convention — usually the ``full`` stem).
      2. A manifest stem whose id is exactly ``full`` (belt-and-suspenders).
      3. A ``mix`` stem, or an ``audio/mix.{wav,ogg,mp3,flac}`` file (legacy
         .auralsong import intermediate).

    The returned file is guaranteed to exist.
    """
    root = Path(pack_root)
    manifest = load_pack_manifest(root)
    if manifest is not None:
        stems = _iter_manifest_stems(manifest)

        def _existing(entry: dict[str, Any]) -> Path | None:
            rel = entry.get("file")
            if not isinstance(rel, str) or not rel:
                return None
            candidate = (root / rel).resolve()
            return candidate if candidate.is_file() else None

        # 1) default-flagged stem.
        for entry in stems:
            if entry.get("default") is True:
                p = _existing(entry)
                if p is not None:
                    return p
        # 2) a stem literally named "full".
        for entry in stems:
            if entry.get("id") == "full":
                p = _existing(entry)
                if p is not None:
                    return p
        # 3) a stem literally named "mix".
        for entry in stems:
            if entry.get("id") == "mix":
                p = _existing(entry)
                if p is not None:
                    return p

    # Fallback: legacy audio/mix.* import intermediate.
    audio_dir = root / "audio"
    for ext in _AUDIO_EXTS:
        candidate = audio_dir / f"mix.{ext}"
        if candidate.is_file():
            return candidate.resolve()
    return None


def update_manifest_keys(pack_root: Path | str, updates: dict[str, Any]) -> None:
    """Apply ``updates`` to the pack's ``manifest.yaml`` in place, order-preserving.

    Reads the manifest text, ``yaml.safe_load`` (a dict preserves insertion
    order in Python 3.7+), applies ``updates`` (new keys append after the
    existing ones; existing keys are overwritten in place), then
    ``yaml.safe_dump(sort_keys=False, allow_unicode=True)`` and writes it back
    atomically (temp file in the same directory + ``os.replace``). Mirrors
    feedpak_writer.py's dump style and cmd_refresh_meter's atomic-replace
    pattern so a disk-full / killed write can never leave a half-written
    manifest.

    UNKNOWN KEYS + KEY ORDER SURVIVE (feedpak/sloppak both require this). The
    one documented data-loss limit is YAML COMMENTS: ``yaml.safe_load`` does
    not retain them, so any comments in the manifest are dropped on round-trip.
    Field data and key ordering are preserved. (See risk R2 — swap to
    ``ruamel.yaml`` later if comment loss ever bites.)

    Raises ``FileNotFoundError`` if the manifest is missing (the caller should
    only invoke this on a real pack), and re-raises on an unparseable manifest
    rather than silently clobbering it.
    """
    root = Path(pack_root)
    manifest_path = root / "manifest.yaml"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"no manifest.yaml in {root}")

    text = manifest_path.read_text(encoding="utf-8-sig")
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ValueError(f"manifest.yaml is not a mapping: {manifest_path}")

    for key, value in updates.items():
        doc[key] = value

    dumped = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(manifest_path.parent), prefix=manifest_path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(dumped)
        os.replace(tmp_name, manifest_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
