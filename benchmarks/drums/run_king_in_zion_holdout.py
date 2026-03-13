"""Run King in Zion holdout with sync-corrected MIDI reference (-540ms offset)."""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(r"D:\AuralPrimer")
SRC = ROOT / "python" / "ingest" / "src"
sys.path.insert(0, str(SRC))

from aural_ingest.drum_benchmark import (
    BenchmarkEvent,
    _parse_midi_note_ons,
    _tick_to_seconds,
    _compress_tempo_changes,
    normalize_drum_note,
    benchmark_algorithms,
    format_benchmark_summary,
)
from aural_ingest.transcription import build_default_drum_algorithm_registry

MIDI_PATH = Path(r"D:\Psalms\Psalm 1\Psalm1_Stems\Psalm 1 (Drums).mid")
WAV_PATH  = Path(r"D:\Psalms\Psalm 1\Psalm1_Stems\Psalm 1 (Drums).wav")
OFFSET_SEC = -0.540  # MIDI plays 540ms ahead of audio

ALGORITHMS = [
    "spectral_flux_multiband",
    "beat_conditioned_multiband_decoder",
    "adaptive_beat_grid",
]

def load_reference_with_offset(midi_path: Path, offset_sec: float) -> list[BenchmarkEvent]:
    note_ons, tempo_changes_raw, tpq = _parse_midi_note_ons(midi_path)
    tempo_changes = _compress_tempo_changes(tempo_changes_raw)

    events = []
    for n in note_ons:
        t = _tick_to_seconds(n.tick, tempo_changes, tpq) + offset_sec
        if t < 0:
            continue
        drum_class = normalize_drum_note(n.note)
        if drum_class is None:
            continue
        events.append(BenchmarkEvent(time=round(t, 6), drum_class=drum_class))

    return sorted(events, key=lambda e: e.time)


def main():
    print(f"MIDI: {MIDI_PATH}")
    print(f"WAV:  {WAV_PATH}")
    print(f"Offset: {OFFSET_SEC:+.3f}s")
    print()

    ref_events = load_reference_with_offset(MIDI_PATH, OFFSET_SEC)
    print(f"Reference events (after offset & filtering): {len(ref_events)}")
    print(f"First 5: {[(round(e.time,3), e.drum_class) for e in ref_events[:5]]}")
    print()

    registry = build_default_drum_algorithm_registry()

    results = benchmark_algorithms(
        WAV_PATH,
        ref_events,
        ALGORITHMS,
        registry,
        tolerance_sec=0.06,
    )

    payload = {
        "reference_path": str(MIDI_PATH),
        "reference_count": len(ref_events),
        "tolerance_ms": 60.0,
        "midi_offset_sec": OFFSET_SEC,
        "results": results,
    }

    print(format_benchmark_summary(payload))
    print()

    out = ROOT / "benchmarks" / "drums" / "king_in_zion_sync_corrected_holdout.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved to: {out}")


if __name__ == "__main__":
    main()
