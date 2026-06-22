"""CQT spectrogram artifact: alignment + artifact-writing tests."""
from __future__ import annotations

import json

import numpy as np

from aural_ingest.spectrogram import (
    FMIN_MIDI,
    compute_cqt_db,
    write_spectrogram_artifact,
)


def _write_tone(path, midi: int, sr: int = 22050, dur: float = 2.0) -> None:
    import librosa
    import soundfile as sf

    f = float(librosa.midi_to_hz(midi))
    t = np.linspace(0.0, dur, int(sr * dur), endpoint=False)
    y = (0.5 * np.sin(2 * np.pi * f * t)).astype(np.float32)
    sf.write(str(path), y, sr)


def test_cqt_bin_aligns_to_midi(tmp_path):
    # A pure tone at MIDI 60 (C4) must be brightest at row (60 - fmin_midi).
    wav = tmp_path / "c4.wav"
    _write_tone(wav, 60)
    res = compute_cqt_db(str(wav))
    brightest_row = int(np.argmax(res["db"].mean(axis=1)))
    expected_row = 60 - FMIN_MIDI
    assert abs(brightest_row - expected_row) <= 1


def test_write_artifact_tiles_and_geometry(tmp_path):
    wav = tmp_path / "tone.wav"
    _write_tone(wav, 48, dur=1.0)
    geom = write_spectrogram_artifact(wav, tmp_path / "spec", role="keys")
    assert geom["role"] == "keys"
    assert geom["fmin_midi"] == FMIN_MIDI
    assert geom["n_bins"] == 84
    assert geom["bins_per_semitone"] == 1
    assert geom["bit_depth"] == 16
    assert geom["packing"] == "rg16"
    assert geom["tiles"], "expected at least one tile"
    # geometry round-trips to disk and tile files exist
    on_disk = json.loads((tmp_path / "spec" / "spectrogram.json").read_text(encoding="utf-8"))
    assert on_disk["n_frames"] == geom["n_frames"]
    for t in geom["tiles"]:
        assert (tmp_path / "spec" / t["file"]).is_file()
