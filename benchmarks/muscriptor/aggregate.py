"""Aggregate the MuScriptor eval across all Piano Psalms.

NOTE: this is the exact script used for the 2026-08-01 run, with that run's
absolute paths (D:\\Psalms\\Piano Psalms source + a scratch packs dir) baked in.
It is committed for provenance, not as a reusable harness -- re-point PACKS /
STEMS_ROOT / SONGS to rerun. `score.py` beside it IS reusable (takes paths as
args).

For each song: score the MuScriptor pack's notes.mid against the authored Suno
stem MIDI (ground truth), then print a per-song / per-instrument table plus a
weighted summary. Reports coverage separately from F1 so "never emitted the
part" is not conflated with "emitted the wrong notes".
"""
from __future__ import annotations

import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from score import score_song  # noqa: E402

PACKS = Path(r"D:\AuralPrimer\tmp\muscriptor_eval\packs")
STEMS_ROOT = Path(r"D:\Psalms\Piano Psalms")

# pack name -> authored Suno "Stems" folder (ground truth)
SONGS = {
    "psalm_121_my_help": "Psalm 121 - My Help - Piano Stems",
    "psalm_10_why": "Psalm 10 - Why - piano Stems",
    "psalm_10_why_instrumental": "Psalm 10 - Why - Piano Instrumental Stems",
    "psalm_5_every_morning": "Psalm 5 - Every Morning - Piano Stems",
    "psalm_5_every_morning_instrumental": "psalm 5 - Every Morning - Instrumental Stems",
    "psalm_6_how_long": "Psalm 6 - How Long - piano Stems",
    "psalm_6_how_long_piano_instrumental": "Psalm 6 - How Long - piano - instrumental Stems",
    "psalm_130_please_hear_me": "psalm 130 - Please Hear Me Stems",
    "psalm_130_please_hear_me_instrumental": "psalm 130 - Please Hear Me - instrumental Stems",
}

INSTS = ["keys", "bass", "drums", "vocals"]


def main() -> None:
    results = {}
    for pack, stems in SONGS.items():
        notes = PACKS / f"{pack}.feedpak" / "aural" / "notes.mid"
        sd = STEMS_ROOT / stems
        if not notes.exists():
            results[pack] = {"error": "no notes.mid (import missing/failed)"}
            continue
        if not sd.is_dir():
            results[pack] = {"error": f"stems dir missing: {sd}"}
            continue
        results[pack] = score_song(notes, sd)

    # Per-song table.
    print("\n=== Per-song note F1 (MuScriptor vs authored MIDI, onset+pitch, 50ms) ===\n")
    hdr = f"{'song':40s} " + " ".join(f"{i:>16s}" for i in INSTS)
    print(hdr)
    print("-" * len(hdr))
    # Accumulate weighted-by-ref-notes F1 per instrument.
    agg = {i: {"ref": 0, "f1w": 0.0, "recallw": 0.0, "covered": 0, "authored": 0} for i in INSTS}
    for pack in SONGS:
        r = results[pack]
        if "error" in r:
            print(f"{pack:40s}  {r['error']}")
            continue
        cells = []
        for i in INSTS:
            s = r["instruments"].get(i)
            if not s or not s.get("authored"):
                cells.append(f"{'—':>16s}")
                continue
            agg[i]["authored"] += 1
            agg[i]["ref"] += s["ref_notes"]
            f1 = s["f1"] if s["f1"] is not None else 0.0
            rec = s.get("recall") or 0.0
            agg[i]["f1w"] += f1 * s["ref_notes"]
            agg[i]["recallw"] += rec * s["ref_notes"]
            if s["muscriptor_emitted"]:
                agg[i]["covered"] += 1
            cells.append(f"{f1:6.3f}(n={s['ref_notes']:>4d})")
        print(f"{pack:40s} " + " ".join(cells))

    print("\n=== Weighted summary (F1 weighted by authored note count) ===\n")
    print(f"{'instrument':12s} {'songs':>6s} {'covered':>8s} {'ref_notes':>10s} {'wF1':>7s} {'wRecall':>8s}")
    for i in INSTS:
        a = agg[i]
        if a["authored"] == 0:
            continue
        wf1 = a["f1w"] / a["ref"] if a["ref"] else 0.0
        wrec = a["recallw"] / a["ref"] if a["ref"] else 0.0
        print(f"{i:12s} {a['authored']:>6d} {a['covered']:>3d}/{a['authored']:<4d} "
              f"{a['ref']:>10d} {wf1:>7.3f} {wrec:>8.3f}")

    (PACKS.parent / "eval_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nJSON: {PACKS.parent / 'eval_results.json'}")


if __name__ == "__main__":
    main()
