import subprocess
import sys
import types
from pathlib import Path

import pytest

from aural_ingest.piano_benchmark import write_melodic_notes_midi
from aural_ingest.transcription import MelodicNote


def _vlq(value: int) -> bytes:
    parts = [value & 0x7F]
    value >>= 7
    while value:
        parts.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(parts))


def _track(events: bytes) -> bytes:
    return b"MTrk" + len(events).to_bytes(4, "big") + events


def _midi(tracks: list[bytes], *, division: int = 480) -> bytes:
    return (
        b"MThd"
        + (6).to_bytes(4, "big")
        + (1).to_bytes(2, "big")
        + len(tracks).to_bytes(2, "big")
        + division.to_bytes(2, "big")
        + b"".join(tracks)
    )


def _meta(delta: int, meta_type: int, payload: bytes = b"") -> bytes:
    return _vlq(delta) + bytes([0xFF, meta_type]) + _vlq(len(payload)) + payload


def _event(delta: int, payload: bytes) -> bytes:
    return _vlq(delta) + payload


def _write_test_midi(path: Path) -> None:
    write_melodic_notes_midi(
        [
            MelodicNote(t_on=0.0, t_off=0.25, pitch=20, velocity=90, instrument="keys"),
            MelodicNote(t_on=0.1, t_off=0.4, pitch=60, velocity=84, instrument="keys"),
            MelodicNote(t_on=0.2, t_off=0.5, pitch=109, velocity=90, instrument="keys"),
        ],
        path,
    )


def test_decode_midi_notes_clamps_to_88_key_range(tmp_path: Path) -> None:
    from aural_ingest.algorithms.piano_midi import decode_midi_notes

    midi = tmp_path / "external.mid"
    _write_test_midi(midi)

    notes = decode_midi_notes(midi, instrument="keys")

    assert [(note.pitch, note.velocity, note.instrument) for note in notes] == [(60, 84, "keys")]


def test_decode_midi_notes_filters_auralsong_tracks_by_instrument(tmp_path: Path) -> None:
    from aural_ingest import cli
    from aural_ingest.algorithms.piano_midi import decode_midi_notes

    midi = tmp_path / "auralsong-notes.mid"
    midi.write_bytes(
        cli._build_notes_mid_bytes(
            bpm=120.0,
            beats=[{"t": 0.0, "bar": 0, "beat": 0}, {"t": 0.5, "bar": 0, "beat": 1}],
            sections=[],
            drum_events=[],
            instrument_tracks={
                "bass": [
                    MelodicNote(t_on=0.0, t_off=0.5, pitch=40, velocity=82, instrument="bass"),
                ],
                "keys": [
                    MelodicNote(t_on=0.25, t_off=0.75, pitch=64, velocity=90, instrument="keys"),
                ],
            },
        )
    )

    keys = decode_midi_notes(midi, instrument="keys")
    bass = decode_midi_notes(midi, instrument="bass")

    assert [(note.pitch, note.velocity, note.instrument) for note in keys] == [(64, 90, "keys")]
    assert [(note.pitch, note.velocity, note.instrument) for note in bass] == [(40, 82, "bass")]


@pytest.mark.parametrize("bpm", [60.0, 180.0])
def test_decode_midi_notes_applies_auralsong_tempo_map_at_tick_zero(
    tmp_path: Path,
    bpm: float,
) -> None:
    from aural_ingest import cli
    from aural_ingest.algorithms.piano_midi import decode_midi_notes

    midi = tmp_path / f"auralsong-notes-{bpm:g}bpm.mid"
    midi.write_bytes(
        cli._build_notes_mid_bytes(
            bpm=bpm,
            beats=[],
            sections=[],
            drum_events=[],
            instrument_tracks={
                "keys": [
                    MelodicNote(t_on=2.0, t_off=3.0, pitch=64, velocity=90, instrument="keys"),
                ],
            },
        )
    )

    notes = decode_midi_notes(midi, instrument="keys")

    assert len(notes) == 1
    assert notes[0].t_on == pytest.approx(2.0, abs=0.00001)
    assert notes[0].t_off == pytest.approx(3.0, abs=0.00001)
    assert (notes[0].pitch, notes[0].velocity, notes[0].instrument) == (64, 90, "keys")


def test_auralsong_midi_writer_preserves_overlapping_same_pitch_notes(tmp_path: Path) -> None:
    from aural_ingest import cli
    from aural_ingest.algorithms.piano_midi import decode_midi_notes
    from aural_ingest.piano_benchmark import parse_piano_midi_reference

    midi = tmp_path / "overlapping-same-pitch.mid"
    midi.write_bytes(
        cli._build_notes_mid_bytes(
            bpm=180.0,
            beats=[],
            sections=[],
            drum_events=[],
            instrument_tracks={
                "keys": [
                    MelodicNote(t_on=0.0, t_off=1.0, pitch=64, velocity=70, instrument="keys"),
                    MelodicNote(t_on=0.5, t_off=1.5, pitch=64, velocity=80, instrument="keys"),
                ],
            },
        )
    )

    decoded = decode_midi_notes(midi, instrument="keys")
    benchmark = parse_piano_midi_reference(midi, role="keys")

    assert [(note.t_on, note.t_off, note.pitch, note.velocity) for note in decoded] == [
        (0.0, pytest.approx(1.0, abs=0.00001), 64, 70),
        (pytest.approx(0.5, abs=0.00001), pytest.approx(1.5, abs=0.00001), 64, 80),
    ]
    assert [(event.time, event.time + event.duration, event.pitch, event.velocity) for event in benchmark] == [
        (0.0, pytest.approx(1.0, abs=0.00001), 64, 70),
        (pytest.approx(0.5, abs=0.00001), pytest.approx(1.5, abs=0.00001), 64, 80),
    ]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"not-midi", "piano midi output missing MThd header"),
        (
            b"MThd" + (5).to_bytes(4, "big") + b"\x00\x00\x00\x01\x01\x00",
            "piano midi output has invalid header length",
        ),
        (
            b"MThd"
            + (6).to_bytes(4, "big")
            + (1).to_bytes(2, "big")
            + (1).to_bytes(2, "big")
            + (0x8000).to_bytes(2, "big")
            + _track(_meta(0, 0x2F)),
            "smpte midi timing is not supported for piano outputs",
        ),
        (
            b"MThd"
            + (6).to_bytes(4, "big")
            + (1).to_bytes(2, "big")
            + (1).to_bytes(2, "big")
            + (480).to_bytes(2, "big"),
            "piano midi output missing MTrk chunk",
        ),
        (
            b"MThd"
            + (6).to_bytes(4, "big")
            + (1).to_bytes(2, "big")
            + (1).to_bytes(2, "big")
            + (480).to_bytes(2, "big")
            + b"MTrk"
            + (4).to_bytes(4, "big")
            + b"\x00",
            "piano midi track chunk is truncated",
        ),
        (_midi([_track(b"\x81")]), "midi variable-length quantity is truncated"),
        (_midi([_track(b"\x00\x40")]), "midi running status encountered without prior status"),
        (_midi([_track(b"\x00\xff")]), "truncated midi meta event"),
        (_midi([_track(b"\x00\x90")]), "truncated midi channel event"),
        (_midi([_track(b"\x00\x90\x40")]), "truncated midi channel event"),
    ],
)
def test_decode_midi_notes_rejects_malformed_midi(tmp_path: Path, payload: bytes, message: str) -> None:
    from aural_ingest.algorithms.piano_midi import decode_midi_notes

    midi = tmp_path / "bad.mid"
    midi.write_bytes(payload)

    with pytest.raises(ValueError, match=message):
        decode_midi_notes(midi)


def test_decode_midi_notes_handles_running_status_and_unmatched_track_fallback(
    tmp_path: Path,
) -> None:
    from aural_ingest.algorithms.piano_midi import decode_midi_notes

    midi = tmp_path / "fallback-running-status.mid"
    events = b"".join(
        [
            _meta(0, 0x03, b"Other"),
            _event(0, bytes([0xC0, 0x01])),
            _event(0, bytes([0xF0, 0x01, 0x7E])),
            _event(0, bytes([0x90, 60, 70])),
            _event(10, bytes([60, 80])),
            _event(10, bytes([0x80, 60, 0])),
            _event(0, bytes([0x90, 62, 90])),
            _meta(10, 0x2F),
        ]
    )
    midi.write_bytes(_midi([_track(events)]))

    notes = decode_midi_notes(midi, instrument="keys")

    assert [(note.t_on, note.t_off, note.pitch, note.velocity) for note in notes] == [
        (0.0, 0.010417, 60, 70),
        (0.010417, 0.020833, 60, 80),
        (0.020833, 0.03125, 62, 90),
    ]


def test_decode_midi_notes_handles_empty_and_nameless_tracks(tmp_path: Path) -> None:
    from aural_ingest.algorithms.piano_midi import decode_midi_notes

    nameless_events = b"".join(
        [
            _event(0, bytes([0x90, 60, 70])),
            _event(10, bytes([0x80, 60, 0])),
            _meta(0, 0x2F),
        ]
    )
    noise_events = b"".join(
        [
            _meta(0, 0x03, b"Keys"),
            _event(0, bytes([0xB0, 64, 127])),
            _event(0, bytes([0x80, 62, 0])),
            _meta(0, 0x2F),
        ]
    )
    midi = tmp_path / "empty-and-nameless.mid"
    midi.write_bytes(_midi([_track(b""), _track(b"\x00"), _track(noise_events), _track(nameless_events)]))

    keys = decode_midi_notes(midi, instrument="keys")
    blank_instrument = decode_midi_notes(midi, instrument="")

    assert [(note.t_on, note.t_off, note.pitch, note.velocity, note.instrument) for note in keys] == [
        (0.0, 0.010417, 60, 70, "keys")
    ]
    assert [(note.t_on, note.t_off, note.pitch, note.velocity, note.instrument) for note in blank_instrument] == [
        (0.0, 0.010417, 60, 70, "")
    ]


def test_decode_midi_notes_skips_zero_duration_notes(tmp_path: Path) -> None:
    from aural_ingest.algorithms.piano_midi import decode_midi_notes

    midi = tmp_path / "zero-duration.mid"
    events = b"".join(
        [
            _meta(0, 0x03, b"Keys"),
            _event(0, bytes([0x90, 60, 70])),
            _event(0, bytes([0x80, 60, 0])),
            _meta(0, 0x2F),
        ]
    )
    midi.write_bytes(_midi([_track(events)]))

    assert decode_midi_notes(midi, instrument="keys") == []


def test_piano_transkun_runs_cli_and_decodes_midi(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest.algorithms import piano_transkun

    stem = tmp_path / "audio.wav"
    stem.write_bytes(b"audio")
    monkeypatch.setattr(piano_transkun, "_ensure_transkun_available", lambda: None)

    def fake_run(cmd, **_kwargs):
        assert cmd[:3] == [sys.executable, "-m", "transkun.transcribe"]
        _write_test_midi(Path(cmd[4]))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(piano_transkun.subprocess, "run", fake_run)

    notes = piano_transkun.transcribe(stem, instrument="keys")

    assert [note.pitch for note in notes] == [60]


def test_piano_transkun_missing_package_surfaces_clear_error(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest.algorithms import piano_transkun

    monkeypatch.setattr(
        piano_transkun.importlib,
        "import_module",
        lambda _name: (_ for _ in ()).throw(ModuleNotFoundError("missing")),
    )

    with pytest.raises(RuntimeError, match="transkun"):
        piano_transkun.transcribe(tmp_path / "audio.wav")


def test_piano_pti_runs_api_and_decodes_midi(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest.algorithms import piano_pti

    stem = tmp_path / "audio.wav"
    stem.write_bytes(b"audio")
    checkpoint = tmp_path / "checkpoint.pth"
    checkpoint.write_bytes(b"checkpoint")
    monkeypatch.setenv("AURAL_PIANO_PTI_CHECKPOINT", str(checkpoint))
    # _device() now flows through select_device(); pin it so the assertion is
    # deterministic regardless of the host's CUDA availability.
    monkeypatch.setenv("AURAL_PIANO_DEVICE", "cpu")

    class FakePianoTranscription:
        def __init__(self, *, device, checkpoint_path, model_type=None):
            assert device == "cpu"
            assert checkpoint_path == str(checkpoint)
            # piano_pti now pins model_type="Regress_onset_offset_frame_velocity_CRNN"
            # by default so the Edwards-robust Kong checkpoint loads correctly.
            assert model_type == "Regress_onset_offset_frame_velocity_CRNN"

        def transcribe(self, _audio, output_midi_path):
            _write_test_midi(Path(output_midi_path))

    fake_pti = types.SimpleNamespace(sample_rate=16_000, PianoTranscription=FakePianoTranscription)
    fake_librosa = types.SimpleNamespace(load=lambda *, path, sr, mono: ([0.0] * 16, sr))

    def fake_import(name):
        if name == "piano_transcription_inference":
            return fake_pti
        if name == "librosa":
            return fake_librosa
        if name == "torch":
            raise ModuleNotFoundError(name)  # exercise _torch_load_compat passthrough
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(piano_pti.importlib, "import_module", fake_import)

    notes = piano_pti.transcribe(stem, instrument="keys")

    assert [note.pitch for note in notes] == [60]


def test_piano_pti_requires_checkpoint_or_explicit_download(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest.algorithms import piano_pti

    fake_pti = types.SimpleNamespace(sample_rate=16_000, PianoTranscription=object)
    fake_librosa = types.SimpleNamespace(load=lambda *, path, sr, mono: ([0.0] * 16, sr))
    monkeypatch.setattr(
        piano_pti.importlib,
        "import_module",
        lambda name: fake_pti if name == "piano_transcription_inference" else fake_librosa,
    )
    monkeypatch.delenv("AURAL_PIANO_PTI_CHECKPOINT", raising=False)
    monkeypatch.delenv("AURAL_PIANO_PTI_ALLOW_DOWNLOAD", raising=False)
    # Force auto-discovery to find nothing so we exercise the "no checkpoint
    # anywhere" path (otherwise the test fails on machines that have the
    # bundled checkpoint under assets/models/piano_pti/).
    monkeypatch.setattr(piano_pti, "_resolve_bundled_checkpoint", lambda: None)

    with pytest.raises(RuntimeError, match="AURAL_PIANO_PTI_CHECKPOINT"):
        piano_pti.transcribe(tmp_path / "audio.wav")


def test_piano_hft_runs_configured_command_and_decodes_midi(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest.algorithms import piano_hft

    stem = tmp_path / "audio.wav"
    stem.write_bytes(b"audio")
    checkpoint = tmp_path / "model.pkl"
    checkpoint.write_bytes(b"checkpoint")
    monkeypatch.setenv("AURAL_PIANO_HFT_CHECKPOINT", str(checkpoint))
    monkeypatch.setenv("AURAL_PIANO_HFT_COMMAND", "hft --audio {audio} --midi {midi} --checkpoint {checkpoint}")

    def fake_run(cmd, **_kwargs):
        _write_test_midi(Path(cmd[cmd.index("--midi") + 1]))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(piano_hft.subprocess, "run", fake_run)

    notes = piano_hft.transcribe(stem, instrument="keys")

    assert [note.pitch for note in notes] == [60]


def test_piano_hft_requires_checkpoint_and_command(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest.algorithms import piano_hft

    monkeypatch.delenv("AURAL_PIANO_HFT_CHECKPOINT", raising=False)
    monkeypatch.delenv("AURAL_PIANO_HFT_COMMAND", raising=False)

    with pytest.raises(RuntimeError, match="AURAL_PIANO_HFT_CHECKPOINT"):
        piano_hft.transcribe(tmp_path / "audio.wav")


def test_clean_research_registry_methods_apply_cleanup(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest.algorithms import (
        piano_cleanup,
        piano_d3rm,
        piano_hft,
        piano_pti,
        piano_transkun,
    )
    from aural_ingest.transcription import build_default_melodic_algorithm_registry

    stem = tmp_path / "audio.wav"
    stem.write_bytes(b"audio")
    raw = [MelodicNote(t_on=0.0, t_off=0.1, pitch=60, velocity=70, instrument="keys")]
    cleaned = [MelodicNote(t_on=0.0, t_off=0.2, pitch=60, velocity=80, instrument="keys")]
    calls: list[str] = []

    monkeypatch.setattr(piano_transkun, "transcribe", lambda _path, *, instrument="keys": raw)
    monkeypatch.setattr(piano_pti, "transcribe", lambda _path, *, instrument="keys": raw)
    monkeypatch.setattr(piano_hft, "transcribe", lambda _path, *, instrument="keys": raw)
    monkeypatch.setattr(piano_d3rm, "transcribe", lambda _path, *, instrument="keys": raw)

    def fake_cleanup(notes, *, stem_path=None, instrument="keys"):
        calls.append(instrument)
        assert notes == raw
        return cleaned

    monkeypatch.setattr(piano_cleanup, "cleanup_notes", fake_cleanup)

    registry = build_default_melodic_algorithm_registry(instrument="keys")

    assert registry["piano_transkun_clean"](stem) == cleaned
    assert registry["piano_pti_clean"](stem) == cleaned
    assert registry["piano_hft_clean"](stem) == cleaned
    assert registry["piano_d3rm_clean"](stem) == cleaned
    assert calls == ["keys", "keys", "keys", "keys"]


def test_piano_basic_pitch_registry_methods_use_strict_basic_pitch(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest.algorithms import melodic_basic_pitch, piano_cleanup
    from aural_ingest.transcription import build_default_melodic_algorithm_registry

    stem = tmp_path / "audio.wav"
    stem.write_bytes(b"audio")
    raw = [MelodicNote(t_on=0.0, t_off=0.4, pitch=35, velocity=82, instrument="keys")]
    cleaned = [MelodicNote(t_on=0.0, t_off=0.5, pitch=35, velocity=88, instrument="keys")]
    allow_fallback_values: list[bool] = []
    threshold_calls: list[dict[str, float]] = []
    cleanup_calls = 0

    def fake_basic_pitch(_path, *, model_path=None, instrument="keys", allow_fallback=True, **kwargs):
        allow_fallback_values.append(allow_fallback)
        threshold_calls.append(dict(kwargs))
        assert instrument == "keys"
        return raw

    def fake_cleanup(notes, *, stem_path=None, instrument="keys"):
        nonlocal cleanup_calls
        cleanup_calls += 1
        assert notes == raw
        assert stem_path == stem
        assert instrument == "keys"
        return cleaned

    monkeypatch.setattr(melodic_basic_pitch, "transcribe", fake_basic_pitch)
    monkeypatch.setattr(piano_cleanup, "cleanup_notes", fake_cleanup)

    registry = build_default_melodic_algorithm_registry(instrument="keys")

    assert registry["piano_basic_pitch_playable"](stem) == raw
    assert registry["piano_basic_pitch"](stem) == raw
    assert registry["piano_basic_pitch_clean"](stem) == cleaned
    assert allow_fallback_values
    assert all(value is False for value in allow_fallback_values)
    assert threshold_calls[0] == {
        "onset_threshold": 0.6,
        "frame_threshold": 0.25,
        "minimum_note_length_ms": 100.0,
    }
    assert threshold_calls[-2:] == [{}, {}]
    assert cleanup_calls == 1


def test_piano_auto_prefers_playable_strict_basic_pitch_before_optional_models_and_heuristic(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest.algorithms import (
        melodic_basic_pitch,
        piano_cleanup,
        piano_polyphonic,
        piano_pti,
    )
    from aural_ingest.transcription import build_default_melodic_algorithm_registry

    stem = tmp_path / "audio.wav"
    stem.write_bytes(b"audio")
    basic_notes = [
        MelodicNote(t_on=0.0, t_off=0.5, pitch=35, velocity=84, instrument="keys"),
        MelodicNote(t_on=0.1, t_off=0.6, pitch=60, velocity=88, instrument="keys"),
        MelodicNote(t_on=0.2, t_off=0.7, pitch=64, velocity=88, instrument="keys"),
    ]
    basic_allow_fallback_values: list[bool] = []
    basic_threshold_calls: list[dict[str, float]] = []
    heuristic_calls: list[str] = []
    pti_calls: list[str] = []

    monkeypatch.setattr(
        piano_pti,
        "transcribe_consensus",
        lambda *args, **kwargs: pti_calls.append("piano_pti_consensus"),
    )
    monkeypatch.setattr(
        piano_pti,
        "transcribe",
        lambda *args, **kwargs: pti_calls.append("piano_pti"),
    )

    def fake_basic_pitch(_path, *, model_path=None, instrument="keys", allow_fallback=True, **kwargs):
        basic_allow_fallback_values.append(allow_fallback)
        basic_threshold_calls.append(dict(kwargs))
        assert allow_fallback is False
        return basic_notes

    def fake_polyphonic(_path, *, instrument="keys"):
        heuristic_calls.append("piano_polyphonic")
        return []

    monkeypatch.setattr(melodic_basic_pitch, "transcribe", fake_basic_pitch)
    monkeypatch.setattr(piano_polyphonic, "transcribe", fake_polyphonic)
    monkeypatch.setattr(
        piano_cleanup,
        "cleanup_notes",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("piano_auto should not use clean variant first")),
    )

    registry = build_default_melodic_algorithm_registry(instrument="keys")
    notes = registry["piano_auto"](stem)

    assert notes == basic_notes
    assert basic_allow_fallback_values
    assert all(value is False for value in basic_allow_fallback_values)
    assert basic_threshold_calls[0] == {
        "onset_threshold": 0.6,
        "frame_threshold": 0.25,
        "minimum_note_length_ms": 100.0,
    }
    assert pti_calls == []
    assert heuristic_calls == []
    last_run = getattr(registry["piano_auto"], "last_run")
    assert last_run["attempted"] == ["piano_basic_pitch_playable"]
    assert "basic_pitch" not in last_run["attempted"]


def test_piano_pti_uses_bundled_checkpoint_when_env_unset(monkeypatch, tmp_path: Path) -> None:
    """When no env var is set, piano_pti should auto-discover the bundled checkpoint."""
    import types

    from aural_ingest.algorithms import piano_pti

    bundled = tmp_path / "high_resolution_MAESTRO_augmentations.pth"
    bundled.write_bytes(b"weights")
    stem = tmp_path / "audio.wav"
    stem.write_bytes(b"audio")
    monkeypatch.delenv("AURAL_PIANO_PTI_CHECKPOINT", raising=False)
    monkeypatch.delenv("AURAL_PIANO_PTI_ALLOW_DOWNLOAD", raising=False)
    monkeypatch.setattr(piano_pti, "_resolve_bundled_checkpoint", lambda: bundled)

    seen: dict[str, str] = {}

    class FakePianoTranscription:
        def __init__(self, *, device, checkpoint_path, model_type=None):
            seen["checkpoint"] = checkpoint_path

        def transcribe(self, _audio, output_midi_path):
            _write_test_midi(Path(output_midi_path))

    fake_pti = types.SimpleNamespace(sample_rate=16_000, PianoTranscription=FakePianoTranscription)
    fake_librosa = types.SimpleNamespace(load=lambda *, path, sr, mono: ([0.0] * 16, sr))
    monkeypatch.setattr(
        piano_pti.importlib,
        "import_module",
        lambda name: fake_pti if name == "piano_transcription_inference" else fake_librosa,
    )

    notes = piano_pti.transcribe(stem, instrument="keys")

    assert [note.pitch for note in notes] == [60]
    assert seen["checkpoint"] == str(bundled)


def test_resolve_piano_pti_checkpoint_path_prefers_robust_filename(tmp_path: Path) -> None:
    from aural_ingest.transcription import (
        PIANO_PTI_ROBUST_CHECKPOINT_FILENAME,
        resolve_piano_pti_checkpoint_path,
    )

    root = tmp_path
    piano_dir = root / "piano_pti"
    piano_dir.mkdir()
    (piano_dir / "older_model.pth").write_bytes(b"")
    primary = piano_dir / PIANO_PTI_ROBUST_CHECKPOINT_FILENAME
    primary.write_bytes(b"")

    assert resolve_piano_pti_checkpoint_path([root]) == primary


def test_resolve_piano_pti_checkpoint_path_falls_back_to_any_pth(tmp_path: Path) -> None:
    from aural_ingest.transcription import resolve_piano_pti_checkpoint_path

    piano_dir = tmp_path / "piano_pti"
    piano_dir.mkdir()
    fallback = piano_dir / "some_checkpoint.pth"
    fallback.write_bytes(b"")

    assert resolve_piano_pti_checkpoint_path([tmp_path]) == fallback


def test_piano_d3rm_runs_configured_command_and_decodes_midi(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest.algorithms import piano_d3rm

    stem = tmp_path / "audio.wav"
    stem.write_bytes(b"audio")
    checkpoint = tmp_path / "d3rm.ckpt"
    checkpoint.write_bytes(b"checkpoint")
    monkeypatch.setenv("AURAL_PIANO_D3RM_CHECKPOINT", str(checkpoint))
    monkeypatch.setenv(
        "AURAL_PIANO_D3RM_COMMAND",
        "d3rm --audio {audio} --midi {midi} --checkpoint {checkpoint}",
    )

    def fake_run(cmd, **_kwargs):
        _write_test_midi(Path(cmd[cmd.index("--midi") + 1]))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(piano_d3rm.subprocess, "run", fake_run)

    notes = piano_d3rm.transcribe(stem, instrument="keys")

    assert [note.pitch for note in notes] == [60]


def test_piano_d3rm_requires_command_and_checkpoint(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest.algorithms import piano_d3rm

    monkeypatch.delenv("AURAL_PIANO_D3RM_CHECKPOINT", raising=False)
    monkeypatch.delenv("AURAL_PIANO_D3RM_COMMAND", raising=False)
    monkeypatch.setattr(piano_d3rm, "_resolve_bundled_checkpoint", lambda: None)

    with pytest.raises(RuntimeError, match="AURAL_PIANO_D3RM_CHECKPOINT"):
        piano_d3rm.transcribe(tmp_path / "audio.wav")


def test_piano_d3rm_command_with_config_placeholder(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest.algorithms import piano_d3rm

    stem = tmp_path / "audio.wav"
    stem.write_bytes(b"audio")
    checkpoint = tmp_path / "d3rm.ckpt"
    checkpoint.write_bytes(b"checkpoint")
    config = tmp_path / "D3RM_cli.yaml"
    config.write_text("placeholder: true\n", encoding="utf-8")
    monkeypatch.setenv("AURAL_PIANO_D3RM_CHECKPOINT", str(checkpoint))
    monkeypatch.setenv(
        "AURAL_PIANO_D3RM_COMMAND",
        "d3rm --audio {audio} --midi {midi} --checkpoint {checkpoint} -c {config}",
    )

    captured: dict[str, list[str]] = {}

    def fake_run(cmd, **_kwargs):
        captured["cmd"] = cmd
        _write_test_midi(Path(cmd[cmd.index("--midi") + 1]))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(piano_d3rm.subprocess, "run", fake_run)

    notes = piano_d3rm.transcribe(stem, instrument="keys")

    assert [note.pitch for note in notes] == [60]
    cmd = captured["cmd"]
    assert str(config) in cmd


def test_piano_denoise_passthrough_on_invalid_audio(tmp_path: Path) -> None:
    """maybe_denoised_stem must return the original path when audio can't be loaded."""
    from aural_ingest.algorithms import piano_denoise

    stem = tmp_path / "audio.wav"
    stem.write_bytes(b"audio")  # not real audio

    with piano_denoise.maybe_denoised_stem(stem) as out:
        assert out == stem


def test_piano_denoise_passthrough_when_disabled(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest.algorithms import piano_denoise

    stem = tmp_path / "audio.wav"
    stem.write_bytes(b"audio")
    monkeypatch.setenv("AURAL_PIANO_DENOISE_STEM", "0")

    with piano_denoise.maybe_denoised_stem(stem) as out:
        assert out == stem
