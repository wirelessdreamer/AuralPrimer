from __future__ import annotations

from pathlib import Path

from aural_ingest.transcription import MelodicNote

_PIANO_MIN_MIDI = 21
_PIANO_MAX_MIDI = 108


def _read_vlq(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    while True:
        if pos >= len(data):
            raise ValueError("midi variable-length quantity is truncated")
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos


def _tempo_segments(tempo_changes: list[tuple[int, int]]) -> list[tuple[int, int]]:
    return sorted(set(tempo_changes), key=lambda item: item[0]) or [(0, 500_000)]


def _tick_to_seconds(tick: int, tempo_changes: list[tuple[int, int]], division: int) -> float:
    tick = max(0, int(tick))
    changes = _tempo_segments(tempo_changes)
    seconds = 0.0
    last_tick = 0
    last_tempo = changes[0][1]

    for change_tick, tempo_us_per_quarter in changes[1:]:
        if tick <= change_tick:
            break
        seconds += ((change_tick - last_tick) * last_tempo) / (division * 1_000_000.0)
        last_tick = change_tick
        last_tempo = tempo_us_per_quarter

    seconds += ((tick - last_tick) * last_tempo) / (division * 1_000_000.0)
    return seconds


def decode_midi_notes(midi_path: Path, *, instrument: str = "keys") -> list[MelodicNote]:
    data = midi_path.read_bytes()
    if len(data) < 14 or data[0:4] != b"MThd":
        raise ValueError("piano midi output missing MThd header")

    header_len = int.from_bytes(data[4:8], "big")
    if header_len < 6:
        raise ValueError("piano midi output has invalid header length")
    track_count = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big")
    if division & 0x8000:
        raise ValueError("smpte midi timing is not supported for piano outputs")

    pos = 8 + header_len
    tempo_changes: list[tuple[int, int]] = [(0, 500_000)]
    note_events: list[tuple[int, int, int, int]] = []

    for _track_index in range(track_count):
        if pos + 8 > len(data) or data[pos : pos + 4] != b"MTrk":
            raise ValueError("piano midi output missing MTrk chunk")
        track_len = int.from_bytes(data[pos + 4 : pos + 8], "big")
        pos += 8
        end = pos + track_len
        if end > len(data):
            raise ValueError("piano midi track chunk is truncated")

        tick = 0
        running_status: int | None = None
        open_notes: dict[tuple[int, int], tuple[int, int]] = {}

        while pos < end:
            delta, pos = _read_vlq(data, pos)
            tick += delta
            if pos >= end:
                break

            status_byte = data[pos]
            if status_byte & 0x80:
                pos += 1
                status = status_byte
                if status < 0xF0:
                    running_status = status
            else:
                if running_status is None:
                    raise ValueError("midi running status encountered without prior status")
                status = running_status

            if status == 0xFF:
                if pos >= end:
                    raise ValueError("truncated midi meta event")
                meta_type = data[pos]
                pos += 1
                length, pos = _read_vlq(data, pos)
                payload = data[pos : pos + length]
                pos += length
                if meta_type == 0x51 and len(payload) == 3:
                    tempo_changes.append((tick, int.from_bytes(payload, "big")))
                elif meta_type == 0x2F:
                    break
                running_status = None
                continue

            if status in {0xF0, 0xF7}:
                length, pos = _read_vlq(data, pos)
                pos += length
                running_status = None
                continue

            event_type = status & 0xF0
            channel = status & 0x0F

            if status_byte & 0x80:
                if pos >= end:
                    raise ValueError("truncated midi channel event")
                data1 = data[pos]
                pos += 1
            else:
                data1 = status_byte
                pos += 1

            if event_type in {0xC0, 0xD0}:
                continue

            if pos >= end:
                raise ValueError("truncated midi channel event")
            data2 = data[pos]
            pos += 1

            pitch = int(data1)
            if pitch < _PIANO_MIN_MIDI or pitch > _PIANO_MAX_MIDI:
                continue

            key = (channel, pitch)
            if event_type == 0x90 and data2 > 0:
                prior = open_notes.get(key)
                if prior is not None:
                    note_events.append((prior[0], tick, pitch, prior[1]))
                open_notes[key] = (tick, int(data2))
            elif event_type == 0x80 or (event_type == 0x90 and data2 == 0):
                prior = open_notes.pop(key, None)
                if prior is not None:
                    note_events.append((prior[0], tick, pitch, prior[1]))

        for (_channel, pitch), (start_tick, velocity) in open_notes.items():
            note_events.append((start_tick, tick, pitch, velocity))

        pos = end

    tempos = _tempo_segments(tempo_changes)
    notes: list[MelodicNote] = []
    for start_tick, end_tick, pitch, velocity in note_events:
        t_on = _tick_to_seconds(start_tick, tempos, division)
        t_off = _tick_to_seconds(max(start_tick, end_tick), tempos, division)
        if t_off <= t_on:
            continue
        notes.append(
            MelodicNote(
                t_on=round(t_on, 6),
                t_off=round(t_off, 6),
                pitch=int(pitch),
                velocity=max(1, min(127, int(velocity))),
                instrument=instrument,
            )
        )

    return sorted(notes, key=lambda note: (note.t_on, note.pitch, note.t_off))
