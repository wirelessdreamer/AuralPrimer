"""
Synthetic piano corpus adapter.

Reads the 4 authored MIDI files + their pretty_midi-synthesised WAVs
from ``D:/AudioSourceOfTruth/data/piano_synthetic/`` and yields one
``GroundTruthCase`` per case.

The corpus is small (4 cases, 117 notes total) and authored to stress
distinct piano-transcription failure modes:

    - ``scale_c_2oct``           single-note pitch range coverage
    - ``triad_arp_progression``  dense eighth-note onsets with pitch repetition
    - ``block_chords``           polyphony (3 simultaneous pitches)
    - ``two_voice``              wide pitch separation (LH octave bass + RH melody)

Timbre caveat: the WAVs are rendered via ``pretty_midi.synthesize``
(additive sine bursts), NOT a sampled piano. The benchmark therefore
measures a LOWER BOUND on production keys accuracy -- real piano
material exercises richer harmonic content the trained models
(piano_pti, basic_pitch, etc.) can lean on. This adapter is a sanity
check that the production pipeline is not catastrophically broken on
isolated cleanly-articulated notes. For a realistic-timbre benchmark,
MAESTRO must be added to ``D:/AudioSourceOfTruth/data/maestro/``.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Iterator

import soundfile as sf

from aural_ingest.transcription import MelodicNote

from .common import GroundTruthCase


def _read_midi_notes(mid_path: Path) -> list[MelodicNote]:
    """Pure-stdlib MIDI parser, format-0/1, type-0/1 tracks.

    Same approach as ``egmd.py`` -- we don't want a hard ``mido`` /
    ``pretty_midi`` dep at the adapter layer. The Ableton-exported
    files are simple type-1 with a single track of NoteOn/NoteOff
    pairs at a known tempo.
    """
    buf = mid_path.read_bytes()
    if not buf.startswith(b"MThd"):
        raise ValueError(f"not a MIDI file: {mid_path}")

    header_len = struct.unpack(">I", buf[4:8])[0]
    fmt, num_tracks, division = struct.unpack(">HHH", buf[8:14])
    if division & 0x8000:
        # SMPTE timing -- not used by Ableton exports for this corpus
        raise ValueError(f"SMPTE timing not supported: {mid_path}")
    ticks_per_quarter = division

    # Default tempo (BPM 120 = 500_000 us/quarter)
    us_per_quarter = 500_000

    notes: list[MelodicNote] = []
    pos = 8 + header_len

    while pos < len(buf):
        if buf[pos:pos + 4] != b"MTrk":
            break
        track_len = struct.unpack(">I", buf[pos + 4:pos + 8])[0]
        track_end = pos + 8 + track_len
        tpos = pos + 8

        cur_ticks = 0
        running_status: int | None = None
        active: dict[int, tuple[float, int]] = {}  # pitch -> (start_sec, velocity)

        while tpos < track_end:
            # Variable-length delta-time
            delta = 0
            while True:
                b = buf[tpos]
                tpos += 1
                delta = (delta << 7) | (b & 0x7F)
                if not (b & 0x80):
                    break
            cur_ticks += delta

            cur_sec = (cur_ticks * us_per_quarter) / (ticks_per_quarter * 1_000_000)

            status = buf[tpos]
            if status & 0x80:
                tpos += 1
                running_status = status
            else:
                status = running_status if running_status is not None else 0

            event_type = status & 0xF0

            if status == 0xFF:
                # Meta event
                meta_type = buf[tpos]
                tpos += 1
                meta_len = 0
                while True:
                    b = buf[tpos]
                    tpos += 1
                    meta_len = (meta_len << 7) | (b & 0x7F)
                    if not (b & 0x80):
                        break
                if meta_type == 0x51 and meta_len == 3:
                    us_per_quarter = (
                        (buf[tpos] << 16) | (buf[tpos + 1] << 8) | buf[tpos + 2]
                    )
                tpos += meta_len
                if meta_type == 0x2F:
                    break  # End-of-track
            elif status in (0xF0, 0xF7):
                # Sysex
                sysex_len = 0
                while True:
                    b = buf[tpos]
                    tpos += 1
                    sysex_len = (sysex_len << 7) | (b & 0x7F)
                    if not (b & 0x80):
                        break
                tpos += sysex_len
            elif event_type == 0x90:  # NoteOn
                pitch = buf[tpos]
                velocity = buf[tpos + 1]
                tpos += 2
                if velocity == 0:
                    if pitch in active:
                        start, vel = active.pop(pitch)
                        notes.append(
                            MelodicNote(
                                t_on=start,
                                t_off=cur_sec,
                                pitch=int(pitch),
                                velocity=int(vel),
                                instrument="keys",
                            )
                        )
                else:
                    active[pitch] = (cur_sec, velocity)
            elif event_type == 0x80:  # NoteOff
                pitch = buf[tpos]
                _ = buf[tpos + 1]
                tpos += 2
                if pitch in active:
                    start, vel = active.pop(pitch)
                    notes.append(
                        MelodicNote(
                            t_on=start,
                            t_off=cur_sec,
                            pitch=int(pitch),
                            velocity=int(vel),
                            instrument="keys",
                        )
                    )
            elif event_type in (0xA0, 0xB0, 0xE0):
                tpos += 2  # 2-data-byte events
            elif event_type in (0xC0, 0xD0):
                tpos += 1  # 1-data-byte events
            else:
                tpos += 1  # Defensive skip

        # Flush any hanging notes (open at end-of-track)
        for pitch, (start, vel) in active.items():
            notes.append(
                MelodicNote(
                    t_on=start,
                    t_off=cur_sec,
                    pitch=int(pitch),
                    velocity=int(vel),
                    instrument="keys",
                )
            )

        pos = track_end

    notes.sort(key=lambda n: (n.t_on, n.pitch))
    return notes


def yield_cases(
    corpus_root: Path = Path("D:/AudioSourceOfTruth/data/piano_synthetic"),
    *,
    limit: int | None = None,
) -> Iterator[GroundTruthCase]:
    """Yield one ``GroundTruthCase`` per MID/WAV pair in the corpus.

    Each case's metadata records ``signal=synth_additive_sine`` so the
    timbre caveat is visible in bucketed reports.
    """
    mids = sorted(corpus_root.glob("*.mid"))
    if limit is not None:
        mids = mids[:limit]
    for mid in mids:
        wav = mid.with_suffix(".wav")
        if not wav.is_file():
            continue
        notes = _read_midi_notes(mid)
        try:
            info = sf.info(str(wav))
            duration_sec = float(info.duration)
        except Exception:
            duration_sec = 0.0
        yield GroundTruthCase(
            case_id=f"piano_synth:{mid.stem}",
            instrument="keys",
            audio_path=wav,
            duration_sec=duration_sec,
            melodic_notes=tuple(notes),
            metadata={
                "split": "synthetic",
                "signal": "synth_additive_sine",
                "case_name": mid.stem,
            },
        )
