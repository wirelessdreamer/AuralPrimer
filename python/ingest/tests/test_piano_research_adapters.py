import subprocess
import sys
import types
from pathlib import Path

import pytest

from aural_ingest.piano_benchmark import write_melodic_notes_midi
from aural_ingest.transcription import MelodicNote


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
