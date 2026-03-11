from __future__ import annotations

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
    frame_mean_abs_series,
    frame_to_time,
    normalize_series,
    onset_novelty,
    preprocess_audio,
    timbral_features,
)
from aural_ingest.transcription import DrumEvent


class AuralOnsetAlgorithm(TranscriptionAlgorithm):
    name = "aural_onset"

    def transcribe(self, stem_path: Path) -> list[DrumEvent]:
        candidates = detect_candidates(stem_path)
        if candidates:
            events = candidates_to_events(candidates, stem_path=stem_path)
            if events:
                return events

        return fallback_events_from_classes(
            stem_path,
            ["kick", "snare", "hh_closed", "hh_open", "crash"],
            step_sec=0.095,
            velocity_base=83,
        )


def detect_candidates(stem_path: Path) -> list[DrumCandidate]:
    samples, sr = preprocess_audio(
        stem_path,
        target_sr=44_100,
        pre_emphasis_coeff=0.96,
        high_pass_hz=35.0,
    )
    if not samples or sr <= 0:
        return []

    hop = 320
    hop_sec = hop / float(sr)
    frame = 640

    time_env = normalize_series(frame_mean_abs_series(samples, frame, hop))
    spectral = compute_band_envelopes(
        samples,
        sr,
        {
            "low": (35.0, 180.0),
            "mid": (180.0, 2500.0),
            "high": (2500.0, 12_000.0),
        },
        hop_size=hop,
        frame_size=1024,
    )
    if not time_env or not spectral:
        return []

    low_n = normalize_series(onset_novelty(spectral["low"]))
    mid_n = normalize_series(onset_novelty(spectral["mid"]))
    high_n = normalize_series(onset_novelty(spectral["high"]))
    time_n = normalize_series(onset_novelty(time_env))

    n = min(len(time_n), len(low_n), len(mid_n), len(high_n))
    if n < 3:
        return []

    blended = [
        (0.45 * time_n[i]) + (0.2 * low_n[i]) + (0.2 * mid_n[i]) + (0.15 * high_n[i])
        for i in range(n)
    ]
    blended = normalize_series(blended)

    peaks = adaptive_peak_pick(
        blended,
        hop_sec=hop_sec,
        k=1.9,
        min_gap_sec=0.048,
        window_sec=0.33,
        percentile=0.78,
    )
    if not peaks:
        return []

    candidates: list[DrumCandidate] = []
    for idx, strength in peaks:
        t = frame_to_time(idx, hop, sr)
        feat = timbral_features(samples, sr, t)

        low = feat["low"]
        mid = feat["mid"]
        crack = feat["snare_crack"]
        high = feat["high"]

        if low >= max(mid, high) * 1.08 and feat["sharpness"] >= 1.8:
            drum_class = "kick"
            winner = low
        elif crack >= low * 0.72 and feat["zcr"] >= 0.11:
            drum_class = "snare"
            winner = crack
        elif high >= max(low, mid) * 0.92:
            if feat["high_decay"] <= 0.3:
                drum_class = "hh_closed"
            elif feat["high_decay"] <= 0.55:
                drum_class = "hh_open"
            elif feat["centroid"] > 9000.0:
                drum_class = "crash"
            else:
                drum_class = "ride"
            winner = high
        else:
            drum_class = classify_tom(feat)
            winner = mid if mid > 0 else low

        confidence = clamp((strength * 0.65) + (winner * 0.35), 0.0, 1.0)
        candidates.append(
            DrumCandidate(
                time=t,
                drum_class=drum_class,
                strength=float(strength),
                confidence=confidence,
                source="aural_onset",
            )
        )

    return candidates


ALGORITHM = AuralOnsetAlgorithm()


def transcribe(stem_path: Path) -> list[DrumEvent]:
    return ALGORITHM.transcribe(stem_path)
