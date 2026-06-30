"""Regression tests for the user-supplied-stem protection in
``_copy_cached_stems``.

The bug fixed here: when the user supplied an input stem for a role
(typically a clean Suno-exported (Keyboard).wav landing at
``audio/stems/keys.wav`` via ``input_stem_paths``), the Demucs separation
pass would then write its ``piano``-routed-to-``keys`` separated output
on top of that file. Demucs's piano head can mis-classify gospel/keys
content -- routing parts of it into ``other``/``guitar`` and leaving the
canonical ``keys.wav`` partially empty -- so the high-quality user input
was effectively destroyed by a noisier separated version.

These tests pin the new contract: ``_copy_cached_stems`` keeps any file
already present at a path whose role is listed in ``protected_roles``,
and reports that existing path back so the caller's manifest stays
consistent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aural_ingest import cli as cli_mod


def _write_marker(p: Path, marker: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(marker, encoding="utf-8")


def test_protected_role_is_not_overwritten_by_cached_stem(tmp_path: Path) -> None:
    """User-supplied keys.wav already on disk should survive the cached copy."""
    cache_dir = tmp_path / "cache"
    stems_dir = tmp_path / "stems"
    _write_marker(cache_dir / "keys.wav", "demucs-separated content")
    _write_marker(cache_dir / "bass.wav", "demucs-separated bass")
    _write_marker(stems_dir / "keys.wav", "user-supplied Suno Keyboard.wav")

    out = cli_mod._copy_cached_stems(
        cache_dir,
        stems_dir,
        {"keys": "keys.wav", "bass": "bass.wav"},
        protected_roles=["keys"],
    )

    # keys.wav still has the user's marker, NOT the demucs one.
    assert (stems_dir / "keys.wav").read_text(encoding="utf-8") == \
        "user-supplied Suno Keyboard.wav"
    # bass.wav was unprotected -- demucs's separated bass landed there.
    assert (stems_dir / "bass.wav").read_text(encoding="utf-8") == \
        "demucs-separated bass"
    # The returned mapping reports BOTH roles so the caller's manifest can
    # still record the canonical paths for both stems.
    assert out == {
        "keys": "audio/stems/keys.wav",
        "bass": "audio/stems/bass.wav",
    }


def test_protected_role_with_no_existing_file_falls_back_to_cache(
    tmp_path: Path,
) -> None:
    """Protection is best-effort -- if the user-supplied file is missing,
    the cache copy still runs so the role isn't silently dropped."""
    cache_dir = tmp_path / "cache"
    stems_dir = tmp_path / "stems"
    _write_marker(cache_dir / "keys.wav", "demucs-separated content")
    stems_dir.mkdir()
    # NOTE: no pre-existing stems_dir/keys.wav.

    out = cli_mod._copy_cached_stems(
        cache_dir,
        stems_dir,
        {"keys": "keys.wav"},
        protected_roles=["keys"],
    )

    # No user file to protect, so demucs's separated copy fills the slot.
    assert (stems_dir / "keys.wav").read_text(encoding="utf-8") == \
        "demucs-separated content"
    assert out == {"keys": "audio/stems/keys.wav"}


def test_no_protected_roles_means_every_role_copied(tmp_path: Path) -> None:
    """Default behaviour (no protected_roles arg) matches the pre-fix flow."""
    cache_dir = tmp_path / "cache"
    stems_dir = tmp_path / "stems"
    _write_marker(cache_dir / "keys.wav", "demucs-separated keys")
    _write_marker(stems_dir / "keys.wav", "user-supplied keys would be lost")

    out = cli_mod._copy_cached_stems(
        cache_dir,
        stems_dir,
        {"keys": "keys.wav"},
    )

    # No protection -> demucs wins. This documents the pre-fix behaviour
    # so callers that intentionally pass no protected_roles get a stable
    # contract.
    assert (stems_dir / "keys.wav").read_text(encoding="utf-8") == \
        "demucs-separated keys"
    assert out == {"keys": "audio/stems/keys.wav"}


def test_protected_role_normalization_is_case_insensitive(tmp_path: Path) -> None:
    """``protected_roles`` matching shouldn't care about case or whitespace."""
    cache_dir = tmp_path / "cache"
    stems_dir = tmp_path / "stems"
    _write_marker(cache_dir / "keys.wav", "demucs")
    _write_marker(stems_dir / "keys.wav", "user input")

    out = cli_mod._copy_cached_stems(
        cache_dir,
        stems_dir,
        {"keys": "keys.wav"},
        # Note the upper-case + leading whitespace -- both should still
        # match the "keys" stem.
        protected_roles=["  KEYS  "],
    )

    assert (stems_dir / "keys.wav").read_text(encoding="utf-8") == "user input"
    assert out == {"keys": "audio/stems/keys.wav"}
