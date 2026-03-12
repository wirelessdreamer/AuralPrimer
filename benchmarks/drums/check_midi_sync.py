"""Check MIDI-to-audio sync for all available Psalm songs with drum stems."""
from __future__ import annotations
import struct, sys, wave
from pathlib import Path

ROOT = Path(r"D:\AuralPrimer")
SRC = ROOT / "python" / "ingest" / "src"
sys.path.insert(0, str(SRC))

from aural_ingest.drum_benchmark import _parse_midi_note_ons, _tick_to_seconds, _compress_tempo_changes

SONGS = [
    {
        "name": "Psalm 1",
        "midi": Path(r"D:\Psalms\Psalm 1\Psalm1_Stems\Psalm 1 (Drums).mid"),
        "wav":  Path(r"D:\Psalms\Psalm 1\Psalm1_Stems\Psalm 1 (Drums).wav"),
    },
    {
        "name": "Psalm 2 (King in Zion)",
        "midi": Path(r"D:\Psalms\Psalm 2\Book of Psalms - Psalm 2 - King in Zion Stems\Book of Psalms - Psalm 2 - King in Zion (Drums).mid"),
        "wav":  Path(r"D:\Psalms\Psalm 2\Book of Psalms - Psalm 2 - King in Zion Stems\Book of Psalms - Psalm 2 - King in Zion (Drums).wav"),
    },
    {
        "name": "Psalm 4 (Trouble Again)",
        "midi": Path(r"D:\Psalms\Psalm 4\Book of Psalms - Psalm 4 - Trouble Again Stems\Book of Psalms - Psalm 4 - Trouble Again (Drums).mid"),
        "wav":  Path(r"D:\Psalms\Psalm 4\Book of Psalms - Psalm 4 - Trouble Again Stems\Book of Psalms - Psalm 4 - Trouble Again (Drums).wav"),
    },
    {
        "name": "Psalm 5 (Every Morning)",
        "midi": Path(r"D:\Psalms\Psalm 5\Book of Psalms - Psalm 5 - Every Morning Stems\Book of Psalms - Psalm 5 - Every Morning (Drums).mid"),
        "wav":  Path(r"D:\Psalms\Psalm 5\Book of Psalms - Psalm 5 - Every Morning Stems\Book of Psalms - Psalm 5 - Every Morning (Drums).wav"),
    },
    {
        "name": "Psalm 6 (Break In)",
        "midi": Path(r"D:\Psalms\Psalm 6\Book of Psalms - Psalm 6 - Break In Stems\Book of Psalms - Psalm 6 - Break In (Drums).mid"),
        "wav":  Path(r"D:\Psalms\Psalm 6\Book of Psalms - Psalm 6 - Break In Stems\Book of Psalms - Psalm 6 - Break In (Drums).wav"),
    },
    {
        "name": "Psalm 7 (The Chase)",
        "midi": Path(r"D:\Psalms\Psalm 7\Psalm 7 - The Chase (Edit) Stems\Psalm 7 - The Chase (Edit) (Drums).mid"),
        "wav":  Path(r"D:\Psalms\Psalm 7\Psalm 7 - The Chase (Edit) Stems\Psalm 7 - The Chase (Edit) (Drums).wav"),
    },
]


def read_wav_mono(path: Path) -> tuple[list[float], int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        nch = w.getnchannels()
        sw = w.getsampwidth()
        n = w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        vals = struct.unpack(f"<{n * nch}h", raw)
        scale = 1.0 / 32768.0
    elif sw == 3:
        vals = []
        for i in range(0, len(raw), 3):
            vals.append(int.from_bytes(raw[i:i+3], 'little', signed=True))
        scale = 1.0 / 8388608.0
    else:
        vals = struct.unpack(f"<{n * nch}h", raw)
        scale = 1.0 / 32768.0
    if nch == 1:
        samples = [v * scale for v in vals]
    else:
        samples = [sum(vals[i:i+nch]) * scale / nch for i in range(0, len(vals), nch)]
    return samples, sr


def detect_audio_onsets(samples, sr, hop=512):
    frame_size = 1024
    energies = []
    pos = 0
    while pos + frame_size <= len(samples):
        seg = samples[pos:pos+frame_size]
        energies.append(sum(x*x for x in seg) / frame_size)
        pos += hop
    novelty = [0.0]
    for i in range(1, len(energies)):
        d = energies[i] - energies[i-1]
        novelty.append(d if d > 0 else 0.0)
    if not novelty:
        return []
    med = sorted(novelty)[len(novelty)//2]
    threshold = med * 4.0 + 0.001
    min_gap = int(0.05 * sr / hop)
    onsets = []
    last = -min_gap - 1
    for i, v in enumerate(novelty):
        if v > threshold and (i - last) >= min_gap:
            onsets.append(i * hop / float(sr))
            last = i
    return onsets


def find_best_offset(midi_times, audio_onsets, tolerance=0.05):
    best_offset = 0.0
    best_matches = 0
    for offset_ms in range(-5000, 5001, 10):
        offset = offset_ms / 1000.0
        matches = sum(1 for mt in midi_times if any(abs(mt + offset - at) <= tolerance for at in audio_onsets))
        if matches > best_matches:
            best_matches = matches
            best_offset = offset
    return best_offset, best_matches


def main():
    for song in SONGS:
        print(f"\n{'='*60}")
        print(f"  {song['name']}")
        print(f"{'='*60}")
        midi_path = song["midi"]
        wav_path = song["wav"]

        if not midi_path.exists():
            print(f"  MIDI not found: {midi_path}")
            continue
        if not wav_path.exists():
            print(f"  WAV not found: {wav_path}")
            continue

        note_ons, tempo_changes_raw, tpq = _parse_midi_note_ons(midi_path)
        tempo_changes = _compress_tempo_changes(tempo_changes_raw)
        midi_times = sorted(set(_tick_to_seconds(n.tick, tempo_changes, tpq) for n in note_ons))

        samples, sr = read_wav_mono(wav_path)
        duration = len(samples) / float(sr)
        audio_onsets = detect_audio_onsets(samples, sr)

        best_offset, best_matches = find_best_offset(midi_times, audio_onsets)

        zero_matches = sum(1 for mt in midi_times if any(abs(mt - at) <= 0.05 for at in audio_onsets))

        if tempo_changes:
            bpm = 60_000_000.0 / tempo_changes[0][1]
        else:
            bpm = 120.0

        print(f"  WAV duration: {duration:.1f}s  SR: {sr}")
        print(f"  MIDI events: {len(note_ons)}  unique onsets: {len(midi_times)}")
        print(f"  Audio onsets: {len(audio_onsets)}")
        print(f"  BPM: {bpm:.1f}  TPQ: {tpq}")
        print(f"  Best offset: {best_offset:+.3f}s  matches: {best_matches}/{len(midi_times)} ({100*best_matches/max(1,len(midi_times)):.1f}%)")
        print(f"  Zero offset:              matches: {zero_matches}/{len(midi_times)} ({100*zero_matches/max(1,len(midi_times)):.1f}%)")

        # Check measure-aligned offsets
        measure_sec = 4 * 60.0 / bpm
        for label, off in [("-1 beat", -60.0/bpm), ("+1 beat", 60.0/bpm),
                           ("-1 measure", -measure_sec), ("+1 measure", measure_sec)]:
            m = sum(1 for mt in midi_times if any(abs(mt + off - at) <= 0.05 for at in audio_onsets))
            print(f"  {label:>12} ({off:+.3f}s): {m}/{len(midi_times)} ({100*m/max(1,len(midi_times)):.1f}%)")

if __name__ == "__main__":
    main()
