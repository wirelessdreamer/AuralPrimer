from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aural_ingest.progress import ProgressEvent, emit, log


@dataclass(frozen=True)
class Stage:
    id: str
    version: str
    outputs: list[str]


PIPELINE_ID = "aural_ingest"
PIPELINE_VERSION = "0.1.0"
SCHEMA_VERSION = "1.0.0"


STAGES: list[Stage] = [
    Stage(id="init_songpack", version="0.1.0", outputs=["manifest.json"]),
    # We always produce mix.wav. Compressed assets are optional (only produced when ffmpeg is available).
    Stage(id="decode_audio", version="0.2.0", outputs=["audio/mix.wav", "audio/mix.mp3", "audio/mix.ogg"]),
    Stage(id="beats_tempo", version="0.1.0", outputs=["features/beats.json", "features/tempo_map.json"]),
    Stage(id="sections", version="0.1.0", outputs=["features/sections.json"]),
    Stage(id="chart_generation", version="0.1.0", outputs=["charts/easy.json"]),
]


def _parse_config_arg(raw: str | None) -> dict[str, Any]:
    """Parse --config.

    Contract (docs/ingest-pipeline.md): --config <json>

    For convenience we accept either:
    - a JSON string
    - a path to a JSON file
    """

    if not raw:
        return {}

    p = Path(raw)
    if p.is_file():
        return json.loads(p.read_text("utf-8"))

    return json.loads(raw)


def _have_ffmpeg() -> bool:
    try:
        cp = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=False)
        return cp.returncode == 0
    except FileNotFoundError:
        return False


def _decode_to_wav(src: Path, dst_wav: Path, *, target_sr: int = 48_000) -> tuple[float, int]:
    """Create a deterministic PCM16 mono WAV (48kHz default).

    Returns (duration_sec, sample_rate).

    Supported inputs:
    - .wav (PCM16): copied through (no resample)
    - others: requires ffmpeg on PATH
    """

    if src.suffix.lower() == ".wav":
        # Keep the original wav as-is. This keeps behavior simple and avoids ffmpeg dependency
        # for our CI + unit tests.
        shutil.copyfile(src, dst_wav)
        duration, sr = _wav_duration_sec(dst_wav)
        return duration, sr

    if not _have_ffmpeg():
        raise RuntimeError(
            "ffmpeg not found on PATH; non-wav inputs require ffmpeg for decode. "
            "Provide a .wav input or install ffmpeg."
        )

    # Force deterministic output:
    # - PCM16 mono
    # - normalized sample rate
    # Note: ffmpeg can still include encoder metadata in non-wav outputs; we only require wav here.
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(src),
        "-ac",
        "1",
        "-ar",
        str(target_sr),
        "-c:a",
        "pcm_s16le",
        str(dst_wav),
    ]
    cp = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if cp.returncode != 0:
        raise RuntimeError(f"ffmpeg decode failed: {cp.stderr.strip()}")

    duration, sr = _wav_duration_sec(dst_wav)
    return duration, sr


def _wav_duration_sec(p: Path) -> tuple[float, int]:
    import wave

    with wave.open(str(p), "rb") as w:
        frames = w.getnframes()
        sr = w.getframerate()
        if sr <= 0:
            return 0.0, sr
        return float(frames) / float(sr), sr


def _estimate_bpm_from_wav(wav_path: Path) -> float:
    """Very simple, fully-deterministic BPM estimator.

    This is intentionally naive (RMS onset autocorrelation), but it is *real analysis* and works
    well for click-tracks / synthetic fixtures.
    """

    import wave
    from array import array

    with wave.open(str(wav_path), "rb") as w:
        sr = w.getframerate()
        channels = w.getnchannels()
        sampwidth = w.getsampwidth()
        if sampwidth != 2:
            raise RuntimeError(f"unsupported wav sample width: {sampwidth} (only PCM16 supported)")

        # Smaller hop improves tempo resolution for the simple autocorrelation approach.
        # (e.g., at 48kHz with hop=1024, 120bpm maps to a non-integer lag and can be off by ~2bpm.)
        hop = 256
        rms: list[float] = []

        while True:
            frames = w.readframes(hop)
            if not frames:
                break
            a = array("h")
            a.frombytes(frames)

            # Convert to mono float [-1,1] in a streaming-friendly way.
            if channels == 1:
                mono_iter = a
                n = len(a)
                if n == 0:
                    continue
                ss = 0.0
                for x in mono_iter:
                    ss += float(x) * float(x)
                rms.append(math.sqrt(ss / float(n)) / 32768.0)
            else:
                # average channels (supports 2+ channels)
                n_frames = len(a) // channels
                if n_frames <= 0:
                    continue
                ss = 0.0
                idx = 0
                for _ in range(n_frames):
                    s = 0.0
                    for _c in range(channels):
                        s += float(a[idx])
                        idx += 1
                    m = s / float(channels)
                    ss += m * m
                rms.append(math.sqrt(ss / float(n_frames)) / 32768.0)

    # Onset envelope (half-wave rectified energy derivative)
    if len(rms) < 8:
        return 120.0

    onset: list[float] = [0.0]
    for i in range(1, len(rms)):
        d = rms[i] - rms[i - 1]
        onset.append(d if d > 0 else 0.0)

    # Autocorrelation over plausible tempos.
    min_bpm = 60.0
    max_bpm = 180.0

    # Lag in frames (each RMS entry is one hop)
    min_lag = max(1, int((60.0 / max_bpm) * float(sr) / float(hop)))
    max_lag = max(min_lag + 1, int((60.0 / min_bpm) * float(sr) / float(hop)))
    max_lag = min(max_lag, len(onset) - 1)

    best_lag = min_lag
    best_score = -1.0

    # Light smoothing by down-weighting very small values.
    eps = 1e-9
    for lag in range(min_lag, max_lag + 1):
        s = 0.0
        for i in range(lag, len(onset)):
            a = onset[i]
            b = onset[i - lag]
            if a > eps and b > eps:
                s += a * b
        if s > best_score:
            best_score = s
            best_lag = lag

    bpm = 60.0 * float(sr) / (float(hop) * float(best_lag))
    if not (min_bpm <= bpm <= max_bpm):
        return 120.0
    return float(round(bpm, 3))


def _quantize(t: float, q: float = 1e-6) -> float:
    return float(round(t / q) * q)


def _generate_beats(duration_sec: float, bpm: float, *, beats_per_bar: int = 4) -> list[dict[str, Any]]:
    if bpm <= 0:
        bpm = 120.0
    period = 60.0 / bpm
    beats: list[dict[str, Any]] = []
    bar = 0
    beat_in_bar = 0
    t = 0.0
    # Include beat at t=0.
    while t <= duration_sec + 1e-9:
        strength = 1.0 if beat_in_bar == 0 else 0.5
        beats.append(
            {
                "t": _quantize(t),
                "bar": bar,
                "beat": beat_in_bar,
                "strength": strength,
            }
        )

        beat_in_bar += 1
        if beat_in_bar >= beats_per_bar:
            beat_in_bar = 0
            bar += 1
        t += period

    return beats


def _generate_sections(duration_sec: float, bpm: float, *, bars_per_section: int = 8) -> list[dict[str, Any]]:
    if bpm <= 0:
        bpm = 120.0

    sec_per_bar = (60.0 / bpm) * 4.0
    sec_per_section = sec_per_bar * float(bars_per_section)
    if sec_per_section <= 0:
        sec_per_section = 8.0

    sections: list[dict[str, Any]] = []
    t0 = 0.0
    idx = 0
    while t0 < duration_sec - 1e-9:
        t1 = min(duration_sec, t0 + sec_per_section)
        sections.append({"t0": _quantize(t0), "t1": _quantize(t1), "label": f"section_{idx}"})
        t0 = t1
        idx += 1

    if not sections:
        sections.append({"t0": 0.0, "t1": _quantize(duration_sec), "label": "section_0"})
    return sections


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _stable_song_id(source_sha256: str, profile: str) -> str:
    # Stable id for identical source+profile for now.
    h = hashlib.sha256()
    h.update((source_sha256 + "|" + profile + "|" + PIPELINE_VERSION).encode("utf-8"))
    return h.hexdigest()[:32]


def _mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def cmd_stages(_args: argparse.Namespace) -> int:
    # Keep output stable + simple.
    for st in STAGES:
        print(json.dumps({"id": st.id, "version": st.version, "outputs": st.outputs}, sort_keys=True))
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    root = Path(args.songpack_dir)
    manifest = root / "manifest.json"
    if not manifest.exists():
        print(json.dumps({"ok": False, "error": "missing manifest.json"}, sort_keys=True))
        return 1

    data = json.loads(manifest.read_text("utf-8"))
    print(json.dumps({"ok": True, "manifest": data}, sort_keys=True))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    root = Path(args.songpack_dir)
    required = [
        "manifest.json",
        "audio/mix.wav",
        "features/beats.json",
        "features/tempo_map.json",
        "features/sections.json",
        "charts/easy.json",
    ]

    missing = [p for p in required if not (root / p).exists()]
    if missing:
        print(json.dumps({"ok": False, "missing": missing}, sort_keys=True))
        return 1

    # Minimal semantic checks (deterministic and fast)
    try:
        manifest = json.loads((root / "manifest.json").read_text("utf-8"))
        duration = float(manifest.get("duration_sec", 0.0) or 0.0)

        beats = json.loads((root / "features/beats.json").read_text("utf-8"))
        beat_items = beats.get("beats", [])
        last_t = -1.0
        for b in beat_items:
            t = float(b.get("t", -1.0))
            if t < 0 or t < last_t:
                raise ValueError("beats are not monotonically increasing")
            if duration > 0 and t > duration + 1e-3:
                raise ValueError("beat time exceeds duration")
            last_t = t

        sections = json.loads((root / "features/sections.json").read_text("utf-8"))
        for s in sections.get("sections", []):
            t0 = float(s.get("t0", -1.0))
            t1 = float(s.get("t1", -1.0))
            if t0 < 0 or t1 < 0 or t1 < t0:
                raise ValueError("invalid section")
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, sort_keys=True))
        return 1

    print(json.dumps({"ok": True}, sort_keys=True))
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    src = Path(args.input_audio_path)
    out = Path(args.out)
    profile = args.profile
    config = _parse_config_arg(args.config)

    if not src.exists():
        log(f"input does not exist: {src}")
        return 2

    # Stage 0: init_songpack
    emit(ProgressEvent(type="stage_start", id="init_songpack", progress=0.0))
    _mkdir(out)
    _mkdir(out / "audio")
    _mkdir(out / "features")
    _mkdir(out / "charts")
    _mkdir(out / "meta")

    source_sha = _sha256_file(src)
    song_id = _stable_song_id(source_sha, profile)

    # MVP timing metadata is minimal.
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "song_id": song_id,
        "title": args.title or src.stem,
        "artist": args.artist or "",
        "duration_sec": 0.0,
        "source": {
            "original_filename": src.name,
            "original_sha256": source_sha,
            "ingest_timestamp": config.get(
                "ingest_timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            ),
        },
        "timing": {
            "audio_sample_rate_hz": None,
            "audio_start_offset_sec": 0.0,
            "timebase": "audio",
        },
        "pipeline": {
            "pipeline_id": PIPELINE_ID,
            "pipeline_version": PIPELINE_VERSION,
            "profile": profile,
            "stage_fingerprints": {st.id: st.version for st in STAGES},
        },
        "assets": {"audio": {"mix_path": "audio/mix.wav"}},
    }

    _write_json(out / "manifest.json", manifest)
    emit(ProgressEvent(type="stage_done", id="init_songpack", progress=0.1, artifact="manifest.json"))

    # Stage 1: decode_audio
    emit(ProgressEvent(type="stage_start", id="decode_audio", progress=0.1))
    dst_wav = out / "audio" / "mix.wav"
    try:
        emit(ProgressEvent(type="stage_progress", id="decode_audio", progress=0.15, message="decoding to PCM wav"))
        duration_sec, sr = _decode_to_wav(src, dst_wav)
    except Exception as e:
        log(str(e))
        emit(ProgressEvent(type="stage_done", id="decode_audio", progress=0.3, message="failed", artifact=None))
        return 3

    manifest["duration_sec"] = float(round(duration_sec, 6))
    manifest["timing"]["audio_sample_rate_hz"] = int(sr)
    manifest["assets"]["audio"]["mix_path"] = "audio/mix.wav"
    _write_json(out / "manifest.json", manifest)
    emit(ProgressEvent(type="stage_done", id="decode_audio", progress=0.3, artifact="audio/mix.wav"))

    # Stage 2: beats_tempo (simple analysis)
    emit(ProgressEvent(type="stage_start", id="beats_tempo", progress=0.3))
    bpm_hint = float(config.get("bpm_hint")) if "bpm_hint" in config else None
    if bpm_hint is not None and bpm_hint > 0:
        bpm = float(round(bpm_hint, 3))
    else:
        emit(ProgressEvent(type="stage_progress", id="beats_tempo", progress=0.35, message="estimating bpm"))
        bpm = _estimate_bpm_from_wav(dst_wav)

    beats = {"beats_version": "1.0.0", "beats": _generate_beats(duration_sec, bpm)}
    tempo = {"tempo_version": "1.0.0", "segments": [{"t0": 0.0, "bpm": bpm, "time_signature": "4/4"}]}
    _write_json(out / "features" / "beats.json", beats)
    _write_json(out / "features" / "tempo_map.json", tempo)
    emit(ProgressEvent(type="stage_done", id="beats_tempo", progress=0.55, artifact="features/beats.json"))

    # Stage 3: sections
    emit(ProgressEvent(type="stage_start", id="sections", progress=0.55))
    emit(ProgressEvent(type="stage_progress", id="sections", progress=0.6, message="segmenting"))
    sections = {"sections_version": "1.0.0", "sections": _generate_sections(duration_sec, bpm)}
    _write_json(out / "features" / "sections.json", sections)
    emit(ProgressEvent(type="stage_done", id="sections", progress=0.75, artifact="features/sections.json"))

    # Stage 4: chart_generation
    emit(ProgressEvent(type="stage_start", id="chart_generation", progress=0.75))
    # MVP chart: one target per beat.
    targets = [{"t": b["t"], "lane": "beat"} for b in beats["beats"]]
    chart = {"chart_version": "1.0.0", "mode": "beats_only", "difficulty": "easy", "targets": targets}
    _write_json(out / "charts" / "easy.json", chart)
    emit(ProgressEvent(type="stage_done", id="chart_generation", progress=1.0, artifact="charts/easy.json"))

    # Optional override (generally not needed once decode stage computes duration)
    if args.duration_sec is not None:
        manifest["duration_sec"] = float(args.duration_sec)
        _write_json(out / "manifest.json", manifest)

    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aural_ingest")
    sub = p.add_subparsers(dest="cmd", required=True)

    s_stages = sub.add_parser("stages")
    s_stages.set_defaults(func=cmd_stages)

    s_validate = sub.add_parser("validate")
    s_validate.add_argument("songpack_dir")
    s_validate.set_defaults(func=cmd_validate)

    s_info = sub.add_parser("info")
    s_info.add_argument("songpack_dir")
    s_info.set_defaults(func=cmd_info)

    s_import = sub.add_parser("import")
    s_import.add_argument("input_audio_path")
    s_import.add_argument("--out", required=True)
    s_import.add_argument("--profile", default="full")
    s_import.add_argument("--config")
    s_import.add_argument("--title")
    s_import.add_argument("--artist")
    s_import.add_argument("--duration-sec", type=float, dest="duration_sec")
    s_import.set_defaults(func=cmd_import)

    return p


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
