import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
import struct
import tomllib
import types
import wave

import pytest


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _valid_musdb_sdr_summary(median_sdr_mean: float = 5.25, *, tracks_ok: int = 10) -> dict[str, object]:
    return {
        "tracks": tracks_ok,
        "tracks_ok": tracks_ok,
        "tracks_failed": 0,
        "tracks_skipped": 0,
        "median_sdr_mean": median_sdr_mean,
        "role_summary": {
            role: {"track_count": tracks_ok, "median_sdr_mean": median_sdr_mean}
            for role in ("bass", "drums", "other", "vocals")
        },
    }


def _valid_musdb_sdr_report(
    *,
    provider: str = "demucs",
    modelpack_id: str | None = None,
    median_sdr_mean: float = 5.25,
    ok: bool = True,
    tracks_ok: int = 10,
    summary: dict[str, object] | None = None,
    dataset: str = "musdb18_or_musdb18_hq",
    split: str = "test",
) -> dict[str, object]:
    config: dict[str, object] = {"stem_separation_provider": provider}
    if modelpack_id is not None:
        config["stem_separation_modelpack_id"] = modelpack_id
    return {
        "ok": ok,
        "provider": provider,
        "dataset": dataset,
        "split": split,
        "config": config,
        "summary": summary
        if summary is not None
        else _valid_musdb_sdr_summary(median_sdr_mean, tracks_ok=tracks_ok),
    }


def _valid_mir_st500_summary(f1: float = 0.75) -> dict[str, object]:
    return {
        "per_algorithm": {
            "melodic_rmvpe": {
                "cases": 1,
                "cases_ok": 1,
                "cases_err": 0,
                "tp": 3,
                "fp": 1,
                "fn": 1,
                "precision": 0.75,
                "recall": 0.75,
                "f1": f1,
            }
        }
    }


def _valid_mir_st500_extra(algorithm: str = "melodic_rmvpe") -> dict[str, object]:
    return {
        "split": "test",
        "variant": "vocal",
        "limit": None,
        "tolerance_ms": 50.0,
        "pitch_tolerance_semitones": 0,
        "algorithms": [algorithm],
    }


def _valid_mir_st500_case() -> dict[str, object]:
    return {
        "case_id": "mir_st500:401",
        "algorithm_id": "melodic_rmvpe",
        "tp": 3,
        "fp": 1,
        "fn": 1,
        "precision": 0.75,
        "recall": 0.75,
        "f1": 0.75,
    }


def _write_mono_wav(path: Path, samples: list[float], sr: int = 48_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sr)
        for sample in samples:
            v = max(-1.0, min(1.0, float(sample)))
            wav_file.writeframesraw(struct.pack("<h", int(v * 32767.0)))


def _load_validate_roformer_runtime_script():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "validate_roformer_runtime.py"
    spec = importlib.util.spec_from_file_location("_test_validate_roformer_runtime", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_config_arg_variants(tmp_path: Path) -> None:
    from aural_ingest import cli

    assert cli._parse_config_arg(None) == {}
    assert cli._parse_config_arg('{"x": 1}') == {"x": 1}

    cfg = tmp_path / "cfg.json"
    cfg.write_text('{"a": "b"}', encoding="utf-8")
    assert cli._parse_config_arg(str(cfg)) == {"a": "b"}

    bom_cfg = tmp_path / "cfg-bom.json"
    bom_cfg.write_text('{"bom": true}', encoding="utf-8-sig")
    assert cli._parse_config_arg(str(bom_cfg)) == {"bom": True}


def test_write_fingering_sidecars_preserves_explicit_and_infers_fretted_notes(tmp_path: Path) -> None:
    from aural_ingest import cli
    from aural_ingest.transcription import MelodicNote

    paths = cli._write_fingering_sidecars(
        tmp_path,
        {
            "lead_guitar": [
                MelodicNote(0.5, 0.75, 64, 96, instrument="lead_guitar", string=1, fret=5),
                MelodicNote(0.1, 0.35, 60, 90, instrument="lead_guitar", string=2, fret=8),
                MelodicNote(0.8, 1.0, 67, 80, instrument="lead_guitar"),
            ],
            "keys": [MelodicNote(0.0, 0.25, 60, 90, instrument="keys")],
        },
    )

    assert paths == {"lead_guitar": "features/fingering.lead_guitar.json"}
    payload = json.loads((tmp_path / paths["lead_guitar"]).read_text("utf-8"))
    assert payload["instrument"] == "lead_guitar"
    assert payload["tuning"] == [40, 45, 50, 55, 59, 64]
    assert [(n["t_on"], n["pitch"], n["string"], n["fret"]) for n in payload["notes"]] == [
        (0.1, 60, 2, 8),
        (0.5, 64, 1, 5),
        (0.8, 67, 5, 3),
    ]
    assert not (tmp_path / "features" / "fingering.keys.json").exists()


def test_write_fingering_sidecars_infers_bass_positions_and_skips_out_of_range(
    tmp_path: Path,
) -> None:
    from aural_ingest import cli
    from aural_ingest.transcription import MelodicNote

    paths = cli._write_fingering_sidecars(
        tmp_path,
        {
            "bass": [
                MelodicNote(0.0, 0.25, 28, 90, instrument="bass"),
                MelodicNote(0.3, 0.55, 52, 88, instrument="bass"),
                MelodicNote(0.6, 0.85, 76, 80, instrument="bass"),
            ],
        },
    )

    assert paths == {"bass": "features/fingering.bass.json"}
    payload = json.loads((tmp_path / paths["bass"]).read_text("utf-8"))
    assert payload["tuning"] == [28, 33, 38, 43]
    assert [(n["pitch"], n["string"], n["fret"]) for n in payload["notes"]] == [
        (28, 0, 0),
        (52, 3, 9),
    ]


def test_write_fingering_sidecars_assigns_chord_notes_to_distinct_strings(
    tmp_path: Path,
) -> None:
    from aural_ingest import cli
    from aural_ingest.transcription import MelodicNote

    paths = cli._write_fingering_sidecars(
        tmp_path,
        {
            "lead_guitar": [
                MelodicNote(0.0, 0.5, 60, 90, instrument="lead_guitar"),
                MelodicNote(0.0, 0.5, 64, 90, instrument="lead_guitar"),
                MelodicNote(0.0, 0.5, 67, 90, instrument="lead_guitar"),
            ],
        },
    )

    payload = json.loads((tmp_path / paths["lead_guitar"]).read_text("utf-8"))
    positions = [(n["pitch"], n["string"], n["fret"]) for n in payload["notes"]]
    assert positions == [
        (60, 3, 5),
        (64, 4, 5),
        (67, 5, 3),
    ]
    assert len({string_idx for _pitch, string_idx, _fret in positions}) == len(positions)
    tuning = payload["tuning"]
    assert all(tuning[string_idx] + fret == pitch for pitch, string_idx, fret in positions)


def test_cmd_stages_emits_all_stage_ids(capsys) -> None:
    from aural_ingest import cli

    rc = cli.cmd_stages(type("Args", (), {})())
    assert rc == 0

    out = capsys.readouterr().out.strip().splitlines()
    payloads = [json.loads(line) for line in out]
    ids = [p["id"] for p in payloads]
    assert ids == [s.id for s in cli.STAGES]
    transcribe_stage = next(p for p in payloads if p["id"] == "transcribe_drums")
    assert "mr_mt3_drums" in transcribe_stage["variants"]
    assert transcribe_stage["variants"]["mr_mt3_drums"]["required_models"][0]["modelpack_id"] == "mr_mt3"
    separate_stage = next(p for p in payloads if p["id"] == "separate_stems")
    assert separate_stage["required_models"][0]["modelpack_id"] == "demucs_6"
    assert separate_stage["policy"]["demucs_support_status"] == "optional_experimental"
    beats_stage = next(p for p in payloads if p["id"] == "beats_tempo")
    assert beats_stage["policy"]["production_default"] == "librosa_first"
    assert beats_stage["policy"]["fallback_mode"] == "standard"


def test_cmd_info_missing_manifest_returns_error(tmp_path: Path, capsys) -> None:
    from aural_ingest import cli

    args = type("Args", (), {})()
    args.auralsong_dir = str(tmp_path / "missing.auralsong")
    rc = cli.cmd_info(args)
    assert rc == 1

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False


def test_cmd_info_returns_manifest_payload(tmp_path: Path, capsys) -> None:
    from aural_ingest import cli

    auralsong = tmp_path / "ok.auralsong"
    auralsong.mkdir(parents=True, exist_ok=True)
    manifest = {"song_id": "abc", "duration_sec": 12.3}
    _write_json(auralsong / "manifest.json", manifest)

    args = type("Args", (), {})()
    args.auralsong_dir = str(auralsong)
    rc = cli.cmd_info(args)
    assert rc == 0

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["manifest"] == manifest


def test_cmd_audit_drums_reports_stem_energy_by_lane(tmp_path: Path, capsys) -> None:
    from aural_ingest import cli

    auralsong = tmp_path / "Psalm.auralsong"
    samples = [0.0] * 48_000
    for idx in range(24_000, 24_600):
        samples[idx] = 0.5

    _write_mono_wav(auralsong / "audio" / "stems" / "drums.wav", samples)
    _write_json(
        auralsong / "manifest.json",
        {
            "title": "Psalm Fixture",
            "profile": "full",
            "pipeline": {
                "transcription": {
                    "transcription_profile": "gameplay_default",
                    "drum_source_kind": "separated_drums",
                    "drum_filter": "combined_filter",
                    "drum_filter_used": "combined_filter",
                    "drum_silence_gate": {"events_in": 2, "events_out": 1, "dropped": 1},
                }
            },
        },
    )
    _write_json(
        auralsong / "features" / "events.json",
        {
            "onsets": [
                {"t": 0.1, "note": 38, "velocity": 90, "instrument": "drums"},
                {"t": 0.5, "note": 36, "velocity": 100, "instrument": "drums"},
            ]
        },
    )

    args = type("Args", (), {})()
    args.auralsong_dir = str(auralsong)
    args.window_ms = 20.0
    args.threshold_dbfs = None

    assert cli.cmd_audit_drums(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["manifest"]["stem_silence_gate_present"] is True
    assert payload["manifest"]["stem_silence_gate"]["dropped"] == 1
    assert payload["events"]["count"] == 2
    assert payload["events"]["by_lane"] == {"kick": 1, "snare": 1}
    assert payload["stem_energy"]["available"] is True
    assert payload["stem_energy"]["below_thresholds"]["-50"]["count"] == 1
    assert payload["stem_energy"]["below_thresholds"]["-50"]["by_lane"] == {"snare": 1}


def test_cmd_validate_detects_invalid_notes_mid(tmp_path: Path, capsys) -> None:
    from aural_ingest import cli

    auralsong = tmp_path / "bad.auralsong"
    (auralsong / "audio").mkdir(parents=True, exist_ok=True)
    (auralsong / "features").mkdir(parents=True, exist_ok=True)

    _write_json(auralsong / "manifest.json", {"duration_sec": 10.0})
    (auralsong / "audio" / "mix.wav").write_bytes(b"wav")
    (auralsong / "features" / "notes.mid").write_bytes(b"not-midi")

    args = type("Args", (), {})()
    args.auralsong_dir = str(auralsong)
    rc = cli.cmd_validate(args)
    assert rc == 1

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False
    assert "notes.mid" in payload["error"]


def test_cmd_validate_detects_events_notes_mid_mismatch(tmp_path: Path, capsys) -> None:
    from aural_ingest import cli
    from aural_ingest.transcription import MelodicNote

    auralsong = tmp_path / "mismatch.auralsong"
    (auralsong / "audio").mkdir(parents=True, exist_ok=True)
    (auralsong / "features").mkdir(parents=True, exist_ok=True)

    _write_json(auralsong / "manifest.json", {"duration_sec": 2.0})
    (auralsong / "audio" / "mix.wav").write_bytes(b"wav")
    (auralsong / "features" / "notes.mid").write_bytes(
        cli._build_notes_mid_bytes(
            bpm=120.0,
            beats=[],
            sections=[],
            drum_events=[],
            instrument_tracks={
                "keys": [
                    MelodicNote(t_on=0.0, t_off=0.5, pitch=64, velocity=90, instrument="keys"),
                ],
            },
        )
    )
    _write_json(
        auralsong / "features" / "events.json",
        {
            "events_version": "1.0.0",
            "tracks": [],
            "onsets": [],
            "notes": [
                {
                    "track_id": "keys_main",
                    "instrument": "keys",
                    "t_on": 0.0,
                    "t_off": 0.5,
                    "pitch": 65,
                    "velocity": 90,
                }
            ],
            "chords": [],
        },
    )

    args = type("Args", (), {})()
    args.auralsong_dir = str(auralsong)
    rc = cli.cmd_validate(args)
    assert rc == 1

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False
    assert "events.json and piano_midi_decoder disagree for keys note 0" in payload["error"]


def test_notes_mid_writes_vocals_on_dedicated_channel() -> None:
    from aural_ingest import cli
    from aural_ingest.transcription import MelodicNote

    midi_bytes = cli._build_notes_mid_bytes(
        bpm=120.0,
        beats=[],
        sections=[],
        drum_events=[],
        instrument_tracks={
            "vocals": [
                MelodicNote(t_on=0.0, t_off=0.5, pitch=69, velocity=90, instrument="vocals"),
            ],
        },
    )

    assert b"Vocals" in midi_bytes
    assert bytes([0x90 | cli.MIDI_CHANNEL_VOCALS, 69, 90]) in midi_bytes


def test_gt_benchmark_case_id_filter_helpers_work_for_non_egmd(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from aural_ingest import cli

    case_file = tmp_path / "cases.txt"
    case_file.write_text("# comment\ncase-b\ncase-c\n", encoding="utf-8")
    args = SimpleNamespace(case_id_file=str(case_file))

    case_ids = cli._case_ids_from_args(args)
    assert case_ids == {"case-b", "case-c"}

    cases = [
        SimpleNamespace(case_id="case-a"),
        SimpleNamespace(case_id="case-b"),
        SimpleNamespace(case_id="case-c"),
    ]
    filtered = cli._filter_cases_by_id(cases, case_ids)
    assert [case.case_id for case in filtered] == ["case-b", "case-c"]
    assert cli._case_adapter_limit(1, case_ids) is None
    assert cli._case_adapter_limit(1, None) == 1
    assert [case.case_id for case in cli._limit_cases(filtered, 1)] == ["case-b"]


def test_gt_benchmark_duration_filter_helper_bounds_cases() -> None:
    from types import SimpleNamespace

    import pytest

    from aural_ingest import cli

    cases = [
        SimpleNamespace(case_id="short", duration_sec=20.0),
        SimpleNamespace(case_id="lower", duration_sec=48.0),
        SimpleNamespace(case_id="middle", duration_sec=90.0),
        SimpleNamespace(case_id="upper", duration_sec=160.0),
        SimpleNamespace(case_id="long", duration_sec=240.0),
        SimpleNamespace(case_id="missing", duration_sec=None),
        SimpleNamespace(case_id="bad", duration_sec="not-a-duration"),
    ]

    filtered = cli._filter_cases_by_duration(
        cases,
        min_duration_sec=48.0,
        max_duration_sec=160.0,
    )
    assert [case.case_id for case in filtered] == ["lower", "middle", "upper"]

    assert cli._filter_cases_by_duration(cases) is cases
    with pytest.raises(ValueError, match="min-duration-sec must be <= max-duration-sec"):
        cli._filter_cases_by_duration(cases, min_duration_sec=10.0, max_duration_sec=5.0)


def test_cmd_gt_benchmark_runs_mir_st500_dataset(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from types import SimpleNamespace

    from aural_ingest import cli
    from aural_ingest import ground_truth_benchmark
    from aural_ingest.transcription import MelodicNote

    ann = tmp_path / "MIR-ST500_20210206"
    ann.mkdir()
    (ann / "MIR-ST500_corrected.json").write_text(
        json.dumps({"401": [[0.0, 0.4, 69.0]]}),
        encoding="utf-8",
    )
    song_dir = tmp_path / "test" / "401"
    song_dir.mkdir(parents=True)
    (song_dir / "Vocal.wav").write_bytes(b"fake wav")

    def fake_get_melodic_algorithm(_algorithm_id):
        def transcribe(_audio_path, instrument):
            return [
                MelodicNote(
                    t_on=0.01,
                    t_off=0.4,
                    pitch=69,
                    velocity=90,
                    instrument=instrument,
                )
            ]

        return transcribe

    monkeypatch.setattr(ground_truth_benchmark, "get_melodic_algorithm", fake_get_melodic_algorithm)

    output = tmp_path / "report.json"
    args = SimpleNamespace(
        dataset="mir_st500",
        corpus_root=str(tmp_path),
        split="test",
        variant=None,
        algorithm=["fake_vocals"],
        limit=None,
        case_id_file=None,
        min_duration_sec=None,
        max_duration_sec=None,
        tolerance_ms=50.0,
        pitch_tolerance_semitones=0,
        output=str(output),
        progress=False,
    )

    rc = cli.cmd_gt_benchmark(args)

    assert rc == 0
    status = json.loads(capsys.readouterr().out)
    assert status["ok"] is True
    assert status["dataset"] == "mir_st500"
    assert status["family"] == "melodic"
    assert status["case_count"] == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["cases"][0]["case_id"] == "mir_st500:401"
    assert payload["summary"]["per_algorithm"]["fake_vocals"]["f1"] == 1.0


def test_cmd_validate_reports_secondary_piano_verifier_summary(tmp_path: Path, capsys) -> None:
    from aural_ingest import cli
    from aural_ingest.transcription import MelodicNote

    auralsong = tmp_path / "ok-piano-verifiers.auralsong"
    (auralsong / "audio").mkdir(parents=True, exist_ok=True)
    (auralsong / "features").mkdir(parents=True, exist_ok=True)

    _write_json(auralsong / "manifest.json", {"duration_sec": 2.0})
    (auralsong / "audio" / "mix.wav").write_bytes(b"wav")
    (auralsong / "features" / "notes.mid").write_bytes(
        cli._build_notes_mid_bytes(
            bpm=120.0,
            beats=[],
            sections=[],
            drum_events=[],
            instrument_tracks={
                "keys": [
                    MelodicNote(t_on=0.0, t_off=0.5, pitch=64, velocity=90, instrument="keys"),
                    MelodicNote(t_on=0.75, t_off=1.25, pitch=67, velocity=88, instrument="keys"),
                ],
            },
        )
    )
    _write_json(
        auralsong / "features" / "events.json",
        {
            "events_version": "1.0.0",
            "tracks": [],
            "onsets": [],
            "notes": [
                {
                    "track_id": "keys_main",
                    "instrument": "keys",
                    "t_on": 0.0,
                    "t_off": 0.5,
                    "pitch": 64,
                    "velocity": 90,
                },
                {
                    "track_id": "keys_main",
                    "instrument": "keys",
                    "t_on": 0.75,
                    "t_off": 1.25,
                    "pitch": 67,
                    "velocity": 88,
                },
            ],
            "chords": [],
        },
    )

    args = type("Args", (), {})()
    args.auralsong_dir = str(auralsong)
    assert cli.cmd_validate(args) == 0

    payload = json.loads(capsys.readouterr().out.strip())
    role_summary = payload["verifiers"]["events_notes_mid"]["roles"]["keys"]
    assert payload["ok"] is True
    assert role_summary == {
        "events_json_notes": 2,
        "piano_benchmark_parser_notes": 2,
        "piano_midi_decoder_notes": 2,
        "secondary_f1": 1.0,
        "secondary_offset_velocity_f1": 1.0,
    }


def test_cmd_validate_detects_secondary_piano_verifier_mismatch(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    from aural_ingest import cli, piano_benchmark
    from aural_ingest.piano_benchmark import PianoBenchmarkEvent
    from aural_ingest.transcription import MelodicNote

    auralsong = tmp_path / "secondary-mismatch.auralsong"
    (auralsong / "audio").mkdir(parents=True, exist_ok=True)
    (auralsong / "features").mkdir(parents=True, exist_ok=True)

    _write_json(auralsong / "manifest.json", {"duration_sec": 2.0})
    (auralsong / "audio" / "mix.wav").write_bytes(b"wav")
    (auralsong / "features" / "notes.mid").write_bytes(
        cli._build_notes_mid_bytes(
            bpm=120.0,
            beats=[],
            sections=[],
            drum_events=[],
            instrument_tracks={
                "keys": [
                    MelodicNote(t_on=0.0, t_off=0.5, pitch=64, velocity=90, instrument="keys"),
                ],
            },
        )
    )
    _write_json(
        auralsong / "features" / "events.json",
        {
            "events_version": "1.0.0",
            "tracks": [],
            "onsets": [],
            "notes": [
                {
                    "track_id": "keys_main",
                    "instrument": "keys",
                    "t_on": 0.0,
                    "t_off": 0.5,
                    "pitch": 64,
                    "velocity": 90,
                }
            ],
            "chords": [],
        },
    )
    monkeypatch.setattr(
        piano_benchmark,
        "parse_piano_midi_reference",
        lambda *_args, **_kwargs: [
            PianoBenchmarkEvent(time=0.0, duration=0.5, pitch=65, velocity=90),
        ],
    )

    args = type("Args", (), {})()
    args.auralsong_dir = str(auralsong)
    assert cli.cmd_validate(args) == 1

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False
    assert "events.json and piano_benchmark_parser disagree for keys note 0" in payload["error"]


def _write_minimal_feedpak(root: Path) -> None:
    import yaml

    _write_json(
        root / "arrangements" / "notation_keys.json",
        {"version": 1, "instrument": "keys", "staves": [], "measures": []},
    )
    _write_json(
        root / "arrangements" / "tab_keys.json",
        {
            "version": 1,
            "instrument": "keys",
            "tuning": [40, 45, 50, 55, 59, 64],
            "notes": [],
        },
    )
    stems_dir = root / "audio" / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)
    (stems_dir / "drums.wav").write_bytes(b"wav")
    _write_json(root / "song_timeline.json", {"version": 1})
    _write_json(root / "drum_tab.json", {"version": 1, "kit": [], "hits": []})
    _write_json(root / "keys.json", {"version": 1, "events": []})
    _write_json(root / "harmony.json", {"version": 1, "events": []})
    _write_json(root / "vocal_pitch.json", {"version": 1, "notes": []})
    _write_json(root / "vocal_pitch_contour.json", {"version": 1, "samples": []})
    _write_json(
        root / "aural" / "fingering.keys.json",
        {"version": "1.0.0", "instrument": "keys", "notes": []},
    )
    (root / "aural" / "notes.mid").write_bytes(b"midi")
    (root / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "feedpak_version": "1.11.0",
                "title": "Fixture",
                "artist": "Unknown",
                "duration": 2.0,
                "arrangements": [
                    {
                        "id": "keys",
                        "name": "Keys",
                        "type": "piano",
                        "notation": "arrangements/notation_keys.json",
                        "file": "arrangements/tab_keys.json",
                    }
                ],
                "stems": [{"id": "drums", "file": "audio/stems/drums.wav", "default": True}],
                "song_timeline": "song_timeline.json",
                "drum_tab": "drum_tab.json",
                "keys": "keys.json",
                "harmony": "harmony.json",
                "vocal_pitch": "vocal_pitch.json",
                "vocal_pitch_contour": "vocal_pitch_contour.json",
                "aural_notes_mid": "aural/notes.mid",
                "aural_fingering": {"keys": "aural/fingering.keys.json"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_cmd_validate_accepts_minimal_feedpak(tmp_path: Path, capsys) -> None:
    from aural_ingest import cli

    feedpak = tmp_path / "ok.feedpak"
    _write_minimal_feedpak(feedpak)

    args = type("Args", (), {})()
    args.auralsong_dir = str(feedpak)
    assert cli.cmd_validate(args) == 0

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["pack_type"] == "feedpak"


def test_cmd_validate_feedpak_reports_missing_files(tmp_path: Path, capsys) -> None:
    from aural_ingest import cli

    feedpak = tmp_path / "broken.feedpak"
    _write_minimal_feedpak(feedpak)
    (feedpak / "audio" / "stems" / "drums.wav").unlink()
    (feedpak / "arrangements" / "notation_keys.json").unlink()
    (feedpak / "arrangements" / "tab_keys.json").unlink()
    (feedpak / "keys.json").unlink()
    (feedpak / "harmony.json").unlink()
    (feedpak / "vocal_pitch_contour.json").unlink()
    (feedpak / "aural" / "fingering.keys.json").unlink()

    args = type("Args", (), {})()
    args.auralsong_dir = str(feedpak)
    assert cli.cmd_validate(args) == 1

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False
    assert "audio/stems/drums.wav" in payload["missing"]
    assert "arrangements/notation_keys.json" in payload["missing"]
    assert "arrangements/tab_keys.json" in payload["missing"]
    assert "keys.json" in payload["missing"]
    assert "harmony.json" in payload["missing"]
    assert "vocal_pitch_contour.json" in payload["missing"]
    assert "aural/fingering.keys.json" in payload["missing"]


def test_cmd_validate_feedpak_detects_unparseable_drum_tab(tmp_path: Path, capsys) -> None:
    from aural_ingest import cli

    feedpak = tmp_path / "bad-json.feedpak"
    _write_minimal_feedpak(feedpak)
    (feedpak / "drum_tab.json").write_text("{not json", encoding="utf-8")

    args = type("Args", (), {})()
    args.auralsong_dir = str(feedpak)
    assert cli.cmd_validate(args) == 1

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False
    assert "error" in payload


def test_cmd_validate_feedpak_detects_unparseable_model_artifact(tmp_path: Path, capsys) -> None:
    from aural_ingest import cli

    feedpak = tmp_path / "bad-model-json.feedpak"
    _write_minimal_feedpak(feedpak)
    (feedpak / "keys.json").write_text("{not json", encoding="utf-8")

    args = type("Args", (), {})()
    args.auralsong_dir = str(feedpak)
    assert cli.cmd_validate(args) == 1

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False
    assert "keys.json" in payload["error"]


def test_cmd_validate_feedpak_detects_schema_invalid_sidecar(tmp_path: Path, capsys) -> None:
    from aural_ingest import cli

    feedpak = tmp_path / "bad-schema.feedpak"
    _write_minimal_feedpak(feedpak)
    _write_json(feedpak / "drum_tab.json", {"version": 1, "kit": [], "hits": [{"t": -1, "lane": "kick"}]})

    args = type("Args", (), {})()
    args.auralsong_dir = str(feedpak)
    assert cli.cmd_validate(args) == 1

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False
    assert "drum_tab.json" in payload["error"]
    assert "schema invalid" in payload["error"]


def test_cmd_validate_feedpak_detects_schema_invalid_aural_fingering(tmp_path: Path, capsys) -> None:
    from aural_ingest import cli

    feedpak = tmp_path / "bad-fingering-schema.feedpak"
    _write_minimal_feedpak(feedpak)
    _write_json(
        feedpak / "aural" / "fingering.keys.json",
        {"version": 1, "instrument": "keys", "notes": [{"t_on": 0, "t_off": 1, "pitch": 60, "velocity": 90, "string": 99, "fret": 1}]},
    )

    args = type("Args", (), {})()
    args.auralsong_dir = str(feedpak)
    assert cli.cmd_validate(args) == 1

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False
    assert "aural/fingering.keys.json" in payload["error"]
    assert "schema invalid" in payload["error"]


def test_cmd_validate_feedpak_rejects_unsupported_fingering_manifest_role(
    tmp_path: Path,
    capsys,
) -> None:
    import yaml

    from aural_ingest import cli

    feedpak = tmp_path / "bad-fingering-role.feedpak"
    _write_minimal_feedpak(feedpak)
    _write_json(
        feedpak / "aural" / "fingering.drums.json",
        {"version": "1.0.0", "instrument": "keys", "notes": []},
    )
    manifest_path = feedpak / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["aural_fingering"] = {"drums": "aural/fingering.drums.json"}
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    args = type("Args", (), {})()
    args.auralsong_dir = str(feedpak)
    assert cli.cmd_validate(args) == 1

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False
    assert "aural_fingering" in payload["error"]
    assert "drums" in payload["error"]


def test_cmd_validate_feedpak_rejects_unsafe_manifest_refs(tmp_path: Path, capsys) -> None:
    import yaml

    from aural_ingest import cli

    cases = [
        ("keys", "C:/outside/keys.json"),
        ("aural_fingering.keys", "../outside/fingering.json"),
    ]
    for idx, (field, rel_path) in enumerate(cases):
        feedpak = tmp_path / f"unsafe-{idx}.feedpak"
        _write_minimal_feedpak(feedpak)
        manifest_path = feedpak / "manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if field == "aural_fingering.keys":
            manifest["aural_fingering"]["keys"] = rel_path
        else:
            manifest[field] = rel_path
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

        args = type("Args", (), {})()
        args.auralsong_dir = str(feedpak)
        assert cli.cmd_validate(args) == 1

        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["ok"] is False
        assert field in payload["error"] or field.replace(".", "/") in payload["error"]
        assert "does not match" in payload["error"]


def test_cmd_import_returns_2_for_missing_input(tmp_path: Path) -> None:
    from aural_ingest import cli

    args = type("Args", (), {})()
    args.input_audio_path = str(tmp_path / "nope.wav")
    args.out = str(tmp_path / "x.auralsong")
    args.profile = "full"
    args.config = None
    args.title = None
    args.artist = None
    args.duration_sec = None

    assert cli.cmd_import(args) == 2


def test_build_parser_knows_core_commands() -> None:
    from aural_ingest import cli

    p = cli.build_parser()
    assert p.parse_args(["stages"]).cmd == "stages"
    assert p.parse_args(["validate", "x"]).cmd == "validate"
    assert p.parse_args(["info", "x"]).cmd == "info"
    assert p.parse_args(["audit-drums", "x"]).cmd == "audit-drums"
    assert p.parse_args(["runtime-check"]).cmd == "runtime-check"
    assert p.parse_args(["runtime-check", "--require-model-upgrade-gates"]).require_model_upgrade_gates is True
    assert p.parse_args(["benchmark-drums", "stem.wav", "reference.json"]).cmd == "benchmark-drums"
    assert p.parse_args(["refine-piano", "--audio", "keys.wav", "--source-midi", "suno.mid"]).cmd == "refine-piano"
    assert p.parse_args(["import", "in.wav", "--out", "o.auralsong"]).cmd == "import"
    assert p.parse_args(["import-dir", "in_dir", "--out", "o.auralsong"]).cmd == "import-dir"
    parsed_refine_vocals = p.parse_args(["refine-candidates", "x.auralsong", "--instrument", "vocals"])
    assert parsed_refine_vocals.cmd == "refine-candidates"
    assert parsed_refine_vocals.instrument == ["vocals"]
    parsed_benchmark_vocals = p.parse_args(["benchmark-transcribers", "x.auralsong", "--instrument", "vocals"])
    assert parsed_benchmark_vocals.cmd == "benchmark-transcribers"
    assert parsed_benchmark_vocals.instrument == "vocals"

    parsed = p.parse_args(
        [
            "import",
            "in.wav",
            "--out",
            "o.auralsong",
            "--drum-filter",
            "combined_filter",
            "--melodic-method",
            "pyin",
            "--beat-analysis-mode",
            "high_accuracy",
            "--stem-separation-provider",
            "none",
            "--shifts",
            "2",
            "--multi-filter",
        ]
    )
    assert parsed.drum_filter == "combined_filter"
    assert parsed.melodic_method == "pyin"
    assert parsed.beat_analysis_mode == "high_accuracy"
    assert parsed.stem_separation_provider == "none"
    assert parsed.shifts == 2
    assert parsed.multi_filter is True

    parsed_engine = p.parse_args(
        [
            "import",
            "in.wav",
            "--out",
            "o.auralsong",
            "--drum-engine",
            "mr_mt3_drums",
        ]
    )
    assert parsed_engine.drum_filter == "mr_mt3_drums"


def test_research_decision_defaults_are_quality_first_and_fail_safe() -> None:
    from aural_ingest import cli

    assert cli.DEFAULT_BEAT_ANALYSIS_MODE == "high_accuracy"
    assert cli.BEAT_TEMPO_PRODUCTION_POLICY["production_default"] == "librosa_first"
    assert cli.BEAT_TEMPO_PRODUCTION_POLICY["essentia_status"] == "research_candidate_not_default"
    assert cli.STEM_SEPARATION_PROVIDER_POLICY["demucs_support_status"] == "optional_experimental"
    assert cli.STEM_SEPARATION_PROVIDER_POLICY["roformer_support_status"] == "research_external_command"
    assert (
        cli.STEM_SEPARATION_PROVIDER_POLICY["absence_behavior"]
        == "skip_separation_and_continue_with_mix_or_provided_stems"
    )
    assert cli.BENCHMARK_THRESHOLD_POLICY["mode"] == "warn"
    assert cli.BENCHMARK_THRESHOLD_POLICY["strict_pr_blocking"] is False


def test_cmd_refine_piano_returns_2_for_missing_audio(tmp_path: Path) -> None:
    from aural_ingest import cli
    from aural_ingest.piano_benchmark import write_melodic_notes_midi
    from aural_ingest.transcription import MelodicNote

    source = tmp_path / "source.mid"
    write_melodic_notes_midi(
        [MelodicNote(t_on=0.0, t_off=0.25, pitch=60, velocity=80, instrument="keys")],
        source,
    )

    args = type("Args", (), {})()
    args.audio = str(tmp_path / "missing.wav")
    args.source_midi = str(source)
    args.reference_midi = None
    args.method = ["source_midi"]
    args.out_root = str(tmp_path / "runs")
    args.label = "unit"
    args.tolerance_ms = 60.0
    args.offset_tolerance_ms = 120.0
    args.velocity_tolerance = 20
    args.source_offset_sec = 0.0
    args.reference_offset_sec = 0.0
    args.bpm = 120.0

    assert cli.cmd_refine_piano(args) == 2


def test_cmd_benchmark_drums_emits_json_payload(tmp_path: Path, monkeypatch, capsys) -> None:
    from aural_ingest import cli
    from aural_ingest.transcription import DrumEvent

    stem = tmp_path / "stem.wav"
    stem.write_bytes(b"x")
    reference = tmp_path / "reference.json"
    reference.write_text(json.dumps([{"t": 0.5, "class": "snare"}]), encoding="utf-8")

    monkeypatch.setattr(
        cli,
        "build_default_drum_algorithm_registry",
        lambda: {
            "combined_filter": lambda _stem: [DrumEvent(time=0.5, note=38, velocity=100)],
            "adaptive_beat_grid": lambda _stem: [DrumEvent(time=0.52, note=50, velocity=100)],
        },
    )

    rc = cli.main(
        [
            "benchmark-drums",
            str(stem),
            str(reference),
            "--algorithm",
            "combined_filter",
            "--algorithm",
            "adaptive_beat_grid",
            "--json",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["algorithm_metadata"]["combined_filter"]["backend"] == "heuristic"
    assert payload["class_order"] == ["kick", "snare", "hi_hat", "crash", "ride", "tom1", "tom2", "tom3"]

    results = {result["algorithm"]: result for result in payload["results"]}
    assert results["combined_filter"]["per_class"]["snare"]["tp"] == 1
    assert results["adaptive_beat_grid"]["per_class"]["snare"]["fn"] == 1
    assert results["adaptive_beat_grid"]["confusions"] == [
        {"reference_class": "snare", "predicted_class": "tom1", "count": 1}
    ]


def test_cmd_runtime_check_emits_dependency_and_modelpack_snapshot(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from aural_ingest import cli

    basic_pitch_model = tmp_path / "basic_pitch" / "saved_models" / "icassp_2022" / "nmp.onnx"
    basic_pitch_model.parent.mkdir(parents=True, exist_ok=True)
    basic_pitch_model.write_bytes(b"basic-pitch")
    ffmpeg_path = tmp_path / "ffmpeg.exe"
    ffmpeg_path.write_bytes(b"ffmpeg")
    demucs_zip = tmp_path / "demucs_6.zip"
    demucs_zip.write_bytes(b"demucs")
    demucs_ft_zip = tmp_path / "demucs_ft_drums.zip"
    demucs_ft_zip.write_bytes(b"demucs-ft")
    mt3_checkpoint = tmp_path / "mr_mt3" / "mt3.pth"
    mt3_checkpoint.parent.mkdir(parents=True, exist_ok=True)
    mt3_checkpoint.write_bytes(b"mt3")

    fake_modules = {
        "librosa": "0.11.0",
        "numpy": "2.2.0",
        "torch": "2.11.0",
        "torchaudio": "2.11.0",
        "mt3_infer": "0.1.3",
        "demucs": "4.0.1",
        "basic_pitch": "0.4.0",
        "basic_pitch.inference": "0.4.0",
        "onnxruntime": "1.23.2",
        "tensorflow": "2.16.1",
    }
    for name, version in fake_modules.items():
        module = types.ModuleType(name)
        module.__version__ = version
        module.__spec__ = importlib.machinery.ModuleSpec(name, loader=None)
        monkeypatch.setitem(sys.modules, name, module)
    sys.modules["numpy"].float32 = "float32"
    sys.modules["numpy"].zeros = lambda size, dtype=None: [0.0] * size
    class _FakeMidi:
        tracks = []

    class FakeAdapter:
        def transcribe(self, audio, sr=16000):
            assert sr == 16000
            return _FakeMidi()

    sys.modules["mt3_infer"].load_model = lambda *args, **kwargs: FakeAdapter()
    monkeypatch.setattr(cli, "ensure_mt3_transformers_compat", lambda: None)
    monkeypatch.setattr(cli, "resolve_basic_pitch_model_path", lambda _roots: basic_pitch_model)
    monkeypatch.setattr(cli, "_default_basic_pitch_model_roots", lambda: [tmp_path])
    monkeypatch.setattr(cli, "_resolve_ffmpeg_path", lambda: str(ffmpeg_path))

    def fake_resolve_demucs_modelpack(config):
        requested_id = cli._requested_demucs_modelpack_id(config) or cli.DEMUCS_MODELPACK_ID
        if requested_id == cli.DEMUCS_FT_DRUMS_MODELPACK_ID:
            return demucs_ft_zip, {"id": "demucs_ft_drums", "version": "ft-test"}, None
        return demucs_zip, {"id": "demucs_6", "version": "test"}, None

    monkeypatch.setattr(cli, "_resolve_demucs_modelpack", fake_resolve_demucs_modelpack)
    monkeypatch.setattr(
        cli,
        "roformer_runtime_status",
        lambda _config: {
            "configured": False,
            "provider": "roformer",
            "missing": ["AURAL_ROFORMER_PYTHON is unset"],
        },
    )

    monkeypatch.setattr(
        cli,
        "available_mt3_modelpacks",
        lambda: {
                "mr_mt3_drums": {
                    "ok": True,
                    "model_id": "mr_mt3",
                    "modelpack_id": "mr_mt3",
                    "modelpack_version": "0.0.1",
                    "checkpoint_path": str(mt3_checkpoint),
                    "modelpack_root": str(mt3_checkpoint.parent),
                },
            "yourmt3_drums": {"ok": False, "error": "missing modelpack"},
        },
    )

    rc = cli.cmd_runtime_check(type("Args", (), {})())
    assert rc == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["policies"]["beat_tempo"]["production_default"] == "librosa_first"
    assert payload["policies"]["stem_separation"]["demucs_support_status"] == "optional_experimental"
    assert payload["dependencies"]["librosa"]["required"] is False
    assert payload["dependencies"]["basic_pitch"]["distribution"] == "basic-pitch"
    assert payload["dependencies"]["basic_pitch"]["required"] is True
    assert payload["dependencies"]["basic_pitch"]["version"] == "0.4.0"
    assert payload["dependencies"]["basic_pitch.inference"]["distribution"] == "basic-pitch"
    assert payload["dependencies"]["basic_pitch.inference"]["required"] is True
    assert payload["dependencies"]["onnxruntime"]["ok"] is True
    assert payload["dependencies"]["tensorflow"]["ok"] is True
    assert payload["dependencies"]["torch"]["version"] == "2.11.0"
    assert payload["drum_engines"]["mr_mt3_drums"]["ok"] is True
    assert payload["drum_engines"]["mr_mt3_drums"]["loadable"] is True
    assert payload["drum_engines"]["mr_mt3_drums"]["adapter_class"] == "FakeAdapter"
    assert payload["drum_engines"]["mr_mt3_drums"]["transcribe_smoke_ok"] is True
    assert payload["assets"]["basic_pitch_model"]["ok"] is True
    assert payload["assets"]["basic_pitch_model"]["sha256"]
    assert payload["runtime_features"]["basic_pitch"]["enabled"] is True
    assert payload["runtime_features"]["basic_pitch"]["backend"] == "onnx"
    assert payload["runtime_features"]["basic_pitch"]["backend_dependency"] == "onnxruntime"
    assert payload["runtime_features"]["basic_pitch"]["runtime_importable"] is True
    assert payload["runtime_features"]["basic_pitch"]["backend_importable"] is True
    assert payload["runtime_features"]["basic_pitch"]["package_dependency_health_ok"] is True
    assert payload["runtime_features"]["roformer"]["enabled"] is False
    assert payload["runtime_features"]["roformer"]["runtime"]["provider"] == "roformer"
    assert payload["model_upgrade_gates"]["exit_code_affects_runtime_check"] is False
    assert "musdb_sdr_baseline" in payload["model_upgrade_gates"]["gates"]
    assert "rmvpe_mir_st500_vocals" in payload["model_upgrade_gates"]["gates"]
    assert payload["assets"]["demucs_modelpack"]["manifest"]["id"] == "demucs_6"
    assert payload["assets"]["demucs_ft_drums_modelpack"]["manifest"]["id"] == "demucs_ft_drums"
    assert payload["assets"]["demucs_ft_drums_modelpack"]["manifest"]["version"] == "ft-test"
    assert payload["assets"]["mt3_checkpoints"]["mr_mt3_drums"]["sha256"]
    assert payload["stages"]["separate_stems"]["enabled"] is True
    assert payload["stages"]["separate_stems"]["required_models"][0]["version"] == "test"
    assert payload["stages"]["transcribe_drums"]["variants"]["mr_mt3_drums"]["enabled"] is True
    assert payload["stages"]["transcribe_drums"]["variants"]["mr_mt3_drums"]["required_models"][0]["version"] == "0.0.1"
    assert payload["stages"]["transcribe_drums"]["variants"]["yourmt3_drums"]["enabled"] is False


def test_cmd_runtime_check_allows_onnx_basic_pitch_without_tensorflow(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from aural_ingest import cli

    basic_pitch_model = tmp_path / "basic_pitch" / "saved_models" / "icassp_2022" / "nmp.onnx"
    basic_pitch_model.parent.mkdir(parents=True, exist_ok=True)
    basic_pitch_model.write_bytes(b"basic-pitch")
    ffmpeg_path = tmp_path / "ffmpeg.exe"
    ffmpeg_path.write_bytes(b"ffmpeg")

    dependency_status = {
        "basic_pitch": {"ok": True, "version": "0.4.0"},
        "basic_pitch.inference": {"ok": True, "version": "0.4.0"},
        "onnxruntime": {"ok": True, "version": "1.26.0"},
        "tensorflow": {"ok": False, "error": "No module named 'tensorflow'"},
        "librosa": {"ok": True, "version": "0.11.0"},
        "torch": {"ok": True, "version": "2.11.0"},
        "torchaudio": {"ok": True, "version": "2.11.0"},
        "mt3_infer": {"ok": True, "version": "0.1.3"},
        "demucs": {"ok": True, "version": "4.0.1"},
    }

    def fake_dependency_snapshot(module_name: str, dependency_policy: dict[str, object]) -> dict[str, object]:
        distribution = str(dependency_policy.get("distribution") or module_name)
        return {
            **dependency_policy,
            "distribution": distribution,
            "installed": bool(dependency_status[module_name]["ok"]),
            **dependency_status[module_name],
        }

    monkeypatch.setattr(cli, "_runtime_dependency_snapshot", fake_dependency_snapshot)
    monkeypatch.setattr(cli, "resolve_basic_pitch_model_path", lambda _roots: basic_pitch_model)
    monkeypatch.setattr(cli, "_default_basic_pitch_model_roots", lambda: [tmp_path])
    monkeypatch.setattr(cli, "_resolve_ffmpeg_path", lambda: str(ffmpeg_path))
    monkeypatch.setattr(cli, "_resolve_demucs_modelpack", lambda _config: (None, None, None))
    monkeypatch.setattr(cli, "available_mt3_modelpacks", lambda: {})
    monkeypatch.setattr(
        cli,
        "roformer_runtime_status",
        lambda _config: {
            "configured": False,
            "provider": "roformer",
            "missing": ["AURAL_ROFORMER_PYTHON is unset"],
        },
    )

    rc = cli.cmd_runtime_check(type("Args", (), {})())

    assert rc == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["dependencies"]["tensorflow"]["ok"] is False
    assert payload["assets"]["basic_pitch_model"]["ok"] is True
    assert payload["runtime_features"]["basic_pitch"]["enabled"] is True
    assert payload["runtime_features"]["basic_pitch"]["backend"] == "onnx"
    assert payload["runtime_features"]["basic_pitch"]["backend_dependency"] == "onnxruntime"
    assert payload["runtime_features"]["basic_pitch"]["package_dependency_health_ok"] is False
    assert payload["runtime_features"]["basic_pitch"]["dependency_warnings"] == ["tensorflow"]
    assert payload["runtime_features"]["roformer"]["enabled"] is False
    assert payload["model_upgrade_gates"]["exit_code_affects_runtime_check"] is False
    assert payload["assets"]["demucs_ft_drums_modelpack"]["ok"] is False


def test_model_upgrade_gate_snapshot_reports_missing_external_gates(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest import cli

    for env_var in (
        "AURAL_MUSDB18_ROOT",
        "AURAL_MUSDB18_HQ_ROOT",
        "AURAL_MIR_ST500_ROOT",
        "AURAL_RMVPE_CHECKPOINT",
        "AURAL_RMVPE_CHECKPOINT_SHA256",
        "AURAL_RMVPE_REPO",
        "AURAL_ADTOF_PYTHON",
        "AURAL_ADTOF_REPO",
        "AURAL_DRUM_STEMSEP_PYTHON",
        "AURAL_DRUM_STEMSEP_REPO",
        "AURAL_DRUM_STEMSEP_RUNNER",
        "AURAL_DRUM_STEMSEP_CHECKPOINT",
        "AURAL_QMUL_GUITAR_PYTHON",
        "AURAL_QMUL_GUITAR_REPO",
        "AURAL_QMUL_GUITAR_COMMAND",
    ):
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setattr(cli, "resolve_rmvpe_checkpoint_path", lambda _roots: None)
    monkeypatch.setattr(cli, "_default_basic_pitch_model_roots", lambda: [tmp_path])

    def fake_asset_payload(path_value, *, kind, required):
        return {
            "ok": path_value is not None,
            "kind": kind,
            "required": required,
            "path": str(path_value) if path_value is not None else None,
        }

    payload = cli._model_upgrade_gates_snapshot(
        demucs_ft_modelpack_path=None,
        demucs_ft_modelpack_manifest=None,
        demucs_ft_modelpack_error=None,
        roformer_status={
            "configured": False,
            "provider": "roformer",
            "missing": ["AURAL_ROFORMER_PYTHON is unset"],
        },
        asset_payload=fake_asset_payload,
    )

    assert payload["ok"] is False
    assert payload["exit_code_affects_runtime_check"] is False
    assert payload["evidence_checklist_relative_path"] == cli.MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST.as_posix()
    assert "beat_this_barline_listening_review" in payload["pending"]
    assert "musdb_sdr_baseline" in payload["pending"]
    assert payload["gates"]["musdb_sdr_baseline"]["evidence"]["ready"] is False
    assert payload["gates"]["musdb_sdr_baseline"]["dataset_ready"] is False
    assert payload["gates"]["rmvpe_mir_st500_vocals"]["runtime"]["ready"] is False
    assert payload["gates"]["rmvpe_mir_st500_vocals"]["mir_st500"]["root"]["configured"] is False
    assert payload["gates"]["adtof_external_runtime"]["runtime"]["configured"] is False
    assert payload["gates"]["drum_stemsep_external_runtime"]["runtime"]["configured"] is False
    assert payload["gates"]["qmul_hr_guitar_external_runtime"]["runtime"]["configured"] is False


def test_model_upgrade_gate_evidence_checklist_covers_runtime_globs() -> None:
    from aural_ingest import cli

    repo_root = Path(__file__).resolve().parents[3]
    checklist_path = repo_root / cli.MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST
    checklist = checklist_path.read_text(encoding="utf-8")

    assert cli.MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST.as_posix() == (
        "benchmarks/runtime/model_upgrade_gate_evidence.md"
    )
    for pattern in (
        cli.BEAT_THIS_REVIEW_EVIDENCE_RELATIVE_PATH.as_posix(),
        cli.MUSDB_SDR_EVIDENCE_GLOB.as_posix(),
        cli.ADTOF_RUNTIME_EVIDENCE_GLOB.as_posix(),
        cli.DRUM_STEMSEP_RUNTIME_EVIDENCE_GLOB.as_posix(),
        cli.RMVPE_RUNTIME_EVIDENCE_GLOB.as_posix(),
        cli.ROFORMER_RUNTIME_EVIDENCE_GLOB.as_posix(),
        cli.QMUL_HR_GUITAR_RUNTIME_EVIDENCE_GLOB.as_posix(),
        cli.MIR_ST500_VOCALS_EVIDENCE_GLOB.as_posix(),
    ):
        assert pattern in checklist.replace("\\", "/")


def test_model_upgrade_evidence_root_prefers_env_then_cwd(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest import cli

    env_root = tmp_path / "env-root"
    monkeypatch.setenv(cli.MODEL_UPGRADE_EVIDENCE_ROOT_ENV, str(env_root))
    assert cli._model_upgrade_evidence_root() == env_root.resolve()

    monkeypatch.delenv(cli.MODEL_UPGRADE_EVIDENCE_ROOT_ENV, raising=False)
    cwd_root = tmp_path / "cwd-root"
    (cwd_root / cli.MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST).parent.mkdir(parents=True)
    (cwd_root / cli.MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST).write_text("# evidence\n", encoding="utf-8")
    monkeypatch.chdir(cwd_root)

    assert cli._model_upgrade_evidence_root() == cwd_root.resolve()


def test_model_upgrade_evidence_root_ignores_cwd_without_gate_checklist(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest import cli

    monkeypatch.delenv(cli.MODEL_UPGRADE_EVIDENCE_ROOT_ENV, raising=False)
    cwd_root = tmp_path / "cwd-with-benchmarks-only"
    (cwd_root / "benchmarks").mkdir(parents=True)
    monkeypatch.chdir(cwd_root)

    assert cli._model_upgrade_evidence_root() == Path(__file__).resolve().parents[3]


def test_model_upgrade_gate_snapshot_accepts_beat_this_review_evidence(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest import cli

    for env_var in (
        "AURAL_MUSDB18_ROOT",
        "AURAL_MUSDB18_HQ_ROOT",
        "AURAL_MIR_ST500_ROOT",
        "AURAL_RMVPE_CHECKPOINT",
        "AURAL_RMVPE_CHECKPOINT_SHA256",
        "AURAL_RMVPE_REPO",
        "AURAL_ADTOF_PYTHON",
        "AURAL_ADTOF_REPO",
        "AURAL_DRUM_STEMSEP_PYTHON",
        "AURAL_DRUM_STEMSEP_REPO",
        "AURAL_DRUM_STEMSEP_RUNNER",
        "AURAL_DRUM_STEMSEP_CHECKPOINT",
        "AURAL_QMUL_GUITAR_PYTHON",
        "AURAL_QMUL_GUITAR_REPO",
        "AURAL_QMUL_GUITAR_COMMAND",
    ):
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setattr(cli, "resolve_rmvpe_checkpoint_path", lambda _roots: None)
    monkeypatch.setattr(cli, "_default_basic_pitch_model_roots", lambda: [tmp_path])
    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)

    evidence_path = tmp_path / cli.BEAT_THIS_REVIEW_EVIDENCE_RELATIVE_PATH
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text(
        json.dumps(
            {
                "version": 1,
                "gate": "beat_this_barline_listening_review",
                "reviewed_by": "test-reviewer",
                "reviewed_at_utc": "2026-07-08T00:00:00Z",
                "source_smoke_report": "benchmarks/meter/beat_this_dbn_refresh_meter_smoke.md",
                "cases": {
                    "psalm_121_my_help.feedpak": {"barlines_ok": True, "listening_ok": True},
                    "psalm_130_please_hear_me.feedpak": {"barlines_ok": True, "listening_ok": True},
                    "psalm_5_every_morning.feedpak": {"barlines_ok": True, "listening_ok": True},
                },
            }
        ),
        encoding="utf-8",
    )

    payload = cli._model_upgrade_gates_snapshot(
        demucs_ft_modelpack_path=None,
        demucs_ft_modelpack_manifest=None,
        demucs_ft_modelpack_error=None,
        roformer_status={"configured": False, "provider": "roformer", "missing": []},
        asset_payload=lambda path_value, *, kind, required: {
            "ok": path_value is not None,
            "kind": kind,
            "required": required,
        },
    )

    beat_gate = payload["gates"]["beat_this_barline_listening_review"]
    assert beat_gate["ready"] is True
    assert beat_gate["evidence"]["reviewed_cases"] == list(cli.BEAT_THIS_REVIEW_REQUIRED_CASES)
    assert "beat_this_barline_listening_review" not in payload["pending"]
    assert "musdb_sdr_baseline" in payload["pending"]


def test_beat_this_review_evidence_requires_review_metadata(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    evidence_path = tmp_path / cli.BEAT_THIS_REVIEW_EVIDENCE_RELATIVE_PATH
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text(
        json.dumps(
            {
                "version": 1,
                "gate": "beat_this_barline_listening_review",
                "reviewed_by": "TODO",
                "reviewed_at_utc": "TODO",
                "source_smoke_report": "benchmarks/meter/beat_this_dbn_refresh_meter_smoke.md",
                "cases": {
                    "psalm_121_my_help.feedpak": {"barlines_ok": True, "listening_ok": True},
                    "psalm_130_please_hear_me.feedpak": {"barlines_ok": True, "listening_ok": True},
                    "psalm_5_every_morning.feedpak": {"barlines_ok": True, "listening_ok": True},
                },
            }
        ),
        encoding="utf-8",
    )

    status = cli._beat_this_review_evidence_status()

    assert status["ready"] is False
    assert status["reviewed_cases"] == list(cli.BEAT_THIS_REVIEW_REQUIRED_CASES)
    assert status["missing_cases"] == []
    assert "reviewed_by must identify the reviewer" in status["metadata_errors"]
    assert "reviewed_at_utc must record the review timestamp" in status["metadata_errors"]
    assert "reviewed_by must identify the reviewer" in status["reason"]


def test_beat_this_review_evidence_requires_utc_review_timestamp(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    evidence_path = tmp_path / cli.BEAT_THIS_REVIEW_EVIDENCE_RELATIVE_PATH
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text(
        json.dumps(
            {
                "version": 1,
                "gate": "beat_this_barline_listening_review",
                "reviewed_by": "test-reviewer",
                "reviewed_at_utc": "yesterday",
                "source_smoke_report": "benchmarks/meter/beat_this_dbn_refresh_meter_smoke.md",
                "cases": {
                    "psalm_121_my_help.feedpak": {"barlines_ok": True, "listening_ok": True},
                    "psalm_130_please_hear_me.feedpak": {"barlines_ok": True, "listening_ok": True},
                    "psalm_5_every_morning.feedpak": {"barlines_ok": True, "listening_ok": True},
                },
            }
        ),
        encoding="utf-8",
    )

    status = cli._beat_this_review_evidence_status()

    assert status["ready"] is False
    assert status["reviewed_cases"] == list(cli.BEAT_THIS_REVIEW_REQUIRED_CASES)
    assert status["missing_cases"] == []
    assert "reviewed_at_utc must be an ISO-8601 UTC timestamp ending in Z" in status["metadata_errors"]
    assert "reviewed_at_utc must be an ISO-8601 UTC timestamp ending in Z" in status["reason"]


def test_musdb_sdr_report_evidence_skips_newer_wrong_provider(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    runs_dir = tmp_path / "benchmarks" / "quality" / "runs"
    valid_report = runs_dir / "20260708_000000_musdb_separation_sdr.json"
    wrong_provider_report = runs_dir / "20260708_010000_musdb_separation_sdr.json"
    _write_json(
        valid_report,
        _valid_musdb_sdr_report(median_sdr_mean=5.25),
    )
    _write_json(
        wrong_provider_report,
        _valid_musdb_sdr_report(provider="roformer", median_sdr_mean=5.75),
    )
    os.utime(valid_report, (1_000_000_000, 1_000_000_000))
    os.utime(wrong_provider_report, (1_000_000_100, 1_000_000_100))

    status = cli._musdb_sdr_report_evidence_status(provider=cli.DEMUCS_PROVIDER)

    assert status["ready"] is True
    assert status["path"] == str(valid_report)
    assert status["candidates_checked"] == 2
    assert status["rejection_count"] == 1
    assert status["rejections"][0]["path"] == str(wrong_provider_report)
    assert status["rejections"][0]["provider"] == "roformer"
    assert "provider is 'roformer', expected 'demucs'" in status["rejections"][0]["reason"]


def test_musdb_sdr_report_evidence_treats_latest_matching_identity_as_authoritative(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    runs_dir = tmp_path / "benchmarks" / "quality" / "runs"
    valid_report = runs_dir / "20260708_000000_musdb_separation_sdr.json"
    weak_report = runs_dir / "20260708_010000_musdb_separation_sdr.json"
    wrong_provider_report = runs_dir / "20260708_020000_musdb_separation_sdr.json"
    _write_json(
        valid_report,
        _valid_musdb_sdr_report(median_sdr_mean=5.25),
    )
    _write_json(
        weak_report,
        _valid_musdb_sdr_report(
            ok=False,
            summary=_valid_musdb_sdr_summary(0.0) | {"tracks_ok": 0},
        ),
    )
    _write_json(
        wrong_provider_report,
        _valid_musdb_sdr_report(provider="roformer", median_sdr_mean=5.75),
    )
    os.utime(valid_report, (1_000_000_000, 1_000_000_000))
    os.utime(weak_report, (1_000_000_100, 1_000_000_100))
    os.utime(wrong_provider_report, (1_000_000_200, 1_000_000_200))

    status = cli._musdb_sdr_report_evidence_status(provider=cli.DEMUCS_PROVIDER)

    assert status["ready"] is False
    assert status["path"] is None
    assert status["candidate_count"] == 3
    assert status["candidates_checked"] == 2
    assert status["matching_identity_candidate_count"] == 1
    assert status["latest_matching_identity_only"] is True
    assert status["rejection_count"] == 2
    assert status["rejections"][0]["path"] == str(wrong_provider_report)
    assert status["rejections"][1]["path"] == str(weak_report)
    assert "report did not complete with at least one successful track" in status["rejections"][1]["reason"]
    assert "no successful MUSDB SDR report found" in status["reason"]


def test_musdb_sdr_report_evidence_requires_expected_dataset_and_test_split(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    dataset_root = tmp_path / "dataset"
    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: dataset_root)
    dataset_report = (
        dataset_root / "benchmarks" / "quality" / "runs" / "20260708_000000_musdb_separation_sdr.json"
    )
    _write_json(
        dataset_report,
        _valid_musdb_sdr_report(dataset="musdb18_train_subset"),
    )

    dataset_status = cli._musdb_sdr_report_evidence_status(provider=cli.DEMUCS_PROVIDER)

    assert dataset_status["ready"] is False
    assert dataset_status["rejections"][0]["dataset"] == "musdb18_train_subset"
    assert (
        "dataset is 'musdb18_train_subset', expected 'musdb18_or_musdb18_hq'"
        in dataset_status["rejections"][0]["reason"]
    )

    split_root = tmp_path / "split"
    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: split_root)
    runs_dir = split_root / "benchmarks" / "quality" / "runs"
    older_valid_report = runs_dir / "20260708_000000_musdb_separation_sdr.json"
    newer_wrong_split_report = runs_dir / "20260708_010000_musdb_separation_sdr.json"
    _write_json(older_valid_report, _valid_musdb_sdr_report())
    _write_json(newer_wrong_split_report, _valid_musdb_sdr_report(split="train"))

    split_status = cli._musdb_sdr_report_evidence_status(provider=cli.DEMUCS_PROVIDER)

    assert split_status["ready"] is False
    assert split_status["path"] is None
    assert split_status["candidates_checked"] == 1
    assert split_status["matching_identity_candidate_count"] == 1
    assert split_status["rejections"][0]["path"] == str(newer_wrong_split_report)
    assert split_status["rejections"][0]["split"] == "train"
    assert "split is 'train', expected 'test'" in split_status["rejections"][0]["reason"]


def test_evidence_candidates_skip_stale_stat_failures(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    runs_dir = tmp_path / "benchmarks" / "quality" / "runs"
    valid_report = runs_dir / "20260708_000000_musdb_separation_sdr.json"
    stale_report = runs_dir / "20260708_010000_musdb_separation_sdr.json"
    _write_json(
        valid_report,
        _valid_musdb_sdr_report(median_sdr_mean=5.25),
    )
    _write_json(
        stale_report,
        _valid_musdb_sdr_report(median_sdr_mean=99.0),
    )

    path_type = type(stale_report)
    original_stat = path_type.stat

    def fake_stat(self, *args, **kwargs):
        if self == stale_report:
            raise OSError("stale evidence candidate")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(path_type, "stat", fake_stat)

    status = cli._musdb_sdr_report_evidence_status(provider=cli.DEMUCS_PROVIDER)

    assert status["ready"] is True
    assert status["path"] == str(valid_report)
    assert status["summary"]["median_sdr_mean"] == 5.25


def test_evidence_candidates_sort_by_filename_timestamp_before_mtime(tmp_path: Path) -> None:
    from aural_ingest import cli

    runs_dir = tmp_path / "benchmarks" / "runtime" / "runs"
    older_success = runs_dir / "20260708_000000_adtof_runtime.json"
    newer_failure = runs_dir / "20260708_010000_adtof_runtime.json"
    _write_json(older_success, {"ok": True})
    _write_json(newer_failure, {"ok": False})
    os.utime(older_success, (1_000_000_200, 1_000_000_200))
    os.utime(newer_failure, (1_000_000_100, 1_000_000_100))

    candidates = cli._evidence_candidates(runs_dir / "*_adtof_runtime.json")

    assert candidates[:2] == [newer_failure, older_success]


def test_musdb_sdr_report_evidence_rejects_wrong_modelpack_for_demucs_ft(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    report = tmp_path / "benchmarks" / "quality" / "runs" / "20260708_000000_musdb_separation_sdr.json"
    _write_json(
        report,
        _valid_musdb_sdr_report(modelpack_id="demucs_6", median_sdr_mean=5.25),
    )

    status = cli._musdb_sdr_report_evidence_status(
        provider=cli.DEMUCS_PROVIDER,
        required_modelpack_id=cli.DEMUCS_FT_DRUMS_MODELPACK_ID,
    )

    assert status["ready"] is False
    assert status["path"] is None
    assert status["candidates_checked"] == 1
    assert status["rejection_count"] == 1
    assert status["rejections"][0]["path"] == str(report)
    assert status["rejections"][0]["modelpack_id"] == "demucs_6"
    assert (
        "modelpack id is 'demucs_6', expected 'demucs_ft_drums'"
        in status["rejections"][0]["reason"]
    )
    assert "no successful MUSDB SDR report found" in status["reason"]


def test_musdb_sdr_report_evidence_rejects_nondefault_demucs_baseline_modelpack(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    report = tmp_path / "benchmarks" / "quality" / "runs" / "20260708_000000_musdb_separation_sdr.json"
    _write_json(
        report,
        _valid_musdb_sdr_report(modelpack_id="demucs_experimental", median_sdr_mean=5.25),
    )

    status = cli._musdb_sdr_report_evidence_status(provider=cli.DEMUCS_PROVIDER)

    assert status["ready"] is False
    assert status["rejections"][0]["modelpack_id"] == "demucs_experimental"
    assert "non-default Demucs report does not satisfy the default Demucs baseline" in status["rejections"][0]["reason"]


def test_musdb_sdr_report_evidence_rejects_failed_or_partial_tracks(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    report = tmp_path / "benchmarks" / "quality" / "runs" / "20260708_000000_musdb_separation_sdr.json"
    summary = _valid_musdb_sdr_summary(5.25)
    summary["tracks"] = 10
    summary["tracks_ok"] = 10
    summary["tracks_failed"] = 0
    summary["role_summary"]["bass"]["track_count"] = 9
    _write_json(
        report,
        _valid_musdb_sdr_report(summary=summary),
    )

    status = cli._musdb_sdr_report_evidence_status(provider=cli.DEMUCS_PROVIDER)

    assert status["ready"] is False
    assert "summary.role_summary.bass.track_count must equal summary.tracks_ok" in status["rejections"][0]["reason"]


def test_musdb_sdr_report_evidence_requires_promotion_sample_size(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    report = tmp_path / "benchmarks" / "quality" / "runs" / "20260708_000000_musdb_separation_sdr.json"
    _write_json(
        report,
        _valid_musdb_sdr_report(median_sdr_mean=5.25, tracks_ok=9),
    )

    status = cli._musdb_sdr_report_evidence_status(provider=cli.DEMUCS_PROVIDER)

    assert status["ready"] is False
    assert "summary.tracks_ok must be at least 10" in status["rejections"][0]["reason"]


def test_musdb_sdr_report_evidence_rejects_nonzero_failed_tracks(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    report = tmp_path / "benchmarks" / "quality" / "runs" / "20260708_000000_musdb_separation_sdr.json"
    summary = _valid_musdb_sdr_summary(5.25)
    summary["tracks"] = 2
    summary["tracks_ok"] = 1
    summary["tracks_failed"] = 1
    _write_json(
        report,
        _valid_musdb_sdr_report(summary=summary),
    )

    status = cli._musdb_sdr_report_evidence_status(provider=cli.DEMUCS_PROVIDER)

    assert status["ready"] is False
    assert "summary.tracks_failed must be zero" in status["rejections"][0]["reason"]


def test_musdb_sdr_report_evidence_rejects_nonfinite_aggregate_sdr(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    report = tmp_path / "benchmarks" / "quality" / "runs" / "20260708_000000_musdb_separation_sdr.json"
    summary = _valid_musdb_sdr_summary(5.25)
    summary["median_sdr_mean"] = float("nan")
    _write_json(
        report,
        _valid_musdb_sdr_report(summary=summary),
    )

    status = cli._musdb_sdr_report_evidence_status(provider=cli.DEMUCS_PROVIDER)

    assert status["ready"] is False
    assert "summary.median_sdr_mean must be finite" in status["rejections"][0]["reason"]


def test_musdb_sdr_comparison_rejects_candidate_below_baseline() -> None:
    from aural_ingest import cli

    status = cli._musdb_sdr_comparison_status(
        baseline={"ready": True, "summary": _valid_musdb_sdr_summary(5.25)},
        candidate={"ready": True, "summary": _valid_musdb_sdr_summary(-20.0)},
        baseline_label="default Demucs",
        candidate_label="RoFormer",
    )

    assert status["ready"] is False
    assert status["baseline_value"] == 5.25
    assert status["candidate_value"] == -20.0
    assert status["delta"] == -25.25
    assert "RoFormer median_sdr_mean -20.000000 is below default Demucs 5.250000" in status["reason"]


def test_musdb_sdr_report_evidence_requires_role_summary(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    report = tmp_path / "benchmarks" / "quality" / "runs" / "20260708_000000_musdb_separation_sdr.json"
    _write_json(
        report,
        _valid_musdb_sdr_report(summary={"tracks_ok": 10, "median_sdr_mean": 5.25}),
    )

    status = cli._musdb_sdr_report_evidence_status(provider=cli.DEMUCS_PROVIDER)

    assert status["ready"] is False
    assert status["rejections"][0]["path"] == str(report)
    assert "summary.role_summary is missing" in status["rejections"][0]["reason"]


def test_runtime_validation_report_evidence_treats_latest_engine_report_as_authoritative(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    runs_dir = tmp_path / "benchmarks" / "runtime" / "runs"
    valid_report = runs_dir / "20260708_000000_adtof_runtime.json"
    weak_report = runs_dir / "20260708_010000_adtof_runtime.json"
    _write_json(
        valid_report,
        {
            "ok": True,
            "engine": "adtof_drums",
            "status": "ok",
            "require_events": True,
            "event_count": 1,
            "events": [{"time": 0.125, "note": 36, "velocity": 100}],
            "runtime": {"configured": True},
        },
    )
    _write_json(
        weak_report,
        {
            "ok": True,
            "engine": "adtof_drums",
            "status": "ok",
            "require_events": False,
            "event_count": 0,
            "events": [],
            "runtime": {"configured": True},
        },
    )
    os.utime(valid_report, (1_000_000_000, 1_000_000_000))
    os.utime(weak_report, (1_000_000_100, 1_000_000_100))

    status = cli._runtime_validation_report_evidence_status(
        evidence_glob=cli.ADTOF_RUNTIME_EVIDENCE_GLOB,
        engine="adtof_drums",
        required_bool="require_events",
        count_field="event_count",
        allowed_statuses=("ok",),
    )

    assert status["ready"] is False
    assert status["path"] is None
    assert status["candidate_count"] == 2
    assert status["candidates_checked"] == 1
    assert status["latest_candidate_only"] is True
    assert status["rejection_count"] == 1
    assert status["rejections"][0]["path"] == str(weak_report)
    assert "require_events must be true" in status["rejections"][0]["reason"]


def test_runtime_validation_report_evidence_rejects_wrong_success_status(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    report = tmp_path / "benchmarks" / "runtime" / "runs" / "20260708_000000_adtof_runtime.json"
    _write_json(
        report,
        {
            "ok": True,
            "engine": "adtof_drums",
            "status": "runner_failed",
            "require_events": True,
            "event_count": 1,
            "events": [{"time": 0.125, "note": 36, "velocity": 100}],
            "runtime": {"configured": True},
        },
    )

    status = cli._runtime_validation_report_evidence_status(
        evidence_glob=cli.ADTOF_RUNTIME_EVIDENCE_GLOB,
        engine="adtof_drums",
        required_bool="require_events",
        count_field="event_count",
        allowed_statuses=("ok",),
    )

    assert status["ready"] is False
    assert "status is 'runner_failed'" in status["rejections"][0]["reason"]


def test_runtime_validation_report_evidence_requires_runtime_ready(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    report = tmp_path / "benchmarks" / "runtime" / "runs" / "20260708_000000_adtof_runtime.json"
    _write_json(
        report,
        {
            "ok": True,
            "engine": "adtof_drums",
            "status": "ok",
            "require_events": True,
            "event_count": 1,
            "events": [{"time": 0.125, "note": 36, "velocity": 100}],
            "runtime": {"configured": False},
        },
    )

    status = cli._runtime_validation_report_evidence_status(
        evidence_glob=cli.ADTOF_RUNTIME_EVIDENCE_GLOB,
        engine="adtof_drums",
        required_bool="require_events",
        count_field="event_count",
        allowed_statuses=("ok",),
    )

    assert status["ready"] is False
    assert "runtime.ready or runtime.configured must be true" in status["rejections"][0]["reason"]


def test_runtime_validation_report_evidence_requires_specific_runtime_field(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    report = tmp_path / "benchmarks" / "runtime" / "runs" / "20260708_000000_adtof_runtime.json"
    _write_json(
        report,
        {
            "ok": True,
            "engine": "adtof_drums",
            "status": "ok",
            "require_events": True,
            "event_count": 1,
            "events": [{"time": 0.125, "note": 36, "velocity": 100}],
            "runtime": {"ready": True, "configured": False},
        },
    )

    status = cli._runtime_validation_report_evidence_status(
        evidence_glob=cli.ADTOF_RUNTIME_EVIDENCE_GLOB,
        engine="adtof_drums",
        required_bool="require_events",
        count_field="event_count",
        allowed_statuses=("ok",),
        runtime_required_field="configured",
    )

    assert status["ready"] is False
    assert status["runtime_required_field"] == "configured"
    assert "runtime.configured must be true" in status["rejections"][0]["reason"]


def test_runtime_validation_report_evidence_requires_event_array(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    report = tmp_path / "benchmarks" / "runtime" / "runs" / "20260708_000000_adtof_runtime.json"
    _write_json(
        report,
        {
            "ok": True,
            "engine": "adtof_drums",
            "status": "ok",
            "require_events": True,
            "event_count": 1,
            "runtime": {"configured": True},
        },
    )

    status = cli._runtime_validation_report_evidence_status(
        evidence_glob=cli.ADTOF_RUNTIME_EVIDENCE_GLOB,
        engine="adtof_drums",
        required_bool="require_events",
        count_field="event_count",
        allowed_statuses=("ok",),
    )

    assert status["ready"] is False
    assert "report is missing events[]" in status["rejections"][0]["reason"]


def test_runtime_validation_report_evidence_requires_event_payload_shape(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    cases = [
        (["not-an-object"], "events[0] must be an object"),
        ([{"time": -0.1, "note": 36, "velocity": 100}], "events[0].time must be a finite nonnegative number"),
        ([{"time": "0.125", "note": 36, "velocity": 100}], "events[0].time must be a finite nonnegative number"),
        ([{"time": 0.125, "note": "snare", "velocity": 100}], "events[0].note must be an integer MIDI note in [0, 127]"),
        ([{"time": 0.125, "note": 36, "velocity": 0}], "events[0].velocity must be an integer MIDI velocity in [1, 127]"),
        (
            [{"time": 0.125, "note": 36, "velocity": 100, "duration": -0.01}],
            "events[0].duration must be a finite nonnegative number when present",
        ),
        (
            [{"time": 0.125, "note": 36, "velocity": 100, "duration": "0.05"}],
            "events[0].duration must be a finite nonnegative number when present",
        ),
    ]

    for idx, (events, expected_reason) in enumerate(cases):
        root = tmp_path / f"case-{idx}"
        monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda root=root: root)
        report = root / "benchmarks" / "runtime" / "runs" / "20260708_000000_adtof_runtime.json"
        _write_json(
            report,
            {
                "ok": True,
                "engine": "adtof_drums",
                "status": "ok",
                "require_events": True,
                "event_count": 1,
                "events": events,
                "runtime": {"configured": True},
            },
        )

        status = cli._runtime_validation_report_evidence_status(
            evidence_glob=cli.ADTOF_RUNTIME_EVIDENCE_GLOB,
            engine="adtof_drums",
            required_bool="require_events",
            count_field="event_count",
            allowed_statuses=("ok",),
        )

        assert status["ready"] is False
        assert expected_reason in status["rejections"][0]["reason"]


def test_runtime_validation_report_evidence_requires_note_payload_shape(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    valid_note = {
        "t_on": 0.5,
        "t_off": 0.75,
        "pitch": 64,
        "velocity": 100,
        "instrument": "lead_guitar",
    }
    cases = [
        (["not-an-object"], "notes[0] must be an object"),
        ([{**valid_note, "t_on": -0.1}], "notes[0].t_on must be a finite nonnegative number"),
        ([{**valid_note, "t_on": "0.5"}], "notes[0].t_on must be a finite nonnegative number"),
        ([{**valid_note, "t_off": 0.5}], "notes[0].t_off must be a finite number greater than t_on"),
        ([{**valid_note, "t_off": "0.75"}], "notes[0].t_off must be a finite number greater than t_on"),
        ([{**valid_note, "pitch": "E4"}], "notes[0].pitch must be an integer MIDI note in [0, 127]"),
        ([{**valid_note, "velocity": 0}], "notes[0].velocity must be an integer MIDI velocity in [1, 127]"),
        ([{**valid_note, "instrument": ""}], "notes[0].instrument must be a non-empty string"),
        ([{**valid_note, "string": 9}], "notes[0].string must be an integer in [0, 8] when present"),
        ([{**valid_note, "fret": "high"}], "notes[0].fret must be an integer in [0, 36] when present"),
    ]

    for idx, (notes, expected_reason) in enumerate(cases):
        root = tmp_path / f"case-{idx}"
        monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda root=root: root)
        report = root / "benchmarks" / "runtime" / "runs" / "20260708_000000_qmul_hr_guitar_runtime.json"
        _write_json(
            report,
            {
                "ok": True,
                "engine": "qmul_hr_guitar",
                "status": "ok",
                "instrument": "lead_guitar",
                "require_notes": True,
                "note_count": 1,
                "notes": notes,
                "runtime": {"configured": True},
            },
        )

        status = cli._runtime_validation_report_evidence_status(
            evidence_glob=cli.QMUL_HR_GUITAR_RUNTIME_EVIDENCE_GLOB,
            engine="qmul_hr_guitar",
            required_bool="require_notes",
            count_field="note_count",
            allowed_statuses=("ok",),
            runtime_required_field="configured",
        )

        assert status["ready"] is False
        assert expected_reason in status["rejections"][0]["reason"]


def test_runtime_validation_report_evidence_requires_roformer_stem_paths(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    report = tmp_path / "benchmarks" / "runtime" / "runs" / "20260708_000000_roformer_runtime.json"
    _write_json(
        report,
        {
            "ok": True,
            "provider": "roformer",
            "status": "fresh",
            "require_roles": ["bass", "drums", "other", "vocals"],
            "missing_roles": [],
            "roles": ["bass", "drums", "other", "vocals"],
            "runtime": {"configured": True},
        },
    )

    status = cli._runtime_validation_report_evidence_status(
        evidence_glob=cli.ROFORMER_RUNTIME_EVIDENCE_GLOB,
        engine=cli.ROFORMER_PROVIDER,
        identity_field="provider",
        allowed_statuses=("fresh",),
        required_roles=cli.MUSDB_SDR_EVIDENCE_REQUIRED_ROLES,
    )

    assert status["ready"] is False
    assert "stem_paths must be an object when roles are required" in status["rejections"][0]["reason"]


def test_mir_st500_vocals_report_evidence_requires_rmvpe_cases(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    runs_dir = tmp_path / "benchmarks" / "vocals" / "gt_runs"
    valid_report = runs_dir / "20260708_000000_mir_st500_vocals.json"
    wrong_algorithm_report = runs_dir / "20260708_010000_mir_st500_vocals.json"
    _write_json(
        valid_report,
        {
            "ok": True,
            "dataset": "mir_st500",
            "family": "melodic",
            "extra": _valid_mir_st500_extra(),
            "case_count": 1,
            "summary": _valid_mir_st500_summary(0.75),
            "cases": [_valid_mir_st500_case()],
        },
    )
    _write_json(
        wrong_algorithm_report,
        {
            "ok": True,
            "dataset": "mir_st500",
            "family": "melodic",
            "extra": _valid_mir_st500_extra("melodic_torchcrepe"),
            "case_count": 1,
            "summary": {
                "per_algorithm": {
                    "melodic_torchcrepe": {
                        "cases": 1,
                        "cases_ok": 1,
                        "cases_err": 0,
                        "precision": 0.25,
                        "recall": 0.25,
                        "f1": 0.25,
                    }
                }
            },
            "cases": [
                {
                    "case_id": "mir_st500:401",
                    "algorithm_id": "melodic_torchcrepe",
                    "tp": 1,
                    "fp": 2,
                    "fn": 3,
                    "precision": 0.333333,
                    "recall": 0.25,
                    "f1": 0.285714,
                }
            ],
        },
    )
    os.utime(valid_report, (1_000_000_000, 1_000_000_000))
    os.utime(wrong_algorithm_report, (1_000_000_100, 1_000_000_100))

    status = cli._mir_st500_vocals_report_evidence_status()

    assert status["ready"] is True
    assert status["path"] == str(valid_report)
    assert status["algorithm_case_count"] == 1
    assert status["summary"]["f1"] == 0.75
    assert status["candidates_checked"] == 2
    assert status["rejection_count"] == 1
    assert "extra.algorithms must include 'melodic_rmvpe'" in status["rejections"][0]["reason"]


def test_mir_st500_vocals_report_evidence_rejects_partial_algorithm_coverage(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    report = tmp_path / "benchmarks" / "vocals" / "gt_runs" / "20260708_000000_mir_st500_vocals.json"
    _write_json(
        report,
        {
            "ok": True,
            "dataset": "mir_st500",
            "family": "melodic",
            "extra": _valid_mir_st500_extra(),
            "case_count": 2,
            "summary": {
                "per_algorithm": {
                    "melodic_rmvpe": {
                        "cases": 1,
                        "cases_ok": 1,
                        "cases_err": 0,
                        "precision": 0.75,
                        "recall": 0.75,
                        "f1": 0.75,
                    }
                }
            },
            "cases": [_valid_mir_st500_case()],
        },
    )

    status = cli._mir_st500_vocals_report_evidence_status()

    assert status["ready"] is False
    assert "melodic_rmvpe case count must equal report case_count" in status["rejections"][0]["reason"]


def test_mir_st500_vocals_report_evidence_rejects_nonfinite_summary_metric(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    report = tmp_path / "benchmarks" / "vocals" / "gt_runs" / "20260708_000000_mir_st500_vocals.json"
    _write_json(
        report,
        {
            "ok": True,
            "dataset": "mir_st500",
            "family": "melodic",
            "extra": _valid_mir_st500_extra(),
            "case_count": 1,
            "summary": _valid_mir_st500_summary(float("nan")),
            "cases": [_valid_mir_st500_case()],
        },
    )

    status = cli._mir_st500_vocals_report_evidence_status()

    assert status["ready"] is False
    assert "summary.per_algorithm.melodic_rmvpe.f1 must be finite" in status["rejections"][0]["reason"]


def test_mir_st500_vocals_report_evidence_requires_successful_algorithm_summary(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    report = tmp_path / "benchmarks" / "vocals" / "gt_runs" / "20260708_000000_mir_st500_vocals.json"
    _write_json(
        report,
        {
            "ok": True,
            "dataset": "mir_st500",
            "family": "melodic",
            "extra": _valid_mir_st500_extra(),
            "case_count": 1,
            "summary": {
                "per_algorithm": {
                    "melodic_rmvpe": {
                        "cases": 1,
                        "cases_ok": 0,
                        "cases_err": 1,
                        "precision": 0.0,
                        "recall": 0.0,
                        "f1": 0.0,
                    }
                }
            },
            "cases": [_valid_mir_st500_case()],
        },
    )

    status = cli._mir_st500_vocals_report_evidence_status()

    assert status["ready"] is False
    assert "summary.per_algorithm.melodic_rmvpe.cases_ok must be greater than zero" in status["rejections"][0]["reason"]


def test_mir_st500_vocals_report_evidence_requires_success_ok(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    report = tmp_path / "benchmarks" / "vocals" / "gt_runs" / "20260708_000000_mir_st500_vocals.json"
    _write_json(
        report,
        {
            "ok": False,
            "dataset": "mir_st500",
            "family": "melodic",
            "extra": _valid_mir_st500_extra(),
            "case_count": 1,
            "summary": _valid_mir_st500_summary(0.75),
            "cases": [_valid_mir_st500_case()],
        },
    )

    status = cli._mir_st500_vocals_report_evidence_status()

    assert status["ready"] is False
    assert status["rejections"][0]["ok"] is False
    assert "report ok is not true" in status["rejections"][0]["reason"]


def test_mir_st500_vocals_report_evidence_rejects_limited_promotion_report(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    report = tmp_path / "benchmarks" / "vocals" / "gt_runs" / "20260708_000000_mir_st500_vocals.json"
    extra = _valid_mir_st500_extra()
    extra["limit"] = 5
    _write_json(
        report,
        {
            "ok": True,
            "dataset": "mir_st500",
            "family": "melodic",
            "extra": extra,
            "case_count": 1,
            "summary": _valid_mir_st500_summary(0.75),
            "cases": [_valid_mir_st500_case()],
        },
    )

    status = cli._mir_st500_vocals_report_evidence_status()

    assert status["ready"] is False
    assert "extra.limit must be null for full test/vocal coverage" in status["rejections"][0]["reason"]


def test_mir_st500_vocals_report_evidence_treats_latest_matching_algorithm_as_authoritative(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)
    runs_dir = tmp_path / "benchmarks" / "vocals" / "gt_runs"
    valid_report = runs_dir / "20260708_000000_mir_st500_vocals.json"
    weak_report = runs_dir / "20260708_010000_mir_st500_vocals.json"
    wrong_algorithm_report = runs_dir / "20260708_020000_mir_st500_vocals.json"
    _write_json(
        valid_report,
        {
            "ok": True,
            "dataset": "mir_st500",
            "family": "melodic",
            "extra": _valid_mir_st500_extra(),
            "case_count": 1,
            "summary": _valid_mir_st500_summary(0.75),
            "cases": [_valid_mir_st500_case()],
        },
    )
    _write_json(
        weak_report,
        {
            "ok": False,
            "dataset": "mir_st500",
            "family": "melodic",
            "extra": _valid_mir_st500_extra(),
            "case_count": 1,
            "summary": _valid_mir_st500_summary(0.0),
            "cases": [_valid_mir_st500_case()],
        },
    )
    _write_json(
        wrong_algorithm_report,
        {
            "ok": True,
            "dataset": "mir_st500",
            "family": "melodic",
            "extra": _valid_mir_st500_extra("melodic_basic_pitch"),
            "case_count": 1,
            "summary": _valid_mir_st500_summary(0.8),
            "cases": [_valid_mir_st500_case()],
        },
    )
    os.utime(valid_report, (1_000_000_000, 1_000_000_000))
    os.utime(weak_report, (1_000_000_100, 1_000_000_100))
    os.utime(wrong_algorithm_report, (1_000_000_200, 1_000_000_200))

    status = cli._mir_st500_vocals_report_evidence_status()

    assert status["ready"] is False
    assert status["path"] is None
    assert status["candidate_count"] == 3
    assert status["candidates_checked"] == 2
    assert status["matching_identity_candidate_count"] == 1
    assert status["latest_matching_identity_only"] is True
    assert status["rejection_count"] == 2
    assert status["rejections"][0]["path"] == str(wrong_algorithm_report)
    assert status["rejections"][0]["algorithms"] == ["melodic_basic_pitch"]
    assert status["rejections"][1]["path"] == str(weak_report)
    assert status["rejections"][1]["ok"] is False
    assert "report ok is not true" in status["rejections"][1]["reason"]
    assert "no successful MIR-ST500 vocal benchmark report found" in status["reason"]


def test_model_upgrade_gate_snapshot_accepts_musdb_sdr_report_evidence(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest import cli

    for env_var in (
        "AURAL_MUSDB18_ROOT",
        "AURAL_MUSDB18_HQ_ROOT",
        "AURAL_MIR_ST500_ROOT",
        "AURAL_RMVPE_CHECKPOINT",
        "AURAL_RMVPE_CHECKPOINT_SHA256",
        "AURAL_RMVPE_REPO",
        "AURAL_ADTOF_PYTHON",
        "AURAL_ADTOF_REPO",
        "AURAL_DRUM_STEMSEP_PYTHON",
        "AURAL_DRUM_STEMSEP_REPO",
        "AURAL_DRUM_STEMSEP_RUNNER",
        "AURAL_DRUM_STEMSEP_CHECKPOINT",
        "AURAL_QMUL_GUITAR_PYTHON",
        "AURAL_QMUL_GUITAR_REPO",
        "AURAL_QMUL_GUITAR_COMMAND",
    ):
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setattr(cli, "resolve_rmvpe_checkpoint_path", lambda _roots: None)
    monkeypatch.setattr(cli, "_default_basic_pitch_model_roots", lambda: [tmp_path])
    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)

    runs_dir = tmp_path / "benchmarks" / "quality" / "runs"
    runs_dir.mkdir(parents=True)
    (runs_dir / "20260708_000000_musdb_separation_sdr.json").write_text(
        json.dumps(
            _valid_musdb_sdr_report(median_sdr_mean=5.25)
        ),
        encoding="utf-8",
    )
    (runs_dir / "20260708_010000_musdb_separation_sdr.json").write_text(
        json.dumps(
            _valid_musdb_sdr_report(
                modelpack_id="demucs_ft_drums",
                median_sdr_mean=5.75,
            )
        ),
        encoding="utf-8",
    )
    demucs_ft_zip = tmp_path / "demucs_ft_drums.zip"
    demucs_ft_zip.write_bytes(b"zip")

    payload = cli._model_upgrade_gates_snapshot(
        demucs_ft_modelpack_path=demucs_ft_zip,
        demucs_ft_modelpack_manifest={
            "id": "demucs_ft_drums",
            "version": "ft-test",
            "license": "CC-BY-NC-SA-4.0",
        },
        demucs_ft_modelpack_error=None,
        roformer_status={"configured": False, "provider": "roformer", "missing": []},
        asset_payload=lambda path_value, *, kind, required: {
            "ok": path_value is not None,
            "kind": kind,
            "required": required,
            "path": str(path_value) if path_value is not None else None,
        },
    )

    musdb_gate = payload["gates"]["musdb_sdr_baseline"]
    demucs_ft_gate = payload["gates"]["demucs_ft_drums_sdr"]
    assert musdb_gate["ready"] is True
    assert musdb_gate["evidence"]["summary"]["tracks_ok"] == 10
    assert demucs_ft_gate["ready"] is True
    assert demucs_ft_gate["evidence"]["modelpack_id"] == "demucs_ft_drums"
    assert "musdb_sdr_baseline" not in payload["pending"]
    assert "demucs_ft_drums_sdr" not in payload["pending"]


def test_model_upgrade_gate_snapshot_accepts_external_validation_evidence(
    monkeypatch, tmp_path: Path
) -> None:
    from aural_ingest import cli

    for env_var in (
        "AURAL_MUSDB18_ROOT",
        "AURAL_MUSDB18_HQ_ROOT",
        "AURAL_MIR_ST500_ROOT",
        "AURAL_RMVPE_CHECKPOINT",
        "AURAL_RMVPE_CHECKPOINT_SHA256",
        "AURAL_RMVPE_REPO",
        "AURAL_ADTOF_PYTHON",
        "AURAL_ADTOF_REPO",
        "AURAL_DRUM_STEMSEP_PYTHON",
        "AURAL_DRUM_STEMSEP_REPO",
        "AURAL_DRUM_STEMSEP_RUNNER",
        "AURAL_DRUM_STEMSEP_CHECKPOINT",
        "AURAL_ROFORMER_PYTHON",
        "AURAL_ROFORMER_REPO",
        "AURAL_ROFORMER_COMMAND",
        "AURAL_QMUL_GUITAR_PYTHON",
        "AURAL_QMUL_GUITAR_REPO",
        "AURAL_QMUL_GUITAR_COMMAND",
    ):
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setattr(cli, "resolve_rmvpe_checkpoint_path", lambda _roots: None)
    monkeypatch.setattr(cli, "_default_basic_pitch_model_roots", lambda: [tmp_path])
    monkeypatch.setattr(cli, "_model_upgrade_evidence_root", lambda: tmp_path)

    quality_runs = tmp_path / "benchmarks" / "quality" / "runs"
    _write_json(
        quality_runs / "20260708_000000_musdb_separation_sdr.json",
        _valid_musdb_sdr_report(median_sdr_mean=5.25),
    )
    _write_json(
        quality_runs / "20260708_010000_musdb_separation_sdr.json",
        _valid_musdb_sdr_report(provider="roformer", median_sdr_mean=5.75),
    )

    runtime_runs = tmp_path / "benchmarks" / "runtime" / "runs"
    _write_json(
        runtime_runs / "20260708_000000_adtof_runtime.json",
        {
            "ok": True,
            "engine": "adtof_drums",
            "status": "ok",
            "require_events": True,
            "event_count": 1,
            "events": [{"time": 0.125, "note": 36, "velocity": 100}],
            "runtime": {"configured": True},
        },
    )
    _write_json(
        runtime_runs / "20260708_000000_drum_stemsep_runtime.json",
        {
            "ok": True,
            "engine": "drum_stemsep",
            "status": "ok",
            "require_events": True,
            "event_count": 1,
            "events": [{"time": 0.125, "note": 36, "velocity": 100}],
            "runtime": {"configured": True},
        },
    )
    _write_json(
        runtime_runs / "20260708_000000_qmul_hr_guitar_runtime.json",
        {
            "ok": True,
            "engine": "qmul_hr_guitar",
            "status": "ok",
            "instrument": "lead_guitar",
            "require_notes": True,
            "note_count": 2,
            "notes": [
                {"t_on": 0.0, "t_off": 0.25, "pitch": 64, "velocity": 100, "instrument": "lead_guitar"},
                {"t_on": 0.5, "t_off": 0.75, "pitch": 67, "velocity": 96, "instrument": "lead_guitar"},
            ],
            "runtime": {"configured": True},
        },
    )
    _write_json(
        runtime_runs / "20260708_000000_rmvpe_runtime.json",
        {
            "ok": True,
            "engine": "melodic_rmvpe",
            "status": "ready",
            "runtime": {"ready": True},
        },
    )
    _write_json(
        runtime_runs / "20260708_000000_roformer_runtime.json",
        {
            "ok": True,
            "provider": "roformer",
            "status": "fresh",
            "require_roles": ["bass", "drums", "other", "vocals"],
            "missing_roles": [],
            "roles": ["bass", "drums", "other", "vocals"],
            "stem_paths": {
                "bass": "audio/stems/bass.wav",
                "drums": "audio/stems/drums.wav",
                "other": "audio/stems/other.wav",
                "vocals": "audio/stems/vocals.wav",
            },
            "runtime": {"configured": True},
        },
    )
    _write_json(
        tmp_path / "benchmarks" / "vocals" / "gt_runs" / "20260708_000000_mir_st500_vocals.json",
        {
            "ok": True,
            "dataset": "mir_st500",
            "family": "melodic",
            "extra": _valid_mir_st500_extra(),
            "case_count": 1,
            "summary": _valid_mir_st500_summary(0.75),
            "cases": [_valid_mir_st500_case()],
        },
    )

    payload = cli._model_upgrade_gates_snapshot(
        demucs_ft_modelpack_path=None,
        demucs_ft_modelpack_manifest=None,
        demucs_ft_modelpack_error=None,
        roformer_status={"configured": True, "provider": "roformer", "missing": []},
        asset_payload=lambda path_value, *, kind, required: {
            "ok": path_value is not None,
            "kind": kind,
            "required": required,
        },
    )

    for gate_name in (
        "musdb_sdr_baseline",
        "roformer_musdb_comparison",
        "rmvpe_mir_st500_vocals",
        "adtof_external_runtime",
        "drum_stemsep_external_runtime",
        "qmul_hr_guitar_external_runtime",
    ):
        assert payload["gates"][gate_name]["ready"] is True
        assert gate_name not in payload["pending"]
    assert payload["gates"]["roformer_musdb_comparison"]["evidence"]["provider"] == "roformer"
    assert payload["gates"]["roformer_musdb_comparison"]["comparison"]["delta"] == 0.5
    assert payload["gates"]["roformer_musdb_comparison"]["runtime_evidence"]["report"]["provider"] == "roformer"
    assert payload["gates"]["rmvpe_mir_st500_vocals"]["benchmark_evidence"]["algorithm_case_count"] == 1
    assert payload["gates"]["adtof_external_runtime"]["evidence"]["report"]["event_count"] == 1


def test_runtime_check_strict_model_upgrade_gates_fail_when_pending(monkeypatch, capsys) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(
        cli,
        "_mt3_runtime_snapshot",
        lambda: {
            "ok": True,
            "model_upgrade_gates": {
                "ok": False,
                "exit_code_affects_runtime_check": False,
                "pending": ["musdb_sdr_baseline", "rmvpe_mir_st500_vocals"],
                "ready": [],
                "gates": {},
            },
        },
    )

    args = type("Args", (), {"require_model_upgrade_gates": True})()
    rc = cli.cmd_runtime_check(args)

    assert rc == 1
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False
    assert payload["model_upgrade_gates"]["exit_code_affects_runtime_check"] is True
    assert payload["errors"] == [
        "model-upgrade gates pending: musdb_sdr_baseline, rmvpe_mir_st500_vocals"
    ]


def test_runtime_check_strict_model_upgrade_gates_fail_when_snapshot_missing(monkeypatch, capsys) -> None:
    from aural_ingest import cli

    monkeypatch.setattr(cli, "_mt3_runtime_snapshot", lambda: {"ok": True})

    args = type("Args", (), {"require_model_upgrade_gates": True})()
    rc = cli.cmd_runtime_check(args)

    assert rc == 1
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False
    assert payload["errors"] == ["model-upgrade gate snapshot missing or malformed"]


def test_basic_pitch_runtime_feature_allows_onnx_without_tensorflow() -> None:
    from aural_ingest import cli

    feature = cli._basic_pitch_runtime_feature(
        {"ok": True, "path": "basic_pitch/saved_models/icassp_2022/nmp.onnx"},
        {
            "basic_pitch": {
                "ok": True,
                "installed": True,
                "installed_version": "0.4.0",
            },
            "basic_pitch.inference": {
                "ok": True,
                "installed": True,
                "installed_version": "0.4.0",
            },
            "onnxruntime": {
                "ok": True,
                "installed": True,
                "installed_version": "1.23.2",
            },
            "tensorflow": {
                "ok": False,
                "installed": False,
                "error": "No module named 'tensorflow'",
            },
        },
    )

    assert feature["enabled"] is True
    assert feature["backend"] == "onnx"
    assert feature["backend_dependency"] == "onnxruntime"
    assert feature["model_installed"] is True
    assert feature["runtime_importable"] is True
    assert feature["backend_importable"] is True
    assert feature["package_dependency_health_ok"] is False
    assert feature["dependency_warnings"] == ["tensorflow"]


def test_basic_pitch_runtime_feature_reports_missing_model_without_tensorflow_backend_error() -> None:
    from aural_ingest import cli

    feature = cli._basic_pitch_runtime_feature(
        {"ok": False, "error": "missing"},
        {
            "basic_pitch": {"ok": True},
            "basic_pitch.inference": {"ok": True},
            "onnxruntime": {"ok": True},
            "tensorflow": {"ok": False, "error": "No module named 'tensorflow'"},
        },
    )

    assert feature["enabled"] is False
    assert feature["backend"] == "unresolved"
    assert feature["backend_dependency"] is None
    assert feature["model_installed"] is False
    assert feature["backend_importable"] is False
    assert feature["missing"] == ["basic_pitch_model"]
    assert "backend_error" not in feature
    assert feature["dependency_warnings"] == ["tensorflow"]


def test_basic_pitch_runtime_feature_requires_tensorflow_for_savedmodel() -> None:
    from aural_ingest import cli

    feature = cli._basic_pitch_runtime_feature(
        {"ok": True, "path": "basic_pitch/saved_models/icassp_2022/nmp"},
        {
            "basic_pitch": {"ok": True},
            "basic_pitch.inference": {"ok": True},
            "onnxruntime": {"ok": True},
            "tensorflow": {
                "ok": False,
                "installed": False,
                "error": "No module named 'tensorflow'",
            },
        },
    )

    assert feature["enabled"] is False
    assert feature["backend"] == "tensorflow_saved_model"
    assert feature["backend_importable"] is False
    assert feature["missing"] == ["tensorflow"]
    assert "tensorflow" in feature["backend_error"]


def test_main_runs_stages_command(capsys) -> None:
    from aural_ingest import cli

    rc = cli.main(["stages"])
    assert rc == 0
    assert '"id"' in capsys.readouterr().out


def test_runtime_stage_snapshot_disables_model_variants_when_packs_missing() -> None:
    from aural_ingest import cli

    stages = cli._runtime_stage_snapshot(
        {
            "mr_mt3_drums": {
                "ok": False,
                "model_id": "mr_mt3",
                "modelpack_id": "mr_mt3",
                "error": "missing modelpack",
            },
            "yourmt3_drums": {
                "ok": False,
                "model_id": "yourmt3",
                "modelpack_id": "yourmt3",
                "error": "missing modelpack",
            },
        },
        None,
        None,
        "missing demucs pack",
    )

    assert stages["separate_stems"]["enabled"] is False
    assert stages["separate_stems"]["required_models"][0]["installed"] is False
    assert stages["transcribe_drums"]["variants"]["mr_mt3_drums"]["enabled"] is False
    assert stages["transcribe_drums"]["variants"]["mr_mt3_drums"]["required_models"][0]["installed"] is False


def test_generation_helpers_cover_edge_cases() -> None:
    from aural_ingest import cli

    beats = cli._generate_beats(0.0, -1.0)
    assert len(beats) == 1
    assert beats[0]["t"] == 0.0

    sections = cli._generate_sections(0.0, -1.0)
    assert len(sections) == 1
    assert sections[0]["t0"] == 0.0
    assert sections[0]["t1"] == 0.0


def test_cmd_import_dir_returns_2_for_missing_input_dir(tmp_path: Path) -> None:
    from aural_ingest import cli

    args = type("Args", (), {})()
    args.input_dir_path = str(tmp_path / "no_such")
    args.out = str(tmp_path / "x.auralsong")
    args.profile = "full"
    args.config = None
    args.title = None
    args.artist = None
    args.duration_sec = None

    assert cli.cmd_import_dir(args) == 2


def test_find_audio_source_prefers_mix_files(tmp_path: Path) -> None:
    from aural_ingest import cli

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "z.wav").write_bytes(b"x")
    (src_dir / "mix.wav").write_bytes(b"x")

    assert cli._find_audio_source_in_dir(src_dir) == src_dir / "mix.wav"


def test_find_audio_source_falls_back_to_sorted_recursive_match(tmp_path: Path) -> None:
    from aural_ingest import cli

    src_dir = tmp_path / "src"
    (src_dir / "b").mkdir(parents=True, exist_ok=True)
    (src_dir / "a").mkdir(parents=True, exist_ok=True)
    (src_dir / "b" / "track.mp3").write_bytes(b"x")
    (src_dir / "a" / "track.ogg").write_bytes(b"x")

    # sorted by relative path => a/track.ogg comes first
    assert cli._find_audio_source_in_dir(src_dir) == src_dir / "a" / "track.ogg"


def test_cmd_import_dir_returns_2_when_no_supported_audio_found(tmp_path: Path) -> None:
    from aural_ingest import cli

    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "readme.txt").write_text("x", encoding="utf-8")

    args = type("Args", (), {})()
    args.input_dir_path = str(src_dir)
    args.out = str(tmp_path / "x.auralsong")
    args.profile = "full"
    args.config = None
    args.title = None
    args.artist = None
    args.duration_sec = None

    assert cli.cmd_import_dir(args) == 2


def test_cmd_import_dir_forwards_selected_source_to_cmd_import(tmp_path: Path, monkeypatch) -> None:
    from aural_ingest import cli

    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "mix.wav"
    src.write_bytes(b"x")

    seen = {}

    def fake_cmd_import(args):
        seen["input_audio_path"] = args.input_audio_path
        seen["out"] = args.out
        seen["profile"] = args.profile
        return 0

    monkeypatch.setattr(cli, "cmd_import", fake_cmd_import)

    args = type("Args", (), {})()
    args.input_dir_path = str(src_dir)
    args.out = str(tmp_path / "x.auralsong")
    args.profile = "full"
    args.config = "{}"
    args.title = "t"
    args.artist = "a"
    args.duration_sec = None

    assert cli.cmd_import_dir(args) == 0
    assert seen["input_audio_path"] == str(src)
    assert seen["out"] == str(tmp_path / "x.auralsong")
    assert seen["profile"] == "full"


def test_cmd_import_dir_synthesizes_mix_from_configured_input_stems(tmp_path: Path, monkeypatch) -> None:
    from aural_ingest import cli

    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    drums = src_dir / "drums.wav"
    bass = src_dir / "bass.wav"
    drums.write_bytes(b"drums")
    bass.write_bytes(b"bass")

    seen = {}

    def fake_cmd_import(args):
        seen["input_audio_path"] = args.input_audio_path
        seen["config"] = args.config
        assert Path(args.input_audio_path).name == "mix.wav"
        assert Path(args.input_audio_path).is_file()
        return 0

    monkeypatch.setattr(cli, "cmd_import", fake_cmd_import)
    monkeypatch.setattr(
        cli,
        "_synthesize_mix_wav_from_input_stems",
        lambda dst, _config: (dst.write_bytes(b"mix"), (1.0, 48_000))[1],
    )

    args = type("Args", (), {})()
    args.input_dir_path = str(src_dir)
    args.out = str(tmp_path / "x.auralsong")
    args.profile = "full"
    args.config = json.dumps(
        {
            "disable_stem_separation": True,
            "input_stem_paths": {
                "drums": str(drums),
                "bass": str(bass),
            },
        }
    )
    args.title = "t"
    args.artist = "a"
    args.duration_sec = None

    assert cli.cmd_import_dir(args) == 0
    assert Path(seen["input_audio_path"]).name == "mix.wav"
    forwarded_config = json.loads(seen["config"])
    assert forwarded_config["disable_stem_separation"] is True
    assert forwarded_config["input_stem_paths"] == {
        "drums": str(drums),
        "bass": str(bass),
    }
    assert forwarded_config["_synthesized_mix_from_input_stems"] is True


def test_pack_audio_for_analysis_prefers_explicit_mix_over_stale_default_stem(tmp_path: Path) -> None:
    from aural_ingest import cli

    pack = tmp_path / "song.feedpak"
    (pack / "audio" / "stems").mkdir(parents=True)
    (pack / "audio").mkdir(exist_ok=True)
    mix = pack / "audio" / "mix.wav"
    bass = pack / "audio" / "stems" / "bass.wav"
    mix.write_bytes(b"mix")
    bass.write_bytes(b"bass")
    (pack / "manifest.yaml").write_text(
        "\n".join(
            [
                "feedpak_version: 1.11.0",
                "title: Fixture",
                "artist: Unknown",
                "duration: 1.0",
                "arrangements: []",
                "stems:",
                "- id: bass",
                "  file: audio/stems/bass.wav",
                "  default: true",
                "",
            ]
        ),
        encoding="utf-8",
    )

    audio, is_tmp = cli._pack_audio_for_analysis(pack)
    assert audio == mix.resolve()
    assert is_tmp is False


def test_cmd_import_handles_unknown_drum_filter_and_rejects_other_invalid_transcription_options(tmp_path: Path) -> None:
    from aural_ingest import cli

    src = tmp_path / "in.wav"
    src.write_bytes(b"RIFFxxxxWAVE")

    args = type("Args", (), {})()
    args.input_audio_path = str(src)
    args.out = str(tmp_path / "out.auralsong")
    args.profile = "full"
    args.config = None
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "not_valid"
    args.melodic_method = "auto"
    args.beat_analysis_mode = "standard"
    args.stem_separation_provider = "auto"
    args.stem_separation_provider_path = None
    args.shifts = 1
    args.multi_filter = False

    # Unknown drum filter is accepted and normalized by recovery policy.
    tr_opts, tr_err = cli._resolve_transcription_options(args)
    assert tr_err is None
    assert tr_opts is not None
    # Default engine moved off `combined_filter` (worst E-GMD recall) to
    # `beat_conditioned_multiband_decoder`.
    assert tr_opts["drum_filter"] == "beat_conditioned_multiband_decoder"
    assert tr_opts["warnings"]

    args.drum_filter = "combined_filter"
    args.melodic_method = "not_valid"
    assert cli.cmd_import(args) == 2

    args.melodic_method = "auto"
    args.shifts = 0
    assert cli.cmd_import(args) == 2

    args.shifts = 1
    args.beat_analysis_mode = "broken"
    assert cli.cmd_import(args) == 2


def test_roformer_stem_provider_is_builtin_and_not_provider_path(tmp_path: Path) -> None:
    from aural_ingest import cli

    src = tmp_path / "in.wav"
    src.write_bytes(b"RIFFxxxxWAVE")

    args = type("Args", (), {})()
    args.input_audio_path = str(src)
    args.out = str(tmp_path / "out.auralsong")
    args.profile = "full"
    args.config = None
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "combined_filter"
    args.melodic_method = "auto"
    args.beat_analysis_mode = "standard"
    args.stem_separation_provider = "roformer"
    args.stem_separation_provider_path = None
    args.shifts = 2
    args.multi_filter = False

    tr_opts, tr_err = cli._resolve_transcription_options(args)

    assert tr_err is None
    assert tr_opts is not None
    assert tr_opts["stem_separation_provider"] == "roformer"
    assert tr_opts["stem_separation_provider_path"] is None
    assert "roformer" in cli.build_default_stem_separation_provider_registry()


def test_pyinstaller_spec_collects_model_upgrade_runtime_deps() -> None:
    spec_path = Path(__file__).resolve().parents[1] / "aural_ingest.spec"
    spec = spec_path.read_text(encoding="utf-8")

    for package in (
        "jsonschema",
        "mido",
        "mir_eval",
        "museval",
        "onnx",
        "onnxruntime",
        "pretty_midi",
        "yaml",
    ):
        assert f'"{package}"' in spec


def test_python_runtime_deps_are_declared_and_attributed() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    pyproject = tomllib.loads((repo_root / "python" / "ingest" / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = "\n".join(pyproject["project"]["dependencies"])
    readme = (repo_root / "README.md").read_text(encoding="utf-8")

    for dependency, attribution in (
        ("pretty_midi", "pretty_midi"),
        ("PyYAML", "PyYAML"),
        ("mido", "mido"),
        ("mir_eval", "mir_eval"),
    ):
        assert dependency in dependencies
        assert attribution in readme


def test_benchmark_overlay_has_vocal_roster() -> None:
    from aural_ingest.benchmark_overlay import DEFAULT_ROSTER

    assert DEFAULT_ROSTER["vocals"] == [
        "melodic_rmvpe",
        "melodic_torchcrepe",
        "melodic_basic_pitch",
    ]


def test_create_portable_supports_optional_demucs_ft_drums_modelpack() -> None:
    script_path = Path(__file__).resolve().parents[3] / "create_portable.ps1"
    script = script_path.read_text(encoding="utf-8")

    assert "$DemucsFtDrumsModelPackZipPath" in script
    assert "Resolve-DefaultDemucsFtDrumsModelPackZipPath" in script
    assert "demucs_ft_drums.zip" in script
    assert "modelpack.json id must be 'demucs_ft_drums'" in script
    assert "demucs_ft_drums modelpack missing required drums stem role" in script
    assert "Assert-DemucsModelPackWeights" in script
    assert "Get-ZipEntryByDeclaredPath" in script
    assert "weights[0].sha256 missing or invalid" in script
    assert "modelpack weight sha256 mismatch" in script
    assert "Assert-DemucsModelPackLicense" in script
    assert "$demucs6ModelPackLicense = \"MIT\"" in script
    assert "license = $demucs6ModelPackLicense" in script
    assert "license = $demucsFtDrumsModelPackLicense" in script
    assert "modelpack zip missing LICENSE file" in script
    assert "Copy-Item -LiteralPath $demucsFtDrumsZipAbs" in script
    assert "Portable demucs_ft_drums modelpack hash mismatch" in script
    assert 'id = "demucs_ft_drums"' in script
    assert "demucs_ft_drums_modelpack_zip" in script


def test_create_portable_packages_third_party_notices_for_ffmpeg() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "create_portable.ps1").read_text(encoding="utf-8")
    notices = (repo_root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    assert "THIRD_PARTY_NOTICES.md is required when bundling ffmpeg" in script
    assert "$portableThirdPartyNotices" in script
    assert "third_party_notices" in script
    assert "Copy-Item -LiteralPath $thirdPartyNoticesSource" in script
    assert "FFmpeg" in notices
    assert "ffmpeg.org" in notices
    assert "portable_manifest.json" in notices


def test_create_portable_packages_project_license() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "create_portable.ps1").read_text(encoding="utf-8")
    license_text = (repo_root / "LICENSE").read_text(encoding="utf-8")

    assert "LICENSE is required for portable packaging" in script
    assert "$portableProjectLicense" in script
    assert "Copy-Item -LiteralPath $projectLicenseSource" in script
    assert "project_license" in script
    assert "GNU GENERAL PUBLIC LICENSE" in license_text


def test_staged_modelpack_manifests_declare_license_metadata() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "create_portable.ps1").read_text(encoding="utf-8")

    assert "modelpack.json missing license" in script
    assert "license = $manifestLicense" in script
    assert 'license = "CC-BY-4.0"' in script
    assert 'license = "checkpoint-license-per-artifact"' in script
    assert "license_review_required = $true" in script

    for rel_path in (
        "assets/models/drum_crnn/0.4.0/modelpack.json",
        "assets/models/mr_mt3/hf-main-20260325/modelpack.json",
        "assets/models/yourmt3/hf-main-20260325/modelpack.json",
    ):
        manifest_path = repo_root / rel_path
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert isinstance(manifest.get("license"), str)
        assert manifest["license"].strip()


def test_build_sidecar_installs_runtime_dependencies_from_pyproject() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "build_sidecar.ps1").read_text(encoding="utf-8")
    pyproject = tomllib.loads((repo_root / "python" / "ingest" / "pyproject.toml").read_text(encoding="utf-8"))
    runtime_requirements = []
    for raw_line in (repo_root / "python" / "ingest" / "requirements-runtime.txt").read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            runtime_requirements.append(line)

    expected_runtime_requirements = [
        dep for dep in pyproject["project"]["dependencies"] if not dep.startswith("basic-pitch")
    ]
    assert runtime_requirements == expected_runtime_requirements
    assert "requirements-runtime.txt" in script
    assert '"install Basic Pitch without TensorFlow transitive dependency"' in script
    assert '"install ingest sidecar runtime dependencies"' in script
    assert '"install ingest sidecar package after runtime dependencies"' in script


def test_build_sidecar_uses_platform_specific_executable_name() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "build_sidecar.ps1").read_text(encoding="utf-8")

    assert "function Get-SidecarExecutableName" in script
    assert 'return "aural_ingest.exe"' in script
    assert 'return "aural_ingest"' in script
    assert 'Join-Path $ingestRoot ("dist/" + $sidecarExecutableName)' in script
    assert 'Join-Path $outDirAbs $sidecarExecutableName' in script


def test_roformer_stem_provider_missing_runtime_skips(tmp_path: Path, monkeypatch) -> None:
    from aural_ingest import cli

    monkeypatch.delenv("AURAL_ROFORMER_PYTHON", raising=False)
    monkeypatch.delenv("AURAL_ROFORMER_REPO", raising=False)
    monkeypatch.delenv("AURAL_ROFORMER_COMMAND", raising=False)

    status = cli.roformer_runtime_status({})
    assert status["configured"] is False
    assert "AURAL_ROFORMER_PYTHON is unset" in status["missing"]

    result = cli._run_stem_separation(
        tmp_path / "mix.wav",
        tmp_path / "stems",
        mix_sha256="abc123",
        shifts=1,
        config={},
        provider_name="roformer",
        provider_path=None,
    )

    assert result["ok"] is False
    assert result["provider"] == "roformer"
    assert result["status"] == "skipped"
    assert "AURAL_ROFORMER_PYTHON" in result["reason"]


def test_validate_roformer_runtime_reports_required_roles(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from aural_ingest import cli

    fake_python = tmp_path / "python.exe"
    fake_python.write_text("", encoding="utf-8")
    fake_repo = tmp_path / "msst"
    fake_repo.mkdir()
    mix = tmp_path / "mix.wav"
    mix.write_bytes(b"mix")
    stems_dir = tmp_path / "validated-stems"

    def fake_run(command, *_args, **_kwargs):
        config_json = Path(str(command).split("--config ", 1)[1].strip().strip('"'))
        payload = json.loads(config_json.read_text(encoding="utf-8"))
        out_dir = Path(payload["out_dir"])
        (out_dir / "drums.wav").write_bytes(b"drums")
        (out_dir / "vocals.wav").write_bytes(b"vocals")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    report = cli.validate_roformer_runtime(
        mix,
        stems_dir=stems_dir,
        shifts=2,
        config={
            "roformer_python": str(fake_python),
            "roformer_repo": str(fake_repo),
            "roformer_command": "{python_q} infer.py --config {config_json_q}",
        },
        require_roles=["drums", "vocals"],
    )

    assert report["ok"] is True
    assert report["provider"] == "roformer"
    assert report["status"] == "fresh"
    assert report["roles"] == ["drums", "vocals"]
    assert report["missing_roles"] == []
    assert report["runtime"]["configured"] is True
    assert report["stem_paths"] == {
        "drums": "audio/stems/drums.wav",
        "vocals": "audio/stems/vocals.wav",
    }


def test_validate_roformer_runtime_can_require_roles(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from aural_ingest import cli

    fake_python = tmp_path / "python.exe"
    fake_python.write_text("", encoding="utf-8")
    fake_repo = tmp_path / "msst"
    fake_repo.mkdir()
    mix = tmp_path / "mix.wav"
    mix.write_bytes(b"mix")

    def fake_run(command, *_args, **_kwargs):
        config_json = Path(str(command).split("--config ", 1)[1].strip().strip('"'))
        payload = json.loads(config_json.read_text(encoding="utf-8"))
        out_dir = Path(payload["out_dir"])
        (out_dir / "drums.wav").write_bytes(b"drums")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    report = cli.validate_roformer_runtime(
        mix,
        stems_dir=tmp_path / "validated-stems",
        config={
            "roformer_python": str(fake_python),
            "roformer_repo": str(fake_repo),
            "roformer_command": "{python_q} infer.py --config {config_json_q}",
        },
        require_roles=["drums", "vocals"],
    )

    assert report["ok"] is False
    assert report["status"] == "fresh"
    assert report["missing_roles"] == ["vocals"]
    assert report["reason"] == "missing required RoFormer stem roles: vocals"


def test_validate_roformer_runtime_script_writes_report(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    from aural_ingest import cli

    script = _load_validate_roformer_runtime_script()
    mix = tmp_path / "mix.wav"
    mix.write_bytes(b"mix")
    stems_dir = tmp_path / "stems"
    output = tmp_path / "roformer-report.json"
    captured: dict[str, object] = {}

    def fake_validate_roformer_runtime(mix_wav, *, stems_dir, shifts, require_roles):
        captured["mix_wav"] = mix_wav
        captured["stems_dir"] = stems_dir
        captured["shifts"] = shifts
        captured["require_roles"] = require_roles
        return {
            "ok": True,
            "provider": "roformer",
            "mix_wav": str(mix_wav),
            "stems_dir": str(stems_dir),
            "status": "fresh",
            "reason": None,
            "require_roles": require_roles,
            "missing_roles": [],
            "roles": ["drums"],
            "stem_paths": {"drums": "audio/stems/drums.wav"},
            "runtime": {"configured": True},
            "raw_result": {},
        }

    monkeypatch.setattr(cli, "validate_roformer_runtime", fake_validate_roformer_runtime)

    rc = script.main(
        [
            str(mix),
            "--stems-dir",
            str(stems_dir),
            "--shifts",
            "3",
            "--require-role",
            "drums",
            "--output",
            str(output),
        ]
    )

    assert rc == 0
    assert capsys.readouterr().out.strip() == str(output)
    assert captured == {
        "mix_wav": mix,
        "stems_dir": stems_dir,
        "shifts": 3,
        "require_roles": ["drums"],
    }
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["provider"] == "roformer"


def test_validate_roformer_runtime_script_writes_gate_evidence(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    from aural_ingest import cli

    script = _load_validate_roformer_runtime_script()
    evidence_root = tmp_path / "evidence"
    mix = tmp_path / "mix.wav"
    mix.write_bytes(b"mix")
    stems_dir = tmp_path / "stems"
    monkeypatch.setenv("AURAL_MODEL_UPGRADE_EVIDENCE_ROOT", str(evidence_root))

    def fake_validate_roformer_runtime(mix_wav, *, stems_dir, shifts, require_roles):
        return {
            "ok": True,
            "provider": "roformer",
            "mix_wav": str(mix_wav),
            "stems_dir": str(stems_dir),
            "status": "fresh",
            "reason": None,
            "require_roles": require_roles,
            "missing_roles": [],
            "roles": ["bass", "drums", "other", "vocals"],
            "stem_paths": {
                "bass": "audio/stems/bass.wav",
                "drums": "audio/stems/drums.wav",
                "other": "audio/stems/other.wav",
                "vocals": "audio/stems/vocals.wav",
            },
            "runtime": {"configured": True},
        }

    monkeypatch.setattr(cli, "validate_roformer_runtime", fake_validate_roformer_runtime)

    rc = script.main(
        [
            str(mix),
            "--stems-dir",
            str(stems_dir),
            "--require-role",
            "bass",
            "--require-role",
            "drums",
            "--require-role",
            "other",
            "--require-role",
            "vocals",
            "--write-gate-evidence",
        ]
    )

    assert rc == 0
    output = Path(capsys.readouterr().out.strip())
    assert output.parent == evidence_root / "benchmarks" / "runtime" / "runs"
    assert output.name.endswith("_roformer_runtime.json")
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["provider"] == "roformer"
    assert payload["require_roles"] == ["bass", "drums", "other", "vocals"]


def test_validate_roformer_runtime_script_requires_all_roles_for_gate_evidence(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    script = _load_validate_roformer_runtime_script()
    evidence_root = tmp_path / "evidence"
    monkeypatch.setenv("AURAL_MODEL_UPGRADE_EVIDENCE_ROOT", str(evidence_root))

    with pytest.raises(SystemExit) as exc_info:
        script.main([str(tmp_path / "mix.wav"), "--write-gate-evidence", "--require-role", "drums"])

    assert exc_info.value.code == 2
    assert (
        "--write-gate-evidence requires --require-role bass --require-role drums "
        "--require-role other --require-role vocals"
    ) in capsys.readouterr().err
    assert not list(evidence_root.glob("benchmarks/runtime/runs/*_roformer_runtime.json"))


def test_validate_roformer_runtime_script_rejects_missing_mix(tmp_path: Path, capsys) -> None:
    script = _load_validate_roformer_runtime_script()
    missing = tmp_path / "missing.wav"

    rc = script.main([str(missing)])

    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    assert captured.err.strip() == f"validate_roformer_runtime: input WAV not found: {missing}"


def test_roformer_stem_provider_rejects_python_directory(tmp_path: Path) -> None:
    from aural_ingest import cli

    fake_python_dir = tmp_path / "python-dir"
    fake_python_dir.mkdir()
    fake_repo = tmp_path / "msst"
    fake_repo.mkdir()

    result = cli._run_stem_separation(
        tmp_path / "mix.wav",
        tmp_path / "stems",
        mix_sha256="abc123",
        shifts=1,
        config={
            "roformer_python": str(fake_python_dir),
            "roformer_repo": str(fake_repo),
            "roformer_command": "{python_q} -m msst.predict",
        },
        provider_name="roformer",
        provider_path=None,
    )

    assert result["ok"] is False
    assert result["provider"] == "roformer"
    assert result["status"] == "skipped"
    assert "not a file" in result["reason"]


def test_roformer_stem_provider_rejects_repo_file(tmp_path: Path) -> None:
    from aural_ingest import cli

    fake_python = tmp_path / "python.exe"
    fake_python.write_text("", encoding="utf-8")
    fake_repo_file = tmp_path / "repo.txt"
    fake_repo_file.write_text("not a repo", encoding="utf-8")

    result = cli._run_stem_separation(
        tmp_path / "mix.wav",
        tmp_path / "stems",
        mix_sha256="abc123",
        shifts=1,
        config={
            "roformer_python": str(fake_python),
            "roformer_repo": str(fake_repo_file),
            "roformer_command": "{python_q} -m msst.predict",
        },
        provider_name="roformer",
        provider_path=None,
    )

    assert result["ok"] is False
    assert result["provider"] == "roformer"
    assert result["status"] == "skipped"
    assert "not a directory" in result["reason"]


def test_roformer_stem_provider_command_outputs_stems_and_respects_protected_roles(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from aural_ingest import cli

    fake_python = tmp_path / "python.exe"
    fake_python.write_text("", encoding="utf-8")
    fake_repo = tmp_path / "msst"
    fake_repo.mkdir()
    mix = tmp_path / "mix.wav"
    mix.write_bytes(b"mix")
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    protected_keys = stems_dir / "keys.wav"
    protected_keys.write_bytes(b"user supplied keys")
    captured: dict[str, object] = {}

    def fake_run(command, cwd, env, capture_output, text, shell, timeout):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["env_pythonpath"] = env.get("PYTHONPATH", "")
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["shell"] = shell
        captured["timeout"] = timeout
        config_json = Path(str(command).split("--config ", 1)[1].strip().strip('"'))
        payload = json.loads(config_json.read_text(encoding="utf-8"))
        out_dir = Path(payload["out_dir"])
        (out_dir / "drums.wav").write_bytes(b"drums")
        (out_dir / "piano.wav").write_bytes(b"separated keys")
        (out_dir / "vocals.wav").write_bytes(b"vocals")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = cli._run_stem_separation(
        mix,
        stems_dir,
        mix_sha256="abc123",
        shifts=3,
        config={
            "roformer_python": str(fake_python),
            "roformer_repo": str(fake_repo),
            "roformer_command": "{python_q} infer.py --config {config_json_q}",
            "roformer_timeout_sec": "9",
        },
        provider_name="roformer",
        provider_path=None,
        protected_roles=["keys"],
    )

    assert result["ok"] is True
    assert result["provider"] == "roformer"
    assert result["status"] == "fresh"
    assert result["stem_paths"] == {
        "drums": "audio/stems/drums.wav",
        "keys": "audio/stems/keys.wav",
        "vocals": "audio/stems/vocals.wav",
    }
    assert (stems_dir / "drums.wav").read_bytes() == b"drums"
    assert protected_keys.read_bytes() == b"user supplied keys"
    assert (stems_dir / "vocals.wav").read_bytes() == b"vocals"
    assert str(fake_python) in str(captured["command"])
    assert captured["cwd"] == str(fake_repo)
    assert str(fake_repo) in str(captured["env_pythonpath"])
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["shell"] is True
    assert captured["timeout"] == 9.0


def test_cmd_import_dir_forwards_transcription_options(tmp_path: Path, monkeypatch) -> None:
    from aural_ingest import cli

    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "mix.wav"
    src.write_bytes(b"x")

    seen = {}

    def fake_cmd_import(args):
        seen["drum_filter"] = args.drum_filter
        seen["drum_silence_gate_dbfs"] = args.drum_silence_gate_dbfs
        seen["drum_silence_gate_window_ms"] = args.drum_silence_gate_window_ms
        seen["drum_silence_gate_disabled"] = args.drum_silence_gate_disabled
        seen["melodic_method"] = args.melodic_method
        seen["beat_analysis_mode"] = args.beat_analysis_mode
        seen["stem_separation_provider"] = args.stem_separation_provider
        seen["stem_separation_provider_path"] = args.stem_separation_provider_path
        seen["shifts"] = args.shifts
        seen["multi_filter"] = args.multi_filter
        return 0

    monkeypatch.setattr(cli, "cmd_import", fake_cmd_import)

    args = type("Args", (), {})()
    args.input_dir_path = str(src_dir)
    args.out = str(tmp_path / "x.auralsong")
    args.profile = "full"
    args.config = "{}"
    args.title = "t"
    args.artist = "a"
    args.duration_sec = None
    args.drum_filter = "dsp_bandpass_improved"
    args.drum_silence_gate_dbfs = -42.0
    args.drum_silence_gate_window_ms = 45.0
    args.drum_silence_gate_disabled = True
    args.melodic_method = "basic_pitch"
    args.beat_analysis_mode = "high_accuracy"
    args.stem_separation_provider = "none"
    args.stem_separation_provider_path = None
    args.shifts = 3
    args.multi_filter = True

    assert cli.cmd_import_dir(args) == 0
    assert seen == {
        "drum_filter": "dsp_bandpass_improved",
        "drum_silence_gate_dbfs": -42.0,
        "drum_silence_gate_window_ms": 45.0,
        "drum_silence_gate_disabled": True,
        "melodic_method": "basic_pitch",
        "beat_analysis_mode": "high_accuracy",
        "stem_separation_provider": "none",
        "stem_separation_provider_path": None,
        "shifts": 3,
        "multi_filter": True,
    }


# --------------------------------------------------------------------------- #
# MuScriptor gated-weights setup helpers
# --------------------------------------------------------------------------- #

def test_muscriptor_needs_auth_ignores_hex_request_ids() -> None:
    """Hub errors carry a hex request id; a naive substring match on 401/403
    flags a transport failure as "accept the license" and sends the user in
    circles."""
    from aural_ingest import cli

    transport = Exception(
        "Connection reset. (Request ID: Root=1-6a5e2725-5f4dc0bd403ae9e27596f70e)"
    )
    assert cli._muscriptor_needs_auth(transport) is False


def test_muscriptor_needs_auth_detects_real_auth_failures() -> None:
    from aural_ingest import cli

    assert cli._muscriptor_needs_auth(Exception("401 Client Error.")) is True
    assert cli._muscriptor_needs_auth(Exception("Access to model X is gated")) is True
    assert (
        cli._muscriptor_needs_auth(Exception("Cannot reach host: offline mode is enabled"))
        is False
    )


def test_muscriptor_needs_auth_uses_exception_type_when_available() -> None:
    from aural_ingest import cli

    gated = pytest.importorskip("huggingface_hub.errors").GatedRepoError
    # Message carries no auth keywords at all -- only the type identifies it.
    assert cli._muscriptor_needs_auth(gated("nondescript")) is True


def test_muscriptor_progress_watcher_reports_incomplete_blob_growth(tmp_path, capsys) -> None:
    """The download watcher polls huggingface_hub's `*.incomplete` blob.

    Path contract (file_download.py): the partial file is `blob_path +
    ".incomplete"` under `<cache>/models--<org>--<name>/blobs/`, i.e. inside the
    repo dir this watches.
    """
    import threading
    import time

    from aural_ingest import cli

    blobs = tmp_path / "blobs"
    blobs.mkdir()
    partial = blobs / "deadbeef.incomplete"
    partial.write_bytes(b"x" * 250)

    stop = threading.Event()
    watcher = threading.Thread(
        target=cli._watch_incomplete_downloads, args=(tmp_path, 1000, stop), daemon=True
    )
    watcher.start()
    try:
        deadline = time.time() + 6.0
        while time.time() < deadline and "downloaded_bytes" not in capsys.readouterr().out:
            time.sleep(0.2)
    finally:
        stop.set()
        watcher.join(timeout=3.0)

    assert not watcher.is_alive()  # stop event must actually end the thread


def test_muscriptor_progress_watcher_survives_a_missing_dir() -> None:
    """A cache dir that does not exist yet must not kill the watcher thread --
    it is created partway through the first download."""
    import threading

    from aural_ingest import cli

    stop = threading.Event()
    watcher = threading.Thread(
        target=cli._watch_incomplete_downloads,
        args=(Path("no/such/dir"), None, stop),
        daemon=True,
    )
    watcher.start()
    stop.set()
    watcher.join(timeout=3.0)
    assert not watcher.is_alive()
