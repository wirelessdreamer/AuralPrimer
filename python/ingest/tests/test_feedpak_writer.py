"""Tests for the feedpak WRITER (``aural_ingest.feedpak_writer``).

Writes the real ``psalm5.auralsong`` pack to a temp ``.feedpak`` and asserts:
  * ``manifest.yaml`` validates against the vendored manifest schema,
  * notation, ``song_timeline.json``, and ``drum_tab.json`` validate against
    their schemas,
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


def test_drum_tab_validates(written: dict) -> None:
    feedpak_dir: Path = written["feedpak_dir"]
    manifest = written["manifest"]
    rel = manifest.get("drum_tab")
    assert isinstance(rel, str)
    drum_tab = json.loads((feedpak_dir / rel).read_text("utf-8"))
    errors = feedpak_validate.iter_errors(drum_tab, "drum-tab.schema.json")
    assert not errors, f"{rel}: {errors}"


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
    for key in (
        "song_timeline",
        "drum_tab",
        "keys",
        "harmony",
        "aural_notes_mid",
        "aural_spectrogram",
        "aural_benchmark",
    ):
        if isinstance(manifest.get(key), str):
            pointers.append(manifest[key])
    if isinstance(manifest.get("aural_refine_candidates"), dict):
        pointers.extend(manifest["aural_refine_candidates"].values())
    if isinstance(manifest.get("aural_fingering"), dict):
        pointers.extend(manifest["aural_fingering"].values())

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


def _write_c_major_midi(path: Path) -> None:
    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    inst = pretty_midi.Instrument(program=0, name="Keys")
    for pitches, start, end in (
        ((60, 64, 67), 0.0, 2.0),
        ((65, 69, 72), 2.0, 4.0),
        ((67, 71, 74, 77), 4.0, 6.0),
    ):
        for pitch in pitches:
            inst.notes.append(pretty_midi.Note(velocity=100, pitch=pitch, start=start, end=end))
    pm.instruments.append(inst)
    pm.write(str(path))


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
    for key in (
        "keys",
        "harmony",
        "drum_tab",
        "vocal_pitch",
        "vocal_pitch_contour",
        "aural_notes_mid",
        "aural_spectrogram",
        "aural_benchmark",
    ):
        if isinstance(manifest.get(key), str):
            pointers.append(manifest[key])
    for key in ("aural_refine_candidates", "aural_fingering"):
        if isinstance(manifest.get(key), dict):
            pointers.extend(manifest[key].values())
    for ptr in pointers:
        assert (feedpak_dir / ptr).exists(), f"pointer does not resolve: {ptr}"

    # Every notation document validates against the notation schema.
    for arr in manifest["arrangements"]:
        if "notation" in arr:
            notation = json.loads((feedpak_dir / arr["notation"]).read_text("utf-8"))
            errors = feedpak_validate.iter_errors(notation, "notation.schema.json")
            assert not errors, f"{arr['notation']}: {errors}"
        if "file" in arr:
            arrangement = json.loads((feedpak_dir / arr["file"]).read_text("utf-8"))
            errors = feedpak_validate.iter_errors(arrangement, "arrangement.schema.json")
            assert not errors, f"{arr['file']}: {errors}"
    if isinstance(manifest.get("vocal_pitch"), str):
        vocal_pitch = json.loads((feedpak_dir / manifest["vocal_pitch"]).read_text("utf-8"))
        errors = feedpak_validate.iter_errors(vocal_pitch, "vocal-pitch.schema.json")
        assert not errors, f"{manifest['vocal_pitch']}: {errors}"
    if isinstance(manifest.get("vocal_pitch_contour"), str):
        contour = json.loads((feedpak_dir / manifest["vocal_pitch_contour"]).read_text("utf-8"))
        errors = feedpak_validate.iter_errors(contour, "vocal-pitch-contour.schema.json")
        assert not errors, f"{manifest['vocal_pitch_contour']}: {errors}"
    if isinstance(manifest.get("drum_tab"), str):
        drum_tab = json.loads((feedpak_dir / manifest["drum_tab"]).read_text("utf-8"))
        errors = feedpak_validate.iter_errors(drum_tab, "drum-tab.schema.json")
        assert not errors, f"{manifest['drum_tab']}: {errors}"
    if isinstance(manifest.get("aural_fingering"), dict):
        for role, rel_path in manifest["aural_fingering"].items():
            fingering = json.loads((feedpak_dir / rel_path).read_text("utf-8"))
            errors = feedpak_validate.iter_errors(fingering, "aural-fingering.schema.json")
            assert not errors, f"{role} {rel_path}: {errors}"
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
    assert "keys" not in fp_manifest
    assert "harmony" not in fp_manifest


def test_write_feedpak_clears_existing_output_dir(tmp_path: Path) -> None:
    """Rewriting the same feedpak target must not preserve stale sidecars."""
    auralsong = tmp_path / "Sine.auralsong"
    (auralsong / "audio").mkdir(parents=True, exist_ok=True)
    (auralsong / "features").mkdir(parents=True, exist_ok=True)

    _write_clicktrack_wav(auralsong / "audio" / "mix.wav", sr=48_000, duration_sec=2.0, bpm=120.0)

    pm = pretty_midi.PrettyMIDI()
    pm.instruments.append(pretty_midi.Instrument(program=0, name="Structure"))
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
        json.dumps({"segments": [{"bpm": 120.0, "t0": 0.0, "t1": 2.0, "time_signature": "4/4"}]}),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    stale_dir = out_dir / "Sine.feedpak"
    (stale_dir / "aural").mkdir(parents=True)
    (stale_dir / "drum_tab.json").write_text('{"stale": true}', encoding="utf-8")
    (stale_dir / "aural" / "fingering.lead_guitar.json").write_text('{"stale": true}', encoding="utf-8")

    summary = feedpak_writer.write_feedpak(auralsong, out_dir)
    feedpak_dir = Path(summary["feedpak_dir"])
    fp_manifest = yaml.safe_load((feedpak_dir / "manifest.yaml").read_text("utf-8"))

    assert not (feedpak_dir / "drum_tab.json").exists()
    assert not (feedpak_dir / "aural" / "fingering.lead_guitar.json").exists()
    assert "drum_tab" not in fp_manifest
    assert "aural_fingering" not in fp_manifest


def test_key_and_harmony_docs_for_melodic_notes(tmp_path: Path) -> None:
    """A deterministic notes-only key pass fills the feedpak key/harmony slots."""
    auralsong = tmp_path / "Keyed.auralsong"
    (auralsong / "audio").mkdir(parents=True, exist_ok=True)
    (auralsong / "features").mkdir(parents=True, exist_ok=True)

    _write_clicktrack_wav(auralsong / "audio" / "mix.wav", sr=48_000, duration_sec=6.0, bpm=120.0)
    _write_c_major_midi(auralsong / "features" / "notes.mid")

    manifest = {
        "schema_version": "1.0.0",
        "title": "Keyed Demo",
        "artist": "",
        "duration_sec": 6.0,
        "assets": {
            "audio": {"mix_path": "audio/mix.wav", "stems": {}},
            "midi": {"notes_path": "features/notes.mid"},
            "features": {"tempo_map_path": "features/tempo_map.json"},
        },
    }
    (auralsong / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (auralsong / "features" / "tempo_map.json").write_text(
        json.dumps(
            {"segments": [{"bpm": 120.0, "t0": 0.0, "t1": 6.0, "time_signature": "4/4"}]}
        ),
        encoding="utf-8",
    )

    summary = feedpak_writer.write_feedpak(auralsong, tmp_path / "out")
    feedpak_dir = Path(summary["feedpak_dir"])
    fp_manifest = _validate_feedpak_dir(feedpak_dir)

    assert fp_manifest["keys"] == "keys.json"
    assert fp_manifest["harmony"] == "harmony.json"
    assert fp_manifest["key"] == "C"
    assert fp_manifest["mode"] == "major"

    keys_doc = json.loads((feedpak_dir / "keys.json").read_text("utf-8"))
    harmony_doc = json.loads((feedpak_dir / "harmony.json").read_text("utf-8"))
    assert not feedpak_validate.iter_errors(keys_doc, "keys.schema.json")
    assert not feedpak_validate.iter_errors(harmony_doc, "harmony.schema.json")
    assert len(keys_doc["events"]) == 1
    key_event = keys_doc["events"][0]
    assert key_event["t"] == 0.0
    assert key_event["key"] == "C"
    assert key_event["scale"] == "major"
    assert 0.52 <= key_event["confidence"] <= 0.99
    assert harmony_doc["key"] == "C"
    assert harmony_doc["mode"] == "major"
    assert harmony_doc["chord_method"] == "measure_note_profile_chords_v1"
    assert [(event["t"], event["root"], event["quality"], event["bass"]) for event in harmony_doc["events"]] == [
        (0.0, "C", "maj", "C"),
        (2.0, "F", "maj", "F"),
        (4.0, "G", "7", "G"),
    ]
    assert harmony_doc["events"][0]["rn"] == "I"
    assert harmony_doc["events"][1]["rn"] == "IV"
    assert harmony_doc["events"][2]["rn"] == "V7"


def test_vocal_pitch_docs_for_vocals_track(tmp_path: Path) -> None:
    auralsong = tmp_path / "Vocals.auralsong"
    (auralsong / "audio").mkdir(parents=True, exist_ok=True)
    (auralsong / "audio" / "stems").mkdir(parents=True, exist_ok=True)
    (auralsong / "features").mkdir(parents=True, exist_ok=True)

    _write_clicktrack_wav(auralsong / "audio" / "mix.wav", sr=48_000, duration_sec=2.0, bpm=120.0)
    _write_clicktrack_wav(
        auralsong / "audio" / "stems" / "vocals.wav",
        sr=48_000,
        duration_sec=2.0,
        bpm=120.0,
    )

    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    vocals = pretty_midi.Instrument(program=54, name="Vocals")
    vocals.notes.append(pretty_midi.Note(velocity=96, pitch=69, start=0.25, end=0.75))
    vocals.notes.append(pretty_midi.Note(velocity=88, pitch=71, start=1.0, end=1.4))
    pm.instruments.append(vocals)
    pm.write(str(auralsong / "features" / "notes.mid"))

    manifest = {
        "schema_version": "1.0.0",
        "title": "Vocals Demo",
        "artist": "",
        "duration_sec": 2.0,
        "assets": {
            "audio": {
                "mix_path": "audio/mix.wav",
                "stems": {"vocals_path": "audio/stems/vocals.wav"},
            },
            "midi": {"notes_path": "features/notes.mid"},
            "features": {"tempo_map_path": "features/tempo_map.json"},
        },
        "pipeline": {
            "transcription": {
                "instrument_melodic_methods_used": {"vocals": "melodic_rmvpe"},
            }
        },
    }
    (auralsong / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (auralsong / "features" / "tempo_map.json").write_text(
        json.dumps({"segments": [{"bpm": 120.0, "t0": 0.0, "t1": 2.0, "time_signature": "4/4"}]}),
        encoding="utf-8",
    )
    contour = {
        "version": 1,
        "samples": [
            {"t": 0.25, "hz": 440.0},
            {"t": 0.27, "hz": 441.0},
        ],
    }
    (auralsong / "features" / "vocal_pitch_contour.json").write_text(
        json.dumps(contour), encoding="utf-8"
    )

    summary = feedpak_writer.write_feedpak(auralsong, tmp_path / "out")
    feedpak_dir = Path(summary["feedpak_dir"])
    fp_manifest = _validate_feedpak_dir(feedpak_dir)

    assert fp_manifest["vocal_pitch"] == "vocal_pitch.json"
    assert fp_manifest["vocal_pitch_contour"] == "vocal_pitch_contour.json"
    assert fp_manifest["pitch_extraction"] == {
        "engine": "aural_ingest",
        "model": "melodic_rmvpe",
        "version": "1.0.0",
    }
    vocal_pitch = json.loads((feedpak_dir / "vocal_pitch.json").read_text("utf-8"))
    assert vocal_pitch == {
        "version": 1,
        "notes": [
            {"t": 0.25, "d": 0.5, "midi": 69},
            {"t": 1.0, "d": 0.4, "midi": 71},
        ],
    }
    copied_contour = json.loads((feedpak_dir / "vocal_pitch_contour.json").read_text("utf-8"))
    assert copied_contour == contour


def test_fingering_sidecars_copy_to_feedpak_aural_extension(tmp_path: Path) -> None:
    auralsong = tmp_path / "Fingered.auralsong"
    (auralsong / "audio").mkdir(parents=True, exist_ok=True)
    (auralsong / "audio" / "stems").mkdir(parents=True, exist_ok=True)
    (auralsong / "features").mkdir(parents=True, exist_ok=True)

    _write_clicktrack_wav(auralsong / "audio" / "mix.wav", sr=48_000, duration_sec=1.0, bpm=120.0)
    _write_clicktrack_wav(
        auralsong / "audio" / "stems" / "lead_guitar.wav",
        sr=48_000,
        duration_sec=1.0,
        bpm=120.0,
    )

    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    lead = pretty_midi.Instrument(program=29, name="Lead Guitar")
    lead.notes.append(pretty_midi.Note(velocity=96, pitch=64, start=0.5, end=0.75))
    pm.instruments.append(lead)
    pm.write(str(auralsong / "features" / "notes.mid"))

    manifest = {
        "schema_version": "1.0.0",
        "title": "Fingered Demo",
        "artist": "",
        "duration_sec": 1.0,
        "assets": {
            "audio": {
                "mix_path": "audio/mix.wav",
                "stems": {"lead_guitar_path": "audio/stems/lead_guitar.wav"},
            },
            "midi": {"notes_path": "features/notes.mid"},
            "features": {
                "tempo_map_path": "features/tempo_map.json",
                "fingering_paths": {"lead_guitar": "features/fingering.lead_guitar.json"},
            },
        },
    }
    (auralsong / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (auralsong / "features" / "tempo_map.json").write_text(
        json.dumps({"segments": [{"bpm": 120.0, "t0": 0.0, "t1": 1.0, "time_signature": "4/4"}]}),
        encoding="utf-8",
    )
    fingering = {
        "version": "1.0.0",
        "instrument": "lead_guitar",
        "notes": [{"t_on": 0.5, "t_off": 0.75, "pitch": 64, "velocity": 96, "string": 1, "fret": 5}],
    }
    (auralsong / "features" / "fingering.lead_guitar.json").write_text(
        json.dumps(fingering), encoding="utf-8"
    )

    summary = feedpak_writer.write_feedpak(auralsong, tmp_path / "out")
    feedpak_dir = Path(summary["feedpak_dir"])
    fp_manifest = _validate_feedpak_dir(feedpak_dir)

    assert fp_manifest["aural_fingering"] == {"lead_guitar": "aural/fingering.lead_guitar.json"}
    lead_arr = next(arr for arr in fp_manifest["arrangements"] if arr["id"] == "lead_guitar")
    assert lead_arr["type"] == "guitar"
    assert lead_arr["notation"] == "arrangements/notation_lead_guitar.json"
    assert lead_arr["file"] == "arrangements/tab_lead_guitar.json"
    assert lead_arr["tuning"] == [40, 45, 50, 55, 59, 64]
    tab = json.loads((feedpak_dir / "arrangements/tab_lead_guitar.json").read_text("utf-8"))
    assert tab["notes"] == [{"t": 0.5, "s": 1, "f": 5, "sus": 0.25, "midi": 64, "v": 96}]
    copied = json.loads((feedpak_dir / "aural/fingering.lead_guitar.json").read_text("utf-8"))
    assert copied == fingering


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
