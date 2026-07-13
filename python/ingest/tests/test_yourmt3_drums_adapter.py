from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from aural_ingest.ground_truth_benchmark import get_drum_algorithm
from aural_ingest.transcription import DrumEvent


MT3_DRUM_SHIMS = (
    ("aural_ingest.algorithms.yourmt3_drums", "yourmt3_drums"),
    ("aural_ingest.algorithms.mr_mt3_drums", "mr_mt3_drums"),
)


@pytest.mark.parametrize(("module_name", "engine_id"), MT3_DRUM_SHIMS)
def test_mt3_drum_shim_imports_without_checkpoint(module_name: str, engine_id: str) -> None:
    module = importlib.import_module(module_name)

    assert module.ENGINE_ID == engine_id
    assert callable(module.transcribe)


@pytest.mark.parametrize(("module_name", "engine_id"), MT3_DRUM_SHIMS)
def test_mt3_drum_shim_delegates_to_shared_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    engine_id: str,
) -> None:
    stem = tmp_path / "drums.wav"
    stem.write_bytes(b"not real audio; helper is monkeypatched")
    expected = [DrumEvent(time=0.125, note=36, velocity=100)]
    captured: dict[str, object] = {}

    def fake_transcribe(stem_path: Path, actual_engine_id: str):
        captured["stem_path"] = stem_path
        captured["engine_id"] = actual_engine_id
        return expected, {"backend": "mt3"}

    monkeypatch.setattr(
        "aural_ingest.transcription._transcribe_drums_mt3_events",
        fake_transcribe,
    )

    module = importlib.import_module(module_name)
    assert module.transcribe(stem) == expected
    assert captured == {"stem_path": stem, "engine_id": engine_id}


@pytest.mark.parametrize(("module_name", "engine_id"), MT3_DRUM_SHIMS)
def test_mt3_drum_shim_missing_checkpoint_raises_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    engine_id: str,
) -> None:
    def missing_modelpack(_stem_path: Path, _actual_engine_id: str):
        raise FileNotFoundError("missing modelpack")

    monkeypatch.setattr(
        "aural_ingest.transcription._transcribe_drums_mt3_events",
        missing_modelpack,
    )

    module = importlib.import_module(module_name)
    with pytest.raises(RuntimeError, match=engine_id) as exc_info:
        module.transcribe(tmp_path / "drums.wav")
    assert isinstance(exc_info.value.__cause__, FileNotFoundError)


@pytest.mark.parametrize(("algorithm_id", "engine_id"), [
    ("yourmt3_drums", "yourmt3_drums"),
    ("mr_mt3_drums", "mr_mt3_drums"),
])
def test_ground_truth_benchmark_resolves_mt3_drum_shims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    algorithm_id: str,
    engine_id: str,
) -> None:
    stem = tmp_path / "drums.wav"
    stem.write_bytes(b"not real audio; helper is monkeypatched")
    expected = [DrumEvent(time=0.25, note=38, velocity=96)]
    seen: list[tuple[Path, str]] = []

    def fake_transcribe(stem_path: Path, actual_engine_id: str):
        seen.append((stem_path, actual_engine_id))
        return expected, {"backend": "mt3"}

    monkeypatch.setattr(
        "aural_ingest.transcription._transcribe_drums_mt3_events",
        fake_transcribe,
    )

    algorithm = get_drum_algorithm(algorithm_id)
    assert algorithm(stem) == expected
    assert seen == [(stem, engine_id)]
