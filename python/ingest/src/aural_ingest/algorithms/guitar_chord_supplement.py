"""FFT-based chord-tone supplement for rhythm guitar.

Ports the piano ``piano_chord_supplement`` pattern with one
architectural change: piano's fallback fires only when the neural
base is empty for the whole stem, but rhythm guitar's neural base
(Basic Pitch) is typically *partially* right at every strum -- it
catches two or three strings out of five or six. So this supplement
*composes* with the base, adding chord tones the base missed at each
strum onset rather than firing as an all-or-nothing fallback.

Pipeline: (1) detect strum onsets via spectral flux; (2) FFT a
~200 ms window post-onset, peak-pick, snap to chromatic MIDI in E2..
E5; (3) emit any chord tone not already present within a small time
window around the onset. Never removes or duplicates. Returns a
merged time-sorted list. Missing deps -> input returned unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aural_ingest.transcription import MelodicNote


# Guitar rhythm range: low E of drop tunings up to a typical high
# barre-chord top note. Basic Pitch and CQT-based detectors both
# tolerate a couple of semitones of slack past these edges; we keep a
# small buffer to avoid clipping legitimate voicings.
_MIDI_E2 = 40
_MIDI_E5 = 76


@dataclass(frozen=True)
class GuitarChordSupplementConfig:
    """All thresholds for the guitar chord-tone supplement.

    Defaults are tuned for typical rhythm-guitar strumming with
    moderate distortion, biased toward NOT adding spurious notes.
    """

    # Onset detection. min_inter_strum: strums rarely faster than a
    # 16th at 180 BPM (~83 ms). onset_delta: stricter than piano's
    # 0.05 because guitar attacks are noisier (pick noise, string
    # ring). backtrack aligns onsets to the pre-attack minimum.
    min_inter_strum_sec: float = 0.08
    onset_delta: float = 0.07
    onset_backtrack: bool = True

    # FFT window. 200 ms captures steady-state ring; 30 ms offset
    # skips the pick-click transient. peaks must sit this many dB
    # above the median-bin floor. max_chord_tones = one per string
    # on standard tuning.
    fft_window_sec: float = 0.20
    fft_offset_sec: float = 0.03
    min_peak_db_above_floor: float = 20.0
    max_chord_tones: int = 6

    # Note-emission. match_window_sec = 50 ms is a realistic upper
    # bound for Basic Pitch's onset jitter on strums (hop 25.6 ms).
    # duration = gap to next onset clamped to [min, max]; default is
    # used for the last strum in the stem.
    default_velocity: int = 70
    match_window_sec: float = 0.05
    min_note_duration_sec: float = 0.10
    max_note_duration_sec: float = 0.50
    default_note_duration_sec: float = 0.30

    # Rhythm-guitar pitch range (E2..E5 by default).
    pitch_low: int = _MIDI_E2
    pitch_high: int = _MIDI_E5

    # Instrument tag on emitted notes.
    instrument: str = "guitar"


def _hz_to_midi(freq_hz: float) -> float:
    """Frequency in Hz -> fractional MIDI (A4 = 69). ``-1`` on non-positive."""
    import math
    if freq_hz <= 0:
        return -1.0
    return 69.0 + 12.0 * math.log2(freq_hz / 440.0)


def detect_strum_onsets(
    audio, sr: int, config: GuitarChordSupplementConfig | None = None,
) -> list[float]:
    """Return strum onset times (seconds).

    Uses ``librosa.onset.onset_detect`` (spectral-flux) which fires on
    broadband energy jumps -- the signature of a multi-string strum.
    Returns [] if deps or buffer are missing.
    """
    if config is None:
        config = GuitarChordSupplementConfig()
    try:
        import numpy as np  # noqa: F401
        import librosa
    except Exception:
        return []
    if sr <= 0 or len(audio) == 0:
        return []

    try:
        onset_frames = librosa.onset.onset_detect(
            y=audio,
            sr=sr,
            units="frames",
            backtrack=config.onset_backtrack,
            delta=config.onset_delta,
        )
    except Exception:
        return []
    if onset_frames is None or len(onset_frames) == 0:
        return []

    onset_times = librosa.frames_to_time(
        onset_frames, sr=sr, hop_length=512,
    )

    # Enforce min-inter-strum: greedily keep the earliest, drop any
    # within the min window. Handles both the "decay artefact" case
    # and rapid re-triggering by tremolo picking.
    filtered: list[float] = []
    last = -1e9
    for t in onset_times:
        t = float(t)
        if t - last >= config.min_inter_strum_sec:
            filtered.append(t)
            last = t
    return filtered


def estimate_chord_tones_at(
    audio, sr: int, onset_time: float,
    config: GuitarChordSupplementConfig | None = None,
) -> list[int]:
    """Return chord-tone MIDI pitches at ``onset_time``.

    Hann-windowed FFT of a ``fft_window_sec`` slice starting
    ``fft_offset_sec`` after the onset. Peaks above the dB threshold
    are snapped to chromatic MIDI, filtered to rhythm range, deduped,
    top-N by strength. Returns [] on missing deps, window overrun, or
    zero-energy spectrum.
    """
    if config is None:
        config = GuitarChordSupplementConfig()
    try:
        import numpy as np
    except Exception:
        return []
    if sr <= 0 or len(audio) == 0:
        return []

    fft_len = 1
    target = int(config.fft_window_sec * sr)
    while fft_len < max(2048, target):
        fft_len *= 2

    start = int(onset_time * sr) + int(config.fft_offset_sec * sr)
    end = start + fft_len
    if start < 0 or end > len(audio):
        return []
    frame = audio[start:end]
    if len(frame) < fft_len:
        return []

    window = np.hanning(len(frame))
    spectrum = np.abs(np.fft.rfft(frame * window))
    freqs = np.fft.rfftfreq(len(frame), d=1.0 / sr)
    if spectrum.max() <= 0:
        return []

    spectrum_db = 20.0 * np.log10(spectrum / spectrum.max() + 1e-9)
    median_db = float(np.median(spectrum_db))
    threshold_db = median_db + config.min_peak_db_above_floor

    # Local-maximum scan with a small lobe-skip. We keep everything
    # above the threshold and let the pitch-snapping stage dedupe.
    peaks: list[tuple[float, int]] = []  # (db, midi_pitch)
    lo_hz = 440.0 * (2 ** ((config.pitch_low - 69) / 12.0)) * 0.94
    hi_hz = 440.0 * (2 ** ((config.pitch_high - 69) / 12.0)) * 1.06
    i = 1
    n = len(spectrum_db) - 1
    while i < n:
        db = float(spectrum_db[i])
        if (
            db >= threshold_db
            and db > spectrum_db[i - 1]
            and db > spectrum_db[i + 1]
        ):
            f = float(freqs[i])
            if lo_hz <= f <= hi_hz:
                midi_f = _hz_to_midi(f)
                pitch = int(round(midi_f))
                if config.pitch_low <= pitch <= config.pitch_high:
                    peaks.append((db, pitch))
            i += 3  # skip past the lobe
        else:
            i += 1

    if not peaks:
        return []

    # Sort peaks by descending dB, take top-N unique pitches.
    peaks.sort(key=lambda p: -p[0])
    picked: list[int] = []
    seen: set[int] = set()
    for _, pitch in peaks:
        if pitch in seen:
            continue
        seen.add(pitch)
        picked.append(pitch)
        if len(picked) >= config.max_chord_tones:
            break

    picked.sort()
    return picked


def _find_existing_pitches(
    notes: list[MelodicNote], onset_time: float, window_sec: float,
) -> set[int]:
    """Return pitches with onsets in +/- ``window_sec`` of ``onset_time``."""
    lo = onset_time - window_sec
    hi = onset_time + window_sec
    return {n.pitch for n in notes if lo <= n.t_on <= hi}


def supplement_rhythm_guitar(
    notes: list[MelodicNote],
    stem_path: Path,
    *,
    config: GuitarChordSupplementConfig | None = None,
) -> list[MelodicNote]:
    """Add FFT-supported chord tones to an existing note stream.

    ``notes`` is the neural-base output (e.g. Basic Pitch). Loads
    ``stem_path``, detects strum onsets, adds whichever chord tones
    the base missed at each onset. Never removes or duplicates.
    Returns a merged time-sorted list. If audio can't be loaded or
    DSP deps are missing the input is returned unchanged.
    """
    if config is None:
        config = GuitarChordSupplementConfig()
    try:
        import librosa
    except Exception:
        return list(notes)

    try:
        y, sr = librosa.load(str(stem_path), sr=None, mono=True)
    except Exception:
        return list(notes)
    if sr <= 0 or len(y) == 0:
        return list(notes)

    onsets = detect_strum_onsets(y, sr, config)
    if not onsets:
        return list(notes)

    added: list[MelodicNote] = []
    for i, t_on in enumerate(onsets):
        tones = estimate_chord_tones_at(y, sr, t_on, config)
        if not tones:
            continue

        existing = _find_existing_pitches(
            notes + added,  # include just-added tones so we don't re-emit
            t_on,
            config.match_window_sec,
        )
        missing = [p for p in tones if p not in existing]
        if not missing:
            continue

        # Gap to next onset drives duration; clamp for sanity.
        if i + 1 < len(onsets):
            gap = onsets[i + 1] - t_on
        else:
            gap = config.default_note_duration_sec
        dur = min(
            config.max_note_duration_sec,
            max(config.min_note_duration_sec, gap),
        )
        for pitch in missing:
            added.append(
                MelodicNote(
                    t_on=float(t_on),
                    t_off=float(t_on + dur),
                    pitch=int(pitch),
                    velocity=int(config.default_velocity),
                    instrument=config.instrument,
                )
            )

    merged = list(notes) + added
    merged.sort(key=lambda n: (n.t_on, n.pitch))
    return merged
