from __future__ import annotations

from pathlib import Path

from aural_ingest.algorithms._common import (
    DrumCandidate,
    TranscriptionAlgorithm,
    adaptive_peak_pick,
    candidates_to_events,
    classify_hat_or_cymbal,
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


class DspBandpassAlgorithm(TranscriptionAlgorithm):
    name = "dsp_bandpass"

    def transcribe(self, stem_path: Path) -> list[DrumEvent]:
        candidates = detect_candidates(stem_path)
        if candidates:
            events = candidates_to_events(candidates, stem_path=stem_path)
            if events:
                return events

        return fallback_events_from_classes(
            stem_path,
            ["kick", "snare", "hh_closed", "hh_open"],
            step_sec=0.1,
            velocity_base=82,
        )


def _combine(a: list[float], b: list[float], wa: float, wb: float) -> list[float]:
    n = min(len(a), len(b))
    return [(a[i] * wa) + (b[i] * wb) for i in range(n)]


def _argmax_class(scores: dict[str, float]) -> str:
    return max(scores, key=scores.get)


def detect_candidates(stem_path: Path) -> list[DrumCandidate]:
    samples, sr = preprocess_audio(
        stem_path,
        target_sr=44_100,
        pre_emphasis_coeff=0.97,
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
            "kick": (35.0, 140.0),
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
    snare = normalize_series(_combine(env["snare_body"], env["snare_crack"], 0.6, 0.9))
    hat = normalize_series(env["hat"])
    cym = normalize_series(env["cym"])
    tom = normalize_series(_combine(env["tom_body"], env["tom_harm"], 0.8, 0.45))

    n = min(len(kick), len(snare), len(hat), len(cym), len(tom))
    if n < 3:
        return []

    composite = [max(kick[i], snare[i], hat[i], cym[i], tom[i]) for i in range(n)]
    peaks = adaptive_peak_pick(
        composite,
        hop_sec=hop_sec,
        k=2.1,
        min_gap_sec=0.055,
        window_sec=0.4,
        percentile=0.8,
    )
    if not peaks:
        return []

    candidates: list[DrumCandidate] = []
    for idx, strength in peaks:
        scores = {
            "kick": kick[idx],
            "snare": snare[idx],
            "hat_cym": max(hat[idx], cym[idx]),
            "tom": tom[idx],
        }
        label = _argmax_class(scores)
        t = frame_to_time(idx, hop, sr)

        if label == "hat_cym":
            feat = timbral_features(samples, sr, t)
            drum_class = classify_hat_or_cymbal(feat)
        elif label == "tom":
            feat = timbral_features(samples, sr, t)
            drum_class = classify_tom(feat)
        else:
            drum_class = label

        confidence = clamp((strength * 0.75) + (scores[label] * 0.25), 0.0, 1.0)
        candidates.append(
            DrumCandidate(
                time=t,
                drum_class=drum_class,
                strength=float(strength),
                confidence=confidence,
                source="dsp_bandpass",
            )
        )

    return candidates


ALGORITHM = DspBandpassAlgorithm()


def transcribe(stem_path: Path) -> list[DrumEvent]:
    return ALGORITHM.transcribe(stem_path)
