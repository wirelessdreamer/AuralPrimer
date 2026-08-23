"""Build a ``.feedpak`` directly from a MusicXML score.

MusicXML carries exactly what a pack needs and what audio transcription has to
guess at: the notes, the tempo, the time signature, and a metronomic bar grid.
So this path skips transcription entirely — it parses the score into per-role
notes and an exact beat/measure timeline, assembles a minimal ``.auralsong``
working directory, then hands off to the shared :func:`write_feedpak`.

A feedpak must carry at least one audio stem to be schema-valid and playable,
so import needs a render: the mix co-located with the score (Mirelo drops the
``.wav`` beside the ``.musicxml``) or one passed via ``audio_path``.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from aural_ingest.arrangement_prep import MIN_NOTE_SEC, ROLE_ORDER, ROLE_TRACK_NAME
from aural_ingest.feedpak_writer import write_feedpak
from aural_ingest.musicxml_import import (
    MelodicNote,
    parse_musicxml,
    parse_musicxml_timeline,
)

_AUDIO_EXTS = (".wav", ".flac", ".mp3", ".ogg", ".m4a")


def _build_notes_mid(roles: dict[str, list[MelodicNote]], out_path: Path) -> int:
    """Write notes.mid, one named instrument per role in ROLE_ORDER. Returns
    the note count."""
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    total = 0
    for role in ROLE_ORDER:
        notes = roles.get(role)
        if not notes:
            continue
        inst = pretty_midi.Instrument(program=0, is_drum=False,
                                      name=ROLE_TRACK_NAME.get(role, role.title()))
        for n in notes:
            if not (0 <= n.pitch <= 127):
                continue
            end = max(n.t_off, n.t_on + MIN_NOTE_SEC)
            inst.notes.append(pretty_midi.Note(
                velocity=int(n.velocity) or 100, pitch=int(n.pitch),
                start=float(n.t_on), end=float(end)))
            total += 1
        if inst.notes:
            pm.instruments.append(inst)
    pm.write(str(out_path))
    return total


def _build_beats(timeline, out_path: Path) -> None:
    """Emit beats.json with one downbeat (``beat==0``) per notated measure plus
    evenly-spaced sub-beats, so the feedpak writer's measure grid is exact."""
    num, den = timeline.time_signature
    onsets = timeline.measure_onsets_sec or [0.0]
    beats: list[dict[str, Any]] = []
    for i, start in enumerate(onsets):
        end = onsets[i + 1] if i + 1 < len(onsets) else start + (
            (onsets[i] - onsets[i - 1]) if i > 0 else 60.0 / timeline.tempo_bpm * num)
        step = (end - start) / num if num > 0 else (end - start)
        for b in range(num):
            beats.append({"t": round(start + b * step, 6), "beat": b})
    out_path.write_text(json.dumps({"beats": beats}), encoding="utf-8")


def _find_sibling_audio(xml_path: Path) -> Path | None:
    """A render beside the score with the same stem (Mirelo drops the .wav next
    to the .musicxml), else any lone audio file in the folder."""
    for ext in _AUDIO_EXTS:
        cand = xml_path.with_suffix(ext)
        if cand.exists():
            return cand
    audio = [p for p in xml_path.parent.iterdir()
             if p.is_file() and p.suffix.lower() in _AUDIO_EXTS]
    return audio[0] if len(audio) == 1 else None


def build_feedpak_from_musicxml(
    xml_path: str | Path,
    out_dir: str | Path,
    *,
    audio_path: str | Path | None = None,
    title: str | None = None,
    artist: str | None = None,
) -> dict[str, Any]:
    """Parse a MusicXML score and write a ``.feedpak`` under ``out_dir``.

    ``audio_path`` defaults to a render co-located with the score. ``title``
    defaults to the score's file stem — never its ``movement-title``, which
    Mirelo's exporter fills with the detected key, not a song name.
    """
    xml_path = Path(xml_path)
    out_dir = Path(out_dir)
    roles = parse_musicxml(xml_path)
    if not any(roles.values()):
        raise ValueError(f"{xml_path.name}: no notes parsed from the score")
    timeline = parse_musicxml_timeline(xml_path)

    resolved_audio = Path(audio_path) if audio_path else _find_sibling_audio(xml_path)
    if not resolved_audio or not resolved_audio.exists():
        raise ValueError(
            f"{xml_path.name}: no audio found. A feedpak needs an audio stem — "
            "put a render (.wav/.mp3/...) beside the score or pass audio_path."
        )
    pack_title = title or xml_path.stem
    pack_artist = artist or "Unknown"

    # --- assemble a minimal .auralsong working dir --------------------------
    work_parent = out_dir / "_musicxml_work"
    song = work_parent / f"{xml_path.stem}.auralsong"
    if song.exists():
        shutil.rmtree(song)
    (song / "features").mkdir(parents=True, exist_ok=True)
    (song / "audio").mkdir(parents=True, exist_ok=True)

    note_count = _build_notes_mid(roles, song / "features" / "notes.mid")

    num, den = timeline.time_signature
    (song / "features" / "tempo_map.json").write_text(json.dumps({"segments": [
        {"bpm": timeline.tempo_bpm, "t0": 0.0, "t1": None,
         "time_signature": f"{num}/{den}"}
    ]}), encoding="utf-8")
    _build_beats(timeline, song / "features" / "beats.json")

    assets: dict[str, Any] = {
        "midi": {"notes_path": "features/notes.mid"},
        "features": {
            "tempo_map_path": "features/tempo_map.json",
            "beats_path": "features/beats.json",
        },
        "audio": {},
    }
    duration = timeline.duration_sec
    ext = resolved_audio.suffix or ".wav"
    shutil.copy2(resolved_audio, song / "audio" / f"mix{ext}")
    assets["audio"]["mix_path"] = f"audio/mix{ext}"

    manifest = {
        "title": pack_title,
        "artist": pack_artist,
        "duration_sec": duration,
        "source": "musicxml",
        "assets": assets,
    }
    (song / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # --- hand off to the shared feedpak writer ------------------------------
    result = write_feedpak(song, out_dir)
    shutil.rmtree(work_parent, ignore_errors=True)

    return {
        "ok": True,
        "feedpak": str(result["feedpak_dir"]),
        "title": pack_title,
        "roles": {r: len(v) for r, v in roles.items() if v},
        "notes": note_count,
        "measures": len(timeline.measure_onsets_sec),
        "tempo_bpm": timeline.tempo_bpm,
        "time_signature": f"{num}/{den}",
        "audio_attached": bool(resolved_audio and resolved_audio.exists()),
        "movement_title_ignored": timeline.movement_title,
    }
