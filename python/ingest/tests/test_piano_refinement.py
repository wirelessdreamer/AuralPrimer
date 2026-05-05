import json
from pathlib import Path

from aural_ingest.piano_benchmark import write_melodic_notes_midi
from aural_ingest.transcription import MelodicNote


def _write_source_midi(path: Path, notes: list[MelodicNote]) -> None:
    write_melodic_notes_midi(notes, path)


def test_parse_refinement_methods_expands_profiles() -> None:
    from aural_ingest.piano_refinement import parse_refinement_methods

    assert parse_refinement_methods(None)[:3] == [
        "source_midi",
        "source_midi_clean",
        "source_midi_clean_playable",
    ]
    assert parse_refinement_methods("source_midi,piano_polyphonic_clean") == [
        "source_midi",
        "piano_polyphonic_clean",
    ]
    research = parse_refinement_methods(["research_ab"])
    assert "source_midi_clean" in research
    assert "piano_transkun_clean" in research
    assert len(research) == len(set(research))


def test_playable_reduction_caps_polyphony_and_preserves_melody_and_bass() -> None:
    from aural_ingest.piano_refinement import _max_polyphony, reduce_piano_polyphony_for_playability

    notes = [
        MelodicNote(t_on=0.0, t_off=1.0, pitch=pitch, velocity=60 + idx, instrument="keys")
        for idx, pitch in enumerate([31, 36, 43, 48, 52, 55, 60, 64, 67, 71, 76])
    ]

    reduced = reduce_piano_polyphony_for_playability(notes, max_polyphony=7)
    pitches = {note.pitch for note in reduced}

    assert len(reduced) == 7
    assert _max_polyphony(reduced) <= 7
    assert 76 in pitches  # top note is the melody proxy
    assert 36 in pitches  # lowest useful left-hand support is kept
    assert 31 not in pitches  # muddy/extreme low note loses priority


def test_refinement_run_scores_candidates_and_writes_artifacts(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest import piano_refinement

    audio = tmp_path / "keys.wav"
    audio.write_bytes(b"not a real wav; fake registry ignores it")
    source = tmp_path / "suno.mid"
    reference = tmp_path / "truth.mid"
    _write_source_midi(
        source,
        [MelodicNote(t_on=0.0, t_off=0.5, pitch=60, velocity=80, instrument="keys")],
    )
    _write_source_midi(
        reference,
        [MelodicNote(t_on=0.0, t_off=0.5, pitch=64, velocity=90, instrument="keys")],
    )

    monkeypatch.setattr(
        piano_refinement,
        "build_default_melodic_algorithm_registry",
        lambda instrument="keys": {
            "fake_audio": lambda _path: [
                MelodicNote(t_on=0.0, t_off=0.5, pitch=64, velocity=90, instrument=instrument)
            ]
        },
    )
    monkeypatch.setattr(
        piano_refinement.piano_cleanup,
        "cleanup_notes",
        lambda notes, *, stem_path=None, instrument="keys": [
            MelodicNote(
                t_on=n.t_on,
                t_off=n.t_off,
                pitch=n.pitch,
                velocity=min(127, n.velocity + 1),
                instrument=instrument,
            )
            for n in notes
        ],
    )

    out = piano_refinement.run_piano_refinement_workbench(
        audio_path=audio,
        source_midi_path=source,
        reference_midi_path=reference,
        methods=["source_midi", "source_midi_clean", "fake_audio", "missing_method"],
        output_root=tmp_path / "runs",
        label="unit",
    )

    summary = json.loads((out / "summary.json").read_text("utf-8"))
    candidates = {candidate["method"]: candidate for candidate in summary["candidates"]}

    assert summary["reference_available"] is True
    assert summary["recommendation"]["method"] == "fake_audio"
    assert candidates["fake_audio"]["reference_eval"]["f1"] == 1.0
    assert candidates["source_midi"]["source_eval"]["f1"] == 1.0
    assert candidates["missing_method"]["error"]
    assert (out / "report.md").is_file()
    assert (out / "refinement_dashboard.html").is_file()
    assert (out / "playability_report.html").is_file()
    assert (out / "playability_metrics.svg").is_file()
    assert (out / "playability_polyphony.svg").is_file()
    assert (out / "playability_roll.svg").is_file()
    assert (out / "playability_audition_before.wav").read_bytes()[:4] == b"RIFF"
    assert (out / "playability_audition_after.wav").read_bytes()[:4] == b"RIFF"
    assert (out / "playability_audition_ab.wav").read_bytes()[:4] == b"RIFF"
    assert (out / "candidates" / "fake-audio.mid").is_file()
    assert (out / "candidates" / "source-midi-clean.mid").is_file()
    assert (out / "candidates" / "index.json").is_file()

    dashboard = (out / "refinement_dashboard.html").read_text("utf-8")
    assert "Piano MIDI Refinement Workbench" in dashboard
    assert "http://" not in dashboard
    assert "https://" not in dashboard
    playability_report = (out / "playability_report.html").read_text("utf-8")
    assert "Piano Playability Visual Report" in playability_report
    assert "playability_audition_ab.wav" in playability_report
    assert "http://" not in playability_report
    assert "https://" not in playability_report


def test_refinement_without_reference_prefers_conservative_source_cleanup(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest import piano_refinement

    audio = tmp_path / "keys.wav"
    audio.write_bytes(b"x")
    source = tmp_path / "suno.mid"
    _write_source_midi(
        source,
        [
            MelodicNote(t_on=0.0, t_off=0.5, pitch=60, velocity=80, instrument="keys"),
            MelodicNote(t_on=0.5, t_off=0.9, pitch=64, velocity=80, instrument="keys"),
        ],
    )

    monkeypatch.setattr(
        piano_refinement,
        "build_default_melodic_algorithm_registry",
        lambda instrument="keys": {
            "fake_audio": lambda _path: [
                MelodicNote(t_on=0.0, t_off=0.5, pitch=60, velocity=80, instrument=instrument)
            ]
        },
    )
    monkeypatch.setattr(
        piano_refinement.piano_cleanup,
        "cleanup_notes",
        lambda notes, *, stem_path=None, instrument="keys": list(notes),
    )

    out = piano_refinement.run_piano_refinement_workbench(
        audio_path=audio,
        source_midi_path=source,
        methods=["source_midi", "source_midi_clean", "fake_audio"],
        output_root=tmp_path / "runs",
        label="no-ref",
    )
    summary = json.loads((out / "summary.json").read_text("utf-8"))

    assert summary["reference_available"] is False
    assert summary["recommendation"]["method"] == "source_midi_clean"
    assert summary["recommendation"]["requires_human_review"] is True


def test_refinement_playable_candidate_reduces_source_polyphony(monkeypatch, tmp_path: Path) -> None:
    from aural_ingest import piano_refinement

    audio = tmp_path / "keys.wav"
    audio.write_bytes(b"x")
    source = tmp_path / "dense.mid"
    _write_source_midi(
        source,
        [
            MelodicNote(t_on=0.0, t_off=1.0, pitch=pitch, velocity=80, instrument="keys")
            for pitch in [31, 36, 43, 48, 52, 55, 60, 64, 67, 71, 76]
        ],
    )
    monkeypatch.setattr(
        piano_refinement.piano_cleanup,
        "cleanup_notes",
        lambda notes, *, stem_path=None, instrument="keys": list(notes),
    )

    out = piano_refinement.run_piano_refinement_workbench(
        audio_path=audio,
        source_midi_path=source,
        methods=["source_midi_clean", "source_midi_clean_playable"],
        output_root=tmp_path / "runs",
        label="playable",
    )
    summary = json.loads((out / "summary.json").read_text("utf-8"))
    candidates = {candidate["method"]: candidate for candidate in summary["candidates"]}

    assert summary["recommendation"]["method"] == "source_midi_clean_playable"
    assert candidates["source_midi_clean"]["diagnostics"]["max_polyphony"] == 11
    assert candidates["source_midi_clean"]["diagnostics"]["risk_flags"]["playability_polyphony"] is True
    assert candidates["source_midi_clean_playable"]["diagnostics"]["max_polyphony"] == 7
    assert candidates["source_midi_clean_playable"]["diagnostics"]["risk_flags"]["playability_polyphony"] is False
    assert (out / "candidates" / "source-midi-clean-playable.mid").is_file()
    assert "11 -&gt; 7" in (out / "playability_report.html").read_text("utf-8")
    assert "A/B section: before, then after" in (out / "playability_report.html").read_text("utf-8")
    assert "playable cap 7" in (out / "playability_polyphony.svg").read_text("utf-8")
    assert (out / "playability_audition_ab.wav").stat().st_size > (out / "playability_audition_before.wav").stat().st_size


def test_refinement_missing_inputs_fail_clearly(tmp_path: Path) -> None:
    from aural_ingest.piano_refinement import run_piano_refinement_workbench

    source = tmp_path / "source.mid"
    _write_source_midi(
        source,
        [MelodicNote(t_on=0.0, t_off=0.2, pitch=60, velocity=80, instrument="keys")],
    )

    try:
        run_piano_refinement_workbench(
            audio_path=tmp_path / "missing.wav",
            source_midi_path=source,
            output_root=tmp_path / "runs",
        )
    except FileNotFoundError as exc:
        assert "audio file not found" in str(exc)
    else:
        raise AssertionError("expected missing audio to fail")
