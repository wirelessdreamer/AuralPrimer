import json
import math
import struct
import wave
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


@pytest.mark.parametrize("bpm", [90.0, 120.0])
def test_import_generates_valid_songpack(tmp_path: Path, bpm: float) -> None:
    # Arrange
    src = tmp_path / "src.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=8.0, bpm=bpm)
    out = tmp_path / "Out.songpack"

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
    assert (out / "audio/stems/lead_guitar.wav").is_file()
    assert (out / "audio/stems/rhythm_guitar.wav").is_file()

    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    assert manifest["assets"]["audio"]["mix_path"] == "audio/mix.wav"
    assert manifest["assets"]["midi"]["notes_path"] == "features/notes.mid"
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
    # Melodic: channel 1 note-on status (0x90)
    assert _count_note_on(notes_mid, 0x90) > 0


def test_import_guitar_split_preserves_shape_and_reconstructs_source(tmp_path: Path) -> None:
    src = tmp_path / "src_stereo.wav"
    _write_dual_tone_wav(src, sr=48_000, duration_sec=2.0)
    out = tmp_path / "Split.songpack"

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
    out = tmp_path / "CfgSplit.songpack"

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
    out = tmp_path / "DrumStem.songpack"

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


def test_import_uses_demucs_separated_drums_and_guitar_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src_mix = tmp_path / "mix.wav"
    _write_clicktrack_wav(src_mix, sr=48_000, duration_sec=2.0, bpm=120.0)
    out = tmp_path / "Separated.songpack"

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
    out = tmp_path / "Mt3MissingStem.songpack"

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

    from aural_ingest import cli

    monkeypatch.chdir(sidecar_dir)
    monkeypatch.setattr(cli.sys, "executable", str(sidecar_dir / "aural_ingest.exe"))
    monkeypatch.setattr(cli, "__file__", str(sidecar_dir / "embedded" / "aural_ingest" / "cli.py"))

    candidates = cli._default_demucs_modelpack_candidates()
    assert expected in candidates


def test_validate_passes_on_generated_songpack(tmp_path: Path) -> None:
    src = tmp_path / "src.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=4.0, bpm=120.0)
    out = tmp_path / "Out.songpack"

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
    validate_args.songpack_dir = str(out)

    assert cmd_validate(validate_args) == 0


def test_nonwav_input_requires_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # If ffmpeg is not present, non-wav sources must fail deterministically.
    src = tmp_path / "src.mp3"
    src.write_bytes(b"not really an mp3")
    out = tmp_path / "Out.songpack"

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
    out = tmp_path / "Out.songpack"

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
    out = tmp_path / "OutOrder.songpack"

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


def test_import_dtx_uses_chart_folder_audio(tmp_path: Path) -> None:
    song_dir = tmp_path / "song_dtx"
    song_dir.mkdir(parents=True, exist_ok=True)
    dtx = song_dir / "chart.dtx"
    src = song_dir / "mix.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=2.0, bpm=120.0)
    dtx.write_text("; minimal chart\n", encoding="utf-8")
    out = tmp_path / "OutDtx.songpack"

    from aural_ingest.cli import cmd_import_dtx

    args = type("Args", (), {})()
    args.dtx_path = str(dtx)
    args.out = str(out)
    args.profile = "full"
    args.config = json.dumps({"ingest_timestamp": "2000-01-01T00:00:00Z"})
    args.title = None
    args.artist = "DTX Artist"
    args.duration_sec = None

    rc = cmd_import_dtx(args)
    assert rc == 0
    assert (out / "manifest.json").is_file()
    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    assert manifest["title"] == "chart"
    assert manifest["artist"] == "DTX Artist"


def test_import_persists_transcription_options_into_manifest(tmp_path: Path) -> None:
    src = tmp_path / "src.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=2.0, bpm=120.0)
    out = tmp_path / "Opts.songpack"

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
    assert tr["shifts"] == 2
    assert tr["multi_filter"] is True
    assert recognition["drums"]["requested_engine"] == "dsp_spectral_flux"
    assert recognition["drums"]["used_engine"] == "dsp_spectral_flux"
    assert recognition["melodic"]["requested_engine"] == "basic_pitch"
    assert recognition["melodic"]["used_engine"] in {"basic_pitch", "pyin"}


def test_import_unknown_drum_filter_falls_back_to_default_engine_and_records_warning(tmp_path: Path) -> None:
    src = tmp_path / "src.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=2.0, bpm=120.0)
    out = tmp_path / "UnknownFilter.songpack"

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
    assert tr["drum_filter"] == "adaptive_beat_grid"
    assert tr["drum_filter_requested"] == "legacy_unknown_filter"
    assert tr["drum_filter_used"] == "adaptive_beat_grid"
    assert tr["warnings"]
    assert manifest["recognition"]["drums"]["requested_engine"] == "legacy_unknown_filter"
    assert manifest["recognition"]["drums"]["used_engine"] == "adaptive_beat_grid"


def test_import_auto_melodic_no_longer_requires_external_basic_pitch_model(tmp_path: Path) -> None:
    src = tmp_path / "src.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=2.0, bpm=120.0)
    out = tmp_path / "AutoMelodic.songpack"

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


def test_import_fallback_chain_uses_next_algorithm_when_requested_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "src.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=2.0, bpm=120.0)
    out = tmp_path / "Fallback.songpack"

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
    out = tmp_path / "MelodicFallback.songpack"

    from aural_ingest import cli
    from aural_ingest.transcription import MelodicNote

    def basic_fail(_stem: Path) -> list[MelodicNote]:
        raise RuntimeError("missing model")

    def pyin_ok(_stem: Path) -> list[MelodicNote]:
        return [MelodicNote(t_on=0.0, t_off=0.1, pitch=64, velocity=90)]

    monkeypatch.setattr(
        cli,
        "build_default_melodic_algorithm_registry",
        lambda: {
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
    out_a = tmp_path / "Combined.songpack"
    out_b = tmp_path / "Aural.songpack"

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
