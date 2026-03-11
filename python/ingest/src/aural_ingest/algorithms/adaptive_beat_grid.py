from __future__ import annotations

from pathlib import Path

from aural_ingest.algorithms._common import (
    DrumCandidate,
    TranscriptionAlgorithm,
    adaptive_peak_pick,
    candidates_to_events,
    classify_hat_or_cymbal,
    clamp,
    compute_band_envelopes,
    estimate_tempo_from_onset_env,
    fallback_events_from_classes,
    frame_to_time,
    normalize_series,
    onset_novelty,
    preprocess_audio,
    snap_time_to_grid,
    timbral_features,
)
from aural_ingest.transcription import DrumEvent


class AdaptiveBeatGridAlgorithm(TranscriptionAlgorithm):
    name = "adaptive_beat_grid"

    def transcribe(self, stem_path: Path) -> list[DrumEvent]:
        candidates = detect_candidates(stem_path)
        if candidates:
            events = candidates_to_events(candidates, stem_path=stem_path)
            if events:
                return events

        return fallback_events_from_classes(
            stem_path,
            ["kick", "snare", "hh_closed"],
            step_sec=0.11,
            velocity_base=84,
        )


def _choose_grid_step(period_sec: float, onset_count: int, duration_sec: float, confidence: float) -> float:
    if period_sec <= 0.0:
        return 0.25

    beats = max(1.0, duration_sec / period_sec)
    density = onset_count / beats

    # 1/16 grid when confident and onset density supports it, otherwise 1/8.
    if confidence >= 0.22 and density >= 1.7:
        return period_sec / 4.0
    return period_sec / 2.0


def _local_peak(values: list[float], idx: int, *, radius: int = 1) -> float:
    if not values:
        return 0.0
    start = max(0, idx - radius)
    end = min(len(values), idx + radius + 1)
    if start >= end:
        return 0.0
    return max(values[start:end])


def _merge_supplemental_peaks(
    peaks: list[tuple[int, float]],
    novelty: list[float],
    supplemental: list[tuple[int, float]],
    *,
    novelty_ratio: float,
    min_strength: float,
    min_gap_frames: int,
) -> list[tuple[int, float]]:
    merged: dict[int, float] = {idx: float(strength) for idx, strength in peaks}
    ordered = sorted(merged)

    for idx, strength in supplemental:
        if strength < min_strength:
            continue
        if idx < 0 or idx >= len(novelty):
            continue
        if novelty[idx] >= strength * novelty_ratio:
            continue
        if any(abs(idx - existing_idx) <= min_gap_frames for existing_idx in ordered):
            continue

        merged[idx] = max(float(strength), merged.get(idx, 0.0))
        ordered.append(idx)
        ordered.sort()

    return sorted(merged.items())


def detect_candidates(stem_path: Path) -> list[DrumCandidate]:
    samples, sr = preprocess_audio(
        stem_path,
        target_sr=44_100,
        pre_emphasis_coeff=0.9,
        high_pass_hz=35.0,
    )
    if not samples or sr <= 0:
        return []

    hop = 384
    hop_sec = hop / float(sr)
    env = compute_band_envelopes(
        samples,
        sr,
        {
            "low": (35.0, 180.0),
            "mid": (180.0, 2500.0),
            "high": (2500.0, 12_000.0),
        },
        hop_size=hop,
    )
    if not env:
        return []

    low = normalize_series(env["low"])
    mid = normalize_series(env["mid"])
    high = normalize_series(env["high"])

    n = min(len(low), len(mid), len(high))
    if n < 3:
        return []

    low_n = normalize_series(onset_novelty(low))
    mid_n = normalize_series(onset_novelty(mid))
    high_n = normalize_series(onset_novelty(high))
    novelty = normalize_series(
        [(0.45 * low_n[i]) + (0.3 * mid_n[i]) + (0.25 * high_n[i]) for i in range(n)]
    )

    peaks = adaptive_peak_pick(
        novelty,
        hop_sec=hop_sec,
        k=2.1,
        min_gap_sec=0.05,
        window_sec=0.38,
        percentile=0.85,
    )
    if not peaks:
        return []

    peaks = _merge_supplemental_peaks(
        peaks,
        novelty,
        adaptive_peak_pick(
            low_n,
            hop_sec=hop_sec,
            k=2.0,
            min_gap_sec=0.06,
            window_sec=0.34,
            percentile=0.84,
        ),
        novelty_ratio=0.9,
        min_strength=0.16,
        min_gap_frames=max(2, int(round(0.05 / hop_sec))),
    )
    peaks = _merge_supplemental_peaks(
        peaks,
        novelty,
        adaptive_peak_pick(
            high_n,
            hop_sec=hop_sec,
            k=1.75,
            min_gap_sec=0.045,
            window_sec=0.28,
            percentile=0.8,
        ),
        novelty_ratio=0.88,
        min_strength=0.14,
        min_gap_frames=max(2, int(round(0.04 / hop_sec))),
    )

    beat_period, beat_conf = estimate_tempo_from_onset_env(novelty, hop_sec)
    duration_sec = len(samples) / float(sr)
    step = _choose_grid_step(beat_period, len(peaks), duration_sec, beat_conf)
    tolerance = 0.045 if beat_conf < 0.22 else 0.03

    candidates: list[DrumCandidate] = []
    for idx, strength in peaks:
        t_raw = frame_to_time(idx, hop, sr)
        t = snap_time_to_grid(t_raw, anchor=0.0, step=step, tolerance=tolerance)
        feat = timbral_features(samples, sr, t)
        low_hit = _local_peak(low_n, idx, radius=1)
        mid_hit = _local_peak(mid_n, idx, radius=1)
        high_hit = _local_peak(high_n, idx, radius=1)

        total = max(
            1e-9,
            feat["sub"] + feat["low"] + feat["mid"] + feat["snare_crack"] + feat["high"] + feat["air"],
        )
        low_dom = clamp((feat["sub"] + (0.8 * feat["low"])) / total, 0.0, 1.0)
        snare_dom = clamp((feat["mid"] + (0.9 * feat["snare_crack"])) / total, 0.0, 1.0)
        high_dom = clamp((feat["high"] + (0.7 * feat["air"])) / total, 0.0, 1.0)
        crack_ratio = clamp(
            feat["snare_crack"] / max(1e-9, feat["low"] + feat["high"]),
            0.0,
            1.0,
        )

        kick_score = (0.58 * low_hit) + (0.42 * low_dom)
        snare_score = (0.46 * mid_hit) + (0.38 * snare_dom) + (0.16 * crack_ratio)
        hat_score = (0.62 * high_hit) + (0.38 * high_dom)

        if high_hit > max(low_hit, mid_hit) * 0.98 and high_dom > 0.16:
            hat_score += 0.08
        if low_hit > mid_hit * 1.08 and low_dom > 0.18:
            kick_score += 0.06
        if mid_hit > high_hit * 1.04 and snare_dom > 0.17:
            snare_score += 0.06

        drum_class, winner = max(
            (
                ("kick", kick_score),
                ("snare", snare_score),
                ("hh_closed", hat_score),
            ),
            key=lambda item: item[1],
        )

        if drum_class == "hh_closed":
            hat_class = classify_hat_or_cymbal(feat, prefer_ride_when_on_grid=False)
            if hat_class == "crash" and (high_hit < 0.22 or strength < 0.62):
                hat_class = "hh_open"
            drum_class = hat_class

        confidence = clamp((strength * 0.6) + (winner * 0.4), 0.0, 1.0)
        if drum_class == "crash" and confidence < 0.74:
            drum_class = "hh_open"

        candidates.append(
            DrumCandidate(
                time=t,
                drum_class=drum_class,
                strength=float(strength),
                confidence=confidence,
                source="adaptive_beat_grid",
            )
        )

    return candidates


ALGORITHM = AdaptiveBeatGridAlgorithm()


def transcribe(stem_path: Path) -> list[DrumEvent]:
    return ALGORITHM.transcribe(stem_path)
