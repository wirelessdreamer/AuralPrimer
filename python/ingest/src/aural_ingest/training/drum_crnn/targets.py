"""Build per-frame multi-label onset targets from E-GMD MIDI.

Two responsibilities:

1. Parse an E-GMD drum ``.midi`` into ``(onset_time_sec, gm_note)`` pairs
   (pure stdlib; same byte-level approach as ``dataset_adapters/egmd.py``).
2. Fold GM notes into the fixed five-class taxonomy and rasterise onsets onto
   the feature frame grid as a ``(n_frames, num_classes)`` float32 target with
   a small triangular bump around each onset (the standard ADT label-widening
   trick, controlled by :class:`TargetConfig`).

The GM-note -> 5-class fold reuses the production benchmark note map
(``_BENCHMARK_NOTE_TO_CLASS``) and collapses its finer buckets
(``tom1/tom2/tom3`` -> ``toms``, ``crash/ride`` -> ``cymbals``) so the training
labels agree with what the benchmark harness scores against.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .config import CLASSES, FeatureConfig, TargetConfig

# Fold the benchmark's finer drum buckets into the 5-class taxonomy.
_BENCHMARK_CLASS_TO_5CLASS: dict[str, str] = {
    "kick": "kick",
    "snare": "snare",
    "hi_hat": "hi_hat",
    "tom1": "toms",
    "tom2": "toms",
    "tom3": "toms",
    "crash": "cymbals",
    "ride": "cymbals",
}

_CLASS_INDEX: dict[str, int] = {name: i for i, name in enumerate(CLASSES)}


def gm_note_to_class_index(note: int) -> int | None:
    """Map a GM percussion MIDI note to a 5-class index, or ``None``.

    ``None`` means the note is outside our taxonomy (e.g. E-GMD jingle-stack
    notes); callers drop it rather than forcing it into a bucket.
    """
    from aural_ingest.transcription import _BENCHMARK_NOTE_TO_CLASS

    bench = _BENCHMARK_NOTE_TO_CLASS.get(int(note))
    if bench is None:
        return None
    mapped = _BENCHMARK_CLASS_TO_5CLASS.get(bench)
    if mapped is None:
        return None
    return _CLASS_INDEX[mapped]


def _read_vlq(data: bytes, pos: int) -> tuple[int, int]:
    """Read a MIDI Variable-Length Quantity. Returns ``(value, new_pos)``."""
    value = 0
    while True:
        b = data[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if b & 0x80 == 0:
            return value, pos


def parse_drum_onsets(midi_path: str | Path) -> list[tuple[float, int]]:
    """Parse an E-GMD drum MIDI into ``(onset_sec, gm_note)`` pairs.

    Only channel-9 NoteOn events with velocity > 0 are kept, converted to
    absolute seconds through the file's tempo map. Mirrors the parser in
    ``dataset_adapters/egmd.py`` (kept standalone so the training harness does
    not import the benchmark stack).
    """
    data = Path(midi_path).read_bytes()
    if len(data) < 14 or data[:4] != b"MThd":
        raise ValueError(f"not a MIDI file: {midi_path}")
    header_len = int.from_bytes(data[4:8], "big")
    if header_len != 6:
        raise ValueError(f"unexpected MIDI header length: {header_len}")
    fmt = int.from_bytes(data[8:10], "big")
    ntracks = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big")
    if division & 0x8000:
        raise ValueError(f"SMPTE timing not supported: {midi_path}")
    ticks_per_quarter = division
    if fmt not in (0, 1):
        raise ValueError(f"unsupported MIDI format {fmt}: {midi_path}")

    pos = 8 + header_len
    tempo_changes: list[tuple[int, int]] = [(0, 500_000)]
    note_ons: list[tuple[int, int]] = []  # (tick, note)

    for _ in range(ntracks):
        if pos + 8 > len(data) or data[pos : pos + 4] != b"MTrk":
            raise ValueError(f"MTrk header missing in {midi_path}")
        track_len = int.from_bytes(data[pos + 4 : pos + 8], "big")
        pos += 8
        end = pos + track_len
        if end > len(data):
            raise ValueError(f"MTrk body truncated in {midi_path}")

        tick = 0
        running_status = 0
        while pos < end:
            delta, pos = _read_vlq(data, pos)
            tick += delta
            if pos >= end:
                break
            status = data[pos]
            if status < 0x80:
                status = running_status
            else:
                pos += 1
                running_status = status

            if status == 0xFF:
                meta_type = data[pos]
                pos += 1
                meta_len, pos = _read_vlq(data, pos)
                if meta_type == 0x51 and meta_len == 3:
                    tempo_changes.append((tick, int.from_bytes(data[pos : pos + 3], "big")))
                pos += meta_len
            elif status in (0xF0, 0xF7):
                sysex_len, pos = _read_vlq(data, pos)
                pos += sysex_len
            else:
                high = status & 0xF0
                channel = status & 0x0F
                if high in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                    n = data[pos]
                    v = data[pos + 1]
                    pos += 2
                    if high == 0x90 and v > 0 and channel == 9:
                        note_ons.append((tick, n))
                elif high in (0xC0, 0xD0):
                    pos += 1
                else:
                    raise ValueError(f"unknown MIDI status 0x{status:02X} in {midi_path}")
        pos = end

    if not note_ons:
        return []

    tempo_changes.sort()
    dedup: list[tuple[int, int]] = []
    for tick, tempo in tempo_changes:
        if dedup and dedup[-1][0] == tick:
            dedup[-1] = (tick, tempo)
        else:
            dedup.append((tick, tempo))

    def tick_to_sec(target: int) -> float:
        sec = 0.0
        prev_tick = 0
        cur_tempo = dedup[0][1]
        for tick, tempo in dedup:
            if tick >= target:
                break
            sec += (tick - prev_tick) * cur_tempo / 1_000_000 / ticks_per_quarter
            prev_tick = tick
            cur_tempo = tempo
        sec += (target - prev_tick) * cur_tempo / 1_000_000 / ticks_per_quarter
        return sec

    out = [(tick_to_sec(tick), int(note)) for tick, note in note_ons]
    out.sort()
    return out


def onsets_to_class_frames(
    onsets: list[tuple[float, int]],
    n_frames: int,
    feat: FeatureConfig,
    tgt: TargetConfig,
) -> np.ndarray:
    """Rasterise ``(onset_sec, gm_note)`` pairs to ``(n_frames, num_classes)``.

    Onset time -> frame via ``round(t * sr / hop)`` (the ``center=True`` frame
    convention). Each onset writes a triangular bump of half-width
    ``tgt.smoothing_frames`` into its class column, taking the max where bumps
    overlap so the peak stays at 1.0.
    """
    target = np.zeros((n_frames, len(CLASSES)), dtype=np.float32)
    if n_frames <= 0:
        return target
    hop = float(feat.hop_length)
    sr = float(feat.sample_rate)
    half = int(tgt.smoothing_frames)

    for t_sec, note in onsets:
        cls = gm_note_to_class_index(note)
        if cls is None:
            continue
        center = int(round(t_sec * sr / hop))
        if center < 0 or center >= n_frames:
            continue
        for offset in range(-half, half + 1):
            f = center + offset
            if 0 <= f < n_frames:
                weight = 1.0 - (abs(offset) / (half + 1.0))
                if weight > target[f, cls]:
                    target[f, cls] = weight
    return target


def build_targets_from_midi(
    midi_path: str | Path,
    n_frames: int,
    feat: FeatureConfig,
    tgt: TargetConfig,
) -> np.ndarray:
    """End-to-end: parse a MIDI file and return its per-frame class targets."""
    onsets = parse_drum_onsets(midi_path)
    return onsets_to_class_frames(onsets, n_frames, feat, tgt)
