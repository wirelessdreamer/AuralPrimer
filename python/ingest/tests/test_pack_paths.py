"""Tests for aural_ingest.pack_paths (pure, yaml-only — no heavy deps).

Covers CONTRACT C2 (feature-dir routing incl. .sloppak), manifest-driven stem
resolution for BOTH the feedpak (audio/stems/*.wav) and sloppak
(stems/*.ogg) layouts, glob fallback for legacy packs, mix resolution via the
default-flagged stem, and update_manifest_keys order + unknown-key preservation
(asserted on the raw re-read text).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aural_ingest.pack_paths import (
    load_pack_manifest,
    pack_feature_dirname,
    resolve_drum_tab_path,
    resolve_mix_path,
    resolve_stem_paths,
    update_manifest_keys,
)

FIXTURE_SLOPPAK = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "sloppak"
    / "fixtures"
    / "minimal.sloppak"
)


# --- CONTRACT C2: feature-dir routing --------------------------------------


def test_pack_feature_dirname_routes_sloppak_and_feedpak_to_aural(tmp_path: Path):
    assert pack_feature_dirname(tmp_path / "song.sloppak") == "aural"
    assert pack_feature_dirname(tmp_path / "song.feedpak") == "aural"
    assert pack_feature_dirname(tmp_path / "song.auralsong") == "features"
    assert pack_feature_dirname(tmp_path / "song") == "features"
    # Accepts a str too.
    assert pack_feature_dirname(str(tmp_path / "x.sloppak")) == "aural"


# --- manifest loading -------------------------------------------------------


def test_load_pack_manifest_reads_fixture():
    mf = load_pack_manifest(FIXTURE_SLOPPAK)
    assert mf is not None
    assert mf["slopsmith_version"] == "0.9.0"
    assert mf["title"] == "Minimal Sloppak"
    assert isinstance(mf["stems"], list)


def test_load_pack_manifest_missing_returns_none(tmp_path: Path):
    assert load_pack_manifest(tmp_path) is None


def test_load_pack_manifest_strips_bom(tmp_path: Path):
    (tmp_path / "manifest.yaml").write_bytes(
        "﻿".encode("utf-8") + b"title: X\nartist: Y\n"
    )
    mf = load_pack_manifest(tmp_path)
    assert mf is not None
    assert mf["title"] == "X"


# --- stem resolution: sloppak layout (manifest, stems/*.ogg) ---------------


def test_resolve_stem_paths_sloppak_manifest():
    stems = resolve_stem_paths(FIXTURE_SLOPPAK)
    # All four fixture stems exist and are manifest-listed under stems/*.ogg.
    assert set(stems) == {"guitar", "bass", "drums", "full"}
    assert stems["guitar"].name == "guitar.ogg"
    assert stems["guitar"].parent.name == "stems"
    assert stems["drums"].is_file()


def test_resolve_mix_path_sloppak_default_flag():
    mix = resolve_mix_path(FIXTURE_SLOPPAK)
    assert mix is not None
    # full.ogg carries default: true.
    assert mix.name == "full.ogg"


# --- stem resolution: feedpak layout (manifest, audio/stems/*.wav) ---------


def _make_feedpak(tmp_path: Path) -> Path:
    pack = tmp_path / "song.feedpak"
    (pack / "audio" / "stems").mkdir(parents=True)
    for role in ("bass", "drums", "guitar"):
        (pack / "audio" / "stems" / f"{role}.wav").write_bytes(b"RIFF")
    (pack / "audio" / "stems" / "full.wav").write_bytes(b"RIFF")
    manifest = (
        "feedpak_version: 1.11.0\n"
        "title: FP\n"
        "artist: A\n"
        "duration: 4.0\n"
        "arrangements: []\n"
        "stems:\n"
        "- id: bass\n  file: audio/stems/bass.wav\n"
        "- id: drums\n  file: audio/stems/drums.wav\n"
        "- id: guitar\n  file: audio/stems/guitar.wav\n"
        "- id: full\n  file: audio/stems/full.wav\n  default: true\n"
    )
    (pack / "manifest.yaml").write_text(manifest, encoding="utf-8")
    return pack


def test_resolve_stem_paths_feedpak_manifest(tmp_path: Path):
    pack = _make_feedpak(tmp_path)
    stems = resolve_stem_paths(pack)
    assert set(stems) == {"bass", "drums", "guitar", "full"}
    assert stems["bass"].name == "bass.wav"
    assert stems["bass"].parent.name == "stems"


def test_resolve_mix_path_feedpak_default_flag(tmp_path: Path):
    pack = _make_feedpak(tmp_path)
    mix = resolve_mix_path(pack)
    assert mix is not None
    assert mix.name == "full.wav"


def test_resolve_drum_tab_path_honors_safe_manifest_pointer(tmp_path: Path):
    pack = _make_feedpak(tmp_path)
    (pack / "custom").mkdir()
    (pack / "manifest.yaml").write_text(
        (pack / "manifest.yaml").read_text(encoding="utf-8")
        + "drum_tab: custom/drums.json\n",
        encoding="utf-8",
    )

    assert resolve_drum_tab_path(pack) == pack / "custom" / "drums.json"


def test_resolve_drum_tab_path_falls_back_for_missing_or_unsafe_manifest_pointer(
    tmp_path: Path,
):
    pack = _make_feedpak(tmp_path)
    assert resolve_drum_tab_path(pack) == pack / "drum_tab.json"

    (pack / "manifest.yaml").write_text(
        (pack / "manifest.yaml").read_text(encoding="utf-8")
        + "drum_tab: custom//drums.json\n",
        encoding="utf-8",
    )
    assert resolve_drum_tab_path(pack) == pack / "drum_tab.json"


# --- glob fallback (legacy pack, no manifest) ------------------------------


def test_resolve_stem_paths_glob_fallback_no_manifest(tmp_path: Path):
    pack = tmp_path / "legacy.auralsong"
    (pack / "audio" / "stems").mkdir(parents=True)
    (pack / "audio" / "stems" / "bass.wav").write_bytes(b"RIFF")
    (pack / "audio" / "stems" / "drums.wav").write_bytes(b"RIFF")
    stems = resolve_stem_paths(pack)
    assert set(stems) == {"bass", "drums"}


def test_resolve_mix_path_legacy_audio_mix(tmp_path: Path):
    pack = tmp_path / "legacy.auralsong"
    (pack / "audio").mkdir(parents=True)
    (pack / "audio" / "mix.wav").write_bytes(b"RIFF")
    mix = resolve_mix_path(pack)
    assert mix is not None
    assert mix.name == "mix.wav"


# --- update_manifest_keys: order + unknown-key preservation ----------------


def test_update_manifest_keys_preserves_order_and_unknown_key(tmp_path: Path):
    pack = tmp_path / "song.sloppak"
    pack.mkdir()
    # slopsmith_version is an UNKNOWN key that must survive AND stay first.
    original = (
        'slopsmith_version: "0.9.0"\n'
        "title: T\n"
        "artist: A\n"
        "duration: 4.0\n"
        "stems:\n"
        "- id: full\n  file: stems/full.ogg\n  default: true\n"
    )
    (pack / "manifest.yaml").write_text(original, encoding="utf-8")

    update_manifest_keys(
        pack,
        {"aural_notes_mid": "aural/notes.mid", "song_timeline": "song_timeline.json"},
    )

    raw = (pack / "manifest.yaml").read_text(encoding="utf-8")

    # Unknown key survived and remains first.
    assert "slopsmith_version" in raw
    lines = [ln for ln in raw.splitlines() if ln and not ln.startswith((" ", "-"))]
    top_keys = [ln.split(":", 1)[0] for ln in lines]
    assert top_keys[0] == "slopsmith_version"
    # New keys appended AFTER the originals, in insertion order.
    assert top_keys.index("title") < top_keys.index("aural_notes_mid")
    assert top_keys.index("aural_notes_mid") < top_keys.index("song_timeline")
    # Re-parse to confirm the new values are present + old ones intact.
    mf = load_pack_manifest(pack)
    assert mf["slopsmith_version"] == "0.9.0"
    assert mf["aural_notes_mid"] == "aural/notes.mid"
    assert mf["song_timeline"] == "song_timeline.json"
    assert mf["stems"][0]["id"] == "full"


def test_update_manifest_keys_overwrites_existing_in_place(tmp_path: Path):
    pack = tmp_path / "song.sloppak"
    pack.mkdir()
    (pack / "manifest.yaml").write_text(
        "title: T\nsong_timeline: old.json\nartist: A\n", encoding="utf-8"
    )
    update_manifest_keys(pack, {"song_timeline": "song_timeline.json"})
    mf = load_pack_manifest(pack)
    assert mf["song_timeline"] == "song_timeline.json"
    raw = (pack / "manifest.yaml").read_text(encoding="utf-8")
    lines = [ln.split(":", 1)[0] for ln in raw.splitlines() if ln and not ln[0].isspace()]
    # Overwrite in place — song_timeline keeps its original position (2nd).
    assert lines == ["title", "song_timeline", "artist"]


def test_update_manifest_keys_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        update_manifest_keys(tmp_path, {"x": "y"})
