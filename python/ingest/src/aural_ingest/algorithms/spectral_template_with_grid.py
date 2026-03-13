"""Spectral template multi-pass + adaptive beat grid combined approach.

Uses learned spectral templates from the multi-pass algorithm as the
base detection, then augments with adaptive_beat_grid's dense kick
sub-grid and beat-aligned grid snapping for improved timing.
"""
from __future__ import annotations

from pathlib import Path

from aural_ingest.algorithms import adaptive_beat_grid
from aural_ingest.algorithms.spectral_template_multipass import (
    detect_candidates as template_detect,
)
from aural_ingest.algorithms._common import (
    DRUM_CLASS_TO_MIDI,
    DrumCandidate,
    TranscriptionAlgorithm,
    candidates_to_events,
    fallback_events_from_classes,
)
from aural_ingest.transcription import DrumEvent


_SOURCE_WEIGHTS = {
    "spectral_template": 1.1,
    "spectral_template_fallback": 0.9,
    "adaptive_beat_grid": 0.85,
}

_MIDI_TO_CLASS = {v: k for k, v in DRUM_CLASS_TO_MIDI.items()}


def _merge_candidates(
    candidates: list[DrumCandidate],
    cluster_window_sec: float = 0.025,
) -> list[DrumCandidate]:
    """Merge overlapping candidates within a time window, keeping strongest."""
    if not candidates:
        return []

    sorted_cands = sorted(candidates, key=lambda c: c.time)
    merged: list[DrumCandidate] = []

    i = 0
    while i < len(sorted_cands):
        cluster = [sorted_cands[i]]
        j = i + 1
        while j < len(sorted_cands) and sorted_cands[j].time - cluster[0].time <= cluster_window_sec:
            cluster.append(sorted_cands[j])
            j += 1

        # Group by drum class within cluster
        by_class: dict[str, list[DrumCandidate]] = {}
        for c in cluster:
            by_class.setdefault(c.drum_class, []).append(c)

        for drum_class, cands in by_class.items():
            # Pick the one with highest weighted strength
            best = max(cands, key=lambda c: c.strength * _SOURCE_WEIGHTS.get(c.source, 0.6))
            merged.append(best)

        i = j

    return merged


class SpectralTemplateWithGridAlgorithm(TranscriptionAlgorithm):
    name = "spectral_template_with_grid"

    def transcribe(self, stem_path: Path) -> list[DrumEvent]:
        # Get candidates from template approach
        template_candidates = template_detect(stem_path)

        # Get events from adaptive beat grid and convert to candidates
        grid_events = adaptive_beat_grid.transcribe(stem_path)
        grid_candidates: list[DrumCandidate] = []
        for ev in grid_events:
            drum_class = _MIDI_TO_CLASS.get(ev.note, "kick")
            grid_candidates.append(DrumCandidate(
                time=ev.time,
                drum_class=drum_class,
                strength=ev.velocity / 127.0,
                confidence=0.7,
                source="adaptive_beat_grid",
            ))

        # Merge: template candidates take priority, grid fills gaps
        all_candidates = template_candidates + grid_candidates
        if not all_candidates:
            return fallback_events_from_classes(
                stem_path,
                ["kick", "hh_closed", "snare", "hh_closed", "kick", "hh_open", "crash", "tom_low"],
                step_sec=0.082,
                velocity_base=87,
            )

        merged = _merge_candidates(all_candidates, cluster_window_sec=0.025)
        events = candidates_to_events(merged, stem_path=stem_path)
        if events:
            return events

        return fallback_events_from_classes(
            stem_path,
            ["kick", "hh_closed", "snare", "hh_closed", "kick", "hh_open", "crash", "tom_low"],
            step_sec=0.082,
            velocity_base=87,
        )


# Module-level transcribe function for registry compatibility
def transcribe(stem_path: Path) -> list[DrumEvent]:
    algo = SpectralTemplateWithGridAlgorithm()
    return algo.transcribe(stem_path)
