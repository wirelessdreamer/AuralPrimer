"""
``librosa_superflux_dense`` — recall-tuned superflux for dry isolated
drum-kit recordings (E-GMD-style stems).

The production-tuned `librosa_superflux` algorithm uses an adaptive
peak-picker with ``k=2.4``, ``percentile=0.90``, ``min_gap_sec=0.07``,
``window_sec=0.45``. Those settings were chosen to be CONSERVATIVE on
noisy multi-instrument stems where every spurious onset becomes a
gameplay false positive.

Against ground-truth-annotated isolated drum stems (E-GMD), the same
settings reject ~90% of the actual hits. This module re-runs the same
SuperFlux pipeline with a sensitivity-tuned set of peak-picker
parameters: lower thresholds, smaller minimum gap (catches ghost
snare doubles + fast hi-hat), shorter local-mean window (reacts to
intensity envelope changes within a single fill).

Pure-Python -- depends only on the modules already imported by the
production variant. Switching between the two is a configuration
choice, not an algorithm change.
"""

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
from aural_ingest.algorithms.librosa_superflux import _superflux_envelope
from aural_ingest.transcription import DrumEvent


class LibrosaSuperfluxDenseAlgorithm(TranscriptionAlgorithm):
    name = "librosa_superflux_dense"

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


def detect_candidates(stem_path: Path) -> list[DrumCandidate]:
    """SuperFlux pipeline with E-GMD-tuned peak-picker parameters.

    Identical band layout + envelope + classification to the production
    variant; only the ``adaptive_peak_pick`` call differs:

        production: k=2.4, min_gap=0.07s, window=0.45s, percentile=0.90
        dense:      k=1.3, min_gap=0.04s, window=0.30s, percentile=0.70

    The classification branches are also slightly more permissive:
    the kick low/high ratio drops from 1.08 to 1.02, and the snare
    branch's "mid >= high * 0.65" gates onto "mid >= high * 0.55" so
    pop-snare hits with strong overtones don't get re-classified as
    cymbals.
    """
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

    # The four parameters this variant actually changes.
    peaks = adaptive_peak_pick(
        onset_env,
        hop_sec=hop_sec,
        k=1.3,             # was 2.4: admit ~3x more peaks above local mean
        min_gap_sec=0.04,  # was 0.07: catches ghost-snare doubles + 16th-note hat
        window_sec=0.30,   # was 0.45: react faster to local-envelope changes
        percentile=0.70,   # was 0.90: drop the "ridiculously prominent" gate
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

        # Loosened classification gates -- see the docstring above.
        if low >= max(mid, high) * 1.02:
            drum_class = "kick"
            winner = low
        elif (ordered[4][idx] + feat["snare_crack"]) >= low * 0.80 and mid >= high * 0.55:
            drum_class = "snare"
            winner = mid
        elif high >= max(low, mid) * 0.85:
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
                source="librosa_superflux_dense",
            )
        )

    return candidates


ALGORITHM = LibrosaSuperfluxDenseAlgorithm()


def transcribe(stem_path: Path) -> list[DrumEvent]:
    return ALGORITHM.transcribe(stem_path)
