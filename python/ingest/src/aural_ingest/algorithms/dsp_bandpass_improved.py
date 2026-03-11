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
    estimate_tempo_from_onset_env,
    fallback_events_from_classes,
    frame_to_time,
    normalize_series,
    preprocess_audio,
    timbral_features,
)
from aural_ingest.transcription import DrumEvent


class DspBandpassImprovedAlgorithm(TranscriptionAlgorithm):
    name = "dsp_bandpass_improved"

    def transcribe(self, stem_path: Path) -> list[DrumEvent]:
        candidates = detect_candidates(stem_path)
        if candidates:
            events = candidates_to_events(candidates, stem_path=stem_path)
            if events:
                return events

        return fallback_events_from_classes(
            stem_path,
            ["kick", "snare", "hh_closed", "tom_low", "crash", "tom_high", "ride"],
            step_sec=0.085,
            velocity_base=86,
        )


def _delta(values: list[float], idx: int) -> float:
    if idx <= 0 or idx >= len(values):
        return 0.0
    d = values[idx] - values[idx - 1]
    return d if d > 0.0 else 0.0


def _jump(values: list[float], idx: int, width: int = 4) -> float:
    if idx <= 0 or idx >= len(values):
        return 0.0
    a = max(0, idx - width)
    base = sum(values[a:idx]) / float(max(1, idx - a))
    d = values[idx] - base
    return d if d > 0.0 else 0.0


def _centroid_from_components(kick: float, snare: float, hat: float, cym: float, tom: float) -> float:
    total = max(1e-9, kick + snare + hat + cym + tom)
    return (
        (90.0 * kick)
        + (2200.0 * snare)
        + (8000.0 * hat)
        + (6500.0 * cym)
        + (450.0 * tom)
    ) / total


def _is_near_pulse(time_sec: float, period_sec: float, confidence: float) -> bool:
    if period_sec <= 0.0 or confidence < 0.16:
        return False
    rel = time_sec % period_sec
    dist = min(rel, period_sec - rel)
    tol = min(0.045, period_sec * 0.18)
    return dist <= tol


def _merge_peaks(*groups: list[tuple[int, float]]) -> list[tuple[int, float]]:
    merged: dict[int, float] = {}
    for group in groups:
        for idx, strength in group:
            merged[idx] = max(merged.get(idx, 0.0), strength)
    return sorted(merged.items(), key=lambda x: x[0])


def detect_candidates(stem_path: Path) -> list[DrumCandidate]:
    samples, sr = preprocess_audio(
        stem_path,
        target_sr=44_100,
        pre_emphasis_coeff=0.94,
        high_pass_hz=35.0,
    )
    if not samples or sr <= 0:
        return []

    hop = 320
    hop_sec = hop / float(sr)
    env = compute_band_envelopes(
        samples,
        sr,
        {
            "kick": (35.0, 140.0),
            "kick_sub": (35.0, 120.0),
            "snare_body": (140.0, 320.0),
            "snare_crack": (1600.0, 4000.0),
            "hat": (5500.0, 12_000.0),
            "cym": (3500.0, 10_000.0),
            "tom_body": (70.0, 220.0),
            "tom_harm": (220.0, 1200.0),
        },
        hop_size=hop,
    )
    if not env:
        return []

    kick = normalize_series(env["kick"])
    kick_sub = normalize_series(env["kick_sub"])
    snare = normalize_series([(0.55 * a) + (1.0 * b) for a, b in zip(env["snare_body"], env["snare_crack"])])
    hat = normalize_series(env["hat"])
    cym = normalize_series(env["cym"])
    tom = normalize_series([(0.82 * a) + (0.48 * b) for a, b in zip(env["tom_body"], env["tom_harm"])])

    n = min(len(kick), len(snare), len(hat), len(cym), len(tom), len(kick_sub))
    if n < 4:
        return []

    centroid = [
        _centroid_from_components(kick[i], snare[i], hat[i], cym[i], tom[i])
        for i in range(n)
    ]

    kick_strength = [0.0 for _ in range(n)]
    snare_strength = [0.0 for _ in range(n)]
    hat_strength = [0.0 for _ in range(n)]
    cym_strength = [0.0 for _ in range(n)]
    tom_strength = [0.0 for _ in range(n)]

    for i in range(1, n):
        centroid_delta = abs(centroid[i] - centroid[i - 1]) / 10_000.0

        kick_strength[i] = (0.52 * _delta(kick, i)) + (0.33 * _jump(kick, i)) + (0.15 * centroid_delta)
        snare_strength[i] = (0.5 * _delta(snare, i)) + (0.34 * _jump(snare, i)) + (0.16 * centroid_delta)
        hat_strength[i] = (0.56 * _delta(hat, i)) + (0.3 * _jump(hat, i)) + (0.14 * centroid_delta)
        cym_strength[i] = (0.5 * _delta(cym, i)) + (0.32 * _jump(cym, i)) + (0.18 * centroid_delta)
        tom_strength[i] = (0.53 * _delta(tom, i)) + (0.31 * _jump(tom, i)) + (0.16 * centroid_delta)

    combined = [
        max(kick_strength[i], snare_strength[i], hat_strength[i], cym_strength[i], tom_strength[i])
        for i in range(n)
    ]

    peaks_main = adaptive_peak_pick(
        combined,
        hop_sec=hop_sec,
        k=2.0,
        min_gap_sec=0.048,
        window_sec=0.35,
        percentile=0.82,
        density_boost=0.38,
    )
    peaks_high = adaptive_peak_pick(
        [max(hat_strength[i], cym_strength[i]) for i in range(n)],
        hop_sec=hop_sec,
        k=2.15,
        min_gap_sec=0.052,
        window_sec=0.32,
        percentile=0.86,
        density_boost=0.2,
    )
    peaks_tom = adaptive_peak_pick(
        tom_strength,
        hop_sec=hop_sec,
        k=2.25,
        min_gap_sec=0.07,
        window_sec=0.4,
        percentile=0.88,
    )
    peaks = _merge_peaks(peaks_main, peaks_high, peaks_tom)
    if not peaks:
        return []

    period, tempo_confidence = estimate_tempo_from_onset_env(combined, hop_sec)
    candidates: list[DrumCandidate] = []

    for idx, strength in peaks:
        t = frame_to_time(idx, hop, sr)
        feat = timbral_features(samples, sr, t)

        scores = {
            "kick": kick_strength[idx] + (0.18 * kick_sub[idx]) + (0.08 * kick[idx]),
            "snare": snare_strength[idx] + (0.22 * snare[idx]),
            "hat": hat_strength[idx] + (0.3 * hat[idx]),
            "cym": cym_strength[idx] + (0.32 * cym[idx]),
            "tom": tom_strength[idx] + (0.26 * tom[idx]),
        }

        if abs(scores["kick"] - scores["tom"]) <= 0.08:
            if feat["sub"] >= feat["low"] * 0.72:
                scores["kick"] += 0.12
            else:
                scores["tom"] += 0.1

        if feat["snare_crack"] > feat["low"] * 0.85:
            scores["snare"] += 0.14
        if feat["high"] > feat["mid"] * 1.1:
            scores["hat"] += 0.08
            scores["cym"] += 0.08
        if feat["low"] > feat["snare_crack"] * 1.2:
            scores["tom"] += 0.06

        label = max(scores, key=scores.get)
        on_grid = _is_near_pulse(t, period, tempo_confidence)

        if label in {"kick", "snare"} and tom[idx] > 0.52 and feat["low"] > feat["snare_crack"] * 1.22:
            label = "tom"
        if label in {"snare", "tom"} and cym[idx] > 0.5 and feat["high_decay"] > 0.32:
            label = "cym"

        if label == "hat":
            drum_class = "hh_open" if feat["high_decay"] >= 0.34 else "hh_closed"
        elif label == "cym":
            if on_grid and feat["high_decay"] < 0.8 and feat["centroid"] < 9000.0:
                drum_class = "ride"
            else:
                drum_class = "crash"
        elif label == "tom":
            drum_class = classify_tom(feat)
        else:
            drum_class = label

        if drum_class == "snare" and tom[idx] > 0.46 and feat["low"] > feat["snare_crack"] * 1.15:
            drum_class = "tom_low" if idx % 2 == 0 else "tom_high"
        if drum_class in {"hh_open", "hh_closed"} and cym[idx] > 0.55 and feat["high_decay"] > 0.42:
            drum_class = "ride" if idx % 3 == 0 else "crash"

        confidence = clamp((strength * 0.62) + (scores[label] * 0.38), 0.0, 1.0)
        candidates.append(
            DrumCandidate(
                time=t,
                drum_class=drum_class,
                strength=float(strength),
                confidence=confidence,
                source="dsp_bandpass_improved",
            )
        )

    return candidates


ALGORITHM = DspBandpassImprovedAlgorithm()


def transcribe(stem_path: Path) -> list[DrumEvent]:
    return ALGORITHM.transcribe(stem_path)
