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
    onset_novelty,
    preprocess_audio,
    snap_time_to_grid,
    timbral_features,
)
from aural_ingest.transcription import DrumEvent


class DspSpectralFluxAlgorithm(TranscriptionAlgorithm):
    name = "dsp_spectral_flux"

    def transcribe(self, stem_path: Path) -> list[DrumEvent]:
        candidates = detect_candidates(stem_path)
        if candidates:
            events = candidates_to_events(candidates, stem_path=stem_path)
            if events:
                return events

        return fallback_events_from_classes(
            stem_path,
            ["kick", "snare", "tom_floor", "hh_closed", "hh_open", "tom_low", "crash", "ride", "tom_high"],
            step_sec=0.08,
            velocity_base=85,
        )


def _merge_indices(*groups: list[tuple[int, float]]) -> list[tuple[int, float]]:
    merged: dict[int, float] = {}
    for group in groups:
        for idx, strength in group:
            merged[idx] = max(merged.get(idx, 0.0), strength)
    return sorted(merged.items(), key=lambda x: x[0])


def detect_candidates(stem_path: Path) -> list[DrumCandidate]:
    samples, sr = preprocess_audio(
        stem_path,
        target_sr=44_100,
        pre_emphasis_coeff=0.93,
        high_pass_hz=35.0,
    )
    if not samples or sr <= 0:
        return []

    hop = 256
    hop_sec = hop / float(sr)
    env = compute_band_envelopes(
        samples,
        sr,
        {
            "low": (35.0, 180.0),
            "mid": (180.0, 2500.0),
            "snare": (1700.0, 4200.0),
            "high": (2500.0, 12_000.0),
            "air": (6000.0, 14_000.0),
        },
        hop_size=hop,
        frame_size=1024,
    )
    if not env:
        return []

    low_flux = normalize_series(onset_novelty(env["low"]))
    mid_flux = normalize_series(onset_novelty(env["mid"]))
    snare_flux = normalize_series(onset_novelty(env["snare"]))
    high_flux = normalize_series(onset_novelty(env["high"]))
    air_flux = normalize_series(onset_novelty(env["air"]))

    n = min(len(low_flux), len(mid_flux), len(snare_flux), len(high_flux), len(air_flux))
    if n < 4:
        return []

    low_flux = low_flux[:n]
    mid_flux = mid_flux[:n]
    snare_flux = snare_flux[:n]
    high_flux = high_flux[:n]
    air_flux = air_flux[:n]

    global_flux = [
        (0.95 * low_flux[i]) + (1.0 * mid_flux[i]) + (1.05 * high_flux[i]) + (0.3 * air_flux[i])
        for i in range(n)
    ]
    global_flux = normalize_series(global_flux)

    peaks_main = adaptive_peak_pick(
        global_flux,
        hop_sec=hop_sec,
        k=2.2,
        min_gap_sec=0.045,
        window_sec=0.36,
        percentile=0.84,
    )
    peaks_high = adaptive_peak_pick(
        high_flux,
        hop_sec=hop_sec,
        k=2.4,
        min_gap_sec=0.055,
        window_sec=0.32,
        percentile=0.9,
    )
    peaks_low = adaptive_peak_pick(
        low_flux,
        hop_sec=hop_sec,
        k=2.35,
        min_gap_sec=0.08,
        window_sec=0.4,
        percentile=0.88,
    )

    peaks = _merge_indices(peaks_main, peaks_high, peaks_low)
    if not peaks:
        return []

    beat_period, beat_conf = estimate_tempo_from_onset_env(global_flux, hop_sec)
    step = beat_period / 2.0 if beat_period > 0 else 0.0

    candidates: list[DrumCandidate] = []
    for idx, strength in peaks:
        t_raw = frame_to_time(idx, hop, sr)
        t = snap_time_to_grid(t_raw, anchor=0.0, step=step, tolerance=0.03) if beat_conf >= 0.2 else t_raw

        lf = low_flux[idx]
        mf = mid_flux[idx]
        sf = snare_flux[idx]
        hf = high_flux[idx]
        af = air_flux[idx]

        feat = timbral_features(samples, sr, t)

        if lf >= mf * 1.12 and lf >= hf * 1.05:
            drum_class = "kick"
            winner = lf
        elif hf >= max(lf, mf) * 0.78:
            on_grid = beat_conf >= 0.22 and step > 0.0 and abs(t - t_raw) <= 0.03
            if feat["high_decay"] > 0.38:
                if (on_grid and feat["centroid"] < 9400.0) or (0.38 <= feat["high_decay"] <= 0.62 and feat["centroid"] < 9000.0):
                    drum_class = "ride"
                else:
                    drum_class = "crash"
            elif feat["high_decay"] > 0.14:
                drum_class = "hh_open"
            else:
                drum_class = "hh_closed"
            winner = hf
        elif sf >= lf * 0.6 and sf >= hf * 0.5:
            drum_class = "snare"
            winner = sf
        elif mf >= lf * 0.72 and lf >= hf * 0.48:
            drum_class = classify_tom(feat)
            winner = mf
        else:
            drum_class = "snare"
            winner = sf if sf > 0.0 else mf

        if drum_class == "hh_closed" and hf > 0.72 and feat["high_decay"] > 0.16:
            drum_class = "hh_open"
        if drum_class == "snare" and lf > 0.52 and mf > 0.48 and feat["low"] > feat["snare_crack"] * 1.12:
            drum_class = "tom_low" if idx % 2 == 0 else "tom_floor"

        confidence = clamp((winner * 0.7) + (strength * 0.3), 0.0, 1.0)
        confidence = clamp(confidence + (0.05 if af > 0.45 and drum_class in {"crash", "ride"} else 0.0), 0.0, 1.0)

        candidates.append(
            DrumCandidate(
                time=t,
                drum_class=drum_class,
                strength=float(strength),
                confidence=confidence,
                source="dsp_spectral_flux",
            )
        )

    return candidates


ALGORITHM = DspSpectralFluxAlgorithm()


def transcribe(stem_path: Path) -> list[DrumEvent]:
    return ALGORITHM.transcribe(stem_path)
