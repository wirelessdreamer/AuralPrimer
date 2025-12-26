import json
import struct
import wave
from pathlib import Path

import pytest


def _write_clicktrack_wav(path: Path, *, sr: int, duration_sec: float, bpm: float) -> None:
    """Generate a deterministic mono PCM16 click track.

    - 1 sample impulse per beat
    - silence otherwise
    """

    n = int(sr * duration_sec)
    period_samples = int(round((60.0 / bpm) * sr))
    if period_samples <= 0:
        raise ValueError("invalid bpm")

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)

        for i in range(n):
            # impulse at each beat boundary
            if i % period_samples == 0:
                s = 30000
            else:
                s = 0
            w.writeframesraw(struct.pack("<h", s))


@pytest.mark.parametrize("bpm", [90.0, 120.0])
def test_import_generates_valid_songpack(tmp_path: Path, bpm: float) -> None:
    # Arrange
    src = tmp_path / "src.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=8.0, bpm=bpm)
    out = tmp_path / "Out.songpack"

    # Act
    from aural_ingest.cli import cmd_import

    # Use an instance so we can refer to outer-scope vars without Python class-scope quirks.
    args = type("Args", (), {})()
    args.input_audio_path = str(src)
    args.out = str(out)
    args.profile = "full"
    args.config = json.dumps({"ingest_timestamp": "2000-01-01T00:00:00Z"})
    args.title = "Test"
    args.artist = ""
    args.duration_sec = None

    rc = cmd_import(args)
    assert rc == 0

    # Assert outputs exist
    assert (out / "manifest.json").is_file()
    assert (out / "audio/mix.wav").is_file()
    assert (out / "features/beats.json").is_file()
    assert (out / "features/tempo_map.json").is_file()
    assert (out / "features/sections.json").is_file()
    assert (out / "charts/easy.json").is_file()

    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    assert manifest["assets"]["audio"]["mix_path"] == "audio/mix.wav"
    assert manifest["timing"]["audio_sample_rate_hz"] == 48_000
    assert manifest["duration_sec"] == pytest.approx(8.0, abs=1e-6)
    assert manifest["source"]["ingest_timestamp"] == "2000-01-01T00:00:00Z"

    tempo = json.loads((out / "features/tempo_map.json").read_text("utf-8"))
    assert tempo["segments"][0]["time_signature"] == "4/4"
    # Autocorrelation estimator should get close for an impulse click track.
    assert tempo["segments"][0]["bpm"] == pytest.approx(bpm, abs=1.0)

    beats = json.loads((out / "features/beats.json").read_text("utf-8"))
    beat_times = [b["t"] for b in beats["beats"]]
    assert beat_times[0] == 0.0
    assert all(t2 >= t1 for t1, t2 in zip(beat_times, beat_times[1:]))
    assert beat_times[-1] <= manifest["duration_sec"] + 1e-6

    chart = json.loads((out / "charts/easy.json").read_text("utf-8"))
    assert chart["mode"] == "beats_only"
    assert len(chart["targets"]) == len(beats["beats"])


def test_validate_passes_on_generated_songpack(tmp_path: Path) -> None:
    src = tmp_path / "src.wav"
    _write_clicktrack_wav(src, sr=48_000, duration_sec=4.0, bpm=120.0)
    out = tmp_path / "Out.songpack"

    from aural_ingest.cli import cmd_import, cmd_validate

    import_args = type("Args", (), {})()
    import_args.input_audio_path = str(src)
    import_args.out = str(out)
    import_args.profile = "full"
    import_args.config = json.dumps({"ingest_timestamp": "2000-01-01T00:00:00Z", "bpm_hint": 120})
    import_args.title = None
    import_args.artist = None
    import_args.duration_sec = None

    assert cmd_import(import_args) == 0

    validate_args = type("Args", (), {})()
    validate_args.songpack_dir = str(out)

    assert cmd_validate(validate_args) == 0


def test_nonwav_input_requires_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # If ffmpeg is not present, non-wav sources must fail deterministically.
    src = tmp_path / "src.mp3"
    src.write_bytes(b"not really an mp3")
    out = tmp_path / "Out.songpack"

    from aural_ingest import cli as ingest

    monkeypatch.setattr(ingest, "_have_ffmpeg", lambda: False)

    args = type("Args", (), {})()
    args.input_audio_path = str(src)
    args.out = str(out)
    args.profile = "full"
    args.config = None
    args.title = None
    args.artist = None
    args.duration_sec = None

    rc = ingest.cmd_import(args)
    assert rc == 3
