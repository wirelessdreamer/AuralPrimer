"""Narrowly gated cleanup passes for guitar note streams.

Mirrors the discipline of ``piano_cleanup.py``: each pass is a small pure
function operating on ``list[MelodicNote]``; each has a ``_should_run`` gate
so it fires only on inputs whose shape matches its target failure mode; each
is independently unit-tested; and none mutate their input.

Passes (all opt-in by gate):

* :func:`prune_distortion_overtone_shadows` — drop harmonic shadows sitting a
  perfect octave/fifth above a louder, overlapping base note. Fires only when
  the input density suggests a distorted electric guitar.
* :func:`merge_bend_slide_vibrato_glides` — collapse adjacent single-semitone
  neighbor sequences (monotonic bends, oscillating vibrato) into a single
  held note. Fires only when input shows ≥3 adjacent single-step neighbors
  within 200 ms.
* :func:`suppress_palm_mutes_and_ghost_notes` — drop notes below a velocity
  AND duration floor. Fires only when the input velocity distribution is
  bimodal (a real-note cluster plus a low-velocity tail).
* :func:`per_string_range_gate` — restrict pitches to a role-appropriate
  range (``"lead"`` → E3+, ``"rhythm"`` → E2..A4). Fires only when the role
  is known.

The composer :func:`apply_guitar_cleanup` runs all passes in order and is a
helper only — it is intentionally *not* wired into the melodic dispatcher.
Phase 5 (guitar_auto) will handle production wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from aural_ingest.transcription import MelodicNote


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GuitarCleanupConfig:
    """Tunable thresholds for each cleanup pass.

    Defaults are conservative — each pass has a gate that keeps it a no-op
    unless the input clearly matches its target failure mode.
    """

    # Distortion overtone shadow pruning
    distortion_gate_min_notes: int = 8
    distortion_gate_min_shadow_ratio: float = 0.35
    distortion_shadow_intervals: tuple[int, ...] = (7, 12, 19, 24)
    distortion_shadow_velocity_margin: int = 4
    distortion_shadow_min_overlap_sec: float = 0.025
    distortion_shadow_max_duration_ratio: float = 1.05

    # Bend/slide/vibrato glide merging
    glide_gate_min_neighbors: int = 3
    glide_gate_window_sec: float = 0.2
    glide_max_onset_gap_sec: float = 0.08

    # Palm-mute / ghost-note suppression
    mute_gate_min_notes: int = 6
    mute_gate_min_low_ratio: float = 0.18
    mute_velocity_floor: int = 40
    mute_duration_floor_sec: float = 0.06
    mute_gate_velocity_gap: int = 20

    # Per-string range gating
    lead_min_midi: int = 40  # E3
    rhythm_min_midi: int = 28  # E2 (six-string standard low E)
    rhythm_max_midi: int = 69  # A4 (typical rhythm upper bound)


DEFAULT_GUITAR_CLEANUP_CONFIG = GuitarCleanupConfig()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _duration(note: MelodicNote) -> float:
    return max(0.0, float(note.t_off) - float(note.t_on))


def _sort_by_onset(notes: list[MelodicNote]) -> list[MelodicNote]:
    return sorted(notes, key=lambda n: (float(n.t_on), int(n.pitch), float(n.t_off)))


def _overlap(a: MelodicNote, b: MelodicNote) -> float:
    return max(0.0, min(float(a.t_off), float(b.t_off)) - max(float(a.t_on), float(b.t_on)))


# ---------------------------------------------------------------------------
# Pass 1: distortion overtone shadow pruning
# ---------------------------------------------------------------------------


def _distortion_gate(notes: list[MelodicNote], config: GuitarCleanupConfig) -> bool:
    """Fire only when input density suggests distorted electric guitar.

    Signature: at least ``distortion_gate_min_notes`` total notes AND at
    least ``distortion_gate_min_shadow_ratio`` of them sit at a distortion
    interval (octave/fifth/twelfth/double-octave) above another temporally
    overlapping note.
    """
    if len(notes) < int(config.distortion_gate_min_notes):
        return False

    intervals = set(int(x) for x in config.distortion_shadow_intervals)
    shadow_hits = 0
    for i, upper in enumerate(notes):
        for j, lower in enumerate(notes):
            if i == j:
                continue
            if int(upper.pitch) - int(lower.pitch) not in intervals:
                continue
            if _overlap(upper, lower) > 0.0:
                shadow_hits += 1
                break

    return (shadow_hits / float(len(notes))) >= float(config.distortion_gate_min_shadow_ratio)


def prune_distortion_overtone_shadows(
    notes: list[MelodicNote],
    config: GuitarCleanupConfig = DEFAULT_GUITAR_CLEANUP_CONFIG,
) -> list[MelodicNote]:
    """Drop harmonic shadows sitting a perfect octave/fifth above a louder base note."""
    if not notes or not _distortion_gate(notes, config):
        return list(notes)

    intervals = set(int(x) for x in config.distortion_shadow_intervals)
    keep = [True] * len(notes)
    for i, upper in enumerate(notes):
        for j, lower in enumerate(notes):
            if i == j:
                continue
            interval = int(upper.pitch) - int(lower.pitch)
            if interval not in intervals:
                continue
            if _overlap(upper, lower) < float(config.distortion_shadow_min_overlap_sec):
                continue
            # Base note must be *louder* (velocity margin) — shadow is quieter.
            if int(lower.velocity) < int(upper.velocity) + int(config.distortion_shadow_velocity_margin):
                continue
            # Shadow duration should not exceed the base note's duration much
            # (real doubled notes typically last as long or longer than shadows).
            if _duration(upper) > _duration(lower) * float(config.distortion_shadow_max_duration_ratio):
                continue
            keep[i] = False
            break

    return [n for n, k in zip(notes, keep) if k]


# ---------------------------------------------------------------------------
# Pass 2: bend / slide / vibrato glide merging
# ---------------------------------------------------------------------------


def _glide_gate(notes: list[MelodicNote], config: GuitarCleanupConfig) -> bool:
    """Fire only when input shows ≥3 adjacent single-semitone-step notes
    within a short window (default 200 ms)."""
    if len(notes) < int(config.glide_gate_min_neighbors):
        return False

    ordered = _sort_by_onset(notes)
    window = float(config.glide_gate_window_sec)
    min_neighbors = int(config.glide_gate_min_neighbors)

    for start in range(len(ordered) - min_neighbors + 1):
        first = ordered[start]
        end = start + min_neighbors - 1
        last = ordered[end]
        if float(last.t_on) - float(first.t_on) > window:
            continue
        ok = True
        for k in range(start, end):
            if abs(int(ordered[k + 1].pitch) - int(ordered[k].pitch)) != 1:
                ok = False
                break
        if ok:
            return True
    return False


def _find_glide_runs(
    notes: list[MelodicNote], config: GuitarCleanupConfig
) -> list[list[int]]:
    """Return index-runs of adjacent single-step neighbors under the onset gap."""
    runs: list[list[int]] = []
    if len(notes) < int(config.glide_gate_min_neighbors):
        return runs

    max_gap = float(config.glide_max_onset_gap_sec)
    current: list[int] = [0]
    for i in range(1, len(notes)):
        prev = notes[current[-1]]
        step = abs(int(notes[i].pitch) - int(prev.pitch))
        gap = float(notes[i].t_on) - float(prev.t_on)
        if step == 1 and 0.0 <= gap <= max_gap:
            current.append(i)
            continue
        if len(current) >= int(config.glide_gate_min_neighbors):
            runs.append(current)
        current = [i]

    if len(current) >= int(config.glide_gate_min_neighbors):
        runs.append(current)
    return runs


def merge_bend_slide_vibrato_glides(
    notes: list[MelodicNote],
    config: GuitarCleanupConfig = DEFAULT_GUITAR_CLEANUP_CONFIG,
) -> list[MelodicNote]:
    """Merge sequences of adjacent single-semitone-step notes into one held note.

    Handles two shapes at once: a monotonic bend/slide (all steps in one
    direction) and a symmetric vibrato (steps oscillating around a center).
    The merged note takes the pitch that is either the run's endpoint (bend
    target) or the center pitch (vibrato).
    """
    if not notes:
        return []

    ordered = _sort_by_onset(notes)
    if not _glide_gate(ordered, config):
        return list(notes)

    runs = _find_glide_runs(ordered, config)
    if not runs:
        return list(notes)

    absorbed: set[int] = set()
    merged_notes: list[MelodicNote] = []
    for run in runs:
        pitches = [int(ordered[i].pitch) for i in run]
        first = ordered[run[0]]
        last = ordered[run[-1]]
        # Monotonic vs oscillating.
        deltas = [pitches[k + 1] - pitches[k] for k in range(len(pitches) - 1)]
        monotonic = all(d > 0 for d in deltas) or all(d < 0 for d in deltas)
        if monotonic:
            merged_pitch = pitches[-1]  # bend target
        else:
            merged_pitch = int(round(sum(pitches) / float(len(pitches))))  # vibrato center

        merged_notes.append(
            replace(
                first,
                t_on=round(float(first.t_on), 6),
                t_off=round(max(float(last.t_off), float(first.t_off)), 6),
                pitch=merged_pitch,
                velocity=max(int(ordered[i].velocity) for i in run),
            )
        )
        for i in run:
            absorbed.add(i)

    kept = [n for i, n in enumerate(ordered) if i not in absorbed]
    kept.extend(merged_notes)
    return _sort_by_onset(kept)


# ---------------------------------------------------------------------------
# Pass 3: palm-mute / ghost-note suppression
# ---------------------------------------------------------------------------


def _mute_gate(notes: list[MelodicNote], config: GuitarCleanupConfig) -> bool:
    """Fire only when the input has a bimodal velocity distribution: a
    substantial low-velocity tail plus a clearly-separated main cluster."""
    if len(notes) < int(config.mute_gate_min_notes):
        return False

    velocities = sorted(int(n.velocity) for n in notes)
    low_count = sum(1 for v in velocities if v < int(config.mute_velocity_floor))
    if low_count == 0:
        return False
    if (low_count / float(len(velocities))) < float(config.mute_gate_min_low_ratio):
        return False

    # Require a real gap between low tail and main cluster (bimodality proxy).
    high = [v for v in velocities if v >= int(config.mute_velocity_floor)]
    if not high:
        return False
    low_max = max(v for v in velocities if v < int(config.mute_velocity_floor))
    high_min = min(high)
    return (high_min - low_max) >= int(config.mute_gate_velocity_gap)


def suppress_palm_mutes_and_ghost_notes(
    notes: list[MelodicNote],
    config: GuitarCleanupConfig = DEFAULT_GUITAR_CLEANUP_CONFIG,
) -> list[MelodicNote]:
    """Drop notes below both a velocity floor and a duration floor."""
    if not notes or not _mute_gate(notes, config):
        return list(notes)

    v_floor = int(config.mute_velocity_floor)
    d_floor = float(config.mute_duration_floor_sec)
    out: list[MelodicNote] = []
    for note in notes:
        if int(note.velocity) < v_floor and _duration(note) < d_floor:
            continue
        out.append(note)
    return out


# ---------------------------------------------------------------------------
# Pass 4: per-string range gating
# ---------------------------------------------------------------------------


def _range_gate(role: Optional[str]) -> bool:
    """Fire only when the role is known (``"lead"`` or ``"rhythm"``)."""
    return role in ("lead", "rhythm")


def per_string_range_gate(
    notes: list[MelodicNote],
    role: Optional[str],
    config: GuitarCleanupConfig = DEFAULT_GUITAR_CLEANUP_CONFIG,
) -> list[MelodicNote]:
    """Restrict notes to a role-appropriate pitch range.

    * ``role="lead"`` → keep pitches at or above ``lead_min_midi`` (E3, 40).
    * ``role="rhythm"`` → keep pitches within
      ``[rhythm_min_midi, rhythm_max_midi]`` (E2..A4).
    * Any other role (including ``None``) → no-op.
    """
    if not notes or not _range_gate(role):
        return list(notes)

    if role == "lead":
        lo = int(config.lead_min_midi)
        return [n for n in notes if int(n.pitch) >= lo]

    # role == "rhythm"
    lo = int(config.rhythm_min_midi)
    hi = int(config.rhythm_max_midi)
    return [n for n in notes if lo <= int(n.pitch) <= hi]


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------


def apply_guitar_cleanup(
    notes: list[MelodicNote],
    *,
    role: Optional[str] = None,
    config: GuitarCleanupConfig = DEFAULT_GUITAR_CLEANUP_CONFIG,
) -> list[MelodicNote]:
    """Run all gated cleanup passes in order and return a NEW note list.

    Order rationale:

    1. Distortion overtone pruning first — removes spurious upper shadows
       before glide detection can mistake them for neighbors.
    2. Glide merging next — collapses bend/vibrato clusters before mute
       suppression can drop short vibrato segments as "ghost notes".
    3. Palm-mute / ghost-note suppression.
    4. Per-string range gating last — hard pitch clamp on the final stream.
    """
    if not notes:
        return []

    cleaned = _sort_by_onset(notes)
    cleaned = prune_distortion_overtone_shadows(cleaned, config)
    cleaned = merge_bend_slide_vibrato_glides(cleaned, config)
    cleaned = suppress_palm_mutes_and_ghost_notes(cleaned, config)
    cleaned = per_string_range_gate(cleaned, role, config)
    return _sort_by_onset(cleaned)
