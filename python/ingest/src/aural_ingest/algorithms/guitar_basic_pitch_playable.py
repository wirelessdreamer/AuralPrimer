"""Guitar-tuned Spotify Basic Pitch adapter.

Mirrors the ``piano_basic_pitch_playable`` wrapper pattern but with
inference parameters tuned for electric + acoustic guitar and a MIDI
pitch clamp anchored to the guitar range instead of the piano range.

Tuning rationale (starting point; will be validated by the benchmark):

* ``onset_threshold=0.4`` -- Basic Pitch's ICASSP 2022 default is 0.5,
  which is picked to be conservative on piano hammer-strike attacks.
  Guitar plucks (and especially slurs / hammer-ons) have softer onset
  transients, so lowering the threshold recovers legato / lead runs
  that the piano defaults miss.
* ``frame_threshold=0.25`` -- slightly below the ICASSP default of 0.3
  so that decaying guitar sustain doesn't get truncated mid-note.
* ``minimum_note_length_ms=50`` -- short enough for fast alternate-
  picking lead runs (a 16th at 180 bpm is ~83 ms; single-note fills
  can be shorter).
* Pitch clamp: E2 (MIDI 40) to E6 (MIDI 88) for electric; E2 to E5
  (MIDI 76) is closer to acoustic. Both bounds are overridable via
  ``AURAL_GUITAR_MIN_MIDI`` / ``AURAL_GUITAR_MAX_MIDI``.

The wider Basic Pitch ``INSTRUMENT_FREQ_RANGES["guitar"]`` (75-1400
Hz, MIDI 32-88ish) is still what gets fed into Basic Pitch's own
prediction path via ``melodic_basic_pitch.transcribe``; our MIDI
clamp is a post-filter so that we can tighten the pitch range
independent of Basic Pitch's continuous-frequency inference bounds.

Model-absent behaviour matches the piano wrapper: if Basic Pitch is
not installed / the ONNX model isn't downloaded, we return an empty
list and log a warning instead of crashing. Discovery for the benchmark
runner is via ``importlib`` on the module name (see
``ground_truth_benchmark.get_melodic_algorithm``), and the algorithm is
also registered on the melodic algorithm registry so that
``KNOWN_MELODIC_METHODS`` accepts it.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from aural_ingest.algorithms import melodic_basic_pitch
from aural_ingest.transcription import MelodicNote


LOGGER = logging.getLogger(__name__)


# Default guitar range, tuned for electric guitar.
# E2 = MIDI 40 (open low-E on standard-tuned electric/acoustic).
# E6 = MIDI 88 (approx. highest fretted note reachable on a 24-fret
# electric high-E string — 22-fret guitars top out at MIDI 86).
GUITAR_DEFAULT_MIN_MIDI = 40
GUITAR_DEFAULT_MAX_MIDI = 88

# Guitar Basic Pitch tuning defaults. Justification in the module docstring.
GUITAR_BASIC_PITCH_ONSET_THRESHOLD = 0.4
GUITAR_BASIC_PITCH_FRAME_THRESHOLD = 0.25
GUITAR_BASIC_PITCH_MIN_NOTE_LENGTH_MS = 50.0

_MODEL_ABSENT_WARNED = False


def _read_midi_env(var_name: str, default: int) -> int:
    """Read a MIDI clamp bound from an env var, falling back to ``default``.

    Silently ignores non-integer values and out-of-MIDI-range (0..127)
    values instead of raising so that a misconfigured shell doesn't
    take down the whole ingest pipeline.
    """
    raw = os.environ.get(var_name)
    if raw is None or raw.strip() == "":
        return int(default)
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        LOGGER.warning(
            "%s=%r is not a valid MIDI integer; using default %d",
            var_name,
            raw,
            default,
        )
        return int(default)
    if value < 0 or value > 127:
        LOGGER.warning(
            "%s=%d out of MIDI range 0..127; using default %d",
            var_name,
            value,
            default,
        )
        return int(default)
    return value


def _guitar_midi_range() -> tuple[int, int]:
    """Resolve (min_midi, max_midi) for the guitar pitch clamp.

    Env-var overrides:
      * ``AURAL_GUITAR_MIN_MIDI``
      * ``AURAL_GUITAR_MAX_MIDI``

    If min > max after resolution, falls back to the pinned defaults
    (this happens with e.g. a swapped-var typo).
    """
    lo = _read_midi_env("AURAL_GUITAR_MIN_MIDI", GUITAR_DEFAULT_MIN_MIDI)
    hi = _read_midi_env("AURAL_GUITAR_MAX_MIDI", GUITAR_DEFAULT_MAX_MIDI)
    if lo > hi:
        LOGGER.warning(
            "AURAL_GUITAR_MIN_MIDI=%d > AURAL_GUITAR_MAX_MIDI=%d; "
            "reverting to default range %d..%d",
            lo,
            hi,
            GUITAR_DEFAULT_MIN_MIDI,
            GUITAR_DEFAULT_MAX_MIDI,
        )
        return GUITAR_DEFAULT_MIN_MIDI, GUITAR_DEFAULT_MAX_MIDI
    return lo, hi


def _basic_pitch_model_available() -> bool:
    """Detect whether Basic Pitch's ONNX model can be loaded.

    We keep this cheap: import ``basic_pitch`` and check that the
    packaged ``ICASSP_2022_MODEL_PATH`` exists. This avoids paying the
    ONNX model-load cost just to decide whether to warn.
    """
    try:
        from basic_pitch import ICASSP_2022_MODEL_PATH
    except Exception:
        return False
    try:
        return Path(str(ICASSP_2022_MODEL_PATH)).exists()
    except Exception:
        return False


def _warn_model_absent_once() -> None:
    global _MODEL_ABSENT_WARNED
    if _MODEL_ABSENT_WARNED:
        return
    LOGGER.warning(
        "basic_pitch is not installed or its ONNX model is missing; "
        "guitar_basic_pitch_playable will return an empty note list. "
        "Install basic_pitch and download the ICASSP 2022 model to enable."
    )
    _MODEL_ABSENT_WARNED = True


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "guitar",
    onset_threshold: float = GUITAR_BASIC_PITCH_ONSET_THRESHOLD,
    frame_threshold: float = GUITAR_BASIC_PITCH_FRAME_THRESHOLD,
    minimum_note_length_ms: float = GUITAR_BASIC_PITCH_MIN_NOTE_LENGTH_MS,
    min_midi: int | None = None,
    max_midi: int | None = None,
    **_kwargs,
) -> list[MelodicNote]:
    """Guitar-tuned Basic Pitch transcription.

    Args:
        stem_path: Absolute path to a guitar stem (mono or stereo WAV).
        instrument: Instrument label to attach to emitted notes. Defaults
            to ``"guitar"`` so downstream tooling (e.g. MIDI writers)
            routes the notes to the right track.
        onset_threshold: Basic Pitch onset detection threshold.
        frame_threshold: Basic Pitch frame activation threshold.
        minimum_note_length_ms: Minimum accepted note length in ms.
        min_midi: MIDI pitch clamp lower bound (inclusive). Falls back to
            ``AURAL_GUITAR_MIN_MIDI`` env var, then to E2 (MIDI 40).
        max_midi: MIDI pitch clamp upper bound (inclusive). Falls back to
            ``AURAL_GUITAR_MAX_MIDI`` env var, then to E6 (MIDI 88).

    Returns:
        A list of ``MelodicNote`` sorted by ``(t_on, pitch, t_off)`` and
        filtered to the resolved MIDI range. Returns ``[]`` if Basic
        Pitch is unavailable (model or package missing) instead of
        raising, matching the piano wrapper's behavior.
    """
    if min_midi is None or max_midi is None:
        env_lo, env_hi = _guitar_midi_range()
        if min_midi is None:
            min_midi = env_lo
        if max_midi is None:
            max_midi = env_hi
    lo = int(min_midi)
    hi = int(max_midi)
    if lo > hi:
        lo, hi = GUITAR_DEFAULT_MIN_MIDI, GUITAR_DEFAULT_MAX_MIDI

    if not _basic_pitch_model_available():
        _warn_model_absent_once()
        return []

    try:
        notes = melodic_basic_pitch.transcribe(
            stem_path,
            instrument=instrument,
            allow_fallback=False,
            onset_threshold=float(onset_threshold),
            frame_threshold=float(frame_threshold),
            minimum_note_length_ms=float(minimum_note_length_ms),
        )
    except Exception as exc:
        LOGGER.warning(
            "guitar_basic_pitch_playable: basic_pitch inference failed on %s: %s",
            stem_path,
            exc,
        )
        return []

    clamped = [note for note in notes if lo <= note.pitch <= hi]
    clamped.sort(key=lambda note: (note.t_on, note.pitch, note.t_off))
    return clamped
