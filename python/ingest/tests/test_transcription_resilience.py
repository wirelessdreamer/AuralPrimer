import json
import struct
import wave
from pathlib import Path

import pytest


def _write_clicktrack_wav(path: Path, *, sr: int, duration_sec: float, bpm: float) -> None:
    period_samples = int(round((60.0 / bpm) * sr))
    total_samples = int(round(duration_sec * sr))
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sr)
        for i in range(total_samples):
            sample = 30000 if i % period_samples == 0 else 0
            wav_file.writeframesraw(struct.pack("<h", sample))


def _drum_note_set(notes_mid: bytes) -> set[int]:
    notes: set[int] = set()
    for i in range(0, len(notes_mid) - 2):
        if notes_mid[i] != 0x99:
            continue
        note = notes_mid[i + 1]
        velocity = notes_mid[i + 2]
        if velocity > 0:
            notes.add(note)
    return notes


def test_default_drum_engine_recovery_prefers_combined_filter() -> None:
    from aural_ingest.transcription import DEFAULT_DRUM_ENGINE, resolve_drum_filter

    assert DEFAULT_DRUM_ENGINE == "combined_filter"
    assert resolve_drum_filter(None) == ("combined_filter", [])
    assert resolve_drum_filter(" auto ") == ("combined_filter", [])


def test_unknown_drum_filter_warns_and_recovers_to_combined_filter() -> None:
    from aural_ingest.transcription import resolve_drum_filter

    normalized, warnings = resolve_drum_filter("legacy_unknown")
    assert normalized == "combined_filter"
    assert warnings
    assert "legacy_unknown" in warnings[0]
    assert "combined_filter" in warnings[0]


def test_default_import_dir_path_restores_expanded_drum_note_diversity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aural_ingest import cli
    from aural_ingest.transcription import DrumEvent, MelodicNote

    src_dir = tmp_path / "fixture"
    src_dir.mkdir()
    src = src_dir / "fixture_mix.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=2.0, bpm=120.0)
    out = tmp_path / "Recovered.songpack"

    expanded_notes = [36, 38, 41, 42, 46, 47, 49, 50, 51]
    core_only_notes = [36, 38, 42]
    seen_calls: list[str] = []

    def combined_filter(_stem: Path) -> list[DrumEvent]:
        seen_calls.append("combined_filter")
        return [
            DrumEvent(time=index * 0.1, note=note, velocity=100)
            for index, note in enumerate(expanded_notes)
        ]

    def adaptive_beat_grid(_stem: Path) -> list[DrumEvent]:
        seen_calls.append("adaptive_beat_grid")
        return [
            DrumEvent(time=index * 0.1, note=note, velocity=100)
            for index, note in enumerate(core_only_notes)
        ]

    monkeypatch.setattr(
        cli,
        "build_default_drum_algorithm_registry",
        lambda: {
            "combined_filter": combined_filter,
            "adaptive_beat_grid": adaptive_beat_grid,
        },
    )
    monkeypatch.setattr(
        cli,
        "build_default_melodic_algorithm_registry",
        lambda *args, **kwargs: {
            "basic_pitch": lambda _stem: [MelodicNote(t_on=0.0, t_off=0.1, pitch=60, velocity=90)],
            "pyin": lambda _stem: [MelodicNote(t_on=0.0, t_off=0.1, pitch=60, velocity=90)],
        },
    )

    args = type("Args", (), {})()
    args.input_dir_path = str(src_dir)
    args.out = str(out)
    args.profile = "full"
    args.config = "{}"
    args.title = "Fixture Recovery"
    args.artist = ""
    args.duration_sec = None
    args.melodic_method = "auto"
    args.shifts = 1
    args.multi_filter = False

    assert cli.cmd_import_dir(args) == 0

    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    transcription = manifest["pipeline"]["transcription"]
    notes_mid = (out / "features" / "notes.mid").read_bytes()
    recovered_notes = _drum_note_set(notes_mid)

    assert transcription["drum_filter"] == "combined_filter"
    assert transcription["drum_filter_requested"] == "combined_filter"
    assert transcription["drum_filter_used"] == "combined_filter"
    assert transcription["drum_attempted_algorithms"] == ["combined_filter"]
    assert seen_calls == ["combined_filter"]
    assert recovered_notes == set(expanded_notes)
    assert recovered_notes > set(core_only_notes)
    assert any(note in recovered_notes for note in {41, 46, 47, 49, 50, 51})
