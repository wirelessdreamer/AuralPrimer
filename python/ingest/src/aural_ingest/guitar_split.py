from __future__ import annotations

import math
import sys
import wave
from array import array
from pathlib import Path
from typing import Any


class _OnePoleLowPass:
    def __init__(self, sample_rate_hz: int, cutoff_hz: float) -> None:
        sr = max(1.0, float(sample_rate_hz))
        fc = max(1.0, float(cutoff_hz))
        self._alpha = math.exp((-2.0 * math.pi * fc) / sr)
        self._state = 0.0

    def process(self, x: float) -> float:
        self._state = (1.0 - self._alpha) * x + self._alpha * self._state
        return self._state


def _clamp(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _to_pcm16_sample(x: float) -> int:
    y = int(round(x * 32767.0))
    if y > 32767:
        return 32767
    if y < -32768:
        return -32768
    return y


def split_lead_rhythm_guitar_stem(
    source_wav: Path,
    lead_out_wav: Path,
    rhythm_out_wav: Path,
) -> dict[str, Any]:
    """Split a guitar source into lead/rhythm stems via deterministic spectral masking.

    This is a lightweight fallback splitter for environments where dedicated lead/rhythm
    sources are unavailable. It is intentionally deterministic and dependency-free.
    """

    source = Path(source_wav)
    if not source.is_file():
        raise RuntimeError(f"guitar split source does not exist: {source}")

    lead_out_wav.parent.mkdir(parents=True, exist_ok=True)
    rhythm_out_wav.parent.mkdir(parents=True, exist_ok=True)

    with wave.open(str(source), "rb") as src_w:
        channels = src_w.getnchannels()
        sample_rate_hz = src_w.getframerate()
        sample_width = src_w.getsampwidth()

        if channels <= 0:
            raise RuntimeError(f"invalid channel count in wav: {channels}")
        if sample_width != 2:
            raise RuntimeError(f"unsupported wav sample width for split: {sample_width} (expected PCM16)")

        with wave.open(str(lead_out_wav), "wb") as lead_w, wave.open(str(rhythm_out_wav), "wb") as rhythm_w:
            lead_w.setnchannels(channels)
            lead_w.setsampwidth(sample_width)
            lead_w.setframerate(sample_rate_hz)
            rhythm_w.setnchannels(channels)
            rhythm_w.setsampwidth(sample_width)
            rhythm_w.setframerate(sample_rate_hz)

            lp_180 = _OnePoleLowPass(sample_rate_hz, 180.0)
            lp_800 = _OnePoleLowPass(sample_rate_hz, 800.0)
            lp_1200 = _OnePoleLowPass(sample_rate_hz, 1200.0)
            lp_4500 = _OnePoleLowPass(sample_rate_hz, 4500.0)
            lp_2500 = _OnePoleLowPass(sample_rate_hz, 2500.0)

            atk = 1.0 - math.exp(-1.0 / max(1.0, 0.008 * float(sample_rate_hz)))
            rel = 1.0 - math.exp(-1.0 / max(1.0, 0.065 * float(sample_rate_hz)))
            mask_prev = 0.5

            total_frames = 0
            sum_mask = 0.0
            chunk_frames = 4096

            while True:
                raw = src_w.readframes(chunk_frames)
                if not raw:
                    break

                samples = array("h")
                samples.frombytes(raw)
                if sys.byteorder != "little":
                    samples.byteswap()

                n_frames = len(samples) // channels
                if n_frames <= 0:
                    continue

                out_lead = array("h")
                out_rhythm = array("h")

                idx = 0
                for _ in range(n_frames):
                    mono = 0.0
                    for c in range(channels):
                        mono += float(samples[idx + c]) / 32768.0
                    mono /= float(channels)

                    low = lp_180.process(mono)
                    hp_180 = mono - low
                    low_mid = lp_800.process(hp_180)
                    hp_1200 = mono - lp_1200.process(mono)
                    high_mid = lp_4500.process(hp_1200)
                    transient = mono - lp_2500.process(mono)

                    lead_metric = abs(high_mid) + 0.45 * abs(transient) + 0.02
                    rhythm_metric = abs(low_mid) + 0.35 * abs(low) + 0.02

                    raw_mask = lead_metric / (lead_metric + rhythm_metric + 1e-12)
                    k = atk if raw_mask > mask_prev else rel
                    mask_prev += k * (raw_mask - mask_prev)
                    mask = _clamp(mask_prev, 0.08, 0.92)

                    for c in range(channels):
                        x = float(samples[idx + c]) / 32768.0
                        lead = _to_pcm16_sample(x * mask)
                        rhythm = _to_pcm16_sample(x * (1.0 - mask))
                        out_lead.append(lead)
                        out_rhythm.append(rhythm)

                    sum_mask += mask
                    total_frames += 1
                    idx += channels

                if sys.byteorder != "little":
                    out_lead.byteswap()
                    out_rhythm.byteswap()

                lead_w.writeframesraw(out_lead.tobytes())
                rhythm_w.writeframesraw(out_rhythm.tobytes())

    if total_frames <= 0:
        raise RuntimeError("guitar split source had no audio frames")

    return {
        "method": "spectral_energy_mask_v1",
        "sample_rate_hz": int(sample_rate_hz),
        "channels": int(channels),
        "frames": int(total_frames),
        "mean_lead_ratio": round(sum_mask / float(total_frames), 6),
        "source_path": str(source),
    }
