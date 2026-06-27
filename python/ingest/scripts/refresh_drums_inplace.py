"""In-place drum refresh: re-transcribe each pack's drums stem with the
current default engine chain (mr_mt3 neural ADT first, DSP fallback) and
rewrite drum_tab.json + the drum track in aural/notes.mid. Does NOT touch
melodic arrangements, stems, or the manifest -- surgical drums-only update.

Device is auto-detected (GPU if available, else CPU) by the transcription
layer; nothing here hardcodes a device.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pretty_midi

from aural_ingest.transcription import (
    build_default_drum_algorithm_registry,
    transcribe_drums_with_profile,
    validate_drum_events_against_stem_silence,
)
from aural_ingest.feedpak_writer import _build_drum_tab

PACKS = [
    "AuralPrimerPortable/data/songs/ingest_michael_jackson_beat_it.feedpak",
    "AuralPrimerPortable/data/songs/ingest_psalm_4_trouble_again_edit.feedpak",
    "AuralPrimerPortable/data/songs/ingest_psalm_88_darkness.feedpak",
]


def _drum_instrument(events) -> pretty_midi.Instrument:
    inst = pretty_midi.Instrument(program=0, is_drum=True, name="drums")
    for e in events:
        dur = float(getattr(e, "duration", 0.0) or 0.0)
        start = float(e.time)
        end = start + max(dur, 0.03)  # MIDI notes need positive length
        inst.notes.append(
            pretty_midi.Note(
                velocity=max(1, min(127, int(e.velocity))),
                pitch=int(e.note),
                start=start,
                end=end,
            )
        )
    return inst


def refresh(pack: Path, registry) -> None:
    drums = pack / "audio" / "stems" / "drums.wav"
    drum_tab_path = pack / "drum_tab.json"
    notes_mid = pack / "aural" / "notes.mid"

    old_hits = 0
    if drum_tab_path.exists():
        try:
            old_hits = len(json.loads(drum_tab_path.read_text("utf-8")).get("hits", []))
        except Exception:
            old_hits = 0

    t0 = time.time()
    result = transcribe_drums_with_profile(
        drums,
        profile="gameplay_default",
        algorithm_registry=registry,
        logger=lambda m: None,
    )
    gated, gate_meta = validate_drum_events_against_stem_silence(result.events, drums)

    inst = _drum_instrument(gated)
    drum_tab = _build_drum_tab(inst, "drums")
    drum_tab_path.write_text(json.dumps(drum_tab, indent=2), encoding="utf-8")

    if notes_mid.exists():
        pm = pretty_midi.PrettyMIDI(str(notes_mid))
        pm.instruments = [i for i in pm.instruments if not i.is_drum] + [inst]
        pm.write(str(notes_mid))

    dt = time.time() - t0
    print(
        f"{pack.name}: engine={result.used_algorithm} "
        f"| hits {old_hits} -> {len(drum_tab['hits'])} "
        f"| gate dropped {gate_meta.get('dropped', '?')} "
        f"| {dt:.0f}s",
        flush=True,
    )


def main() -> int:
    print("[boot] script started", flush=True)
    registry = build_default_drum_algorithm_registry()
    print("[boot] drum registry built", flush=True)
    for raw in PACKS:
        pack = Path(raw)
        if not (pack / "audio" / "stems" / "drums.wav").exists():
            print(f"SKIP {pack.name}: no drums stem", flush=True)
            continue
        try:
            print(f"[start] {pack.name} ...", flush=True)
            refresh(pack, registry)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {pack.name}: {type(exc).__name__}: {exc}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
