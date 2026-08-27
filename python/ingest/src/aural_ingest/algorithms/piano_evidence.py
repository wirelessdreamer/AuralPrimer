"""Audio evidence for transcribed notes: NNLS harmonic-template unmixing.

A note that is really an overtone of a lower note has no energy of its own
— every partial it "explains" is already explained by the note below it.
Fitting a non-negative mixture of per-pitch harmonic combs to the observed
CQT magnitude just after an attack separates the two cases: the artifact's
fitted amplitude collapses to zero while the real note's does not.

This works without velocity, which is the point. MuScriptor emits a
constant velocity for every note (100), so every velocity-based overtone
heuristic in :mod:`aural_ingest.algorithms.piano_cleanup` is a no-op on
exactly the packs that need it most.

Measured on the piano-midi.de packs in the library (notes.mid is the
source of the rendered mix, so it is ground truth), with synthetic
overtone artifacts injected at the octave/fifth/twelfth/double-octave/
seventeenth/nineteenth:

===================  ====  ==========  =============
pack                 AUC   artifacts   real notes
                           flagged     wrongly flagged
===================  ====  ==========  =============
bach__bach_846       0.97  91.9%       4.0%
schubert__schuim-3   0.98  74.4%       1.7%
mozart__mz_331_3     0.88  82.7%       13.9%
chopin__chpn-p15     0.88  79.2%       14.7%
beethoven__elise     0.85  75.6%       16.8%
===================  ====  ==========  =============

The spread tracks how well each rendered mix is synced to its MIDI, not
the method: the two packs whose audio lines up cleanly lose 2-4% of real
notes, the rubato renders that drift lose 14-17%. :meth:`AudioEvidence.calibrate`
recovers a constant offset but not drift. **Because of that tail this
score must never be a standalone delete rule** — it is a ranking prior,
and the only place it deletes on its own is when it agrees with a
structural overtone signature (see
:func:`aural_ingest.algorithms.piano_playability.prune_overtone_shadows`).

Optional dependency: librosa + scipy. :func:`available` reports whether
they imported; when they did not, callers get ``None`` and fall back to
the harmony/register scoring path.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Iterable, Sequence

LOGGER = logging.getLogger(__name__)

try:  # pragma: no cover - exercised by the availability check
    import numpy as np
    import librosa
    from scipy.optimize import nnls

    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover
    np = None  # type: ignore[assignment]
    librosa = None  # type: ignore[assignment]
    nnls = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc


SR = 22_050
BINS_PER_OCTAVE = 36          # three CQT bins per semitone
N_OCTAVES = 7                 # C1..C8
HOP_LENGTH = 256              # 11.6 ms
N_HARMONICS = 8
HARMONIC_DECAY = 0.8          # partial amplitude ~ h ** -HARMONIC_DECAY
BIN_SIGMA = 0.9               # template partial width, in CQT bins
ATTACK_WINDOW_SEC = 0.09
PRE_ATTACK_SEC = 0.02


def available() -> bool:
    """Whether the optional audio dependencies imported."""
    return _IMPORT_ERROR is None


class AudioEvidence:
    """CQT of one or more stems, with per-pitch harmonic unmixing."""

    def __init__(self, wav_paths: Sequence[Path], *, sr: int = SR) -> None:
        if not available():  # pragma: no cover - guarded by callers
            raise RuntimeError(f"audio evidence unavailable: {_IMPORT_ERROR}")
        signal = None
        for path in wav_paths:
            part, _ = librosa.load(str(path), sr=sr, mono=True)
            if signal is None:
                signal = part
            else:
                n = min(len(signal), len(part))
                signal = signal[:n] + part[:n]
        if signal is None or not len(signal):
            raise ValueError("no audio loaded")
        self.sr = sr
        self.offset = 0.0
        self.fmin = float(librosa.note_to_hz("C1"))
        spectrum = librosa.cqt(
            signal,
            sr=sr,
            hop_length=HOP_LENGTH,
            fmin=self.fmin,
            n_bins=BINS_PER_OCTAVE * N_OCTAVES,
            bins_per_octave=BINS_PER_OCTAVE,
        )
        self.magnitude = np.abs(spectrum).astype(np.float64)
        self.times = librosa.frames_to_time(
            np.arange(self.magnitude.shape[1]), sr=sr, hop_length=HOP_LENGTH
        )
        self._templates: dict[int, "np.ndarray"] = {}

    # -- templates --------------------------------------------------------
    def template(self, pitch: int) -> "np.ndarray":
        cached = self._templates.get(pitch)
        if cached is not None:
            return cached
        n_bins = self.magnitude.shape[0]
        f0 = 440.0 * 2 ** ((int(pitch) - 69) / 12.0)
        column = np.zeros(n_bins)
        index = np.arange(n_bins)
        for harmonic in range(1, N_HARMONICS + 1):
            freq = f0 * harmonic
            if freq <= self.fmin or freq >= self.sr / 2:
                continue
            centre = BINS_PER_OCTAVE * math.log2(freq / self.fmin)
            if centre < -3 or centre > n_bins + 3:
                continue
            column += (harmonic ** -HARMONIC_DECAY) * np.exp(
                -0.5 * ((index - centre) / BIN_SIGMA) ** 2
            )
        norm = float(np.linalg.norm(column))
        result = column / norm if norm > 0 else column
        self._templates[pitch] = result
        return result

    # -- observation ------------------------------------------------------
    def frame(self, t: float, *, window_sec: float = ATTACK_WINDOW_SEC) -> "np.ndarray":
        start = t + self.offset - PRE_ATTACK_SEC
        stop = t + self.offset + window_sec
        i0 = min(int(np.searchsorted(self.times, start)), self.magnitude.shape[1] - 1)
        i1 = min(max(int(np.searchsorted(self.times, stop)), i0 + 1), self.magnitude.shape[1])
        return self.magnitude[:, i0:i1].max(axis=1)

    # -- fit --------------------------------------------------------------
    def fit(
        self,
        pitches: Iterable[int],
        t: float,
        *,
        window_sec: float = ATTACK_WINDOW_SEC,
    ) -> tuple[dict[int, float], float]:
        """Fit harmonic combs for ``pitches`` at time ``t``.

        Returns ``({pitch: relative amplitude in [0, 1]}, residual fraction)``.
        Amplitudes are normalised by the largest in the group so the score is
        comparable across quiet and loud passages.
        """
        unique = list(dict.fromkeys(int(p) for p in pitches))
        observed = self.frame(t, window_sec=window_sec)
        norm = float(np.linalg.norm(observed))
        if norm <= 1e-9 or not unique:
            return {p: 0.0 for p in unique}, 1.0
        dictionary = np.stack([self.template(p) for p in unique], axis=1)
        amplitudes, _ = nnls(dictionary, observed)
        residual = float(np.linalg.norm(observed - dictionary @ amplitudes)) / norm
        peak = float(amplitudes.max()) if amplitudes.size else 0.0
        if peak <= 0.0:
            return {p: 0.0 for p in unique}, residual
        return {p: float(a / peak) for p, a in zip(unique, amplitudes)}, residual

    # -- self-calibration -------------------------------------------------
    def calibrate(
        self,
        groups: Sequence[Sequence[object]],
        *,
        span_sec: float = 0.25,
        step_sec: float = 0.02,
        sample: int = 200,
    ) -> float:
        """Pick the chart-to-audio offset that best explains the chart itself.

        Needs no ground truth: most charted notes are real, so the offset
        that minimises total residual is the true sync. Recovers a constant
        offset only — a render that drifts in tempo will still score badly,
        which is why the evidence score is a prior and not a verdict.
        """
        import random

        candidates = [g for g in groups if len(g) >= 2]
        if not candidates:
            return 0.0
        random.Random(0).shuffle(candidates)
        candidates = candidates[:sample]
        best_offset, best_score = 0.0, float("-inf")
        steps = int(round(2 * span_sec / step_sec)) + 1
        for i in range(steps):
            self.offset = -span_sec + i * step_sec
            total = 0.0
            for group in candidates:
                t = min(float(n.t_on) for n in group)  # type: ignore[attr-defined]
                _, residual = self.fit([int(n.pitch) for n in group], t)  # type: ignore[attr-defined]
                total += 1.0 - residual
            if total > best_score:
                best_score, best_offset = total, self.offset
        self.offset = best_offset
        return best_offset


def score_notes(
    notes: Sequence[object],
    wav_paths: Sequence[Path],
    *,
    window_sec: float = 0.05,
) -> dict[int, float] | None:
    """Relative audio support per note, keyed by ``id(note)``.

    Returns ``None`` when the optional dependencies or the audio are
    missing, so callers can degrade instead of failing.
    """
    if not available():
        LOGGER.warning("piano_evidence unavailable (%s); skipping audio scoring", _IMPORT_ERROR)
        return None
    paths = [Path(p) for p in wav_paths if Path(p).exists()]
    if not paths:
        LOGGER.warning("piano_evidence: no stem audio found; skipping audio scoring")
        return None
    try:
        engine = AudioEvidence(paths)
    except Exception as exc:  # pragma: no cover
        LOGGER.warning("piano_evidence: could not analyse audio (%s)", exc)
        return None

    from aural_ingest.algorithms.piano_playability import group_by_onset

    groups = group_by_onset(notes, window_sec=window_sec)  # type: ignore[arg-type]
    engine.calibrate(groups)
    scores: dict[int, float] = {}
    for group in groups:
        t = min(float(n.t_on) for n in group)  # type: ignore[attr-defined]
        amplitudes, _ = engine.fit([int(n.pitch) for n in group], t)  # type: ignore[attr-defined]
        for note in group:
            scores[id(note)] = amplitudes.get(int(note.pitch), 0.0)  # type: ignore[attr-defined]
    return scores
