from __future__ import annotations

import math
from pathlib import Path

from aural_ingest.algorithms._common import (
    DrumCandidate,
    TranscriptionAlgorithm,
    adaptive_peak_pick,
    candidates_to_events,
    classify_tom,
    clamp,
    compute_band_envelopes,
    fallback_events_from_classes,
    frame_to_time,
    normalize_series,
    preprocess_audio,
    timbral_features,
)
from aural_ingest.transcription import DrumEvent


class LibrosaSuperfluxAlgorithm(TranscriptionAlgorithm):
    name = "librosa_superflux"

    def transcribe(self, stem_path: Path) -> list[DrumEvent]:
        candidates = detect_candidates(stem_path)
        if candidates:
            events = candidates_to_events(candidates, stem_path=stem_path)
            if events:
                return events

        return fallback_events_from_classes(
            stem_path,
            ["kick", "snare", "hh_closed", "crash"],
            step_sec=0.105,
            velocity_base=80,
        )


def _superflux_envelope(log_bands: list[list[float]], lag: int, max_size: int) -> list[float]:
    if not log_bands:
        return []
    n = min((len(b) for b in log_bands), default=0)
    if n <= lag:
        return []

    out = [0.0 for _ in range(n)]
    num_bands = len(log_bands)

    for t in range(lag, n):
        acc = 0.0
        for b in range(num_bands):
            lo = max(0, b - max_size // 2)
            hi = min(num_bands, b + max_size // 2 + 1)
            pooled = max(log_bands[k][t] for k in range(lo, hi))
            ref = log_bands[b][t - lag]
            diff = pooled - ref
            if diff > 0.0:
                acc += diff
        out[t] = acc

    return out


def detect_candidates(stem_path: Path) -> list[DrumCandidate]:
    samples, sr = preprocess_audio(
        stem_path,
        target_sr=22_050,
        pre_emphasis_coeff=0.96,
        high_pass_hz=32.0,
    )
    if not samples or sr <= 0:
        return []

    hop = 256
    hop_sec = hop / float(sr)
    bands = compute_band_envelopes(
        samples,
        sr,
        {
            "b0": (40.0, 120.0),
            "b1": (120.0, 280.0),
            "b2": (280.0, 800.0),
            "b3": (800.0, 2000.0),
            "b4": (2000.0, 4000.0),
            "b5": (4000.0, 7000.0),
            "b6": (7000.0, 12_000.0),
        },
        hop_size=hop,
        frame_size=1024,
    )
    if not bands:
        return []

    ordered = [bands[f"b{i}"] for i in range(7)]
    n = min((len(b) for b in ordered), default=0)
    if n < 4:
        return []

    ordered = [b[:n] for b in ordered]
    log_bands = [[math.log1p(max(0.0, v) * 40.0) for v in b] for b in ordered]
    onset_env = normalize_series(_superflux_envelope(log_bands, lag=2, max_size=3))
    if len(onset_env) < 3:
        return []

    peaks = adaptive_peak_pick(
        onset_env,
        hop_sec=hop_sec,
        k=2.4,
        min_gap_sec=0.07,
        window_sec=0.45,
        percentile=0.9,
    )
    if not peaks:
        return []

    candidates: list[DrumCandidate] = []
    for idx, strength in peaks:
        t = frame_to_time(idx, hop, sr)
        feat = timbral_features(samples, sr, t)

        low = ordered[0][idx] + ordered[1][idx]
        mid = ordered[2][idx] + ordered[3][idx] + ordered[4][idx]
        high = ordered[5][idx] + ordered[6][idx]

        if low >= max(mid, high) * 1.08:
            drum_class = "kick"
            winner = low
        elif (ordered[4][idx] + feat["snare_crack"]) >= low * 0.85 and mid >= high * 0.65:
            drum_class = "snare"
            winner = mid
        elif high >= max(low, mid) * 0.92:
            if feat["high_decay"] > 0.6:
                drum_class = "crash"
            elif feat["high_decay"] > 0.34:
                drum_class = "hh_open"
            else:
                drum_class = "hh_closed"
            winner = high
        else:
            drum_class = classify_tom(feat)
            winner = mid if mid > 0.0 else low

        confidence = clamp((strength * 0.66) + (winner * 0.34), 0.0, 1.0)
        candidates.append(
            DrumCandidate(
                time=t,
                drum_class=drum_class,
                strength=float(strength),
                confidence=confidence,
                source="librosa_superflux",
            )
        )

    return candidates


ALGORITHM = LibrosaSuperfluxAlgorithm()


def transcribe(stem_path: Path) -> list[DrumEvent]:
    return ALGORITHM.transcribe(stem_path)
