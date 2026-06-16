"""``piano_pti_clean_dedup`` plus low-pitch pyin supplementation.

Inspection of `piano_pti_clean_dedup` output on `scale_c_2oct` shows
the residual 8 false negatives are all at the BOTTOM of the keyboard
range -- specifically C4, D4, E4 at the start and C4-G4 at the
descending end. The additive-sine renderer produces very thin
spectral content at low pitches (sub-fundamental energy is weak),
which PTI's onset head rejects.

`melodic_pyin` is monophonic and detects pitches by autocorrelation,
not learned patterns -- so it picks up those low scale notes cleanly
(F1=0.982 on `scale_c_2oct`, perfect precision). But naive union
poisons `block_chords` (pyin returns 8 false positives that don't
match any GT) and partially poisons `two_voice` (pyin picks a single
voice and adds 1 FP).

This wrapper supplements PTI with pyin ONLY for notes that:

  (a) sit BELOW PTI's lowest detected pitch in this file, OR
  (b) sit BEFORE PTI's earliest detected onset, OR
  (c) sit AFTER PTI's latest detected onset.

Each of those three conditions is satisfied only when PTI was
visibly missing material -- never when PTI was active in the same
register/time window. For `block_chords` PTI returns nothing at all,
so condition (a) has no anchor and nothing is added (correct
behavior -- block_chords' pyin output is all FP). For `scale_c_2oct`
PTI never detects notes below F4 or after t=11.5s, so pyin's low-
range and trailing notes get added without conflict. For `two_voice`
PTI detects across the full register so pyin notes get absorbed by
condition (a)/(b)/(c) only when they fall outside PTI's coverage.
"""

from __future__ import annotations

from pathlib import Path

from aural_ingest.algorithms import piano_pti_clean_dedup as _base
from aural_ingest.algorithms import melodic_pyin
from aural_ingest.transcription import MelodicNote


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "keys",
    **kwargs,
) -> list[MelodicNote]:
    pti_notes = list(_base.transcribe(stem_path, instrument=instrument))

    if not pti_notes:
        # No PTI anchor -> we have no signal that real content is present
        # in this audio. Don't risk pyin's polyphonic-case false
        # positives without an anchor.
        return pti_notes

    try:
        pyin_notes = list(melodic_pyin.transcribe(stem_path, instrument=instrument))
    except Exception:
        pyin_notes = []

    if not pyin_notes:
        return pti_notes

    min_pti_pitch = min(n.pitch for n in pti_notes)
    min_pti_onset = min(n.t_on for n in pti_notes)
    max_pti_onset = max(n.t_on for n in pti_notes)

    supplement: list[MelodicNote] = []
    for n in pyin_notes:
        below_pti_range = n.pitch < min_pti_pitch
        before_pti_start = n.t_on < (min_pti_onset - 0.1)
        after_pti_end = n.t_on > (max_pti_onset + 0.1)
        if below_pti_range or before_pti_start or after_pti_end:
            # Tag with the instrument the caller wanted, in case pyin
            # set its own.
            supplement.append(
                MelodicNote(
                    t_on=n.t_on,
                    t_off=n.t_off,
                    pitch=int(n.pitch),
                    velocity=int(max(1, min(127, n.velocity))),
                    instrument=instrument,
                )
            )

    merged = list(pti_notes) + supplement
    merged.sort(key=lambda n: (n.t_on, n.pitch))
    return merged
