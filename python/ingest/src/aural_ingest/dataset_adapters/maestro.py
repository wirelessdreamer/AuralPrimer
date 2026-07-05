"""
MAESTRO v3.0.0 adapter (real acoustic-piano ground truth for ``keys``).

Yields one ``GroundTruthCase`` per WAV+MIDI pair listed in the
``maestro-v3.0.0.csv`` metadata file. Each MIDI is a captured Disklavier
performance aligned to its audio to ~3 ms (per the dataset notes), so no
per-case offset estimation is required. The MIDI is parsed once with the
same pure-stdlib reader the sibling adapters use (no ``mido`` /
``pretty_midi`` dependency at the adapter layer) and mapped into the
canonical ``MelodicNote`` list the melodic benchmark scoring consumes
with pitch-exact matching.

This is the realistic-timbre counterpart to ``piano_synthetic.py``:
where that corpus renders authored MIDI to additive-sine WAVs (a *lower
bound* on keys accuracy), MAESTRO's WAVs are real grand-piano captures,
so a sweep here reflects the harmonic richness the trained models
(piano_pti, basic_pitch, etc.) actually lean on in production.

Metadata kept on the case (``split``, ``composer``, ``title``, ``year``)
enables per-bucket aggregation in the benchmark report.

Gated on the ``AURAL_MAESTRO_ROOT`` env var (consistent with the other
drive-backed adapters): when unset or pointing at a missing tree, the
adapter yields nothing gracefully rather than raising, so
``gt-benchmark --dataset maestro`` degrades to a clean "no cases yielded"
on a machine that hasn't downloaded the corpus.

Reference: https://magenta.tensorflow.org/datasets/maestro
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Iterator

from aural_ingest.transcription import MelodicNote

from .common import GroundTruthCase


# Env var the corpus root is read from when a caller doesn't pass one
# explicitly. Consistent with the other drive-backed adapters (E-GMD /
# GuitarSet resolve their roots from the CLI ``--corpus-root``, but
# MAESTRO also honours this env var so a fresh machine can point at its
# download without editing the sweep command).
MAESTRO_ROOT_ENV = "AURAL_MAESTRO_ROOT"

# CSV / on-disk filename for the v3.0.0 metadata index.
_METADATA_CSV_NAME = "maestro-v3.0.0.csv"


def _read_vlq(data: bytes, pos: int) -> tuple[int, int]:
    """Read a MIDI Variable-Length Quantity. Returns ``(value, new_pos)``."""
    value = 0
    while True:
        b = data[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if b & 0x80 == 0:
            return value, pos


def _parse_piano_midi(midi_path: Path) -> list[MelodicNote]:
    """Pure-stdlib MIDI parser for MAESTRO's captured-piano files.

    MAESTRO MIDIs are format-0/1 files with note events on channel 0 and
    a running tempo map. We build a cumulative (tick -> seconds) table
    from every tempo meta event across all tracks (same shape as the
    E-GMD adapter's tempo handling), collect NoteOn/NoteOff pairs, and
    emit one ``MelodicNote`` per note.

    Sustain pedal (CC64) is ignored for offset purposes: the note's
    ``t_off`` is its own NoteOff, which is what the captured MIDI records
    regardless of pedal state. The melodic scoring is onset+pitch based,
    so exact offset semantics don't affect F1 here.

    Notes are labelled ``instrument="keys"`` so the case flows through
    production's keys-tuned transcription path, matching
    ``piano_synthetic.py``.
    """
    data = midi_path.read_bytes()
    if len(data) < 14 or data[:4] != b"MThd":
        raise ValueError(f"not a MIDI file: {midi_path}")
    header_len = int.from_bytes(data[4:8], "big")
    if header_len < 6:
        raise ValueError(f"unexpected MIDI header length: {header_len}")
    fmt = int.from_bytes(data[8:10], "big")
    ntracks = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big")
    if division & 0x8000:
        raise ValueError(f"SMPTE timing not supported in MAESTRO adapter: {midi_path}")
    ticks_per_quarter = division
    if ticks_per_quarter <= 0:
        raise ValueError(f"invalid ticks-per-quarter in {midi_path}")
    if fmt not in (0, 1):
        raise ValueError(f"unsupported MIDI format {fmt}: {midi_path}")

    pos = 8 + header_len

    # Collect a global tempo map (tick, us_per_quarter) plus raw note
    # events (tick, is_on, pitch, velocity) across all tracks. In a
    # captured single-track file these all live together; format-1 files
    # may carry tempo on a conductor track.
    tempo_changes: list[tuple[int, int]] = [(0, 500_000)]
    raw_events: list[tuple[int, bool, int, int]] = []  # (tick, is_on, pitch, vel)

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
                if high in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                    if pos + 2 > end:
                        raise ValueError("note event truncated")
                    n = data[pos]
                    v = data[pos + 1]
                    pos += 2
                    if high == 0x90 and v > 0:
                        raw_events.append((tick, True, int(n), int(v)))
                    elif high == 0x80 or (high == 0x90 and v == 0):
                        raw_events.append((tick, False, int(n), int(v)))
                elif high in (0xC0, 0xD0):
                    pos += 1
                else:
                    raise ValueError(
                        f"unknown MIDI status 0x{status:02X} in {midi_path}"
                    )
        pos = end

    if not raw_events:
        return []

    # Build a deduped cumulative-time table (keep last tempo per tick).
    tempo_changes.sort()
    dedup: list[tuple[int, int]] = []
    for t, tempo in tempo_changes:
        if dedup and dedup[-1][0] == t:
            dedup[-1] = (t, tempo)
        else:
            dedup.append((t, tempo))

    def tick_to_sec(target: int) -> float:
        sec = 0.0
        prev_tick = 0
        cur_tempo = dedup[0][1]
        for t, tempo in dedup:
            if t >= target:
                break
            sec += (t - prev_tick) * cur_tempo / 1_000_000 / ticks_per_quarter
            prev_tick = t
            cur_tempo = tempo
        sec += (target - prev_tick) * cur_tempo / 1_000_000 / ticks_per_quarter
        return sec

    # Pair NoteOn/NoteOff in event order (stable by tick). A pitch may be
    # retriggered before its previous NoteOff in dense piano passages, so
    # we FIFO-match per pitch.
    raw_events.sort(key=lambda e: (e[0], 0 if not e[1] else 1))
    active: dict[int, list[tuple[int, int]]] = {}  # pitch -> [(on_tick, vel)]
    notes: list[MelodicNote] = []
    for t, is_on, pitch, vel in raw_events:
        if is_on:
            active.setdefault(pitch, []).append((t, vel))
        else:
            stack = active.get(pitch)
            if stack:
                on_tick, on_vel = stack.pop(0)
                notes.append(
                    MelodicNote(
                        t_on=tick_to_sec(on_tick),
                        t_off=tick_to_sec(t),
                        pitch=pitch,
                        velocity=on_vel,
                        instrument="keys",
                    )
                )

    # Flush any notes left open at end-of-file (rare; bad capture tail).
    if active:
        last_tick = raw_events[-1][0]
        last_sec = tick_to_sec(last_tick)
        for pitch, stack in active.items():
            for on_tick, on_vel in stack:
                notes.append(
                    MelodicNote(
                        t_on=tick_to_sec(on_tick),
                        t_off=last_sec,
                        pitch=pitch,
                        velocity=on_vel,
                        instrument="keys",
                    )
                )

    notes.sort(key=lambda n: (n.t_on, n.pitch))
    return notes


def _resolve_root(corpus_root: Path | str | None) -> Path | None:
    """Resolve the MAESTRO root from an explicit arg or the env var.

    Returns ``None`` (rather than raising) when nothing usable is
    configured, so the adapter can yield nothing gracefully on a machine
    that hasn't downloaded the corpus. The v3.0.0 archive extracts to a
    ``maestro-v3.0.0`` directory, so we accept either that directory
    directly or a parent that contains it.
    """
    candidate: Path | None = None
    if corpus_root is not None:
        candidate = Path(corpus_root)
    else:
        env = os.environ.get(MAESTRO_ROOT_ENV)
        if env:
            candidate = Path(env)
    if candidate is None:
        return None
    if not candidate.exists():
        return None
    # Accept either the extracted dataset dir or a parent holding it.
    if (candidate / _METADATA_CSV_NAME).is_file():
        return candidate
    nested = candidate / "maestro-v3.0.0"
    if (nested / _METADATA_CSV_NAME).is_file():
        return nested
    return candidate


def yield_cases(
    corpus_root: Path | str | None = None,
    *,
    split: str | None = "test",
    limit: int | None = None,
) -> Iterator[GroundTruthCase]:
    """Yield ``GroundTruthCase``s from a prepared MAESTRO v3.0.0 corpus.

    Parameters
    ----------
    corpus_root
        Directory containing ``maestro-v3.0.0.csv`` and the per-year
        subdirectories of paired ``.wav`` / ``.midi`` files (i.e. the
        extracted ``maestro-v3.0.0`` root). When ``None``, resolved from
        the ``AURAL_MAESTRO_ROOT`` env var. If neither is set or the tree
        is absent, yields nothing (no exception).
    split
        ``"test"`` (default) / ``"train"`` / ``"validation"``, or
        ``None`` to yield every row regardless of split.
    limit
        Stop after this many cases. Useful for fast smoke runs.
    """
    root = _resolve_root(corpus_root)
    if root is None:
        return

    metadata_csv = root / _METADATA_CSV_NAME
    if not metadata_csv.is_file():
        # Root exists but isn't a MAESTRO tree -- yield nothing.
        return

    count = 0
    with metadata_csv.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if split is not None and row.get("split") != split:
                continue
            audio_rel = row.get("audio_filename") or ""
            midi_rel = row.get("midi_filename") or ""
            if not audio_rel or not midi_rel:
                continue
            audio_path = root / audio_rel
            midi_path = root / midi_rel
            if not audio_path.is_file() or not midi_path.is_file():
                # Partial downloads / year filters -- skip missing pairs.
                continue
            try:
                notes = _parse_piano_midi(midi_path)
            except Exception:
                continue
            if not notes:
                continue

            duration_sec = float(row.get("duration", "0") or 0.0)
            # ``audio_filename`` is unique within the CSV (e.g.
            # ``2004/MIDI-Unprocessed_..._wav--1.wav``); use its stem for a
            # stable, readable case id.
            case_id = f"maestro:{Path(audio_rel).stem}"
            yield GroundTruthCase(
                case_id=case_id,
                instrument="keys",
                audio_path=audio_path,
                duration_sec=duration_sec,
                melodic_notes=tuple(notes),
                metadata={
                    "split": row.get("split", ""),
                    "composer": row.get("canonical_composer", ""),
                    "title": row.get("canonical_title", ""),
                    "year": row.get("year", ""),
                    "signal": "acoustic_grand_piano",
                },
            )
            count += 1
            if limit is not None and count >= limit:
                return
