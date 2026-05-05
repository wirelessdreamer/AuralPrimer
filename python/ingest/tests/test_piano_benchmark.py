from pathlib import Path

from aural_ingest.piano_benchmark import (
    PianoBenchmarkEvent,
    benchmark_piano_algorithms,
    evaluate_piano,
    format_piano_summary,
    write_melodic_notes_midi,
)
from aural_ingest.piano_benchmark_suite import _trim_reference_to_window
from aural_ingest.transcription import MelodicNote


def test_evaluate_piano_tracks_offset_and_velocity_metrics() -> None:
    result = evaluate_piano(
        predicted=[
            MelodicNote(t_on=0.10, t_off=0.40, pitch=60, velocity=80, instrument="keys"),
            MelodicNote(t_on=0.115, t_off=0.395, pitch=60, velocity=78, instrument="keys"),
            MelodicNote(t_on=0.50, t_off=0.56, pitch=64, velocity=90, instrument="keys"),
        ],
        reference=[
            PianoBenchmarkEvent(time=0.10, pitch=60, duration=0.30, velocity=82),
            PianoBenchmarkEvent(time=0.50, pitch=64, duration=0.20, velocity=70),
        ],
        tolerance_sec=0.03,
        offset_tolerance_sec=0.05,
        velocity_tolerance=12,
    )

    assert result.tp == 2
    assert result.fp == 1
    assert result.fn == 0
    assert result.offset_tp == 1
    assert result.offset_velocity_tp == 1
    assert result.onset_only_tp == 2
    assert result.duplicate_predictions == 1
    assert round(result.note_with_offset_f1, 4) == 0.4
    assert round(result.note_with_offset_velocity_f1, 4) == 0.4
    assert round(result.duplicate_rate, 4) == 0.3333


def test_benchmark_piano_algorithms_supports_no_reference_cases(monkeypatch, tmp_path: Path) -> None:
    stem = tmp_path / "solo.wav"
    stem.write_bytes(b"not used by fake registry")

    def fake_registry(instrument: str):
        return {
            "fake_piano": lambda _path: [
                MelodicNote(t_on=0.10, t_off=0.30, pitch=60, velocity=82, instrument=instrument),
                MelodicNote(t_on=0.12, t_off=0.32, pitch=60, velocity=76, instrument=instrument),
            ]
        }

    monkeypatch.setattr("aural_ingest.piano_benchmark.build_default_melodic_algorithm_registry", fake_registry)

    results = benchmark_piano_algorithms(stem, None, ["fake_piano"], instrument="keys")

    assert results[0]["note_count"] == 2
    assert results[0]["prediction"]["duplicate_predictions"] == 1
    assert results[0]["predicted_notes"][0]["pitch"] == 60
    assert results[0]["overall"]["f1"] == 0.0


def test_write_melodic_notes_midi_writes_valid_header(tmp_path: Path) -> None:
    out = tmp_path / "piano.mid"
    write_melodic_notes_midi(
        [
            MelodicNote(t_on=0.0, t_off=0.5, pitch=60, velocity=90, instrument="keys"),
            {"t_on": 0.5, "t_off": 0.75, "pitch": 64, "velocity": 84, "instrument": "keys"},
        ],
        out,
    )

    data = out.read_bytes()
    assert data.startswith(b"MThd")
    assert b"MTrk" in data


def test_format_piano_summary_for_no_reference_cases() -> None:
    text = format_piano_summary(
        {
            "reference_available": False,
            "results": [
                {
                    "algorithm": "fake_piano",
                    "note_count": 2,
                    "prediction": {
                        "duplicate_rate": 0.5,
                        "pitch_min": 60,
                        "pitch_max": 64,
                        "mean_duration_sec": 0.25,
                        "mean_velocity": 83.0,
                    },
                }
            ],
        }
    )

    assert "fake_piano" in text
    assert "60-64" in text


def test_trim_reference_to_window_excludes_pre_window_notes() -> None:
    trimmed = _trim_reference_to_window(
        [
            PianoBenchmarkEvent(time=1.0, pitch=60, duration=0.5, velocity=80),
            PianoBenchmarkEvent(time=9.8, pitch=62, duration=0.5, velocity=80),
            PianoBenchmarkEvent(time=10.2, pitch=64, duration=0.4, velocity=80),
            PianoBenchmarkEvent(time=23.0, pitch=65, duration=0.5, velocity=80),
        ],
        start_sec=10.0,
        duration_sec=12.0,
    )

    assert [(event.time, event.pitch, event.duration) for event in trimmed] == [
        (0.0, 62, 0.3),
        (0.2, 64, 0.4),
    ]
