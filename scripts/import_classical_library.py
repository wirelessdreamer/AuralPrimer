"""Install the licensed classical MIDI collection as playable feedpaks.

Run with python/ingest/.venv/Scripts/python.exe. Original MIDI is kept unchanged
and rendered with its own programs, controllers and timing. A separate full-score
keyboard chart preserves every pitched note and the native tempo/meter maps.
No transcription, real-time DAW session or application rebuild is involved.
"""
from __future__ import annotations

import argparse
import bisect
import concurrent.futures
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

import jsonschema
import mido
import numpy as np
import soundfile as sf
import yaml

ROOT = Path(__file__).resolve().parents[1]
COLLECTION = ROOT / "assets/classical"
BUILDER_VERSION = 1
SOUNDFONT_SHA256 = "9575028c7a1f589f5770fccc8cff2734566af40cd26ed836944e9a5152688cfe"
CHART_CHANNELS = tuple(ch for ch in range(15) if ch != 9)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class Note:
    on: int
    off: int
    pitch: int
    velocity: int


class Score:
    def __init__(self, path: Path):
        self.midi = mido.MidiFile(path)
        if self.midi.type not in (0, 1) or self.midi.ticks_per_beat <= 0:
            raise ValueError(f"Unsupported MIDI timing: {path}")
        self.ppq = self.midi.ticks_per_beat
        self.notes: list[Note] = []
        self.meta: list[tuple[int, Any]] = []
        self.percussion_notes = 0
        for track in self.midi.tracks:
            tick = 0
            pending: dict[tuple[int, int], list[tuple[int, int]]] = {}
            for msg in track:
                tick += msg.time
                if msg.is_meta and msg.type in {"set_tempo", "time_signature", "key_signature"}:
                    self.meta.append((tick, msg.copy(time=0)))
                if msg.type == "note_on" and msg.velocity > 0:
                    if msg.channel == 9:
                        self.percussion_notes += 1
                        continue
                    pending.setdefault((msg.channel, msg.note), []).append((tick, msg.velocity))
                elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                    queue = pending.get((msg.channel, msg.note), [])
                    if queue:
                        start, velocity = queue.pop(0)
                        if tick > start:
                            self.notes.append(Note(start, tick, msg.note, velocity))
            if any(pending.values()):
                raise ValueError(f"Unterminated pitched notes in {path}")
        if not self.notes:
            raise ValueError(f"No pitched notes in {path}")
        self.notes.sort(key=lambda n: (n.on, n.pitch, n.off, n.velocity))
        self.end_tick = max(n.off for n in self.notes)
        self.meta.sort(key=lambda item: item[0])
        tempos = {0: 500000}
        meters = {0: (4, 4)}
        for tick, msg in self.meta:
            if msg.type == "set_tempo":
                tempos[tick] = msg.tempo
            elif msg.type == "time_signature":
                meters[tick] = (msg.numerator, msg.denominator)
        self.tempos = sorted(tempos.items())
        self.meters = sorted(meters.items())
        self.tempo_ticks = [t for t, _ in self.tempos]
        self.tempo_seconds = [0.0]
        for i in range(1, len(self.tempos)):
            prev_tick, prev_tempo = self.tempos[i - 1]
            self.tempo_seconds.append(self.tempo_seconds[-1] + mido.tick2second(
                self.tempos[i][0] - prev_tick, self.ppq, prev_tempo))

    def seconds(self, tick: int | Fraction) -> float:
        i = bisect.bisect_right(self.tempo_ticks, tick) - 1
        return self.tempo_seconds[i] + mido.tick2second(
            float(tick - self.tempos[i][0]), self.ppq, self.tempos[i][1])

    def timing(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        beats: list[dict[str, Any]] = []
        measures: list[dict[str, Any]] = []
        measure = 1
        for i, (start, (num, den)) in enumerate(self.meters):
            stop = self.meters[i + 1][0] if i + 1 < len(self.meters) else self.end_tick + 1
            tick, beat = Fraction(start), 0
            step = Fraction(self.ppq * 4, den)
            while tick < stop and tick <= self.end_tick:
                time = self.seconds(tick)
                if beat == 0:
                    tempo = self.tempos[bisect.bisect_right(self.tempo_ticks, tick) - 1][1]
                    measures.append({"idx": measure, "t": time, "ts": [num, den],
                                     "tempo": 60_000_000 / tempo})
                beats.append({"time": time, "measure": measure})
                beat += 1
                if beat == num:
                    beat = 0
                    measure += 1
                tick += step
            if beat:
                measure += 1
        timeline = {
            "version": 1,
            "tempos": [{"time": self.seconds(t), "bpm": 60_000_000 / tempo}
                       for t, tempo in self.tempos],
            "time_signatures": [{"time": self.seconds(t), "ts": list(meter)}
                                for t, meter in self.meters],
            "beats": beats,
        }
        return timeline, measures

    def write_chart(self, path: Path) -> None:
        # One Keys track is discoverable by the game's role-based chart loader.
        # Reassign overlapping equal pitches to spare MIDI channels so neither
        # the game parser nor a MIDI reader truncates an ensemble's unison notes.
        events: list[tuple[int, int, Any]] = []
        busy: dict[int, dict[int, int]] = {}
        for note in self.notes:
            channels = busy.setdefault(note.pitch, {})
            available = next((ch for ch in CHART_CHANNELS
                              if channels.get(ch, -1) <= note.on), None)
            if available is None:
                raise ValueError(f"More than 14 overlapping voices on pitch {note.pitch}")
            channels[available] = note.off
            events.extend([
                (note.on, 1, mido.Message("note_on", channel=available, note=note.pitch,
                                         velocity=note.velocity)),
                (note.off, 0, mido.Message("note_off", channel=available, note=note.pitch,
                                          velocity=0)),
            ])
        mid = mido.MidiFile(type=1, ticks_per_beat=self.ppq)
        conductor = mido.MidiTrack([mido.MetaMessage("track_name", name="Conductor")])
        previous = 0
        for tick, message in self.meta:
            conductor.append(message.copy(time=tick - previous))
            previous = tick
        conductor.append(mido.MetaMessage("end_of_track"))
        mid.tracks.append(conductor)
        track = mido.MidiTrack([mido.MetaMessage("track_name", name="Keys")])
        previous = 0
        for tick, _, message in sorted(events, key=lambda item: (item[0], item[1])):
            track.append(message.copy(time=tick - previous))
            previous = tick
        track.append(mido.MetaMessage("end_of_track"))
        mid.tracks.append(track)
        mid.save(path)

    def notation(self, measures: list[dict[str, Any]]) -> dict[str, Any]:
        # Encode note values from source ticks, so rubato does not change the
        # written duration. MIDI/playback note-off times remain exact separately.
        starts = [m["t"] for m in measures]
        output = [{**m, "staves": {}} for m in measures]
        for note in self.notes:
            time = self.seconds(note.on)
            i = max(0, bisect.bisect_right(starts, time + 1e-9) - 1)
            staff = "treble" if note.pitch >= 60 else "bass"
            quarters = (note.off - note.on) / self.ppq
            value = min((1, 2, 4, 8, 16, 32), key=lambda v: abs(quarters - 4 / v))
            st = output[i]["staves"].setdefault(staff, {"voices": [{"v": 1, "beats": []}]})
            st["voices"][0]["beats"].append(
                {"t": time, "dur": value, "notes": [{"midi": note.pitch}]})
        return {"version": 1, "instrument": "keys", "staves": [
            {"id": "treble", "clef": "G2"}, {"id": "bass", "clef": "F4"}],
            "measures": output}


def validate_json(value: Any, schema_name: str) -> None:
    schema = json.loads((ROOT / "packages/feedpak/schemas" / schema_name).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(value)


def check_source(entry: dict[str, Any]) -> Path:
    for field, hash_field in (("file", "sha256"), ("score_file", "score_sha256"),
                              ("evidence_file", "evidence_sha256")):
        path = COLLECTION / entry[field]
        if digest(path) != entry[hash_field]:
            raise ValueError(f"Source checksum mismatch: {path}")
    return COLLECTION / entry["file"]


def audio_stats(path: Path) -> dict[str, float]:
    info = sf.info(path)
    peak, squares, count = 0.0, 0.0, 0
    for block in sf.blocks(path, blocksize=65536, dtype="float32", always_2d=True):
        peak = max(peak, float(np.max(np.abs(block))))
        squares += float(np.sum(block.astype(np.float64) ** 2))
        count += block.size
    return {"duration_sec": info.duration, "sample_rate": info.samplerate,
            "channels": info.channels, "peak": peak, "rms": math.sqrt(squares / max(count, 1))}


def verify_pack(entry: dict[str, Any], folder: Path) -> dict[str, Any]:
    source = check_source(entry)
    manifest = yaml.safe_load((folder / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["title"] == entry["title"]
    assert manifest["artist"] == entry["composer"]
    assert manifest["genre"] == "Classical"
    validate_json(manifest, "manifest.schema.json")
    attribution = json.loads((folder / "attribution.json").read_text(encoding="utf-8"))
    assert attribution["sha256"] == entry["sha256"]
    assert attribution["license_url"] == entry["license_url"]
    assert digest(folder / "source/original.mid") == entry["sha256"]
    original, chart = Score(source), Score(folder / manifest["aural_notes_mid"])
    assert original.notes == chart.notes, f"Pitched note data changed in {entry['id']}"
    assert original.ppq == chart.ppq
    assert original.tempos == chart.tempos
    assert original.meters == chart.meters
    timeline = json.loads((folder / manifest["song_timeline"]).read_text(encoding="utf-8"))
    validate_json(timeline, "song-timeline.schema.json")
    expected_timeline, _ = original.timing()
    assert timeline == expected_timeline
    notation = json.loads((folder / manifest["arrangements"][0]["notation"]).read_text(encoding="utf-8"))
    validate_json(notation, "notation.schema.json")
    notation_notes = sum(len(beat["notes"]) for measure in notation["measures"]
                         for staff in measure.get("staves", {}).values()
                         for voice in staff["voices"] for beat in voice["beats"])
    assert notation_notes == len(original.notes), "Incomplete notation"
    audio = audio_stats(folder / manifest["stems"][0]["file"])
    assert audio["duration_sec"] >= original.seconds(original.end_tick)
    assert audio["peak"] > 0.001 and audio["rms"] > 0.0001, "Silent audio"
    assert audio["peak"] < 0.9999, "Audio clipped; lower renderer gain"
    assert abs(manifest["duration"] - audio["duration_sec"]) < 0.001
    return {"id": entry["id"], "work_id": entry["work_id"], "title": entry["title"],
            "composer": entry["composer"], "notes": len(original.notes),
            "percussion_notes_in_source": original.percussion_notes,
            "score_duration_sec": original.seconds(original.end_tick),
            "tempo_segments": len(original.tempos), "meter_segments": len(original.meters),
            "meters": [list(m) for _, m in original.meters], "audio": audio,
            "original_sha256": entry["sha256"], "chart_sha256": digest(folder / "aural/notes.mid"),
            "audio_sha256": digest(folder / "audio/stems/mix.flac"),
            "license": entry["license"], "ok": True}


def build(entry: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    original_path = check_source(entry)
    target = args.out / (entry["id"] + ".feedpak")
    if target.exists():
        return verify_pack(entry, target)
    score = Score(original_path)
    stage = args.cache / "staging" / (entry["id"] + ".feedpak")
    if stage.exists():
        raise FileExistsError(f"Prior staging directory exists; inspect before removing: {stage}")
    for directory in ("aural", "arrangements", "audio/stems", "source", "licenses"):
        (stage / directory).mkdir(parents=True, exist_ok=True)
    rendered = args.cache / (entry["id"] + ".flac")
    if not rendered.exists():
        result = subprocess.run([
            str(args.fluidsynth), "-ni", "-R", "0", "-C", "0", "-g", "0.35",
            "-T", "flac", "-O", "s16", "-r", "44100", "-F", str(rendered),
            str(args.soundfont), str(original_path),
        ], capture_output=True, text=True, timeout=300)
        if result.returncode:
            raise RuntimeError(f"FluidSynth failed for {entry['id']}: {result.stderr}")
    shutil.copy2(rendered, stage / "audio/stems/mix.flac")
    shutil.copy2(original_path, stage / "source/original.mid")
    shutil.copy2(COLLECTION / entry["score_file"], stage / "source" / Path(entry["score_file"]).name)
    shutil.copy2(COLLECTION / entry["evidence_file"], stage / "source" / "mutopia.rdf")
    for path in (COLLECTION / "licenses").iterdir():
        shutil.copy2(path, stage / "licenses" / path.name)
    score.write_chart(stage / "aural/notes.mid")
    timeline, measures = score.timing()
    write_json(stage / "song_timeline.json", timeline)
    write_json(stage / "arrangements/notation_keys.json", score.notation(measures))
    audio_info = sf.info(rendered)
    manifest = {
        "feedpak_version": "1.11.0", "title": entry["title"], "artist": entry["composer"],
        "genre": "Classical", "duration": audio_info.duration,
        "arrangements": [{"id": "keys", "name": "Full score (keyboard practice)",
                          "type": "piano", "notation": "arrangements/notation_keys.json"}],
        "stems": [{"id": "mix", "file": "audio/stems/mix.flac", "default": True}],
        "song_timeline": "song_timeline.json", "aural_notes_mid": "aural/notes.mid",
        "license": entry["license"], "license_url": entry["license_url"],
        "attribution": "attribution.json", "source_midi": "source/original.mid",
        "work_id": entry["work_id"], "collection": "Classical standards expansion",
    }
    (stage / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False,
                                                       allow_unicode=True), encoding="utf-8")
    credit = {**entry, "builder_version": BUILDER_VERSION,
              "modifications": "Original MIDI retained unchanged in source/original.mid. Gameplay combines all pitched parts in one Keys track, reallocating overlapping unison pitches to spare channels; pitches, velocities, on/off ticks, PPQ, tempo and meter are preserved. GM percussion is retained in source MIDI and audio, excluded from the keyboard chart. Notation is generated from source ticks; ornaments, articulations and dotted/tuplet spelling are approximated by the game's notation format.",
              "derived_assets_license": entry["license"],
              "audio_render": {"engine": "FluidSynth 2.6.1", "engine_url": "https://www.fluidsynth.org/",
                               "soundfont": "GeneralUser GS 2.0.3", "soundfont_creator": "S. Christian Collins",
                               "soundfont_url": "https://www.schristiancollins.com/generaluser.php",
                               "soundfont_sha256": SOUNDFONT_SHA256,
                               "soundfont_license": "licenses/GENERALUSER-GS-LICENSE.txt",
                               "settings": "44100 Hz stereo PCM16 FLAC; gain 0.35; reverb/chorus disabled; original MIDI programs/controllers/tempo"}}
    write_json(stage / "attribution.json", credit)
    (stage / "LICENSE.txt").write_text(
        f"{entry['title']}\nComposer: {entry['composer']}\nMutopia contributor: {entry['attribution']}\n"
        f"Source: {entry['mutopia_url']}\nEdition/MIDI and derived chart/audio license: {entry['license']}\n"
        f"{entry['license_url']}\n\nOriginal MIDI is unchanged; the full-score gameplay chart is adapted.\n"
        "See attribution.json for exact changes, source hashes, arranger, and renderer credits.\n"
        "The music assets retain their own licenses independently of AuralPrimer's GPL software.\n"
        "Audio uses GeneralUser GS by S. Christian Collins; its full license is in licenses/.\n",
        encoding="utf-8")
    audit = verify_pack(entry, stage)
    args.out.mkdir(parents=True, exist_ok=True)
    os.replace(stage, target)
    return audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "AuralPrimerPortable/data/songs")
    parser.add_argument("--cache", type=Path, default=ROOT / ".work/classical-renders")
    parser.add_argument("--fluidsynth", type=Path)
    parser.add_argument("--soundfont", type=Path)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--audit-out", type=Path)
    args = parser.parse_args()
    catalog = json.loads((COLLECTION / "catalog.json").read_text(encoding="utf-8"))
    entries = catalog["entries"][:args.limit]
    assert len({e["id"] for e in catalog["entries"]}) == catalog["track_count"]
    assert len({e["sha256"] for e in catalog["entries"]}) == catalog["track_count"], "Duplicate MIDI"
    assert len({e["title"] for e in catalog["entries"]}) == catalog["track_count"]
    assert len({e["work_id"] for e in catalog["entries"]}) == 45
    assert catalog["additional_work_count"] == 40
    if not args.verify_only:
        local_tools = ROOT / ".external/classical-render-tools"
        args.fluidsynth = args.fluidsynth or next(local_tools.glob("fluidsynth-*/**/fluidsynth.exe"), None)
        args.soundfont = args.soundfont or next(local_tools.glob("generaluser-gs/*/GeneralUser-GS.sf2"), None)
        if not args.fluidsynth or not args.soundfont:
            parser.error("Pass --fluidsynth and --soundfont; see assets/classical/README.md")
        if digest(args.soundfont) != SOUNDFONT_SHA256:
            parser.error("Soundfont checksum differs from the licensed, pinned GeneralUser GS source")
        args.cache.mkdir(parents=True, exist_ok=True)
    def run(entry: dict[str, Any]) -> dict[str, Any]:
        audit = verify_pack(entry, args.out / (entry["id"] + ".feedpak")) if args.verify_only else build(entry, args)
        print(f"OK {entry['id']}: {audit['notes']} notes, {audit['audio']['duration_sec']:.1f}s", flush=True)
        return audit
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(run, entries))
    report = {"version": BUILDER_VERSION, "tracks": len(results),
              "works": len({e["work_id"] for e in results}),
              "notes": sum(e["notes"] for e in results),
              "audio_duration_sec": sum(e["audio"]["duration_sec"] for e in results),
              "all_passed": True, "entries": results}
    if args.audit_out:
        write_json(args.audit_out, report)
    print(json.dumps({k: v for k, v in report.items() if k != "entries"}), flush=True)


if __name__ == "__main__":
    main()
