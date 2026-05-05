from __future__ import annotations

import os
import math
from bisect import bisect_left
from dataclasses import replace
from pathlib import Path

from aural_ingest.algorithms._common import (
    band_pass_one_pole,
    frame_rms_series,
    read_wav_mono_normalized,
    spectral_flux_series,
    stft_magnitude_frames,
)
from aural_ingest.algorithms.melodic_onset_yin import _detect_onsets
from aural_ingest.transcription import MelodicNote

_PIANO_MIN_MIDI = 21
_PIANO_MAX_MIDI = 108
_DEDUP_ONSET_SEC = 0.032
_MERGE_GAP_SEC = 0.045
_SAME_PITCH_CLOSE_CHATTER_ONSET_SEC = 0.08
_SAME_PITCH_CHATTER_ONSET_SEC = 0.08
_GHOST_NOTE_SEC = 0.04
_GHOST_NOTE_VELOCITY = 44
_MIN_GAP_AFTER_EXTENSION_SEC = 0.008
_AUDIO_SUSTAIN_MIN_NOTE_SEC = 0.075
_AUDIO_SUSTAIN_MAX_EXTENSION_SEC = 1.25
_AUDIO_SUSTAIN_WINDOW_SEC = 0.085
_AUDIO_SUSTAIN_HOP_SEC = 0.035
_AUDIO_SUSTAIN_DECAY_RATIO = 0.16
_AUDIO_SUSTAIN_ABS_FLOOR = 0.0012
_ATTACK_MERGE_SEC = 0.03
_SPLIT_NOTE_MIN_SEC = 0.065
_SPLIT_ATTACK_GUARD_SEC = 0.06
_MAX_SPLITS_PER_NOTE = 4
_CLUSTER_ONSET_SEC = 0.04
_LOW_OCTAVE_SHADOW_MAX_MIDI = 52
_DENSE_CLUSTER_NOTE_LIMIT = 6
_HIGH_HARMONIC_SHADOW_MIN_MIDI = 84
_EXTREME_HIGH_MIN_MIDI = 97
_VERY_LOW_MAX_MIDI = 28
_LOW_EXTREME_MAX_MIDI = 35
_EXTREME_CLUSTER_SEC = 0.16
_HARMONIC_SHADOW_INTERVALS = {12, 19, 24, 28, 31, 36}
_LOW_SHADOW_INTERVALS = {12, 17, 19, 24}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _duration(note: MelodicNote) -> float:
    return max(0.0, float(note.t_off) - float(note.t_on))


def _score_note(note: MelodicNote) -> tuple[float, int, float]:
    return (_duration(note), int(note.velocity), -float(note.t_on))


def _midi_to_freq(pitch: int) -> float:
    return 440.0 * (2.0 ** ((float(pitch) - 69.0) / 12.0))


def _rms(values: list[float]) -> float:
    if not values:
        return 0.0
    return (sum(value * value for value in values) / float(len(values))) ** 0.5


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * _clamp(percentile, 0.0, 1.0)))
    return ordered[index]


def _compress_attack_times(attack_times: list[float], *, window_sec: float = 0.035) -> list[float]:
    if not attack_times:
        return []

    merged: list[float] = [float(attack_times[0])]
    for attack in attack_times[1:]:
        if float(attack) - merged[-1] <= window_sec:
            merged[-1] = round((merged[-1] + float(attack)) * 0.5, 6)
            continue
        merged.append(round(float(attack), 6))
    return merged


def _detect_attack_times(samples: list[float], sr: int) -> list[float]:
    if not samples or sr <= 0:
        return []

    frame_size = 1024 if sr >= 22050 else 512
    hop_size = max(96, frame_size // 4)
    rms_series = frame_rms_series(samples, frame_size, hop_size)
    flux_series: list[float] = []
    use_spectral_flux = os.environ.get("AURAL_PIANO_CLEANUP_SPECTRAL_ATTACKS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if use_spectral_flux:
        stft_frames, _ = stft_magnitude_frames(samples, sr, frame_size=frame_size, hop_size=hop_size)
        flux_series = spectral_flux_series(stft_frames, sr, hop_size, (90.0, 5200.0), frame_size=frame_size)
    if not rms_series and not flux_series:
        return []

    onset_indices = set()
    onset_indices.update(_detect_onsets(rms_series, ratio_threshold=1.85, median_window=5))
    onset_indices.update(_detect_onsets(flux_series, ratio_threshold=1.45, median_window=5))
    attack_times = sorted((index * hop_size) / float(sr) for index in onset_indices)
    return _compress_attack_times(attack_times)


def _has_attack_between(
    attack_times: list[float],
    start: float,
    end: float,
    *,
    pad_sec: float = 0.0,
) -> bool:
    if not attack_times:
        return False

    lo = min(start, end) + pad_sec
    hi = max(start, end) - pad_sec
    if hi <= lo:
        return False

    idx = bisect_left(attack_times, lo)
    return idx < len(attack_times) and attack_times[idx] <= hi


def _attack_matches_pitch(
    samples: list[float],
    sr: int,
    attack_time: float,
    pitch: int,
) -> bool:
    center = int(round(float(attack_time) * float(sr)))
    if center <= 0 or center >= len(samples):
        return False

    lookback = max(1, int(sr * 0.04))
    guard = max(1, int(sr * 0.004))
    post = max(1, int(sr * 0.05))
    start = max(0, center - lookback)
    end = min(len(samples), center + post)
    if end - start < (guard * 3):
        return False

    freq = _midi_to_freq(pitch)
    low_hz = max(35.0, freq * 0.7)
    high_hz = min((sr * 0.48), max(low_hz + 80.0, freq * 3.8))
    band = band_pass_one_pole(samples[start:end], sr, low_hz, high_hz)
    if not band:
        return False

    pre_end = max(0, (center - guard) - start)
    post_start = max(0, center - start)
    pre = band[:pre_end]
    post_band = band[post_start:]
    pre_rms = _rms(pre)
    post_rms = _rms(post_band)
    return post_rms >= max(0.004, (pre_rms * 1.18))


def _group_onset_clusters(notes: list[MelodicNote], *, window_sec: float = _CLUSTER_ONSET_SEC) -> list[list[MelodicNote]]:
    if not notes:
        return []

    clusters: list[list[MelodicNote]] = [[notes[0]]]
    for note in notes[1:]:
        if float(note.t_on) - float(clusters[-1][-1].t_on) <= window_sec:
            clusters[-1].append(note)
            continue
        clusters.append([note])
    return clusters


def _note_fundamental_energy(
    samples: list[float],
    sr: int,
    pitch: int,
    start_time: float,
    end_time: float,
) -> float:
    if not samples or sr <= 0 or end_time <= start_time:
        return 0.0

    start = max(0, int(start_time * sr))
    end = min(len(samples), int(end_time * sr))
    if end <= start:
        return 0.0

    segment = samples[start:end]
    if len(segment) < 8:
        return 0.0

    freq = _midi_to_freq(pitch)
    step = (2.0 * math.pi * freq) / float(sr)
    sin_sum = 0.0
    cos_sum = 0.0
    for index, sample in enumerate(segment):
        phase = step * float(index)
        sin_sum += float(sample) * math.sin(phase)
        cos_sum += float(sample) * math.cos(phase)
    return ((sin_sum * sin_sum) + (cos_sum * cos_sum)) ** 0.5 / float(len(segment))


def _note_support_ratio(
    samples: list[float],
    sr: int,
    note: MelodicNote,
    *,
    max_window_sec: float = 0.14,
) -> float:
    if not samples or sr <= 0:
        return 1.0

    start_time = float(note.t_on)
    end_time = min(float(note.t_off), start_time + max_window_sec)
    if end_time <= start_time:
        return 0.0

    start = max(0, int(start_time * sr))
    end = min(len(samples), int(end_time * sr))
    if end <= start:
        return 0.0

    window_rms = _rms(samples[start:end])
    if window_rms <= 1e-9:
        return 0.0

    pitch_energy = _note_fundamental_energy(samples, sr, note.pitch, start_time, end_time)
    return pitch_energy / window_rms


def _prune_low_octave_shadows(
    notes: list[MelodicNote],
    *,
    samples: list[float],
    sr: int,
) -> list[MelodicNote]:
    if not notes or not samples or sr <= 0:
        return notes

    keep_flags = [True] * len(notes)
    index_by_id = {id(note): index for index, note in enumerate(notes)}
    for cluster in _group_onset_clusters(notes):
        if len(cluster) < 2:
            continue

        cluster_sorted = sorted(cluster, key=lambda note: (note.pitch, note.velocity), reverse=False)
        for lower in cluster_sorted:
            if lower.pitch > _LOW_OCTAVE_SHADOW_MAX_MIDI:
                continue

            upper = next(
                (
                    candidate
                    for candidate in cluster_sorted
                    if (candidate.pitch - lower.pitch) in _LOW_SHADOW_INTERVALS
                    and abs(float(candidate.t_on) - float(lower.t_on)) <= _CLUSTER_ONSET_SEC
                ),
                None,
            )
            if upper is None:
                continue

            lower_window_end = min(lower.t_off, lower.t_on + 0.12)
            upper_window_end = min(upper.t_off, upper.t_on + 0.12)
            lower_energy = _note_fundamental_energy(samples, sr, lower.pitch, lower.t_on, lower_window_end)
            upper_energy = _note_fundamental_energy(samples, sr, upper.pitch, upper.t_on, upper_window_end)
            if upper_energy <= 1e-6:
                continue

            if (
                34 <= int(lower.pitch) <= _LOW_EXTREME_MAX_MIDI
                and _duration(lower) >= 0.5
                and int(lower.velocity) >= 82
                and _note_support_ratio(samples, sr, lower) >= 0.014
            ):
                continue

            overlapping = min(lower.t_off, upper.t_off) - max(lower.t_on, upper.t_on)
            if overlapping <= 0.03:
                continue

            weaker_lower = lower.velocity <= (upper.velocity + 10)
            low_fundamental_missing = lower_energy < max(0.003, upper_energy * 0.52)
            if weaker_lower and low_fundamental_missing:
                keep_flags[index_by_id[id(lower)]] = False

    return [note for note, keep in zip(notes, keep_flags) if keep]


def _prune_harmonic_shadows(notes: list[MelodicNote]) -> list[MelodicNote]:
    if not notes:
        return []

    out: list[MelodicNote] = []
    for cluster in _group_onset_clusters(notes):
        keep: list[MelodicNote] = []
        for note in cluster:
            shadowed = False
            if int(note.pitch) >= _HIGH_HARMONIC_SHADOW_MIN_MIDI:
                for lower in cluster:
                    interval = int(note.pitch) - int(lower.pitch)
                    if interval not in _HARMONIC_SHADOW_INTERVALS:
                        continue
                    lower_strong_enough = int(lower.velocity) >= int(note.velocity) - 12
                    lower_sustains = _duration(lower) >= (_duration(note) * 0.65)
                    if lower_strong_enough and lower_sustains:
                        shadowed = True
                        break
            if not shadowed:
                keep.append(note)

        if len(keep) > _DENSE_CLUSTER_NOTE_LIMIT:
            ranked = sorted(keep, key=_score_note, reverse=True)
            keep_ids = {id(note) for note in ranked[:_DENSE_CLUSTER_NOTE_LIMIT]}
            keep = [note for note in keep if id(note) in keep_ids]
        out.extend(keep)

    return sorted(out, key=lambda note: (note.t_on, note.pitch, note.t_off))


def _prune_unsupported_extreme_notes(
    notes: list[MelodicNote],
    *,
    samples: list[float],
    sr: int,
) -> list[MelodicNote]:
    if not notes or not samples or sr <= 0:
        return notes

    out: list[MelodicNote] = []
    support_cache: dict[tuple[int, int, int], float] = {}
    for note in notes:
        pitch = int(note.pitch)
        if _LOW_EXTREME_MAX_MIDI < pitch <= _EXTREME_HIGH_MIN_MIDI - 1:
            out.append(note)
            continue

        cache_key = (
            pitch,
            int(round(float(note.t_on) * 1000.0)),
            int(round(min(float(note.t_off), float(note.t_on) + 0.14) * 1000.0)),
        )
        support = support_cache.get(cache_key)
        if support is None:
            support = _note_support_ratio(samples, sr, note)
            support_cache[cache_key] = support

        if pitch >= 103:
            threshold = 0.06
        elif pitch >= _EXTREME_HIGH_MIN_MIDI:
            threshold = 0.075
        elif pitch <= _VERY_LOW_MAX_MIDI:
            threshold = 0.04
        elif pitch <= 33:
            threshold = 0.02
        else:
            threshold = 0.012

        if support >= threshold:
            out.append(note)
            continue

        # Keep strong, sustained low notes near the normal bass-clef boundary;
        # short unsupported extremes are the usual subharmonic/chime artifacts.
        if 34 <= pitch <= _LOW_EXTREME_MAX_MIDI and _duration(note) >= 0.5 and note.velocity >= 90:
            out.append(note)

    return out


def _prune_extreme_pitch_spray(
    notes: list[MelodicNote],
    *,
    samples: list[float],
    sr: int,
) -> list[MelodicNote]:
    if not notes or not samples or sr <= 0:
        return notes

    keep_ids: set[int] = set()
    drop_ids: set[int] = set()
    support_cache: dict[int, float] = {}

    def support(note: MelodicNote) -> float:
        value = support_cache.get(id(note))
        if value is None:
            value = _note_support_ratio(samples, sr, note)
            support_cache[id(note)] = value
        return value

    for cluster in _group_onset_clusters(notes, window_sec=_EXTREME_CLUSTER_SEC):
        high_notes = [note for note in cluster if int(note.pitch) >= _EXTREME_HIGH_MIN_MIDI]
        non_high_notes = [note for note in cluster if int(note.pitch) < _EXTREME_HIGH_MIN_MIDI]
        if len(high_notes) >= 2 and non_high_notes:
            for note in high_notes:
                strong_supported_high = support(note) >= 0.085 and _duration(note) >= 0.18
                if not strong_supported_high:
                    drop_ids.add(id(note))

        low_notes = [note for note in cluster if int(note.pitch) <= _VERY_LOW_MAX_MIDI]
        non_low_notes = [note for note in cluster if int(note.pitch) > _LOW_EXTREME_MAX_MIDI]
        if len(low_notes) >= 2 and non_low_notes:
            for note in low_notes:
                strong_supported_low = support(note) >= 0.075 and _duration(note) >= 0.25
                if not strong_supported_low:
                    drop_ids.add(id(note))

        if len(high_notes) <= 1 and len(low_notes) <= 1:
            keep_ids.update(id(note) for note in cluster)

    return [note for note in notes if id(note) not in drop_ids or id(note) in keep_ids]


def _normalize_note(note: MelodicNote, *, instrument: str) -> MelodicNote | None:
    pitch = int(note.pitch)
    if pitch < _PIANO_MIN_MIDI or pitch > _PIANO_MAX_MIDI:
        return None

    t_on = round(max(0.0, float(note.t_on)), 6)
    t_off = round(max(t_on, float(note.t_off)), 6)
    if t_off <= t_on:
        return None

    velocity = max(1, min(127, int(note.velocity)))
    return MelodicNote(
        t_on=t_on,
        t_off=t_off,
        pitch=pitch,
        velocity=velocity,
        instrument=instrument,
    )


def _dedupe_same_pitch_clusters(notes: list[MelodicNote]) -> list[MelodicNote]:
    if not notes:
        return []

    notes_by_pitch: dict[int, list[MelodicNote]] = {}
    for note in notes:
        notes_by_pitch.setdefault(int(note.pitch), []).append(note)

    out: list[MelodicNote] = []
    for pitch_notes in notes_by_pitch.values():
        merged_for_pitch: list[MelodicNote] = []
        for note in sorted(pitch_notes, key=lambda item: (item.t_on, item.t_off)):
            if not merged_for_pitch:
                merged_for_pitch.append(note)
                continue

            prev = merged_for_pitch[-1]
            close_onset = (note.t_on - prev.t_on) <= _DEDUP_ONSET_SEC
            overlapping = note.t_on <= (prev.t_off + 0.02)
            if close_onset and overlapping:
                better = note if _score_note(note) > _score_note(prev) else prev
                merged_for_pitch[-1] = MelodicNote(
                    t_on=min(prev.t_on, note.t_on),
                    t_off=max(prev.t_off, note.t_off),
                    pitch=better.pitch,
                    velocity=max(prev.velocity, note.velocity),
                    instrument=better.instrument,
                )
                continue

            merged_for_pitch.append(note)

        out.extend(merged_for_pitch)

    return sorted(out, key=lambda note: (note.t_on, note.pitch, note.t_off))


def _has_pitch_attack_between(
    attack_times: list[float],
    start: float,
    end: float,
    *,
    pitch: int,
    samples: list[float],
    sr: int,
) -> bool:
    if not attack_times:
        return False

    lo = min(start, end)
    hi = max(start, end)
    idx = bisect_left(attack_times, lo)
    while idx < len(attack_times) and attack_times[idx] <= hi:
        attack = attack_times[idx]
        if not samples or sr <= 0 or _attack_matches_pitch(samples, sr, attack, pitch):
            return True
        idx += 1

    return False


def _merge_same_pitch_microgaps(
    notes: list[MelodicNote],
    *,
    attack_times: list[float] | None = None,
    samples: list[float] | None = None,
    sr: int = 0,
) -> list[MelodicNote]:
    if not notes:
        return []

    notes_by_pitch: dict[int, list[MelodicNote]] = {}
    for note in notes:
        notes_by_pitch.setdefault(int(note.pitch), []).append(note)

    out: list[MelodicNote] = []
    for pitch, pitch_notes in notes_by_pitch.items():
        pitch_notes_sorted = sorted(pitch_notes, key=lambda item: (item.t_on, item.t_off))
        merged_for_pitch: list[MelodicNote] = []
        merged_counts: list[int] = []
        merged_last_onsets: list[float] = []
        for note_index, note in enumerate(pitch_notes_sorted):
            if not merged_for_pitch:
                merged_for_pitch.append(note)
                merged_counts.append(1)
                merged_last_onsets.append(float(note.t_on))
                continue

            prev = merged_for_pitch[-1]
            gap = note.t_on - prev.t_off
            onset_delta = note.t_on - merged_last_onsets[-1]
            next_same_pitch_is_close = (
                note_index + 1 < len(pitch_notes_sorted)
                and pitch_notes_sorted[note_index + 1].t_on - note.t_on <= _SAME_PITCH_CHATTER_ONSET_SEC
            )
            looks_like_chatter = (
                onset_delta <= _SAME_PITCH_CLOSE_CHATTER_ONSET_SEC
                or merged_counts[-1] > 1
                or next_same_pitch_is_close
            )
            blocked_by_attack = False
            if attack_times:
                blocked_by_attack = _has_pitch_attack_between(
                    attack_times,
                    prev.t_off - _ATTACK_MERGE_SEC,
                    note.t_on + _ATTACK_MERGE_SEC,
                    pitch=pitch,
                    samples=samples or [],
                    sr=sr,
                )
            if (
                gap <= _MERGE_GAP_SEC
                and onset_delta <= _SAME_PITCH_CHATTER_ONSET_SEC
                and looks_like_chatter
                and not blocked_by_attack
            ):
                merged_for_pitch[-1] = MelodicNote(
                    t_on=prev.t_on,
                    t_off=max(prev.t_off, note.t_off),
                    pitch=prev.pitch,
                    velocity=max(prev.velocity, note.velocity),
                    instrument=prev.instrument,
                )
                merged_counts[-1] += 1
                merged_last_onsets[-1] = float(note.t_on)
                continue

            merged_for_pitch.append(note)
            merged_counts.append(1)
            merged_last_onsets.append(float(note.t_on))

        out.extend(merged_for_pitch)

    return sorted(out, key=lambda note: (note.t_on, note.pitch, note.t_off))


def _drop_ghost_notes(notes: list[MelodicNote]) -> list[MelodicNote]:
    out: list[MelodicNote] = []
    for note in notes:
        dur = _duration(note)
        if dur < _GHOST_NOTE_SEC and int(note.velocity) < _GHOST_NOTE_VELOCITY:
            continue
        out.append(note)
    return out


def _split_notes_at_audio_reattacks(
    notes: list[MelodicNote],
    attack_times: list[float],
    *,
    samples: list[float],
    sr: int,
) -> list[MelodicNote]:
    if not notes or not attack_times or not samples or sr <= 0:
        return notes

    attack_cache: dict[tuple[int, int], bool] = {}
    split_notes: list[MelodicNote] = []
    for note in notes:
        if _duration(note) < (_SPLIT_NOTE_MIN_SEC * 2.2):
            split_notes.append(note)
            continue

        split_points: list[float] = []
        for attack in attack_times:
            if attack <= note.t_on + _SPLIT_ATTACK_GUARD_SEC:
                continue
            if attack >= note.t_off - _SPLIT_NOTE_MIN_SEC:
                break

            cache_key = (int(note.pitch), int(round(attack * 1000.0)))
            relevant = attack_cache.get(cache_key)
            if relevant is None:
                relevant = _attack_matches_pitch(samples, sr, attack, note.pitch)
                attack_cache[cache_key] = relevant
            if not relevant:
                continue

            split_points.append(float(attack))
            if len(split_points) >= _MAX_SPLITS_PER_NOTE:
                break

        if not split_points:
            split_notes.append(note)
            continue

        cursor = float(note.t_on)
        produced = False
        for split_time in split_points:
            if split_time - cursor < _SPLIT_NOTE_MIN_SEC:
                continue
            produced = True
            split_notes.append(
                replace(
                    note,
                    t_on=round(cursor, 6),
                    t_off=round(split_time, 6),
                )
            )
            cursor = float(split_time)

        if not produced:
            split_notes.append(note)
            continue

        if note.t_off - cursor >= (_SPLIT_NOTE_MIN_SEC * 0.75):
            split_notes.append(
                replace(
                    note,
                    t_on=round(cursor, 6),
                    t_off=round(note.t_off, 6),
                )
            )
        elif split_notes:
            prev = split_notes[-1]
            split_notes[-1] = replace(prev, t_off=round(note.t_off, 6))

    return split_notes


def _blend_velocities_from_audio(notes: list[MelodicNote], stem_path: Path | None) -> list[MelodicNote]:
    if stem_path is None:
        return notes
    samples, sr = read_wav_mono_normalized(stem_path)
    if not samples or sr <= 0:
        return notes

    onset_radius = max(1, int(sr * 0.035))
    lookback = max(0, int(sr * 0.01))
    onset_energies: list[float] = []
    band_energies: list[float] = []
    for note in notes:
        center = max(0, int(note.t_on * sr) - lookback)
        end = min(len(samples), center + onset_radius)
        if end <= center:
            onset_energies.append(0.0)
            band_energies.append(0.0)
            continue
        window = samples[center:end]
        onset_energies.append(sum(abs(sample) for sample in window) / float(len(window)))

        freq = _midi_to_freq(note.pitch)
        low_hz = max(28.0, freq * 0.72)
        high_hz = min(sr * 0.48, max(low_hz + 36.0, freq * 2.4))
        band = band_pass_one_pole(window, sr, low_hz, high_hz)
        band_energies.append(_rms(band))

    if not onset_energies:
        return notes

    def normalize(values: list[float]) -> list[float]:
        lo = _percentile(values, 0.10)
        hi = _percentile(values, 0.95)
        span = hi - lo
        if span <= 1e-9:
            return [0.5 for _value in values]
        return [_clamp((value - lo) / span, 0.0, 1.0) for value in values]

    onset_units = normalize(onset_energies)
    band_units = normalize(band_energies)

    blended: list[MelodicNote] = []
    for note, onset_unit, band_unit in zip(notes, onset_units, band_units):
        pitch_unit = _clamp(
            (float(note.pitch) - float(_PIANO_MIN_MIDI)) / float(_PIANO_MAX_MIDI - _PIANO_MIN_MIDI),
            0.0,
            1.0,
        )
        pitch_taper = 0.82 + (pitch_unit * 0.16)
        dynamic_unit = (0.60 * onset_unit) + (0.40 * band_unit)
        audio_velocity = int(round(28.0 + (70.0 * dynamic_unit * pitch_taper)))
        velocity = int(round((float(note.velocity) * 0.55) + (float(audio_velocity) * 0.45)))
        blended.append(replace(note, velocity=max(1, min(127, velocity))))
    return blended


def _pitch_band_rms_series(
    samples: list[float],
    sr: int,
    *,
    pitch: int,
    start_time: float,
    end_time: float,
) -> tuple[list[float], float]:
    if not samples or sr <= 0 or end_time <= start_time:
        return [], 0.0

    start = max(0, int(round(start_time * sr)))
    end = min(len(samples), int(round(end_time * sr)))
    if end <= start:
        return [], 0.0

    freq = _midi_to_freq(pitch)
    low_hz = max(28.0, freq * 0.72)
    high_hz = min(sr * 0.48, max(low_hz + 24.0, freq * 1.42))
    band = band_pass_one_pole(samples[start:end], sr, low_hz, high_hz)
    if not band:
        return [], float(start) / float(sr)

    window = max(8, int(round(_AUDIO_SUSTAIN_WINDOW_SEC * sr)))
    hop = max(4, int(round(_AUDIO_SUSTAIN_HOP_SEC * sr)))
    if len(band) < window:
        return [_rms(band)], float(start) / float(sr)

    values: list[float] = []
    for offset in range(0, len(band) - window + 1, hop):
        values.append(_rms(band[offset : offset + window]))
    return values, float(start) / float(sr)


def _extend_note_sustain_static(notes: list[MelodicNote]) -> list[MelodicNote]:
    if not notes:
        return []

    next_same_pitch_onset: dict[int, float] = {}
    out_reversed: list[MelodicNote] = []

    for note in reversed(notes):
        dur = _duration(note)
        velocity_unit = _clamp(float(note.velocity) / 127.0, 0.0, 1.0)
        extension = 0.025 + min(0.11, (dur * 0.35) + (velocity_unit * 0.05))

        next_onset = next_same_pitch_onset.get(note.pitch)
        target_off = note.t_off + extension
        if next_onset is not None:
            target_off = min(target_off, next_onset - _MIN_GAP_AFTER_EXTENSION_SEC)
        target_off = max(note.t_off, target_off)

        out_reversed.append(replace(note, t_off=round(target_off, 6)))
        next_same_pitch_onset[note.pitch] = note.t_on

    out_reversed.reverse()
    return out_reversed


def _extend_note_sustain_from_audio(
    notes: list[MelodicNote],
    *,
    samples: list[float],
    sr: int,
) -> list[MelodicNote]:
    if not notes or not samples or sr <= 0:
        return _extend_note_sustain_static(notes)

    audio_duration = len(samples) / float(sr)
    next_same_pitch_onset: dict[int, float] = {}
    out_reversed: list[MelodicNote] = []

    for note in reversed(notes):
        dur = _duration(note)
        next_onset = next_same_pitch_onset.get(note.pitch)
        max_end = min(audio_duration, note.t_off + _AUDIO_SUSTAIN_MAX_EXTENSION_SEC)
        if next_onset is not None:
            max_end = min(max_end, next_onset - _MIN_GAP_AFTER_EXTENSION_SEC)

        target_off = float(note.t_off)
        if dur >= _AUDIO_SUSTAIN_MIN_NOTE_SEC and max_end > note.t_off + _AUDIO_SUSTAIN_HOP_SEC:
            values, series_start = _pitch_band_rms_series(
                samples,
                sr,
                pitch=int(note.pitch),
                start_time=float(note.t_on),
                end_time=max_end,
            )
            if values:
                hop_sec = _AUDIO_SUSTAIN_HOP_SEC
                early_count = max(1, int(math.ceil(min(0.18, max(dur, _AUDIO_SUSTAIN_WINDOW_SEC)) / hop_sec)))
                early_energy = max(values[:early_count] or [0.0])
                floor = _percentile(values, 0.25)
                threshold = max(_AUDIO_SUSTAIN_ABS_FLOOR, floor * 1.65, early_energy * _AUDIO_SUSTAIN_DECAY_RATIO)
                below_count = 0
                last_supported = float(note.t_off)
                for value_index, value in enumerate(values):
                    window_start = series_start + (value_index * hop_sec)
                    window_end = window_start + _AUDIO_SUSTAIN_WINDOW_SEC
                    if window_end <= note.t_off:
                        continue
                    if window_start >= max_end:
                        break

                    if value >= threshold:
                        last_supported = min(max_end, window_end)
                        below_count = 0
                        continue

                    below_count += 1
                    if below_count >= 2:
                        break

                target_off = max(target_off, last_supported)

        if target_off <= note.t_off:
            static_extended = _extend_note_sustain_static([note])[0]
            target_off = min(float(static_extended.t_off), max_end)

        out_reversed.append(replace(note, t_off=round(max(float(note.t_off), target_off), 6)))
        next_same_pitch_onset[note.pitch] = note.t_on

    out_reversed.reverse()
    return out_reversed


def cleanup_notes(
    notes: list[MelodicNote],
    *,
    stem_path: Path | None = None,
    instrument: str = "keys",
) -> list[MelodicNote]:
    samples: list[float] = []
    sr = 0
    if stem_path is not None:
        samples, sr = read_wav_mono_normalized(stem_path)
    attack_times = _detect_attack_times(samples, sr) if samples and sr > 0 else []

    normalized = [
        note
        for note in (
            _normalize_note(note, instrument=instrument)
            for note in sorted(notes, key=lambda item: (item.t_on, item.pitch, item.t_off))
        )
        if note is not None
    ]
    if not normalized:
        return []

    normalized = _dedupe_same_pitch_clusters(normalized)
    normalized = _split_notes_at_audio_reattacks(normalized, attack_times, samples=samples, sr=sr)
    normalized = _merge_same_pitch_microgaps(normalized, attack_times=attack_times, samples=samples, sr=sr)
    normalized = _prune_low_octave_shadows(normalized, samples=samples, sr=sr)
    normalized = _prune_harmonic_shadows(normalized)
    normalized = _prune_unsupported_extreme_notes(normalized, samples=samples, sr=sr)
    normalized = _prune_extreme_pitch_spray(normalized, samples=samples, sr=sr)
    normalized = _drop_ghost_notes(normalized)
    normalized = _blend_velocities_from_audio(normalized, stem_path)
    normalized = _extend_note_sustain_from_audio(normalized, samples=samples, sr=sr)

    return [
        replace(note, t_on=round(note.t_on, 6), t_off=round(max(note.t_on, note.t_off), 6))
        for note in normalized
        if note.t_off > note.t_on
    ]
