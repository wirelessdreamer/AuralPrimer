"""Build Psalm 1-7 songpacks with per-instrument MIDI ground truth.

Each Psalm folder in D:\Psalms has per-instrument MIDI files (Drums.mid,
Bass.mid, Guitar.mid etc.) alongside matching stem WAVs.  This script
creates songpacks with per-instrument note highways by:

  1. Copying the full-mix WAV as audio/mix.wav
  2. Merging all per-instrument MIDIs into features/events.json
     with proper role assignments (drums, bass, guitar, keys, other)
  3. Generating beats/tempo/sections from the MIDI tempo

Usage:
  python build_psalms_midi.py
"""

import json
import os
import re
import shutil
import struct
import sys
import hashlib
from pathlib import Path
from typing import Optional


# ── Config ──────────────────────────────────────────────────────────

PSALMS_DIR = Path("D:/Psalms")
OUT_DIR = Path("D:/AuralPrimer/AuralPrimerPortable/data/songs")

# Map each Psalm to (mix wav filename, title, stems subfolder)
PSALM_MAP = [
    {
        "num": 1,
        "mix": "Book of Psalms - Psalm 1 - The Road.wav",
        "title": "Psalm 1 - The Road",
        "artist": "Book of Psalms",
        "stems_dir": "Psalm 1/Psalm1_Stems",
    },
    {
        "num": 2,
        "mix": "Book of Psalms - Psalm 2 - King in Zion.wav",
        "title": "Psalm 2 - King in Zion",
        "artist": "Book of Psalms",
        "stems_dir": "Psalm 2/Book of Psalms - Psalm 2 - King in Zion Stems",
    },
    {
        "num": 3,
        "mix": "Book of Psalms - Psalm 3 - Shield Me On All Sides.wav",
        "title": "Psalm 3 - Shield Me On All Sides",
        "artist": "Book of Psalms",
        "stems_dir": "Psalm 3",
    },
    {
        "num": 4,
        "mix": "Book of Psalms - Psalm 4 - Trouble Again.wav",
        "title": "Psalm 4 - Trouble Again",
        "artist": "Book of Psalms",
        "stems_dir": "Psalm 4/Book of Psalms - Psalm 4 - Trouble Again Stems",
    },
    {
        "num": 5,
        "mix": "Book of Psalms - Psalm 5 - Every Morning.wav",
        "title": "Psalm 5 - Every Morning",
        "artist": "Book of Psalms",
        "stems_dir": "Psalm 5/Book of Psalms - Psalm 5 - Every Morning Stems",
    },
    {
        "num": 6,
        "mix": "Book of Psalms - Psalm 6 - Break In.wav",
        "title": "Psalm 6 - Break In",
        "artist": "Book of Psalms",
        "stems_dir": "Psalm 6/Book of Psalms - Psalm 6 - Break In Stems",
    },
    {
        "num": 7,
        "mix": "Psalm 7 - The Chase (Edit).wav",
        "title": "Psalm 7 - The Chase",
        "artist": "Book of Psalms",
        "stems_dir": "Psalm 7/Psalm 7 - The Chase (Edit) Stems",
    },
]

# Filename pattern → role mapping
ROLE_MAP = {
    "drums": "drums",
    "bass": "bass",
    "guitar": "guitar",
    "keyboard": "keys",
    "synth": "keys",
    "keys": "keys",
    "piano": "keys",
    "organ": "keys",
    "vocals": "other",
    "backing vocals": "other",
    "fx": "other",
    "percussion": "drums",
}


def infer_role(filename: str) -> str:
    """Infer instrument role from MIDI filename."""
    # Extract the instrument name from pattern like "Song Name (Instrument).mid"
    # Use the LAST parenthesized group (handles "Song (Edit) (Drums).mid")
    matches = re.findall(r'\(([^)]+)\)', filename)
    if matches:
        instrument = matches[-1].strip().lower()
        for key, role in ROLE_MAP.items():
            if key in instrument:
                return role
    return "other"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wav_duration_sec(wav_path: Path) -> float:
    """Get duration of a WAV file in seconds."""
    with open(wav_path, "rb") as f:
        f.read(4)  # RIFF
        f.read(4)  # file size
        f.read(4)  # WAVE
        # Find 'fmt ' chunk
        while True:
            chunk_id = f.read(4)
            if len(chunk_id) < 4:
                return 0.0
            chunk_size = struct.unpack("<I", f.read(4))[0]
            if chunk_id == b"fmt ":
                fmt_data = f.read(chunk_size)
                channels = struct.unpack("<H", fmt_data[2:4])[0]
                sample_rate = struct.unpack("<I", fmt_data[4:8])[0]
                bits_per_sample = struct.unpack("<H", fmt_data[14:16])[0]
                break
            else:
                f.seek(chunk_size, 1)
        # Find 'data' chunk
        while True:
            chunk_id = f.read(4)
            if len(chunk_id) < 4:
                return 0.0
            chunk_size = struct.unpack("<I", f.read(4))[0]
            if chunk_id == b"data":
                bytes_per_sample = bits_per_sample // 8
                num_samples = chunk_size // (channels * bytes_per_sample)
                return num_samples / sample_rate
            else:
                f.seek(chunk_size, 1)


# ── MIDI Parsing ────────────────────────────────────────────────────

def read_var_len(data: bytes, pos: int) -> tuple[int, int]:
    """Read a MIDI variable-length quantity. Returns (value, next_pos)."""
    value = 0
    for _ in range(4):
        if pos >= len(data):
            break
        b = data[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if (b & 0x80) == 0:
            break
    return value, pos


def parse_midi_notes(midi_bytes: bytes) -> tuple[list[dict], float]:
    """Parse MIDI file and return (notes, bpm).

    Returns a list of note dicts with t_on, t_off, pitch, velocity.
    """
    if len(midi_bytes) < 14 or midi_bytes[:4] != b"MThd":
        return [], 120.0

    header_len = struct.unpack(">I", midi_bytes[4:8])[0]
    tpq = struct.unpack(">H", midi_bytes[12:14])[0]
    if tpq == 0:
        return [], 120.0

    # Default 120 BPM = 500000 us/beat
    tempo_us = 500000
    bpm = 120.0

    notes = []
    pos = 8 + header_len

    while pos < len(midi_bytes):
        if pos + 8 > len(midi_bytes):
            break
        if midi_bytes[pos:pos + 4] != b"MTrk":
            break
        trk_len = struct.unpack(">I", midi_bytes[pos + 4:pos + 8])[0]
        pos += 8
        end = min(len(midi_bytes), pos + trk_len)

        t_ticks = 0
        running_status = 0
        open_notes: dict[tuple[int, int], tuple[int, int]] = {}  # (ch, pitch) -> (t_ticks, vel)

        while pos < end:
            delta, pos = read_var_len(midi_bytes, pos)
            t_ticks += delta
            if pos >= end:
                break

            status = midi_bytes[pos]
            if status < 0x80:
                if running_status < 0x80:
                    break
                status = running_status
            else:
                pos += 1
                running_status = status

            if status == 0xFF:
                if pos >= end:
                    break
                meta_type = midi_bytes[pos]
                pos += 1
                length, pos = read_var_len(midi_bytes, pos)
                if pos + length > end:
                    break
                if meta_type == 0x51 and length == 3:
                    tempo_us = (midi_bytes[pos] << 16) | (midi_bytes[pos + 1] << 8) | midi_bytes[pos + 2]
                    bpm = 60_000_000.0 / tempo_us
                pos += length
                if meta_type == 0x2F:
                    break
                continue

            if status == 0xF0 or status == 0xF7:
                length, pos = read_var_len(midi_bytes, pos)
                pos = min(end, pos + length)
                continue

            high = status & 0xF0
            ch = status & 0x0F
            data_len = 1 if high in (0xC0, 0xD0) else 2
            if pos + data_len > end:
                break
            d1 = midi_bytes[pos]
            d2 = midi_bytes[pos + 1] if data_len > 1 else 0
            pos += data_len

            def ticks_to_sec(t: int) -> float:
                return (t / tpq) * (tempo_us / 1_000_000.0)

            if high == 0x90 and d2 > 0:
                open_notes[(ch, d1)] = (t_ticks, d2)
            elif high == 0x80 or (high == 0x90 and d2 == 0):
                key = (ch, d1)
                if key in open_notes:
                    on_ticks, vel = open_notes.pop(key)
                    notes.append({
                        "t_on": round(ticks_to_sec(on_ticks), 6),
                        "t_off": round(ticks_to_sec(t_ticks), 6),
                        "pitch": d1,
                        "velocity": round(vel / 127.0, 4),
                    })

        pos = end

    return notes, bpm


def build_events_json(midi_files: list[tuple[Path, str]], duration_sec: float) -> tuple[dict, float]:
    """Build events.json from multiple per-instrument MIDI files.

    Args:
        midi_files: list of (path, role) tuples
        duration_sec: audio duration

    Returns:
        (events_dict, bpm)
    """
    all_notes = []
    tracks = []
    seen_roles = set()
    bpm = 120.0

    for midi_path, role in midi_files:
        midi_bytes = midi_path.read_bytes()
        notes, track_bpm = parse_midi_notes(midi_bytes)
        if track_bpm != 120.0:
            bpm = track_bpm  # Use last non-default BPM

        if role not in seen_roles:
            seen_roles.add(role)
            tracks.append({
                "track_id": role,
                "role": role,
                "name": midi_path.stem,
            })

        for n in notes:
            n["track_id"] = role
            n["confidence"] = 1.0
            n["source"] = "midi_import"
            # Wrap pitch
            n["pitch"] = {"type": "midi", "value": n["pitch"]}
            all_notes.append(n)

    # Sort by t_on
    all_notes.sort(key=lambda x: x["t_on"])

    return {
        "events_version": "1.0.0",
        "tracks": tracks,
        "notes": all_notes,
    }, bpm


def quantize(t: float, q: float = 1e-6) -> float:
    return round(t / q) * q


def generate_beats(duration_sec: float, bpm: float, beats_per_bar: int = 4) -> dict:
    period = 60.0 / bpm
    beats = []
    bar = 0
    beat_in_bar = 0
    t = 0.0
    while t <= duration_sec + 1e-9:
        strength = 1.0 if beat_in_bar == 0 else 0.5
        beats.append({"t": quantize(t), "bar": bar, "beat": beat_in_bar, "strength": strength})
        beat_in_bar += 1
        if beat_in_bar >= beats_per_bar:
            beat_in_bar = 0
            bar += 1
        t += period
    return {"beats_version": "1.0.0", "beats": beats}


def generate_tempo_map(bpm: float) -> dict:
    return {
        "tempo_version": "1.0.0",
        "segments": [{"t0": 0.0, "bpm": round(bpm, 3), "time_signature": "4/4"}],
    }


def generate_sections(duration_sec: float, bpm: float, bars_per_section: int = 8) -> dict:
    sec_per_bar = (60.0 / bpm) * 4.0
    sec_per_section = max(sec_per_bar * bars_per_section, 1.0)
    sections = []
    t0 = 0.0
    idx = 0
    while t0 < duration_sec - 1e-9:
        t1 = min(t0 + sec_per_section, duration_sec)
        sections.append({"t0": quantize(t0), "t1": quantize(t1), "label": f"section_{idx}"})
        t0 = t1
        idx += 1
    if not sections:
        sections.append({"t0": 0.0, "t1": quantize(duration_sec), "label": "section_0"})
    return {"sections_version": "1.0.0", "sections": sections}


def build_psalm_songpack(psalm: dict) -> Optional[Path]:
    """Build a single Psalm songpack with per-instrument MIDI highways."""
    num = psalm["num"]
    mix_wav = PSALMS_DIR / psalm["mix"]
    stems_dir = PSALMS_DIR / psalm["stems_dir"]

    if not mix_wav.is_file():
        print(f"  SKIP - mix WAV not found: {mix_wav}")
        return None

    if not stems_dir.is_dir():
        print(f"  SKIP - stems dir not found: {stems_dir}")
        return None

    # Find per-instrument MIDI files
    midi_files = sorted(stems_dir.glob("*.mid"))
    if not midi_files:
        print(f"  SKIP - no MIDI files in {stems_dir}")
        return None

    # Assign roles
    midi_with_roles = []
    for mf in midi_files:
        role = infer_role(mf.name)
        midi_with_roles.append((mf, role))
        print(f"    {mf.name} → {role}")

    # Duration
    dur = wav_duration_sec(mix_wav)
    print(f"    duration: {dur:.1f}s")

    # Build events
    events, bpm = build_events_json(midi_with_roles, dur)
    total_notes = len(events["notes"])
    track_counts = {}
    for n in events["notes"]:
        tid = n["track_id"]
        track_counts[tid] = track_counts.get(tid, 0) + 1
    print(f"    bpm: {bpm:.1f}, total notes: {total_notes}")
    for tid, cnt in sorted(track_counts.items()):
        print(f"      {tid}: {cnt} notes")

    # Output path
    folder_name = f"psalm_{num}_midi.songpack"
    out_dir = OUT_DIR / folder_name
    if out_dir.exists():
        print(f"    removing existing {out_dir}")
        shutil.rmtree(out_dir)

    os.makedirs(out_dir / "audio", exist_ok=True)
    os.makedirs(out_dir / "features", exist_ok=True)
    os.makedirs(out_dir / "charts", exist_ok=True)

    # Copy mix WAV
    shutil.copy2(mix_wav, out_dir / "audio" / "mix.wav")

    # Merge all MIDIs into one notes.mid by just copying the drums MIDI
    # (the full multi-track MIDI is better, but we don't have one — just copy
    # the drums MIDI for compatibility with the drum chart loader)
    drums_midi = None
    for mf, role in midi_with_roles:
        if role == "drums":
            drums_midi = mf
            break
    if drums_midi:
        shutil.copy2(drums_midi, out_dir / "features" / "notes.mid")

    # Write events.json
    with open(out_dir / "features" / "events.json", "w") as f:
        json.dump(events, f, indent=2)

    # Write beats, tempo, sections
    beats = generate_beats(dur, bpm)
    with open(out_dir / "features" / "beats.json", "w") as f:
        json.dump(beats, f, indent=2)

    tempo = generate_tempo_map(bpm)
    with open(out_dir / "features" / "tempo_map.json", "w") as f:
        json.dump(tempo, f, indent=2)

    sections = generate_sections(dur, bpm)
    with open(out_dir / "features" / "sections.json", "w") as f:
        json.dump(sections, f, indent=2)

    # Write a basic chart
    beat_items = beats.get("beats", [])
    targets = [{"t": b["t"], "lane": "beat"} for b in beat_items]
    chart = {"chart_version": "1.0.0", "mode": "beats_only", "difficulty": "easy", "targets": targets}
    with open(out_dir / "charts" / "easy.json", "w") as f:
        json.dump(chart, f, indent=2)

    # Manifest
    audio_sha = sha256_hex(mix_wav.read_bytes())
    midi_sha = sha256_hex(json.dumps(events, sort_keys=True).encode())
    song_id = sha256_hex(f"psalm_midi|{audio_sha}|{midi_sha}".encode())[:32]

    manifest = {
        "schema_version": "1.0.0",
        "song_id": song_id,
        "title": psalm["title"],
        "artist": psalm["artist"],
        "duration_sec": round(dur, 6),
        "source": {
            "kind": "stem_midi",
            "audio_sha256": audio_sha,
            "midi_source": "per_instrument_midi_import",
            "stems_dir": str(stems_dir),
        },
        "assets": {
            "audio": {"mix_path": "audio/mix.wav"},
        },
    }
    with open(out_dir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    return out_dir


def main():
    print(f"Building Psalm songpacks with MIDI ground truth")
    print(f"Source: {PSALMS_DIR}")
    print(f"Output: {OUT_DIR}")
    print()

    os.makedirs(OUT_DIR, exist_ok=True)

    built = 0
    for psalm in PSALM_MAP:
        print(f"[Psalm {psalm['num']}] {psalm['title']}")
        result = build_psalm_songpack(psalm)
        if result:
            print(f"  → {result}")
            built += 1
        print()

    print(f"DONE — built {built}/{len(PSALM_MAP)} songpacks")


if __name__ == "__main__":
    main()
