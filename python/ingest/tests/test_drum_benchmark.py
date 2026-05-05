import json
from pathlib import Path
import numpy as np
import pytest
import soundfile as sf


def test_load_drum_reference_json_filters_to_drum_tracks_and_normalizes_aliases(tmp_path: Path) -> None:
    from aural_ingest.drum_benchmark import load_drum_reference

    reference = tmp_path / "reference.json"
    reference.write_text(
        json.dumps(
            {
                "tracks": [
                    {"track_id": "drums_main", "role": "drums"},
                    {"track_id": "guitar_main", "role": "guitar"},
                ],
                "onsets": [
                    {"track_id": "guitar_main", "t": 0.25, "class": "snare"},
                    {"track_id": "drums_main", "t": 0.50, "class": "Snare"},
                    {"track_id": "drums_main", "t": 0.75, "class": "hh_closed"},
                    {"track_id": "drums_main", "t": 1.00, "note": 47},
                    {"track_id": "drums_main", "t": 1.25, "class": "cowbell"},
                ],
            }
        ),
        encoding="utf-8",
    )

    events, meta = load_drum_reference(reference)

    assert meta["format"] == "json"
    assert meta["selected_track_ids"] == ["drums_main"]
    assert meta["ignored_reference_events"] == 1
    assert [round(event.time, 3) for event in events] == [0.5, 0.75, 1.0]
    assert [event.drum_class for event in events] == ["snare", "hi_hat", "tom2"]


def test_load_drum_reference_midi_reads_drum_track_and_preserves_times(tmp_path: Path) -> None:
    from aural_ingest import cli
    from aural_ingest.drum_benchmark import load_drum_reference
    from aural_ingest.transcription import DrumEvent

    reference = tmp_path / "reference.mid"
    midi_bytes = cli._build_notes_mid_bytes(
        bpm=120.0,
        beats=[],
        sections=[],
        drum_events=[
            DrumEvent(time=0.0, note=36, velocity=96),
            DrumEvent(time=0.5, note=38, velocity=104),
        ],
        melodic_notes=[],
    )
    reference.write_bytes(midi_bytes)

    events, meta = load_drum_reference(reference)

    assert meta["format"] == "midi"
    assert meta["selected_mode"] == "strict"
    assert [event.drum_class for event in events] == ["kick", "snare"]
    assert events[0].time == 0.0
    assert abs(events[1].time - 0.5) < 1e-6


def test_parse_midi_note_ons_handles_running_status_note_pairs(tmp_path: Path) -> None:
    from aural_ingest.drum_benchmark import _parse_midi_note_ons

    track_data = bytes(
        [
            0x00,
            0xFF,
            0x03,
            0x05,
            0x44,
            0x72,
            0x75,
            0x6D,
            0x73,
            0x00,
            0x99,
            0x24,
            0x60,
            0x0A,
            0x26,
            0x64,
            0x0A,
            0x89,
            0x24,
            0x00,
            0x00,
            0x26,
            0x00,
            0x00,
            0xFF,
            0x2F,
            0x00,
        ]
    )
    midi_bytes = (
        b"MThd"
        + (6).to_bytes(4, "big")
        + (0).to_bytes(2, "big")
        + (1).to_bytes(2, "big")
        + (480).to_bytes(2, "big")
        + b"MTrk"
        + len(track_data).to_bytes(4, "big")
        + track_data
    )
    reference = tmp_path / "running_status.mid"
    reference.write_bytes(midi_bytes)

    notes, _, division = _parse_midi_note_ons(reference)

    assert division == 480
    assert [(note.tick, note.note, note.velocity, note.channel, note.track_name) for note in notes] == [
        (0, 36, 96, 9, "Drums"),
        (10, 38, 100, 9, "Drums"),
    ]


def test_load_drum_reference_midi_can_normalize_start_to_audio(tmp_path: Path) -> None:
    from aural_ingest import cli
    from aural_ingest.drum_benchmark import load_drum_reference
    from aural_ingest.transcription import DrumEvent

    sample_rate = 48_000
    audio = np.zeros(sample_rate * 2, dtype=np.float32)
    audio[int(0.5 * sample_rate) : int(1.1 * sample_rate)] = 0.35
    wav_path = tmp_path / "drums.wav"
    sf.write(str(wav_path), audio, sample_rate)

    reference = tmp_path / "reference.mid"
    midi_bytes = cli._build_notes_mid_bytes(
        bpm=120.0,
        beats=[],
        sections=[],
        drum_events=[
            DrumEvent(time=1.0, note=36, velocity=96),
            DrumEvent(time=1.5, note=38, velocity=104),
        ],
        melodic_notes=[],
    )
    reference.write_bytes(midi_bytes)

    raw_events, _ = load_drum_reference(reference)
    normalized_events, meta = load_drum_reference(
        reference,
        audio_path=wav_path,
        normalize_start_to_audio=True,
    )

    assert raw_events[0].time == pytest.approx(1.0, abs=1e-6)
    assert meta["start_alignment_policy"] == "audio_start_offset"
    assert meta["start_alignment_applied"] is True
    assert meta["applied_start_offset_sec"] == pytest.approx(meta["observed_start_offset_sec"], abs=1e-6)
    assert 0.4 <= float(meta["applied_start_offset_sec"]) <= 0.6
    assert normalized_events[0].time == pytest.approx(
        raw_events[0].time - float(meta["applied_start_offset_sec"]),
        abs=1e-6,
    )


def test_evaluate_drum_transcription_surfaces_snare_confusions() -> None:
    from aural_ingest.drum_benchmark import BenchmarkEvent, evaluate_drum_transcription

    reference_events = [
        BenchmarkEvent(time=0.0, drum_class="kick"),
        BenchmarkEvent(time=0.5, drum_class="snare"),
    ]
    predicted_events = [
        BenchmarkEvent(time=0.01, drum_class="kick"),
        BenchmarkEvent(time=0.51, drum_class="tom1"),
    ]

    result = evaluate_drum_transcription(reference_events, predicted_events, tolerance_sec=0.06)

    assert result["overall"]["tp"] == 1
    assert result["overall"]["fp"] == 1
    assert result["overall"]["fn"] == 1
    assert result["per_class"]["snare"]["tp"] == 0
    assert result["per_class"]["snare"]["fn"] == 1
    assert result["per_class"]["tom1"]["fp"] == 1
    assert result["confusions"] == [
        {"reference_class": "snare", "predicted_class": "tom1", "count": 1}
    ]


def test_benchmark_fixture_midis_load_and_match_manifest() -> None:
    from aural_ingest.drum_benchmark import load_drum_reference

    root = Path(__file__).resolve().parents[3]
    fixture_dir = root / "assets" / "test_fixtures" / "drum_benchmark_midis"
    manifest = json.loads((fixture_dir / "manifest.json").read_text("utf-8"))

    assert manifest["format"] == "auralprimer_drum_benchmark_manifest_v1"
    assert len(manifest["cases"]) == 10

    lane_alias = {
        "kick": "kick",
        "snare": "snare",
        "hi_hat": "hi_hat",
        "open_hat": "hi_hat",
        "crash": "crash",
        "ride": "ride",
        "tom1": "tom1",
        "tom2": "tom2",
        "tom3": "tom3",
    }

    for case in manifest["cases"]:
        midi_path = fixture_dir / case["midi_path"]
        assert midi_path.is_file()
        events, meta = load_drum_reference(midi_path)
        assert meta["format"] == "midi"
        assert len(events) == case["event_count"]

        normalized_lanes = sorted({lane_alias[event.drum_class] for event in events})
        expected_lanes = sorted({lane_alias[lane] for lane in case["lane_set"]})
        assert normalized_lanes == expected_lanes
