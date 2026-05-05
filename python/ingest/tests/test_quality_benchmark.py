import json
from pathlib import Path

import pytest

from aural_ingest.transcription import DrumEvent, MelodicNote


def _vlq(value: int) -> bytes:
    value = max(0, int(value))
    buffer = value & 0x7F
    value >>= 7
    out = [buffer]
    while value:
        out.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(out)


def _named_note_track(name: str, *, channel: int, pitch: int, start_tick: int = 0, duration_tick: int = 240) -> bytes:
    body = bytearray()
    encoded_name = name.encode("utf-8")
    body.extend(_vlq(0) + b"\xff\x03" + _vlq(len(encoded_name)) + encoded_name)
    body.extend(_vlq(start_tick) + bytes([0x90 | channel, pitch, 90]))
    body.extend(_vlq(duration_tick) + bytes([0x80 | channel, pitch, 0]))
    body.extend(_vlq(0) + b"\xff\x2f\x00")
    return b"MTrk" + len(body).to_bytes(4, "big") + bytes(body)


def _write_named_multitrack_midi(path: Path) -> None:
    tracks = [
        _named_note_track("Drums", channel=9, pitch=36, start_tick=0),
        _named_note_track("Keys", channel=3, pitch=64, start_tick=120),
        _named_note_track("Rhythm Guitar", channel=1, pitch=52, start_tick=240),
    ]
    header = b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big") + len(tracks).to_bytes(2, "big") + (480).to_bytes(2, "big")
    path.write_bytes(header + b"".join(tracks))


def test_transcription_profiles_are_valid_and_role_specific() -> None:
    from aural_ingest.transcription import (
        drum_engines_for_profile,
        melodic_methods_for_profile,
        transcription_profile_metadata,
        validate_transcription_profile,
    )

    assert validate_transcription_profile(None) == "gameplay_default"
    assert validate_transcription_profile("research_ab") == "research_ab"
    assert validate_transcription_profile("missing") is None

    meta = transcription_profile_metadata("fidelity_midi")
    assert meta["profile"] == "fidelity_midi"
    assert "piano_transkun_clean" in melodic_methods_for_profile("fidelity_midi", "keys")
    assert "torchcrepe" in melodic_methods_for_profile("research_ab", "bass")
    assert "torchcrepe" not in melodic_methods_for_profile("gameplay_default", "bass")
    assert drum_engines_for_profile("gameplay_default")[0] == "beat_conditioned_multiband_decoder"


def test_melodic_gameplay_metrics_flag_density_duplicates_and_polyphony() -> None:
    from aural_ingest.quality_benchmark import compute_melodic_gameplay_metrics

    notes = [
        MelodicNote(t_on=0.00, t_off=0.50, pitch=48, velocity=90, instrument="keys"),
        MelodicNote(t_on=0.00, t_off=0.50, pitch=60, velocity=90, instrument="keys"),
        MelodicNote(t_on=0.02, t_off=0.40, pitch=60, velocity=88, instrument="keys"),
        MelodicNote(t_on=0.04, t_off=0.40, pitch=64, velocity=88, instrument="keys"),
    ]

    metrics = compute_melodic_gameplay_metrics(notes, duration_sec=1.0, role="keys")

    assert metrics["note_count"] == 4
    assert metrics["playable_density_flag"] is True
    assert metrics["duplicate_count"] == 1
    assert metrics["max_polyphony"] >= 3
    assert metrics["piano_left_hand_notes"] == 1
    assert metrics["piano_right_hand_notes"] == 3


def test_drum_gameplay_metrics_tracks_overlap_and_lane_coverage() -> None:
    from aural_ingest.quality_benchmark import compute_drum_gameplay_metrics

    events = [
        DrumEvent(time=0.0, note=36, velocity=110),
        DrumEvent(time=0.01, note=42, velocity=90),
        DrumEvent(time=0.02, note=42, velocity=88),
        DrumEvent(time=0.5, note=38, velocity=100),
    ]

    metrics = compute_drum_gameplay_metrics(events, duration_sec=2.0)

    assert metrics["event_count"] == 4
    assert metrics["lane_coverage"] == 3
    assert metrics["overlap_count"] >= 2
    assert metrics["duplicate_count"] == 1


def test_classifier_payloads_track_drum_and_piano_classes() -> None:
    from aural_ingest.quality_benchmark import _drum_classifier_payload, _melodic_classifier_payload

    drum_payload = _drum_classifier_payload(
        {
            "predicted_events": [
                {"time": 0.01, "note": 36, "velocity": 100},
                {"time": 0.51, "note": 42, "velocity": 80},
            ]
        },
        [
            DrumEvent(time=0.0, note=36, velocity=100),
            DrumEvent(time=0.5, note=38, velocity=100),
        ],
        tolerance_sec=0.04,
    )

    assert drum_payload["kind"] == "drums"
    assert drum_payload["dimensions"][0]["name"] == "drum_lane"
    assert drum_payload["dimensions"][0]["matched_tp"] == 1
    assert drum_payload["dimensions"][0]["matched_confusions"] == 1
    assert drum_payload["dimensions"][0]["confusions"][0]["reference_class"] == "snare"

    piano_payload = _melodic_classifier_payload(
        [
            MelodicNote(t_on=0.0, t_off=0.7, pitch=60, velocity=90, instrument="keys"),
            MelodicNote(t_on=0.5, t_off=0.9, pitch=73, velocity=90, instrument="keys"),
        ],
        [
            MelodicNote(t_on=0.01, t_off=0.7, pitch=60, velocity=90, instrument="keys"),
            MelodicNote(t_on=0.5, t_off=0.9, pitch=72, velocity=90, instrument="keys"),
        ],
        role="keys",
        tolerance_sec=0.04,
    )

    dimension_names = {dimension["name"] for dimension in piano_payload["dimensions"]}
    pitch_dimension = next(d for d in piano_payload["dimensions"] if d["name"] == "pitch")
    assert piano_payload["kind"] == "piano"
    assert {"pitch", "pitch_class", "octave", "hand_zone", "sustain_bucket"} <= dimension_names
    assert pitch_dimension["matched_tp"] == 1
    assert pitch_dimension["pitch_error_summary"]["semitone_near_confusions"] == 1


def test_start_offset_quarantines_large_offsets() -> None:
    from aural_ingest.quality_benchmark import classify_start_offset

    assert classify_start_offset(10.0, 9.5)["status"] == "ok"
    quarantined = classify_start_offset(10.0, 2.0)
    assert quarantined["status"] == "quarantine"
    assert quarantined["quarantine"] is True


def test_quality_metric_backend_and_dataset_status_is_fail_safe(monkeypatch) -> None:
    from aural_ingest import quality_benchmark

    monkeypatch.setattr(
        quality_benchmark.importlib.util,
        "find_spec",
        lambda name: object() if name == "mir_eval" else None,
    )
    monkeypatch.delenv("AURAL_MUSDB18_ROOT", raising=False)
    monkeypatch.delenv("AURAL_ENST_DRUMS_ROOT", raising=False)

    backends = quality_benchmark.inspect_quality_metric_backends()
    datasets = quality_benchmark.inspect_quality_dataset_sources()
    missing_wav = Path("missing_ref.wav")
    separation = quality_benchmark.evaluate_museval_separation(
        {"vocals": missing_wav},
        {"vocals": missing_wav},
    )

    assert backends["mir_eval"]["ok"] is True
    assert backends["museval"]["ok"] is False
    assert datasets["musdb18"]["ok"] is False
    assert "internal benchmarking only" in datasets["enst_drums"]["ship_policy"]
    assert separation["available"] is False
    assert separation["backend"] == "museval"


def test_mir_eval_transcription_metrics_report_onset_and_offset_modes() -> None:
    import importlib.util

    if importlib.util.find_spec("mir_eval") is None:
        pytest.skip("mir_eval is optional")

    from aural_ingest.quality_benchmark import evaluate_mir_eval_transcription

    reference = [
        MelodicNote(t_on=0.0, t_off=1.0, pitch=60, velocity=90, instrument="keys"),
        MelodicNote(t_on=1.0, t_off=2.0, pitch=64, velocity=90, instrument="keys"),
    ]
    predicted = [
        MelodicNote(t_on=0.01, t_off=1.01, pitch=60, velocity=90, instrument="keys"),
        MelodicNote(t_on=1.02, t_off=1.35, pitch=64, velocity=90, instrument="keys"),
    ]

    metrics = evaluate_mir_eval_transcription(
        predicted,
        reference,
        onset_tolerance_sec=0.06,
        offset_ratio=0.2,
        offset_min_tolerance_sec=0.05,
    )

    assert metrics["available"] is True
    assert metrics["onset"]["f1"] == 1.0
    assert metrics["onset_offset"]["f1"] < metrics["onset"]["f1"]


def test_scan_corpus_finds_songpacks_and_split_stems(tmp_path: Path) -> None:
    from aural_ingest.quality_benchmark import scan_corpus

    songpack = tmp_path / "Song.songpack"
    (songpack / "audio" / "stems").mkdir(parents=True)
    (songpack / "features").mkdir()
    (songpack / "manifest.json").write_text(
        json.dumps(
            {
                "song_id": "abc",
                "title": "Song",
                "duration_sec": 12,
                "pipeline": {"transcription": {"melodic_method_used": "piano_auto"}},
            }
        ),
        encoding="utf-8",
    )
    (songpack / "audio" / "stems" / "keys.wav").write_bytes(b"x")
    (songpack / "audio" / "stems" / "guitar.wav").write_bytes(b"x")
    (songpack / "audio" / "stems" / "rhythm_guitar.wav").write_bytes(b"x")
    (songpack / "features" / "notes.mid").write_bytes(b"MThd")

    split = tmp_path / "split"
    split.mkdir()
    (split / "drums.wav").write_bytes(b"x")
    (split / "bass.wav").write_bytes(b"x")

    run = tmp_path / "benchmarks" / "piano" / "runs" / "20260501_case"
    (run / "predictions" / "case").mkdir(parents=True)
    (run / "summary.json").write_text("{}", encoding="utf-8")
    (run / "report.md").write_text("# report", encoding="utf-8")
    (run / "predictions" / "case" / "piano.mid").write_bytes(b"MThd")

    payload = scan_corpus(tmp_path)

    assert payload["songpack_count"] == 1
    assert payload["songpacks"][0]["has_notes_mid"] is True
    assert payload["songpacks"][0]["midi_files"]
    assert payload["songpacks"][0]["stems"]["keys"].endswith("keys.wav")
    assert payload["split_stem_folder_count"] >= 1
    assert payload["benchmark_artifact_count"] == 1
    assert payload["benchmark_artifacts"][0]["prediction_midi_count"] == 1


def test_build_quality_manifest_from_scan_tracks_sources_and_references(tmp_path: Path) -> None:
    from aural_ingest.quality_benchmark import build_quality_manifest_from_scan, scan_corpus

    songpack = tmp_path / "Psalm.songpack"
    (songpack / "audio" / "stems").mkdir(parents=True)
    (songpack / "features").mkdir()
    (songpack / "manifest.json").write_text(
        json.dumps(
            {
                "song_id": "psalm",
                "title": "Psalm",
                "duration_sec": 31.5,
                "pipeline": {
                    "transcription": {
                        "instrument_melodic_methods": {"keys": "piano_auto"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (songpack / "audio" / "stems" / "keys.wav").write_bytes(b"x")
    (songpack / "audio" / "stems" / "guitar.wav").write_bytes(b"x")
    (songpack / "audio" / "stems" / "rhythm_guitar.wav").write_bytes(b"x")
    (songpack / "features" / "notes.mid").write_bytes(b"MThd")

    split = tmp_path / "Psalm split"
    split.mkdir()
    (split / "drums.wav").write_bytes(b"x")
    (split / "bass.wav").write_bytes(b"x")

    manifest = build_quality_manifest_from_scan(scan_corpus(tmp_path))
    cases = {case["id"]: case for case in manifest["cases"]}

    assert cases["psalm-keys"]["family"] == "piano"
    assert cases["psalm-keys"]["reference_midi"].endswith("notes.mid")
    assert cases["psalm-keys"]["current_method"] == "piano_auto"
    rhythm_ids = sorted(case["id"] for case in manifest["cases"] if case["role"] == "rhythm_guitar")
    assert len(rhythm_ids) == len(set(rhythm_ids))
    assert any(case_id.startswith("psalm-rhythm-guitar") for case_id in rhythm_ids)
    assert cases["psalm-split-drums"]["family"] == "drums"
    assert cases["psalm-split-drums"]["source"] == "split_stem_folder"
    assert cases["psalm-split-bass"]["stem_provenance"] == "pre_split_folder"

    referenced_only = build_quality_manifest_from_scan(
        scan_corpus(tmp_path),
        include_unreferenced=False,
    )
    assert "psalm-keys" in [case["id"] for case in referenced_only["cases"]]
    assert all(case["source"] == "songpack" for case in referenced_only["cases"])


def test_quality_manifest_loads_cases(tmp_path: Path) -> None:
    from aural_ingest.quality_benchmark import load_quality_manifest

    manifest = tmp_path / "quality.json"
    manifest.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "case1",
                        "family": "piano",
                        "role": "keys",
                        "wav": str(tmp_path / "keys.wav"),
                        "reference_midi": str(tmp_path / "ref.mid"),
                        "offset_sec": 0.25,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    cases = load_quality_manifest(manifest)

    assert cases[0].case_id == "case1"
    assert cases[0].role == "keys"
    assert cases[0].offset_sec == pytest.approx(0.25)


def test_filter_quality_cases_by_role_pattern_and_limit(tmp_path: Path) -> None:
    from aural_ingest.quality_benchmark import QualityCase, filter_quality_cases

    cases = [
        QualityCase(case_id="psalm-130-keys", name="Psalm 130 Keys", role="keys", family="piano", wav_path=tmp_path / "keys.wav"),
        QualityCase(case_id="psalm-130-drums", name="Psalm 130 Drums", role="drums", family="drums", wav_path=tmp_path / "drums.wav"),
        QualityCase(case_id="psalm-2-keys", name="Psalm 2 Keys", role="keys", family="piano", wav_path=tmp_path / "keys2.wav"),
    ]

    filtered = filter_quality_cases(cases, roles=["keys"], case_filters=["130"], max_cases=1)

    assert [case.case_id for case in filtered] == ["psalm-130-keys"]


def test_combined_midi_references_filter_by_role_track_name(tmp_path: Path) -> None:
    from aural_ingest.melodic_benchmark import parse_melodic_midi_reference
    from aural_ingest.piano_benchmark import parse_piano_midi_reference

    midi = tmp_path / "notes.mid"
    _write_named_multitrack_midi(midi)

    keys = parse_piano_midi_reference(midi, role="keys")
    guitar = parse_melodic_midi_reference(midi, role="rhythm_guitar")
    unfiltered = parse_melodic_midi_reference(midi)

    assert [event.pitch for event in keys] == [64]
    assert [event.pitch for event in guitar] == [52]
    assert sorted(event.pitch for event in unfiltered) == [36, 52, 64]


def test_drum_hat_overlay_allows_hat_over_confident_core() -> None:
    from aural_ingest.algorithms.beat_conditioned_multiband_decoder import (
        _should_emit_hat_overlay,
    )

    votes = {"kick": 0.62, "snare": 0.02, "hi_hat": 0.17, "cymbal": 0.0, "tom": 0.0}

    assert _should_emit_hat_overlay(
        emit_core=True,
        hat_conf=0.29,
        core_score=0.56,
        votes=votes,
        high_hit=0.24,
        high_dom=0.14,
    )
    assert not _should_emit_hat_overlay(
        emit_core=True,
        hat_conf=0.18,
        core_score=0.56,
        votes=votes,
        high_hit=0.09,
        high_dom=0.08,
    )


def test_optional_model_backend_contracts_are_fail_safe(monkeypatch) -> None:
    from aural_ingest import quality_benchmark

    monkeypatch.setattr(
        quality_benchmark,
        "available_mt3_modelpacks",
        lambda _roots=None: {
            "yourmt3_drums": {"ok": False, "error": "missing"},
            "mr_mt3_drums": {"ok": False, "error": "missing"},
        },
    )
    monkeypatch.setattr(
        quality_benchmark.importlib.util,
        "find_spec",
        lambda name: object() if name in {"basic_pitch", "torchcrepe", "torch"} else None,
    )
    monkeypatch.delenv("AURAL_PIANO_HFT_CHECKPOINT", raising=False)
    monkeypatch.delenv("AURAL_PIANO_HFT_COMMAND", raising=False)

    backends = quality_benchmark.inspect_optional_model_backends([])

    assert backends["basic_pitch"]["adapter_contract"]["portable_safe_absence"] is True
    assert backends["torchcrepe"]["ok"] is True
    assert backends["piano_hft"]["ok"] is False
    assert "piano_hft_clean" in backends["piano_hft"]["methods"]


def test_quality_output_writes_heatmaps(tmp_path: Path) -> None:
    from aural_ingest.quality_benchmark import write_quality_outputs

    payload = {
        "generated_at_utc": "2026-05-01T00:00:00Z",
        "profile": {"profile": "gameplay_default"},
        "tolerance_ms": 60.0,
        "quality_metric_backends": {
            "mir_eval": {
                "ok": True,
                "purpose": "note-event transcription metrics",
                "protocols": ["mir_eval.transcription"],
                "failure_mode": "optional",
            },
            "museval": {
                "ok": False,
                "purpose": "source-separation SDR protocol",
                "protocols": ["BSSEval v4"],
                "failure_mode": "optional",
            },
        },
        "dataset_sources": {
            "musdb18": {
                "ok": False,
                "purpose": "separation",
                "roots": [{"env_var": "AURAL_MUSDB18_ROOT"}],
                "ship_policy": "internal benchmarking only",
            }
        },
        "model_backends": {"basic_pitch": {"ok": False, "methods": ["basic_pitch"]}},
        "cases": [
            {
                "case_id": "case1",
                "role": "keys",
                "results": [
                    {
                        "algorithm": "piano_auto",
                        "overall": {"f1": 0.5},
                        "note_count": 10,
                        "gameplay": {"duplicate_rate": 0.0, "playable_density_flag": False},
                        "sync": {"quarantine": False},
                        "mir_eval": {
                            "onset": {"f1": 0.6},
                            "onset_offset": {"f1": 0.4},
                        },
                        "classifier": {
                            "kind": "piano",
                            "primary_dimension": "pitch",
                            "coverage": {"reference_events": 12, "predicted_events": 10},
                            "timeline": [{"start": 0.0, "end": 1.0, "tp": 3, "fp": 1, "fn": 2}],
                            "dimensions": [
                                {
                                    "name": "pitch",
                                    "matched_tp": 3,
                                    "matched_confusions": 1,
                                    "unmatched_predictions": 1,
                                    "unmatched_references": 2,
                                    "pitch_error_summary": {
                                        "semitone_near_confusions": 1,
                                        "octave_confusions": 0,
                                    },
                                    "class_metrics": [
                                        {
                                            "label": "C4",
                                            "reference_count": 4,
                                            "predicted_count": 3,
                                            "tp": 3,
                                            "precision": 1.0,
                                            "recall": 0.75,
                                            "f1": 0.857143,
                                        }
                                    ],
                                    "confusions": [
                                        {"reference_class": "C4", "predicted_class": "C#4", "count": 1}
                                    ],
                                }
                            ],
                        },
                    },
                    {
                        "algorithm": "legacy",
                        "overall": {"f1": 0.1},
                        "note_count": 200,
                        "gameplay": {"duplicate_rate": 0.2, "playable_density_flag": True},
                        "sync": {"quarantine": True},
                    },
                ],
            }
        ],
    }

    out = write_quality_outputs(payload, output_root=tmp_path, label="unit")

    assert (out / "f1_heatmap.svg").is_file()
    assert (out / "gameplay_risk_heatmap.svg").is_file()
    assert (out / "classifier_performance.html").is_file()
    report = (out / "report.md").read_text("utf-8")
    classifier_html = (out / "classifier_performance.html").read_text("utf-8")
    summary = json.loads((out / "summary.json").read_text("utf-8"))["summary"]

    assert "Optional Model Backends" in report
    assert "Quality Metric Backends" in report
    assert "Research Dataset Sources" in report
    assert "MIR Onset F1" in report
    assert "Promotion Candidates" in report
    assert "classifier_performance.html" in report
    assert "Classifier Performance Explorer" in classifier_html
    assert 'id="classifier-data"' in classifier_html
    assert "Per-Class Metrics" in classifier_html
    assert "MIR onset F1" in classifier_html
    assert "http://" not in classifier_html
    assert "https://" not in classifier_html
    assert summary["promotion_candidates"][0]["algorithm"] == "piano_auto"
    piano_summary = next(row for row in summary["algorithm_summaries"] if row["algorithm"] == "piano_auto")
    assert piano_summary["mir_eval_mean_onset_f1"] == 0.6
    assert summary["promotion_candidates"][0]["promotion_status"] == "benchmark_winner_review_required"
    assert summary["promotion_candidates"][0]["can_promote_without_review"] is False


def test_torchcrepe_missing_package_surfaces_clear_error(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest.algorithms import melodic_torchcrepe

    monkeypatch.setattr(
        melodic_torchcrepe.importlib,
        "import_module",
        lambda _name: (_ for _ in ()).throw(ModuleNotFoundError("missing")),
    )

    with pytest.raises(RuntimeError, match="torchcrepe"):
        melodic_torchcrepe.transcribe(tmp_path / "audio.wav")
