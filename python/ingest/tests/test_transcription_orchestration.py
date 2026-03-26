import importlib.machinery
from pathlib import Path

import pytest


def test_drum_fallback_chains_match_recovery_defaults() -> None:
    from aural_ingest.transcription import drum_fallback_chain

    assert drum_fallback_chain("combined_filter") == [
        "combined_filter",
        "dsp_bandpass_improved",
        "adaptive_beat_grid",
        "dsp_spectral_flux",
        "dsp_bandpass",
        "aural_onset",
    ]
    assert drum_fallback_chain("adaptive_beat_grid") == [
        "adaptive_beat_grid",
        "combined_filter",
        "dsp_bandpass_improved",
        "dsp_spectral_flux",
        "dsp_bandpass",
        "aural_onset",
    ]
    assert drum_fallback_chain("beat_conditioned_multiband_decoder") == [
        "beat_conditioned_multiband_decoder",
        "spectral_flux_multiband",
        "adaptive_beat_grid",
        "combined_filter",
        "dsp_bandpass_improved",
        "dsp_spectral_flux",
        "dsp_bandpass",
        "aural_onset",
    ]
    assert drum_fallback_chain("aural_onset") == [
        "aural_onset",
        "combined_filter",
        "adaptive_beat_grid",
        "dsp_bandpass_improved",
        "dsp_spectral_flux",
        "dsp_bandpass",
    ]


def test_unknown_drum_filter_does_not_default_to_adaptive() -> None:
    from aural_ingest.transcription import resolve_drum_filter

    normalized, warnings = resolve_drum_filter("legacy_unknown")
    assert normalized == "adaptive_beat_grid"
    assert warnings
    assert "legacy_unknown" in warnings[0]


def test_librosa_requested_filter_stays_first_in_chain() -> None:
    from aural_ingest.transcription import drum_fallback_chain

    chain = drum_fallback_chain("librosa_superflux")
    assert chain[0] == "librosa_superflux"
    assert chain[1:4] == ["combined_filter", "dsp_bandpass_improved", "dsp_spectral_flux"]


def test_resolve_drum_filter_accepts_mt3_engine_ids() -> None:
    from aural_ingest.transcription import resolve_drum_filter

    assert resolve_drum_filter("mr_mt3_drums") == ("mr_mt3_drums", [])
    assert resolve_drum_filter("yourmt3_drums") == ("yourmt3_drums", [])


def test_mt3_engine_has_no_heuristic_fallback_chain() -> None:
    from aural_ingest.transcription import drum_fallback_chain

    assert drum_fallback_chain("mr_mt3_drums") == ["mr_mt3_drums"]


def test_validate_melodic_method_accepts_known_values() -> None:
    from aural_ingest.transcription import validate_melodic_method

    assert validate_melodic_method("auto") == "auto"
    assert validate_melodic_method("pyin") == "pyin"
    assert validate_melodic_method("basic_pitch") == "basic_pitch"
    assert validate_melodic_method("x") is None


def test_transcribe_drums_uses_fallback_chain_when_first_fails(tmp_path: Path) -> None:
    from aural_ingest.transcription import DrumEvent, transcribe_drums_dsp

    stem = tmp_path / "stem.wav"
    stem.write_bytes(b"x")

    calls: list[str] = []

    def bad(_p: Path) -> list[DrumEvent]:
        calls.append("combined_filter")
        raise RuntimeError("boom")

    def ok(_p: Path) -> list[DrumEvent]:
        calls.append("dsp_bandpass_improved")
        return [DrumEvent(time=0.1, note=36, velocity=100)]

    result = transcribe_drums_dsp(
        stem,
        requested_filter="combined_filter",
        algorithm_registry={
            "combined_filter": bad,
            "dsp_bandpass_improved": ok,
        },
    )

    assert calls == ["combined_filter", "dsp_bandpass_improved"]
    assert result.used_algorithm == "dsp_bandpass_improved"
    assert len(result.events) == 1


def test_basic_pitch_model_resolution_prefers_onnx_then_tflite_then_savedmodel(tmp_path: Path) -> None:
    from aural_ingest.transcription import resolve_basic_pitch_model_path

    root = tmp_path / "root"
    model_dir = root / "basic_pitch" / "saved_models" / "icassp_2022"
    model_dir.mkdir(parents=True, exist_ok=True)

    # Lowest priority first.
    (model_dir / "nmp").mkdir(exist_ok=True)
    assert resolve_basic_pitch_model_path([root]) == model_dir / "nmp"

    (model_dir / "nmp.tflite").write_bytes(b"x")
    assert resolve_basic_pitch_model_path([root]) == model_dir / "nmp.tflite"

    (model_dir / "nmp.onnx").write_bytes(b"x")
    assert resolve_basic_pitch_model_path([root]) == model_dir / "nmp.onnx"


def test_transcribe_melodic_auto_falls_back_to_pyin_when_basic_pitch_unavailable(tmp_path: Path) -> None:
    from aural_ingest.transcription import MelodicNote, transcribe_melodic

    stem = tmp_path / "stem.wav"
    stem.write_bytes(b"x")

    calls: list[str] = []

    def basic_fail(_p: Path) -> list[MelodicNote]:
        calls.append("basic_pitch")
        raise RuntimeError("no model")

    def pyin_ok(_p: Path) -> list[MelodicNote]:
        calls.append("pyin")
        return [MelodicNote(t_on=0.1, t_off=0.2, pitch=60, velocity=90)]

    result = transcribe_melodic(
        stem,
        requested_method="auto",
        algorithm_registry={
            "basic_pitch": basic_fail,
            "pyin": pyin_ok,
        },
    )

    assert calls == ["basic_pitch", "pyin"]
    assert result.used_method == "pyin"
    assert len(result.notes) == 1


def test_transcribe_melodic_requested_basic_pitch_can_fallback_to_pyin(tmp_path: Path) -> None:
    from aural_ingest.transcription import MelodicNote, transcribe_melodic

    stem = tmp_path / "stem.wav"
    stem.write_bytes(b"x")

    def basic_fail(_p: Path) -> list[MelodicNote]:
        raise RuntimeError("model missing")

    def pyin_ok(_p: Path) -> list[MelodicNote]:
        return [MelodicNote(t_on=0.0, t_off=0.1, pitch=48, velocity=80)]

    result = transcribe_melodic(
        stem,
        requested_method="basic_pitch",
        algorithm_registry={
            "basic_pitch": basic_fail,
            "pyin": pyin_ok,
        },
    )

    assert result.used_method == "pyin"
    assert result.attempted_methods[:2] == ["basic_pitch", "pyin"]


def test_resolve_drum_filter_accepts_none_and_auto() -> None:
    from aural_ingest.transcription import resolve_drum_filter

    assert resolve_drum_filter(None) == ("adaptive_beat_grid", [])
    assert resolve_drum_filter(" auto ") == ("adaptive_beat_grid", [])


def test_validate_melodic_method_accepts_none_and_blank() -> None:
    from aural_ingest.transcription import validate_melodic_method

    assert validate_melodic_method(None) == "auto"
    assert validate_melodic_method("   ") == "auto"


def test_melodic_fallback_chain_for_pyin_and_unknown() -> None:
    from aural_ingest.transcription import melodic_fallback_chain

    assert melodic_fallback_chain("pyin") == [
        "pyin",
        "melodic_octave_fix",
        "melodic_yin_octave_hps_fix",
        "melodic_combined",
        "basic_pitch",
    ]
    assert melodic_fallback_chain("unknown_method") == [
        "melodic_octave_fix",
        "melodic_combined",
        "basic_pitch",
        "pyin",
    ]


def test_transcribe_drums_reports_unavailable_algorithms_and_returns_empty(tmp_path: Path) -> None:
    from aural_ingest.transcription import transcribe_drums_dsp

    stem = tmp_path / "stem.wav"
    stem.write_bytes(b"x")
    warnings: list[str] = []

    result = transcribe_drums_dsp(
        stem,
        requested_filter="combined_filter",
        algorithm_registry={},
        logger=warnings.append,
    )

    assert result.used_algorithm is None
    assert result.events == []
    assert result.attempted_algorithms
    assert any("unavailable" in w for w in result.warnings)
    assert warnings


def test_transcribe_melodic_unknown_requested_method_and_missing_registry(tmp_path: Path) -> None:
    from aural_ingest.transcription import transcribe_melodic

    stem = tmp_path / "stem.wav"
    stem.write_bytes(b"x")
    warnings: list[str] = []

    result = transcribe_melodic(
        stem,
        requested_method="legacy_unknown_method",
        algorithm_registry={},
        logger=warnings.append,
    )

    assert result.used_method is None
    assert result.notes == []
    assert result.attempted_methods == [
        "melodic_octave_fix",
        "melodic_combined",
        "basic_pitch",
        "pyin",
    ]
    assert any("unknown melodic method" in w for w in result.warnings)
    assert any("unavailable" in w for w in result.warnings)
    assert warnings


def test_default_melodic_registry_uses_model_resolution_and_generates_notes(tmp_path: Path, monkeypatch) -> None:
    import sys

    from aural_ingest.transcription import (
        build_default_melodic_algorithm_registry,
        resolve_basic_pitch_model_path,
    )

    model_dir = tmp_path / "basic_pitch" / "saved_models" / "icassp_2022"
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "nmp.onnx"
    model_path.write_bytes(b"x")

    stem = tmp_path / "stem.wav"
    stem.write_bytes(b"dummy")

    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    resolved = resolve_basic_pitch_model_path([None, tmp_path])
    assert resolved == model_path

    reg = build_default_melodic_algorithm_registry(model_search_roots=[tmp_path])
    notes = reg["basic_pitch"](stem)
    assert notes
    assert all(n.t_off >= n.t_on for n in notes)


def test_resolve_mt3_modelpack_prefers_installed_modelpack_layout(tmp_path: Path) -> None:
    import json

    from aural_ingest.transcription import drum_engine_metadata, resolve_mt3_modelpack

    model_root = (
        tmp_path
        / "assets"
        / "models"
        / "mr_mt3"
        / "0.0.1"
    )
    checkpoint = model_root / "files" / "checkpoints" / "mr_mt3" / "mt3.pth"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_bytes(b"x")
    (model_root / "modelpack.json").write_text(
        json.dumps(
            {
                "id": "mr_mt3",
                "version": "0.0.1",
                "checkpoints": [
                    {"model": "mr_mt3", "path": "files/checkpoints/mr_mt3/mt3.pth"}
                ],
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_mt3_modelpack("mr_mt3_drums", search_roots=[tmp_path])
    assert resolved["modelpack_id"] == "mr_mt3"
    assert resolved["modelpack_version"] == "0.0.1"
    assert Path(resolved["checkpoint_path_resolved"]) == checkpoint
    assert isinstance(drum_engine_metadata("mr_mt3_drums")["checkpoint_path"], str)


def test_midi_to_drum_events_normalizes_note_classes_and_timing() -> None:
    import mido

    from aural_ingest.transcription import _midi_to_drum_events

    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=600000, time=0))
    track.append(mido.Message("note_on", channel=9, note=35, velocity=101, time=480))
    track.append(mido.Message("note_on", channel=9, note=46, velocity=96, time=240))
    track.append(mido.Message("note_on", channel=9, note=50, velocity=88, time=240))

    events = _midi_to_drum_events(midi)
    assert [event.note for event in events] == [36, 42, 48]
    assert events[0].time == pytest.approx(0.6)
    assert events[1].time == pytest.approx(0.9)
    assert events[2].time == pytest.approx(1.2)


def test_transcribe_drums_mt3_engine_surfaces_model_meta(tmp_path: Path, monkeypatch) -> None:
    from aural_ingest.transcription import DrumEvent, transcribe_drums

    stem = tmp_path / "stem.wav"
    stem.write_bytes(b"x")

    monkeypatch.setattr(
        "aural_ingest.transcription._transcribe_drums_mt3_events",
        lambda _stem, _engine: (
            [DrumEvent(time=0.25, note=36, velocity=110)],
            {
                "backend": "mt3",
                "model_id": "yourmt3",
                "modelpack_id": "yourmt3",
                "modelpack_version": "0.0.1",
            },
        ),
    )

    result = transcribe_drums(stem, requested_engine="yourmt3_drums", algorithm_registry={})
    assert result.used_algorithm == "yourmt3_drums"
    assert result.meta["backend"] == "mt3"
    assert result.meta["modelpack_id"] == "yourmt3"


def test_transcribe_drums_mt3_uses_local_checkpoint_only(tmp_path: Path, monkeypatch) -> None:
    import mido
    import numpy as np
    import sys
    import types

    from aural_ingest.transcription import _transcribe_drums_mt3_events

    stem = tmp_path / "stem.wav"
    stem.write_bytes(b"x")

    checkpoint = tmp_path / "files" / "checkpoints" / "mr_mt3" / "mt3.pth"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_bytes(b"checkpoint")

    captured: dict[str, object] = {}

    class FakeModel:
        def transcribe(self, audio, sr):
            captured["audio_len"] = len(audio)
            captured["audio_sr"] = sr
            midi = mido.MidiFile(ticks_per_beat=480)
            track = mido.MidiTrack()
            midi.tracks.append(track)
            track.append(mido.Message("note_on", channel=9, note=36, velocity=100, time=0))
            return midi

    fake_mt3 = types.ModuleType("mt3_infer")

    def fake_load_model(model_id, *, checkpoint_path, device, auto_download):
        captured["model_id"] = model_id
        captured["checkpoint_path"] = checkpoint_path
        captured["device"] = device
        captured["auto_download"] = auto_download
        return FakeModel()

    fake_mt3.load_model = fake_load_model
    monkeypatch.setitem(sys.modules, "mt3_infer", fake_mt3)

    fake_librosa = types.ModuleType("librosa")
    fake_librosa.__spec__ = importlib.machinery.ModuleSpec("librosa", loader=None)
    fake_librosa.load = lambda _path, sr, mono: (np.zeros(1600, dtype=np.float32), sr)
    monkeypatch.setitem(sys.modules, "librosa", fake_librosa)
    monkeypatch.setattr(
        "aural_ingest.transcription.resolve_mt3_modelpack",
        lambda _engine_id, **_kwargs: {
            "model_id": "mr_mt3",
            "modelpack_id": "mr_mt3",
            "modelpack_version": "0.0.1",
            "checkpoint_path_resolved": str(checkpoint),
            "modelpack_root": str(tmp_path),
            "size_mb": 176.0,
            "speed_x_realtime": 57.0,
        },
    )

    events, meta = _transcribe_drums_mt3_events(stem, "mr_mt3_drums")
    assert [event.note for event in events] == [36]
    assert captured["model_id"] == "mr_mt3"
    assert captured["checkpoint_path"] == str(checkpoint)
    assert captured["device"] == "cpu"
    assert captured["auto_download"] is False
    assert meta["checkpoint_path"] == str(checkpoint)
    assert meta["modelpack_root"] == str(tmp_path)


def test_transcribe_drums_mt3_suppresses_known_runtime_warnings(tmp_path: Path, monkeypatch) -> None:
    import mido
    import numpy as np
    import sys
    import types
    import warnings

    from aural_ingest.transcription import _transcribe_drums_mt3_events

    stem = tmp_path / "stem.wav"
    stem.write_bytes(b"x")

    checkpoint = tmp_path / "files" / "checkpoints" / "yourmt3" / "last.ckpt"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_bytes(b"checkpoint")

    class FakeModel:
        def transcribe(self, audio, sr):
            warnings.warn(
                "At least one mel filterbank has all zero values. The value for `n_mels` (512) may be set too high.",
                UserWarning,
                stacklevel=1,
            )
            midi = mido.MidiFile(ticks_per_beat=480)
            track = mido.MidiTrack()
            midi.tracks.append(track)
            track.append(mido.Message("note_on", channel=9, note=36, velocity=100, time=0))
            return midi

    fake_mt3 = types.ModuleType("mt3_infer")

    def fake_load_model(model_id, *, checkpoint_path, device, auto_download):
        warnings.warn(
            "torch.cuda.amp.autocast(args...) is deprecated. Please use `torch.amp.autocast('cuda', args...)` instead.",
            FutureWarning,
            stacklevel=1,
        )
        warnings.warn(
            "Instantiating a decoder T5Attention without passing `layer_idx` is not recommended and will to errors during the forward call, if caching is used.",
            UserWarning,
            stacklevel=1,
        )
        warnings.warn(
            "The `device` argument is deprecated and will be removed in v5 of Transformers.",
            FutureWarning,
            stacklevel=1,
        )
        return FakeModel()

    fake_mt3.load_model = fake_load_model
    monkeypatch.setitem(sys.modules, "mt3_infer", fake_mt3)

    fake_librosa = types.ModuleType("librosa")
    fake_librosa.__spec__ = importlib.machinery.ModuleSpec("librosa", loader=None)
    fake_librosa.load = lambda _path, sr, mono: (np.zeros(1600, dtype=np.float32), sr)
    monkeypatch.setitem(sys.modules, "librosa", fake_librosa)
    monkeypatch.setattr(
        "aural_ingest.transcription.resolve_mt3_modelpack",
        lambda _engine_id, **_kwargs: {
            "model_id": "yourmt3",
            "modelpack_id": "yourmt3",
            "modelpack_version": "0.0.1",
            "checkpoint_path_resolved": str(checkpoint),
            "modelpack_root": str(tmp_path),
            "size_mb": 536.0,
            "speed_x_realtime": 15.0,
        },
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        events, meta = _transcribe_drums_mt3_events(stem, "yourmt3_drums")

    assert [event.note for event in events] == [36]
    assert meta["modelpack_id"] == "yourmt3"
    assert caught == []
