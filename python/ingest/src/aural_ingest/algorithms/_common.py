from __future__ import annotations

import array
import audioop
from dataclasses import dataclass
import math
from pathlib import Path
import statistics
import sys
from typing import Iterable, Mapping, Protocol
import wave

from aural_ingest.transcription import DrumEvent, MelodicNote


DRUM_CLASS_TO_MIDI: dict[str, int] = {
    "kick": 36,
    "snare": 38,
    "hh_closed": 42,
    "hh_open": 46,
    "crash": 49,
    "ride": 51,
    "tom_high": 50,
    "tom_low": 47,
    "tom_floor": 41,
}

CORE_DRUM_CLASSES: set[str] = {"kick", "snare", "hh_closed"}

CLASS_REFRACTORY_SEC: dict[str, float] = {
    "kick": 0.1,
    "snare": 0.095,
    "tom_high": 0.09,
    "tom_low": 0.095,
    "tom_floor": 0.1,
    "hh_closed": 0.055,
    "hh_open": 0.06,
    "crash": 0.065,
    "ride": 0.06,
}

CLASS_DURATION_SEC: dict[str, float] = {
    "kick": 0.05,
    "snare": 0.055,
    "hh_closed": 0.04,
    "hh_open": 0.085,
    "crash": 0.12,
    "ride": 0.095,
    "tom_high": 0.075,
    "tom_low": 0.08,
    "tom_floor": 0.085,
}


@dataclass(frozen=True)
class DrumCandidate:
    time: float
    drum_class: str
    strength: float
    confidence: float
    source: str


class TranscriptionAlgorithm(Protocol):
    name: str

    def transcribe(self, stem_path: Path) -> list[DrumEvent]:
        ...


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def estimate_duration_sec(stem_path: Path) -> float:
    if stem_path.suffix.lower() == ".wav":
        try:
            with wave.open(str(stem_path), "rb") as w:
                sr = w.getframerate()
                frames = w.getnframes()
                if sr > 0:
                    return max(0.25, float(frames) / float(sr))
        except Exception:
            pass

    try:
        size = stem_path.stat().st_size
    except Exception:
        size = 0
    return 1.0 + float(size % 40_000) / 10_000.0


def read_wav_mono_normalized(stem_path: Path) -> tuple[list[float], int]:
    if stem_path.suffix.lower() != ".wav" or not stem_path.is_file():
        return [], 0

    try:
        with wave.open(str(stem_path), "rb") as w:
            channels = int(w.getnchannels())
            sampwidth = int(w.getsampwidth())
            sr = int(w.getframerate())
            nframes = int(w.getnframes())

            if sr <= 0 or channels <= 0 or sampwidth <= 0 or nframes <= 0:
                return [], 0

            raw = w.readframes(nframes)
            if channels > 1:
                raw = audioop.tomono(raw, sampwidth, 0.5, 0.5)

            if sampwidth != 2:
                raw = audioop.lin2lin(raw, sampwidth, 2)

            pcm = array.array("h")
            pcm.frombytes(raw)
            if sys.byteorder != "little":
                pcm.byteswap()

            samples = [float(v) / 32768.0 for v in pcm]
            return normalize_peak(samples), sr
    except Exception:
        return [], 0


def build_pattern_events(
    stem_path: Path,
    notes: list[int],
    *,
    step_sec: float,
    velocity_base: int,
) -> list[DrumEvent]:
    duration = estimate_duration_sec(stem_path)
    out: list[DrumEvent] = []

    if step_sec <= 0:
        step_sec = 0.1

    t = 0.0
    idx = 0
    while t < duration:
        note = notes[idx % len(notes)]
        vel = max(25, min(127, velocity_base + ((idx % 7) * 3)))
        out.append(DrumEvent(time=round(t, 6), note=note, velocity=vel))
        t += step_sec
        idx += 1

    return out


def normalize_peak(samples: list[float]) -> list[float]:
    if not samples:
        return []
    peak = max((abs(x) for x in samples), default=0.0)
    if peak <= 1e-12:
        return samples[:]
    scale = 1.0 / max(1.0, peak)
    if abs(scale - 1.0) <= 1e-9:
        return samples[:]
    return [x * scale for x in samples]


def resample_linear(samples: list[float], src_sr: int, dst_sr: int) -> list[float]:
    if not samples or src_sr <= 0 or dst_sr <= 0:
        return []
    if src_sr == dst_sr:
        return samples[:]

    out_len = max(1, int(round(len(samples) * (float(dst_sr) / float(src_sr)))))
    last = len(samples) - 1
    out = [0.0 for _ in range(out_len)]
    ratio = float(src_sr) / float(dst_sr)

    for i in range(out_len):
        pos = i * ratio
        idx = int(pos)
        frac = pos - float(idx)
        a = samples[min(last, idx)]
        b = samples[min(last, idx + 1)]
        out[i] = a + ((b - a) * frac)

    return out


def apply_pre_emphasis(samples: list[float], coeff: float) -> list[float]:
    if not samples:
        return []
    if coeff <= 0.0:
        return samples[:]

    out = [samples[0]]
    prev = samples[0]
    for x in samples[1:]:
        out.append(x - (coeff * prev))
        prev = x
    return out


def low_pass_one_pole(samples: list[float], sr: int, cutoff_hz: float) -> list[float]:
    if not samples or sr <= 0 or cutoff_hz <= 0.0:
        return [] if not samples else samples[:]

    dt = 1.0 / float(sr)
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    alpha = clamp(dt / (rc + dt), 0.0001, 0.9999)

    out = [0.0 for _ in samples]
    y = 0.0
    for i, x in enumerate(samples):
        y += alpha * (x - y)
        out[i] = y
    return out


def high_pass_one_pole(samples: list[float], sr: int, cutoff_hz: float) -> list[float]:
    if not samples or sr <= 0 or cutoff_hz <= 0.0:
        return [] if not samples else samples[:]

    dt = 1.0 / float(sr)
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    alpha = clamp(dt / (rc + dt), 0.0001, 0.9999)

    out = [0.0 for _ in samples]
    lp = 0.0
    for i, x in enumerate(samples):
        lp += alpha * (x - lp)
        out[i] = x - lp
    return out


def band_pass_one_pole(samples: list[float], sr: int, low_hz: float, high_hz: float) -> list[float]:
    if not samples or sr <= 0:
        return []
    hi = max(low_hz + 1.0, high_hz)
    lo = max(1.0, min(low_hz, hi - 1.0))
    return high_pass_one_pole(low_pass_one_pole(samples, sr, hi), sr, lo)


def frame_rms_series(samples: list[float], frame_size: int, hop_size: int) -> list[float]:
    out: list[float] = []
    if not samples or frame_size <= 0 or hop_size <= 0:
        return out
    n = len(samples)
    if n < frame_size:
        return out

    i = 0
    while i + frame_size <= n:
        seg = samples[i : i + frame_size]
        out.append(math.sqrt(sum(x * x for x in seg) / float(frame_size)))
        i += hop_size
    return out


def frame_mean_abs_series(samples: list[float], frame_size: int, hop_size: int) -> list[float]:
    out: list[float] = []
    if not samples or frame_size <= 0 or hop_size <= 0:
        return out
    n = len(samples)
    if n < frame_size:
        return out

    i = 0
    while i + frame_size <= n:
        seg = samples[i : i + frame_size]
        out.append(sum(abs(x) for x in seg) / float(frame_size))
        i += hop_size
    return out


def smooth_series(values: list[float], radius: int) -> list[float]:
    if not values:
        return []
    if radius <= 0:
        return values[:]

    out = [0.0 for _ in values]
    n = len(values)
    for i in range(n):
        a = max(0, i - radius)
        b = min(n, i + radius + 1)
        win = values[a:b]
        out[i] = sum(win) / float(len(win))
    return out


def normalize_series(values: list[float]) -> list[float]:
    if not values:
        return []
    peak = max(values)
    if peak <= 1e-12:
        return values[:]
    return [v / peak for v in values]


def preprocess_audio(
    stem_path: Path,
    *,
    target_sr: int,
    pre_emphasis_coeff: float = 0.0,
    high_pass_hz: float | None = None,
) -> tuple[list[float], int]:
    samples, sr = read_wav_mono_normalized(stem_path)
    if not samples or sr <= 0:
        return [], 0

    dst_sr = target_sr if target_sr > 0 else sr
    if sr != dst_sr:
        samples = resample_linear(samples, sr, dst_sr)
        sr = dst_sr

    samples = normalize_peak(samples)
    if pre_emphasis_coeff > 0.0:
        samples = apply_pre_emphasis(samples, pre_emphasis_coeff)

    if high_pass_hz is not None and high_pass_hz > 0.0:
        samples = high_pass_one_pole(samples, sr, high_pass_hz)

    return normalize_peak(samples), sr


def trim_series_dict(series_map: Mapping[str, list[float]]) -> dict[str, list[float]]:
    if not series_map:
        return {}
    lengths = [len(v) for v in series_map.values() if v]
    if not lengths:
        return {k: [] for k in series_map}
    n = min(lengths)
    return {k: v[:n] for k, v in series_map.items()}


def compute_band_envelopes(
    samples: list[float],
    sr: int,
    bands: Mapping[str, tuple[float, float]],
    *,
    hop_size: int,
    frame_size: int | None = None,
    smooth_radius: int = 1,
) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    if not samples or sr <= 0 or hop_size <= 0:
        return out

    frame = frame_size if frame_size is not None else hop_size * 2
    frame = max(frame, hop_size)

    for name, (lo, hi) in bands.items():
        band = band_pass_one_pole(samples, sr, lo, hi)
        env = frame_rms_series(band, frame, hop_size)
        if smooth_radius > 0:
            env = smooth_series(env, smooth_radius)
        out[name] = env

    return trim_series_dict(out)


def frame_to_time(frame_idx: int, hop_size: int, sr: int) -> float:
    if sr <= 0 or hop_size <= 0:
        return 0.0
    return (frame_idx * hop_size) / float(sr)


def time_to_frame(time_sec: float, hop_size: int, sr: int) -> int:
    if sr <= 0 or hop_size <= 0:
        return 0
    return max(0, int(round((time_sec * sr) / float(hop_size))))


def onset_novelty(env: list[float]) -> list[float]:
    if len(env) < 2:
        return [0.0 for _ in env]
    out = [0.0]
    for i in range(1, len(env)):
        d = env[i] - env[i - 1]
        out.append(d if d > 0 else 0.0)
    return out


def adaptive_peak_pick(
    env: list[float],
    *,
    hop_sec: float,
    k: float,
    min_gap_sec: float,
    window_sec: float = 0.35,
    percentile: float | None = 0.85,
    density_boost: float = 0.0,
) -> list[tuple[int, float]]:
    if len(env) < 3 or hop_sec <= 0.0:
        return []

    win = max(4, int(window_sec / hop_sec))
    min_gap = max(1, int(min_gap_sec / hop_sec))
    density_win = max(min_gap * 2, int(0.45 / hop_sec))

    out: list[tuple[int, float]] = []
    last_peak = -100_000
    for i in range(1, len(env) - 1):
        cur = env[i]
        if cur <= env[i - 1] or cur < env[i + 1]:
            continue

        start = max(0, i - win)
        local = env[start : i + 1]
        med = statistics.median(local)
        mad = statistics.median([abs(v - med) for v in local]) if local else 0.0
        thr = med + (k * max(mad, 1e-8))

        if percentile is not None and local:
            sorted_local = sorted(local)
            idx = int(clamp(percentile, 0.0, 1.0) * float(len(sorted_local) - 1))
            thr = max(thr, sorted_local[idx])

        if density_boost > 0.0 and out:
            recent = sum(1 for p, _ in out if i - p <= density_win)
            thr *= 1.0 + (density_boost * min(1.0, recent / 4.0))

        if cur < thr:
            continue
        if i - last_peak < min_gap:
            continue

        out.append((i, cur))
        last_peak = i

    if out:
        return out

    best = max(range(len(env)), key=lambda idx: env[idx])
    return [(best, env[best])] if env[best] > 0.0 else []


def estimate_tempo_from_onset_env(onset_env: list[float], hop_sec: float) -> tuple[float, float]:
    if len(onset_env) < 8 or hop_sec <= 0.0:
        return 0.5, 0.0

    min_bpm = 60.0
    max_bpm = 180.0
    min_lag = max(1, int((60.0 / max_bpm) / hop_sec))
    max_lag = min(len(onset_env) - 1, max(min_lag + 1, int((60.0 / min_bpm) / hop_sec)))
    if max_lag <= min_lag:
        return 0.5, 0.0

    best_lag = min_lag
    best_score = -1.0
    sum_scores = 0.0

    for lag in range(min_lag, max_lag + 1):
        score = 0.0
        for i in range(lag, len(onset_env)):
            a = onset_env[i]
            b = onset_env[i - lag]
            if a > 1e-9 and b > 1e-9:
                score += a * b
        sum_scores += max(0.0, score)
        if score > best_score:
            best_score = score
            best_lag = lag

    if best_score <= 0.0:
        return 0.5, 0.0

    period = best_lag * hop_sec
    confidence = best_score / max(1e-9, sum_scores)
    return period, clamp(confidence, 0.0, 1.0)


def snap_time_to_grid(time_sec: float, *, anchor: float, step: float, tolerance: float) -> float:
    if step <= 0.0:
        return time_sec
    idx = round((time_sec - anchor) / step)
    snapped = anchor + (idx * step)
    if abs(snapped - time_sec) <= tolerance:
        return snapped
    return time_sec


def _band_rms(samples: list[float], sr: int, low_hz: float, high_hz: float) -> float:
    if not samples or sr <= 0:
        return 0.0
    band = band_pass_one_pole(samples, sr, low_hz, high_hz)
    if not band:
        return 0.0
    return math.sqrt(sum(x * x for x in band) / float(len(band)))


def _window_at(samples: list[float], sr: int, time_sec: float, *, length_sec: float) -> list[float]:
    if not samples or sr <= 0:
        return []
    start = max(0, int(time_sec * sr))
    size = max(32, int(length_sec * sr))
    end = min(len(samples), start + size)
    if end <= start:
        return []
    return samples[start:end]


def _zero_crossing_rate(window: list[float]) -> float:
    if len(window) < 2:
        return 0.0
    zc = 0
    for a, b in zip(window, window[1:]):
        if (a >= 0.0) != (b >= 0.0):
            zc += 1
    return zc / float(len(window) - 1)


def timbral_features(samples: list[float], sr: int, time_sec: float) -> dict[str, float]:
    window = _window_at(samples, sr, time_sec, length_sec=0.1)
    if not window:
        return {
            "low": 0.0,
            "sub": 0.0,
            "mid": 0.0,
            "snare_crack": 0.0,
            "high": 0.0,
            "air": 0.0,
            "zcr": 0.0,
            "peak": 0.0,
            "rms": 0.0,
            "sharpness": 0.0,
            "high_decay": 0.0,
            "centroid": 0.0,
        }

    low = _band_rms(window, sr, 35.0, 160.0)
    sub = _band_rms(window, sr, 35.0, 120.0)
    mid = _band_rms(window, sr, 160.0, 2400.0)
    crack = _band_rms(window, sr, 2000.0, 4000.0)
    high = _band_rms(window, sr, 5500.0, 12000.0)
    air = _band_rms(window, sr, 6000.0, 15000.0)

    peak = max((abs(x) for x in window), default=0.0)
    rms = math.sqrt(sum(x * x for x in window) / float(len(window)))
    zcr = _zero_crossing_rate(window)
    sharpness = peak / max(1e-6, rms)

    early_n = max(8, int(sr * 0.02))
    tail_n = max(8, int(sr * 0.05))
    early = window[:early_n]
    tail = window[early_n : early_n + tail_n]
    high_early = _band_rms(early, sr, 5500.0, 12000.0)
    high_tail = _band_rms(tail, sr, 5500.0, 12000.0)
    high_decay = high_tail / max(1e-6, high_early)

    total = max(1e-9, low + mid + crack + high + air)
    centroid = (
        (90.0 * low)
        + (900.0 * mid)
        + (3000.0 * crack)
        + (8000.0 * high)
        + (11000.0 * air)
    ) / total

    return {
        "low": low,
        "sub": sub,
        "mid": mid,
        "snare_crack": crack,
        "high": high,
        "air": air,
        "zcr": zcr,
        "peak": peak,
        "rms": rms,
        "sharpness": sharpness,
        "high_decay": high_decay,
        "centroid": centroid,
    }


def classify_tom(features: Mapping[str, float]) -> str:
    low = float(features.get("low", 0.0))
    crack = float(features.get("snare_crack", 0.0))
    high = float(features.get("high", 0.0))

    if crack > (low * 0.75):
        return "tom_high"
    if high > (low * 0.45):
        return "tom_low"
    return "tom_floor"


def classify_hat_or_cymbal(
    features: Mapping[str, float],
    *,
    prefer_ride_when_on_grid: bool = False,
    on_grid: bool = False,
) -> str:
    high_decay = float(features.get("high_decay", 0.0))
    high = float(features.get("high", 0.0))
    crack = float(features.get("snare_crack", 0.0))

    if high_decay > 0.58 and high > crack * 1.1:
        if prefer_ride_when_on_grid and on_grid and high_decay < 0.9:
            return "ride"
        return "crash"

    if high_decay > 0.34:
        return "hh_open"
    return "hh_closed"


def classify_core_from_features(features: Mapping[str, float], *, allow_expanded: bool) -> str:
    low = float(features.get("low", 0.0))
    sub = float(features.get("sub", 0.0))
    mid = float(features.get("mid", 0.0))
    crack = float(features.get("snare_crack", 0.0))
    high = float(features.get("high", 0.0))
    zcr = float(features.get("zcr", 0.0))
    sharp = float(features.get("sharpness", 0.0))

    if (sub >= mid * 1.35 and sub >= high * 1.3 and sharp >= 1.8) or (low > mid * 1.1 and zcr < 0.16):
        return "kick"

    if crack > low * 0.65 and (zcr >= 0.11 or sharp >= 2.1):
        return "snare"

    if high >= max(low, mid) * 0.92:
        return classify_hat_or_cymbal(features)

    if allow_expanded:
        return classify_tom(features)

    return "hh_closed"


def merge_candidate_clusters(candidates: list[DrumCandidate], *, window_sec: float) -> list[list[DrumCandidate]]:
    if not candidates:
        return []

    ordered = sorted(candidates, key=lambda c: (c.time, c.source, c.drum_class))
    clusters: list[list[DrumCandidate]] = []

    current: list[DrumCandidate] = [ordered[0]]
    for cand in ordered[1:]:
        center = sum(c.time for c in current) / float(len(current))
        if abs(cand.time - center) <= window_sec:
            current.append(cand)
            continue

        clusters.append(current)
        current = [cand]

    if current:
        clusters.append(current)
    return clusters


def dedup_same_class(candidates: list[DrumCandidate], *, window_sec: float) -> list[DrumCandidate]:
    if not candidates:
        return []

    by_class: dict[str, list[DrumCandidate]] = {}
    for c in candidates:
        by_class.setdefault(c.drum_class, []).append(c)

    out: list[DrumCandidate] = []
    for cls, group in by_class.items():
        ordered = sorted(group, key=lambda c: c.time)
        cluster: list[DrumCandidate] = [ordered[0]]
        for cand in ordered[1:]:
            if cand.time - cluster[-1].time <= window_sec:
                cluster.append(cand)
                continue
            out.append(max(cluster, key=lambda c: (c.strength, c.confidence)))
            cluster = [cand]
        if cluster:
            out.append(max(cluster, key=lambda c: (c.strength, c.confidence)))

    return sorted(out, key=lambda c: c.time)


def enforce_refractory(candidates: list[DrumCandidate]) -> list[DrumCandidate]:
    if not candidates:
        return []

    by_class: dict[str, list[DrumCandidate]] = {}
    for c in candidates:
        by_class.setdefault(c.drum_class, []).append(c)

    out: list[DrumCandidate] = []
    for cls, group in by_class.items():
        refractory = CLASS_REFRACTORY_SEC.get(cls, 0.08)
        ordered = sorted(group, key=lambda c: c.time)
        kept: list[DrumCandidate] = []
        for cand in ordered:
            if not kept:
                kept.append(cand)
                continue

            prev = kept[-1]
            if cand.time - prev.time >= refractory:
                kept.append(cand)
                continue

            if (cand.strength, cand.confidence) > (prev.strength, prev.confidence):
                kept[-1] = cand

        out.extend(kept)

    return sorted(out, key=lambda c: c.time)


def velocity_from_strength(norm_strength: float, drum_class: str) -> int:
    n = clamp(norm_strength, 0.0, 1.0)
    vel = int(35 + (92 * n))

    if drum_class in {"kick", "snare"}:
        vel += 8
    elif drum_class in {"hh_closed", "hh_open", "crash", "ride"}:
        vel += 4

    return int(clamp(float(vel), 25.0, 127.0))


def _candidate_activity_band(features: Mapping[str, float], drum_class: str) -> float:
    if drum_class == "kick":
        return max(float(features.get("sub", 0.0)), float(features.get("low", 0.0)))
    if drum_class == "snare":
        return max(float(features.get("mid", 0.0)), float(features.get("snare_crack", 0.0)))
    if drum_class in {"hh_closed", "hh_open", "crash", "ride"}:
        return max(float(features.get("high", 0.0)), float(features.get("air", 0.0)))
    if drum_class in {"tom_high", "tom_low", "tom_floor"}:
        return max(float(features.get("low", 0.0)), float(features.get("mid", 0.0)))
    return float(features.get("rms", 0.0))


def suppress_silent_candidates(candidates: list[DrumCandidate], stem_path: Path) -> list[DrumCandidate]:
    samples, sr = read_wav_mono_normalized(stem_path)
    if not samples or sr <= 0 or not candidates:
        return candidates

    feature_rows: list[tuple[DrumCandidate, float, float, float]] = []
    rms_values: list[float] = []
    peak_values: list[float] = []
    band_values_by_class: dict[str, list[float]] = {}

    for candidate in candidates:
        features = timbral_features(samples, sr, candidate.time)
        rms = float(features.get("rms", 0.0))
        peak = float(features.get("peak", 0.0))
        band = _candidate_activity_band(features, candidate.drum_class)
        feature_rows.append((candidate, rms, peak, band))
        rms_values.append(rms)
        peak_values.append(peak)
        band_values_by_class.setdefault(candidate.drum_class, []).append(band)

    if not feature_rows:
        return candidates

    rms_floor = max(0.0015, statistics.median(rms_values) * 0.08)
    peak_floor = max(0.012, statistics.median(peak_values) * 0.08)

    band_floor_by_class: dict[str, float] = {}
    for drum_class, values in band_values_by_class.items():
        if not values:
            continue
        band_floor_by_class[drum_class] = max(0.001, statistics.median(values) * 0.1)

    gated: list[DrumCandidate] = []
    for candidate, rms, peak, band in feature_rows:
        band_floor = band_floor_by_class.get(candidate.drum_class, 0.001)
        too_quiet = rms < rms_floor and peak < peak_floor and band < band_floor
        extremely_quiet = rms < (rms_floor * 0.55) and band < (band_floor * 0.8)
        if too_quiet or extremely_quiet:
            continue
        gated.append(candidate)

    return gated


def candidates_to_events(candidates: list[DrumCandidate], *, stem_path: Path | None = None) -> list[DrumEvent]:
    if not candidates:
        return []

    gated = suppress_silent_candidates(candidates, stem_path) if stem_path is not None else candidates
    if not gated:
        return []

    collapsed = dedup_same_class(gated, window_sec=0.03)
    refractory = enforce_refractory(collapsed)
    if not refractory:
        return []

    max_strength = max((c.strength for c in refractory), default=1.0)
    if max_strength <= 1e-9:
        max_strength = 1.0

    out: list[DrumEvent] = []
    for c in refractory:
        note = DRUM_CLASS_TO_MIDI.get(c.drum_class)
        if note is None:
            continue

        norm = clamp((c.strength / max_strength) * 0.7 + (c.confidence * 0.3), 0.0, 1.0)
        vel = velocity_from_strength(norm, c.drum_class)
        duration = CLASS_DURATION_SEC.get(c.drum_class, 0.05)
        out.append(
            DrumEvent(
                time=round(c.time, 6),
                note=int(note),
                velocity=int(vel),
                duration=round(duration, 6),
            )
        )

    return sorted(out, key=lambda e: e.time)


def fallback_events_from_classes(
    stem_path: Path,
    classes: Iterable[str],
    *,
    step_sec: float,
    velocity_base: int,
) -> list[DrumEvent]:
    notes = [DRUM_CLASS_TO_MIDI[c] for c in classes if c in DRUM_CLASS_TO_MIDI]
    if not notes:
        notes = [36, 38, 42]
    return build_pattern_events(stem_path, notes, step_sec=step_sec, velocity_base=velocity_base)


def _midi_from_freq(freq_hz: float) -> int | None:
    if not (math.isfinite(freq_hz) and freq_hz > 0.0):
        return None
    midi = int(round(69.0 + 12.0 * math.log2(freq_hz / 440.0)))
    if midi < 21 or midi > 108:
        return None
    return midi


def extract_melodic_notes_mono(
    stem_path: Path,
    *,
    frame_sec: float = 0.05,
    hop_sec: float = 0.02,
    min_note_sec: float = 0.07,
    min_freq_hz: float = 55.0,
    max_freq_hz: float = 1600.0,
) -> list[MelodicNote]:
    samples, sr = read_wav_mono_normalized(stem_path)
    if not samples or sr <= 0:
        return []

    frame = max(96, int(sr * max(0.004, frame_sec)))
    hop = max(32, int(sr * max(0.002, hop_sec)))
    if len(samples) < frame:
        return []

    frames: list[tuple[float, int | None, float]] = []
    i = 0
    while i + frame <= len(samples):
        seg = samples[i : i + frame]
        rms = math.sqrt(sum(x * x for x in seg) / float(len(seg)))

        zc = 0
        for a, b in zip(seg, seg[1:]):
            if (a >= 0.0) != (b >= 0.0):
                zc += 1
        freq = (zc * sr) / float(2 * max(1, len(seg) - 1))
        midi = None
        if min_freq_hz <= freq <= max_freq_hz:
            midi = _midi_from_freq(freq)

        t = i / float(sr)
        frames.append((t, midi, rms))
        i += hop

    if not frames:
        return []

    rms_vals = [f[2] for f in frames]
    floor = max(0.006, statistics.median(rms_vals) * 0.55)

    out: list[MelodicNote] = []
    cur_pitch: int | None = None
    cur_start = 0.0
    cur_rms = 0.0
    cur_count = 0

    def flush(t_end: float) -> None:
        nonlocal cur_pitch, cur_start, cur_rms, cur_count
        if cur_pitch is None or cur_count <= 0:
            cur_pitch = None
            cur_start = t_end
            cur_rms = 0.0
            cur_count = 0
            return
        dur = max(0.0, t_end - cur_start)
        if dur >= min_note_sec:
            mean_rms = cur_rms / float(cur_count)
            vel = int(42 + mean_rms * 85.0)
            vel = max(24, min(127, vel))
            out.append(
                MelodicNote(
                    t_on=round(cur_start, 6),
                    t_off=round(t_end, 6),
                    pitch=int(cur_pitch),
                    velocity=vel,
                )
            )
        cur_pitch = None
        cur_start = t_end
        cur_rms = 0.0
        cur_count = 0

    for t, midi, rms in frames:
        voiced = midi is not None and rms >= floor
        if not voiced:
            flush(t)
            continue

        assert midi is not None
        if cur_pitch is None:
            cur_pitch = midi
            cur_start = t
            cur_rms = rms
            cur_count = 1
            continue

        if abs(midi - cur_pitch) <= 1:
            cur_rms += rms
            cur_count += 1
            continue

        flush(t)
        cur_pitch = midi
        cur_start = t
        cur_rms = rms
        cur_count = 1

    if frames:
        flush(frames[-1][0] + (hop / float(sr)))

    return out
