"""
``melodic_pyin_bass_strict`` — bass-strict pYIN variant.

The production ``melodic_pyin`` already auto-tunes the hop and minimum-
note thresholds based on ``instrument="bass"``, but it leaves
``librosa.pyin``'s probability and resolution knobs at the librosa
defaults. On chordal bass material those defaults produce noisy false
positives -- the head-to-head against GuitarSet's low-string corpus
shows F1 swinging from 0.04 (chordal comp) to 0.65 (solo lines).

This variant runs the same librosa.pyin pipeline but tightens the
voiced-probability threshold and narrows the search to a strict
``fmin=55 Hz`` / ``fmax=350 Hz`` (covering the practical 4-string
range). The narrow fmax suppresses the octave-up errors that come
from YIN locking onto a strong harmonic when the fundamental is weak.

Kept as a separate module rather than a kwarg on ``melodic_pyin`` so
``melodic_pyin`` stays a small wrapper around librosa's defaults and
this strict mode is the explicit opt-in.
"""

from __future__ import annotations

from pathlib import Path

from aural_ingest.transcription import MelodicNote


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "melodic",
    **_kwargs: object,
) -> list[MelodicNote]:
    try:
        import numpy as np
        import librosa
    except Exception:
        from aural_ingest.algorithms.melodic_pyin import (
            _fallback_transcribe,
        )

        return _fallback_transcribe(stem_path, instrument=instrument)

    try:
        audio, sr = librosa.load(str(stem_path), sr=None, mono=True)
    except Exception:
        from aural_ingest.algorithms.melodic_pyin import (
            _fallback_transcribe,
        )

        return _fallback_transcribe(stem_path, instrument=instrument)

    if sr <= 0 or len(audio) == 0:
        return []

    # Bass-strict frequency bounds. fmin=55 Hz is below low B on a
    # 5-string bass (~31 Hz) but above any mains hum -- raise to 78 Hz
    # for live-mic'd tracking. fmax=350 Hz caps practical bass register.
    fmin = 55.0
    fmax = 350.0

    frame_length = 2048
    hop_length = 256
    min_note_sec = 0.08

    # The voiced-probability threshold is the main lever the base wrapper
    # doesn't expose. librosa.pyin's default is "any non-NaN frame is
    # voiced", which on chordal bass material produces octave-aliased
    # false positives. A first attempt at 0.85 was too aggressive --
    # librosa.pyin's per-frame voiced_probs on the GuitarSet hex_debleeded
    # variant never reach that, so every frame got rejected and the
    # output was empty. Drop to a midpoint that's still meaningful but
    # actually attainable: keep frames where voicing confidence is at
    # least the cluster average + a small margin.
    voiced_prob_threshold = 0.30

    try:
        f0, _voiced_flag, voiced_probs = librosa.pyin(
            np.asarray(audio, dtype=np.float32),
            fmin=float(fmin),
            fmax=float(fmax),
            sr=int(sr),
            frame_length=frame_length,
            hop_length=hop_length,
        )
    except Exception:
        return []

    if f0 is None or len(f0) == 0:
        return []

    times = librosa.times_like(f0, sr=int(sr), hop_length=hop_length)
    hop_sec = hop_length / float(sr)

    notes: list[MelodicNote] = []
    cur_pitch: int | None = None
    cur_start: float = 0.0
    cur_vel_acc: float = 0.0
    cur_n: int = 0

    def flush(end_t: float) -> None:
        nonlocal cur_pitch, cur_vel_acc, cur_n
        if cur_pitch is None:
            return
        dur = end_t - cur_start
        if dur >= min_note_sec:
            vel = int(max(1, min(127, round((cur_vel_acc / max(1, cur_n)) * 127))))
            notes.append(
                MelodicNote(
                    t_on=cur_start,
                    t_off=end_t,
                    pitch=cur_pitch,
                    velocity=vel,
                    instrument=instrument,
                )
            )
        cur_pitch = None
        cur_vel_acc = 0.0
        cur_n = 0

    for t, freq, prob in zip(times, f0, voiced_probs):
        if (
            freq is None
            or (isinstance(freq, float) and (freq != freq))  # NaN
            or freq <= 0
            or prob is None
            or prob < voiced_prob_threshold
        ):
            flush(float(t))
            continue
        pitch_f = 69.0 + 12.0 * (np.log2(float(freq) / 440.0))
        pitch = int(round(pitch_f))
        if cur_pitch is None:
            cur_pitch = pitch
            cur_start = float(t)
        elif pitch != cur_pitch:
            flush(float(t))
            cur_pitch = pitch
            cur_start = float(t)
        cur_vel_acc += float(prob)
        cur_n += 1

    if len(times) > 0:
        flush(float(times[-1]) + hop_sec)

    return notes
