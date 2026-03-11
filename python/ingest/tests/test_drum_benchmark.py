import json
from pathlib import Path


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
