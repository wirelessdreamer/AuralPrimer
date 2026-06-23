"""Tests for the feedpak WRITER (``aural_ingest.feedpak_writer``).

Writes the real ``psalm5.auralsong`` pack to a temp ``.feedpak`` and asserts:
  * ``manifest.yaml`` validates against the vendored manifest schema,
  * each notation + ``song_timeline.json`` validate against their schemas,
  * every manifest pointer (relpath) resolves to a real file,
  * notation round-trips the ``notes.mid`` pitches + onsets with no loss.
"""

from __future__ import annotations

import json
import struct
import wave
from pathlib import Path

import pretty_midi
import pytest
import yaml

from aural_ingest import feedpak_validate, feedpak_writer

# The real, fully-populated test pack ships in the portable, outside this
# worktree. Skip gracefully if it isn't on disk.
_PSALM5 = Path("D:/AuralPrimer/AuralPrimerPortable/data/songs/psalm5.auralsong")


def _is_relpath(value: str) -> bool:
    return (
        not value.startswith("/")
        and "//" not in value
        and ":" not in value
        and "\\" not in value
        and ".." not in value.split("/")
    )


@pytest.fixture(scope="module")
def written(tmp_path_factory: pytest.TempPathFactory) -> dict:
    if not _PSALM5.exists():
        pytest.skip(f"test pack not present: {_PSALM5}")
    out = tmp_path_factory.mktemp("feedpak_out")
    return feedpak_writer.write_feedpak(_PSALM5, out)


def test_manifest_validates(written: dict) -> None:
    feedpak_dir: Path = written["feedpak_dir"]
    manifest = yaml.safe_load((feedpak_dir / "manifest.yaml").read_text("utf-8"))
    feedpak_validate.validate(manifest, "manifest.schema.json")
    assert manifest["feedpak_version"] == "1.11.0"
    assert manifest["arrangements"]
    assert manifest["stems"]


def test_notation_validates(written: dict) -> None:
    feedpak_dir: Path = written["feedpak_dir"]
    assert written["notation_files"]
    for rel in written["notation_files"]:
        notation = yaml.safe_load((feedpak_dir / rel).read_text("utf-8"))
        errors = feedpak_validate.iter_errors(notation, "notation.schema.json")
        assert not errors, f"{rel}: {errors}"


def test_song_timeline_validates(written: dict) -> None:
    feedpak_dir: Path = written["feedpak_dir"]
    timeline = yaml.safe_load((feedpak_dir / "song_timeline.json").read_text("utf-8"))
    errors = feedpak_validate.iter_errors(timeline, "song-timeline.schema.json")
    assert not errors, errors


def test_all_pointers_resolve(written: dict) -> None:
    """Every relpath in the manifest must resolve to a real file/dir."""
    feedpak_dir: Path = written["feedpak_dir"]
    manifest = written["manifest"]

    pointers: list[str] = []
    for arr in manifest["arrangements"]:
        if "notation" in arr:
            pointers.append(arr["notation"])
        if "file" in arr:
            pointers.append(arr["file"])
    for stem in manifest["stems"]:
        pointers.append(stem["file"])
    for key in ("song_timeline", "drum_tab", "aural_notes_mid", "aural_spectrogram",
                "aural_benchmark"):
        if isinstance(manifest.get(key), str):
            pointers.append(manifest[key])
    if isinstance(manifest.get("aural_refine_candidates"), dict):
        pointers.extend(manifest["aural_refine_candidates"].values())

    assert pointers
    for ptr in pointers:
        assert _is_relpath(ptr), f"not a POSIX relpath: {ptr}"
        assert (feedpak_dir / ptr).exists(), f"pointer does not resolve: {ptr}"


def test_notation_roundtrips_pitches_and_onsets(written: dict) -> None:
    """Notation must preserve every notes.mid pitch + onset (no loss)."""
    feedpak_dir: Path = written["feedpak_dir"]

    pm = pretty_midi.PrettyMIDI(str(_PSALM5 / "features" / "notes.mid"))

    for rel in written["notation_files"]:
        role = Path(rel).stem.replace("notation_", "")
        inst = next(
            (
                ins
                for ins in pm.instruments
                if not ins.is_drum
                and feedpak_writer._role_matches_instrument(role, ins.name)
            ),
            None,
        )
        assert inst is not None, f"no MIDI instrument matched role {role}"

        expected = sorted(
            (round(float(n.start), 4), int(n.pitch)) for n in inst.notes
        )

        notation = yaml.safe_load((feedpak_dir / rel).read_text("utf-8"))
        got: list[tuple[float, int]] = []
        for measure in notation["measures"]:
            for staff in measure.get("staves", {}).values():
                for voice in staff.get("voices", []):
                    for beat in voice.get("beats", []):
                        for note in beat.get("notes", []):
                            got.append((round(float(beat["t"]), 4), int(note["midi"])))
        got.sort()

        assert got == expected, (
            f"{role}: notation lost/changed notes "
            f"(expected {len(expected)}, got {len(got)})"
        )


# --- placeholder-arrangement fallback (no melodic notes / no stems) ---------


def _write_clicktrack_wav(path: Path, *, sr: int, duration_sec: float, bpm: float) -> None:
    period = int(round((60.0 / bpm) * sr))
    total = int(round(duration_sec * sr))
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sr)
        for i in range(total):
            wav_file.writeframesraw(struct.pack("<h", 30000 if i % period == 0 else 0))


def _validate_feedpak_dir(feedpak_dir: Path) -> dict:
    """Validate a feedpak directory against the vendored schemas + resolve
    every manifest pointer. Returns the parsed manifest."""
    manifest = yaml.safe_load((feedpak_dir / "manifest.yaml").read_text("utf-8"))
    feedpak_validate.validate(manifest, "manifest.schema.json")
    assert manifest["arrangements"], "feedpak must carry >=1 arrangement"
    assert manifest["stems"], "feedpak must carry >=1 stem"

    pointers: list[str] = []
    for arr in manifest["arrangements"]:
        for key in ("notation", "file"):
            if key in arr:
                pointers.append(arr[key])
    for stem in manifest["stems"]:
        pointers.append(stem["file"])
    if isinstance(manifest.get("song_timeline"), str):
        pointers.append(manifest["song_timeline"])
    for ptr in pointers:
        assert (feedpak_dir / ptr).exists(), f"pointer does not resolve: {ptr}"

    # Every notation document validates against the notation schema.
    for arr in manifest["arrangements"]:
        if "notation" in arr:
            notation = json.loads((feedpak_dir / arr["notation"]).read_text("utf-8"))
            errors = feedpak_validate.iter_errors(notation, "notation.schema.json")
            assert not errors, f"{arr['notation']}: {errors}"
    return manifest


def test_placeholder_arrangement_for_no_melodic_notes(tmp_path: Path) -> None:
    """A pack with no derivable melodic notes (and no stems) must still produce
    a schema-valid feedpak via the placeholder-arrangement + mix-stem fallback."""
    auralsong = tmp_path / "Sine.auralsong"
    (auralsong / "audio").mkdir(parents=True, exist_ok=True)
    (auralsong / "features").mkdir(parents=True, exist_ok=True)

    _write_clicktrack_wav(auralsong / "audio" / "mix.wav", sr=48_000, duration_sec=2.0, bpm=120.0)

    # notes.mid with only a non-melodic marker track (no matching instrument).
    pm = pretty_midi.PrettyMIDI()
    marker = pretty_midi.Instrument(program=0, name="Structure")
    pm.instruments.append(marker)
    pm.write(str(auralsong / "features" / "notes.mid"))

    manifest = {
        "schema_version": "1.0.0",
        "title": "Sine Demo",
        "artist": "",
        "duration_sec": 2.0,
        "assets": {
            "audio": {"mix_path": "audio/mix.wav", "stems": {}},
            "midi": {"notes_path": "features/notes.mid"},
            "features": {"tempo_map_path": "features/tempo_map.json"},
        },
    }
    (auralsong / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (auralsong / "features" / "tempo_map.json").write_text(
        json.dumps(
            {"segments": [{"bpm": 120.0, "t0": 0.0, "t1": 2.0, "time_signature": "4/4"}]}
        ),
        encoding="utf-8",
    )

    summary = feedpak_writer.write_feedpak(auralsong, tmp_path / "out")
    feedpak_dir = Path(summary["feedpak_dir"])

    fp_manifest = _validate_feedpak_dir(feedpak_dir)
    # Placeholder arrangement: one treble staff, a single empty measure.
    assert summary["arrangements"] == ["keys"]
    notation = json.loads((feedpak_dir / "arrangements/notation_keys.json").read_text("utf-8"))
    assert notation["staves"] == [{"id": "treble", "clef": "G2"}]
    assert len(notation["measures"]) == 1
    assert "staves" not in notation["measures"][0]
    # Mix-stem fallback (no separated stems present).
    assert [s["id"] for s in fp_manifest["stems"]] == ["mix"]


def test_import_emits_valid_feedpak_at_out(tmp_path: Path) -> None:
    """End-to-end: cmd_import converts its working layout in place to a
    schema-valid .feedpak at the requested --out path."""
    from aural_ingest import cli

    src = tmp_path / "src.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=4.0, bpm=120.0)
    out = tmp_path / "Imported.feedpak"

    args = type("Args", (), {})()
    args.input_audio_path = str(src)
    args.out = str(out)
    args.profile = "full"
    args.config = json.dumps(
        {"ingest_timestamp": "2000-01-01T00:00:00Z", "bpm_hint": 120}
    )
    args.title = "Imported"
    args.artist = ""
    args.duration_sec = None
    args.drum_filter = "combined_filter"
    args.melodic_method = "auto"
    args.beat_analysis_mode = "standard"
    args.stem_separation_provider = "none"
    args.stem_separation_provider_path = None
    args.shifts = 1
    args.multi_filter = False

    assert cli.IMPORT_EMIT_FEEDPAK is True
    assert cli.cmd_import(args) == 0

    # The durable artifact at --out is a feedpak (manifest.yaml), not auralsong.
    assert out.is_dir()
    assert (out / "manifest.yaml").is_file()
    assert not (out / "manifest.json").exists()
    _validate_feedpak_dir(out)
