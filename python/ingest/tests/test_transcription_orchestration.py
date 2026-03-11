from pathlib import Path


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
    assert normalized == "combined_filter"
    assert warnings
    assert "legacy_unknown" in warnings[0]


def test_librosa_requested_filter_stays_first_in_chain() -> None:
    from aural_ingest.transcription import drum_fallback_chain

    chain = drum_fallback_chain("librosa_superflux")
    assert chain[0] == "librosa_superflux"
    assert chain[1:4] == ["combined_filter", "dsp_bandpass_improved", "dsp_spectral_flux"]


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

    assert resolve_drum_filter(None) == ("combined_filter", [])
    assert resolve_drum_filter(" auto ") == ("combined_filter", [])


def test_validate_melodic_method_accepts_none_and_blank() -> None:
    from aural_ingest.transcription import validate_melodic_method

    assert validate_melodic_method(None) == "auto"
    assert validate_melodic_method("   ") == "auto"


def test_melodic_fallback_chain_for_pyin_and_unknown() -> None:
    from aural_ingest.transcription import melodic_fallback_chain

    assert melodic_fallback_chain("pyin") == ["pyin"]
    assert melodic_fallback_chain("unknown_method") == ["basic_pitch", "pyin"]


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
    assert result.attempted_methods == ["basic_pitch", "pyin"]
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
