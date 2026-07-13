from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _write_guitarset_case(root: Path, basename: str = "00_BN1-129-Eb_comp") -> None:
    ann_dir = root / "guitarset_annotations"
    audio_dir = root / "guitarset_mono_mic"
    ann_dir.mkdir(parents=True)
    audio_dir.mkdir(parents=True)
    (audio_dir / f"{basename}_mic.wav").write_bytes(b"fake wav")
    payload = {
        "file_metadata": {"duration": 4.0},
        "annotations": [
            {
                "namespace": "note_midi",
                "annotation_metadata": {"data_source": "0"},
                "data": [
                    {"time": 1.0, "duration": 0.5, "value": 43.9, "confidence": 0.8},
                ],
            },
            {
                "namespace": "note_midi",
                "annotation_metadata": {"data_source": "1"},
                "data": [
                    {"time": 0.5, "duration": 0.25, "value": 48.1, "confidence": None},
                ],
            },
            {
                "namespace": "chord",
                "data": [
                    {"time": 0.0, "duration": 2.0, "value": "D#:maj", "confidence": None},
                ],
            },
            {
                "namespace": "chord",
                "data": [
                    {
                        "time": 0.0,
                        "duration": 2.0,
                        "value": "D#:maj7/1",
                        "confidence": 0.9,
                    },
                ],
            },
            {
                "namespace": "key_mode",
                "data": [
                    {"time": 0.0, "duration": 4.0, "value": "Eb:major", "confidence": 1.0},
                ],
            },
        ],
    }
    (ann_dir / f"{basename}.jams").write_text(json.dumps(payload), encoding="utf-8")


def test_guitarset_yields_string_fret_chord_and_key_ground_truth(tmp_path: Path) -> None:
    from aural_ingest.dataset_adapters import guitarset

    _write_guitarset_case(tmp_path)

    case = next(guitarset.yield_cases(tmp_path, variant="mic"))

    assert case.case_id == "guitarset:00_BN1-129-Eb_comp:mic"
    assert [note.pitch for note in case.melodic_notes] == [48, 44]
    assert case.melodic_note_metadata == (
        {"string": 1, "fret": 3, "open_midi": 45},
        {"string": 0, "fret": 4, "open_midi": 40},
    )
    assert [(event.label, event.source, event.confidence) for event in case.chord_events] == [
        ("D#:maj", "mireval", None),
        ("D#:maj7/1", "pretty_midi", 0.9),
    ]
    assert [(event.key, event.mode, event.label, event.confidence) for event in case.key_events] == [
        ("Eb", "major", "Eb:major", 1.0),
    ]


def test_guitarset_string_filter_keeps_note_metadata_aligned(tmp_path: Path) -> None:
    from aural_ingest.dataset_adapters import guitarset

    _write_guitarset_case(tmp_path)

    case = next(guitarset.yield_cases(tmp_path, variant="mic", string_filter=(1,)))

    assert [note.pitch for note in case.melodic_notes] == [48]
    assert case.melodic_note_metadata == (
        {"string": 1, "fret": 3, "open_midi": 45},
    )
    assert case.metadata["string_filter"] == "1"


def test_guitarset_fingering_validation_script_accepts_adapter_metadata(
    tmp_path: Path,
) -> None:
    from aural_ingest.dataset_adapters import guitarset

    _write_guitarset_case(tmp_path)
    script = Path(__file__).resolve().parents[3] / "benchmarks" / "guitar" / "validate_guitarset_fingering.py"
    spec = importlib.util.spec_from_file_location("validate_guitarset_fingering", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    report = module.validate_cases(guitarset.yield_cases(tmp_path, variant="mic"))

    assert report["ok"] is True
    assert report["case_count"] == 1
    assert report["note_count"] == 2
    assert report["metadata_count"] == 2
    assert report["fret_min"] == 3
    assert report["fret_max"] == 4
    assert report["string_counts"] == {"0": 1, "1": 1}


def test_guitarset_fingering_validation_script_rejects_bad_metadata() -> None:
    from aural_ingest.dataset_adapters.common import GroundTruthCase
    from aural_ingest.transcription import MelodicNote

    script = Path(__file__).resolve().parents[3] / "benchmarks" / "guitar" / "validate_guitarset_fingering.py"
    spec = importlib.util.spec_from_file_location("validate_guitarset_fingering_bad", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    case = GroundTruthCase(
        case_id="bad",
        instrument="guitar",
        audio_path=Path("bad.wav"),
        duration_sec=1.0,
        melodic_notes=(
            MelodicNote(t_on=0.0, t_off=0.2, pitch=64, velocity=90, instrument="guitar"),
        ),
        melodic_note_metadata=({"string": 5, "fret": 3, "open_midi": 64},),
    )

    report = module.validate_cases([case])

    assert report["ok"] is False
    assert report["invalid_note_count"] == 1
    assert "imply fret 0" in report["invalid_examples"][0]["reason"]
