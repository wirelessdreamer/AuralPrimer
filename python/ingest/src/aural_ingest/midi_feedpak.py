"""Build a ``.feedpak`` directly from a MIDI score, keeping its exact timing.

For material that was RENDERED from a MIDI -- sequenced libraries, our own
classical set -- the MIDI is not an estimate of the recording, it is the thing
the recording was made from. Note times are exact by construction, and any step
that re-derives them can only lose.

That is what happened. The classical packs were imported by converting their
MIDI to MusicXML and building from the score. MusicXML carries note VALUES, not
milliseconds, so a human performance with unquantised ticks is snapped to the
nearest notated subdivision, and the error accumulates rather than cancelling:
measured against the source, Fur Elise ended 5.8s long, Debussy 36s, Schumann
22s. On a rubato piece the chart drifts away from the recording and back, which
under Wait mode is a stall waiting for a note the audio already played.

So this path exists to make the timing-exact case a first-class import rather
than a repair. It reads notes straight off the MIDI, keeps the file's own tempo
map, and derives the beat grid from that map instead of inventing one.

Use ``import-musicxml`` when the score is the source of truth and the audio is
incidental. Use this when the AUDIO was rendered from the MIDI and the two must
agree sample for sample.
"""
from __future__ import annotations

import bisect
import json
import shutil
from pathlib import Path
from typing import Any

from aural_ingest.arrangement_prep import MIN_NOTE_SEC, ROLE_ORDER, ROLE_TRACK_NAME
from aural_ingest.feedpak_writer import write_feedpak

_AUDIO_EXTS = (".wav", ".flac", ".mp3", ".ogg", ".m4a")

#: Default role for a MIDI whose tracks say nothing useful. These sources are
#: piano scores; anything else is expected to name its tracks.
_DEFAULT_ROLE = "keys"

#: How far a render may stop short of the last note before it is rejected. A
#: bounce ends on a buffer boundary and a final note may be released into a
#: fade, so a fraction of a second is normal; seconds are a truncated render.
_RENDER_TAIL_TOLERANCE_SEC = 0.75

#: Lead-in worth correcting. Below this the render is already aligned and
#: trimming would be churn; above it the chart and the audio disagree audibly.
MAX_UNALIGNED_LEAD_SEC = 0.05

#: An apparent lead-in this large that could NOT be measured confidently is a
#: refusal rather than a shrug: real renders correlate at 14x to 70x, so a weak
#: peak this far out means the audio and the score do not match.
UNMEASURABLE_LEAD_LIMIT_SEC = 1.0

#: Track-name substrings that identify a role, most specific first so that
#: "lead guitar" is not swallowed by "guitar".
_ROLE_HINTS: tuple[tuple[str, str], ...] = (
    ("lead guitar", "lead_guitar"),
    ("rhythm guitar", "rhythm_guitar"),
    ("guitar", "rhythm_guitar"),
    ("bass", "bass"),
    ("vocal", "vocals"),
    ("voice", "vocals"),
    ("drum", "drums"),
    ("piano", "keys"),
    ("keys", "keys"),
    ("synth", "keys"),
)


def role_for_track(name: str) -> str:
    """Map a MIDI track name to a pack role, defaulting to keys."""
    lowered = (name or "").strip().lower()
    for hint, role in _ROLE_HINTS:
        if hint in lowered:
            return role
    return _DEFAULT_ROLE


def tempo_map(mid: Any) -> list[tuple[int, float, int]]:
    """``(tick, seconds, tempo)`` at every tempo change, cumulative."""
    import mido

    events: list[tuple[int, int]] = []
    for track in mid.tracks:
        now = 0
        for msg in track:
            now += msg.time
            if msg.type == "set_tempo":
                events.append((now, msg.tempo))
    events.sort()
    if not events or events[0][0] != 0:
        events.insert(0, (0, 500000))

    out: list[tuple[int, float, int]] = [(0, 0.0, events[0][1])]
    for tick, tempo in events[1:]:
        prev_tick, prev_sec, prev_tempo = out[-1]
        if tick == prev_tick:
            # Two tempi at one tick: the later one is what plays.
            out[-1] = (tick, prev_sec, tempo)
            continue
        out.append((tick, prev_sec + mido.tick2second(
            tick - prev_tick, mid.ticks_per_beat, prev_tempo), tempo))
    return out


def read_midi_roles(
    midi_path: str | Path,
) -> tuple[dict[str, list], float, list[tuple[int, float, int]]]:
    """Notes per role, duration, and the file's tempo map -- times unaltered."""
    import mido

    from aural_ingest.transcription import MelodicNote

    mid = mido.MidiFile(str(midi_path))
    tmap = tempo_map(mid)
    ticks = [entry[0] for entry in tmap]

    def to_sec(tick: int) -> float:
        i = bisect.bisect_right(ticks, tick) - 1
        base_tick, base_sec, tempo = tmap[i]
        return base_sec + mido.tick2second(tick - base_tick, mid.ticks_per_beat, tempo)

    roles: dict[str, list] = {}
    duration = 0.0
    for track in mid.tracks:
        role = role_for_track(track.name)
        open_notes: dict[int, list[tuple[int, int]]] = {}
        now = 0
        for msg in track:
            now += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                open_notes.setdefault(msg.note, []).append((now, msg.velocity))
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                pending = open_notes.get(msg.note)
                if not pending:
                    # A release with nothing holding it: a malformed file, not
                    # a note. Dropping it beats inventing an onset for it.
                    continue
                on_tick, velocity = pending.pop(0)
                t_on = to_sec(on_tick)
                t_off = max(to_sec(now), t_on + MIN_NOTE_SEC)
                roles.setdefault(role, []).append(MelodicNote(
                    t_on=t_on, t_off=t_off, pitch=int(msg.note),
                    velocity=int(velocity) or 100, instrument=role))
                duration = max(duration, t_off)

    for notes in roles.values():
        notes.sort(key=lambda n: (n.t_on, n.pitch))
    return roles, duration, tmap


def _build_notes_mid(roles: dict[str, list], out_path: Path) -> int:
    """One named instrument per role, at the times read off the source."""
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    total = 0
    for role in ROLE_ORDER:
        notes = roles.get(role)
        if not notes:
            continue
        inst = pretty_midi.Instrument(program=0, is_drum=False,
                                      name=ROLE_TRACK_NAME.get(role, role.title()))
        for note in notes:
            if not (0 <= note.pitch <= 127):
                continue
            inst.notes.append(pretty_midi.Note(
                velocity=int(note.velocity) or 100, pitch=int(note.pitch),
                start=float(note.t_on),
                end=float(max(note.t_off, note.t_on + MIN_NOTE_SEC))))
            total += 1
        if inst.notes:
            pm.instruments.append(inst)
    pm.write(str(out_path))
    return total


def _build_beats(
    tmap: list[tuple[int, float, int]],
    duration: float,
    beats_per_bar: int,
    out_path: Path,
) -> None:
    """Beats from the file's own tempo map, so the grid bends where it bends.

    Deriving them from one averaged bpm would put the bar lines somewhere the
    music never was -- the same failure this module exists to avoid,
    reintroduced a layer down.
    """
    import mido

    beats: list[dict[str, Any]] = []
    index = 0
    for i, (_tick, sec, tempo) in enumerate(tmap):
        end_sec = tmap[i + 1][1] if i + 1 < len(tmap) else duration
        step = mido.tick2second(1, 1, tempo)
        if step <= 0:
            continue
        t = sec
        while t < end_sec - 1e-9:
            beats.append({"t": round(t, 6), "beat": index % beats_per_bar})
            index += 1
            t += step
    if not beats:
        beats.append({"t": 0.0, "beat": 0})
    out_path.write_text(json.dumps({"beats": beats}), encoding="utf-8")


def audio_duration_sec(path: Path) -> float | None:
    """Length of an audio file, read from its header rather than decoded."""
    try:
        import soundfile as sf

        info = sf.info(str(path))
        return float(info.frames) / float(info.samplerate) if info.samplerate else None
    except Exception:
        return None


def check_render_covers_notes(
    audio: Path, last_onset_sec: float
) -> tuple[bool, str | None, float | None]:
    """Refuse a render that stops before the chart does.

    A studio render is bounced by hand, and the ways that goes wrong are
    mundane: the loop brace was left on an eight-bar region, the bounce ended
    at the last clip edge and clipped the final decay, the wrong track was
    soloed. The result is audio shorter than the notes it is supposed to carry,
    and the pack it makes stalls in Wait mode on a note the audio never plays.

    Checking the header costs nothing and turns that into an import-time error
    instead of a practice session that hangs.
    """
    duration = audio_duration_sec(audio)
    if duration is None:
        return True, None, None            # unreadable header is not evidence
    if duration + _RENDER_TAIL_TOLERANCE_SEC < last_onset_sec:
        return False, (
            f"{audio.name} is {duration:.1f}s but the chart's last note starts at "
            f"{last_onset_sec:.1f}s -- the render stops short of the score. "
            "Re-bounce over the whole arrangement, or pass the right file."
        ), duration
    return True, None, duration


def attribution_for(midi_path: Path) -> dict[str, Any] | None:
    """The attribution entry for ``midi_path``, from a sibling manifest.

    These sources are CC BY-SA: the attribution has to travel with the work,
    which means into the pack, not just into a folder on the machine that built
    it. The sibling file may be a list covering a whole collection (one entry
    per MIDI) or a single dict for one piece.
    """
    manifest = midi_path.parent / "attribution.json"
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        for entry in data:
            if not isinstance(entry, dict):
                continue
            named = entry.get("file") or ""
            if Path(str(named)).name.lower() == midi_path.name.lower():
                return entry
    return None


def _find_sibling_audio(midi_path: Path) -> Path | None:
    """A render beside the MIDI with the same stem, else a lone audio file."""
    for ext in _AUDIO_EXTS:
        candidate = midi_path.with_suffix(ext)
        if candidate.exists():
            return candidate
    audio = [p for p in midi_path.parent.iterdir()
             if p.is_file() and p.suffix.lower() in _AUDIO_EXTS]
    return audio[0] if len(audio) == 1 else None


def build_feedpak_from_midi(
    midi_path: str | Path,
    out_dir: str | Path,
    *,
    audio_path: str | Path | None = None,
    title: str | None = None,
    artist: str | None = None,
    genre: str | None = None,
    beats_per_bar: int = 4,
    align: bool = True,
) -> dict[str, Any]:
    """Write a ``.feedpak`` from ``midi_path``, keeping its note times exactly."""
    midi_path = Path(midi_path)
    out_dir = Path(out_dir)

    roles, duration, tmap = read_midi_roles(midi_path)
    if not any(roles.values()):
        raise ValueError(f"{midi_path.name}: no notes found in the MIDI")

    resolved_audio = Path(audio_path) if audio_path else _find_sibling_audio(midi_path)
    if not resolved_audio or not resolved_audio.exists():
        raise ValueError(
            f"{midi_path.name}: no audio found. A feedpak needs an audio stem -- "
            "put a render beside the MIDI or pass audio_path."
        )

    last_onset = max((n.t_on for notes in roles.values() for n in notes), default=0.0)
    covered, problem, audio_sec = check_render_covers_notes(resolved_audio, last_onset)
    if not covered:
        raise ValueError(f"{midi_path.name}: {problem}")

    pack_title = title or midi_path.stem
    pack_artist = artist or "Unknown"

    work_parent = out_dir / "_midi_work"
    song = work_parent / f"{midi_path.stem}.auralsong"
    if song.exists():
        shutil.rmtree(song)
    (song / "features").mkdir(parents=True, exist_ok=True)
    (song / "audio").mkdir(parents=True, exist_ok=True)

    note_count = _build_notes_mid(roles, song / "features" / "notes.mid")
    _build_beats(tmap, duration, beats_per_bar, song / "features" / "beats.json")

    # Every tempo the file declares, not an average of them.
    segments: list[dict[str, Any]] = []
    for i, (_tick, sec, tempo) in enumerate(tmap):
        end = tmap[i + 1][1] if i + 1 < len(tmap) else None
        segments.append({
            "bpm": round(60_000_000.0 / tempo, 6),
            "t0": round(sec, 6),
            "t1": (round(end, 6) if end is not None else None),
            "time_signature": f"{beats_per_bar}/4",
        })
    (song / "features" / "tempo_map.json").write_text(
        json.dumps({"segments": segments}), encoding="utf-8")

    # Align the render to the score before it becomes the pack's audio.
    #
    # A studio bounce is captured through the DAW transport, and the capture
    # starts before the music does -- across the classical set the gap ran
    # 5.04s to 5.50s. It is silent, so nothing about the audio looks wrong;
    # what goes wrong is that the chart says play at 0.9s and the recording
    # plays at 6.0s. The variance is why this is measured per render rather
    # than assumed: it is transport-race latency, not a count-in.
    ext = resolved_audio.suffix or ".wav"
    dest_audio = song / "audio" / f"mix{ext}"
    alignment = None
    applied_lead_in = 0.0
    if align:
        from aural_ingest.render_align import measure_lead_in, trim_lead_in

        onsets = sorted(n.t_on for notes in roles.values() for n in notes)
        alignment = measure_lead_in(resolved_audio, onsets)
        # A weak correlation peak means the lag could not be measured, which is
        # not the same as the render being wrong -- audio with no clear onsets
        # produces one. It only becomes a refusal when the apparent lag is too
        # large to be measurement noise, because then something really is off
        # and trimming by a number we do not trust would make it worse.
        if not alignment.ok and alignment.lag_sec > UNMEASURABLE_LEAD_LIMIT_SEC:
            raise ValueError(
                f"{midi_path.name}: the render looks {alignment.lag_sec:.2f}s "
                f"behind the score but {alignment.reason}. Refusing to guess -- "
                "pass --no-align to import it as-is."
            )
        if alignment.ok and alignment.lag_sec > MAX_UNALIGNED_LEAD_SEC:
            applied_lead_in = trim_lead_in(resolved_audio, dest_audio, alignment.lag_sec)
    if applied_lead_in == 0.0:
        shutil.copy2(resolved_audio, dest_audio)

    manifest = {
        "title": pack_title,
        "artist": pack_artist,
        "genre": (genre or "").strip(),
        "duration_sec": duration,
        "source": "midi",
        "assets": {
            "midi": {"notes_path": "features/notes.mid"},
            "features": {
                "tempo_map_path": "features/tempo_map.json",
                "beats_path": "features/beats.json",
            },
            "audio": {"mix_path": f"audio/mix{ext}"},
        },
    }
    (song / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = write_feedpak(song, out_dir)

    credit = attribution_for(midi_path)
    if credit:
        (Path(result["feedpak_dir"]) / "attribution.json").write_text(
            json.dumps(credit, indent=2), encoding="utf-8")

    shutil.rmtree(work_parent, ignore_errors=True)

    return {
        "ok": True,
        "feedpak": str(result["feedpak_dir"]),
        "title": pack_title,
        "roles": {role: len(notes) for role, notes in roles.items() if notes},
        "notes": note_count,
        "duration_sec": duration,
        "tempo_segments": len(segments),
        "audio_attached": True,
        "audio_sec": (round(audio_sec, 3) if audio_sec is not None else None),
        "last_note_onset_sec": round(last_onset, 3),
        "lead_in_trimmed_sec": round(applied_lead_in, 4),
        "attributed": bool(credit),
        "alignment_confidence": (round(alignment.confidence, 1) if alignment else None),
    }
