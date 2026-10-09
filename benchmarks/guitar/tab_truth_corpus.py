"""Inventory a local corpus of feedpak tabs and score the fingering solver on it.

For every arrangement of every ``*.feedpak`` / ``*.sloppak`` (zip or
directory) under ``--songs`` this writes the normalized tab truth
(``aural_ingest.cli tab-truth`` shape, one arrangement per file) to
``<out>/<song-slug>/<arr-id>.tab_truth.json``, then feeds the truth pitches
(t_on, t_off, pitch, velocity=100 -- no string/fret) through the heuristic
solver ``cli._build_fingering_doc`` and compares its string/fret choices with
the authored tab. ``summary.json`` + ``summary.md`` hold the inventory and
the scores.

Scoring (standard tuning, no capo only -- the solver assumes both): a
prediction matches a truth note with the same (t_on, pitch). Coverage =
matched / truth notes; exact / string accuracy and mean |fret diff| are over
matched notes. Pooled numbers are note-weighted.

The corpus may hold commercial charts: run outputs live in the git-ignored
``benchmarks/guitar/tab_truth_runs/``. Never commit them.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "python" / "ingest" / "src"
sys.path.insert(0, str(SRC))

from aural_ingest import cli  # noqa: E402
from aural_ingest.feedpak_tab import (  # noqa: E402
    BASS_OCTAVE,
    STANDARD,
    TECHNIQUE_KEYS,
    full_difficulty_events,
    is_bass,
    read_pack,
)

DEFAULT_SONGS = Path(r"D:\feedbackSongs")
RUNS_DIR = ROOT / "benchmarks" / "guitar" / "tab_truth_runs"
_PITCH_CLASSES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "song"


def tuning_name(offsets: list[int]) -> str:
    """Human name for per-string offsets from E standard (low string first)."""
    if offsets and len(set(offsets)) == 1:
        return f"{_PITCH_CLASSES[(4 + offsets[0]) % 12]} standard"
    if len(offsets) > 1 and len(set(offsets[1:])) == 1 and offsets[0] == offsets[1] - 2:
        return f"Drop {_PITCH_CLASSES[(4 + offsets[0]) % 12]}"
    return f"custom {offsets}"


def _raw_note_count(arr: dict[str, Any]) -> int:
    """Notes the full chart authors, before feedpak_tab drops unplayable ones."""
    templates = arr.get("templates") if isinstance(arr.get("templates"), list) else []
    singles, chords = full_difficulty_events(arr)
    count = len(singles)
    for chord in chords:
        members = chord.get("notes")
        if isinstance(members, list) and members:
            count += len(members)
            continue
        tid = chord.get("id")
        if isinstance(tid, int) and 0 <= tid < len(templates):
            count += sum(
                1 for f in templates[tid].get("frets") or [] if isinstance(f, int) and f >= 0
            )
    return count


def score(role: str, truth: list[dict[str, Any]]) -> dict[str, Any]:
    """Solver vs authored tab for one arrangement (see module docstring)."""
    fed = [n for n in truth if 0 <= n["midi"] <= 127 and n["t_off"] > n["t"]]
    notes = [
        SimpleNamespace(t_on=n["t"], t_off=n["t_off"], pitch=n["midi"], velocity=100) for n in fed
    ]
    doc = cli._build_fingering_doc(role, notes) or {"notes": []}

    want: dict[tuple[float, int], list[tuple[int, int]]] = defaultdict(list)
    for n in fed:
        want[(round(n["t"], 6), n["midi"])].append((n["s"], n["f"]))
    got: dict[tuple[float, int], list[tuple[int, int]]] = defaultdict(list)
    for p in doc["notes"]:
        got[(p["t_on"], p["pitch"])].append((p["string"], p["fret"]))

    matched = exact = same_string = fret_diff = 0
    for key, truths in want.items():
        preds = list(got.get(key, []))
        rest: list[tuple[int, int]] = []
        for pos in truths:  # pair identical positions first (unisons, chords)
            if pos in preds:
                preds.remove(pos)
                matched += 1
                exact += 1
                same_string += 1
            else:
                rest.append(pos)
        for t_pos, p_pos in zip(sorted(rest), sorted(preds)):
            matched += 1
            same_string += t_pos[0] == p_pos[0]
            fret_diff += abs(t_pos[1] - p_pos[1])
    return {
        "truth_notes": len(fed),
        "matched": matched,
        "exact": exact,
        "same_string": same_string,
        "fret_diff_sum": fret_diff,
        **_rates(len(fed), matched, exact, same_string, fret_diff),
    }


def _rates(
    total: int, matched: int, exact: int, same_string: int, fret_diff: int
) -> dict[str, Any]:
    return {
        "coverage": matched / total if total else None,
        "exact_accuracy": exact / matched if matched else None,
        "string_accuracy": same_string / matched if matched else None,
        "mean_abs_fret_diff": fret_diff / matched if matched else None,
    }


def _pool(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys = ("truth_notes", "matched", "exact", "same_string", "fret_diff_sum")
    sums = {k: sum(r["score"][k] for r in rows) for k in keys}
    return {"arrangements": len(rows), **sums, **_rates(*(sums[k] for k in keys))}


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{100 * value:.1f}%"


def _num(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def run(songs: Path, out: Path) -> dict[str, Any]:
    # Originals sort before their "name (1).feedpak" copies, so the copy is the one skipped.
    packs = sorted(
        (p for p in songs.iterdir() if p.suffix in {".feedpak", ".sloppak"}),
        key=lambda p: (re.sub(r" \(\d+\)$", "", p.stem), bool(re.search(r" \(\d+\)$", p.stem))),
    )
    seen: dict[str, str] = {}
    duplicates: list[dict[str, str]] = []
    rows: list[dict[str, Any]] = []

    for pack in packs:
        if pack.is_file():
            digest = hashlib.sha256(pack.read_bytes()).hexdigest()
            if digest in seen:
                duplicates.append({"pack": pack.name, "duplicate_of": seen[digest]})
                continue
            seen[digest] = pack.name

        doc = cli._tab_truth_doc(pack)
        _, raw = read_pack(pack)
        song_dir = out / _slug(pack.stem)
        song_dir.mkdir(parents=True, exist_ok=True)
        song = f"{doc.get('artist')} - {doc.get('title')}"

        for arr_doc, item in zip(doc["arrangements"], raw):
            entry, arr = item["entry"], item["arrangement"]
            notes = arr_doc["notes"]
            arr_id = str(arr_doc["id"] or arr_doc["name"] or "arrangement")
            (song_dir / f"{_slug(arr_id)}.tab_truth.json").write_text(
                json.dumps({**{k: doc[k] for k in ("title", "artist")}, "arrangements": [arr_doc]}),
                encoding="utf-8",
            )

            bass = is_bass(arr, entry)
            base = [p - BASS_OCTAVE for p in STANDARD] if bass else STANDARD
            opens = arr_doc["open_strings_midi"]
            offsets = [o - b for o, b in zip(opens, base)]
            techniques = Counter(k for n in notes for k in TECHNIQUE_KEYS if k in n)
            row: dict[str, Any] = {
                "song": song,
                "pack": pack.name,
                "arrangement": arr_id,
                "name": arr_doc["name"],
                "type": arr_doc["type"],
                "role": arr_doc["role"],
                "tuning": tuning_name(offsets),
                "open_strings_midi": opens,
                "capo": arr_doc["capo"],
                "notes": len(notes),
                "dropped_notes": _raw_note_count(arr) - len(notes),
                "chords": len({n["t"] for n in notes if "chord_id" in n}),
                "max_string": max((n["s"] for n in notes), default=None),
                "fret_range": [
                    min((n["f"] for n in notes), default=None),
                    max((n["f"] for n in notes), default=None),
                ],
                "midi_range": [
                    min((n["midi"] for n in notes), default=None),
                    max((n["midi"] for n in notes), default=None),
                ],
                "top_techniques": dict(techniques.most_common(5)),
            }
            if any(offsets):
                row["skipped"] = "non-standard tuning"
            elif arr_doc["capo"]:
                row["skipped"] = "capo"
            else:
                kind = f"{arr_doc['id']} {arr_doc['name']}".lower()
                solver_role = (
                    "bass" if bass else ("rhythm_guitar" if "rhythm" in kind else "lead_guitar")
                )
                row["solver_role"] = solver_role
                row["score"] = score(solver_role, notes)
            rows.append(row)

    scored = [r for r in rows if "score" in r]
    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "songs_dir": str(songs),
        "packs": len(packs) - len(duplicates),
        "duplicates_skipped": duplicates,
        "pooled": {
            "all": _pool(scored),
            "bass": _pool([r for r in scored if r["solver_role"] == "bass"]),
            "guitar": _pool([r for r in scored if r["solver_role"] != "bass"]),
        },
        "arrangements": rows,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "summary.md").write_text(_markdown(summary), encoding="utf-8")
    return summary


def _markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Tab-truth corpus run",
        "",
        f"- run: {summary['run_at']}",
        f"- songs: `{summary['songs_dir']}` ({summary['packs']} packs; "
        f"{len(summary['duplicates_skipped'])} byte-identical duplicates skipped)",
        "",
        "## Fingering solver vs authored tab (pooled, standard tuning only)",
        "",
        "Coverage = matched / truth notes; accuracies and fret diff are over matched notes.",
        "",
        "| scope | arrangements | truth notes | coverage | exact position | string | mean abs fret diff |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for scope, p in summary["pooled"].items():
        lines.append(
            f"| {scope} | {p['arrangements']} | {p['truth_notes']} | {_pct(p['coverage'])} | "
            f"{_pct(p['exact_accuracy'])} | {_pct(p['string_accuracy'])} | "
            f"{_num(p['mean_abs_fret_diff'])} |"
        )
    lines += [
        "",
        "## Arrangements",
        "",
        "| song | arrangement | type | tuning | capo | notes | chords | dropped | top techniques "
        "| coverage | exact | string | fret diff |",
        "|---|---|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|",
    ]
    for r in summary["arrangements"]:
        tech = ", ".join(f"{k} {v}" for k, v in r["top_techniques"].items()) or "-"
        if "score" in r:
            s = r["score"]
            scores = (
                f"{_pct(s['coverage'])} | {_pct(s['exact_accuracy'])} | "
                f"{_pct(s['string_accuracy'])} | {_num(s['mean_abs_fret_diff'])}"
            )
        else:
            scores = f"skipped: {r['skipped']} | | |"
        lines.append(
            f"| {r['song']} | {r['arrangement']} | {r['type'] or '-'} | {r['tuning']} | "
            f"{r['capo']} | {r['notes']} | {r['chords']} | {r['dropped_notes']} | {tech} | {scores} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(prog="tab_truth_corpus", description=__doc__.splitlines()[0])
    parser.add_argument("--songs", type=Path, default=DEFAULT_SONGS, help="Folder of feedpaks.")
    parser.add_argument("--out", type=Path, default=None, help="Run dir (default: timestamped).")
    args = parser.parse_args()
    out = args.out or RUNS_DIR / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    summary = run(args.songs, out)
    print(json.dumps({"out": str(out), "pooled": summary["pooled"]}, indent=2))


if __name__ == "__main__":
    main()
