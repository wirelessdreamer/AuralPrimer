"""Probabilistic drum pattern model (Theory 5).

Post-processing approach that uses beat-position probability to filter
false positives and boost likely missed hits:
1. Run base detection (spectral_template_with_grid)
2. Estimate BPM and beat grid
3. Build probabilistic model: P(drum_class | beat_position)
4. Weight detections by proximity to likely beat positions
5. Remove improbable false positives
"""
from __future__ import annotations

import math
from pathlib import Path

from aural_ingest.algorithms._common import (
    DRUM_CLASS_TO_MIDI,
    DrumCandidate,
    TranscriptionAlgorithm,
    candidates_to_events,
    compute_band_envelopes,
    estimate_tempo_from_onset_env,
    fallback_events_from_classes,
    normalize_series,
    onset_novelty,
    preprocess_audio,
    timbral_features,
)
from aural_ingest.algorithms.spectral_template_with_grid import (
    SpectralTemplateWithGridAlgorithm,
)
from aural_ingest.transcription import DrumEvent

HOP = 320
FRAME = 1024

# Beat position probability models.
# Position is measured as fraction of beat (0.0 = downbeat, 0.5 = offbeat)
# Each pattern defines P(hit | position) for common time signatures.

# Kick: strong on beats 1 and 3 (positions 0.0, 0.5 in half-bar)
KICK_BEAT_PROBS = {
    0.00: 0.95,  # beat 1
    0.25: 0.30,  # beat 2
    0.50: 0.70,  # beat 3
    0.75: 0.25,  # beat 4
    0.125: 0.20,  # 8th notes
    0.375: 0.15,
    0.625: 0.20,
    0.875: 0.15,
}

# Snare: strong on beats 2 and 4
SNARE_BEAT_PROBS = {
    0.00: 0.10,  # beat 1
    0.25: 0.90,  # beat 2
    0.50: 0.10,  # beat 3
    0.75: 0.90,  # beat 4
    0.125: 0.15,
    0.375: 0.20,
    0.625: 0.15,
    0.875: 0.20,
}

# Hi-hat: strong on all 8th/16th note positions
HAT_BEAT_PROBS = {
    0.00: 0.85,
    0.125: 0.70,
    0.25: 0.85,
    0.375: 0.70,
    0.50: 0.85,
    0.625: 0.70,
    0.75: 0.85,
    0.875: 0.70,
}

# Map MIDI notes to pattern category
_MIDI_TO_PATTERN = {
    36: "kick",   # kick
    38: "snare",  # snare
    42: "hat",    # closed hat
    46: "hat",    # open hat
    49: "hat",    # crash (use hat pattern)
    51: "hat",    # ride
    50: "snare",  # tom high (snare-like pattern)
    47: "snare",  # tom low
    41: "kick",   # tom floor (kick-like pattern)
}


def _get_beat_position(time_sec: float, beat_period_sec: float, bar_beats: int = 4) -> float:
    """Get position within bar as fraction [0, 1)."""
    if beat_period_sec <= 0:
        return 0.0
    bar_period = beat_period_sec * bar_beats
    return (time_sec % bar_period) / bar_period


def _beat_probability(
    time_sec: float,
    beat_period_sec: float,
    pattern: str,
) -> float:
    """Get probability of a drum hit at this beat position."""
    if beat_period_sec <= 0:
        return 0.5  # no grid info — neutral

    probs = {"kick": KICK_BEAT_PROBS, "snare": SNARE_BEAT_PROBS, "hat": HAT_BEAT_PROBS}
    prob_map = probs.get(pattern, HAT_BEAT_PROBS)

    pos = _get_beat_position(time_sec, beat_period_sec)

    # Find nearest defined position
    best_prob = 0.3  # default probability for unlisted positions
    best_dist = float("inf")
    for defined_pos, prob in prob_map.items():
        dist = min(abs(pos - defined_pos), abs(pos - defined_pos + 1.0), abs(pos - defined_pos - 1.0))
        if dist < best_dist:
            best_dist = dist
            best_prob = prob

    # Interpolate — closer to grid = higher probability
    grid_tolerance = beat_period_sec * 0.15  # 15% of beat
    grid_factor = max(0.0, 1.0 - (best_dist * beat_period_sec * 4.0) / max(0.001, grid_tolerance))
    return best_prob * (0.5 + 0.5 * grid_factor)


class ProbabilisticPatternAlgorithm(TranscriptionAlgorithm):
    name = "probabilistic_pattern"

    def transcribe(self, stem_path: Path) -> list[DrumEvent]:
        # Step 1: Run base detection
        base_algo = SpectralTemplateWithGridAlgorithm()
        base_events = base_algo.transcribe(stem_path)
        if not base_events:
            return fallback_events_from_classes(
                stem_path,
                ["kick", "hh_closed", "snare", "hh_closed"],
                step_sec=0.082, velocity_base=87,
            )

        # Step 2: Estimate tempo
        samples, sr = preprocess_audio(
            stem_path,
            target_sr=44_100,
            pre_emphasis_coeff=0.94,
            high_pass_hz=35.0,
        )
        if not samples or sr <= 0:
            return base_events

        hop_sec = HOP / float(sr)

        onset_bands = {
            "kick_low":    (35.0, 120.0),
            "snare_mid":   (200.0, 2200.0),
            "hat_main":    (5000.0, 12000.0),
        }
        env = compute_band_envelopes(
            samples, sr, onset_bands, hop_size=HOP, frame_size=FRAME,
        )
        if not env:
            return base_events

        min_len = min(len(v) for v in env.values())
        if min_len < 4:
            return base_events

        combined = normalize_series([
            sum(env[b][i] for b in env) for i in range(min_len)
        ])
        novelty = normalize_series(onset_novelty(combined))
        beat_period, beat_conf = estimate_tempo_from_onset_env(novelty, hop_sec)

        if beat_conf < 0.10 or beat_period <= 0:
            return base_events  # can't determine beat grid

        # Step 3: Apply probabilistic filtering
        filtered_events: list[DrumEvent] = []

        for ev in base_events:
            pattern = _MIDI_TO_PATTERN.get(ev.note, "hat")
            prob = _beat_probability(ev.time, beat_period, pattern)

            # Dynamic threshold based on velocity
            vel_factor = ev.velocity / 127.0

            # Keep event if probability is high enough relative to its strength
            # Strong hits (high velocity) need lower probability threshold
            threshold = max(0.10, 0.35 - vel_factor * 0.20)

            if prob >= threshold:
                # Optionally adjust velocity based on beat position prominence
                vel_boost = 1.0 + (prob - 0.5) * 0.15
                new_vel = int(min(127, max(1, ev.velocity * vel_boost)))
                filtered_events.append(DrumEvent(
                    time=ev.time,
                    note=ev.note,
                    velocity=new_vel,
                    duration=ev.duration,
                ))
            # else: drop the event (probable false positive)

        if not filtered_events:
            return base_events  # don't return empty — keep base

        return sorted(filtered_events, key=lambda e: e.time)


def transcribe(stem_path: Path) -> list[DrumEvent]:
    algo = ProbabilisticPatternAlgorithm()
    return algo.transcribe(stem_path)
