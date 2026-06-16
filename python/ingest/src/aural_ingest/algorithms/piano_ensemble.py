"""Ensemble keys transcriber: union of piano_pti + melodic_basic_pitch.

The 2026-06-14 synthetic piano benchmark surfaced that piano_pti and
melodic_basic_pitch have COMPLEMENTARY failure modes:

  Case                    piano_pti   basic_pitch
  scale_c_2oct                0.656     1.000
  triad_arp_progression       0.968     1.000
  block_chords                0.000     0.340
  two_voice                   0.778     0.246

melodic_basic_pitch wins single-voice content (perfect on scale +
arpeggio, partial recovery on chord polyphony). piano_pti wins wide
register separation (LH bass + RH melody) where basic_pitch's
polyphonic head gets confused by the octave-displaced voices.

This module runs both pipelines on the same stem and unions the
detected notes with onset-pitch dedup. Two notes are "the same" when
their onsets are within ``onset_tolerance_sec`` AND their pitches
agree exactly; the piano_pti detection wins for the kept
representative (precision-favoring tiebreak).

Cost: ~2x a single PTI call (both engines run independently). For
the synthetic 4-case corpus that's ~3-5s/case end-to-end -- still
sub-realtime per second of audio, but slower than the production
auto-chain which stops at the first method that returns notes.

Use when:
  - producing the Refine workspace "high-recall" keys candidate
  - establishing an F1 ceiling for evaluation purposes
  - production stems where neither engine alone meets the F1 target
"""

from __future__ import annotations

from pathlib import Path

from aural_ingest.transcription import MelodicNote


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "keys",
    onset_tolerance_sec: float = 0.05,
    **kwargs,
) -> list[MelodicNote]:
    from aural_ingest.algorithms import piano_pti
    from aural_ingest.algorithms import melodic_basic_pitch
    from aural_ingest.transcription import (
        _default_basic_pitch_model_roots,
        resolve_basic_pitch_model_path,
    )

    bp_model = resolve_basic_pitch_model_path(_default_basic_pitch_model_roots())

    pti_notes: list[MelodicNote] = []
    try:
        pti_notes = list(piano_pti.transcribe(stem_path, instrument=instrument))
    except Exception:
        pti_notes = []

    bp_notes: list[MelodicNote] = []
    try:
        bp_notes = list(
            melodic_basic_pitch.transcribe(
                stem_path,
                model_path=bp_model,
                instrument=instrument,
            )
        )
    except Exception:
        bp_notes = []

    # Union with onset-pitch dedup. PTI's representative wins for kept
    # notes (higher precision on this corpus).
    out: list[MelodicNote] = list(pti_notes)
    for nb in bp_notes:
        is_dup = any(
            abs(nb.t_on - na.t_on) <= onset_tolerance_sec and nb.pitch == na.pitch
            for na in out
        )
        if not is_dup:
            out.append(nb)

    out.sort(key=lambda n: (n.t_on, n.pitch))
    return out
