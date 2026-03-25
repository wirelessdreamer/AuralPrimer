"""Hybrid kick-optimized algorithm.

Takes kick events from spectral_template_with_grid (which has the best kick F1
at 0.593 mean) and snare/hat events from adaptive_beat_grid (which has the best
snare F1 at 0.447 mean).  This class-selective merger should combine the strengths
of both algorithms.

Also applies onset-alignment correction from onset_aligned to fix systematic
timing offsets.
"""
from __future__ import annotations

import math
from pathlib import Path

from aural_ingest.algorithms.adaptive_beat_grid import (
    _detect_candidates_internal as abg_detect_internal,
)
from aural_ingest.algorithms.spectral_template_multipass import (
    detect_candidates as template_detect,
)
from aural_ingest.algorithms._common import (
    DRUM_CLASS_TO_MIDI,
    DrumCandidate,
    TranscriptionAlgorithm,
    candidates_to_events,
    compute_band_envelopes,
    fallback_events_from_classes,
    normalize_series,
    onset_novelty,
    preprocess_audio,
)
from aural_ingest.transcription import DrumEvent

_MIDI_TO_CLASS = {v: k for k, v in DRUM_CLASS_TO_MIDI.items()}

# Classes to prefer from each source
_KICK_CLASSES = {"kick"}
_GRID_CLASSES = {"snare", "hh_closed", "hh_open", "crash", "ride", "tom1", "tom2", "tom3"}

# Onset-alignment parameters
_HOP = 320
_MAX_OFFSET_SEC = 0.060
_SEARCH_FRAMES = 30


def _estimate_timing_offset(
    samples: list[float],
    sr: int,
    events: list[DrumEvent],
) -> float:
    """Estimate systematic timing offset via cross-correlation."""
    hop_sec = _HOP / float(sr)
    duration_sec = len(samples) / float(sr)

    bands = {
        "kick_low": (35.0, 120.0),
        "snare_mid": (200.0, 2200.0),
        "hat_main": (5000.0, 12000.0),
    }
    env = compute_band_envelopes(samples, sr, bands, hop_size=_HOP)
    if not env:
        return 0.0

    min_len = min(len(v) for v in env.values())
    if min_len < 4:
        return 0.0

    combined = normalize_series([sum(env[b][i] for b in env) for i in range(min_len)])
    audio_novelty = normalize_series(onset_novelty(combined))

    # Build predicted onset envelope
    n_frames = max(1, int(duration_sec / hop_sec))
    pred_env = [0.0] * n_frames
    for ev in events:
        idx = int(ev.time / hop_sec)
        if 0 <= idx < n_frames:
            w = ev.velocity / 127.0
            for off in range(-3, 4):
                ii = idx + off
                if 0 <= ii < n_frames:
                    g = math.exp(-0.5 * (off ** 2))
                    pred_env[ii] = max(pred_env[ii], w * g)

    # Cross-correlate
    n = min(len(audio_novelty), len(pred_env))
    max_lag = min(_SEARCH_FRAMES, int(_MAX_OFFSET_SEC / hop_sec))
    best_lag, best_corr = 0, -float("inf")
    for lag in range(-max_lag, max_lag + 1):
        corr = sum(
            audio_novelty[i] * pred_env[i + lag]
            for i in range(n) if 0 <= i + lag < n
        )
        if corr > best_corr:
            best_corr = corr
            best_lag = lag

    offset = best_lag * hop_sec
    return max(-_MAX_OFFSET_SEC, min(_MAX_OFFSET_SEC, offset))


class HybridKickGridAlgorithm(TranscriptionAlgorithm):
    name = "hybrid_kick_grid"

    def transcribe(self, stem_path: Path) -> list[DrumEvent]:
        # 1. Get kick candidates from spectral template (best kick detection)
        template_cands = template_detect(stem_path)
        kick_cands = [c for c in template_cands if c.drum_class in _KICK_CLASSES]

        # Boost kick candidate weight
        kick_cands = [
            DrumCandidate(
                time=c.time,
                drum_class=c.drum_class,
                strength=c.strength,
                confidence=c.confidence,
                source="hybrid_kick_template",
            )
            for c in kick_cands
        ]

        # 2. Get snare/hat candidates from ABG (best snare/hat detection)
        abg_cands, kick_step = abg_detect_internal(stem_path)
        grid_cands = [c for c in abg_cands if c.drum_class in _GRID_CLASSES]

        # 3. Also include any template non-kick candidates (crash, tom, etc.)
        # but at lower weight — template catches things ABG doesn't
        template_other = [
            DrumCandidate(
                time=c.time,
                drum_class=c.drum_class,
                strength=c.strength * 0.85,
                confidence=c.confidence * 0.85,
                source="hybrid_template_support",
            )
            for c in template_cands
            if c.drum_class not in _KICK_CLASSES
        ]

        # 4. Merge with class-aware windowing
        all_cands = kick_cands + grid_cands + template_other
        if not all_cands:
            return fallback_events_from_classes(
                stem_path, ["kick", "snare", "hh_closed"], step_sec=0.11, velocity_base=84,
            )

        merged = _merge_by_class(all_cands, window_sec=0.025)

        # 5. Convert to events
        from aural_ingest.algorithms._common import CLASS_REFRACTORY_SEC
        refractory_overrides = None
        if kick_step is not None:
            refractory_overrides = {
                "kick": min(CLASS_REFRACTORY_SEC["kick"], max(0.055, kick_step * 0.82))
            }

        events = candidates_to_events(
            merged, stem_path=stem_path, refractory_overrides=refractory_overrides,
        )
        if not events:
            return fallback_events_from_classes(
                stem_path, ["kick", "snare", "hh_closed"], step_sec=0.11, velocity_base=84,
            )

        # 6. Apply onset-alignment correction
        samples, sr = preprocess_audio(
            stem_path, target_sr=44_100, pre_emphasis_coeff=0.94, high_pass_hz=35.0,
        )
        if samples and sr > 0:
            offset = _estimate_timing_offset(samples, sr, events)
            if abs(offset) >= 0.002:
                events = [
                    DrumEvent(
                        time=round(max(0.0, e.time - offset), 6),
                        note=e.note,
                        velocity=e.velocity,
                        duration=e.duration,
                    )
                    for e in events
                ]

        return sorted(events, key=lambda e: e.time)


def _merge_by_class(
    candidates: list[DrumCandidate],
    window_sec: float = 0.025,
) -> list[DrumCandidate]:
    """Merge candidates, keeping best per drum class within each time window."""
    if not candidates:
        return []

    sorted_cands = sorted(candidates, key=lambda c: c.time)
    merged: list[DrumCandidate] = []

    i = 0
    while i < len(sorted_cands):
        cluster = [sorted_cands[i]]
        j = i + 1
        while j < len(sorted_cands) and sorted_cands[j].time - cluster[0].time <= window_sec:
            cluster.append(sorted_cands[j])
            j += 1

        by_class: dict[str, list[DrumCandidate]] = {}
        for c in cluster:
            by_class.setdefault(c.drum_class, []).append(c)

        for drum_class, cands in by_class.items():
            best = max(cands, key=lambda c: c.strength * c.confidence)
            merged.append(best)

        i = j

    return merged


ALGORITHM = HybridKickGridAlgorithm()


def transcribe(stem_path: Path) -> list[DrumEvent]:
    return ALGORITHM.transcribe(stem_path)
