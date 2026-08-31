"""Prepare a MIDI for rendering in Ableton without losing the performance.

Ableton does not import MIDI tempo maps. Loading a piano-midi.de file into a
clip as-is plays every note on a rigid grid at the set tempo, which for
Clair de Lune (8 to 132 BPM) is unrecognisable. So a render needs the rubato
moved OUT of the tempo map and INTO the note positions: rewrite every event at
its true wall-clock position under one fixed tempo, and Live plays the
performance correctly because the notes are where they always were.

That flattened file is render prep and nothing else. **The chart is always
imported from the original**, which carries the same note times plus the tempo
map -- so the pack gets the render's audio and the source's bar lines. Building
the chart from the flattened file instead is what put a flat 120 BPM into the
classical packs, and with it measures falling wherever two seconds of wall
clock happened to land.

The gate here measures the notes, not the file. The previous version compared
``MidiFile.length``, which includes trailing silence: it reported 71 seconds of
"drift" on Bach because the original has a long tail after the last note, and
that false alarm would have masked a real error somewhere else. Note span is
what has to survive.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: One tempo for the whole file. The value is arbitrary -- the performance
#: lives in the note positions now -- but a round number keeps Live's grid
#: readable for anyone who opens the set.
FIXED_BPM = 120.0
TICKS_PER_BEAT = 480

#: How far a note may move before the flatten is considered to have failed.
#: Tick quantisation at 480 PPQ and 120 BPM is about 1ms, so anything past a
#: few milliseconds is a real error rather than rounding.
MAX_NOTE_DRIFT_SEC = 0.005


@dataclass
class FlattenResult:
    source: Path
    output: Path
    notes: int
    source_span_sec: float
    output_span_sec: float
    max_note_drift_sec: float
    tempo_events_removed: int
    ok: bool
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "output": str(self.output),
            "notes": self.notes,
            "source_span_sec": round(self.source_span_sec, 4),
            "output_span_sec": round(self.output_span_sec, 4),
            "max_note_drift_ms": round(self.max_note_drift_sec * 1000, 3),
            "tempo_events_removed": self.tempo_events_removed,
            "ok": self.ok,
            "warnings": self.warnings,
        }


def _note_onsets(midi_path: Path) -> list[float]:
    """Absolute onset seconds for every note, tempo map applied."""
    from aural_ingest.midi_feedpak import read_midi_roles

    roles, _duration, _tmap = read_midi_roles(midi_path)
    return sorted(note.t_on for notes in roles.values() for note in notes)


def flatten(
    src: str | Path,
    dst: str | Path,
    *,
    bpm: float = FIXED_BPM,
    tpb: int = TICKS_PER_BEAT,
) -> FlattenResult:
    """Bake ``src``'s tempo map into note positions at a constant ``bpm``."""
    import mido

    src, dst = Path(src), Path(dst)
    mid = mido.MidiFile(str(src))

    # Iterating the MidiFile rather than each track is what applies the tempo
    # map: these are format-1 files whose tempo events live in track 0, so
    # per-track iteration would time the note tracks at a default 120 BPM and
    # mangle every duration -- silently, and in the direction that looks fine.
    timed: list[tuple[float, Any]] = []
    seconds = 0.0
    removed = 0
    for msg in mid:                      # msg.time is delta SECONDS here
        seconds += msg.time
        if msg.type == "set_tempo":
            removed += 1
            continue                     # the rubato moves into the timing
        if msg.is_meta and msg.type not in ("time_signature", "key_signature"):
            continue
        timed.append((seconds, msg))

    out = mido.MidiFile(ticks_per_beat=tpb)
    track = mido.MidiTrack()
    out.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))

    sec_per_tick = 60.0 / (bpm * tpb)
    prev_tick = 0
    for at, msg in timed:
        tick = int(round(at / sec_per_tick))
        track.append(msg.copy(time=max(0, tick - prev_tick)))
        prev_tick = tick

    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(str(dst))

    # Verify against the notes, not the file. A long trailing silence is not a
    # defect; a note landing somewhere else is.
    before, after = _note_onsets(src), _note_onsets(dst)
    warnings: list[str] = []
    if len(before) != len(after):
        warnings.append(f"note count changed: {len(before)} -> {len(after)}")
        drift = float("inf")
    else:
        drift = max((abs(a - b) for a, b in zip(before, after)), default=0.0)
        if drift > MAX_NOTE_DRIFT_SEC:
            warnings.append(
                f"notes moved by up to {drift * 1000:.1f}ms, over the "
                f"{MAX_NOTE_DRIFT_SEC * 1000:.0f}ms budget"
            )

    return FlattenResult(
        source=src,
        output=dst,
        notes=sum(1 for _at, m in timed if m.type == "note_on" and m.velocity > 0),
        source_span_sec=(before[-1] - before[0]) if before else 0.0,
        output_span_sec=(after[-1] - after[0]) if after else 0.0,
        max_note_drift_sec=drift,
        tempo_events_removed=removed,
        ok=not warnings,
        warnings=warnings,
    )


def prepare_render(
    src: str | Path,
    out_dir: str | Path,
    *,
    lead_in_sec: float = 0.0,
    bpm: float = FIXED_BPM,
) -> dict[str, Any]:
    """Write a render-ready MIDI beside a note saying how to finish the job.

    ``lead_in_sec`` shifts everything later so a render has room before the
    first note. It defaults to zero: a lead-in has to be trimmed back out of
    the audio afterwards by exactly the same amount, and an offset nobody
    wrote down is how the packs ended up misaligned the first time.
    """
    import mido

    src, out_dir = Path(src), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / src.name

    result = flatten(src, dst, bpm=bpm)

    if lead_in_sec > 0:
        mid = mido.MidiFile(str(dst))
        ticks = int(round(mido.second2tick(lead_in_sec, mid.ticks_per_beat,
                                           mido.bpm2tempo(bpm))))
        for track in mid.tracks:
            for i, msg in enumerate(track):
                if msg.type in ("note_on", "note_off"):
                    track[i] = msg.copy(time=msg.time + ticks)
                    break
        mid.save(str(dst))

    payload = result.as_dict()
    payload["lead_in_sec"] = lead_in_sec
    # The chart source, recorded next to the render source so the pairing is
    # not left to memory. Importing the flattened file is the mistake this
    # whole module exists to stop.
    payload["chart_source"] = str(src)
    payload["render_source"] = str(dst)
    payload["note"] = (
        "Render this file in Ableton. Import the pack from chart_source with "
        "aural_ingest import-midi, attaching the render as --audio: the "
        "original carries the tempo map, the flattened file does not."
    )
    (out_dir / f"{src.stem}.render.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8")
    return payload
