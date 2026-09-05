"""Measure how far a render sits behind the score it was rendered from.

A studio bounce is captured through a DAW's transport, and the moment the
capture starts is not the moment the music starts. In the classical set the
gap came out between 5.04 and 5.50 seconds -- close enough to look like a
fixed count-in, variable enough to prove it is not one. It is the latency
between "start recording" and "fire the clip", and it lands wherever the two
round-trips happen to land on the day.

That gap is silent, so nothing about the audio looks wrong. What goes wrong is
downstream: the chart says play at 0.9s, the recording plays at 6.0s, and the
practice session is five seconds ahead of what it hears.

Estimating it as a constant does not work, for the reason above. Measuring it
per render does, because we have both sides -- the audio and the exact note
times it was rendered from. Correlating an onset envelope against the score's
onset train finds the lag directly, and on synthetic renders the peak is
sharp: 12 to 47 times the median across this set.

The one thing this cannot do is align perfectly periodic material. If every
note is evenly spaced, a shift of exactly one note explains the audio as well
as no shift at all, and no amount of correlation will separate them -- the
information is not there. Real scores have enough irregularity that the true
peak dominates; a metronomic sequence would need its offset recorded at render
time instead.

Accuracy against an independent measure (where the first audible sample sits
relative to the first scored note) is within 42ms across the classical set,
with most of the residual being the onset envelope's own group delay.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: Analysis rate for the coarse search. The onset envelope does not need the
#: full bandwidth and the correlation is O(lag x frames).
_SR = 22050
_HOP = 256

#: How far ahead of the score the audio may start. A transport race is seconds;
#: anything past this is not a lead-in, it is the wrong file.
MAX_LEAD_SEC = 30.0

#: Below this the correlation peak is not distinct enough to act on. Values on
#: real renders run 4.8x to 46.6x, so this rejects noise without being tight.
MIN_CONFIDENCE = 3.0

#: The onset envelope peaks slightly AFTER the attack that caused it -- it is a
#: spectral flux, so energy has to have already risen for it to register. That
#: makes every measurement late by a fixed amount, which would be trimmed off
#: the front of the audio as if it were lead-in.
#:
#: Measured on synthetic renders whose lead-in is known exactly, it is +23.6ms
#: with a 0.9ms spread across lead-ins from 0 to 7.3 seconds. It is a property
#: of the hop and window above, not of the music, so it is a constant here
#: rather than something fitted per piece.
_ENVELOPE_GROUP_DELAY_SEC = 0.0236

@dataclass
class Alignment:
    lag_sec: float
    confidence: float
    ok: bool
    reason: str | None = None


def _onset_envelope(wav: Path, seconds: float):
    import librosa

    y, _ = librosa.load(str(wav), sr=_SR, mono=True, duration=seconds)
    env = librosa.onset.onset_strength(y=y, sr=_SR, hop_length=_HOP)
    return (env - env.mean()) / (env.std() + 1e-9)


def _onset_train(onsets, frames):
    import numpy as np

    train = np.zeros(frames, dtype=float)
    for t in onsets:
        f = int(t * _SR / _HOP)
        if 0 <= f < frames:
            train[f] += 1.0
    if train.sum() == 0:
        return None
    return (train - train.mean()) / (train.std() + 1e-9)


def measure_lead_in(
    audio: str | Path,
    onsets: list[float],
    *,
    window_sec: float = 90.0,
) -> Alignment:
    """Seconds the render sits behind the score, by onset cross-correlation.

    Only the opening ``window_sec`` is used. The lead-in is a constant shift of
    the whole file, so the beginning is enough to find it, and a rubato piece
    correlates best where the two are still closest together.
    """
    import numpy as np

    audio = Path(audio)
    env = _onset_envelope(audio, window_sec)
    train = _onset_train([t for t in onsets if t < window_sec], len(env))
    if train is None or len(env) < 8:
        return Alignment(0.0, 0.0, False, "not enough onsets to correlate")

    max_lag = min(int(MAX_LEAD_SEC * _SR / _HOP), len(env) - 1)
    corr = np.array([float(np.dot(env[lag:], train[: len(train) - lag]))
                     for lag in range(max_lag)])
    # Strongest peak wins, with no preference for earlier candidates.
    #
    # Preferring the earliest near-tied peak looks like the right way to break
    # the periodic ambiguity -- a false match is always a whole period late --
    # but measured against the audio it makes things worse, and worst on
    # exactly the piece it was meant to help: Bach's prelude is uniform
    # semiquavers, and biasing towards early moved it 360ms off. Plain argmax
    # lands within 42ms across the set.
    best = int(np.argmax(corr))
    confidence = float(corr[best] / (np.median(np.abs(corr)) + 1e-9))

    # Parabolic interpolation between frames: the true lag rarely lands on a
    # frame boundary, and a hop is 11.6ms of avoidable error.
    lag_frames = float(best)
    if 0 < best < len(corr) - 1:
        a, b, c = corr[best - 1], corr[best], corr[best + 1]
        denom = a - 2 * b + c
        if denom != 0:
            lag_frames += 0.5 * (a - c) / denom

    # Never negative: the correction is a bias, not a licence to shift audio
    # earlier than the score.
    lag = max(0.0, lag_frames * _HOP / _SR - _ENVELOPE_GROUP_DELAY_SEC)
    if confidence < MIN_CONFIDENCE:
        return Alignment(lag, confidence, False,
                         f"correlation peak is only {confidence:.1f}x the median; "
                         "the render may not be of this score")
    return Alignment(lag, confidence, True)


def trim_lead_in(src: str | Path, dst: str | Path, lag_sec: float) -> float:
    """Copy ``src`` to ``dst`` with ``lag_sec`` removed from the head."""
    import soundfile as sf

    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with sf.SoundFile(str(src)) as f:
        start = max(0, int(round(lag_sec * f.samplerate)))
        f.seek(start)
        data = f.read(dtype="float32", always_2d=True)
        sf.write(str(dst), data, f.samplerate, subtype=f.subtype)
        return start / f.samplerate
