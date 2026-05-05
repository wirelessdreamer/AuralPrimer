"""Polyphonic piano heuristic transcription.

This is a piano-focused framewise salience model rather than a generic
single-note melodic tracker. It estimates note salience across the full 88-key
range, keeps multiple concurrent notes active, and uses per-note hysteresis so
chords and repeated notes survive long enough to sound playable.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

from aural_ingest.algorithms._common import read_wav_mono_normalized
from aural_ingest.algorithms.melodic_onset_yin import _detect_onsets
from aural_ingest.transcription import MelodicNote

try:
    import numpy as np
except ImportError:
    np = None

try:
    import librosa as _librosa
except ImportError:
    _librosa = None


_PIANO_MIN_MIDI = 21
_PIANO_MAX_MIDI = 108
_HARMONIC_WEIGHTS = (1.0, 0.82, 0.58, 0.41, 0.27, 0.18)
_ATTACK_CANDIDATE_FRAME_RADIUS = 1
_ATTACK_CANDIDATE_SCORE_FLOOR = 1.0
_ATTACK_CANDIDATE_TOP_RATIO = 0.7
_ATTACK_CANDIDATE_FUND_RATIO = 0.3


def _midi_to_freq(midi: int) -> float:
    return 440.0 * (2.0 ** ((float(midi) - 69.0) / 12.0))


def _next_pow_two(value: int) -> int:
    n = 1
    while n < value:
        n <<= 1
    return n


def _compute_note_windows(sr: int, frame_size: int) -> tuple[list[int], list[list[tuple[int, int, float]]]]:
    nyquist = sr * 0.5
    max_bin = frame_size // 2
    midi_values: list[int] = []
    note_windows: list[list[tuple[int, int, float]]] = []

    for midi in range(_PIANO_MIN_MIDI, _PIANO_MAX_MIDI + 1):
        f0 = _midi_to_freq(midi)
        if f0 >= nyquist * 0.98:
            continue

        harmonic_windows: list[tuple[int, int, float]] = []
        for harmonic_index, weight in enumerate(_HARMONIC_WEIGHTS, start=1):
            harmonic_freq = f0 * float(harmonic_index)
            if harmonic_freq >= nyquist * 0.98:
                break
            bin_index = int(round(harmonic_freq * frame_size / float(sr)))
            if bin_index <= 0 or bin_index > max_bin:
                continue

            spread = 2 if harmonic_freq < 90.0 else 1
            lo = max(1, bin_index - spread)
            hi = min(max_bin, bin_index + spread)
            harmonic_windows.append((lo, hi, weight))

        if harmonic_windows:
            midi_values.append(midi)
            note_windows.append(harmonic_windows)

    return midi_values, note_windows


def _attack_neighbor_frames(attack_frames: set[int], frame_count: int) -> set[int]:
    out: set[int] = set()
    for frame_index in attack_frames:
        for delta in range(-_ATTACK_CANDIDATE_FRAME_RADIUS, _ATTACK_CANDIDATE_FRAME_RADIUS + 1):
            candidate = int(frame_index) + delta
            if 0 <= candidate < frame_count:
                out.add(candidate)
    return out


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "keys",
    frame_sec: float = 0.085,
    hop_ratio: float = 0.125,
    min_note_sec: float = 0.06,
    max_polyphony: int = 6,
) -> list[MelodicNote]:
    if np is None:
        raise RuntimeError("piano_polyphonic requires numpy for framewise salience analysis")

    samples, sr = read_wav_mono_normalized(stem_path)
    if not samples or sr <= 0:
        return []

    signal = np.asarray(samples, dtype=np.float32)
    use_hpss = os.environ.get("AURAL_PIANO_POLYPHONIC_HPSS", "").strip().lower() in {"1", "true", "yes"}
    if use_hpss and _librosa is not None:
        try:
            harmonic, _percussive = _librosa.effects.hpss(signal)
            signal = harmonic.astype(np.float32, copy=False)
        except Exception:
            pass

    target_frame = max(1024, int(sr * max(0.05, frame_sec)))
    frame_size = min(4096, _next_pow_two(target_frame))
    hop_size = max(128, int(frame_size * max(0.0625, min(0.25, hop_ratio))))
    if signal.shape[0] < frame_size:
        return []

    window = np.hanning(frame_size).astype(np.float32)
    starts = range(0, int(signal.shape[0]) - frame_size + 1, hop_size)

    spectra_rows: list[np.ndarray] = []
    rms_rows: list[float] = []
    for start in starts:
        segment = signal[start : start + frame_size]
        rms_rows.append(float(np.sqrt(np.mean(segment * segment))))
        magnitude = np.abs(np.fft.rfft(segment * window))
        spectra_rows.append(np.sqrt(np.maximum(magnitude, 0.0)))

    if not spectra_rows:
        return []

    spectra = np.stack(spectra_rows, axis=0).astype(np.float32, copy=False)
    rms = np.asarray(rms_rows, dtype=np.float32)
    frame_times = (np.arange(spectra.shape[0], dtype=np.float32) * float(hop_size)) / float(sr)

    midi_values, note_windows = _compute_note_windows(sr, frame_size)
    if not midi_values:
        return []

    frame_count = spectra.shape[0]
    note_count = len(midi_values)
    salience = np.zeros((frame_count, note_count), dtype=np.float32)
    fundamental = np.zeros((frame_count, note_count), dtype=np.float32)

    for note_index, harmonic_windows in enumerate(note_windows):
        for harmonic_index, (lo, hi, weight) in enumerate(harmonic_windows):
            band_energy = np.max(spectra[:, lo : hi + 1], axis=1)
            salience[:, note_index] += band_energy * float(weight)
            if harmonic_index == 0:
                fundamental[:, note_index] = band_energy

    if float(np.max(salience)) <= 0.0:
        return []

    global_peak = max(float(np.percentile(salience, 99.5)), 1e-5)
    note_high = np.percentile(salience, 98.0, axis=0)
    note_low = np.percentile(salience, 70.0, axis=0)
    note_span = np.maximum(note_high - note_low, global_peak * 0.015)
    score = np.clip((salience - note_low[None, :]) / note_span[None, :], 0.0, 2.0)

    inactive_notes = note_high < max(global_peak * 0.025, 1e-4)
    if inactive_notes.any():
        score[:, inactive_notes] = 0.0
        salience[:, inactive_notes] = 0.0
        fundamental[:, inactive_notes] = 0.0

    fund_ratio = fundamental / np.maximum(salience, 1e-8)
    fund_bonus = 0.72 + (0.28 * np.clip(fund_ratio / 0.24, 0.0, 1.25))
    candidate_score = score * fund_bonus

    candidate_mask = candidate_score >= 0.44
    if note_count > 1:
        candidate_mask[:, 0] &= candidate_score[:, 0] >= (candidate_score[:, 1] * 1.05)
        candidate_mask[:, -1] &= candidate_score[:, -1] >= (candidate_score[:, -2] * 1.05)
    if note_count > 2:
        center = candidate_mask[:, 1:-1]
        center &= candidate_score[:, 1:-1] >= (candidate_score[:, :-2] * 1.05)
        center &= candidate_score[:, 1:-1] >= (candidate_score[:, 2:] * 1.05)
        candidate_mask[:, 1:-1] = center

    for note_index in range(12, note_count):
        lower_index = note_index - 12
        octave_shadow = (
            (candidate_score[:, lower_index] >= (candidate_score[:, note_index] * 0.92))
            & (fund_ratio[:, note_index] < 0.18)
            & (fundamental[:, note_index] < (fundamental[:, lower_index] * 0.75))
        )
        candidate_mask[:, note_index] &= ~octave_shadow

    trimmed_candidates = np.zeros_like(candidate_mask)
    for frame_index in range(frame_count):
        frame_candidates = np.flatnonzero(candidate_mask[frame_index])
        if frame_candidates.size == 0:
            continue
        order = frame_candidates[np.argsort(candidate_score[frame_index, frame_candidates])[::-1]]
        order = order[:max_polyphony]
        top_score = float(candidate_score[frame_index, order[0]])
        floor = max(0.34, top_score * 0.45)
        keep = order[candidate_score[frame_index, order] >= floor]
        trimmed_candidates[frame_index, keep] = True
    candidate_mask = trimmed_candidates

    rms_onsets = _detect_onsets(rms.tolist(), ratio_threshold=2.0, median_window=5)
    flux = np.concatenate(
        (
            np.zeros(1, dtype=np.float32),
            np.sum(np.maximum(0.0, spectra[1:] - spectra[:-1]), axis=1, dtype=np.float32),
        ),
        axis=0,
    )
    flux_onsets = _detect_onsets(flux.tolist(), ratio_threshold=1.8, median_window=5)
    attack_frames = rms_onsets | flux_onsets
    attack_neighbor_frames = _attack_neighbor_frames(attack_frames, frame_count)
    base_candidate_mask = candidate_mask.copy()

    for frame_index in sorted(attack_neighbor_frames):
        frame_scores = candidate_score[frame_index]
        top_score = float(np.max(frame_scores))
        if top_score < _ATTACK_CANDIDATE_SCORE_FLOOR:
            continue
        floor = max(_ATTACK_CANDIDATE_SCORE_FLOOR, top_score * _ATTACK_CANDIDATE_TOP_RATIO)
        attack_candidates = np.flatnonzero(
            (frame_scores >= floor)
            & (
                (fund_ratio[frame_index] >= _ATTACK_CANDIDATE_FUND_RATIO)
                | (frame_scores >= max(0.72, top_score * 0.58))
            )
        )
        if attack_candidates.size == 0:
            continue
        order = attack_candidates[np.argsort(frame_scores[attack_candidates])[::-1]]
        base_candidates = np.flatnonzero(base_candidate_mask[frame_index])
        if base_candidates.size >= max_polyphony:
            continue
        added = [int(index) for index in order if not base_candidate_mask[frame_index, int(index)]]
        if not added:
            continue
        remaining = max_polyphony - int(base_candidates.size)
        candidate_mask[frame_index, added[:remaining]] = True

    rms_floor = float(np.percentile(rms, 25.0))
    rms_span = max(float(np.percentile(rms, 95.0)) - rms_floor, 1e-6)
    rms_unit = np.clip((rms - rms_floor) / rms_span, 0.0, 1.5)

    min_frames = max(2, int(math.ceil((min_note_sec * sr) / float(hop_size))))
    retrigger_frames = max(3, int(math.ceil((0.085 * sr) / float(hop_size))))

    active = [False] * note_count
    start_frame = [-1] * note_count
    onset_strength = [0.0] * note_count
    peak_strength = [0.0] * note_count
    start_rms = [0.0] * note_count
    sum_strength = [0.0] * note_count
    frame_counter = [0] * note_count
    out: list[MelodicNote] = []

    hop_sec = float(hop_size) / float(sr)

    def start_note(note_index: int, frame_index: int) -> None:
        active[note_index] = True
        start_frame[note_index] = frame_index
        onset_strength[note_index] = float(candidate_score[frame_index, note_index])
        peak_strength[note_index] = float(candidate_score[frame_index, note_index])
        start_rms[note_index] = float(rms_unit[frame_index])
        sum_strength[note_index] = float(candidate_score[frame_index, note_index])
        frame_counter[note_index] = 1

    def flush_note(note_index: int, frame_index: int) -> None:
        if not active[note_index] or start_frame[note_index] < 0:
            active[note_index] = False
            start_frame[note_index] = -1
            return

        frame_length = frame_index - start_frame[note_index]
        t_on = float(frame_times[start_frame[note_index]])
        t_off = float(frame_times[min(frame_index, frame_count - 1)]) + hop_sec
        if frame_length >= min_frames and t_off > t_on:
            mean_strength = sum_strength[note_index] / max(1, frame_counter[note_index])
            intensity = max(onset_strength[note_index], peak_strength[note_index] * 0.92, mean_strength)
            velocity = int(round(28.0 + (46.0 * min(1.4, intensity)) + (24.0 * start_rms[note_index])))
            out.append(
                MelodicNote(
                    t_on=round(t_on, 6),
                    t_off=round(t_off, 6),
                    pitch=int(midi_values[note_index]),
                    velocity=max(24, min(127, velocity)),
                    instrument=instrument,
                )
            )

        active[note_index] = False
        start_frame[note_index] = -1
        onset_strength[note_index] = 0.0
        peak_strength[note_index] = 0.0
        start_rms[note_index] = 0.0
        sum_strength[note_index] = 0.0
        frame_counter[note_index] = 0

    for frame_index in range(frame_count):
        attack_frame = frame_index in attack_neighbor_frames
        for note_index in range(note_count):
            current_score = float(candidate_score[frame_index, note_index])
            previous_score = float(candidate_score[frame_index - 1, note_index]) if frame_index > 0 else 0.0
            score_delta = current_score - previous_score
            is_candidate = bool(candidate_mask[frame_index, note_index])
            sustain_ok = current_score >= 0.22 or (active[note_index] and current_score >= 0.14)
            strong_attack = is_candidate and (
                attack_frame or score_delta >= 0.12 or float(fund_ratio[frame_index, note_index]) >= 0.24
            )

            if not active[note_index]:
                if (is_candidate and current_score >= 0.52 and strong_attack) or current_score >= 0.92:
                    start_note(note_index, frame_index)
                continue

            peak_strength[note_index] = max(peak_strength[note_index], current_score)

            note_age = frame_index - start_frame[note_index]
            retrigger = (
                is_candidate
                and strong_attack
                and note_age >= retrigger_frames
                and (current_score >= (peak_strength[note_index] * 0.58) or score_delta >= 0.2)
            )
            if retrigger:
                flush_note(note_index, frame_index)
                start_note(note_index, frame_index)
                continue

            if not sustain_ok:
                flush_note(note_index, frame_index)
                continue

            sum_strength[note_index] += current_score
            frame_counter[note_index] += 1

    for note_index in range(note_count):
        if active[note_index]:
            flush_note(note_index, frame_count - 1)

    return sorted(out, key=lambda note: (note.t_on, note.pitch, note.t_off))
