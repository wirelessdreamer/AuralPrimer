"""
Guitar-TECHS v1 adapter.

Extracted layout (under ``E:\\AudioSourceOfTruthData\\extracted\\guitar_techs``):

    guitar_techs_<player>_<category>/<Player>_<Category>/
        midi/midi_<id>.mid
        audio/directinput/directinput_<id>.wav    <- DI source
        audio/micamp/micamp_<id>.wav              <- amp-mic source
        video/...                                 <- not used here

Where ``<player>`` is one of ``p1``/``p2``/``p3`` and ``<category>``
is ``chords``/``scales``/``singlenotes``/``techniques`` (p1+p2 only)
or ``music`` (p3 only).

Per the dataset notes, the published signals may be misaligned by up to
100 ms. We don't attempt cross-signal alignment for v1 -- the benchmark
runs against ONE signal at a time, so an absolute MIDI-to-audio offset
is the only thing that matters. We record ``signal`` (``directinput``
vs ``micamp``) on the case metadata so the benchmark report can break
results down by it.

Reference: https://zenodo.org/records/14963133
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Sequence

from aural_ingest.transcription import MelodicNote

from .common import GroundTruthCase


# Reuse the E-GMD MIDI parser's plumbing -- Guitar-TECHS MIDIs are also
# plain format-1 with melodic channel events; we just pick up all
# channels rather than channel 9 only.
from .egmd import _read_vlq


def _parse_melodic_midi(midi_path: Path) -> list[MelodicNote]:
    """Pure-stdlib parser for Guitar-TECHS melodic MIDIs.

    Mirrors the drum parser in ``egmd.py`` but accepts every non-channel-
    -9 NoteOn (Guitar-TECHS MIDIs encode notes on channel 0). Tracks
    NoteOn/NoteOff pairs and emits ``t_off`` from the matching NoteOff
    (or end-of-track if absent).
    """
    data = midi_path.read_bytes()
    if len(data) < 14 or data[:4] != b"MThd":
        raise ValueError(f"not a MIDI file: {midi_path}")
    header_len = int.from_bytes(data[4:8], "big")
    fmt = int.from_bytes(data[8:10], "big")
    ntracks = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big")
    if division & 0x8000:
        raise ValueError(f"SMPTE timing not supported: {midi_path}")
    ticks_per_quarter = division
    pos = 8 + header_len
    if fmt not in (0, 1):
        raise ValueError(f"unsupported MIDI format {fmt}")

    tempo_changes: list[tuple[int, int]] = [(0, 500_000)]
    # (tick, pitch, velocity, is_note_on, channel).
    raw: list[tuple[int, int, int, bool, int]] = []
    track_end_ticks: list[int] = []

    for _ in range(ntracks):
        if pos + 8 > len(data) or data[pos : pos + 4] != b"MTrk":
            raise ValueError(f"MTrk missing in {midi_path}")
        track_len = int.from_bytes(data[pos + 4 : pos + 8], "big")
        pos += 8
        end = pos + track_len
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
                    n = data[pos]
                    v = data[pos + 1]
                    pos += 2
                    if high == 0x90 and v > 0 and channel != 9:
                        raw.append((tick, int(n), int(v), True, channel))
                    elif high == 0x80 or (high == 0x90 and v == 0):
                        raw.append((tick, int(n), 0, False, channel))
                elif high in (0xC0, 0xD0):
                    pos += 1
                else:
                    raise ValueError(f"unknown MIDI status 0x{status:02X}")
        track_end_ticks.append(tick)
        pos = end

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

    end_tick = max(track_end_ticks) if track_end_ticks else 0

    # Pair NoteOns with their NoteOffs (per channel + pitch).
    open_notes: dict[tuple[int, int], tuple[int, int]] = {}  # (channel,pitch) -> (start_tick, vel)
    notes: list[MelodicNote] = []
    for tick, pitch, vel, is_on, channel in sorted(raw, key=lambda r: r[0]):
        key = (channel, pitch)
        if is_on:
            # If a previous NoteOn for the same key is still open, close it now.
            if key in open_notes:
                start_tick, start_vel = open_notes.pop(key)
                notes.append(
                    MelodicNote(
                        t_on=tick_to_sec(start_tick),
                        t_off=tick_to_sec(tick),
                        pitch=pitch,
                        velocity=start_vel,
                        instrument="guitar",
                    )
                )
            open_notes[key] = (tick, vel)
        else:
            if key in open_notes:
                start_tick, start_vel = open_notes.pop(key)
                notes.append(
                    MelodicNote(
                        t_on=tick_to_sec(start_tick),
                        t_off=tick_to_sec(tick),
                        pitch=pitch,
                        velocity=start_vel,
                        instrument="guitar",
                    )
                )

    # Close any still-open notes at end-of-track.
    for (_, pitch), (start_tick, start_vel) in open_notes.items():
        notes.append(
            MelodicNote(
                t_on=tick_to_sec(start_tick),
                t_off=tick_to_sec(end_tick),
                pitch=pitch,
                velocity=start_vel,
                instrument="guitar",
            )
        )

    notes.sort(key=lambda n: (n.t_on, n.pitch))
    return notes


_VALID_SIGNALS = ("directinput", "micamp")


def _scan_subset(
    subset_root: Path,
    *,
    signal: str,
) -> list[tuple[Path, Path, str, str]]:
    """Return ``(midi_path, audio_path, player, category)`` tuples.

    ``subset_root`` is one of the ``guitar_techs_p<n>_<category>``
    directories. The signal is ``directinput`` or ``micamp``.
    """
    if signal not in _VALID_SIGNALS:
        raise ValueError(f"unknown signal: {signal!r}")
    # The next directory level is e.g. ``P1_chords``.
    inner = next((p for p in subset_root.iterdir() if p.is_dir()), None)
    if inner is None:
        return []
    midi_dir = inner / "midi"
    audio_dir = inner / "audio" / signal
    if not midi_dir.is_dir() or not audio_dir.is_dir():
        return []

    parts = inner.name.split("_", 1)
    player = parts[0]  # "P1"
    category = parts[1] if len(parts) > 1 else "unknown"  # "chords"

    pairs: list[tuple[Path, Path, str, str]] = []
    for midi_path in sorted(midi_dir.glob("midi_*.mid")):
        suffix = midi_path.stem[len("midi_") :]
        audio_path = audio_dir / f"{signal}_{suffix}.wav"
        if audio_path.is_file():
            pairs.append((midi_path, audio_path, player, category))
    return pairs


def yield_cases(
    corpus_root: Path,
    *,
    signal: str = "directinput",
    subsets: Sequence[str] | None = None,
    limit: int | None = None,
) -> Iterator[GroundTruthCase]:
    """Yield ``GroundTruthCase``s from Guitar-TECHS.

    Parameters
    ----------
    corpus_root
        ``E:\\AudioSourceOfTruthData\\extracted\\guitar_techs``.
    signal
        ``directinput`` (default; the DI source -- cleanest reference
        for transcribers) or ``micamp`` (amplifier mic).
    subsets
        Iterable of subset directory names to include (e.g.
        ``("guitar_techs_p3_music",)`` for the music-pilot only).
        ``None`` includes every subset present on disk.
    limit
        Cap on cases emitted.
    """
    if not corpus_root.is_dir():
        return
    all_subsets = sorted(p for p in corpus_root.iterdir() if p.is_dir())
    if subsets is not None:
        wanted = set(subsets)
        all_subsets = [p for p in all_subsets if p.name in wanted]

    count = 0
    for subset_root in all_subsets:
        pairs = _scan_subset(subset_root, signal=signal)
        for midi_path, audio_path, player, category in pairs:
            try:
                notes = _parse_melodic_midi(midi_path)
            except Exception:
                continue
            if not notes:
                continue
            duration_sec = max(n.t_off for n in notes)
            yield GroundTruthCase(
                case_id=f"guitar_techs:{player}_{category}:{midi_path.stem}:{signal}",
                instrument="guitar",
                audio_path=audio_path,
                duration_sec=duration_sec,
                melodic_notes=tuple(notes),
                metadata={
                    "player": player,
                    "category": category,
                    "signal": signal,
                },
            )
            count += 1
            if limit is not None and count >= limit:
                return
