"""
E-GMD (Expanded Groove MIDI Dataset v1.0.0) adapter.

Yields one ``GroundTruthCase`` per WAV+MIDI pair listed in the
``e-gmd-v1.0.0.csv`` metadata file. The MIDI is parsed once and mapped
into the same canonical ``DrumEvent`` list our drum benchmark scoring
already consumes, using the existing production MIDI-note → drum-class
map (``_BENCHMARK_NOTE_TO_CLASS``).

Metadata kept on the case (``split``, ``style``, ``kit_name``, ``bpm``,
``time_signature``, ``drummer``) enables per-bucket aggregation in the
benchmark report.

Reference: https://magenta.withgoogle.com/datasets/e-gmd
Audio is aligned within 2 ms of the MIDI (per dataset notes); no
per-case offset estimation is required.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from aural_ingest.transcription import DrumEvent

from .common import GroundTruthCase


def _read_vlq(data: bytes, pos: int) -> tuple[int, int]:
    """Read a MIDI Variable-Length Quantity. Returns ``(value, new_pos)``."""
    value = 0
    while True:
        b = data[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if b & 0x80 == 0:
            return value, pos


def _parse_drum_midi(
    midi_path: Path,
    note_to_class: dict[int, str],
) -> list[DrumEvent]:
    """Pure-stdlib MIDI parser for E-GMD's single-track drum files.

    E-GMD MIDIs are simple format-0/1 files with one track of NoteOn
    events on channel 9 (drums). We only need NoteOn (velocity > 0) with
    the running tempo, so we skip the bulkier midly-style abstractions
    and read the bytes directly.

    Returns a list of ``DrumEvent``s with ``time`` in seconds, ``note``
    in standard GM drum MIDI, and ``velocity`` 1..127.
    """
    data = midi_path.read_bytes()
    if len(data) < 14 or data[:4] != b"MThd":
        raise ValueError(f"not a MIDI file: {midi_path}")
    header_len = int.from_bytes(data[4:8], "big")
    if header_len != 6:
        raise ValueError(f"unexpected MIDI header length: {header_len}")
    fmt = int.from_bytes(data[8:10], "big")
    ntracks = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big")
    if division & 0x8000:
        raise ValueError(f"SMPTE timing not supported in E-GMD adapter: {midi_path}")
    ticks_per_quarter = division

    pos = 8 + header_len
    if pos + 8 > len(data):
        raise ValueError(f"MIDI body truncated: {midi_path}")
    if fmt not in (0, 1):
        raise ValueError(f"unsupported MIDI format {fmt}: {midi_path}")

    # Build (tick, tempo_us_per_quarter) tempo map by scanning all tracks
    # for tempo meta events, plus collect note-on events from any track
    # that uses channel 9 (drums). E-GMD is a single drum track, but
    # tempo meta may live on a separate conductor track in some files.
    tempo_changes: list[tuple[int, int]] = [(0, 500_000)]
    note_ons: list[tuple[int, int, int]] = []  # (tick, note, velocity)

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
                # Running status.
                status = running_status
            else:
                pos += 1
                running_status = status

            if status == 0xFF:
                meta_type = data[pos]
                pos += 1
                meta_len, pos = _read_vlq(data, pos)
                if meta_type == 0x51 and meta_len == 3:
                    tempo_us = int.from_bytes(data[pos : pos + 3], "big")
                    tempo_changes.append((tick, tempo_us))
                pos += meta_len
            elif status in (0xF0, 0xF7):
                sysex_len, pos = _read_vlq(data, pos)
                pos += sysex_len
            else:
                high = status & 0xF0
                channel = status & 0x0F
                if high in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                    if pos + 2 > end:
                        raise ValueError("note event truncated")
                    n = data[pos]
                    v = data[pos + 1]
                    pos += 2
                    if high == 0x90 and v > 0 and channel == 9:
                        note_ons.append((tick, n, v))
                elif high in (0xC0, 0xD0):
                    pos += 1
                else:
                    raise ValueError(f"unknown MIDI status 0x{status:02X} in {midi_path}")
        pos = end

    if not note_ons:
        return []

    # Build cumulative-time table.
    tempo_changes.sort()
    # Dedupe identical-tick entries: keep last (later wins per MIDI spec).
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

    events: list[DrumEvent] = []
    for tick, note, vel in note_ons:
        cls = note_to_class.get(int(note))
        if cls is None:
            # Out-of-vocabulary drum note -- E-GMD uses jingle stacks
            # etc. that don't map cleanly into our 9-class taxonomy.
            # Keep them with the raw MIDI note; the scoring routine
            # ignores notes outside its class set.
            pass
        events.append(
            DrumEvent(
                time=tick_to_sec(tick),
                note=int(note),
                velocity=int(vel),
            )
        )
    events.sort(key=lambda e: (e.time, e.note))
    return events


def yield_cases(
    corpus_root: Path,
    *,
    split: str | Iterable[str] | None = "test",
    style_filter: Sequence[str] | None = None,
    limit: int | None = None,
) -> Iterator[GroundTruthCase]:
    """Yield ``GroundTruthCase``s from a prepared E-GMD corpus.

    Parameters
    ----------
    corpus_root
        Directory containing both the ``e_gmd_metadata`` and
        ``e_gmd_full`` extraction subtrees (i.e. the per-dataset root
        under ``E:\\AudioSourceOfTruthData\\extracted\\e_gmd``).
    split
        ``"test"`` / ``"train"`` / ``"validation"``, or an iterable of
        those, or ``None`` to yield every row.
    style_filter
        Only yield rows whose ``style`` column matches one of these.
    limit
        Stop after this many cases. Useful for fast smoke runs.
    """
    metadata_csv = corpus_root / "e_gmd_metadata" / "e-gmd-v1.0.0.csv"
    audio_root = corpus_root / "e_gmd_full" / "e-gmd-v1.0.0"
    if not metadata_csv.is_file():
        raise FileNotFoundError(f"E-GMD metadata not found: {metadata_csv}")
    if not audio_root.is_dir():
        raise FileNotFoundError(f"E-GMD audio root not found: {audio_root}")

    splits = (
        {split} if isinstance(split, str)
        else set(split) if split is not None
        else None
    )
    style_set = set(style_filter) if style_filter else None

    from aural_ingest.transcription import _BENCHMARK_NOTE_TO_CLASS as note_to_class

    count = 0
    with metadata_csv.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if splits is not None and row["split"] not in splits:
                continue
            if style_set is not None and row["style"] not in style_set:
                continue
            midi_rel = row["midi_filename"]
            audio_rel = row["audio_filename"]
            midi_path = audio_root / midi_rel
            audio_path = audio_root / audio_rel
            if not midi_path.is_file() or not audio_path.is_file():
                # E-GMD extractions can be partial during prep.
                continue
            try:
                events = _parse_drum_midi(midi_path, dict(note_to_class))
            except Exception:
                continue
            if not events:
                continue
            # The CSV's ``id`` column is the abstract performance id; the
            # same MIDI is recorded against multiple kits, so we add the
            # audio basename to keep case_ids unique across kit rows.
            case_id = f"egmd:{row['id']}::{Path(audio_rel).stem}"
            duration_sec = float(row.get("duration", "0") or 0.0)
            yield GroundTruthCase(
                case_id=case_id,
                instrument="drums",
                audio_path=audio_path,
                duration_sec=duration_sec,
                drum_events=tuple(events),
                metadata={
                    "split": row["split"],
                    "style": row["style"],
                    "kit_name": row["kit_name"],
                    "bpm": row["bpm"],
                    "time_signature": row["time_signature"],
                    "drummer": row["drummer"],
                },
            )
            count += 1
            if limit is not None and count >= limit:
                return
