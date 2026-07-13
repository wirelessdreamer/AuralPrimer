from __future__ import annotations

import importlib.util
from pathlib import Path


def _vlq(value: int) -> bytes:
    value = max(0, int(value))
    buffer = value & 0x7F
    value >>= 7
    out = [buffer]
    while value:
        out.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(out)


def _write_midi(path: Path) -> None:
    body = bytearray()
    body.extend(_vlq(0) + b"\xff\x51\x03\x07\xa1\x20")  # 120 BPM.
    body.extend(_vlq(0) + bytes([0x90, 64, 92]))
    body.extend(_vlq(240) + bytes([0x80, 64, 0]))
    # Channel 9 is drum percussion and should be ignored by the melodic parser.
    body.extend(_vlq(0) + bytes([0x99, 36, 100]))
    body.extend(_vlq(120) + bytes([0x89, 36, 0]))
    body.extend(_vlq(0) + bytes([0x90, 67, 88]))
    body.extend(_vlq(480) + bytes([0x80, 67, 0]))
    body.extend(_vlq(0) + b"\xff\x2f\x00")

    path.write_bytes(
        b"MThd"
        + (6).to_bytes(4, "big")
        + (1).to_bytes(2, "big")
        + (1).to_bytes(2, "big")
        + (480).to_bytes(2, "big")
        + b"MTrk"
        + len(body).to_bytes(4, "big")
        + bytes(body)
    )


def _write_guitar_techs_case(root: Path) -> None:
    inner = root / "guitar_techs_p1_chords" / "P1_chords"
    (inner / "midi").mkdir(parents=True)
    (inner / "audio" / "directinput").mkdir(parents=True)
    (inner / "audio" / "micamp").mkdir(parents=True)
    _write_midi(inner / "midi" / "midi_Set1_aug.mid")
    (inner / "audio" / "directinput" / "directinput_Set1_aug.wav").write_bytes(b"di")
    (inner / "audio" / "micamp" / "micamp_Set1_aug.wav").write_bytes(b"mic")


def test_guitar_techs_yields_signal_specific_cases(tmp_path: Path) -> None:
    from aural_ingest.dataset_adapters import guitar_techs

    _write_guitar_techs_case(tmp_path)

    case = next(guitar_techs.yield_cases(tmp_path, signal="directinput"))

    assert case.case_id == "guitar_techs:P1_chords:midi_Set1_aug:directinput"
    assert case.audio_path.name == "directinput_Set1_aug.wav"
    assert case.metadata == {"player": "P1", "category": "chords", "signal": "directinput"}
    assert [(n.pitch, n.velocity, round(n.t_on, 3), round(n.t_off, 3)) for n in case.melodic_notes] == [
        (64, 92, 0.0, 0.25),
        (67, 88, 0.375, 0.875),
    ]

    micamp = next(guitar_techs.yield_cases(tmp_path, signal="micamp"))
    assert micamp.audio_path.name == "micamp_Set1_aug.wav"
    assert micamp.metadata["signal"] == "micamp"


def test_guitar_techs_adapter_validation_script_accepts_adapter_cases(tmp_path: Path) -> None:
    from aural_ingest.dataset_adapters import guitar_techs

    _write_guitar_techs_case(tmp_path)
    script = Path(__file__).resolve().parents[3] / "benchmarks" / "guitar" / "validate_guitar_techs_adapter.py"
    spec = importlib.util.spec_from_file_location("validate_guitar_techs_adapter", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    report = module.validate_cases(guitar_techs.yield_cases(tmp_path, signal="directinput"))

    assert report["ok"] is True
    assert report["case_count"] == 1
    assert report["note_count"] == 2
    assert report["category_counts"] == {"chords": 1}
    assert report["player_counts"] == {"P1": 1}


def test_guitar_techs_adapter_validation_script_rejects_bad_case() -> None:
    from aural_ingest.dataset_adapters.common import GroundTruthCase
    from aural_ingest.transcription import MelodicNote

    script = Path(__file__).resolve().parents[3] / "benchmarks" / "guitar" / "validate_guitar_techs_adapter.py"
    spec = importlib.util.spec_from_file_location("validate_guitar_techs_adapter_bad", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    case = GroundTruthCase(
        case_id="bad",
        instrument="guitar",
        audio_path=Path("missing.wav"),
        duration_sec=0.0,
        melodic_notes=(
            MelodicNote(t_on=1.0, t_off=0.5, pitch=64, velocity=90, instrument="guitar"),
        ),
        metadata={"signal": "directinput"},
    )

    report = module.validate_cases([case])

    assert report["ok"] is False
    assert report["invalid_item_count"] == 3
    assert {item["reason"].split()[0] for item in report["invalid_examples"]} == {
        "missing",
        "non-positive",
    }
