import importlib.machinery
from collections.abc import Iterable
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
    # Unknown filters fall back to the global default, not silently to
    # `adaptive_beat_grid`. The default moved off `combined_filter` (worst
    # E-GMD recall) to `beat_conditioned_multiband_decoder`.
    assert normalized == "beat_conditioned_multiband_decoder"
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
    assert validate_melodic_method("piano_auto") == "piano_auto"
    assert validate_melodic_method("piano_basic_pitch_playable") == "piano_basic_pitch_playable"
    assert validate_melodic_method("piano_basic_pitch") == "piano_basic_pitch"
    assert validate_melodic_method("piano_basic_pitch_clean") == "piano_basic_pitch_clean"
    assert validate_melodic_method("piano_polyphonic_clean") == "piano_polyphonic_clean"
    assert validate_melodic_method("piano_transkun_clean") == "piano_transkun_clean"
    assert validate_melodic_method("piano_pti_clean") == "piano_pti_clean"
    assert validate_melodic_method("piano_hft_clean") == "piano_hft_clean"
    assert validate_melodic_method("pyin") == "pyin"
    assert validate_melodic_method("basic_pitch") == "basic_pitch"
    assert validate_melodic_method("melodic_adaptive") == "melodic_adaptive"
    assert validate_melodic_method("melodic_yin_bass80") == "melodic_yin_bass80"
    assert validate_melodic_method("melodic_hpss_combined") == "melodic_hpss_combined"
    assert validate_melodic_method("melodic_template_multipass") == "melodic_template_multipass"
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


def test_default_basic_pitch_roots_include_dependency_package_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aural_ingest import transcription

    package_dir = tmp_path / "site-packages" / "basic_pitch"
    package_dir.mkdir(parents=True)
    spec = importlib.machinery.ModuleSpec("basic_pitch", loader=None, is_package=True)
    spec.submodule_search_locations = [str(package_dir)]

    monkeypatch.setattr(
        transcription.importlib.util,
        "find_spec",
        lambda name: spec if name == "basic_pitch" else None,
    )

    roots = transcription._default_basic_pitch_model_roots()
    assert package_dir.parent in roots


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


def test_transcribe_melodic_requested_piano_basic_pitch_can_fallback_to_heuristic(tmp_path: Path) -> None:
    from aural_ingest.transcription import MelodicNote, transcribe_melodic

    stem = tmp_path / "keys.wav"
    stem.write_bytes(b"x")
    calls: list[str] = []

    def piano_basic_fail(_p: Path) -> list[MelodicNote]:
        calls.append("piano_basic_pitch")
        raise RuntimeError("basic pitch package missing")

    def piano_basic_clean_fail(_p: Path) -> list[MelodicNote]:
        calls.append("piano_basic_pitch_clean")
        raise RuntimeError("basic pitch package missing")

    def piano_polyphonic_ok(_p: Path) -> list[MelodicNote]:
        calls.append("piano_polyphonic_clean")
        return [
            MelodicNote(t_on=0.0, t_off=0.5, pitch=48, velocity=84, instrument="keys"),
            MelodicNote(t_on=0.0, t_off=0.5, pitch=60, velocity=88, instrument="keys"),
            MelodicNote(t_on=0.0, t_off=0.5, pitch=64, velocity=88, instrument="keys"),
        ]

    result = transcribe_melodic(
        stem,
        requested_method="piano_basic_pitch",
        instrument="keys",
        algorithm_registry={
            "piano_basic_pitch": piano_basic_fail,
            "piano_basic_pitch_clean": piano_basic_clean_fail,
            "piano_polyphonic_clean": piano_polyphonic_ok,
        },
    )

    assert calls == ["piano_basic_pitch", "piano_basic_pitch_clean", "piano_polyphonic_clean"]
    assert result.used_method == "piano_polyphonic_clean"
    assert result.attempted_methods[:4] == [
        "piano_basic_pitch",
        "piano_basic_pitch_playable",
        "piano_basic_pitch_clean",
        "piano_polyphonic_clean",
    ]


def test_resolve_drum_filter_accepts_none_and_auto() -> None:
    from aural_ingest.transcription import resolve_drum_filter

    assert resolve_drum_filter(None) == ("beat_conditioned_multiband_decoder", [])
    assert resolve_drum_filter(" auto ") == ("beat_conditioned_multiband_decoder", [])


def test_validate_melodic_method_accepts_none_and_blank() -> None:
    from aural_ingest.transcription import validate_melodic_method

    assert validate_melodic_method(None) == "auto"
    assert validate_melodic_method("   ") == "auto"


def test_melodic_fallback_chain_for_pyin_and_unknown() -> None:
    from aural_ingest.transcription import melodic_fallback_chain

    assert melodic_fallback_chain("pyin") == [
        "pyin",
        "melodic_adaptive",
        "melodic_octave_fix",
        "melodic_yin_octave_hps_fix",
        "melodic_hpss_combined",
        "melodic_combined",
        "basic_pitch",
    ]
    assert melodic_fallback_chain("piano_auto", instrument="keys") == [
        "piano_auto",
        "piano_basic_pitch_playable",
        "piano_basic_pitch",
        "piano_basic_pitch_clean",
        "piano_polyphonic_clean",
        "melodic_hpss_combined",
        "melodic_octave_fix",
        "melodic_combined",
        "basic_pitch",
        "pyin",
    ]
    assert melodic_fallback_chain("unknown_method") == [
        "melodic_adaptive",
        "melodic_octave_fix",
        "melodic_combined",
        "basic_pitch",
        "pyin",
    ]


def test_melodic_fallback_chain_auto_prefers_bass_and_keys_specialists() -> None:
    from aural_ingest.transcription import melodic_fallback_chain

    # torchcrepe leads the bass chain (neural monophonic pitch tracker:
    # matches YIN+HPS octave-cleanliness, ~9x faster), with the YIN chain
    # retained as the score-gated fallback.
    assert melodic_fallback_chain("auto", instrument="bass")[:5] == [
        "torchcrepe",
        "melodic_yin_octave_hps_fix",
        "melodic_adaptive",
        "melodic_yin_bass80",
        "melodic_octave_fix",
    ]
    assert melodic_fallback_chain("auto", instrument="keys")[:5] == [
        "piano_auto",
        "piano_basic_pitch_playable",
        "piano_basic_pitch",
        "piano_basic_pitch_clean",
        "piano_polyphonic_clean",
    ]

    # Lead guitar is monophonic: torchcrepe leads (octave-clean, ~6-8x faster),
    # with the polyphonic DSP chain retained as the score-gated fallback.
    assert melodic_fallback_chain("auto", instrument="lead_guitar") == [
        "torchcrepe",
        "melodic_adaptive",
        "melodic_octave_fix",
        "melodic_combined",
        "basic_pitch",
        "pyin",
    ]

    # Rhythm guitar is polyphonic (chords): the polyphonic HPSS+onset engine
    # stays first. torchcrepe (monophonic) must NOT lead here — it would collapse
    # the chord voices despite scoring well on the cleanliness heuristic.
    rhythm_chain = melodic_fallback_chain("auto", instrument="rhythm_guitar")
    assert rhythm_chain[0] == "melodic_hpss_combined"
    assert "torchcrepe" not in rhythm_chain
    assert rhythm_chain == [
        "melodic_hpss_combined",
        "melodic_adaptive",
        "melodic_octave_fix",
        "melodic_combined",
        "basic_pitch",
        "pyin",
    ]


def test_melodic_fallback_chain_for_piano_research_methods_prefers_piano_auto() -> None:
    from aural_ingest.transcription import melodic_fallback_chain

    assert melodic_fallback_chain("piano_polyphonic_clean", instrument="keys")[:6] == [
        "piano_polyphonic_clean",
        "piano_auto",
        "piano_basic_pitch_playable",
        "piano_basic_pitch",
        "piano_basic_pitch_clean",
        "melodic_hpss_combined",
    ]
    assert melodic_fallback_chain("piano_transkun_clean", instrument="keys")[:6] == [
        "piano_transkun_clean",
        "piano_auto",
        "piano_basic_pitch_playable",
        "piano_basic_pitch",
        "piano_basic_pitch_clean",
        "melodic_hpss_combined",
    ]
    assert melodic_fallback_chain("piano_pti", instrument="keys")[:6] == [
        "piano_pti",
        "piano_auto",
        "piano_basic_pitch_playable",
        "piano_basic_pitch",
        "piano_basic_pitch_clean",
        "melodic_hpss_combined",
    ]


def test_transcribe_melodic_piano_research_method_falls_back_to_piano_auto(tmp_path: Path) -> None:
    from aural_ingest.transcription import MelodicNote, transcribe_melodic

    stem = tmp_path / "stem.wav"
    stem.write_bytes(b"x")

    calls: list[str] = []

    def piano_fail(_p: Path) -> list[MelodicNote]:
        calls.append("piano_transkun_clean")
        raise RuntimeError("missing piano model")

    def piano_auto_ok(_p: Path) -> list[MelodicNote]:
        calls.append("piano_auto")
        return [MelodicNote(t_on=0.0, t_off=0.25, pitch=60, velocity=96, instrument="keys")]

    result = transcribe_melodic(
        stem,
        requested_method="piano_transkun_clean",
        instrument="keys",
        algorithm_registry={
            "piano_transkun_clean": piano_fail,
            "piano_auto": piano_auto_ok,
        },
    )

    assert calls == ["piano_transkun_clean", "piano_auto"]
    assert result.used_method == "piano_auto"
    assert len(result.notes) == 1


def test_transcribe_melodic_auto_keys_prefers_polyphonic_piano_path(tmp_path: Path) -> None:
    from aural_ingest.transcription import MelodicNote, transcribe_melodic

    stem = tmp_path / "keys.wav"
    stem.write_bytes(b"x")
    calls: list[str] = []

    def piano_auto_ok(_p: Path) -> list[MelodicNote]:
        calls.append("piano_auto")
        return [
            MelodicNote(t_on=0.0, t_off=0.5, pitch=48, velocity=84, instrument="keys"),
            MelodicNote(t_on=0.0, t_off=0.5, pitch=60, velocity=88, instrument="keys"),
            MelodicNote(t_on=0.0, t_off=0.5, pitch=64, velocity=88, instrument="keys"),
        ]

    def monophonic_fail(_p: Path) -> list[MelodicNote]:
        calls.append("melodic_adaptive")
        return [MelodicNote(t_on=0.0, t_off=0.5, pitch=60, velocity=80, instrument="keys")]

    result = transcribe_melodic(
        stem,
        requested_method="auto",
        instrument="keys",
        algorithm_registry={
            "piano_auto": piano_auto_ok,
            "melodic_adaptive": monophonic_fail,
        },
    )

    assert calls == ["piano_auto"]
    assert result.used_method == "piano_auto"
    assert len(result.notes) == 3


def test_role_playability_cleanup_caps_rhythm_guitar_chatter() -> None:
    from aural_ingest.transcription import MelodicNote, apply_role_playability_cleanup

    notes = [
        MelodicNote(t_on=0.00, t_off=0.30, pitch=52, velocity=80, instrument="rhythm_guitar"),
        MelodicNote(t_on=0.01, t_off=0.28, pitch=55, velocity=82, instrument="rhythm_guitar"),
        MelodicNote(t_on=0.02, t_off=0.26, pitch=59, velocity=84, instrument="rhythm_guitar"),
        MelodicNote(t_on=0.03, t_off=0.25, pitch=64, velocity=70, instrument="rhythm_guitar"),
        MelodicNote(t_on=0.04, t_off=0.20, pitch=67, velocity=70, instrument="rhythm_guitar"),
        MelodicNote(t_on=0.07, t_off=0.30, pitch=60, velocity=92, instrument="rhythm_guitar"),
        MelodicNote(t_on=0.16, t_off=0.35, pitch=62, velocity=90, instrument="rhythm_guitar"),
    ]

    cleaned = apply_role_playability_cleanup(notes, "rhythm_guitar")

    assert len([n for n in cleaned if n.t_on < 0.04]) == 3
    assert all(n.t_on != 0.07 for n in cleaned)
    assert any(n.t_on == 0.16 for n in cleaned)


def test_role_playability_cleanup_caps_keys_polyphony_preserving_melody_and_bass() -> None:
    from aural_ingest.transcription import MelodicNote, apply_role_playability_cleanup

    notes = [
        MelodicNote(t_on=0.0, t_off=1.0, pitch=pitch, velocity=60 + idx, instrument="keys")
        for idx, pitch in enumerate([31, 36, 43, 48, 52, 55, 60, 64, 67, 71, 76])
    ]

    cleaned = apply_role_playability_cleanup(notes, "keys")
    pitches = {note.pitch for note in cleaned}

    assert len(cleaned) == 7
    assert 76 in pitches
    assert 36 in pitches
    assert 31 not in pitches

    points: list[tuple[float, int]] = []
    for note in cleaned:
        points.append((note.t_on, 1))
        points.append((note.t_off, -1))
    active = 0
    peak = 0
    for _time, delta in sorted(points, key=lambda item: (item[0], item[1])):
        active += delta
        peak = max(peak, active)
    assert peak == 7


def test_role_playability_cleanup_prunes_dense_low_confidence_keys_without_touching_sparse() -> None:
    from aural_ingest.transcription import MelodicNote, apply_role_playability_cleanup

    dense_notes = []
    for idx in range(40):
        velocity = 82 if idx % 2 == 0 else 64
        dense_notes.append(
            MelodicNote(
                t_on=idx * 0.08,
                t_off=idx * 0.08 + 0.04,
                pitch=60 + (idx % 5),
                velocity=velocity,
                instrument="keys",
            )
        )

    cleaned_dense = apply_role_playability_cleanup(dense_notes, "keys")

    assert len(cleaned_dense) == 20
    assert {note.velocity for note in cleaned_dense} == {41}

    sparse_notes = [
        MelodicNote(
            t_on=float(idx),
            t_off=float(idx) + 0.25,
            pitch=60 + (idx % 5),
            velocity=62,
            instrument="keys",
        )
        for idx in range(20)
    ]

    assert apply_role_playability_cleanup(sparse_notes, "keys") == sparse_notes


def test_role_playability_cleanup_calibrates_dense_keys_velocity_but_preserves_sparse() -> None:
    from aural_ingest.transcription import MelodicNote, apply_role_playability_cleanup

    dense_notes = [
        MelodicNote(
            t_on=idx * 0.1,
            t_off=idx * 0.1 + 0.08,
            pitch=60 + (idx % 4),
            velocity=80 + (idx % 4) * 4,
            instrument="keys",
        )
        for idx in range(28)
    ]

    cleaned_dense = apply_role_playability_cleanup(dense_notes, "keys")

    assert len(cleaned_dense) == len(dense_notes)
    assert max(note.velocity for note in cleaned_dense) <= 46
    assert min(note.velocity for note in cleaned_dense) >= 40

    sparse_notes = [
        MelodicNote(
            t_on=float(idx),
            t_off=float(idx) + 0.25,
            pitch=60 + (idx % 4),
            velocity=88,
            instrument="keys",
        )
        for idx in range(8)
    ]

    assert apply_role_playability_cleanup(sparse_notes, "keys") == sparse_notes


def test_role_playability_cleanup_extends_dense_keys_sustain_without_exceeding_cap() -> None:
    from aural_ingest.transcription import MelodicNote, apply_role_playability_cleanup

    notes = [
        MelodicNote(
            t_on=idx * 0.12,
            t_off=idx * 0.12 + 0.1,
            pitch=60 + (idx % 8),
            velocity=88,
            instrument="keys",
        )
        for idx in range(30)
    ]

    cleaned = apply_role_playability_cleanup(notes, "keys")

    assert len(cleaned) == len(notes)
    assert max(note.t_off - note.t_on for note in cleaned) >= 0.5

    points: list[tuple[float, int]] = []
    for note in cleaned:
        points.append((note.t_on, 1))
        points.append((note.t_off, -1))
    active = 0
    peak = 0
    for _time, delta in sorted(points, key=lambda item: (item[0], item[1])):
        active += delta
        peak = max(peak, active)
    assert peak <= 7


def test_keys_recall_restore_adds_dense_confident_notes_without_breaking_caps() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _apply_keys_recall_restore,
        _keys_max_polyphony,
    )

    playable = [
        MelodicNote(
            t_on=idx * 0.1,
            t_off=idx * 0.1 + 0.08,
            pitch=60 + (idx % 4),
            velocity=40,
            instrument="keys",
        )
        for idx in range(32)
    ]
    recall = [
        MelodicNote(t_on=0.405, t_off=0.75, pitch=72, velocity=86, instrument="keys"),
        MelodicNote(t_on=0.02, t_off=0.36, pitch=60, velocity=90, instrument="keys"),
        MelodicNote(t_on=0.55, t_off=0.90, pitch=76, velocity=69, instrument="keys"),
        MelodicNote(t_on=0.72, t_off=0.86, pitch=79, velocity=90, instrument="keys"),
    ]

    restored = _apply_keys_recall_restore(playable, recall)

    assert len(restored) == len(playable) + 1
    restored_note = next(note for note in restored if note.pitch == 72)
    assert restored_note.velocity == 43
    assert _keys_max_polyphony(restored) <= 7
    assert all(
        sum(1 for other in restored if abs(other.t_on - note.t_on) <= 0.055) <= 7
        for note in restored
    )


def test_keys_recall_restore_skips_sparse_passages() -> None:
    from aural_ingest.transcription import MelodicNote, _apply_keys_recall_restore

    playable = [
        MelodicNote(
            t_on=float(idx),
            t_off=float(idx) + 0.25,
            pitch=60 + (idx % 3),
            velocity=40,
            instrument="keys",
        )
        for idx in range(8)
    ]
    recall = [
        MelodicNote(t_on=0.45, t_off=0.95, pitch=72, velocity=90, instrument="keys"),
    ]

    assert _apply_keys_recall_restore(playable, recall) == playable


def test_keys_basic_pitch_release_sustain_extends_dense_mid_high_notes_under_cap() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _apply_keys_basic_pitch_release_sustain,
        _keys_max_polyphony,
    )

    notes = [
        MelodicNote(
            t_on=idx * 0.12,
            t_off=idx * 0.12 + 0.16,
            pitch=60 + (idx % 4),
            velocity=42,
            instrument="keys",
        )
        for idx in range(30)
    ]

    sustained = _apply_keys_basic_pitch_release_sustain(notes)

    assert len(sustained) == len(notes)
    assert max(note.t_off - note.t_on for note in sustained) >= 1.0
    assert _keys_max_polyphony(sustained) <= 7


def test_keys_basic_pitch_release_sustain_extends_sparse_chordal_notes_under_cap() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _apply_keys_basic_pitch_release_sustain,
        _keys_max_polyphony,
    )

    chordal = [
        MelodicNote(
            t_on=cluster * 1.4,
            t_off=cluster * 1.4 + 0.25,
            pitch=60 + (cluster * 2) + chord_tone,
            velocity=42,
            instrument="keys",
        )
        for cluster in range(7)
        for chord_tone in range(2)
    ]
    monophonic = [
        MelodicNote(
            t_on=idx * 0.7,
            t_off=idx * 0.7 + 0.25,
            pitch=72,
            velocity=42,
            instrument="keys",
        )
        for idx in range(14)
    ]

    sustained = _apply_keys_basic_pitch_release_sustain(chordal)

    assert len(sustained) == len(chordal)
    assert max(note.t_off - note.t_on for note in sustained) >= 2.0
    assert _keys_max_polyphony(sustained) <= 7
    assert _apply_keys_basic_pitch_release_sustain(monophonic) == monophonic


def test_keys_basic_pitch_release_sustain_extends_dense_low_register_tails_under_cap() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _apply_keys_basic_pitch_release_sustain,
        _keys_max_polyphony,
    )

    notes = [
        MelodicNote(
            t_on=cluster * 0.45,
            t_off=cluster * 0.45 + 0.24,
            pitch=[39, 50, 58, 65, 70][tone],
            velocity=42,
            instrument="keys",
        )
        for cluster in range(24)
        for tone in range(5)
    ]

    sustained = _apply_keys_basic_pitch_release_sustain(notes)

    assert len(sustained) == len(notes)
    assert max(note.t_off - note.t_on for note in sustained if note.pitch >= 58) >= 2.0
    assert _keys_max_polyphony(sustained) <= 7


def test_keys_basic_pitch_release_sustain_trims_sparse_low_register_high_tails() -> None:
    from aural_ingest.transcription import MelodicNote, _trim_keys_sparse_low_tails

    sparse_low_sustained = [
        MelodicNote(
            t_on=idx * 0.28,
            t_off=idx * 0.28 + 1.2,
            pitch=31 if idx % 3 == 0 else 58 + (idx % 5),
            velocity=74,
            instrument="keys",
        )
        for idx in range(36)
    ]
    unrelated_dense_high = [
        MelodicNote(
            t_on=idx * 0.08,
            t_off=idx * 0.08 + 1.2,
            pitch=60 + (idx % 6),
            velocity=74,
            instrument="keys",
        )
        for idx in range(36)
    ]

    trimmed = _trim_keys_sparse_low_tails(sparse_low_sustained)

    assert max(note.t_off - note.t_on for note in trimmed if note.pitch >= 58) <= 0.350001
    assert any(note.t_off - note.t_on > 1.0 for note in trimmed if note.pitch == 31)
    assert max(note.velocity for note in trimmed) < max(note.velocity for note in sparse_low_sustained)
    assert _trim_keys_sparse_low_tails(unrelated_dense_high) == unrelated_dense_high


def test_keys_basic_pitch_release_sustain_trims_dense_high_staccato_tails() -> None:
    from aural_ingest.transcription import MelodicNote, _trim_keys_dense_high_staccato_tails

    dense_high = [
        MelodicNote(
            t_on=idx * 0.09,
            t_off=idx * 0.09 + 0.6,
            pitch=56 + (idx % 18),
            velocity=42,
            instrument="keys",
        )
        for idx in range(130)
    ]
    trimmed = _trim_keys_dense_high_staccato_tails(dense_high)

    assert max(note.t_off - note.t_on for note in trimmed if note.pitch >= 56) == pytest.approx(0.25)
    assert len(trimmed) == len(dense_high)

    low_register = [
        MelodicNote(
            t_on=idx * 0.09,
            t_off=idx * 0.09 + 0.6,
            pitch=35 + (idx % 18),
            velocity=42,
            instrument="keys",
        )
        for idx in range(130)
    ]
    sparse_high = [
        MelodicNote(
            t_on=idx * 0.25,
            t_off=idx * 0.25 + 0.6,
            pitch=56 + (idx % 18),
            velocity=42,
            instrument="keys",
        )
        for idx in range(40)
    ]

    assert _trim_keys_dense_high_staccato_tails(low_register) == low_register
    assert _trim_keys_dense_high_staccato_tails(sparse_high) == sparse_high


def test_keys_sparse_low_treble_restore_adds_octaves_under_cap() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _keys_max_polyphony,
        _restore_keys_sparse_low_treble,
    )

    synth_like = [
        MelodicNote(
            t_on=idx * 0.26,
            t_off=idx * 0.26 + 0.75,
            pitch=[31, 34, 38, 41, 46, 50, 53, 58, 60, 62, 63, 65, 70, 70, 82][idx % 15],
            velocity=44,
            instrument="keys",
        )
        for idx in range(45)
    ]
    high_sparse = [
        MelodicNote(
            t_on=idx * 0.42,
            t_off=idx * 0.42 + 1.0,
            pitch=82 if idx % 2 else 61,
            velocity=44,
            instrument="keys",
        )
        for idx in range(45)
    ]

    restored = _restore_keys_sparse_low_treble(synth_like)

    assert len(restored) > len(synth_like)
    assert any(note.pitch == 55 for note in restored if abs(note.t_on - 0.0) <= 0.055)
    assert any(note.pitch == 65 for note in restored if abs(note.t_on - 1.56) <= 0.055)
    assert max(note.velocity for note in restored if note not in synth_like) <= 24
    assert _keys_max_polyphony(restored) <= 7
    assert _restore_keys_sparse_low_treble(high_sparse) == high_sparse


def test_keys_sparse_low_mid_profile_gate_targets_synth_like_output() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _keys_should_try_sparse_low_mid_profile,
    )

    synth_like = [
        MelodicNote(
            t_on=idx * 0.2,
            t_off=idx * 0.2 + 0.7,
            pitch=[
                31,
                34,
                38,
                41,
                46,
                50,
                53,
                58,
                60,
                62,
                63,
                65,
                70,
                70,
                82,
            ][idx % 15],
            velocity=44,
            instrument="keys",
        )
        for idx in range(58)
    ]
    no_high_anchor = [
        MelodicNote(
            t_on=idx * 0.2,
            t_off=idx * 0.2 + 0.7,
            pitch=[
                31,
                34,
                38,
                41,
                46,
                50,
                53,
                58,
                60,
                62,
                63,
                65,
                70,
                70,
                74,
            ][idx % 15],
            velocity=44,
            instrument="keys",
        )
        for idx in range(58)
    ]
    dense_high = [
        MelodicNote(
            t_on=idx * 0.09,
            t_off=idx * 0.09 + 0.4,
            pitch=58 + (idx % 24),
            velocity=44,
            instrument="keys",
        )
        for idx in range(90)
    ]

    assert _keys_should_try_sparse_low_mid_profile(synth_like)
    assert not _keys_should_try_sparse_low_mid_profile(no_high_anchor)
    assert not _keys_should_try_sparse_low_mid_profile(dense_high)


def test_keys_sparse_low_spectral_candidate_revoices_synth_chord_clusters() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _build_keys_sparse_low_spectral_candidate,
        _keys_max_polyphony,
    )

    raw_events = [
        (0.000, 0.360, 62),
        (0.000, 0.360, 70),
        (0.000, 0.360, 82),
        (0.035, 0.870, 55),
        (0.058, 0.410, 58),
        (0.476, 0.655, 81),
        (0.685, 0.864, 82),
        (0.894, 2.894, 39),
        (0.894, 1.750, 51),
        (0.894, 1.240, 63),
        (0.894, 1.750, 75),
        (1.753, 2.253, 63),
        (1.776, 4.240, 51),
        (1.939, 2.439, 70),
        (3.217, 4.263, 46),
        (3.217, 4.263, 58),
        (3.217, 4.263, 70),
        (4.275, 7.783, 46),
        (4.275, 7.783, 58),
        (4.275, 7.783, 70),
        (4.287, 5.529, 53),
        (4.345, 7.202, 34),
        (5.099, 5.599, 65),
        (6.389, 6.889, 58),
        (6.842, 7.342, 58),
        (7.399, 7.899, 38),
        (7.399, 8.399, 53),
        (7.399, 8.399, 65),
        (7.678, 11.010, 41),
        (8.446, 11.070, 53),
        (8.446, 11.070, 65),
        (8.446, 11.070, 77),
        (9.363, 10.000, 60),
        (9.514, 10.214, 65),
        (9.514, 10.214, 70),
        (10.212, 10.700, 60),
        (10.212, 10.700, 65),
        (10.397, 10.900, 65),
        (10.397, 10.900, 70),
        (10.676, 11.176, 48),
        (11.059, 11.920, 43),
        (11.059, 11.920, 55),
        (11.129, 11.460, 31),
        (11.129, 11.460, 55),
        (11.245, 11.910, 50),
        (11.245, 11.910, 70),
        (11.477, 11.990, 31),
        (11.477, 11.990, 65),
        (2.200, 3.000, 58),
        (5.400, 6.200, 58),
        (6.000, 6.800, 65),
        (9.800, 10.600, 58),
    ]
    notes = [
        MelodicNote(t_on=t_on, t_off=t_off, pitch=pitch, velocity=42, instrument="keys")
        for t_on, t_off, pitch in raw_events
    ]
    spectral: dict[float, dict[int, float]] = {
        0.000: {36: 20.0, 55: 20.0, 58: 25.0, 62: 20.0, 70: 20.0, 77: 12.0, 82: 30.0},
        0.476: {81: 20.0},
        0.685: {82: 20.0},
        0.894: {39: 20.0, 51: 20.0, 58: 20.0, 63: 20.0, 70: 10.0, 75: 20.0, 82: 20.0},
        3.217: {39: 20.0, 51: 20.0, 58: 20.0, 63: 20.0, 70: 10.0, 82: 10.0, 84: 20.0},
        4.345: {34: 20.0, 46: 20.0, 53: 20.0, 58: 20.0, 65: 20.0, 82: 10.0, 84: 10.0},
        7.678: {41: 20.0, 53: 20.0, 60: 10.0, 70: 10.0, 72: 15.0, 77: 10.0, 84: 10.0},
        11.129: {43: 20.0, 55: 20.0, 67: 10.0, 74: 10.0, 77: 10.0, 82: 10.0, 84: 10.0},
    }

    def fake_energy(center: float, pitches: Iterable[int]) -> dict[int, float]:
        closest = min(spectral, key=lambda value: abs(value - center))
        values = spectral[closest] if abs(closest - center) <= 0.08 else {}
        return {int(pitch): float(values.get(int(pitch), 0.0)) for pitch in pitches}

    candidate = _build_keys_sparse_low_spectral_candidate(notes, fake_energy)

    assert len(candidate) == 44
    assert _keys_max_polyphony(candidate) <= 7
    assert any(note.pitch == 36 for note in candidate if abs(note.t_on - 0.0) <= 0.055)
    assert any(note.pitch == 81 for note in candidate if abs(note.t_on - 0.476) <= 0.055)
    assert any(note.pitch == 84 for note in candidate if abs(note.t_on - 3.217) <= 0.055)
    assert any(note.pitch == 74 for note in candidate if abs(note.t_on - 11.129) <= 0.055)
    assert not any(abs(note.t_on - 10.676) <= 0.055 for note in candidate)
    assert not any(abs(note.t_on - 11.477) <= 0.055 for note in candidate)


def test_keys_sparse_low_spectral_candidate_revoices_local_full_song_window() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _apply_keys_sparse_low_spectral_chord_candidate_with_probe,
        _keys_max_polyphony,
    )

    filler = [
        MelodicNote(
            t_on=idx * 0.75,
            t_off=idx * 0.75 + 0.7,
            pitch=60 + (idx % 7),
            velocity=42,
            instrument="keys",
        )
        for idx in range(80)
    ]
    local_window_start = 120.0
    local: list[MelodicNote] = []
    for idx in range(8):
        start = local_window_start + idx * 1.2
        for pitch in (34, 46, 51, 58, 63):
            local.append(
                MelodicNote(
                    t_on=start,
                    t_off=start + 0.9,
                    pitch=pitch,
                    velocity=42,
                    instrument="keys",
                )
            )
        if idx < 4:
            local.append(
                MelodicNote(
                    t_on=start,
                    t_off=start + 0.3,
                    pitch=82,
                    velocity=42,
                    instrument="keys",
                )
            )

    notes = filler + local

    def fake_energy(center: float, pitches: Iterable[int]) -> dict[int, float]:
        local_center = center - local_window_start
        values: dict[int, float] = {}
        if -0.1 <= local_center <= 10.0:
            values.update({34: 20.0, 46: 20.0, 58: 22.0, 70: 12.0, 82: 18.0})
        return {int(pitch): float(values.get(int(pitch), 0.0)) for pitch in pitches}

    result = _apply_keys_sparse_low_spectral_chord_candidate_with_probe(
        notes,
        fake_energy,
        stem_path=None,
    )

    assert result != notes
    assert _keys_max_polyphony(result) <= 7
    assert sum(1 for note in result if note.t_on < 80.0) == len(filler)
    assert any(
        abs(note.t_on - local_window_start) <= 0.055
        and note.pitch == 82
        and note.velocity == 24
        for note in result
    )


def test_keys_dense_low_spectral_restore_adds_upper_under_recalled_voices() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _keys_max_polyphony,
        _keys_should_restore_dense_low_spectral_treble,
        _restore_keys_dense_low_spectral_treble_with_probe,
    )

    notes: list[MelodicNote] = []
    for idx in range(50):
        start = round(idx * 0.285, 6)
        bass_pitch = 39 if idx % 5 == 0 else 58
        notes.append(
            MelodicNote(
                t_on=start,
                t_off=round(start + 0.5, 6),
                pitch=bass_pitch,
                velocity=42,
                instrument="keys",
            )
        )
        notes.append(
            MelodicNote(
                t_on=start,
                t_off=round(start + 0.5, 6),
                pitch=70,
                velocity=42,
                instrument="keys",
            )
        )
        if idx < 10:
            notes.append(
                MelodicNote(
                    t_on=start,
                    t_off=round(start + 0.5, 6),
                    pitch=77,
                    velocity=42,
                    instrument="keys",
                )
            )

    assert len(notes) == 110
    assert _keys_max_polyphony(notes) <= 7
    assert _keys_should_restore_dense_low_spectral_treble(notes)

    def fake_energy(center: float, pitches: Iterable[int]) -> dict[int, float]:
        values: dict[int, float] = {70: 60.0, 77: 80.0}
        if center <= 0.05:
            values[77] = 120.0
            values[89] = 45.0
        return {int(pitch): float(values.get(int(pitch), 0.0)) for pitch in pitches}

    restored = _restore_keys_dense_low_spectral_treble_with_probe(notes, fake_energy)

    assert len(restored) > len(notes)
    assert _keys_max_polyphony(restored) <= 7
    assert any(note.pitch == 89 for note in restored if abs(note.t_on - 0.0) <= 0.055)
    assert any(note.pitch == 77 for note in restored if abs(note.t_on - 2.85) <= 0.055)

    already_has_high = notes + [
        MelodicNote(
            t_on=14.3,
            t_off=14.8,
            pitch=85,
            velocity=42,
            instrument="keys",
        )
    ]
    assert not _keys_should_restore_dense_low_spectral_treble(already_has_high)


def test_keys_basic_pitch_release_sustain_leaves_sparse_or_low_notes_alone() -> None:
    from aural_ingest.transcription import MelodicNote, _apply_keys_basic_pitch_release_sustain

    sparse = [
        MelodicNote(
            t_on=float(idx),
            t_off=float(idx) + 0.25,
            pitch=72,
            velocity=42,
            instrument="keys",
        )
        for idx in range(8)
    ]
    low_dense = [
        MelodicNote(
            t_on=idx * 0.1,
            t_off=idx * 0.1 + 0.2,
            pitch=40 + (idx % 4),
            velocity=42,
            instrument="keys",
        )
        for idx in range(30)
    ]

    assert _apply_keys_basic_pitch_release_sustain(sparse) == sparse
    assert _apply_keys_basic_pitch_release_sustain(low_dense) == low_dense


def test_keys_onset_peak_alignment_moves_clusters_to_audio_peaks() -> None:
    from pytest import approx

    from aural_ingest.transcription import (
        MelodicNote,
        _apply_keys_onset_peak_alignment,
        _keys_max_polyphony,
    )

    notes = [
        MelodicNote(t_on=0.0, t_off=0.18, pitch=60, velocity=42, instrument="keys"),
        MelodicNote(t_on=0.012, t_off=0.19, pitch=64, velocity=44, instrument="keys"),
        MelodicNote(t_on=0.5, t_off=0.68, pitch=67, velocity=45, instrument="keys"),
        MelodicNote(t_on=0.512, t_off=0.7, pitch=72, velocity=46, instrument="keys"),
    ]

    aligned = _apply_keys_onset_peak_alignment(
        notes,
        envelope_times=[0.0, 0.032, 0.5, 0.532],
        envelope_values=[0.05, 0.9, 0.05, 1.0],
        peak_times=[0.032, 0.532],
        peak_values=[0.9, 1.0],
    )

    assert [note.pitch for note in aligned] == [60, 64, 67, 72]
    assert [note.t_on for note in aligned] == approx([0.032, 0.044, 0.532, 0.544])
    assert [note.t_off for note in aligned] == approx([0.212, 0.222, 0.712, 0.732])
    assert _keys_max_polyphony(aligned) <= 7


def test_keys_onset_peak_alignment_rejects_polyphony_breaking_shift() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _apply_keys_onset_peak_alignment,
        _keys_max_polyphony,
    )

    notes = [
        MelodicNote(t_on=0.0, t_off=0.03, pitch=48, velocity=42, instrument="keys"),
        *[
            MelodicNote(
                t_on=0.06,
                t_off=0.2,
                pitch=60 + idx,
                velocity=42,
                instrument="keys",
            )
            for idx in range(7)
        ],
    ]

    aligned = _apply_keys_onset_peak_alignment(
        notes,
        envelope_times=[0.0, 0.059, 0.12],
        envelope_values=[0.05, 1.0, 0.05],
        peak_times=[0.059],
        peak_values=[1.0],
    )

    assert _keys_max_polyphony(notes) == 7
    assert aligned == notes


def test_keys_basic_pitch_profile_selector_prefers_guarded_alternates() -> None:
    from aural_ingest.transcription import (
        KEYS_BASIC_PITCH_AGGRESSIVE_PROFILE,
        KEYS_BASIC_PITCH_BALANCED_PROFILE,
        KEYS_BASIC_PITCH_MID_PROFILE,
        _KeysPlayableProfileFeatures,
        _choose_keys_basic_pitch_playable_profile,
    )

    assert (
        _choose_keys_basic_pitch_playable_profile(
            _KeysPlayableProfileFeatures(
                note_count=124,
                density_per_minute=595.7,
                mean_attack_cluster_size=2.08,
                min_pitch=35,
                mean_duration_sec=0.59,
            )
        )
        == KEYS_BASIC_PITCH_MID_PROFILE
    )
    assert (
        _choose_keys_basic_pitch_playable_profile(
            _KeysPlayableProfileFeatures(
                note_count=50,
                density_per_minute=259.0,
                mean_attack_cluster_size=1.35,
                min_pitch=47,
                mean_duration_sec=0.81,
            )
        )
        == KEYS_BASIC_PITCH_AGGRESSIVE_PROFILE
    )
    assert (
        _choose_keys_basic_pitch_playable_profile(
            _KeysPlayableProfileFeatures(
                note_count=88,
                density_per_minute=443.4,
                mean_attack_cluster_size=2.23,
                min_pitch=35,
                mean_duration_sec=0.78,
            )
        )
        == KEYS_BASIC_PITCH_AGGRESSIVE_PROFILE
    )
    assert (
        _choose_keys_basic_pitch_playable_profile(
            _KeysPlayableProfileFeatures(
                note_count=47,
                density_per_minute=225.8,
                mean_attack_cluster_size=1.47,
                min_pitch=31,
                mean_duration_sec=1.13,
            )
        )
        == KEYS_BASIC_PITCH_BALANCED_PROFILE
    )


def test_keys_playable_default_candidate_rescues_sparse_pitch_spray() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _choose_keys_playable_default_candidate,
    )

    profile_notes = [
        MelodicNote(
            t_on=idx * 0.32,
            t_off=idx * 0.32 + 0.8,
            pitch=21 + ((idx * 2) % 88),
            velocity=42,
            instrument="keys",
        )
        for idx in range(38)
    ]
    default_notes = [
        MelodicNote(
            t_on=idx * 0.38,
            t_off=idx * 0.38 + 0.5,
            pitch=35 + (idx % 32),
            velocity=42,
            instrument="keys",
        )
        for idx in range(32)
    ]

    assert (
        _choose_keys_playable_default_candidate(profile_notes, default_notes)
        == default_notes
    )


def test_keys_playable_default_candidate_keeps_small_dense_recall_gain() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _choose_keys_playable_default_candidate,
    )

    profile_notes = [
        MelodicNote(
            t_on=idx * 0.1,
            t_off=idx * 0.1 + 0.3,
            pitch=50 + (idx % 12),
            velocity=42,
            instrument="keys",
        )
        for idx in range(110)
    ]
    default_notes = [
        MelodicNote(
            t_on=idx * 0.09,
            t_off=idx * 0.09 + 0.3,
            pitch=50 + (idx % 12),
            velocity=42,
            instrument="keys",
        )
        for idx in range(124)
    ]

    assert (
        _choose_keys_playable_default_candidate(profile_notes, default_notes)
        == default_notes
    )


def test_keys_playable_default_candidate_prefers_tied_fewer_low_artifact_candidate() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _choose_keys_playable_default_candidate,
    )

    profile_notes = [
        MelodicNote(
            t_on=idx * 0.24,
            t_off=idx * 0.24 + 0.46,
            pitch=21 + (idx % 24),
            velocity=42,
            instrument="keys",
        )
        for idx in range(80)
    ]
    default_notes = [
        MelodicNote(
            t_on=idx * 0.27,
            t_off=idx * 0.27 + 0.42,
            pitch=21 + (idx % 24),
            velocity=42,
            instrument="keys",
        )
        for idx in range(70)
    ]

    assert (
        _choose_keys_playable_default_candidate(profile_notes, default_notes)
        == default_notes
    )


def test_keys_playable_default_candidate_rejects_dense_flood() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _choose_keys_playable_default_candidate,
    )

    profile_notes = [
        MelodicNote(
            t_on=idx * 0.1,
            t_off=idx * 0.1 + 0.3,
            pitch=50 + (idx % 12),
            velocity=42,
            instrument="keys",
        )
        for idx in range(110)
    ]
    default_notes = [
        MelodicNote(
            t_on=idx * 0.07,
            t_off=idx * 0.07 + 0.3,
            pitch=50 + (idx % 12),
            velocity=42,
            instrument="keys",
        )
        for idx in range(155)
    ]

    assert (
        _choose_keys_playable_default_candidate(profile_notes, default_notes)
        == profile_notes
    )


def test_keys_playable_loose_candidate_removes_sparse_low_artifacts() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _choose_keys_playable_loose_candidate,
    )

    selected_notes = [
        MelodicNote(
            t_on=idx * 0.245,
            t_off=idx * 0.245 + 0.75,
            pitch=31 if idx % 6 == 0 else 49 + (idx % 37),
            velocity=42,
            instrument="keys",
        )
        for idx in range(48)
    ]
    loose_notes = [
        MelodicNote(
            t_on=idx * 0.39,
            t_off=idx * 0.39 + 0.75,
            pitch=49 + (idx % 37),
            velocity=42,
            instrument="keys",
        )
        for idx in range(27)
    ]

    assert (
        _choose_keys_playable_loose_candidate(selected_notes, loose_notes)
        == loose_notes
    )


def test_keys_playable_loose_candidate_rejects_sustained_low_register() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _choose_keys_playable_loose_candidate,
    )

    selected_notes = [
        MelodicNote(
            t_on=idx * 0.28,
            t_off=idx * 0.28 + 1.15,
            pitch=31 if idx % 5 == 0 else 52 + (idx % 24),
            velocity=42,
            instrument="keys",
        )
        for idx in range(47)
    ]
    loose_notes = [
        MelodicNote(
            t_on=idx * 0.32,
            t_off=idx * 0.32 + 1.15,
            pitch=34 if idx % 8 == 0 else 52 + (idx % 24),
            velocity=42,
            instrument="keys",
        )
        for idx in range(38)
    ]

    assert (
        _choose_keys_playable_loose_candidate(selected_notes, loose_notes)
        == selected_notes
    )


def test_keys_sparse_low_flood_clamp_removes_full_song_bass_spray() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _apply_keys_sparse_low_flood_clamp,
    )

    low_spray = [
        MelodicNote(
            t_on=idx * 1.25,
            t_off=idx * 1.25 + 0.24,
            pitch=21 + (idx % 15),
            velocity=72,
            instrument="keys",
        )
        for idx in range(56)
    ]
    playable_mid = [
        MelodicNote(
            t_on=idx * 1.62 + 0.08,
            t_off=idx * 1.62 + 0.92,
            pitch=45 + (idx % 30),
            velocity=84,
            instrument="keys",
        )
        for idx in range(74)
    ]
    high_spray = [
        MelodicNote(
            t_on=idx * 11.0 + 0.21,
            t_off=idx * 11.0 + 0.44,
            pitch=89 + (idx % 6),
            velocity=76,
            instrument="keys",
        )
        for idx in range(10)
    ]
    notes = sorted([*low_spray, *playable_mid, *high_spray], key=lambda note: note.t_on)

    clamped = _apply_keys_sparse_low_flood_clamp(notes)

    assert len(clamped) == len(playable_mid)
    assert min(note.pitch for note in clamped) >= 36
    assert max(note.pitch for note in clamped) <= 88
    assert all(note in playable_mid for note in clamped)


def test_keys_sparse_low_flood_clamp_keeps_dense_low_material() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _apply_keys_sparse_low_flood_clamp,
    )

    notes = [
        MelodicNote(
            t_on=idx * 0.075,
            t_off=idx * 0.075 + 0.4,
            pitch=31 if idx % 3 == 0 else 48 + (idx % 24),
            velocity=82,
            instrument="keys",
        )
        for idx in range(150)
    ]

    assert _apply_keys_sparse_low_flood_clamp(notes) == notes


def test_keys_full_song_velocity_calibration_scales_long_high_confidence_output() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _apply_keys_full_song_velocity_calibration,
    )

    notes = [
        MelodicNote(
            t_on=idx * 0.38,
            t_off=idx * 0.38 + 0.32,
            pitch=48 + (idx % 36),
            velocity=80 + (idx % 20),
            instrument="keys",
        )
        for idx in range(520)
    ]

    calibrated = _apply_keys_full_song_velocity_calibration(notes)

    assert calibrated != notes
    assert [note.pitch for note in calibrated] == [note.pitch for note in notes]
    assert calibrated[0].velocity == 36
    assert calibrated[19].velocity == 45


def test_keys_full_song_velocity_calibration_keeps_soft_or_dense_output() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _apply_keys_full_song_velocity_calibration,
    )

    soft_notes = [
        MelodicNote(
            t_on=idx * 0.38,
            t_off=idx * 0.38 + 0.32,
            pitch=48 + (idx % 36),
            velocity=42,
            instrument="keys",
        )
        for idx in range(520)
    ]
    dense_notes = [
        MelodicNote(
            t_on=idx * 0.12,
            t_off=idx * 0.12 + 0.32,
            pitch=48 + (idx % 36),
            velocity=84,
            instrument="keys",
        )
        for idx in range(520)
    ]

    assert _apply_keys_full_song_velocity_calibration(soft_notes) == soft_notes
    assert _apply_keys_full_song_velocity_calibration(dense_notes) == dense_notes


def test_keys_sparse_synth_transient_cull_removes_short_full_song_clutter() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _apply_keys_sparse_synth_transient_cull,
    )

    notes = [
        MelodicNote(
            t_on=idx * 0.24,
            t_off=idx * 0.24 + (0.5 if idx < 115 else 1.1),
            pitch=29 if idx == 0 else 89 if idx == 1 else 50 + (idx % 36),
            velocity=40,
            instrument="keys",
        )
        for idx in range(247)
    ]

    culled = _apply_keys_sparse_synth_transient_cull(notes)

    assert len(culled) == 132
    assert all(note.t_off - note.t_on >= 0.8 for note in culled)
    assert {note.pitch for note in notes if note.pitch in {29, 89}} - {
        note.pitch for note in culled
    } == {29, 89}


def test_keys_sparse_synth_transient_cull_keeps_other_full_song_shapes() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _apply_keys_sparse_synth_transient_cull,
    )

    keyboard_like_long_span = [
        MelodicNote(
            t_on=idx * 0.52,
            t_off=idx * 0.52 + (0.5 if idx < 115 else 1.1),
            pitch=31 if idx == 0 else 86 if idx == 1 else 50 + (idx % 36),
            velocity=42,
            instrument="keys",
        )
        for idx in range(247)
    ]
    dense_short_window = [
        MelodicNote(
            t_on=idx * 0.04,
            t_off=idx * 0.04 + 0.6,
            pitch=29 if idx == 0 else 89 if idx == 1 else 50 + (idx % 36),
            velocity=42,
            instrument="keys",
        )
        for idx in range(247)
    ]

    assert _apply_keys_sparse_synth_transient_cull(keyboard_like_long_span) == keyboard_like_long_span
    assert _apply_keys_sparse_synth_transient_cull(dense_short_window) == dense_short_window


def test_keys_long_sparse_duration_cull_removes_low_range_clutter() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _apply_keys_long_sparse_duration_cull,
    )

    notes = [
        MelodicNote(
            t_on=idx * 1.04,
            t_off=idx * 1.04 + (0.28 if idx < 163 else 1.2),
            pitch=36
            if idx % 4 == 0
            else 38
            if idx % 4 == 1
            else 45 + (idx % 22),
            velocity=42,
            instrument="keys",
        )
        for idx in range(197)
    ]

    culled = _apply_keys_long_sparse_duration_cull(notes)

    assert len(culled) == 34
    assert all(note.t_off - note.t_on >= 1.0 for note in culled)
    assert max(note.pitch for note in culled) <= 66


def test_keys_long_sparse_duration_cull_uses_lighter_upper_range_floor() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _apply_keys_long_sparse_duration_cull,
    )

    notes = [
        MelodicNote(
            t_on=idx * 2.5,
            t_off=idx * 2.5 + (0.32 if idx < 54 else 0.55),
            pitch=36 if idx % 5 == 0 else 72 + (idx % 14),
            velocity=42,
            instrument="keys",
        )
        for idx in range(78)
    ]

    culled = _apply_keys_long_sparse_duration_cull(notes)

    assert len(culled) == 24
    assert all(note.t_off - note.t_on >= 0.5 for note in culled)
    assert max(note.pitch for note in notes) > 70


def test_keys_long_sparse_duration_cull_keeps_dense_or_non_low_shapes() -> None:
    from aural_ingest.transcription import (
        MelodicNote,
        _apply_keys_long_sparse_duration_cull,
    )

    dense_short_span = [
        MelodicNote(
            t_on=idx * 0.25,
            t_off=idx * 0.25 + (0.28 if idx < 163 else 1.2),
            pitch=36 if idx % 4 == 0 else 45 + (idx % 22),
            velocity=42,
            instrument="keys",
        )
        for idx in range(197)
    ]
    non_low_shape = [
        MelodicNote(
            t_on=idx * 1.04,
            t_off=idx * 1.04 + (0.28 if idx < 163 else 1.2),
            pitch=40 if idx % 4 == 0 else 45 + (idx % 22),
            velocity=42,
            instrument="keys",
        )
        for idx in range(197)
    ]

    assert _apply_keys_long_sparse_duration_cull(dense_short_span) == dense_short_span
    assert _apply_keys_long_sparse_duration_cull(non_low_shape) == non_low_shape


def test_piano_basic_pitch_playable_uses_recall_pass_only_for_dense_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aural_ingest import transcription
    from aural_ingest.algorithms import melodic_basic_pitch
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "keys.wav"
    stem.write_bytes(b"audio")
    calls: list[tuple[float, float, float]] = []

    dense_primary = [
        MelodicNote(
            t_on=idx * 0.1,
            t_off=idx * 0.1 + 0.08,
            pitch=60 + (idx % 4),
            velocity=82,
            instrument="keys",
        )
        for idx in range(32)
    ]
    recall = [
        MelodicNote(t_on=0.405, t_off=0.75, pitch=72, velocity=86, instrument="keys"),
    ]

    def fake_transcribe(_stem: Path, **kwargs: object) -> list[MelodicNote]:
        calls.append(
            (
                float(kwargs.get("onset_threshold", 0.5)),
                float(kwargs.get("frame_threshold", 0.3)),
                float(kwargs.get("minimum_note_length_ms", 127.7)),
            )
        )
        if len(calls) == 1:
            return dense_primary
        return recall

    monkeypatch.setattr(melodic_basic_pitch, "transcribe", fake_transcribe)
    monkeypatch.setattr(transcription, "resolve_basic_pitch_model_path", lambda _roots: None)

    registry = transcription.build_default_melodic_algorithm_registry(instrument="keys")
    restored = registry["piano_basic_pitch_playable"](stem)

    assert calls == [(0.6, 0.25, 100.0), (0.5, 0.3, 127.7)]
    restored_note = next(note for note in restored if note.pitch == 72)
    assert restored_note.t_off - restored_note.t_on >= 1.0


def test_piano_basic_pitch_playable_runs_aggressive_profile_for_upper_sparse_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aural_ingest import transcription
    from aural_ingest.algorithms import melodic_basic_pitch
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "keys.wav"
    stem.write_bytes(b"audio")
    calls: list[tuple[float, float, float]] = []

    balanced_sparse = [
        MelodicNote(
            t_on=float(idx),
            t_off=float(idx) + 0.25,
            pitch=60 + (idx % 4),
            velocity=82,
            instrument="keys",
        )
        for idx in range(8)
    ]
    aggressive_sparse = [
        MelodicNote(t_on=0.0, t_off=0.4, pitch=72, velocity=84, instrument="keys"),
    ]

    def fake_transcribe(_stem: Path, **kwargs: object) -> list[MelodicNote]:
        calls.append(
            (
                float(kwargs.get("onset_threshold", 0.5)),
                float(kwargs.get("frame_threshold", 0.3)),
                float(kwargs.get("minimum_note_length_ms", 127.7)),
            )
        )
        if len(calls) == 1:
            return balanced_sparse
        return aggressive_sparse

    monkeypatch.setattr(melodic_basic_pitch, "transcribe", fake_transcribe)
    monkeypatch.setattr(transcription, "resolve_basic_pitch_model_path", lambda _roots: None)

    registry = transcription.build_default_melodic_algorithm_registry(instrument="keys")
    notes = registry["piano_basic_pitch_playable"](stem)

    assert calls == [(0.6, 0.25, 100.0), (0.7, 0.25, 127.7)]
    assert notes == aggressive_sparse


def test_piano_basic_pitch_playable_runs_mid_profile_for_very_dense_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aural_ingest import transcription
    from aural_ingest.algorithms import melodic_basic_pitch
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "keys.wav"
    stem.write_bytes(b"audio")
    calls: list[tuple[float, float, float]] = []

    balanced_dense = [
        MelodicNote(
            t_on=idx * 0.05,
            t_off=idx * 0.05 + 0.03,
            pitch=60 + (idx % 7),
            velocity=82,
            instrument="keys",
        )
        for idx in range(130)
    ]
    mid_dense = [
        MelodicNote(t_on=0.0, t_off=0.2, pitch=76, velocity=82, instrument="keys"),
    ]

    def fake_transcribe(_stem: Path, **kwargs: object) -> list[MelodicNote]:
        calls.append(
            (
                float(kwargs.get("onset_threshold", 0.5)),
                float(kwargs.get("frame_threshold", 0.3)),
                float(kwargs.get("minimum_note_length_ms", 127.7)),
            )
        )
        if len(calls) == 1:
            return balanced_dense
        if len(calls) == 2:
            return []
        return mid_dense

    monkeypatch.setattr(melodic_basic_pitch, "transcribe", fake_transcribe)
    monkeypatch.setattr(transcription, "resolve_basic_pitch_model_path", lambda _roots: None)

    registry = transcription.build_default_melodic_algorithm_registry(instrument="keys")
    notes = registry["piano_basic_pitch_playable"](stem)

    assert calls == [(0.6, 0.25, 100.0), (0.5, 0.3, 127.7), (0.65, 0.25, 127.7)]
    assert notes == mid_dense


def test_piano_basic_pitch_playable_runs_mid_profile_for_sparse_low_synth_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aural_ingest import transcription
    from aural_ingest.algorithms import melodic_basic_pitch
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "keys.wav"
    stem.write_bytes(b"audio")
    calls: list[tuple[float, float, float]] = []

    balanced_sparse_low = [
        MelodicNote(
            t_on=idx * 0.2,
            t_off=idx * 0.2 + 0.7,
            pitch=[
                31,
                34,
                38,
                41,
                46,
                50,
                53,
                58,
                60,
                62,
                63,
                65,
                70,
                70,
                82,
            ][idx % 15],
            velocity=82,
            instrument="keys",
        )
        for idx in range(58)
    ]
    mid_sparse_low = [
        MelodicNote(t_on=0.0, t_off=0.7, pitch=77, velocity=82, instrument="keys"),
    ]

    def fake_transcribe(_stem: Path, **kwargs: object) -> list[MelodicNote]:
        calls.append(
            (
                float(kwargs.get("onset_threshold", 0.5)),
                float(kwargs.get("frame_threshold", 0.3)),
                float(kwargs.get("minimum_note_length_ms", 127.7)),
            )
        )
        if len(calls) == 1:
            return balanced_sparse_low
        if len(calls) == 2:
            return mid_sparse_low
        raise RuntimeError("default profile intentionally unavailable")

    monkeypatch.setattr(melodic_basic_pitch, "transcribe", fake_transcribe)
    monkeypatch.setattr(transcription, "resolve_basic_pitch_model_path", lambda _roots: None)
    monkeypatch.setattr(
        transcription,
        "_apply_keys_basic_pitch_release_sustain",
        lambda notes: notes,
    )

    registry = transcription.build_default_melodic_algorithm_registry(instrument="keys")
    notes = registry["piano_basic_pitch_playable"](stem)

    assert calls == [(0.6, 0.25, 100.0), (0.65, 0.25, 127.7), (0.5, 0.3, 127.7)]
    assert notes == mid_sparse_low


def test_piano_basic_pitch_playable_runs_loose_profile_for_sparse_low_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aural_ingest import transcription
    from aural_ingest.algorithms import melodic_basic_pitch
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "keys.wav"
    stem.write_bytes(b"audio")
    calls: list[tuple[float, float, float]] = []

    selected_sparse = [
        MelodicNote(
            t_on=idx * 0.245,
            t_off=idx * 0.245 + 0.75,
            pitch=31 if idx % 6 == 0 else 49 + (idx % 37),
            velocity=82,
            instrument="keys",
        )
        for idx in range(48)
    ]
    loose_sparse = [
        MelodicNote(
            t_on=idx * 0.39,
            t_off=idx * 0.39 + 0.75,
            pitch=49 + (idx % 37),
            velocity=82,
            instrument="keys",
        )
        for idx in range(27)
    ]

    def fake_transcribe(_stem: Path, **kwargs: object) -> list[MelodicNote]:
        calls.append(
            (
                float(kwargs.get("onset_threshold", 0.5)),
                float(kwargs.get("frame_threshold", 0.3)),
                float(kwargs.get("minimum_note_length_ms", 127.7)),
            )
        )
        if len(calls) == 1:
            return selected_sparse
        if len(calls) == 2:
            return selected_sparse
        return loose_sparse

    monkeypatch.setattr(melodic_basic_pitch, "transcribe", fake_transcribe)
    monkeypatch.setattr(transcription, "resolve_basic_pitch_model_path", lambda _roots: None)

    registry = transcription.build_default_melodic_algorithm_registry(instrument="keys")
    notes = registry["piano_basic_pitch_playable"](stem)

    assert calls == [(0.6, 0.25, 100.0), (0.5, 0.3, 127.7), (0.55, 0.2, 80.0)]
    assert [
        (round(note.t_on, 6), round(note.t_off, 6), note.pitch, note.velocity)
        for note in notes
    ] == [
        (round(note.t_on, 6), round(note.t_off, 6), note.pitch, note.velocity)
        for note in loose_sparse
    ]


def test_transcribe_all_melodic_stems_applies_keys_playability_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from aural_ingest import transcription
    from aural_ingest.transcription import MelodicNote

    stem = tmp_path / "keys.wav"
    stem.write_bytes(b"audio")
    raw_notes = [
        MelodicNote(t_on=0.0, t_off=1.0, pitch=pitch, velocity=60 + idx, instrument="keys")
        for idx, pitch in enumerate([31, 36, 43, 48, 52, 55, 60, 64, 67, 71, 76])
    ]

    monkeypatch.setattr(
        transcription,
        "build_default_melodic_algorithm_registry",
        lambda instrument="keys": {"piano_auto": lambda _path: raw_notes},
    )

    results = transcription.transcribe_all_melodic_stems(
        {"keys": stem},
        requested_method="piano_auto",
    )

    assert len(results) == 1
    assert results[0].used_method == "piano_auto"
    assert len(results[0].notes) == 7
    assert {note.pitch for note in results[0].notes}.issuperset({36, 76})


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
        "melodic_adaptive",
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
    mido = pytest.importorskip("mido")

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
    mido = pytest.importorskip("mido")
    import numpy as np
    import sys
    import types

    from aural_ingest.transcription import _transcribe_drums_mt3_events

    # MT3 device now flows through select_device(); pin it so the assertion is
    # deterministic regardless of the host's CUDA availability.
    monkeypatch.setenv("AURAL_MT3_DEVICE", "cpu")

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
    # mt3_infer is a stub here, so the real transformers/MT3 patching is a no-op;
    # bypass it to keep this test independent of the (heavy + env-sensitive) transformers
    # + tensorflow imports it would otherwise trigger.
    monkeypatch.setattr(
        "aural_ingest.transcription.ensure_mt3_transformers_compat", lambda: None
    )
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
    mido = pytest.importorskip("mido")
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
    # mt3_infer is a stub here, so the real transformers/MT3 patching is a no-op;
    # bypass it to keep this test independent of the (heavy + env-sensitive) transformers
    # + tensorflow imports it would otherwise trigger.
    monkeypatch.setattr(
        "aural_ingest.transcription.ensure_mt3_transformers_compat", lambda: None
    )
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


def test_keys_only_method_does_not_hijack_other_instruments() -> None:
    """An explicit piano engine must apply to keys only.

    ``--melodic-method`` builds one instrument-agnostic chain, so before this
    guard a keys-focused override was also handed the bass, guitar and vocal
    stems. On a real import that dropped bass to 0.50 and returned 81 notes for
    a 17-minute vocal.
    """
    from aural_ingest.transcription import melodic_fallback_chain

    assert melodic_fallback_chain("piano_transkun", instrument="keys")[0] == "piano_transkun"

    for instrument in ("bass", "vocals", "lead_guitar", "rhythm_guitar"):
        chain = melodic_fallback_chain("piano_transkun", instrument=instrument)
        assert chain == melodic_fallback_chain("auto", instrument=instrument)
        assert not chain[0].startswith("piano_")


def test_keys_only_method_guard_leaves_non_piano_overrides_global() -> None:
    """Only piano engines are keys-scoped; other explicit overrides are unchanged."""
    from aural_ingest.transcription import keys_only_melodic_method, melodic_fallback_chain

    assert keys_only_melodic_method("piano_transkun") is True
    assert keys_only_melodic_method("piano_auto") is True
    assert keys_only_melodic_method("torchcrepe") is False
    assert keys_only_melodic_method("auto") is False

    assert melodic_fallback_chain("torchcrepe", instrument="bass")[0] == "torchcrepe"
    assert melodic_fallback_chain("torchcrepe", instrument="keys")[0] == "torchcrepe"
