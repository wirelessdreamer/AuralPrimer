import json
import hashlib
import math
import struct
import wave
import zipfile
from array import array
from pathlib import Path

import pytest


def _write_clicktrack_wav(path: Path, *, sr: int, duration_sec: float, bpm: float) -> None:
    """Generate a deterministic mono PCM16 click track.

    - 1 sample impulse per beat
    - silence otherwise
    """

    n = int(sr * duration_sec)
    period_samples = int(round((60.0 / bpm) * sr))
    if period_samples <= 0:
        raise ValueError("invalid bpm")

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)

        for i in range(n):
            # impulse at each beat boundary
            if i % period_samples == 0:
                s = 30000
            else:
                s = 0
            w.writeframesraw(struct.pack("<h", s))


def _write_dual_tone_wav(path: Path, *, sr: int, duration_sec: float) -> None:
    n = int(sr * duration_sec)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)

        for i in range(n):
            t = float(i) / float(sr)
            left = (
                0.45 * math.sin(2.0 * math.pi * 220.0 * t)
                + 0.35 * math.sin(2.0 * math.pi * 1760.0 * t)
            )
            right = (
                0.42 * math.sin(2.0 * math.pi * 110.0 * t)
                + 0.38 * math.sin(2.0 * math.pi * 1320.0 * t)
            )

            left_i16 = int(max(-32768, min(32767, round(left * 32767.0))))
            right_i16 = int(max(-32768, min(32767, round(right * 32767.0))))
            w.writeframesraw(struct.pack("<hh", left_i16, right_i16))


def _read_pcm16_wav(path: Path) -> tuple[int, int, list[int]]:
    with wave.open(str(path), "rb") as w:
        channels = w.getnchannels()
        sr = w.getframerate()
        assert w.getsampwidth() == 2
        raw = w.readframes(w.getnframes())

    samples = array("h")
    samples.frombytes(raw)
    return channels, sr, list(samples)


def _extract_tempo_bpm_from_midi_bytes(midi_bytes: bytes) -> float:
    idx = midi_bytes.find(b"\xFF\x51\x03")
    if idx < 0 or idx + 6 > len(midi_bytes):
        raise AssertionError("SetTempo meta event not found in notes.mid")
    us_per_quarter = int.from_bytes(midi_bytes[idx + 3 : idx + 6], "big")
    if us_per_quarter <= 0:
        raise AssertionError("invalid tempo value in notes.mid")
    return 60_000_000.0 / float(us_per_quarter)


def _count_note_on(midi_bytes: bytes, status: int, notes: set[int] | None = None) -> int:
    out = 0
    for i in range(0, len(midi_bytes) - 2):
        if midi_bytes[i] != status:
            continue
        note = midi_bytes[i + 1]
        vel = midi_bytes[i + 2]
        if vel == 0:
            continue
        if notes is not None and note not in notes:
            continue
        out += 1
    return out


def _count_note_on_statuses(midi_bytes: bytes, statuses: set[int], notes: set[int] | None = None) -> int:
    return sum(_count_note_on(midi_bytes, status, notes) for status in statuses)


def _melodic_note_tuple(note: object) -> tuple[float, float, int, int, str]:
    return (
        round(float(getattr(note, "t_on")), 6),
        round(float(getattr(note, "t_off")), 6),
        int(getattr(note, "pitch")),
        int(getattr(note, "velocity")),
        str(getattr(note, "instrument")),
    )


def _assert_keys_auralsong_matches_multiple_ingress_verifiers(
    auralsong: Path,
    expected_notes: list[object],
) -> None:
    from aural_ingest.algorithms.piano_midi import decode_midi_notes
    from aural_ingest.piano_benchmark import evaluate_piano, parse_piano_midi_reference

    expected_tuples = [_melodic_note_tuple(note) for note in expected_notes]
    events_payload = json.loads((auralsong / "features" / "events.json").read_text("utf-8"))
    event_tuples = [
        (
            round(float(note["t_on"]), 6),
            round(float(note["t_off"]), 6),
            int(note["pitch"]),
            int(note["velocity"]),
            str(note["instrument"]),
        )
        for note in sorted(events_payload["notes"], key=lambda item: (item["t_on"], item["pitch"], item["t_off"]))
        if note["track_id"] == "keys_main"
    ]
    assert event_tuples == expected_tuples

    notes_mid = auralsong / "features" / "notes.mid"
    decoded_notes = decode_midi_notes(notes_mid, instrument="keys")
    assert [_melodic_note_tuple(note) for note in decoded_notes] == expected_tuples

    benchmark_reference = parse_piano_midi_reference(notes_mid, role="keys")
    reference_tuples = [
        (
            round(float(event.time), 6),
            round(float(event.time + event.duration), 6),
            int(event.pitch),
            int(event.velocity),
            "keys",
        )
        for event in benchmark_reference
    ]
    assert reference_tuples == expected_tuples

    secondary = evaluate_piano(
        decoded_notes,
        benchmark_reference,
        tolerance_sec=0.001,
        offset_tolerance_sec=0.001,
        velocity_tolerance=0,
    )
    assert secondary.tp == len(expected_notes)
    assert secondary.fp == 0
    assert secondary.fn == 0
    assert secondary.f1 == pytest.approx(1.0)
    assert secondary.note_with_offset_f1 == pytest.approx(1.0)
    assert secondary.note_with_offset_velocity_f1 == pytest.approx(1.0)


def _assert_validate_reports_keys_ingress_verifiers(capsys: pytest.CaptureFixture[str], expected_count: int) -> None:
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["verifiers"]["events_notes_mid"]["verifiers"] == [
        "events_json",
        "piano_midi_decoder",
        "piano_benchmark_parser",
    ]
    assert payload["verifiers"]["events_notes_mid"]["roles"]["keys"] == {
        "events_json_notes": expected_count,
        "piano_benchmark_parser_notes": expected_count,
        "piano_midi_decoder_notes": expected_count,
        "secondary_f1": 1.0,
        "secondary_offset_velocity_f1": 1.0,
    }


@pytest.fixture(autouse=True)
def _use_fast_standard_beat_default_for_import_smokes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep broad import fixtures fast; explicit high-accuracy tests still opt in."""

    from aural_ingest import cli

    monkeypatch.setattr(cli, "DEFAULT_BEAT_ANALYSIS_MODE", "standard")
    monkeypatch.setattr(cli, "DEFAULT_STEM_SEPARATION_PROVIDER", "none")
    # These tests assert the intermediate .auralsong working layout; opt out of
    # the stage-6a in-place .feedpak conversion so it stays inspectable.
    monkeypatch.setattr(cli, "IMPORT_EMIT_FEEDPAK", False)


@pytest.mark.parametrize("bpm", [90.0, 120.0])
def test_import_generates_valid_auralsong(tmp_path: Path, bpm: float) -> None:
    # Arrange
    src = tmp_path / "src.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=8.0, bpm=bpm)
    out = tmp_path / "Out.auralsong"

    # Act
    from aural_ingest.cli import cmd_import

    # Use an instance so we can refer to outer-scope vars without Python class-scope quirks.
    args = type("Args", (), {})()
    args.input_audio_path = str(src)
    args.out = str(out)
    args.profile = "full"
    args.config = json.dumps({"ingest_timestamp": "2000-01-01T00:00:00Z"})
    args.title = "Test"
    args.artist = ""
    args.duration_sec = None

    rc = cmd_import(args)
    assert rc == 0

    # Assert outputs exist
    assert (out / "manifest.json").is_file()
    assert (out / "audio/mix.wav").is_file()
    assert (out / "features/notes.mid").is_file()
    assert (out / "features/beats.json").is_file()
    assert (out / "features/tempo_map.json").is_file()
    assert (out / "features/sections.json").is_file()
    assert (out / "audio/stems/lead_guitar.wav").is_file()
    assert (out / "audio/stems/rhythm_guitar.wav").is_file()

    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    assert manifest["assets"]["audio"]["mix_path"] == "audio/mix.wav"
    assert manifest["assets"]["midi"]["notes_path"] == "features/notes.mid"
    assert manifest["assets"]["features"]["beats_path"] == "features/beats.json"
    assert manifest["assets"]["features"]["tempo_map_path"] == "features/tempo_map.json"
    assert manifest["assets"]["features"]["sections_path"] == "features/sections.json"
    assert manifest["timing"]["audio_sample_rate_hz"] == 48_000
    assert manifest["duration_sec"] == pytest.approx(8.0, abs=1e-6)
    assert manifest["source"]["ingest_timestamp"] == "2000-01-01T00:00:00Z"

    notes_mid = (out / "features/notes.mid").read_bytes()
    assert notes_mid.startswith(b"MThd")
    midi_bpm = _extract_tempo_bpm_from_midi_bytes(notes_mid)
    # Autocorrelation estimator should get close for an impulse click track.
    assert midi_bpm == pytest.approx(bpm, abs=1.0)

    # Structure track emits beat pulse notes on channel 16 (0x9F) with notes 36/37.
    structure_beat_notes = _count_note_on(notes_mid, 0x9F, {36, 37})
    assert structure_beat_notes > 0
    # Drums: channel 10 note-on status (0x99)
    assert _count_note_on(notes_mid, 0x99) > 0
    # Melodic: legacy fallback is channel 1, instrument stems use channels 1-5.
    assert _count_note_on_statuses(notes_mid, {0x90, 0x91, 0x92, 0x93, 0x94}) > 0


def test_import_guitar_split_preserves_shape_and_reconstructs_source(tmp_path: Path) -> None:
    src = tmp_path / "src_stereo.wav"
    _write_dual_tone_wav(src, sr=48_000, duration_sec=2.0)
    out = tmp_path / "Split.auralsong"

    from aural_ingest.cli import cmd_import

    args = type("Args", (), {})()
    args.input_audio_path = str(src)
    args.out = str(out)
    args.profile = "full"
    args.config = json.dumps(
        {
            "ingest_timestamp": "2000-01-01T00:00:00Z",
            "disable_stem_separation": True,
        }
    )
    args.title = None
    args.artist = None
    args.duration_sec = None

    assert cmd_import(args) == 0

    lead = out / "audio" / "stems" / "lead_guitar.wav"
    rhythm = out / "audio" / "stems" / "rhythm_guitar.wav"
    assert lead.is_file()
    assert rhythm.is_file()

    ch_src, sr_src, src_samples = _read_pcm16_wav(src)
    ch_lead, sr_lead, lead_samples = _read_pcm16_wav(lead)
    ch_rhythm, sr_rhythm, rhythm_samples = _read_pcm16_wav(rhythm)

    assert ch_src == ch_lead == ch_rhythm
    assert sr_src == sr_lead == sr_rhythm
    assert len(src_samples) == len(lead_samples) == len(rhythm_samples)

    abs_err = 0
    for src_i, lead_i, rhythm_i in zip(src_samples, lead_samples, rhythm_samples):
        abs_err += abs(int(src_i) - int(lead_i) - int(rhythm_i))
    mean_abs_err = abs_err / float(len(src_samples))
    assert mean_abs_err <= 2.0

    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    stems = manifest["assets"]["audio"]["stems"]
    assert stems["lead_guitar_path"] == "audio/stems/lead_guitar.wav"
    assert stems["rhythm_guitar_path"] == "audio/stems/rhythm_guitar.wav"
    assert stems["guitar_split_source_kind"] == "mix_fallback"
    assert manifest["pipeline"]["guitar_split"]["method"] == "spectral_energy_mask_v1"


def test_import_guitar_split_uses_configured_guitar_stem_path(tmp_path: Path) -> None:
    src_mix = tmp_path / "mix.wav"
    _write_clicktrack_wav(src_mix, sr=48_000, duration_sec=2.0, bpm=120.0)
    source_guitar = tmp_path / "guitar_source.wav"
    _write_dual_tone_wav(source_guitar, sr=48_000, duration_sec=2.0)
    out = tmp_path / "CfgSplit.auralsong"

    from aural_ingest.cli import cmd_import

    args = type("Args", (), {})()
    args.input_audio_path = str(src_mix)
    args.out = str(out)
    args.profile = "full"
    args.config = json.dumps(
        {
            "ingest_timestamp": "2000-01-01T00:00:00Z",
            "guitar_stem_path": str(source_guitar),
        }
    )
    args.title = None
    args.artist = None
    args.duration_sec = None

    assert cmd_import(args) == 0
    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    split_meta = manifest["pipeline"]["guitar_split"]
    assert split_meta["source_kind"] == "config"
    assert split_meta["sample_rate_hz"] == 48_000


def test_import_uses_explicit_drum_stem_for_drum_transcription(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src_mix = tmp_path / "mix.wav"
    _write_clicktrack_wav(src_mix, sr=48_000, duration_sec=2.0, bpm=120.0)
    drum_stem = tmp_path / "drums.wav"
    _write_clicktrack_wav(drum_stem, sr=48_000, duration_sec=2.0, bpm=90.0)
    out = tmp_path / "DrumStem.auralsong"

    from aural_ingest import cli
    from aural_ingest.transcription import (
        DrumEvent,
        DrumTranscriptionResult,
        MelodicTranscriptionResult,
    )

    seen: dict[str, Path] = {}

    def fake_transcribe_drums(
        stem_path: Path,
        requested_engine: str | None,
        algorithm_registry: dict[str, object],
        logger: object = None,
    ) -> DrumTranscriptionResult:
        seen["stem_path"] = Path(stem_path)
        return DrumTranscriptionResult(
            events=[DrumEvent(time=0.0, note=36, velocity=90)],
            used_algorithm=requested_engine or "combined_filter",
            attempted_algorithms=[requested_engine or "combined_filter"],
            warnings=[],
        )

    def fake_transcribe_melodic(*_args: object, **_kwargs: object) -> MelodicTranscriptionResult:
        return MelodicTranscriptionResult(
            notes=[],
            used_method="basic_pitch",
            attempted_methods=["basic_pitch"],
            warnings=[],
        )

    monkeypatch.setattr(cli, "transcribe_drums", fake_transcribe_drums)
    monkeypatch.setattr(cli, "transcribe_melodic", fake_transcribe_melodic)

    args = type("Args", (), {})()
    args.input_audio_path = str(src_mix)
    args.out = str(out)
    args.profile = "full"
    args.config = "{}"
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "adaptive_beat_grid"
    args.drum_stem_path = str(drum_stem)
    args.melodic_method = "auto"
    args.shifts = 1
    args.multi_filter = False

    assert cli.cmd_import(args) == 0
    assert seen["stem_path"] == drum_stem

    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    tr = manifest["pipeline"]["transcription"]
    assert tr["drum_source_kind"] == "arg"
    assert tr["drum_source_path"] == str(drum_stem)
    assert tr["drum_source_sha256"]
    assert manifest["assets"]["audio"]["stems"]["drum_transcription_source_kind"] == "arg"
    assert manifest["assets"]["audio"]["stems"]["drum_transcription_source_path"] == str(drum_stem)
    assert manifest["recognition"]["drums"]["source_kind"] == "arg"
    assert manifest["recognition"]["drums"]["source_path"] == str(drum_stem)


def test_import_reuses_configured_input_stems_for_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src_mix = tmp_path / "mix.wav"
    _write_clicktrack_wav(src_mix, sr=48_000, duration_sec=2.0, bpm=120.0)
    drum_stem = tmp_path / "drums.wav"
    bass_stem = tmp_path / "bass.wav"
    lead_stem = tmp_path / "lead.wav"
    rhythm_stem = tmp_path / "rhythm.wav"
    keys_stem = tmp_path / "keys.wav"
    vocal_stem = tmp_path / "vocals.wav"
    _write_clicktrack_wav(drum_stem, sr=48_000, duration_sec=2.0, bpm=90.0)
    _write_clicktrack_wav(bass_stem, sr=48_000, duration_sec=2.0, bpm=60.0)
    _write_dual_tone_wav(lead_stem, sr=48_000, duration_sec=2.0)
    _write_dual_tone_wav(rhythm_stem, sr=48_000, duration_sec=2.0)
    _write_dual_tone_wav(keys_stem, sr=48_000, duration_sec=2.0)
    _write_dual_tone_wav(vocal_stem, sr=48_000, duration_sec=2.0)
    out = tmp_path / "InputStems.auralsong"

    from aural_ingest import cli
    from aural_ingest.transcription import (
        DrumEvent,
        DrumTranscriptionResult,
        InstrumentTranscriptionResult,
        MelodicNote,
        MelodicTranscriptionResult,
    )

    seen: dict[str, object] = {}

    def fake_transcribe_drums(
        stem_path: Path,
        requested_engine: str | None,
        algorithm_registry: dict[str, object],
        logger: object = None,
    ) -> DrumTranscriptionResult:
        seen["drum_source"] = Path(stem_path)
        return DrumTranscriptionResult(
            events=[DrumEvent(time=0.0, note=36, velocity=90)],
            used_algorithm=requested_engine or "combined_filter",
            attempted_algorithms=[requested_engine or "combined_filter"],
            warnings=[],
        )

    def fake_transcribe_all_melodic_stems(
        stems: dict[str, Path],
        requested_method: str | None,
        logger: object = None,
    ) -> list[InstrumentTranscriptionResult]:
        seen["instrument_stems"] = {role: Path(path) for role, path in stems.items()}
        bass_note = MelodicNote(t_on=0.0, t_off=0.2, pitch=48, velocity=90, instrument="bass")
        vocal_note = MelodicNote(t_on=0.5, t_off=0.8, pitch=69, velocity=88, instrument="vocals")
        return [
            InstrumentTranscriptionResult(
                instrument="bass",
                notes=[bass_note],
                used_method=requested_method,
                attempted_methods=[requested_method or "auto"],
                warnings=[],
                stem_path=str(stems["bass"]),
            ),
            InstrumentTranscriptionResult(
                instrument="vocals",
                notes=[vocal_note],
                used_method="melodic_rmvpe",
                attempted_methods=["melodic_rmvpe"],
                warnings=[],
                stem_path=str(stems["vocals"]),
                meta={
                    "vocal_pitch_contour": {
                        "version": 1,
                        "samples": [
                            {"t": 0.5, "hz": 440.0, "confidence": 0.9},
                            {"t": 0.52, "hz": 441.0, "confidence": 0.8},
                        ],
                    },
                },
            ),
        ]

    def fake_transcribe_melodic(*_args: object, **_kwargs: object) -> MelodicTranscriptionResult:
        return MelodicTranscriptionResult(
            notes=[],
            used_method="melodic_adaptive",
            attempted_methods=["melodic_adaptive"],
            warnings=[],
        )

    monkeypatch.setattr(cli, "transcribe_drums", fake_transcribe_drums)
    monkeypatch.setattr(cli, "transcribe_all_melodic_stems", fake_transcribe_all_melodic_stems)
    monkeypatch.setattr(cli, "transcribe_melodic", fake_transcribe_melodic)

    args = type("Args", (), {})()
    args.input_audio_path = str(src_mix)
    args.out = str(out)
    args.profile = "full"
    args.config = json.dumps(
        {
            "disable_stem_separation": True,
            "input_stem_paths": {
                "drums": str(drum_stem),
                "bass": str(bass_stem),
                "lead_guitar": str(lead_stem),
                "rhythm_guitar": str(rhythm_stem),
                "keys": str(keys_stem),
                "vocals": str(vocal_stem),
            },
        }
    )
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "combined_filter"
    args.drum_stem_path = None
    args.melodic_method = "melodic_adaptive"
    args.shifts = 1
    args.multi_filter = False

    assert cli.cmd_import(args) == 0
    assert seen["drum_source"] == out / "audio" / "stems" / "drums.wav"
    assert seen["instrument_stems"] == {
        "bass": out / "audio" / "stems" / "bass.wav",
        "lead_guitar": out / "audio" / "stems" / "lead_guitar.wav",
        "rhythm_guitar": out / "audio" / "stems" / "rhythm_guitar.wav",
        "keys": out / "audio" / "stems" / "keys.wav",
        "vocals": out / "audio" / "stems" / "vocals.wav",
    }

    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    assert manifest["pipeline"]["input_stems"]["roles"] == [
        "bass",
        "drums",
        "keys",
        "lead_guitar",
        "rhythm_guitar",
        "vocals",
    ]
    assert manifest["pipeline"]["guitar_split"]["status"] == "reused"
    assert manifest["pipeline"]["guitar_split"]["source_kind"] == "provided_split"
    stems_assets = manifest["assets"]["audio"]["stems"]
    assert stems_assets["drums_path"] == "audio/stems/drums.wav"
    assert stems_assets["bass_path"] == "audio/stems/bass.wav"
    assert stems_assets["lead_guitar_path"] == "audio/stems/lead_guitar.wav"
    assert stems_assets["rhythm_guitar_path"] == "audio/stems/rhythm_guitar.wav"
    assert stems_assets["keys_path"] == "audio/stems/keys.wav"
    assert stems_assets["vocals_path"] == "audio/stems/vocals.wav"
    assert manifest["assets"]["features"]["vocal_pitch_path"] == "features/vocal_pitch.json"
    assert json.loads((out / "features" / "vocal_pitch.json").read_text("utf-8")) == {
        "version": 1,
        "notes": [{"t": 0.5, "d": 0.3, "midi": 69}],
    }
    assert manifest["assets"]["features"]["vocal_pitch_contour_path"] == "features/vocal_pitch_contour.json"
    assert json.loads((out / "features" / "vocal_pitch_contour.json").read_text("utf-8")) == {
        "version": 1,
        "samples": [
            {"t": 0.5, "hz": 440.0, "confidence": 0.9},
            {"t": 0.52, "hz": 441.0, "confidence": 0.8},
        ],
    }


def test_import_feedpak_emits_inferred_guitar_fingering_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src_mix = tmp_path / "mix.wav"
    guitar_stem = tmp_path / "lead.wav"
    _write_clicktrack_wav(src_mix, sr=48_000, duration_sec=2.0, bpm=120.0)
    _write_dual_tone_wav(guitar_stem, sr=48_000, duration_sec=2.0)
    out = tmp_path / "GuitarFallback.feedpak"

    from aural_ingest import cli
    from aural_ingest.transcription import (
        DrumTranscriptionResult,
        InstrumentTranscriptionResult,
        MelodicNote,
        MelodicTranscriptionResult,
    )

    monkeypatch.setattr(cli, "IMPORT_EMIT_FEEDPAK", True)

    def fake_transcribe_drums(*_args: object, **_kwargs: object) -> DrumTranscriptionResult:
        return DrumTranscriptionResult(
            events=[],
            used_algorithm="combined_filter",
            attempted_algorithms=["combined_filter"],
            warnings=[],
        )

    def fake_transcribe_all_melodic_stems(
        stems: dict[str, Path],
        requested_method: str | None,
        logger: object = None,
    ) -> list[InstrumentTranscriptionResult]:
        note = MelodicNote(
            t_on=0.5,
            t_off=0.8,
            pitch=64,
            velocity=96,
            instrument="lead_guitar",
        )
        return [
            InstrumentTranscriptionResult(
                instrument="lead_guitar",
                notes=[note],
                used_method=requested_method,
                attempted_methods=[requested_method or "auto"],
                warnings=[],
                stem_path=str(stems["lead_guitar"]),
            )
        ]

    def fake_transcribe_melodic(*_args: object, **_kwargs: object) -> MelodicTranscriptionResult:
        return MelodicTranscriptionResult(
            notes=[],
            used_method="melodic_adaptive",
            attempted_methods=["melodic_adaptive"],
            warnings=[],
        )

    monkeypatch.setattr(cli, "transcribe_drums", fake_transcribe_drums)
    monkeypatch.setattr(cli, "transcribe_all_melodic_stems", fake_transcribe_all_melodic_stems)
    monkeypatch.setattr(cli, "transcribe_melodic", fake_transcribe_melodic)

    args = type("Args", (), {})()
    args.input_audio_path = str(src_mix)
    args.out = str(out)
    args.profile = "full"
    args.config = json.dumps(
        {
            "disable_stem_separation": True,
            "input_stem_paths": {"lead_guitar": str(guitar_stem)},
        }
    )
    args.title = "GuitarFallback"
    args.artist = ""
    args.duration_sec = None
    args.drum_filter = "combined_filter"
    args.drum_stem_path = None
    args.melodic_method = "melodic_adaptive"
    args.beat_analysis_mode = "standard"
    args.stem_separation_provider = "none"
    args.stem_separation_provider_path = None
    args.shifts = 1
    args.multi_filter = False

    assert cli.cmd_import(args) == 0

    import yaml

    manifest = yaml.safe_load((out / "manifest.yaml").read_text("utf-8"))
    assert manifest["aural_fingering"] == {
        "lead_guitar": "aural/fingering.lead_guitar.json"
    }
    lead_arr = next(arr for arr in manifest["arrangements"] if arr["id"] == "lead_guitar")
    assert lead_arr["file"] == "arrangements/tab_lead_guitar.json"
    assert lead_arr["tuning"] == [40, 45, 50, 55, 59, 64]
    fingering = json.loads((out / "aural" / "fingering.lead_guitar.json").read_text("utf-8"))
    assert fingering["notes"] == [
        {
            "fret": 0,
            "pitch": 64,
            "string": 5,
            "t_off": 0.8,
            "t_on": 0.5,
            "velocity": 96,
        }
    ]
    tab = json.loads((out / "arrangements" / "tab_lead_guitar.json").read_text("utf-8"))
    assert tab["notes"] == [{"t": 0.5, "s": 5, "f": 0, "sus": 0.3, "midi": 64, "v": 96}]


def test_import_dir_reuses_configured_input_stems_without_existing_mix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src_dir = tmp_path / "stem_dir"
    src_dir.mkdir(parents=True, exist_ok=True)
    drum_stem = src_dir / "drums.wav"
    bass_stem = src_dir / "bass.wav"
    lead_stem = src_dir / "lead.wav"
    rhythm_stem = src_dir / "rhythm.wav"
    keys_stem = src_dir / "keys.wav"
    _write_clicktrack_wav(drum_stem, sr=48_000, duration_sec=2.0, bpm=90.0)
    _write_clicktrack_wav(bass_stem, sr=48_000, duration_sec=2.0, bpm=60.0)
    _write_dual_tone_wav(lead_stem, sr=48_000, duration_sec=2.0)
    _write_dual_tone_wav(rhythm_stem, sr=48_000, duration_sec=2.0)
    _write_dual_tone_wav(keys_stem, sr=48_000, duration_sec=2.0)
    out = tmp_path / "StemDir.auralsong"

    from aural_ingest import cli
    from aural_ingest.transcription import (
        DrumEvent,
        DrumTranscriptionResult,
        InstrumentTranscriptionResult,
        MelodicNote,
        MelodicTranscriptionResult,
    )

    seen: dict[str, object] = {}

    def fake_transcribe_drums(
        stem_path: Path,
        requested_engine: str | None,
        algorithm_registry: dict[str, object],
        logger: object = None,
    ) -> DrumTranscriptionResult:
        seen["drum_source"] = Path(stem_path)
        return DrumTranscriptionResult(
            events=[DrumEvent(time=0.0, note=36, velocity=90)],
            used_algorithm=requested_engine or "combined_filter",
            attempted_algorithms=[requested_engine or "combined_filter"],
            warnings=[],
        )

    def fake_transcribe_all_melodic_stems(
        stems: dict[str, Path],
        requested_method: str | None,
        logger: object = None,
    ) -> list[InstrumentTranscriptionResult]:
        seen["instrument_stems"] = {role: Path(path) for role, path in stems.items()}
        note = MelodicNote(t_on=0.0, t_off=0.2, pitch=48, velocity=90, instrument="bass")
        return [
            InstrumentTranscriptionResult(
                instrument="bass",
                notes=[note],
                used_method=requested_method,
                attempted_methods=[requested_method or "auto"],
                warnings=[],
                stem_path=str(stems["bass"]),
            )
        ]

    def fake_transcribe_melodic(*_args: object, **_kwargs: object) -> MelodicTranscriptionResult:
        return MelodicTranscriptionResult(
            notes=[],
            used_method="melodic_adaptive",
            attempted_methods=["melodic_adaptive"],
            warnings=[],
        )

    monkeypatch.setattr(cli, "transcribe_drums", fake_transcribe_drums)
    monkeypatch.setattr(cli, "transcribe_all_melodic_stems", fake_transcribe_all_melodic_stems)
    monkeypatch.setattr(cli, "transcribe_melodic", fake_transcribe_melodic)

    args = type("Args", (), {})()
    args.input_dir_path = str(src_dir)
    args.out = str(out)
    args.profile = "full"
    args.config = json.dumps(
        {
            "disable_stem_separation": True,
            "input_stem_paths": {
                "drums": str(drum_stem),
                "bass": str(bass_stem),
                "lead_guitar": str(lead_stem),
                "rhythm_guitar": str(rhythm_stem),
                "keys": str(keys_stem),
            },
        }
    )
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "combined_filter"
    args.drum_stem_path = None
    args.melodic_method = "melodic_adaptive"
    args.shifts = 1
    args.multi_filter = False

    assert cli.cmd_import_dir(args) == 0
    assert (out / "audio" / "mix.wav").is_file()
    assert seen["drum_source"] == out / "audio" / "stems" / "drums.wav"
    assert seen["instrument_stems"] == {
        "bass": out / "audio" / "stems" / "bass.wav",
        "lead_guitar": out / "audio" / "stems" / "lead_guitar.wav",
        "rhythm_guitar": out / "audio" / "stems" / "rhythm_guitar.wav",
        "keys": out / "audio" / "stems" / "keys.wav",
    }

    _mix_channels, mix_sr, mix_samples = _read_pcm16_wav(out / "audio" / "mix.wav")
    _drum_channels, drum_sr, drum_samples = _read_pcm16_wav(drum_stem)
    assert mix_sr == drum_sr == 48_000
    assert mix_samples != drum_samples

    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    assert manifest["pipeline"]["input_stems"]["roles"] == [
        "bass",
        "drums",
        "keys",
        "lead_guitar",
        "rhythm_guitar",
    ]
    assert manifest["pipeline"]["guitar_split"]["status"] == "reused"


def test_import_dir_with_only_configured_keys_skips_synthetic_mix_drums_and_guitar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src_dir = tmp_path / "stem_dir"
    src_dir.mkdir(parents=True, exist_ok=True)
    keys_stem = src_dir / "keys.wav"
    _write_dual_tone_wav(keys_stem, sr=48_000, duration_sec=2.0)
    out = tmp_path / "KeysOnly.auralsong"

    from aural_ingest import cli
    from aural_ingest.transcription import InstrumentTranscriptionResult, MelodicNote

    seen: dict[str, object] = {}

    def fail_transcribe_drums(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("keys-only synthesized mix should not be drum-transcribed")

    def fake_transcribe_all_melodic_stems(
        stems: dict[str, Path],
        requested_method: str | None,
        logger: object = None,
    ) -> list[InstrumentTranscriptionResult]:
        seen["instrument_stems"] = {role: Path(path) for role, path in stems.items()}
        note = MelodicNote(t_on=0.0, t_off=0.2, pitch=64, velocity=90, instrument="keys")
        return [
            InstrumentTranscriptionResult(
                instrument="keys",
                notes=[note],
                used_method="piano_auto",
                attempted_methods=["piano_auto"],
                warnings=[],
                stem_path=str(stems["keys"]),
            )
        ]

    monkeypatch.setattr(cli, "transcribe_drums", fail_transcribe_drums)
    monkeypatch.setattr(cli, "transcribe_drums_with_profile", fail_transcribe_drums)
    monkeypatch.setattr(cli, "transcribe_all_melodic_stems", fake_transcribe_all_melodic_stems)

    args = type("Args", (), {})()
    args.input_dir_path = str(src_dir)
    args.out = str(out)
    args.profile = "full"
    args.config = json.dumps(
        {
            "disable_stem_separation": True,
            "input_stem_paths": {
                "keys": str(keys_stem),
            },
        }
    )
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "combined_filter"
    args.drum_stem_path = None
    args.melodic_method = "auto"
    args.shifts = 1
    args.multi_filter = False

    assert cli.cmd_import_dir(args) == 0
    assert seen["instrument_stems"] == {"keys": out / "audio" / "stems" / "keys.wav"}
    assert not (out / "audio" / "stems" / "lead_guitar.wav").exists()
    assert not (out / "audio" / "stems" / "rhythm_guitar.wav").exists()

    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    assert manifest["pipeline"]["input_stems"]["roles"] == ["keys"]
    assert manifest["pipeline"]["guitar_split"] == {
        "reason": "skipped: synthesized input-stem mix has no guitar source",
        "source_kind": "input_stems_mix",
        "source_path": "audio/mix.wav",
        "status": "skipped",
    }
    transcription = manifest["pipeline"]["transcription"]
    assert transcription["drum_engine_backend"] == "skipped"
    assert transcription["drum_filter_used"] == "combined_filter"
    assert transcription["instrument_stems_transcribed"] == ["keys"]
    assert transcription["instrument_melodic_methods_used"] == {"keys": "piano_auto"}


def test_import_with_configured_keys_source_skips_synthetic_mix_drums_and_guitar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys_stem = tmp_path / "keys.wav"
    _write_dual_tone_wav(keys_stem, sr=48_000, duration_sec=2.0)
    out = tmp_path / "KeysOnlyDirect.auralsong"

    from aural_ingest import cli
    from aural_ingest.transcription import InstrumentTranscriptionResult, MelodicNote

    seen: dict[str, object] = {}

    def fail_transcribe_drums(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("configured keys-only source should not be drum-transcribed")

    def fake_transcribe_all_melodic_stems(
        stems: dict[str, Path],
        requested_method: str | None,
        logger: object = None,
    ) -> list[InstrumentTranscriptionResult]:
        seen["instrument_stems"] = {role: Path(path) for role, path in stems.items()}
        note = MelodicNote(t_on=0.0, t_off=0.2, pitch=64, velocity=90, instrument="keys")
        return [
            InstrumentTranscriptionResult(
                instrument="keys",
                notes=[note],
                used_method="piano_auto",
                attempted_methods=["piano_auto"],
                warnings=[],
                stem_path=str(stems["keys"]),
            )
        ]

    monkeypatch.setattr(cli, "transcribe_drums", fail_transcribe_drums)
    monkeypatch.setattr(cli, "transcribe_drums_with_profile", fail_transcribe_drums)
    monkeypatch.setattr(cli, "transcribe_all_melodic_stems", fake_transcribe_all_melodic_stems)

    args = type("Args", (), {})()
    args.input_audio_path = str(keys_stem)
    args.out = str(out)
    args.profile = "full"
    args.config = json.dumps(
        {
            "disable_stem_separation": True,
            "input_stem_paths": {
                "keys": str(keys_stem),
            },
        }
    )
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "combined_filter"
    args.drum_stem_path = None
    args.melodic_method = "auto"
    args.shifts = 1
    args.multi_filter = False

    assert cli.cmd_import(args) == 0
    assert seen["instrument_stems"] == {"keys": out / "audio" / "stems" / "keys.wav"}
    assert not (out / "audio" / "stems" / "lead_guitar.wav").exists()
    assert not (out / "audio" / "stems" / "rhythm_guitar.wav").exists()

    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    assert manifest["pipeline"]["guitar_split"] == {
        "reason": "skipped: synthesized input-stem mix has no guitar source",
        "source_kind": "input_stems_mix",
        "source_path": "audio/mix.wav",
        "status": "skipped",
    }
    transcription = manifest["pipeline"]["transcription"]
    assert transcription["drum_engine_backend"] == "skipped"
    assert transcription["instrument_stems_transcribed"] == ["keys"]
    assert transcription["instrument_melodic_methods_used"] == {"keys": "piano_auto"}


def test_keys_import_transcription_matches_multiple_ingress_verifiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    keys_stem = tmp_path / "keys.wav"
    _write_dual_tone_wav(keys_stem, sr=48_000, duration_sec=2.0)
    out = tmp_path / "KeysVerifier.auralsong"

    from aural_ingest import cli
    from aural_ingest.transcription import InstrumentTranscriptionResult, MelodicNote

    expected_notes = [
        MelodicNote(t_on=0.0, t_off=0.5, pitch=60, velocity=80, instrument="keys"),
        MelodicNote(t_on=0.25, t_off=0.875, pitch=64, velocity=84, instrument="keys"),
        MelodicNote(t_on=1.0, t_off=1.5, pitch=67, velocity=88, instrument="keys"),
    ]

    def fail_transcribe_drums(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("configured keys-only source should not be drum-transcribed")

    def fake_transcribe_all_melodic_stems(
        stems: dict[str, Path],
        requested_method: str | None,
        logger: object = None,
    ) -> list[InstrumentTranscriptionResult]:
        assert requested_method == "piano_auto"
        return [
            InstrumentTranscriptionResult(
                instrument="keys",
                notes=expected_notes,
                used_method="piano_auto",
                attempted_methods=["piano_auto"],
                warnings=[],
                stem_path=str(stems["keys"]),
            )
        ]

    monkeypatch.setattr(cli, "transcribe_drums", fail_transcribe_drums)
    monkeypatch.setattr(cli, "transcribe_drums_with_profile", fail_transcribe_drums)
    monkeypatch.setattr(cli, "transcribe_all_melodic_stems", fake_transcribe_all_melodic_stems)

    args = type("Args", (), {})()
    args.input_audio_path = str(keys_stem)
    args.out = str(out)
    args.profile = "full"
    args.config = json.dumps(
        {
            "bpm_hint": 120.0,
            "disable_stem_separation": True,
            "input_stem_paths": {
                "keys": str(keys_stem),
            },
        }
    )
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "combined_filter"
    args.drum_stem_path = None
    args.melodic_method = "piano_auto"
    args.shifts = 1
    args.multi_filter = False

    assert cli.cmd_import(args) == 0
    capsys.readouterr()

    validate_args = type("Args", (), {})()
    validate_args.auralsong_dir = str(out)
    assert cli.cmd_validate(validate_args) == 0
    _assert_validate_reports_keys_ingress_verifiers(capsys, len(expected_notes))
    _assert_keys_auralsong_matches_multiple_ingress_verifiers(out, expected_notes)


def test_import_dir_keys_transcription_matches_multiple_ingress_verifiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src_dir = tmp_path / "stem_dir"
    src_dir.mkdir(parents=True, exist_ok=True)
    keys_stem = src_dir / "keys.wav"
    _write_dual_tone_wav(keys_stem, sr=48_000, duration_sec=2.0)
    out = tmp_path / "KeysDirVerifier.auralsong"

    from aural_ingest import cli
    from aural_ingest.transcription import InstrumentTranscriptionResult, MelodicNote

    expected_notes = [
        MelodicNote(t_on=0.0, t_off=0.5, pitch=60, velocity=80, instrument="keys"),
        MelodicNote(t_on=0.25, t_off=0.875, pitch=64, velocity=84, instrument="keys"),
        MelodicNote(t_on=1.0, t_off=1.5, pitch=67, velocity=88, instrument="keys"),
    ]

    def fail_transcribe_drums(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("configured keys-only source should not be drum-transcribed")

    def fake_transcribe_all_melodic_stems(
        stems: dict[str, Path],
        requested_method: str | None,
        logger: object = None,
    ) -> list[InstrumentTranscriptionResult]:
        assert requested_method == "piano_auto"
        return [
            InstrumentTranscriptionResult(
                instrument="keys",
                notes=expected_notes,
                used_method="piano_auto",
                attempted_methods=["piano_auto"],
                warnings=[],
                stem_path=str(stems["keys"]),
            )
        ]

    monkeypatch.setattr(cli, "transcribe_drums", fail_transcribe_drums)
    monkeypatch.setattr(cli, "transcribe_drums_with_profile", fail_transcribe_drums)
    monkeypatch.setattr(cli, "transcribe_all_melodic_stems", fake_transcribe_all_melodic_stems)

    args = type("Args", (), {})()
    args.input_dir_path = str(src_dir)
    args.out = str(out)
    args.profile = "full"
    args.config = json.dumps(
        {
            "bpm_hint": 120.0,
            "disable_stem_separation": True,
            "input_stem_paths": {
                "keys": str(keys_stem),
            },
        }
    )
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "combined_filter"
    args.drum_stem_path = None
    args.melodic_method = "piano_auto"
    args.shifts = 1
    args.multi_filter = False

    assert cli.cmd_import_dir(args) == 0
    capsys.readouterr()

    validate_args = type("Args", (), {})()
    validate_args.auralsong_dir = str(out)
    assert cli.cmd_validate(validate_args) == 0
    _assert_validate_reports_keys_ingress_verifiers(capsys, len(expected_notes))
    _assert_keys_auralsong_matches_multiple_ingress_verifiers(out, expected_notes)


def test_import_uses_demucs_separated_drums_and_guitar_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src_mix = tmp_path / "mix.wav"
    _write_clicktrack_wav(src_mix, sr=48_000, duration_sec=2.0, bpm=120.0)
    out = tmp_path / "Separated.auralsong"

    from aural_ingest import cli
    from aural_ingest.transcription import (
        DrumEvent,
        DrumTranscriptionResult,
        MelodicTranscriptionResult,
    )

    seen: dict[str, Path] = {}

    def fake_separate(
        mix_wav: Path,
        stems_dir: Path,
        *,
        mix_sha256: str,
        shifts: int,
        config: dict[str, object],
    ) -> dict[str, object]:
        _write_clicktrack_wav(stems_dir / "drums.wav", sr=48_000, duration_sec=2.0, bpm=90.0)
        _write_dual_tone_wav(stems_dir / "guitar.wav", sr=48_000, duration_sec=2.0)
        return {
            "ok": True,
            "status": "fresh",
            "provider": "demucs",
            "modelpack_id": "demucs_6",
            "modelpack_version": "htdemucs_6s-test",
            "architecture": "htdemucs_6s",
            "modelpack_path": str(tmp_path / "demucs_6.zip"),
            "weight_path": str(tmp_path / "5c90dfd2-34c22ccb.th"),
            "stem_paths": {
                "drums": "audio/stems/drums.wav",
                "guitar": "audio/stems/guitar.wav",
            },
            "cache_hit": False,
            "shifts": shifts,
            "device": "cpu",
        }

    def fake_transcribe_drums(
        stem_path: Path,
        requested_engine: str | None,
        algorithm_registry: dict[str, object],
        logger: object = None,
    ) -> DrumTranscriptionResult:
        seen["drum_source"] = Path(stem_path)
        return DrumTranscriptionResult(
            events=[DrumEvent(time=0.0, note=36, velocity=90)],
            used_algorithm=requested_engine or "combined_filter",
            attempted_algorithms=[requested_engine or "combined_filter"],
            warnings=[],
        )

    def fake_transcribe_melodic(*_args: object, **_kwargs: object) -> MelodicTranscriptionResult:
        return MelodicTranscriptionResult(
            notes=[],
            used_method="basic_pitch",
            attempted_methods=["basic_pitch"],
            warnings=[],
        )

    monkeypatch.setattr(cli, "_separate_stems_with_demucs", fake_separate)
    monkeypatch.setattr(cli, "transcribe_drums", fake_transcribe_drums)
    monkeypatch.setattr(cli, "transcribe_melodic", fake_transcribe_melodic)

    args = type("Args", (), {})()
    args.input_audio_path = str(src_mix)
    args.out = str(out)
    args.profile = "full"
    args.config = "{}"
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "combined_filter"
    args.drum_stem_path = None
    args.melodic_method = "auto"
    args.stem_separation_provider = "demucs"
    args.stem_separation_provider_path = None
    args.shifts = 1
    args.multi_filter = False

    assert cli.cmd_import(args) == 0
    assert seen["drum_source"] == out / "audio" / "stems" / "drums.wav"

    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    split_meta = manifest["pipeline"]["guitar_split"]
    sep_meta = manifest["pipeline"]["stem_separation"]
    assert split_meta["source_kind"] == "stems_guitar"
    assert split_meta["source_path"] == "audio/stems/guitar.wav"
    assert sep_meta["provider"] == "demucs"
    assert sep_meta["architecture"] == "htdemucs_6s"
    assert manifest["assets"]["audio"]["stems"]["drums_path"] == "audio/stems/drums.wav"
    assert manifest["assets"]["audio"]["stems"]["guitar_path"] == "audio/stems/guitar.wav"
    assert manifest["recognition"]["drums"]["source_kind"] == "separated_drums"
    assert manifest["recognition"]["drums"]["source_path"] == "audio/stems/drums.wav"


def test_import_rejects_mt3_engine_when_only_mix_fallback_is_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src_mix = tmp_path / "mix.wav"
    _write_clicktrack_wav(src_mix, sr=48_000, duration_sec=2.0, bpm=120.0)
    out = tmp_path / "Mt3MissingStem.auralsong"

    from aural_ingest import cli

    monkeypatch.setattr(
        cli,
        "_separate_stems_with_demucs",
        lambda *_args, **_kwargs: {"ok": False, "reason": "demucs unavailable"},
    )

    args = type("Args", (), {})()
    args.input_audio_path = str(src_mix)
    args.out = str(out)
    args.profile = "full"
    args.config = "{}"
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "mr_mt3_drums"
    args.drum_stem_path = None
    args.melodic_method = "auto"
    args.shifts = 1
    args.multi_filter = False

    assert cli.cmd_import(args) == 4


def test_demucs_modelpack_auto_discovery_supports_portable_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    portable_root = tmp_path / "AuralPrimerPortable"
    sidecar_dir = portable_root / "sidecar"
    modelpack_dir = portable_root / "modelpacks"
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    modelpack_dir.mkdir(parents=True, exist_ok=True)
    (sidecar_dir / "aural_ingest.exe").write_bytes(b"")
    expected = modelpack_dir / "demucs_6.zip"
    expected.write_bytes(b"")
    expected_ft = modelpack_dir / "demucs_ft_drums.zip"
    expected_ft.write_bytes(b"")

    from aural_ingest import cli

    monkeypatch.chdir(sidecar_dir)
    monkeypatch.setattr(cli.sys, "executable", str(sidecar_dir / "aural_ingest.exe"))
    monkeypatch.setattr(cli, "__file__", str(sidecar_dir / "embedded" / "aural_ingest" / "cli.py"))

    candidates = cli._default_demucs_modelpack_candidates()
    assert expected in candidates
    assert expected_ft in candidates


def _write_demucs_modelpack_zip(
    path: Path,
    *,
    modelpack_id: str,
    architecture: str,
    license_name: str | None = None,
    include_license_file: bool = True,
    weight_sha256: str | None = None,
) -> None:
    weight_bytes = b"weights"
    checksum = weight_sha256 or hashlib.sha256(weight_bytes).hexdigest()
    stems = ["drums"] if modelpack_id == "demucs_ft_drums" else ["keys", "drums", "guitar", "bass", "vocals"]
    manifest = {
        "id": modelpack_id,
        "version": "test",
        "provider": "demucs",
        "architecture": architecture,
        "stems": stems,
        "weights": [
            {
                "path": "files/model.th",
                "sha256": checksum,
                "source_url": "https://example.invalid/model.th",
                "format": "pytorch",
            }
        ],
    }
    if license_name is not None:
        manifest["license"] = license_name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("modelpack.json", json.dumps(manifest))
        zf.writestr("files/model.th", weight_bytes)
        if include_license_file:
            zf.writestr("LICENSE", "test license\n")


def test_resolve_demucs_modelpack_accepts_configured_ft_drums_zip(tmp_path: Path) -> None:
    from aural_ingest import cli

    modelpack_zip = tmp_path / "demucs_ft_drums.zip"
    _write_demucs_modelpack_zip(
        modelpack_zip,
        modelpack_id="demucs_ft_drums",
        architecture="htdemucs_ft_drums",
        license_name="MIT",
    )

    resolved, manifest, err = cli._resolve_demucs_modelpack({"demucs_modelpack_zip_path": str(modelpack_zip)})

    assert err is None
    assert resolved == modelpack_zip
    assert manifest is not None
    assert manifest["id"] == "demucs_ft_drums"


def test_resolve_demucs_modelpack_supports_htdemucs_ft_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aural_ingest import cli

    modelpack_zip = tmp_path / "demucs_ft_drums.zip"
    _write_demucs_modelpack_zip(
        modelpack_zip,
        modelpack_id="demucs_ft_drums",
        architecture="htdemucs_ft_drums",
        license_name="MIT",
    )
    monkeypatch.setattr(cli, "_default_demucs_modelpack_candidates", lambda _ids=None: [modelpack_zip])

    resolved, manifest, err = cli._resolve_demucs_modelpack({"stem_separation_modelpack_id": "htdemucs_ft"})

    assert err is None
    assert resolved == modelpack_zip
    assert manifest is not None
    assert manifest["id"] == "demucs_ft_drums"


def test_resolve_demucs_modelpack_rejects_missing_weight_sha(tmp_path: Path) -> None:
    from aural_ingest import cli

    modelpack_zip = tmp_path / "demucs_6.zip"
    with zipfile.ZipFile(modelpack_zip, "w") as zf:
        zf.writestr(
            "modelpack.json",
            json.dumps(
                {
                    "id": "demucs_6",
                    "version": "test",
                    "provider": "demucs",
                    "architecture": "htdemucs_6s",
                    "stems": ["keys", "drums", "guitar", "bass", "vocals"],
                    "weights": [
                        {
                            "path": "files/model.th",
                            "source_url": "https://example.invalid/model.th",
                        }
                    ],
                }
            ),
        )
        zf.writestr("files/model.th", b"weights")

    resolved, manifest, err = cli._resolve_demucs_modelpack({"demucs_modelpack_zip_path": str(modelpack_zip)})

    assert resolved is None
    assert manifest is None
    assert err is not None
    assert "weights[0].sha256 missing" in err


def test_resolve_demucs_modelpack_rejects_weight_hash_mismatch(tmp_path: Path) -> None:
    from aural_ingest import cli

    modelpack_zip = tmp_path / "demucs_6.zip"
    _write_demucs_modelpack_zip(
        modelpack_zip,
        modelpack_id="demucs_6",
        architecture="htdemucs_6s",
        weight_sha256="0" * 64,
    )

    resolved, manifest, err = cli._resolve_demucs_modelpack({"demucs_modelpack_zip_path": str(modelpack_zip)})

    assert resolved is None
    assert manifest is None
    assert err is not None
    assert "modelpack weight sha256 mismatch" in err


def test_resolve_demucs_ft_drums_requires_license_metadata(tmp_path: Path) -> None:
    from aural_ingest import cli

    missing_license = tmp_path / "demucs_ft_drums_missing_license.zip"
    _write_demucs_modelpack_zip(
        missing_license,
        modelpack_id="demucs_ft_drums",
        architecture="htdemucs_ft_drums",
    )

    resolved, manifest, err = cli._resolve_demucs_modelpack({"demucs_modelpack_zip_path": str(missing_license)})

    assert resolved is None
    assert manifest is None
    assert err is not None
    assert "missing license" in err

    missing_license_file = tmp_path / "demucs_ft_drums_missing_license_file.zip"
    _write_demucs_modelpack_zip(
        missing_license_file,
        modelpack_id="demucs_ft_drums",
        architecture="htdemucs_ft_drums",
        license_name="MIT",
        include_license_file=False,
    )

    resolved, manifest, err = cli._resolve_demucs_modelpack(
        {"demucs_modelpack_zip_path": str(missing_license_file)}
    )

    assert resolved is None
    assert manifest is None
    assert err is not None
    assert "missing LICENSE file" in err


def test_resolved_demucs_stage_requirement_uses_selected_modelpack_id(tmp_path: Path) -> None:
    from aural_ingest import cli

    req = cli._resolved_demucs_stage_requirement(
        tmp_path / "demucs_ft_drums.zip",
        {"id": "demucs_ft_drums", "version": "ft-test"},
        None,
    )

    assert req["model_id"] == "demucs_ft_drums"
    assert req["modelpack_id"] == "demucs_ft_drums"
    assert req["version"] == "ft-test"


def test_demucs_separation_cache_key_includes_modelpack_id() -> None:
    from aural_ingest import cli

    weight_sha = hashlib.sha256(b"weights").hexdigest()
    common = {
        "mix_sha256": "abcdef0123456789abcdef0123456789",
        "version": "same-version",
        "weight_sha": weight_sha,
        "shifts": 1,
    }

    default_dir = cli._demucs_separation_cache_dir(modelpack_id="demucs_6", **common)
    ft_dir = cli._demucs_separation_cache_dir(modelpack_id="demucs_ft_drums", **common)

    assert default_dir != ft_dir
    assert "demucs_6" in default_dir.name
    assert "demucs_ft_drums" in ft_dir.name


def test_demucs_separation_cache_key_requires_weight_sha() -> None:
    from aural_ingest import cli

    with pytest.raises(ValueError, match="verified weight sha256"):
        cli._demucs_separation_cache_dir(
            mix_sha256="abcdef0123456789abcdef0123456789",
            modelpack_id="demucs_6",
            version="test",
            weight_sha="",
            shifts=1,
        )


def test_demucs_ft_drums_composes_default_stems_with_refined_drums(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aural_ingest import cli

    mix = tmp_path / "mix.wav"
    mix.write_bytes(b"mix")
    stems_dir = tmp_path / "stems"
    default_manifest = {"id": "demucs_6", "version": "base-test", "architecture": "htdemucs_6s"}
    ft_manifest = {
        "id": "demucs_ft_drums",
        "version": "ft-test",
        "architecture": "htdemucs_ft_drums",
    }
    default_zip = tmp_path / "demucs_6.zip"
    ft_zip = tmp_path / "demucs_ft_drums.zip"
    calls: list[str] = []

    def fake_resolve(config: dict[str, object]):
        requested = cli._requested_demucs_modelpack_id(config)
        if requested == "demucs_ft_drums":
            return ft_zip, ft_manifest, None
        return default_zip, default_manifest, None

    def fake_single_model(
        mix_wav,
        stems,
        *,
        mix_sha256,
        shifts,
        modelpack_zip,
        modelpack_manifest,
        protected_roles=None,
    ):
        calls.append(modelpack_manifest["id"])
        if modelpack_manifest["id"] == "demucs_6":
            return {
                "ok": True,
                "status": "cached",
                "provider": "demucs",
                "modelpack_id": "demucs_6",
                "modelpack_version": "base-test",
                "stem_paths": {
                    "bass": "audio/stems/bass.wav",
                    "drums": "audio/stems/drums.wav",
                    "guitar": "audio/stems/guitar.wav",
                    "keys": "audio/stems/keys.wav",
                    "vocals": "audio/stems/vocals.wav",
                },
            }
        return {
            "ok": True,
            "status": "fresh",
            "provider": "demucs",
            "modelpack_id": "demucs_ft_drums",
            "modelpack_version": "ft-test",
            "stem_paths": {"drums": "audio/stems/drums.wav"},
        }

    monkeypatch.setattr(cli, "_resolve_demucs_modelpack", fake_resolve)
    monkeypatch.setattr(cli, "_separate_stems_with_demucs_single_model", fake_single_model)

    result = cli._separate_stems_with_demucs(
        mix,
        stems_dir,
        mix_sha256="abc123",
        shifts=2,
        config={"stem_separation_modelpack_id": "demucs_ft_drums"},
    )

    assert calls == ["demucs_6", "demucs_ft_drums"]
    assert result["ok"] is True
    assert result["modelpack_id"] == "demucs_ft_drums"
    assert result["composite"] is True
    assert result["refinement_role"] == "drums"
    assert result["baseline_modelpack_id"] == "demucs_6"
    assert result["stem_paths"] == {
        "bass": "audio/stems/bass.wav",
        "drums": "audio/stems/drums.wav",
        "guitar": "audio/stems/guitar.wav",
        "keys": "audio/stems/keys.wav",
        "vocals": "audio/stems/vocals.wav",
    }


def test_demucs_ft_drums_fails_when_refinement_has_no_drums(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aural_ingest import cli

    default_manifest = {"id": "demucs_6", "version": "base-test", "architecture": "htdemucs_6s"}
    ft_manifest = {
        "id": "demucs_ft_drums",
        "version": "ft-test",
        "architecture": "htdemucs_ft_drums",
    }

    def fake_resolve(config: dict[str, object]):
        requested = cli._requested_demucs_modelpack_id(config)
        if requested == "demucs_ft_drums":
            return tmp_path / "demucs_ft_drums.zip", ft_manifest, None
        return tmp_path / "demucs_6.zip", default_manifest, None

    def fake_single_model(
        mix_wav,
        stems,
        *,
        mix_sha256,
        shifts,
        modelpack_zip,
        modelpack_manifest,
        protected_roles=None,
    ):
        if modelpack_manifest["id"] == "demucs_6":
            return {
                "ok": True,
                "status": "cached",
                "provider": "demucs",
                "modelpack_id": "demucs_6",
                "stem_paths": {"drums": "audio/stems/drums.wav", "bass": "audio/stems/bass.wav"},
            }
        return {
            "ok": True,
            "status": "fresh",
            "provider": "demucs",
            "modelpack_id": "demucs_ft_drums",
            "stem_paths": {},
        }

    monkeypatch.setattr(cli, "_resolve_demucs_modelpack", fake_resolve)
    monkeypatch.setattr(cli, "_separate_stems_with_demucs_single_model", fake_single_model)

    result = cli._separate_stems_with_demucs(
        tmp_path / "mix.wav",
        tmp_path / "stems",
        mix_sha256="abc123",
        shifts=1,
        config={"stem_separation_modelpack_id": "demucs_ft_drums"},
    )

    assert result["ok"] is False
    assert result["modelpack_id"] == "demucs_ft_drums"
    assert "did not produce a drums stem" in result["reason"]


def test_demucs_cache_meta_requires_current_inputs_and_safe_stems(tmp_path: Path) -> None:
    from aural_ingest import cli

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "drums.wav").write_bytes(b"wav")
    weight_sha = hashlib.sha256(b"weights").hexdigest()
    meta = {
        "provider": "demucs",
        "mix_sha256": "mix-a",
        "modelpack_id": "demucs_6",
        "modelpack_version": "test",
        "architecture": "htdemucs_6s",
        "weight_sha256": weight_sha,
        "shifts": 1,
        "stem_files": {"drums": "drums.wav"},
    }

    assert cli._demucs_cache_meta_matches(
        meta,
        cache_dir=cache_dir,
        mix_sha256="mix-a",
        modelpack_id="demucs_6",
        version="test",
        architecture="htdemucs_6s",
        weight_sha=weight_sha,
        shifts=1,
    )
    assert not cli._demucs_cache_meta_matches(
        {**meta, "mix_sha256": "mix-b"},
        cache_dir=cache_dir,
        mix_sha256="mix-a",
        modelpack_id="demucs_6",
        version="test",
        architecture="htdemucs_6s",
        weight_sha=weight_sha,
        shifts=1,
    )
    assert not cli._demucs_cache_meta_matches(
        {**meta, "stem_files": {"drums": "../drums.wav"}},
        cache_dir=cache_dir,
        mix_sha256="mix-a",
        modelpack_id="demucs_6",
        version="test",
        architecture="htdemucs_6s",
        weight_sha=weight_sha,
        shifts=1,
    )


def test_validate_passes_on_generated_auralsong(tmp_path: Path) -> None:
    src = tmp_path / "src.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=4.0, bpm=120.0)
    out = tmp_path / "Out.auralsong"

    from aural_ingest.cli import cmd_import, cmd_validate

    import_args = type("Args", (), {})()
    import_args.input_audio_path = str(src)
    import_args.out = str(out)
    import_args.profile = "full"
    import_args.config = json.dumps({"ingest_timestamp": "2000-01-01T00:00:00Z", "bpm_hint": 120})
    import_args.title = None
    import_args.artist = None
    import_args.duration_sec = None

    assert cmd_import(import_args) == 0

    validate_args = type("Args", (), {})()
    validate_args.auralsong_dir = str(out)

    assert cmd_validate(validate_args) == 0


def test_nonwav_input_requires_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # If ffmpeg is not present, non-wav sources must fail deterministically.
    src = tmp_path / "src.mp3"
    src.write_bytes(b"not really an mp3")
    out = tmp_path / "Out.auralsong"

    from aural_ingest import cli as ingest

    monkeypatch.setattr(ingest, "_have_ffmpeg", lambda: False)

    args = type("Args", (), {})()
    args.input_audio_path = str(src)
    args.out = str(out)
    args.profile = "full"
    args.config = None
    args.title = None
    args.artist = None
    args.duration_sec = None

    rc = ingest.cmd_import(args)
    assert rc == 3


def test_import_dir_picks_audio_from_directory(tmp_path: Path) -> None:
    src_dir = tmp_path / "src_dir"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "mix.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=2.0, bpm=120.0)
    out = tmp_path / "Out.auralsong"

    from aural_ingest.cli import cmd_import_dir

    args = type("Args", (), {})()
    args.input_dir_path = str(src_dir)
    args.out = str(out)
    args.profile = "full"
    args.config = json.dumps({"ingest_timestamp": "2000-01-01T00:00:00Z"})
    args.title = "FromDir"
    args.artist = "Test"
    args.duration_sec = None

    rc = cmd_import_dir(args)
    assert rc == 0
    assert (out / "manifest.json").is_file()
    assert (out / "audio/mix.wav").is_file()


def test_import_dir_writes_notes_mid_without_ordering_failure(tmp_path: Path) -> None:
    src_dir = tmp_path / "src_dir"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "mix.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=2.0, bpm=120.0)
    out = tmp_path / "OutOrder.auralsong"

    from aural_ingest.cli import cmd_import_dir

    args = type("Args", (), {})()
    args.input_dir_path = str(src_dir)
    args.out = str(out)
    args.profile = "full"
    args.config = json.dumps({"ingest_timestamp": "2000-01-01T00:00:00Z"})
    args.title = "OrderCheck"
    args.artist = "Test"
    args.duration_sec = None

    rc = cmd_import_dir(args)
    assert rc == 0

    notes_mid = out / "features" / "notes.mid"
    assert notes_mid.is_file()
    data = notes_mid.read_bytes()
    assert data.startswith(b"MThd")
    assert b"MTrk" in data
    assert b"\xFF\x51\x03" in data


def test_import_persists_transcription_options_into_manifest(tmp_path: Path) -> None:
    src = tmp_path / "src.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=2.0, bpm=120.0)
    out = tmp_path / "Opts.auralsong"

    from aural_ingest.cli import cmd_import

    args = type("Args", (), {})()
    args.input_audio_path = str(src)
    args.out = str(out)
    args.profile = "full"
    args.config = "{}"
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "dsp_spectral_flux"
    args.melodic_method = "basic_pitch"
    args.beat_analysis_mode = "high_accuracy"
    args.stem_separation_provider = "none"
    args.stem_separation_provider_path = None
    args.shifts = 2
    args.multi_filter = True

    assert cmd_import(args) == 0
    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    tr = manifest["pipeline"]["transcription"]
    recognition = manifest["recognition"]
    assert tr["drum_filter"] == "dsp_spectral_flux"
    assert tr["drum_filter_requested"] == "dsp_spectral_flux"
    assert tr["drum_filter_used"] == "dsp_spectral_flux"
    assert tr["melodic_method"] == "basic_pitch"
    assert tr["melodic_method_used"] in {"basic_pitch", "pyin"}
    assert tr["beat_analysis_mode"] == "high_accuracy"
    assert tr["stem_separation_provider"] == "none"
    assert tr["shifts"] == 2
    assert tr["multi_filter"] is True
    assert recognition["drums"]["requested_engine"] == "dsp_spectral_flux"
    assert recognition["drums"]["used_engine"] == "dsp_spectral_flux"
    assert recognition["melodic"]["requested_engine"] == "basic_pitch"
    assert recognition["melodic"]["used_engine"] in {"basic_pitch", "pyin"}


def test_import_unknown_drum_filter_falls_back_to_default_engine_and_records_warning(tmp_path: Path) -> None:
    src = tmp_path / "src.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=2.0, bpm=120.0)
    out = tmp_path / "UnknownFilter.auralsong"

    from aural_ingest.cli import cmd_import

    args = type("Args", (), {})()
    args.input_audio_path = str(src)
    args.out = str(out)
    args.profile = "full"
    args.config = "{}"
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "legacy_unknown_filter"
    args.melodic_method = "auto"
    args.shifts = 1
    args.multi_filter = False

    assert cmd_import(args) == 0
    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    tr = manifest["pipeline"]["transcription"]
    # Unknown filters fall back to the global default engine, which moved off
    # the recall-collapsing `combined_filter` to
    # `beat_conditioned_multiband_decoder`.
    assert tr["drum_filter"] == "beat_conditioned_multiband_decoder"
    assert tr["drum_filter_requested"] == "legacy_unknown_filter"
    assert tr["drum_filter_used"] == "beat_conditioned_multiband_decoder"
    assert tr["warnings"]
    assert manifest["recognition"]["drums"]["requested_engine"] == "legacy_unknown_filter"
    assert manifest["recognition"]["drums"]["used_engine"] == "beat_conditioned_multiband_decoder"


def test_import_auto_drum_filter_uses_transcription_profile_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=2.0, bpm=120.0)
    out = tmp_path / "ProfileDrums.auralsong"

    from aural_ingest import cli
    from aural_ingest.transcription import DrumEvent

    calls: list[str] = []

    def beat_conditioned(_stem: Path) -> list[DrumEvent]:
        calls.append("beat_conditioned_multiband_decoder")
        return [DrumEvent(time=0.1, note=36, velocity=90)]

    def combined(_stem: Path) -> list[DrumEvent]:
        calls.append("combined_filter")
        return [DrumEvent(time=0.2, note=38, velocity=90)]

    monkeypatch.setattr(
        cli,
        "build_default_drum_algorithm_registry",
        lambda: {
            "beat_conditioned_multiband_decoder": beat_conditioned,
            "combined_filter": combined,
        },
    )

    args = type("Args", (), {})()
    args.input_audio_path = str(src)
    args.out = str(out)
    args.profile = "full"
    args.config = "{}"
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "auto"
    args.melodic_method = "auto"
    args.beat_analysis_mode = "standard"
    args.stem_separation_provider = "none"
    args.stem_separation_provider_path = None
    args.shifts = 1
    args.multi_filter = False

    assert cli.cmd_import(args) == 0

    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    tr = manifest["pipeline"]["transcription"]
    assert calls == ["beat_conditioned_multiband_decoder"]
    assert tr["drum_engine_selection"] == "profile"
    assert tr["drum_filter_requested"] == "auto"
    assert tr["drum_filter"] == "profile"
    assert tr["drum_filter_used"] == "beat_conditioned_multiband_decoder"
    assert tr["drum_profile_engines"][0] == "beat_conditioned_multiband_decoder"
    assert tr["drum_profile_engines"][1] == "spectral_flux_multiband"
    assert manifest["recognition"]["drums"]["normalized_engine"] == "profile"
    assert manifest["recognition"]["drums"]["used_engine"] == "beat_conditioned_multiband_decoder"


def test_import_auto_melodic_no_longer_requires_external_basic_pitch_model(tmp_path: Path) -> None:
    src = tmp_path / "src.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=2.0, bpm=120.0)
    out = tmp_path / "AutoMelodic.auralsong"

    from aural_ingest.cli import cmd_import

    args = type("Args", (), {})()
    args.input_audio_path = str(src)
    args.out = str(out)
    args.profile = "full"
    args.config = "{}"
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "combined_filter"
    args.melodic_method = "auto"
    args.shifts = 1
    args.multi_filter = False

    assert cmd_import(args) == 0
    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    tr = manifest["pipeline"]["transcription"]
    assert tr["melodic_method_used"] == "basic_pitch"
    assert not any("model path unavailable" in w for w in tr.get("warnings", []))


def test_import_high_accuracy_beat_mode_falls_back_to_standard_without_librosa(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tmp_path / "src.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=2.0, bpm=120.0)
    out = tmp_path / "HighAccuracy.auralsong"

    from aural_ingest import cli

    real_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):  # type: ignore[no-untyped-def]
        if name == "librosa":
            raise ImportError("blocked for test")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    args = type("Args", (), {})()
    args.input_audio_path = str(src)
    args.out = str(out)
    args.profile = "full"
    args.config = "{}"
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "combined_filter"
    args.melodic_method = "auto"
    args.beat_analysis_mode = "high_accuracy"
    args.stem_separation_provider = "none"
    args.stem_separation_provider_path = None
    args.shifts = 1
    args.multi_filter = False

    assert cli.cmd_import(args) == 0
    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    beats_meta = manifest["pipeline"]["beats_tempo"]
    assert beats_meta["mode"] == "high_accuracy"
    assert beats_meta["degraded_to"] == "standard"


def test_import_uses_external_stem_separation_provider_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src_mix = tmp_path / "mix.wav"
    _write_clicktrack_wav(src_mix, sr=48_000, duration_sec=2.0, bpm=120.0)
    out = tmp_path / "ExternalProvider.auralsong"
    provider_module = tmp_path / "fake_provider.py"
    provider_module.write_text(
        "\n".join(
            [
                "import wave",
                "",
                "def separate(mix_wav, stems_dir, *, mix_sha256, shifts, config):",
                "    with wave.open(str(mix_wav), 'rb') as src:",
                "        raw = src.readframes(src.getnframes())",
                "        params = (src.getnchannels(), src.getsampwidth(), src.getframerate())",
                "    stem_path = stems_dir / 'drums.wav'",
                "    with wave.open(str(stem_path), 'wb') as dst:",
                "        dst.setnchannels(params[0])",
                "        dst.setsampwidth(params[1])",
                "        dst.setframerate(params[2])",
                "        dst.writeframes(raw)",
                "    return {",
                "        'ok': True,",
                "        'status': 'fresh',",
                "        'provider': 'external',",
                "        'provider_path': 'fake_provider:separate',",
                "        'stem_paths': {'drums': 'audio/stems/drums.wav'},",
                "        'cache_hit': False,",
                "        'shifts': shifts,",
                "    }",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.syspath_prepend(str(tmp_path))

    from aural_ingest.cli import cmd_import

    args = type("Args", (), {})()
    args.input_audio_path = str(src_mix)
    args.out = str(out)
    args.profile = "full"
    args.config = "{}"
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "combined_filter"
    args.melodic_method = "auto"
    args.beat_analysis_mode = "standard"
    args.stem_separation_provider = "external"
    args.stem_separation_provider_path = "fake_provider:separate"
    args.shifts = 1
    args.multi_filter = False

    assert cmd_import(args) == 0
    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    sep_meta = manifest["pipeline"]["stem_separation"]
    assert sep_meta["provider"] == "external"
    assert sep_meta["provider_path"] == "fake_provider:separate"
    assert manifest["assets"]["audio"]["stems"]["drums_path"] == "audio/stems/drums.wav"


def test_import_fallback_chain_uses_next_algorithm_when_requested_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "src.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=2.0, bpm=120.0)
    out = tmp_path / "Fallback.auralsong"

    from aural_ingest import cli
    from aural_ingest.transcription import DrumEvent

    def bad(_stem: Path) -> list[DrumEvent]:
        raise RuntimeError("forced failure")

    def good(_stem: Path) -> list[DrumEvent]:
        return [DrumEvent(time=0.0, note=36, velocity=90)]

    monkeypatch.setattr(
        cli,
        "build_default_drum_algorithm_registry",
        lambda: {
            "combined_filter": bad,
            "dsp_bandpass_improved": good,
        },
    )

    args = type("Args", (), {})()
    args.input_audio_path = str(src)
    args.out = str(out)
    args.profile = "full"
    args.config = "{}"
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "combined_filter"
    args.melodic_method = "auto"
    args.shifts = 1
    args.multi_filter = False

    assert cli.cmd_import(args) == 0
    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    tr = manifest["pipeline"]["transcription"]
    assert tr["drum_filter_used"] == "dsp_bandpass_improved"
    assert "dsp_bandpass_improved" in tr["drum_attempted_algorithms"]
    assert manifest["recognition"]["drums"]["used_engine"] == "dsp_bandpass_improved"


def test_import_melodic_fallback_uses_pyin_when_basic_pitch_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=2.0, bpm=120.0)
    out = tmp_path / "MelodicFallback.auralsong"

    from aural_ingest import cli
    from aural_ingest.transcription import MelodicNote

    def basic_fail(_stem: Path) -> list[MelodicNote]:
        raise RuntimeError("missing model")

    def pyin_ok(_stem: Path) -> list[MelodicNote]:
        return [
            MelodicNote(t_on=0.0, t_off=0.1, pitch=64, velocity=90),
            MelodicNote(t_on=0.4, t_off=0.5, pitch=62, velocity=88),
        ]

    # cmd_import builds per-instrument stems (via guitar split) and routes them
    # through transcribe_all_melodic_stems, which builds its registry from
    # transcription.build_default_melodic_algorithm_registry(instrument=...).
    # Patch that so the basic_pitch -> pyin fallback is exercised. The legacy
    # single-track pass is intentionally skipped when instrument stems exist.
    import aural_ingest.transcription as transcription

    monkeypatch.setattr(
        transcription,
        "build_default_melodic_algorithm_registry",
        lambda *args, **kwargs: {
            "basic_pitch": basic_fail,
            "pyin": pyin_ok,
        },
    )

    args = type("Args", (), {})()
    args.input_audio_path = str(src)
    args.out = str(out)
    args.profile = "full"
    args.config = "{}"
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "combined_filter"
    args.melodic_method = "basic_pitch"
    args.shifts = 1
    args.multi_filter = False

    assert cli.cmd_import(args) == 0
    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    tr = manifest["pipeline"]["transcription"]
    assert tr["melodic_method_used"] == "pyin"
    assert "pyin" in tr["melodic_attempted_methods"]
    assert manifest["recognition"]["melodic"]["used_engine"] == "pyin"


def test_import_song_id_changes_when_drum_engine_changes(tmp_path: Path) -> None:
    src = tmp_path / "src.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=2.0, bpm=120.0)
    out_a = tmp_path / "Combined.auralsong"
    out_b = tmp_path / "Aural.auralsong"

    from aural_ingest.cli import cmd_import

    args_a = type("Args", (), {})()
    args_a.input_audio_path = str(src)
    args_a.out = str(out_a)
    args_a.profile = "full"
    args_a.config = "{}"
    args_a.title = None
    args_a.artist = None
    args_a.duration_sec = None
    args_a.drum_filter = "combined_filter"
    args_a.melodic_method = "auto"
    args_a.shifts = 1
    args_a.multi_filter = False

    args_b = type("Args", (), {})()
    args_b.input_audio_path = str(src)
    args_b.out = str(out_b)
    args_b.profile = "full"
    args_b.config = "{}"
    args_b.title = None
    args_b.artist = None
    args_b.duration_sec = None
    args_b.drum_filter = "aural_onset"
    args_b.melodic_method = "auto"
    args_b.shifts = 1
    args_b.multi_filter = False

    assert cmd_import(args_a) == 0
    assert cmd_import(args_b) == 0

    manifest_a = json.loads((out_a / "manifest.json").read_text("utf-8"))
    manifest_b = json.loads((out_b / "manifest.json").read_text("utf-8"))

    assert manifest_a["song_id"] != manifest_b["song_id"]


def _write_ieee_float_wav(path: Path, *, sr: int, duration_sec: float, channels: int = 2) -> None:
    """Write a 32-bit IEEE float WAV (format code 3) — the format that broke
    ``wave.open`` with ``wave.Error: unknown format: 3`` on Ableton master bounces."""

    n_frames = int(sr * duration_sec)
    bits_per_sample = 32
    bytes_per_sample = bits_per_sample // 8
    block_align = channels * bytes_per_sample
    byte_rate = sr * block_align
    data_size = n_frames * block_align

    # fmt chunk size 16 (standard PCM-sized chunk reused with format code 3 — what
    # Python's wave module sees and rejects.)
    fmt_chunk = struct.pack(
        "<HHIIHH",
        3,  # IEEE_FLOAT
        channels,
        sr,
        byte_rate,
        block_align,
        bits_per_sample,
    )
    fmt_chunk_size = len(fmt_chunk)
    riff_size = 4 + (8 + fmt_chunk_size) + (8 + data_size)

    with path.open("wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", riff_size))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<I", fmt_chunk_size))
        f.write(fmt_chunk)
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        for i in range(n_frames):
            t = float(i) / float(sr)
            sample = 0.4 * math.sin(2.0 * math.pi * 440.0 * t)
            for _ in range(channels):
                f.write(struct.pack("<f", float(sample)))


def test_decode_to_wav_falls_back_to_ffmpeg_for_ieee_float_wav(tmp_path: Path) -> None:
    """Regression: DAW master bounces are routinely WAV format code 3 (IEEE 32-bit
    float). Python's stdlib wave module rejects them with ``unknown format: 3`` and
    the import previously aborted at the decode_audio stage. _decode_to_wav must
    detect that case and re-encode via ffmpeg so the pipeline keeps running."""

    from aural_ingest import cli

    if cli._resolve_ffmpeg_path() is None:
        pytest.skip("ffmpeg not available on this host; non-PCM WAV fallback requires ffmpeg")

    src = tmp_path / "float32.wav"
    _write_ieee_float_wav(src, sr=44_100, duration_sec=0.5, channels=2)
    dst = tmp_path / "out.wav"

    duration, sr = cli._decode_to_wav(src, dst, target_sr=48_000)

    assert dst.is_file()
    assert sr == 48_000
    assert duration == pytest.approx(0.5, abs=0.05)

    # The output must be a real PCM16 wav that the stdlib wave module accepts.
    with wave.open(str(dst), "rb") as w:
        assert w.getsampwidth() == 2
        assert w.getnchannels() == 1
        assert w.getframerate() == 48_000


def test_decode_to_wav_rejects_ieee_float_wav_without_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ffmpeg available, a non-PCM WAV must surface a clear error instead
    of leaking ``wave.Error: unknown format: 3`` to the caller."""

    from aural_ingest import cli

    src = tmp_path / "float32.wav"
    _write_ieee_float_wav(src, sr=44_100, duration_sec=0.25, channels=1)
    dst = tmp_path / "out.wav"

    monkeypatch.setattr(cli, "_resolve_ffmpeg_path", lambda: None)

    with pytest.raises(RuntimeError, match="ffmpeg"):
        cli._decode_to_wav(src, dst, target_sr=48_000)
