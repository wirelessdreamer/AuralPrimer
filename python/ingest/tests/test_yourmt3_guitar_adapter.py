from __future__ import annotations

from contextlib import contextmanager
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from aural_ingest.ground_truth_benchmark import get_melodic_algorithm
from aural_ingest.transcription import MelodicNote


def test_yourmt3_guitar_imports_without_checkpoint() -> None:
    module = importlib.import_module("aural_ingest.algorithms.yourmt3_guitar")

    assert module.ENGINE_ID == "yourmt3_guitar"
    assert callable(module.transcribe)


def test_yourmt3_guitar_transcribe_delegates_to_mt3_and_decodes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.yourmt3_guitar")
    stem = tmp_path / "guitar.wav"
    stem.write_bytes(b"not real audio; helper is monkeypatched")
    midi = object()
    expected = [
        MelodicNote(
            t_on=0.125,
            t_off=0.375,
            pitch=64,
            velocity=96,
            instrument="lead_guitar",
        )
    ]
    captured: dict[str, object] = {}

    def fake_transcribe_yourmt3_midi(stem_path: Path) -> object:
        captured["stem_path"] = stem_path
        return midi

    def fake_midi_to_melodic_notes(midi_object: object, *, instrument: str) -> list[MelodicNote]:
        captured["midi"] = midi_object
        captured["instrument"] = instrument
        return expected

    monkeypatch.setattr(module, "_transcribe_yourmt3_midi", fake_transcribe_yourmt3_midi)
    monkeypatch.setattr(module, "_midi_to_melodic_notes", fake_midi_to_melodic_notes)

    assert module.transcribe(stem, instrument="lead_guitar") == expected
    assert captured == {
        "stem_path": stem,
        "midi": midi,
        "instrument": "lead_guitar",
    }


def test_yourmt3_guitar_mt3_runner_uses_shared_modelpack_resolver_and_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.yourmt3_guitar")
    stem = tmp_path / "lead.wav"
    stem.write_bytes(b"not real audio; librosa is monkeypatched")
    checkpoint = tmp_path / "models" / "yourmt3" / "last.ckpt"
    modelpack_root = checkpoint.parent
    fake_midi = object()
    captured: dict[str, object] = {}

    class FakeAudio:
        def astype(self, dtype: str) -> tuple[str, str]:
            captured["astype"] = dtype
            return ("audio", dtype)

    def fake_load(path: str, *, sr: int, mono: bool) -> tuple[FakeAudio, int]:
        captured["librosa_load"] = (path, sr, mono)
        return FakeAudio(), 16000

    class FakeModel:
        def transcribe(self, audio: object, *, sr: int) -> object:
            captured["transcribe"] = (audio, sr)
            return fake_midi

    def fake_load_model(
        model_id: str,
        *,
        checkpoint_path: str,
        device: str,
        auto_download: bool,
    ) -> FakeModel:
        captured["load_model"] = (model_id, checkpoint_path, device, auto_download)
        return FakeModel()

    def fake_resolve(engine_id: str, *, stem_path: Path) -> dict[str, object]:
        captured["resolve"] = (engine_id, stem_path)
        return {
            "model_id": "yourmt3",
            "checkpoint_path_resolved": checkpoint,
            "modelpack_root": modelpack_root,
        }

    @contextmanager
    def noop_warnings():
        yield

    import aural_ingest.device as device
    import aural_ingest.mt3_compat as mt3_compat
    import aural_ingest.transcription as transcription

    monkeypatch.setitem(sys.modules, "librosa", SimpleNamespace(load=fake_load))
    monkeypatch.setitem(sys.modules, "mt3_infer", SimpleNamespace(load_model=fake_load_model))
    monkeypatch.setattr(transcription, "resolve_mt3_modelpack", fake_resolve)
    monkeypatch.setattr(mt3_compat, "ensure_mt3_transformers_compat", lambda: captured.setdefault("compat", True))
    monkeypatch.setattr(mt3_compat, "suppress_mt3_runtime_warnings", noop_warnings)
    monkeypatch.setattr(device, "select_device", lambda env_name: f"device:{env_name}")
    monkeypatch.setenv("MT3_CHECKPOINT_DIR", "old")

    assert module._transcribe_yourmt3_midi(stem) is fake_midi
    assert captured == {
        "resolve": ("yourmt3_drums", stem),
        "librosa_load": (str(stem), 16000, True),
        "compat": True,
        "load_model": ("yourmt3", str(checkpoint), "device:AURAL_MT3_DEVICE", False),
        "astype": "float32",
        "transcribe": (("audio", "float32"), 16000),
    }


def test_yourmt3_guitar_missing_checkpoint_raises_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.yourmt3_guitar")

    def missing_modelpack(_stem_path: Path) -> object:
        raise FileNotFoundError("missing modelpack")

    monkeypatch.setattr(module, "_transcribe_yourmt3_midi", missing_modelpack)

    with pytest.raises(RuntimeError, match="yourmt3_guitar") as exc_info:
        module.transcribe(tmp_path / "guitar.wav")
    assert isinstance(exc_info.value.__cause__, FileNotFoundError)


def test_yourmt3_guitar_decodes_saved_midi_with_requested_instrument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.yourmt3_guitar")
    expected = [
        MelodicNote(
            t_on=0.0,
            t_off=0.5,
            pitch=67,
            velocity=90,
            instrument="rhythm_guitar",
        )
    ]
    captured: dict[str, object] = {}

    class FakeMidi:
        def save(self, path: str) -> None:
            captured["save_path"] = Path(path)
            Path(path).write_bytes(b"MThd fake")

    def fake_decode_midi_notes(path: Path, *, instrument: str) -> list[MelodicNote]:
        captured["decode_path"] = path
        captured["decode_exists"] = path.is_file()
        captured["instrument"] = instrument
        return expected

    monkeypatch.setattr(
        "aural_ingest.algorithms.piano_midi.decode_midi_notes",
        fake_decode_midi_notes,
    )

    assert module._midi_to_melodic_notes(FakeMidi(), instrument="rhythm_guitar") == expected
    assert captured["save_path"] == captured["decode_path"]
    assert captured["decode_exists"] is True
    assert captured["instrument"] == "rhythm_guitar"


def test_ground_truth_benchmark_resolves_yourmt3_guitar_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("aural_ingest.algorithms.yourmt3_guitar")
    stem = tmp_path / "lead.wav"
    stem.write_bytes(b"not real audio; helper is monkeypatched")
    expected = [
        MelodicNote(
            t_on=0.25,
            t_off=0.5,
            pitch=69,
            velocity=100,
            instrument="lead_guitar",
        )
    ]
    captured: dict[str, object] = {}

    def fake_transcribe_yourmt3_midi(stem_path: Path) -> object:
        captured["stem_path"] = stem_path
        return object()

    def fake_midi_to_melodic_notes(_midi: object, *, instrument: str) -> list[MelodicNote]:
        captured["instrument"] = instrument
        return expected

    monkeypatch.setattr(module, "_transcribe_yourmt3_midi", fake_transcribe_yourmt3_midi)
    monkeypatch.setattr(module, "_midi_to_melodic_notes", fake_midi_to_melodic_notes)

    algorithm = get_melodic_algorithm("yourmt3_guitar")
    assert algorithm(stem, "lead_guitar") == expected
    assert captured == {"stem_path": stem, "instrument": "lead_guitar"}


def test_yourmt3_guitar_is_opt_in_melodic_method(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aural_ingest.transcription import (
        KNOWN_MELODIC_METHODS,
        build_default_melodic_algorithm_registry,
        validate_melodic_method,
    )

    module = importlib.import_module("aural_ingest.algorithms.yourmt3_guitar")
    stem = tmp_path / "lead.wav"
    stem.write_bytes(b"not real audio; adapter is monkeypatched")
    expected = [
        MelodicNote(
            t_on=0.0,
            t_off=0.25,
            pitch=64,
            velocity=90,
            instrument="lead_guitar",
        )
    ]
    captured: dict[str, object] = {}

    def fake_transcribe(stem_path: Path, *, instrument: str) -> list[MelodicNote]:
        captured["stem_path"] = stem_path
        captured["instrument"] = instrument
        return expected

    monkeypatch.setattr(module, "transcribe", fake_transcribe)

    assert "yourmt3_guitar" in KNOWN_MELODIC_METHODS
    assert validate_melodic_method("yourmt3_guitar") == "yourmt3_guitar"

    registry = build_default_melodic_algorithm_registry(instrument="lead_guitar")
    assert registry["yourmt3_guitar"](stem) == expected
    assert captured == {"stem_path": stem, "instrument": "lead_guitar"}
