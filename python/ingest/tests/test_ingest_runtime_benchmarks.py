from __future__ import annotations

import json
import math
import os
from pathlib import Path
import statistics
import struct
import tracemalloc
import wave

import pytest


pytestmark = pytest.mark.runtime_benchmark


def _benchmark(request: pytest.FixtureRequest, pytestconfig: pytest.Config):
    if os.environ.get("AURAL_RUN_RUNTIME_BENCHMARKS") != "1":
        pytest.skip("runtime benchmarks are opt-in; use npm run bench:python")
    if not pytestconfig.pluginmanager.hasplugin("benchmark"):
        pytest.skip("pytest-benchmark is not installed")
    return request.getfixturevalue("benchmark")


def _write_sine_wav(path: Path, *, seconds: float = 1.0, sample_rate: int = 16_000) -> None:
    frames = bytearray()
    for idx in range(int(seconds * sample_rate)):
        value = int(0.35 * 32767 * math.sin(2.0 * math.pi * 220.0 * idx / sample_rate))
        frames.extend(struct.pack("<h", value))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(frames))


def _decode_pcm(path: Path) -> list[float]:
    with wave.open(str(path), "rb") as handle:
        raw = handle.readframes(handle.getnframes())
    values = struct.unpack(f"<{len(raw) // 2}h", raw)
    return [sample / 32768.0 for sample in values]


def _estimate_energy_beats(samples: list[float], *, sample_rate: int = 16_000) -> list[float]:
    hop = 512
    energies = [
        sum(sample * sample for sample in samples[idx : idx + hop]) / hop
        for idx in range(0, max(0, len(samples) - hop), hop)
    ]
    if not energies:
        return []
    threshold = statistics.fmean(energies) * 1.05
    return [idx * hop / sample_rate for idx, energy in enumerate(energies) if energy >= threshold]


def _segment_sections(beats: list[float]) -> list[tuple[float, float]]:
    if not beats:
        return []
    sections: list[tuple[float, float]] = []
    current = beats[0]
    for idx in range(4, len(beats), 4):
        sections.append((current, beats[idx]))
        current = beats[idx]
    sections.append((current, beats[-1]))
    return sections


def _decode_drum_events(samples: list[float], *, sample_rate: int = 16_000) -> list[dict[str, float | int]]:
    hop = 256
    events = []
    last = 0.0
    for idx in range(hop, len(samples), hop):
        window = samples[idx - hop : idx]
        energy = sum(abs(sample) for sample in window) / hop
        if energy > 0.18 and idx / sample_rate - last > 0.08:
            events.append({"time": idx / sample_rate, "note": 36 if len(events) % 2 == 0 else 38})
            last = idx / sample_rate
    return events


def _decode_melodic_notes(samples: list[float], *, sample_rate: int = 16_000) -> list[dict[str, float | int]]:
    frame = 1024
    notes = []
    for idx in range(0, max(0, len(samples) - frame), frame):
        window = samples[idx : idx + frame]
        rms = math.sqrt(sum(sample * sample for sample in window) / frame)
        if rms < 0.05:
            continue
        pitch = 60 + (idx // frame) % 12
        notes.append({"t_on": idx / sample_rate, "t_off": (idx + frame) / sample_rate, "pitch": pitch})
    return notes


def test_decode_stage_runtime(tmp_path: Path, request: pytest.FixtureRequest, pytestconfig: pytest.Config) -> None:
    wav_path = tmp_path / "fixture.wav"
    _write_sine_wav(wav_path)
    _benchmark(request, pytestconfig)(lambda: _decode_pcm(wav_path))


def test_beats_and_sections_runtime(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    pytestconfig: pytest.Config,
) -> None:
    wav_path = tmp_path / "fixture.wav"
    _write_sine_wav(wav_path)
    samples = _decode_pcm(wav_path)

    def run() -> list[tuple[float, float]]:
        return _segment_sections(_estimate_energy_beats(samples))

    _benchmark(request, pytestconfig)(run)


def test_chart_decode_runtime(tmp_path: Path, request: pytest.FixtureRequest, pytestconfig: pytest.Config) -> None:
    wav_path = tmp_path / "fixture.wav"
    _write_sine_wav(wav_path)
    samples = _decode_pcm(wav_path)

    def run() -> dict[str, int]:
        return {
            "drums": len(_decode_drum_events(samples)),
            "melodic": len(_decode_melodic_notes(samples)),
        }

    _benchmark(request, pytestconfig)(run)


def test_memory_footprint_smoke(tmp_path: Path) -> None:
    if os.environ.get("AURAL_RUN_RUNTIME_BENCHMARKS") != "1":
        pytest.skip("runtime benchmarks are opt-in; use npm run bench:python")

    wav_path = tmp_path / "fixture.wav"
    _write_sine_wav(wav_path, seconds=2.0)
    tracemalloc.start()
    samples = _decode_pcm(wav_path)
    payload = {
        "decoded_samples": len(samples),
        "drum_events": len(_decode_drum_events(samples)),
        "melodic_notes": len(_decode_melodic_notes(samples)),
    }
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    payload["current_bytes"] = current
    payload["peak_bytes"] = peak

    out = os.environ.get("AURAL_PY_BENCH_MEMORY_JSON")
    if out:
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    assert payload["peak_bytes"] < 64 * 1024 * 1024
