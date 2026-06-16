"""``piano_pti_clean`` plus aggressive same-pitch echo deduplication.

Inspection of `piano_pti_clean` output on the synthetic piano corpus
revealed a consistent failure mode: PTI's frame-level decoder emits
"echo" detections roughly 330 ms after each real onset, at the same
pitch. These echoes survive the production ``piano_cleanup`` pipeline
because cleanup operates per-note rather than across the same-pitch
time series.

Concretely, on ``scale_c_2oct`` the 5 residual false positives in
`piano_pti_clean` were all echoes of real notes:

    t=3.00s B4 (TP)  → echo at t=3.33s B4 (FP)
    t=3.50s C5 (TP)  → echo at t=3.83s C5 (FP)
    t=4.00s D5 (TP)  → echo at t=4.33s D5 (FP)
    ... etc.

This wrapper applies a same-pitch suppression window after
`piano_pti_clean` runs: for each detected note, drop any later same-
pitch note whose onset falls within ``echo_window_sec`` of the kept
one. Window default 0.35 s = covers PTI's observed echo cadence
without disturbing legitimate repeated notes (e.g. eighth-note tremolo
runs land further apart on the rhythmic grid).
"""

from __future__ import annotations

from pathlib import Path

from aural_ingest.algorithms import piano_pti_clean as _base
from aural_ingest.transcription import MelodicNote


def _suppress_same_pitch_echoes(
    notes: list[MelodicNote],
    *,
    window_sec: float = 0.35,
) -> list[MelodicNote]:
    """Drop later same-pitch notes within ``window_sec`` of a kept onset.

    Earliest detection wins; any same-pitch note whose onset is within
    the window of the most recently kept one at that pitch is dropped.
    """
    if not notes:
        return notes
    by_pitch_then_time = sorted(notes, key=lambda n: (n.pitch, n.t_on))
    kept: list[MelodicNote] = []
    last_kept_by_pitch: dict[int, MelodicNote] = {}
    for n in by_pitch_then_time:
        prev = last_kept_by_pitch.get(int(n.pitch))
        if prev is None or (n.t_on - prev.t_on) > window_sec:
            kept.append(n)
            last_kept_by_pitch[int(n.pitch)] = n
    kept.sort(key=lambda n: (n.t_on, n.pitch))
    return kept


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "keys",
    echo_window_sec: float = 0.35,
    **kwargs,
) -> list[MelodicNote]:
    notes = list(_base.transcribe(stem_path, instrument=instrument))
    return _suppress_same_pitch_echoes(notes, window_sec=echo_window_sec)
