#!/usr/bin/env python3
"""Build the piano shootout report from cached predictions.

Reads a run directory produced by ``run_piano_shootout.py run`` and emits the
PROCESS.md-required artifacts plus this round's extra analyses. Runs entirely
off cached predictions, so it is cheap to re-run whenever new ground truth
lands (official sheet music, a corrected offset, a hand-built reference).

Two scoring tiers, kept visually and numerically distinct because they answer
different questions:

``gold``  paired Suno keyboard stems with exactly-aligned reference MIDI ->
          real precision / recall / F1 / velocity / offset accuracy.
``song``  the three real recordings. Without note-level ground truth these are
          scored *relatively*: cross-engine agreement, a consensus reference,
          and playability statistics. Clearly labelled as relative, never
          presented as accuracy.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "python" / "ingest" / "src"))

from aural_ingest.melodic_benchmark_suite import (  # noqa: E402
    _render_grouped_bar_svg,
    _render_heatmap_svg,
    _render_timing_mae_svg,
)
from aural_ingest.piano_benchmark import (  # noqa: E402
    PianoBenchmarkEvent,
    evaluate_piano,
    parse_piano_midi_reference,
)
from aural_ingest.piano_benchmark_suite import (  # noqa: E402
    REQUIRED_VISUALIZATION_FILES,
    summarize_piano_suite_results,
)
from aural_ingest.transcription import MelodicNote  # noqa: E402

ONSET_TOL = 0.06
OFFSET_TOL = 0.12
VELOCITY_TOL = 20


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def load_predictions(run_dir: Path, item_id: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    pred_dir = run_dir / "predictions" / item_id
    if not pred_dir.is_dir():
        return out
    for path in sorted(pred_dir.glob("*.json")):
        try:
            out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            out[path.stem] = {"algorithm": path.stem, "status": "unreadable", "error": str(exc), "notes": []}
    return out


def notes_from_record(record: dict[str, Any]) -> list[MelodicNote]:
    return [
        MelodicNote(
            t_on=float(n["t_on"]), t_off=float(n["t_off"]),
            pitch=int(n["pitch"]), velocity=int(n["velocity"]),
            instrument=str(n.get("instrument", "keys")),
        )
        for n in record.get("notes") or []
    ]


# ---------------------------------------------------------------------------
# reference offset calibration
# ---------------------------------------------------------------------------

def calibrate_offset(
    wav: Path,
    reference: list[PianoBenchmarkEvent],
    *,
    max_shift_sec: float = 1.5,
) -> tuple[float, float]:
    """Find the shift that best aligns reference onsets to the audio.

    Purely audio-vs-reference (an onset-strength envelope cross-correlated with
    the reference onset impulse train) so no transcription engine influences
    the offset that all engines are then scored against.

    Returns ``(offset_sec, peak_correlation)``.
    """
    try:
        import librosa
        import numpy as np
    except Exception:
        return 0.0, float("nan")
    if not reference:
        return 0.0, float("nan")

    sr, hop = 22050, 256
    y, _ = librosa.load(str(wav), sr=sr, mono=True)
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    if env.size == 0:
        return 0.0, float("nan")
    env = (env - env.mean()) / (env.std() or 1.0)

    impulses = np.zeros_like(env)
    for event in reference:
        idx = int(round(float(event.time) * sr / hop))
        if 0 <= idx < impulses.size:
            impulses[idx] += 1.0
    if impulses.sum() <= 0:
        return 0.0, float("nan")
    impulses = (impulses - impulses.mean()) / (impulses.std() or 1.0)

    max_lag = int(round(max_shift_sec * sr / hop))
    best_lag, best_score = 0, -math.inf
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            a, b = env[lag:], impulses[: impulses.size - lag] if lag else impulses
        else:
            a, b = env[: env.size + lag], impulses[-lag:]
        n = min(a.size, b.size)
        if n < 16:
            continue
        score = float(np.dot(a[:n], b[:n]) / n)
        if score > best_score:
            best_lag, best_score = lag, score
    return round(best_lag * hop / sr, 4), round(best_score, 4)


# ---------------------------------------------------------------------------
# reference-free analysis
# ---------------------------------------------------------------------------

def _onset_key_set(notes: Iterable[MelodicNote], quantum: float = ONSET_TOL) -> set[tuple[int, int]]:
    return {(int(round(float(n.t_on) / quantum)), int(n.pitch)) for n in notes}


def pairwise_agreement(
    per_algo: dict[str, list[MelodicNote]],
) -> tuple[list[str], list[list[float]]]:
    """Symmetric note-level agreement (F1 of one engine's notes against another's)."""
    names = sorted(per_algo)
    keys = {name: _onset_key_set(per_algo[name]) for name in names}
    matrix = [[0.0] * len(names) for _ in names]
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            ka, kb = keys[a], keys[b]
            if not ka and not kb:
                matrix[i][j] = 1.0 if i == j else 0.0
                continue
            inter = len(ka & kb)
            denom = len(ka) + len(kb)
            matrix[i][j] = round(2 * inter / denom, 4) if denom else 0.0
    return names, matrix


def consensus_reference(
    per_algo: dict[str, list[MelodicNote]],
    *,
    min_votes: int,
) -> list[PianoBenchmarkEvent]:
    """Notes that at least ``min_votes`` engines independently agree on.

    A weak proxy for ground truth -- it can only contain notes some engine
    already found, and it inherits any error the majority shares. Used for
    relative ranking on the real songs, never presented as accuracy.
    """
    votes: dict[tuple[int, int], list[MelodicNote]] = defaultdict(list)
    for notes in per_algo.values():
        seen: set[tuple[int, int]] = set()
        for note in notes:
            key = (int(round(float(note.t_on) / ONSET_TOL)), int(note.pitch))
            if key in seen:
                continue
            seen.add(key)
            votes[key].append(note)

    events: list[PianoBenchmarkEvent] = []
    for (_bucket, pitch), members in votes.items():
        if len(members) < min_votes:
            continue
        t_on = statistics.median(float(m.t_on) for m in members)
        dur = statistics.median(max(0.02, float(m.t_off) - float(m.t_on)) for m in members)
        vel = int(statistics.median(int(m.velocity) for m in members))
        events.append(PianoBenchmarkEvent(time=round(t_on, 6), pitch=int(pitch),
                                          duration=round(dur, 6), velocity=vel))
    return sorted(events, key=lambda e: (e.time, e.pitch))


def playability_stats(notes: list[MelodicNote], duration_sec: float) -> dict[str, Any]:
    """Statistics that say whether output is *playable*, independent of accuracy."""
    if not notes:
        return {"note_count": 0}
    pitches = [int(n.pitch) for n in notes]
    durs = [max(0.0, float(n.t_off) - float(n.t_on)) for n in notes]

    # Max simultaneous voices via a sweep over onset/offset boundaries.
    edges = sorted([(float(n.t_on), 1) for n in notes] + [(float(n.t_off), -1) for n in notes])
    cur = peak = 0
    for _t, delta in edges:
        cur += delta
        peak = max(peak, cur)

    out_of_range = sum(1 for p in pitches if p < 21 or p > 108)
    very_short = sum(1 for d in durs if d < 0.05)
    return {
        "note_count": len(notes),
        "notes_per_sec": round(len(notes) / max(1e-9, duration_sec), 3),
        "max_polyphony": peak,
        "pitch_min": min(pitches),
        "pitch_max": max(pitches),
        "pitch_span_semitones": max(pitches) - min(pitches),
        "out_of_piano_range": out_of_range,
        "out_of_range_rate": round(out_of_range / len(notes), 4),
        "very_short_notes": very_short,
        "very_short_rate": round(very_short / len(notes), 4),
        "mean_duration_sec": round(sum(durs) / len(durs), 4),
        "median_duration_sec": round(statistics.median(durs), 4),
        "mean_velocity": round(sum(int(n.velocity) for n in notes) / len(notes), 2),
        "velocity_stdev": round(statistics.pstdev([int(n.velocity) for n in notes]), 2),
    }


# ---------------------------------------------------------------------------
# report assembly
# ---------------------------------------------------------------------------

def audio_duration(path: Path) -> float:
    try:
        import soundfile as sf

        return float(sf.info(str(path)).duration)
    except Exception:
        return 0.0


def build(run_dir: Path, *, consensus_fraction: float = 0.34) -> dict[str, Any]:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    items = manifest["items"]

    algorithms: list[str] = []
    songs_payload: list[dict[str, Any]] = []
    extras: dict[str, Any] = {"agreement": {}, "playability": {}, "consensus": {}, "runtime": {}}

    for item in items:
        preds = load_predictions(run_dir, item["id"])
        if not preds:
            continue
        for name in preds:
            if name not in algorithms:
                algorithms.append(name)

        wav = Path(item["wav"])
        dur = audio_duration(wav)
        per_algo = {name: notes_from_record(rec) for name, rec in preds.items()}
        usable = {n: v for n, v in per_algo.items() if v}

        reference: list[PianoBenchmarkEvent] | None = None
        reference_kind = "none"
        offset_used = float(item.get("offset_sec", 0.0) or 0.0)
        offset_corr = None

        midi = item.get("midi")
        sheet = run_dir / "references" / f"{item['id']}.json"
        if sheet.is_file():
            # Sheet-derived reference produced by align_sheet_reference.py.
            # Kept in its own tier: chroma-DTW lands notes to roughly the
            # scoring tolerance itself, so it ranks engines fairly (every
            # engine sees the same warped reference) but must not be quoted
            # as absolute accuracy.
            data = json.loads(sheet.read_text(encoding="utf-8"))
            reference = [
                PianoBenchmarkEvent(time=float(e["time"]), pitch=int(e["pitch"]),
                                    duration=float(e["duration"]), velocity=int(e["velocity"]))
                for e in data.get("events", [])
            ]
            reference_kind = "sheet_aligned"
            extras.setdefault("sheet", {})[item["id"]] = {
                "source": data.get("source"),
                "transpose_semitones": data.get("transpose_semitones"),
                "dtw_cost": data.get("dtw_cost"),
                "events": len(reference),
            }
        elif midi and Path(midi).is_file():
            raw = parse_piano_midi_reference(Path(midi), 0.0, role=item.get("instrument", "keys"))
            offset_used, offset_corr = calibrate_offset(wav, raw)
            reference = parse_piano_midi_reference(Path(midi), offset_used, role=item.get("instrument", "keys"))
            reference_kind = "gold_midi"
        elif usable:
            min_votes = max(2, int(round(len(usable) * consensus_fraction)))
            reference = consensus_reference(usable, min_votes=min_votes)
            reference_kind = "consensus"
            extras["consensus"][item["id"]] = {
                "min_votes": min_votes, "voters": len(usable), "events": len(reference),
            }

        results = []
        for name in algorithms:
            rec = preds.get(name)
            if rec is None:
                continue
            notes = per_algo.get(name, [])
            entry: dict[str, Any] = {
                "algorithm": name,
                "status": rec.get("status"),
                "note_count": len(notes),
                "elapsed_sec": rec.get("elapsed_sec"),
                "error": rec.get("error"),
                "prediction": rec.get("prediction") or {},
            }
            if reference is not None:
                ev = evaluate_piano(notes, reference, tolerance_sec=ONSET_TOL,
                                    offset_tolerance_sec=OFFSET_TOL, velocity_tolerance=VELOCITY_TOL)
                entry["overall"] = ev.to_dict()
            results.append(entry)
            extras["runtime"].setdefault(name, []).append(
                (rec.get("elapsed_sec") or 0.0) / max(1e-9, dur))

        names, matrix = pairwise_agreement(usable) if usable else ([], [])
        extras["agreement"][item["id"]] = {"algorithms": names, "matrix": matrix}
        extras["playability"][item["id"]] = {
            n: playability_stats(v, dur) for n, v in per_algo.items()
        }

        songs_payload.append({
            "song_id": item["id"],
            "song_name": item.get("name", item["id"]),
            "tier": item.get("tier"),
            "instrument": item.get("instrument", "keys"),
            "wav_path": str(wav),
            "duration_sec": round(dur, 2),
            "reference_path": midi,
            "reference_kind": reference_kind,
            "reference_available": reference is not None and reference_kind == "gold_midi",
            "reference_relative": reference is not None and reference_kind != "gold_midi",
            "reference_count": len(reference) if reference else 0,
            "midi_offset_sec": offset_used,
            "offset_correlation": offset_corr,
            "tolerance_ms": ONSET_TOL * 1000,
            "offset_tolerance_ms": OFFSET_TOL * 1000,
            "results": results,
        })

    return {
        "label": manifest.get("label"),
        "generated": datetime.now().isoformat(timespec="seconds"),
        "algorithms": algorithms,
        "tolerance_ms": ONSET_TOL * 1000,
        "offset_tolerance_ms": OFFSET_TOL * 1000,
        "songs": songs_payload,
        "extras": extras,
    }


def _mean(values: Iterable[float | None]) -> float | None:
    vals = [float(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def write_artifacts(run_dir: Path, payload: dict[str, Any]) -> None:
    algorithms = list(payload["algorithms"])
    gold = [s for s in payload["songs"] if s["reference_available"]]
    rel = [s for s in payload["songs"] if s.get("reference_relative")]

    gold_payload = {**payload, "songs": gold}
    summary = summarize_piano_suite_results(gold_payload)
    case_order = list(summary["case_order"])
    summaries = summary["algorithm_summaries"]

    def col(key: str) -> list[float | None]:
        return [entry.get(key) for entry in summaries]

    artifacts: dict[str, str] = {
        "overall_f1_heatmap.svg": _render_heatmap_svg(
            title="Piano Exact-Note F1 (gold tier)",
            subtitle="Paired Suno keyboard stems with aligned reference MIDI. Higher is better.",
            row_labels=algorithms, col_labels=case_order, values=summary["overall_f1_matrix"]),
        "offset_f1_heatmap.svg": _render_heatmap_svg(
            title="Piano Note+Offset F1 (gold tier)",
            subtitle="Notes whose offsets also land inside tolerance -- exposes weak sustain modelling.",
            row_labels=algorithms, col_labels=case_order, values=summary["offset_f1_matrix"]),
        "pitch_accuracy_heatmap.svg": _render_heatmap_svg(
            title="Pitch Accuracy (gold tier)",
            subtitle="Exact-pitch matches divided by onset-only matches. Low = octave/voicing drift.",
            row_labels=algorithms, col_labels=case_order, values=summary["pitch_accuracy_matrix"]),
        "algorithm_summary.svg": _render_grouped_bar_svg(
            title="Aggregate Algorithm Summary (gold tier)",
            subtitle="Mean exact-note F1 vs note+offset F1. A large gap means sustains are wrong.",
            algorithms=algorithms,
            series=(("Mean F1", col("mean_f1"), "#56b6c2"),
                    ("Offset F1", col("mean_note_with_offset_f1"), "#ff8f5a")),
            y_label="score", max_value=1.0),
        "velocity_mae.svg": _render_timing_mae_svg(
            title="Mean Velocity MAE by Algorithm (gold tier)",
            subtitle="Predicted vs reference MIDI velocity. Lower is better; flat output reads as unplayable.",
            algorithms=algorithms, values=col("mean_velocity_mae")),
        "duplicate_rate.svg": _render_heatmap_svg(
            title="Duplicate Prediction Rate (gold tier)",
            subtitle="Same-pitch micro-duplicates. Lower is better -- the double-trigger view.",
            row_labels=algorithms, col_labels=case_order,
            values=summary["duplicate_rate_matrix"],
            color_good="#ef4444", color_bad="#22c55e"),
    }

    tiers: dict[str, list[int]] = {}
    for si, song in enumerate(gold):
        tiers.setdefault(song.get("instrument", "keys"), []).append(si)
    artifacts["instrument_summary.svg"] = _render_grouped_bar_svg(
        title="Per-Instrument Mean F1 (gold tier)",
        subtitle="Splits the suite by instrument bucket.",
        algorithms=algorithms,
        series=tuple(
            (inst,
             [sum(summary["overall_f1_matrix"][ai][si] for si in idxs) / max(1, len(idxs))
              for ai in range(len(algorithms))],
             ["#7bd389", "#ff8f5a", "#5aa9e6", "#c084fc"][ii % 4])
            for ii, (inst, idxs) in enumerate(tiers.items())
        ) or (("keys", [0.0] * len(algorithms), "#7bd389"),),
        y_label="F1", max_value=1.0)

    # Relative (real-song) view: agreement with the cross-engine consensus.
    if rel:
        rel_summary = summarize_piano_suite_results({
            **payload,
            "songs": [{**s, "reference_available": True} for s in rel],
        })
        kinds = {s["reference_kind"] for s in rel}
        if kinds == {"sheet_aligned"}:
            title = "Agreement with sheet-derived reference (real songs -- RELATIVE)"
            subtitle = ("Official sheet music warped onto each recording. Chroma-DTW timing error is "
                        "comparable to the scoring tolerance, so this ranks engines but understates all of them.")
        elif "sheet_aligned" in kinds:
            title = "Agreement with reference (real songs -- MIXED sheet/consensus, RELATIVE)"
            subtitle = "Per-item reference kind is listed in report.md. Ranking aid, not accuracy."
        else:
            title = "Agreement with cross-engine consensus (real songs -- RELATIVE, not accuracy)"
            subtitle = "No ground truth for these recordings yet; this ranks engines against each other only."
        artifacts["consensus_agreement_heatmap.svg"] = _render_heatmap_svg(
            title=title, subtitle=subtitle,
            row_labels=algorithms, col_labels=list(rel_summary["case_order"]),
            values=rel_summary["overall_f1_matrix"])

    enriched = {**payload, "summary": summary}
    artifacts["summary.json"] = json.dumps(enriched, indent=2, default=str)
    artifacts["report.md"] = render_markdown(payload, summary)
    artifacts["report.html"] = render_html(payload, summary)

    for name, content in artifacts.items():
        (run_dir / name).write_text(content, encoding="utf-8")

    missing = [n for n in REQUIRED_VISUALIZATION_FILES if not (run_dir / n).is_file()]
    if missing:
        raise RuntimeError(f"run incomplete: missing {', '.join(missing)}")


def render_markdown(payload: dict[str, Any], summary: dict[str, Any]) -> str:
    extras = payload["extras"]
    gold = [s for s in payload["songs"] if s["reference_available"]]
    rel = [s for s in payload["songs"] if s.get("reference_relative")]
    lines = [
        "# Piano Transcription Shootout",
        "",
        f"Generated: {payload['generated']}",
        f"Tolerance: {payload['tolerance_ms']:.0f} ms onset / {payload['offset_tolerance_ms']:.0f} ms offset",
        f"Engines: {len(payload['algorithms'])}    Items: {len(payload['songs'])} "
        f"({len(gold)} gold, {len(rel)} relative)",
        "",
        "## How to read this",
        "",
        "**Gold tier** — paired Suno keyboard stems whose reference MIDI is exactly aligned to the",
        "audio. These numbers are real precision / recall / F1.",
        "",
        "**Relative tier** — the three real recordings (Center, What A God, King of My Heart).",
        "There is no note-level ground truth for them yet, so engines are scored against a",
        "cross-engine consensus. That measures *agreement*, not correctness: an error most engines",
        "share scores as a success. Treat it as a ranking aid only.",
        "",
    ]

    def accuracy_table(title: str, blurb: str, subset: list[dict[str, Any]]) -> list[str]:
        if not subset:
            return []
        sub = summarize_piano_suite_results({**payload, "songs": subset})
        rows = ["", f"## {title}", "", blurb, "",
                "| Algorithm | F1 | Onset F1 | +Offset F1 | +Velocity F1 | Pitch acc | Vel MAE | Dup rate | Onset MAE |",
                "|---|---|---|---|---|---|---|---|---|"]
        for entry in sorted(sub["algorithm_summaries"], key=lambda e: -(e.get("mean_f1") or 0)):
            if not entry.get("mean_f1") and not entry.get("mean_onset_only_f1"):
                continue
            rows.append(
                f"| `{entry['algorithm']}` | {entry['mean_f1']:.3f} | {entry['mean_onset_only_f1']:.3f} | "
                f"{entry['mean_note_with_offset_f1']:.3f} | {entry['mean_note_with_offset_velocity_f1']:.3f} | "
                f"{entry['mean_pitch_accuracy']:.3f} | "
                f"{_fmt(entry.get('mean_velocity_mae'))} | {entry['mean_duplicate_rate']:.3f} | "
                f"{_fmt(entry.get('mean_onset_timing_mae_ms'))} |")
        return rows

    if gold:
        lines += accuracy_table(
            "Gold tier — accuracy (all gold items)",
            "Paired reference MIDI, offset-calibrated per item. Real precision / recall / F1.",
            gold)
        psalms = [s for s in gold if s.get("tier") == "gold"]
        maestro = [s for s in gold if s.get("tier") == "gold_maestro"]
        if psalms and maestro:
            lines += accuracy_table(
                "Gold — worship idiom (Piano Psalms)",
                "Suno keyboard stems with paired MIDI: the material this project actually targets.",
                psalms)
            lines += accuracy_table(
                "Gold — canonical solo piano (MAESTRO v3 test)",
                "Disklavier captures with ~3 ms-aligned MIDI. Comparable to published transcription "
                "numbers, so this doubles as a check that the harness itself is sound.",
                maestro)
        lines.append("")

    lines += ["## Throughput", "", "| Algorithm | mean x realtime |", "|---|---|"]
    for name in sorted(extras["runtime"], key=lambda n: _mean(extras["runtime"][n]) or 0):
        lines.append(f"| `{name}` | {(_mean(extras['runtime'][name]) or 0):.2f}x |")
    lines.append("")

    lines += ["## Per-item status", ""]
    for song in payload["songs"]:
        ok = sum(1 for r in song["results"] if r.get("status") == "ok" and r.get("note_count"))
        empty = sum(1 for r in song["results"] if r.get("status") == "ok" and not r.get("note_count"))
        bad = sum(1 for r in song["results"] if r.get("status") not in ("ok",))
        lines.append(f"- **{song['song_name']}** ({song['tier']}, {song['duration_sec']/60:.1f} min) — "
                     f"{ok} produced notes, {empty} returned nothing, {bad} failed; "
                     f"reference: {song['reference_kind']} ({song['reference_count']} events, "
                     f"offset {song['midi_offset_sec']:+.3f}s)")
    lines.append("")

    lines += ["## Engines that produced nothing", ""]
    dead = defaultdict(list)
    for song in payload["songs"]:
        for r in song["results"]:
            if r.get("status") != "ok" or not r.get("note_count"):
                dead[r["algorithm"]].append(song["song_id"])
    if dead:
        for name, ids in sorted(dead.items()):
            lines.append(f"- `{name}` — {len(ids)}/{len(payload['songs'])} items: {', '.join(ids[:6])}"
                         + (" ..." if len(ids) > 6 else ""))
    else:
        lines.append("- none")
    lines.append("")

    lines += ["## Strengths and weaknesses", "",
              "Each line below is triggered by a threshold on a metric measured in this run.", ""]
    findings = engine_findings(payload, summary)
    order = sorted(payload["algorithms"],
                   key=lambda n: -(next((e["mean_f1"] for e in summaries_of(summary)
                                         if e["algorithm"] == n), 0.0) or 0.0))
    for name in order:
        lines.append(f"### `{name}`")
        for note in findings[name]:
            lines.append(f"- {note}")
        lines.append("")

    lines += ["## Playability (real songs)", "",
              "Accuracy aside: would a player be able to read this?", "",
              "| Algorithm | notes/s | max poly | out-of-range | <50ms notes | vel stdev |",
              "|---|---|---|---|---|---|"]
    song_ids = [s["song_id"] for s in payload["songs"] if s.get("reference_relative")]
    for name in order:
        rows = [extras["playability"][i][name] for i in song_ids
                if extras["playability"].get(i, {}).get(name, {}).get("note_count")]
        if not rows:
            continue
        med = lambda k: statistics.median([r[k] for r in rows])  # noqa: E731
        lines.append(f"| `{name}` | {med('notes_per_sec'):.1f} | {med('max_polyphony'):.0f} | "
                     f"{med('out_of_range_rate'):.1%} | {med('very_short_rate'):.0%} | "
                     f"{med('velocity_stdev'):.1f} |")
    lines.append("")

    lines += ["## Verdicts on the disabled engines", ""]
    pti = [n for n in payload["algorithms"] if n.startswith("piano_pti")]
    pti_live = [n for n in pti if any(
        r.get("note_count") for s in payload["songs"] for r in s["results"] if r["algorithm"] == n)]
    if pti:
        lines.append(f"**`piano_pti*` (torch>=2 gate in `transcription.piano_pti_enabled()`)** — "
                     f"force-enabled for this run. {len(pti_live)}/{len(pti)} variants produced notes.")
        gold_pti = [e for e in summaries_of(summary) if e["algorithm"] in pti and e.get("mean_f1")]
        if gold_pti:
            best = max(gold_pti, key=lambda e: e["mean_f1"])
            others = [e for e in summaries_of(summary)
                      if e["algorithm"] not in pti and e.get("mean_f1")]
            rank = 1 + sum(1 for e in others if e["mean_f1"] > best["mean_f1"])
            lines.append(f"Best PTI variant `{best['algorithm']}` scores F1 {best['mean_f1']:.3f} on "
                         f"gold, ranking #{rank} against the {len(others)} non-PTI engines. "
                         + ("The gate looks stale and should be revisited."
                            if rank <= 3 else
                            "That does not by itself justify lifting the gate."))
        else:
            lines.append("No gold-tier scores available for PTI, so the gate cannot be judged here.")
        lines.append("")
    if "melodic_rmvpe" in payload["algorithms"]:
        live = sum(1 for s in payload["songs"] for r in s["results"]
                   if r["algorithm"] == "melodic_rmvpe" and r.get("note_count"))
        lines.append(f"**`melodic_rmvpe`** — produced notes on {live}/{len(payload['songs'])} items. "
                     + ("Returns nothing across the board in ~0.1 s, which is a silent no-op rather "
                        "than a transcription attempt; it should either be fixed or removed from the "
                        "keys registry." if live == 0 else "Behaves as a real producer here."))
        lines.append("")

    lines += ["## Not run", "",
              "- `piano_hft` — no local checkpoint; needs `AURAL_PIANO_HFT_CHECKPOINT` plus an "
              "external command, and the weights are license-gated.",
              "- `piano_d3rm` — checkpoints are on disk and the repo is at `D:/Code/d3rm`, but it "
              "ships only a PyTorch-Lightning MAESTRO-test-set harness, not an arbitrary-audio CLI.",
              ""]
    return "\n".join(lines) + "\n"


def summaries_of(summary: dict[str, Any]) -> list[dict[str, Any]]:
    return summary["algorithm_summaries"]


def engine_findings(payload: dict[str, Any], summary: dict[str, Any]) -> dict[str, list[str]]:
    """Derive per-engine strengths and weaknesses from the measured numbers.

    Every line is tied to a threshold on a metric that was actually computed,
    so the prose cannot drift away from the data behind it.
    """
    extras = payload["extras"]
    by_algo = {e["algorithm"]: e for e in summaries_of(summary)}
    findings: dict[str, list[str]] = {name: [] for name in payload["algorithms"]}

    # Median note density across engines, per item -- the yardstick for
    # "produces far more/fewer notes than its peers".
    density_by_item: dict[str, float] = {}
    for item_id, per_algo in extras["playability"].items():
        rates = [s["notes_per_sec"] for s in per_algo.values() if s.get("note_count")]
        if rates:
            density_by_item[item_id] = statistics.median(rates)

    for name in payload["algorithms"]:
        out = findings[name]
        gold = by_algo.get(name)

        produced = sum(1 for s in payload["songs"]
                       for r in s["results"] if r["algorithm"] == name and r.get("note_count"))
        total = len(payload["songs"])
        if produced == 0:
            out.append(f"**Produces nothing** on all {total} items — effectively inert.")
            continue
        if produced < total:
            out.append(f"Silent on {total - produced}/{total} items.")

        if gold and gold.get("mean_f1"):
            f1 = gold["mean_f1"]
            onset = gold["mean_onset_only_f1"]
            offs = gold["mean_note_with_offset_f1"]
            pitch = gold["mean_pitch_accuracy"]
            if onset > 0 and offs / max(onset, 1e-9) < 0.5:
                out.append(f"**Sustains are weak** — onset F1 {onset:.2f} collapses to "
                           f"{offs:.2f} once offsets must also match.")
            if pitch and pitch < 0.75:
                out.append(f"**Pitch drift** — only {pitch:.0%} of onset matches land on the "
                           f"right pitch (octave doubling / wrong inner voices).")
            if f1 >= 0.5:
                out.append(f"Strong exact-note accuracy on gold (F1 {f1:.2f}).")
            mae = gold.get("mean_onset_timing_mae_ms")
            if mae is not None and mae <= 25:
                out.append(f"Tight onset timing ({mae:.0f} ms MAE).")
            elif mae is not None and mae >= 45:
                out.append(f"Loose onset timing ({mae:.0f} ms MAE).")
            vel = gold.get("mean_velocity_mae")
            if vel is not None and vel >= 30:
                out.append(f"Velocity is unreliable ({vel:.0f} MAE) — dynamics will read flat.")

        dups = [d for d in (payload_dup(payload, i, name) for i in extras["playability"])
                if d is not None]
        if dups and statistics.median(dups) >= 0.10:
            out.append(f"**Double-triggers** — {statistics.median(dups):.0%} of notes are "
                       f"same-pitch micro-duplicates.")

        live = [(i, extras["playability"][i][name]) for i in extras["playability"]
                if extras["playability"].get(i, {}).get(name, {}).get("note_count")]
        stats = [s for _i, s in live]
        if stats:
            ratios = [s["notes_per_sec"] / density_by_item[i]
                      for i, s in live if density_by_item.get(i)]
            if ratios:
                r = statistics.median(ratios)
                if r >= 2.5:
                    out.append(f"**Over-produces** — {r:.1f}x the median engine's note density.")
                elif r <= 0.4:
                    out.append(f"**Sparse** — {r:.1f}x the median engine's note density; likely misses inner voices.")
            oor = statistics.median([s["out_of_range_rate"] for s in stats])
            if oor >= 0.02:
                out.append(f"{oor:.1%} of notes fall outside the 88-key range.")
            short = statistics.median([s["very_short_rate"] for s in stats])
            if short >= 0.25:
                out.append(f"**Fragmented** — {short:.0%} of notes are under 50 ms.")
            vstd = statistics.median([s["velocity_stdev"] for s in stats])
            if vstd <= 1.0:
                out.append("Velocity is constant — no dynamics at all.")
            poly = statistics.median([s["max_polyphony"] for s in stats])
            if poly >= 20:
                out.append(f"Peak polyphony {poly:.0f} — far beyond two hands.")

        rt = _mean(extras["runtime"].get(name, []))
        if rt is not None and rt >= 1.0:
            out.append(f"Slow: {rt:.1f}x realtime.")
        elif rt is not None and rt <= 0.1:
            out.append(f"Fast: {rt:.2f}x realtime.")

        if not out:
            out.append("No notable strengths or weaknesses flagged by the thresholds.")
    return findings


def payload_dup(payload: dict[str, Any], item_id: str, algorithm: str) -> float | None:
    for song in payload["songs"]:
        if song["song_id"] != item_id:
            continue
        for r in song["results"]:
            if r["algorithm"] == algorithm:
                pred = r.get("prediction") or {}
                val = pred.get("duplicate_rate")
                return float(val) if val is not None else None
    return None


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


def render_html(payload: dict[str, Any], summary: dict[str, Any]) -> str:
    md = render_markdown(payload, summary)
    svgs = ["overall_f1_heatmap.svg", "offset_f1_heatmap.svg", "pitch_accuracy_heatmap.svg",
            "algorithm_summary.svg", "instrument_summary.svg", "velocity_mae.svg",
            "duplicate_rate.svg", "consensus_agreement_heatmap.svg"]
    body = "".join(f'<h2>{s}</h2><object data="{s}" type="image/svg+xml"></object>' for s in svgs)
    return (
        "<!doctype html><meta charset='utf-8'><title>Piano Shootout</title>"
        "<style>body{font:14px/1.6 system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem}"
        "pre{white-space:pre-wrap;background:#f6f8fa;padding:1rem;border-radius:6px}"
        "object{max-width:100%;display:block;margin:1rem 0}</style>"
        f"<pre>{md.replace('&', '&amp;').replace('<', '&lt;')}</pre>{body}"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", required=True, help="run directory from run_piano_shootout.py")
    args = p.parse_args(argv)

    run_dir = Path(args.run)
    payload = build(run_dir)
    write_artifacts(run_dir, payload)
    (run_dir.parent.parent / "LATEST_RUN.txt").write_text(str(run_dir), encoding="utf-8")
    print(f"report written -> {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
