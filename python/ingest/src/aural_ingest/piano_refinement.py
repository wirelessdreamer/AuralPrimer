"""Standalone piano MIDI refinement workbench.

This module compares an existing source MIDI against piano-focused
transcription candidates for a matching audio file. It is intentionally
separate from normal SongPack import.
"""
from __future__ import annotations

from array import array
from dataclasses import replace
from datetime import datetime
import json
import math
from pathlib import Path
import time
from typing import Any, Iterable, Mapping, Sequence
import wave

from aural_ingest.algorithms import piano_cleanup
from aural_ingest.algorithms._common import estimate_duration_sec
from aural_ingest.algorithms.piano_midi import decode_midi_notes
from aural_ingest.piano_benchmark import (
    PIANO_ALGORITHMS,
    PianoBenchmarkEvent,
    evaluate_piano,
    melodic_notes_to_dicts,
    parse_piano_midi_reference,
    summarize_piano_predictions,
    write_melodic_notes_midi,
)
from aural_ingest.transcription import (
    MelodicNote,
    build_default_melodic_algorithm_registry,
    melodic_methods_for_profile,
)

DEFAULT_OUTPUT_ROOT = Path("benchmarks") / "piano" / "refinement_runs"
PLAYABLE_MAX_POLYPHONY = 7
PLAYABLE_ATTACK_CLUSTER_SEC = 0.055
PLAYABLE_MAX_NOTES_PER_ATTACK = 7
AUDITION_SAMPLE_RATE = 22_050
AUDITION_GAP_SEC = 0.75
DEFAULT_REFINEMENT_METHODS = [
    "source_midi",
    "source_midi_clean",
    "source_midi_clean_playable",
    "piano_auto",
    "piano_polyphonic_clean",
    "basic_pitch",
]
REQUIRED_REFINEMENT_ARTIFACTS = [
    "summary.json",
    "report.md",
    "refinement_dashboard.html",
    "playability_report.html",
    "playability_metrics.svg",
    "playability_polyphony.svg",
    "playability_roll.svg",
    "playability_audition_before.wav",
    "playability_audition_after.wav",
    "playability_audition_ab.wav",
    "candidates/index.json",
]


def _slugify(value: str) -> str:
    out: list[str] = []
    last_dash = False
    for ch in value.strip().lower():
        if ch.isalnum():
            out.append(ch)
            last_dash = False
        elif not last_dash:
            out.append("-")
            last_dash = True
    return "".join(out).strip("-") or "piano-refinement"


def parse_refinement_methods(methods: Sequence[str] | str | None) -> list[str]:
    if methods is None:
        return list(DEFAULT_REFINEMENT_METHODS)
    raw_items = [methods] if isinstance(methods, str) else list(methods)
    expanded: list[str] = []
    for raw_item in raw_items:
        for part in str(raw_item).split(","):
            method = part.strip()
            if not method:
                continue
            if method in {"default", "baseline"}:
                expanded.extend(DEFAULT_REFINEMENT_METHODS)
            elif method in {"all", "research_ab"}:
                expanded.extend(["source_midi", "source_midi_clean", "source_midi_clean_playable"])
                expanded.extend(melodic_methods_for_profile("research_ab", "keys"))
            elif method == "piano_suite":
                expanded.extend(["source_midi", "source_midi_clean", "source_midi_clean_playable"])
                expanded.extend(PIANO_ALGORITHMS)
            else:
                expanded.append(method)

    out: list[str] = []
    for method in expanded:
        if method not in out:
            out.append(method)
    return out or list(DEFAULT_REFINEMENT_METHODS)


def _offset_notes(notes: Iterable[MelodicNote], offset_sec: float) -> list[MelodicNote]:
    offset = float(offset_sec or 0.0)
    out: list[MelodicNote] = []
    for note in notes:
        t_on = max(0.0, float(note.t_on) + offset)
        duration = max(0.0, float(note.t_off) - float(note.t_on))
        out.append(replace(note, t_on=round(t_on, 6), t_off=round(t_on + duration, 6)))
    return sorted(out, key=lambda item: (item.t_on, item.pitch, item.t_off))


def _notes_to_reference_events(notes: Iterable[MelodicNote]) -> list[PianoBenchmarkEvent]:
    events: list[PianoBenchmarkEvent] = []
    for note in notes:
        duration = max(0.0, float(note.t_off) - float(note.t_on))
        if duration <= 0.0:
            continue
        events.append(
            PianoBenchmarkEvent(
                time=round(float(note.t_on), 6),
                pitch=int(note.pitch),
                duration=round(duration, 6),
                velocity=int(note.velocity),
            )
        )
    return sorted(events, key=lambda item: (item.time, item.pitch, item.duration))


def _events_to_note_dicts(events: Iterable[PianoBenchmarkEvent]) -> list[dict[str, Any]]:
    return [
        {
            "t_on": round(float(event.time), 6),
            "t_off": round(float(event.time) + float(event.duration), 6),
            "pitch": int(event.pitch),
            "velocity": int(event.velocity),
            "instrument": "keys",
        }
        for event in events
    ]


def _max_polyphony(notes: Sequence[MelodicNote]) -> int:
    points: list[tuple[float, int]] = []
    for note in notes:
        if float(note.t_off) <= float(note.t_on):
            continue
        points.append((float(note.t_on), 1))
        points.append((float(note.t_off), -1))
    active = 0
    peak = 0
    for _time, delta in sorted(points, key=lambda item: (item[0], item[1])):
        active += delta
        peak = max(peak, active)
    return peak


def _cluster_note_indices_by_onset(notes: Sequence[MelodicNote], *, window_sec: float) -> dict[int, int]:
    cluster_for_index: dict[int, int] = {}
    cluster_id = -1
    cluster_start: float | None = None
    for idx, note in sorted(enumerate(notes), key=lambda item: (item[1].t_on, item[1].pitch)):
        onset = float(note.t_on)
        if cluster_start is None or onset - cluster_start > window_sec:
            cluster_id += 1
            cluster_start = onset
        cluster_for_index[idx] = cluster_id
    return cluster_for_index


def _cluster_extreme_pitches(notes: Sequence[MelodicNote], cluster_for_index: Mapping[int, int]) -> dict[int, dict[str, int]]:
    grouped: dict[int, list[MelodicNote]] = {}
    for idx, note in enumerate(notes):
        grouped.setdefault(int(cluster_for_index[idx]), []).append(note)

    out: dict[int, dict[str, int]] = {}
    for cluster_id, cluster_notes in grouped.items():
        pitches = [int(note.pitch) for note in cluster_notes]
        useful_left_pitches = [pitch for pitch in pitches if 35 <= pitch < 60]
        left_pitches = useful_left_pitches or [pitch for pitch in pitches if pitch < 60]
        out[cluster_id] = {
            "highest": max(pitches),
            "lowest_left": min(left_pitches) if left_pitches else min(pitches),
        }
    return out


def _playable_priority(note: MelodicNote, *, cluster_extremes: Mapping[str, int]) -> float:
    pitch = int(note.pitch)
    duration = max(0.0, float(note.t_off) - float(note.t_on))
    score = 0.0

    if pitch == int(cluster_extremes.get("highest", pitch)):
        # Highest attack-cluster note is the best melody proxy.
        score += 10_000.0
    if pitch == int(cluster_extremes.get("lowest_left", pitch)) and pitch < 60:
        # Keep a left-hand anchor when one exists.
        score += 8_000.0

    if pitch >= 60:
        score += 3_000.0
    elif pitch >= 48:
        score += 1_900.0
    elif pitch >= 35:
        score += 1_450.0
    else:
        score -= 2_500.0

    if pitch > 96:
        score -= 600.0
    if 52 <= pitch <= 84:
        score += 350.0

    score += min(2.5, duration) * 90.0
    score += max(1, min(127, int(note.velocity))) * 3.0
    return score


def _would_exceed_polyphony(
    selected: Sequence[MelodicNote],
    candidate: MelodicNote,
    *,
    max_polyphony: int,
) -> bool:
    if max_polyphony <= 0:
        return True
    if float(candidate.t_off) <= float(candidate.t_on):
        return False
    return _max_polyphony([*selected, candidate]) > max_polyphony


def reduce_piano_polyphony_for_playability(
    notes: Sequence[MelodicNote],
    *,
    max_polyphony: int = PLAYABLE_MAX_POLYPHONY,
    attack_cluster_sec: float = PLAYABLE_ATTACK_CLUSTER_SEC,
    max_notes_per_attack: int = PLAYABLE_MAX_NOTES_PER_ATTACK,
) -> list[MelodicNote]:
    """Reduce dense piano MIDI toward a playable melody + support texture.

    This is an arranging pass, not a claim of transcription truth. It preserves
    high melody candidates and useful left-hand anchors first, then fills
    remaining room with stronger chord tones while enforcing a hard polyphony cap.
    """

    normalized = [
        replace(
            note,
            pitch=max(21, min(108, int(note.pitch))),
            velocity=max(1, min(127, int(note.velocity))),
            t_on=round(max(0.0, float(note.t_on)), 6),
            t_off=round(max(float(note.t_on), float(note.t_off)), 6),
        )
        for note in notes
        if float(note.t_off) > float(note.t_on)
    ]
    normalized.sort(key=lambda item: (item.t_on, item.pitch, item.t_off))
    if len(normalized) <= 1:
        return normalized

    cluster_for_index = _cluster_note_indices_by_onset(normalized, window_sec=attack_cluster_sec)
    extremes_by_cluster = _cluster_extreme_pitches(normalized, cluster_for_index)
    ranked: list[tuple[float, float, int, int, MelodicNote]] = []
    for idx, note in enumerate(normalized):
        cluster_id = int(cluster_for_index[idx])
        score = _playable_priority(note, cluster_extremes=extremes_by_cluster[cluster_id])
        ranked.append((score, -float(note.t_on), int(note.pitch), idx, note))

    selected: list[MelodicNote] = []
    selected_per_cluster: dict[int, int] = {}
    for _score, _neg_time, _pitch, idx, note in sorted(ranked, reverse=True):
        cluster_id = int(cluster_for_index[idx])
        if selected_per_cluster.get(cluster_id, 0) >= max_notes_per_attack:
            continue
        if _would_exceed_polyphony(selected, note, max_polyphony=max_polyphony):
            continue
        selected.append(note)
        selected_per_cluster[cluster_id] = selected_per_cluster.get(cluster_id, 0) + 1

    return sorted(selected, key=lambda item: (item.t_on, item.pitch, item.t_off))


def _diagnostics(notes: Sequence[MelodicNote], *, duration_sec: float | None) -> dict[str, Any]:
    prediction = summarize_piano_predictions(list(notes))
    duration = float(duration_sec or 0.0)
    if duration <= 0.0:
        duration = max((float(note.t_off) for note in notes), default=0.0)
    note_count = len(notes)
    low_count = sum(1 for note in notes if int(note.pitch) < 40)
    left_count = sum(1 for note in notes if int(note.pitch) < 60)
    right_count = sum(1 for note in notes if int(note.pitch) >= 60)
    high_count = sum(1 for note in notes if int(note.pitch) > 96)
    duplicate_rate = float(prediction.get("duplicate_rate", 0.0) or 0.0)
    low_rate = low_count / max(1, note_count)
    high_rate = high_count / max(1, note_count)
    density = note_count / max(0.001, duration / 60.0) if duration > 0.0 else None
    max_polyphony = _max_polyphony(notes)
    risk_flags = {
        "duplicates": duplicate_rate > 0.08,
        "very_dense": bool(density is not None and density > 900.0),
        "low_mud": low_rate > 0.22 and low_count >= 8,
        "high_spray": high_rate > 0.25 and high_count >= 8,
        "playability_polyphony": max_polyphony > PLAYABLE_MAX_POLYPHONY,
        "extreme_polyphony": max_polyphony > 12,
        "single_note_track": note_count > 8 and max_polyphony <= 1,
    }
    return {
        **prediction,
        "duration_sec": round(duration, 6) if duration > 0.0 else None,
        "note_density_per_min": round(density, 3) if density is not None else None,
        "max_polyphony": max_polyphony,
        "playable_polyphony_cap": PLAYABLE_MAX_POLYPHONY,
        "left_hand_notes": left_count,
        "right_hand_notes": right_count,
        "low_register_notes": low_count,
        "high_register_notes": high_count,
        "risk_flags": risk_flags,
    }


def _safe_eval(
    notes: Sequence[MelodicNote],
    reference: Sequence[PianoBenchmarkEvent] | None,
    *,
    tolerance_sec: float,
    offset_tolerance_sec: float,
    velocity_tolerance: int,
) -> dict[str, Any] | None:
    if reference is None:
        return None
    return evaluate_piano(
        list(notes),
        list(reference),
        tolerance_sec=tolerance_sec,
        offset_tolerance_sec=offset_tolerance_sec,
        velocity_tolerance=velocity_tolerance,
    ).to_dict()


def _review_score(result: Mapping[str, Any], *, reference_available: bool) -> float:
    if result.get("error"):
        return -1.0
    diagnostics = result.get("diagnostics", {})
    duplicate_rate = float(diagnostics.get("duplicate_rate", 0.0) or 0.0)
    max_polyphony = int(diagnostics.get("max_polyphony", 0) or 0)
    polyphony_penalty = max(0, max_polyphony - PLAYABLE_MAX_POLYPHONY) * 0.035
    playability_bonus = 0.06 if 1 < max_polyphony <= PLAYABLE_MAX_POLYPHONY else 0.0
    if reference_available:
        reference_eval = result.get("reference_eval") or {}
        return (
            float(reference_eval.get("f1", 0.0) or 0.0)
            + (0.25 * float(reference_eval.get("note_with_offset_f1", 0.0) or 0.0))
            - (0.20 * duplicate_rate)
            - polyphony_penalty
            + playability_bonus
        )
    if result.get("method") == "source_midi_clean_playable":
        return 1.35
    if result.get("method") == "source_midi_clean":
        return 1.2
    source_eval = result.get("source_eval") or {}
    return (
        float(source_eval.get("f1", 0.0) or 0.0)
        + (0.10 * float(source_eval.get("note_with_offset_f1", 0.0) or 0.0))
        - (0.25 * duplicate_rate)
        - polyphony_penalty
        + playability_bonus
    )


def _candidate_notes(
    method: str,
    *,
    audio_path: Path,
    source_notes: Sequence[MelodicNote],
    registry: Mapping[str, Any],
) -> tuple[list[MelodicNote], str]:
    if method.endswith("_playable"):
        base_method = method[: -len("_playable")]
        base_notes, base_kind = _candidate_notes(
            base_method,
            audio_path=audio_path,
            source_notes=source_notes,
            registry=registry,
        )
        return (
            reduce_piano_polyphony_for_playability(base_notes),
            f"{base_kind} + playable polyphony reduction",
        )
    if method == "source_midi":
        return list(source_notes), "decoded source MIDI baseline"
    if method == "source_midi_clean":
        return piano_cleanup.cleanup_notes(list(source_notes), stem_path=audio_path, instrument="keys"), "source MIDI cleanup"
    fn = registry.get(method)
    if fn is None:
        raise RuntimeError(f"piano refinement method '{method}' is unavailable")
    return list(fn(audio_path)), "audio-derived transcription candidate"


def run_piano_refinement_workbench(
    *,
    audio_path: Path | str,
    source_midi_path: Path | str,
    reference_midi_path: Path | str | None = None,
    methods: Sequence[str] | str | None = None,
    output_root: Path | str = DEFAULT_OUTPUT_ROOT,
    label: str = "piano-refinement",
    tolerance_ms: float = 60.0,
    offset_tolerance_ms: float = 120.0,
    velocity_tolerance: int = 20,
    source_offset_sec: float = 0.0,
    reference_offset_sec: float = 0.0,
    bpm: float = 120.0,
) -> Path:
    audio = Path(audio_path)
    source_midi = Path(source_midi_path)
    reference_midi = Path(reference_midi_path) if reference_midi_path else None
    if not audio.is_file():
        raise FileNotFoundError(f"audio file not found: {audio}")
    if not source_midi.is_file():
        raise FileNotFoundError(f"source MIDI file not found: {source_midi}")
    if reference_midi is not None and not reference_midi.is_file():
        raise FileNotFoundError(f"reference MIDI file not found: {reference_midi}")

    method_ids = parse_refinement_methods(methods)
    tolerance_sec = float(tolerance_ms) / 1000.0
    offset_tolerance_sec = float(offset_tolerance_ms) / 1000.0
    root = Path(output_root)
    run_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{_slugify(label)}"
    out_dir = root / run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    source_notes = _offset_notes(decode_midi_notes(source_midi, instrument="keys"), source_offset_sec)
    source_reference = _notes_to_reference_events(source_notes)
    truth_reference = (
        parse_piano_midi_reference(reference_midi, offset_sec=float(reference_offset_sec), role="keys")
        if reference_midi is not None
        else None
    )
    audio_duration = estimate_duration_sec(audio)
    duration_sec = max(audio_duration or 0.0, max((note.t_off for note in source_notes), default=0.0))
    if truth_reference:
        duration_sec = max(duration_sec, max((event.time + event.duration for event in truth_reference), default=0.0))

    registry = build_default_melodic_algorithm_registry(instrument="keys")
    candidates: list[dict[str, Any]] = []
    for method in method_ids:
        t0 = time.time()
        try:
            notes, candidate_kind = _candidate_notes(
                method,
                audio_path=audio,
                source_notes=source_notes,
                registry=registry,
            )
            notes = sorted(notes, key=lambda item: (item.t_on, item.pitch, item.t_off))
            elapsed = time.time() - t0
            source_eval = _safe_eval(
                notes,
                source_reference,
                tolerance_sec=tolerance_sec,
                offset_tolerance_sec=offset_tolerance_sec,
                velocity_tolerance=velocity_tolerance,
            )
            reference_eval = _safe_eval(
                notes,
                truth_reference,
                tolerance_sec=tolerance_sec,
                offset_tolerance_sec=offset_tolerance_sec,
                velocity_tolerance=velocity_tolerance,
            )
            candidates.append(
                {
                    "method": method,
                    "candidate_kind": candidate_kind,
                    "elapsed_sec": round(elapsed, 3),
                    "note_count": len(notes),
                    "diagnostics": _diagnostics(notes, duration_sec=duration_sec),
                    "source_eval": source_eval,
                    "reference_eval": reference_eval,
                    "notes": melodic_notes_to_dicts(notes),
                }
            )
        except Exception as exc:
            candidates.append(
                {
                    "method": method,
                    "candidate_kind": "unavailable",
                    "elapsed_sec": round(time.time() - t0, 3),
                    "note_count": 0,
                    "error": str(exc),
                    "diagnostics": _diagnostics([], duration_sec=duration_sec),
                    "source_eval": None,
                    "reference_eval": None,
                    "notes": [],
                }
            )

    reference_available = truth_reference is not None
    for candidate in candidates:
        candidate["review_score"] = round(_review_score(candidate, reference_available=reference_available), 6)

    successful = [candidate for candidate in candidates if not candidate.get("error")]
    recommended = max(successful, key=lambda item: float(item.get("review_score", -1.0))) if successful else None
    recommendation = {
        "method": recommended.get("method") if recommended else None,
        "basis": "reference_f1" if reference_available else "conservative_source_cleanup_or_source_agreement",
        "review_score": recommended.get("review_score") if recommended else None,
        "requires_human_review": True,
    }

    payload: dict[str, Any] = {
        "feature": "piano_refinement_workbench",
        "generated_at": datetime.now().isoformat(),
        "inputs": {
            "audio_path": str(audio),
            "source_midi_path": str(source_midi),
            "reference_midi_path": str(reference_midi) if reference_midi is not None else None,
            "source_offset_sec": float(source_offset_sec),
            "reference_offset_sec": float(reference_offset_sec),
        },
        "settings": {
            "methods": method_ids,
            "tolerance_ms": float(tolerance_ms),
            "offset_tolerance_ms": float(offset_tolerance_ms),
            "velocity_tolerance": int(velocity_tolerance),
            "bpm": float(bpm),
        },
        "reference_available": reference_available,
        "duration_sec": round(duration_sec, 6) if duration_sec > 0.0 else None,
        "source_notes": melodic_notes_to_dicts(source_notes),
        "reference_notes": _events_to_note_dicts(truth_reference or []),
        "source_diagnostics": _diagnostics(source_notes, duration_sec=duration_sec),
        "recommendation": recommendation,
        "candidates": candidates,
    }
    _write_refinement_outputs(out_dir, payload, bpm=bpm)

    missing = [name for name in REQUIRED_REFINEMENT_ARTIFACTS if not (out_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"piano refinement run incomplete: missing {', '.join(missing)}")

    latest_marker = root.parent / "LATEST_REFINEMENT_RUN.txt"
    latest_marker.parent.mkdir(parents=True, exist_ok=True)
    latest_marker.write_text(str(out_dir), encoding="utf-8")
    return out_dir


def _write_refinement_outputs(out_dir: Path, payload: dict[str, Any], *, bpm: float) -> None:
    candidate_dir = out_dir / "candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    index: list[dict[str, Any]] = []
    for candidate in payload.get("candidates", []):
        method = str(candidate.get("method") or "unknown")
        method_slug = _slugify(method)
        notes = candidate.get("notes") or []
        notes_path = candidate_dir / f"{method_slug}.notes.json"
        notes_path.write_text(json.dumps(notes, indent=2), encoding="utf-8")
        artifact_entry: dict[str, Any] = {
            "method": method,
            "notes": str(notes_path.relative_to(out_dir)),
            "note_count": candidate.get("note_count", 0),
            "error": candidate.get("error"),
        }
        if notes and not candidate.get("error"):
            midi_path = candidate_dir / f"{method_slug}.mid"
            write_melodic_notes_midi(notes, midi_path, bpm=bpm)
            artifact_entry["midi"] = str(midi_path.relative_to(out_dir))
            candidate["midi_path"] = artifact_entry["midi"]
        candidate["notes_path"] = artifact_entry["notes"]
        index.append(artifact_entry)
    (candidate_dir / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (out_dir / "report.md").write_text(render_refinement_report_markdown(payload), encoding="utf-8")
    (out_dir / "refinement_dashboard.html").write_text(render_refinement_dashboard_html(payload), encoding="utf-8")
    _write_playability_visual_reports(out_dir, payload)


def render_refinement_report_markdown(payload: Mapping[str, Any]) -> str:
    recommendation = payload.get("recommendation", {})
    lines = ["# Piano MIDI Refinement Report", ""]
    lines.append(f"Generated: {payload.get('generated_at')}")
    lines.append(f"Audio: `{payload.get('inputs', {}).get('audio_path')}`")
    lines.append(f"Source MIDI: `{payload.get('inputs', {}).get('source_midi_path')}`")
    reference_path = payload.get("inputs", {}).get("reference_midi_path")
    lines.append(f"Reference MIDI: `{reference_path}`" if reference_path else "Reference MIDI: none")
    lines.append("")
    lines.append("## Recommendation")
    lines.append("")
    method = recommendation.get("method") or "none"
    lines.append(f"- Candidate: `{method}`")
    lines.append(f"- Basis: `{recommendation.get('basis')}`")
    lines.append("- Status: review required before using this as a learning/gameplay chart")
    lines.append("")
    lines.append("## Candidates")
    lines.append("")
    if payload.get("reference_available"):
        lines.append("| Method | Ref F1 | Offset F1 | Source F1 | Notes | Dup | Poly | Risks | Artifact |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---|---|")
    else:
        lines.append("| Method | Source F1 | Notes | Dup | Poly | Risks | Artifact |")
        lines.append("|---|---:|---:|---:|---:|---|---|")
    for candidate in payload.get("candidates", []):
        diagnostics = candidate.get("diagnostics", {})
        risks = ", ".join(k for k, v in diagnostics.get("risk_flags", {}).items() if v) or "-"
        source_eval = candidate.get("source_eval") or {}
        reference_eval = candidate.get("reference_eval") or {}
        artifact = candidate.get("midi_path") or candidate.get("error") or "-"
        if payload.get("reference_available"):
            lines.append(
                f"| `{candidate.get('method')}` | {float(reference_eval.get('f1', 0.0) or 0.0):.3f} "
                f"| {float(reference_eval.get('note_with_offset_f1', 0.0) or 0.0):.3f} "
                f"| {float(source_eval.get('f1', 0.0) or 0.0):.3f} "
                f"| {candidate.get('note_count', 0)} | {float(diagnostics.get('duplicate_rate', 0.0) or 0.0):.3f} "
                f"| {diagnostics.get('max_polyphony', 0)} | {risks} | `{artifact}` |"
            )
        else:
            lines.append(
                f"| `{candidate.get('method')}` | {float(source_eval.get('f1', 0.0) or 0.0):.3f} "
                f"| {candidate.get('note_count', 0)} | {float(diagnostics.get('duplicate_rate', 0.0) or 0.0):.3f} "
                f"| {diagnostics.get('max_polyphony', 0)} | {risks} | `{artifact}` |"
            )
    lines.append("")
    lines.append("## Artifacts")
    lines.append("")
    lines.append("- `summary.json`: full run data")
    lines.append("- `refinement_dashboard.html`: static before/after review UI")
    lines.append("- `playability_report.html`: focused before/after playability report")
    lines.append("- `playability_*.svg`: static visual summaries for polyphony, metrics, and piano-roll diff")
    lines.append("- `playability_audition_*.wav`: synthesized before/after focused-section audio previews")
    lines.append("- `candidates/*.mid`: normalized MIDI outputs")
    lines.append("- `candidates/*.notes.json`: decoded candidate note lists")
    return "\n".join(lines) + "\n"


def _xml_escape(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _candidate_by_method(payload: Mapping[str, Any], method: str) -> Mapping[str, Any] | None:
    return next(
        (candidate for candidate in payload.get("candidates", []) if candidate.get("method") == method),
        None,
    )


def _select_playability_pair(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    before = _candidate_by_method(payload, "source_midi_clean") or _candidate_by_method(payload, "source_midi")
    after = _candidate_by_method(payload, "source_midi_clean_playable")
    if after is None:
        recommendation = payload.get("recommendation", {})
        rec_method = recommendation.get("method")
        if rec_method:
            after = _candidate_by_method(payload, str(rec_method))
    return before, after


def _note_start(note: Mapping[str, Any]) -> float:
    try:
        return float(note.get("t_on", 0.0) or 0.0)
    except Exception:
        return 0.0


def _note_end(note: Mapping[str, Any]) -> float:
    try:
        return max(_note_start(note), float(note.get("t_off", _note_start(note)) or _note_start(note)))
    except Exception:
        return _note_start(note)


def _note_pitch(note: Mapping[str, Any]) -> int:
    try:
        return int(note.get("pitch", 60) or 60)
    except Exception:
        return 60


def _note_velocity(note: Mapping[str, Any]) -> int:
    try:
        return max(1, min(127, int(note.get("velocity", 80) or 80)))
    except Exception:
        return 80


def _playability_duration(
    payload: Mapping[str, Any],
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> float:
    duration = float(payload.get("duration_sec", 0.0) or 0.0)
    if duration > 0.0:
        return duration
    before_notes = list(before.get("notes", []) or []) if before else []
    after_notes = list(after.get("notes", []) or []) if after else []
    return max(
        *(_note_end(note) for note in before_notes),
        *(_note_end(note) for note in after_notes),
        1.0,
    )


def _polyphony_points(
    notes: Sequence[Mapping[str, Any]],
    *,
    duration_sec: float,
    bucket_count: int = 160,
) -> list[dict[str, float | int]]:
    duration = max(0.001, float(duration_sec or 0.0))
    count = max(2, int(bucket_count))
    points: list[dict[str, float | int]] = []
    for idx in range(count):
        t = (idx / (count - 1)) * duration
        active = sum(1 for note in notes if _note_start(note) <= t < _note_end(note))
        points.append({"time": round(t, 6), "active": active})
    return points


def _note_window_for_roll(before: Mapping[str, Any], after: Mapping[str, Any], duration_sec: float) -> tuple[float, float]:
    before_notes = list(before.get("notes", []) or [])
    points = _polyphony_points(before_notes, duration_sec=duration_sec, bucket_count=180)
    peak = max(points, key=lambda item: int(item["active"])) if points else {"time": 0.0}
    center = float(peak.get("time", 0.0) or 0.0)
    window = 16.0
    start = max(0.0, min(max(0.0, duration_sec - window), center - (window * 0.35)))
    return start, min(duration_sec, start + window)


def _synthesize_piano_preview_samples(
    notes: Sequence[Mapping[str, Any]],
    *,
    start_sec: float,
    end_sec: float,
    sample_rate: int = AUDITION_SAMPLE_RATE,
) -> list[float]:
    duration = max(0.1, float(end_sec) - float(start_sec))
    sample_count = max(1, int(math.ceil(duration * sample_rate)))
    samples = [0.0] * sample_count
    two_pi = math.tau

    for note in notes:
        note_start = _note_start(note)
        note_end = _note_end(note)
        if note_end <= start_sec or note_start >= end_sec:
            continue
        pitch = max(21, min(108, _note_pitch(note)))
        freq = 440.0 * (2.0 ** ((pitch - 69) / 12.0))
        amp = 0.055 * (_note_velocity(note) / 127.0)
        start_idx = max(0, int((max(note_start, start_sec) - start_sec) * sample_rate))
        end_idx = min(sample_count, int(math.ceil((min(note_end, end_sec) - start_sec) * sample_rate)))
        if end_idx <= start_idx:
            continue
        for idx in range(start_idx, end_idx):
            absolute_t = start_sec + (idx / sample_rate)
            age = max(0.0, absolute_t - note_start)
            remaining = max(0.0, note_end - absolute_t)
            attack = min(1.0, age / 0.012)
            release = min(1.0, remaining / 0.085)
            decay = 0.52 + (0.48 * math.exp(-age * 1.9))
            env = attack * release * decay
            phase = two_pi * freq * age
            tone = math.sin(phase) + (0.28 * math.sin(phase * 2.0)) + (0.10 * math.sin(phase * 3.0))
            samples[idx] += amp * env * tone
    return samples


def _write_pcm16_wav(path: Path, samples: Sequence[float], *, sample_rate: int, normalization_peak: float) -> None:
    peak = max(1e-9, float(normalization_peak or 0.0))
    scale = min(1.0, 0.92 / peak)
    pcm = array("h")
    for sample in samples:
        value = max(-1.0, min(1.0, float(sample) * scale))
        pcm.append(int(round(value * 32767.0)))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def _write_silence_wav(path: Path, *, duration_sec: float = 0.25, sample_rate: int = AUDITION_SAMPLE_RATE) -> None:
    sample_count = max(1, int(duration_sec * sample_rate))
    _write_pcm16_wav(path, [0.0] * sample_count, sample_rate=sample_rate, normalization_peak=1.0)


def _write_playability_audition_audio(
    out_dir: Path,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    duration_sec: float,
) -> tuple[float, float]:
    start_sec, end_sec = _note_window_for_roll(before, after, duration_sec)
    before_samples = _synthesize_piano_preview_samples(
        list(before.get("notes", []) or []),
        start_sec=start_sec,
        end_sec=end_sec,
    )
    after_samples = _synthesize_piano_preview_samples(
        list(after.get("notes", []) or []),
        start_sec=start_sec,
        end_sec=end_sec,
    )
    gap = [0.0] * int(AUDITION_GAP_SEC * AUDITION_SAMPLE_RATE)
    ab_samples = [*before_samples, *gap, *after_samples]
    peak = max(
        *(abs(sample) for sample in before_samples),
        *(abs(sample) for sample in after_samples),
        1e-9,
    )
    _write_pcm16_wav(
        out_dir / "playability_audition_before.wav",
        before_samples,
        sample_rate=AUDITION_SAMPLE_RATE,
        normalization_peak=peak,
    )
    _write_pcm16_wav(
        out_dir / "playability_audition_after.wav",
        after_samples,
        sample_rate=AUDITION_SAMPLE_RATE,
        normalization_peak=peak,
    )
    _write_pcm16_wav(
        out_dir / "playability_audition_ab.wav",
        ab_samples,
        sample_rate=AUDITION_SAMPLE_RATE,
        normalization_peak=peak,
    )
    return start_sec, end_sec


def _render_playability_metrics_svg(before: Mapping[str, Any], after: Mapping[str, Any]) -> str:
    metrics = [
        ("Max polyphony", "max_polyphony", "count"),
        ("Notes", "note_count", "count"),
        ("Duplicate rate", "duplicate_rate", "ratio"),
        ("Source F1", "f1", "source_ratio"),
        ("Source offset F1", "note_with_offset_f1", "source_ratio"),
        ("Left-hand notes", "left_hand_notes", "count"),
        ("Right-hand notes", "right_hand_notes", "count"),
    ]
    before_diag = before.get("diagnostics", {})
    after_diag = after.get("diagnostics", {})
    before_source = before.get("source_eval", {}) or {}
    after_source = after.get("source_eval", {}) or {}

    width = 980
    height = 456
    left = 210
    bar_w = 285
    row_h = 48
    top = 76
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#09111d"/>',
        '<text x="26" y="36" fill="#f3f7ff" font-family="Segoe UI, sans-serif" font-size="22" font-weight="700">Piano Playability Before / After</text>',
        f'<text x="{left}" y="62" fill="#9fb1c7" font-family="Segoe UI, sans-serif" font-size="12">Before: {_xml_escape(before.get("method"))}</text>',
        f'<text x="{left + bar_w + 44}" y="62" fill="#9fb1c7" font-family="Segoe UI, sans-serif" font-size="12">After: {_xml_escape(after.get("method"))}</text>',
    ]
    for row_idx, (label, key, kind) in enumerate(metrics):
        y = top + (row_idx * row_h)
        if kind == "source_ratio":
            before_value = float(before_source.get(key, 0.0) or 0.0)
            after_value = float(after_source.get(key, 0.0) or 0.0)
        else:
            before_value = float(before.get("note_count", 0) if key == "note_count" else before_diag.get(key, 0.0) or 0.0)
            after_value = float(after.get("note_count", 0) if key == "note_count" else after_diag.get(key, 0.0) or 0.0)
        max_value = max(1.0, before_value, after_value)
        if kind in {"ratio", "source_ratio"}:
            before_text = f"{before_value:.3f}"
            after_text = f"{after_value:.3f}"
        else:
            before_text = f"{before_value:.0f}"
            after_text = f"{after_value:.0f}"
        lines.append(
            f'<text x="26" y="{y + 20}" fill="#dce7f6" font-family="Segoe UI, sans-serif" font-size="14">{_xml_escape(label)}</text>'
        )
        lines.append(
            f'<rect x="{left}" y="{y}" width="{bar_w}" height="24" rx="6" fill="#16243a"/>'
        )
        lines.append(
            f'<rect x="{left}" y="{y}" width="{max(2.0, (before_value / max_value) * bar_w):.2f}" height="24" rx="6" fill="#58a6ff"/>'
        )
        lines.append(
            f'<text x="{left + 10}" y="{y + 17}" fill="#ffffff" font-family="Consolas, monospace" font-size="12">{before_text}</text>'
        )
        x2 = left + bar_w + 44
        lines.append(
            f'<rect x="{x2}" y="{y}" width="{bar_w}" height="24" rx="6" fill="#16243a"/>'
        )
        lines.append(
            f'<rect x="{x2}" y="{y}" width="{max(2.0, (after_value / max_value) * bar_w):.2f}" height="24" rx="6" fill="#69e6a3"/>'
        )
        lines.append(
            f'<text x="{x2 + 10}" y="{y + 17}" fill="#062017" font-family="Consolas, monospace" font-size="12">{after_text}</text>'
        )
    lines.append(
        f'<line x1="{left}" y1="{top + (len(metrics) * row_h) + 10}" x2="{left + (bar_w * 2) + 44}" y2="{top + (len(metrics) * row_h) + 10}" stroke="#273b5c"/>'
    )
    lines.append(
        f'<text x="{left}" y="{height - 24}" fill="#9fb1c7" font-family="Segoe UI, sans-serif" font-size="12">Playability cap: {PLAYABLE_MAX_POLYPHONY} simultaneous notes. Lower polyphony and duplicate rate are better; note-count reduction is useful only when melody/support are preserved.</text>'
    )
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def _render_playability_polyphony_svg(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    duration_sec: float,
) -> str:
    before_points = _polyphony_points(list(before.get("notes", []) or []), duration_sec=duration_sec)
    after_points = _polyphony_points(list(after.get("notes", []) or []), duration_sec=duration_sec)
    width = 1100
    height = 380
    left = 56
    top = 52
    plot_w = width - 96
    plot_h = height - 116
    max_active = max(
        PLAYABLE_MAX_POLYPHONY,
        *(int(point["active"]) for point in before_points),
        *(int(point["active"]) for point in after_points),
        1,
    )

    def xy(point: Mapping[str, float | int]) -> tuple[float, float]:
        x = left + (float(point["time"]) / max(0.001, duration_sec)) * plot_w
        y = top + plot_h - (int(point["active"]) / max_active) * plot_h
        return x, y

    before_path = " ".join(
        f'{"M" if idx == 0 else "L"} {xy(point)[0]:.2f} {xy(point)[1]:.2f}'
        for idx, point in enumerate(before_points)
    )
    after_path = " ".join(
        f'{"M" if idx == 0 else "L"} {xy(point)[0]:.2f} {xy(point)[1]:.2f}'
        for idx, point in enumerate(after_points)
    )
    cap_y = top + plot_h - (PLAYABLE_MAX_POLYPHONY / max_active) * plot_h
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#09111d"/>',
        '<text x="26" y="34" fill="#f3f7ff" font-family="Segoe UI, sans-serif" font-size="22" font-weight="700">Polyphony Over Time</text>',
        f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="#0d1929" stroke="#273b5c"/>',
        f'<line x1="{left}" y1="{cap_y:.2f}" x2="{left + plot_w}" y2="{cap_y:.2f}" stroke="#f4c95d" stroke-dasharray="8 6"/>',
        f'<text x="{left + 8}" y="{cap_y - 7:.2f}" fill="#f4c95d" font-family="Segoe UI, sans-serif" font-size="12">playable cap {PLAYABLE_MAX_POLYPHONY}</text>',
    ]
    for tick in range(0, max_active + 1, max(1, max_active // 6)):
        y = top + plot_h - (tick / max_active) * plot_h
        lines.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}" stroke="#17263a"/>')
        lines.append(
            f'<text x="24" y="{y + 4:.2f}" fill="#9fb1c7" font-family="Consolas, monospace" font-size="11">{tick}</text>'
        )
    lines.append(f'<path d="{before_path}" fill="none" stroke="#58a6ff" stroke-width="2.2"/>')
    lines.append(f'<path d="{after_path}" fill="none" stroke="#69e6a3" stroke-width="2.2"/>')
    lines.append(
        f'<text x="{left}" y="{height - 32}" fill="#58a6ff" font-family="Segoe UI, sans-serif" font-size="13">Before: {_xml_escape(before.get("method"))}</text>'
    )
    lines.append(
        f'<text x="{left + 250}" y="{height - 32}" fill="#69e6a3" font-family="Segoe UI, sans-serif" font-size="13">After: {_xml_escape(after.get("method"))}</text>'
    )
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def _render_playability_roll_svg(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    duration_sec: float,
) -> str:
    start_sec, end_sec = _note_window_for_roll(before, after, duration_sec)
    window = max(0.001, end_sec - start_sec)
    width = 1180
    height = 720
    left = 64
    top = 62
    plot_w = width - 100
    row_h = 6.8
    min_pitch = 28
    max_pitch = 96
    plot_h = (max_pitch - min_pitch + 1) * row_h

    def rects(notes: Sequence[Mapping[str, Any]], *, color: str, y_offset: float, opacity: float) -> list[str]:
        out: list[str] = []
        for note in notes:
            note_start = _note_start(note)
            note_end = _note_end(note)
            if note_end <= start_sec or note_start >= end_sec:
                continue
            pitch = _note_pitch(note)
            if pitch < min_pitch or pitch > max_pitch:
                continue
            x = left + ((max(note_start, start_sec) - start_sec) / window) * plot_w
            x2 = left + ((min(note_end, end_sec) - start_sec) / window) * plot_w
            y = top + (max_pitch - pitch) * row_h + y_offset
            out.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{max(2.0, x2 - x):.2f}" height="4.6" rx="2" fill="{color}" opacity="{opacity}">'
                f'<title>MIDI {pitch} {note_start:.2f}-{note_end:.2f}s</title></rect>'
            )
        return out

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#09111d"/>',
        f'<text x="26" y="34" fill="#f3f7ff" font-family="Segoe UI, sans-serif" font-size="22" font-weight="700">Focused Piano-Roll Before / After ({start_sec:.1f}s-{end_sec:.1f}s)</text>',
        f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="#0d1929" stroke="#273b5c"/>',
    ]
    for pitch in range(36, max_pitch + 1, 12):
        y = top + (max_pitch - pitch) * row_h
        lines.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}" stroke="#1b2d46"/>')
        lines.append(
            f'<text x="14" y="{y + 4:.2f}" fill="#9fb1c7" font-family="Consolas, monospace" font-size="10">{pitch}</text>'
        )
    for tick in range(int(start_sec), int(end_sec) + 1):
        x = left + ((tick - start_sec) / window) * plot_w
        lines.append(f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{top + plot_h}" stroke="#17263a"/>')
        lines.append(
            f'<text x="{x + 3:.2f}" y="{top + plot_h + 18}" fill="#9fb1c7" font-family="Consolas, monospace" font-size="10">{tick}s</text>'
        )
    lines.extend(rects(list(before.get("notes", []) or []), color="#58a6ff", y_offset=-1.8, opacity=0.45))
    lines.extend(rects(list(after.get("notes", []) or []), color="#69e6a3", y_offset=2.0, opacity=0.82))
    lines.append(
        f'<text x="{left}" y="{height - 34}" fill="#58a6ff" font-family="Segoe UI, sans-serif" font-size="13">Before: {_xml_escape(before.get("method"))}</text>'
    )
    lines.append(
        f'<text x="{left + 250}" y="{height - 34}" fill="#69e6a3" font-family="Segoe UI, sans-serif" font-size="13">After: {_xml_escape(after.get("method"))}</text>'
    )
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def render_playability_report_html(payload: Mapping[str, Any]) -> str:
    before, after = _select_playability_pair(payload)
    before_method = before.get("method") if before else "none"
    after_method = after.get("method") if after else "none"
    before_diag = before.get("diagnostics", {}) if before else {}
    after_diag = after.get("diagnostics", {}) if after else {}
    before_source = before.get("source_eval", {}) if before else {}
    after_source = after.get("source_eval", {}) if after else {}
    duration = _playability_duration(payload, before, after)
    start_sec, end_sec = _note_window_for_roll(before, after, duration) if before and after else (0.0, 0.0)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Piano Playability Visual Report</title>
<style>
body {{ margin: 0; padding: 28px; background: #07111d; color: #eef6ff; font-family: "Aptos", "Segoe UI", sans-serif; }}
h1 {{ margin: 0 0 6px; font-size: 42px; letter-spacing: -0.04em; }}
h2 {{ margin: 28px 0 10px; }}
.muted {{ color: #9fb1c7; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 18px 0; }}
.card {{ border: 1px solid #273b5c; border-radius: 16px; background: #101c2d; padding: 14px; }}
.card strong {{ display: block; font-size: 28px; }}
.audio-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; max-width: 1180px; }}
.audio-card {{ border: 1px solid #273b5c; border-radius: 16px; background: #101c2d; padding: 14px; }}
.audio-card strong {{ display: block; margin-bottom: 8px; }}
audio {{ width: 100%; }}
img {{ width: 100%; max-width: 1180px; display: block; border: 1px solid #273b5c; border-radius: 14px; background: #09111d; }}
code {{ color: #69e6a3; }}
</style>
</head>
<body>
<h1>Piano Playability Visual Report</h1>
<p class="muted">Before: <code>{_xml_escape(before_method)}</code> / After: <code>{_xml_escape(after_method)}</code>. This report focuses on practical playability, not transcription truth.</p>
<div class="cards">
  <div class="card"><strong>{_xml_escape(before_diag.get("max_polyphony", "n/a"))} -&gt; {_xml_escape(after_diag.get("max_polyphony", "n/a"))}</strong><span class="muted">max polyphony</span></div>
  <div class="card"><strong>{_xml_escape((before or {}).get("note_count", "n/a"))} -&gt; {_xml_escape((after or {}).get("note_count", "n/a"))}</strong><span class="muted">notes</span></div>
  <div class="card"><strong>{float(before_diag.get("duplicate_rate", 0.0) or 0.0):.3f} -&gt; {float(after_diag.get("duplicate_rate", 0.0) or 0.0):.3f}</strong><span class="muted">duplicate rate</span></div>
  <div class="card"><strong>{float(before_source.get("f1", 0.0) or 0.0):.3f} -&gt; {float(after_source.get("f1", 0.0) or 0.0):.3f}</strong><span class="muted">source F1</span></div>
  <div class="card"><strong>{PLAYABLE_MAX_POLYPHONY}</strong><span class="muted">playable cap</span></div>
</div>
<h2>Audition Clips</h2>
<p class="muted">Generated-piano previews for the focused section from {start_sec:.1f}s to {end_sec:.1f}s. These are synthesized from MIDI notes for A/B review; they are not the original audio recording.</p>
<div class="audio-grid">
  <div class="audio-card"><strong>Before section</strong><audio controls preload="none" src="playability_audition_before.wav"></audio></div>
  <div class="audio-card"><strong>After section</strong><audio controls preload="none" src="playability_audition_after.wav"></audio></div>
  <div class="audio-card"><strong>A/B section: before, then after</strong><audio controls preload="none" src="playability_audition_ab.wav"></audio></div>
</div>
<h2>Metric Summary</h2>
<img src="playability_metrics.svg" alt="Before and after playability metrics">
<h2>Polyphony Timeline</h2>
<img src="playability_polyphony.svg" alt="Before and after polyphony timeline">
<h2>Focused Piano-Roll Diff</h2>
<img src="playability_roll.svg" alt="Focused before and after piano roll">
</body>
</html>
"""


def _write_playability_visual_reports(out_dir: Path, payload: Mapping[str, Any]) -> None:
    before, after = _select_playability_pair(payload)
    if before is None or after is None:
        empty = '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="120"><rect width="100%" height="100%" fill="#09111d"/><text x="24" y="64" fill="#f3f7ff" font-family="Segoe UI, sans-serif" font-size="18">No before/after playability pair available.</text></svg>\n'
        (out_dir / "playability_metrics.svg").write_text(empty, encoding="utf-8")
        (out_dir / "playability_polyphony.svg").write_text(empty, encoding="utf-8")
        (out_dir / "playability_roll.svg").write_text(empty, encoding="utf-8")
        _write_silence_wav(out_dir / "playability_audition_before.wav")
        _write_silence_wav(out_dir / "playability_audition_after.wav")
        _write_silence_wav(out_dir / "playability_audition_ab.wav")
        (out_dir / "playability_report.html").write_text(render_playability_report_html(payload), encoding="utf-8")
        return
    duration = _playability_duration(payload, before, after)
    (out_dir / "playability_metrics.svg").write_text(_render_playability_metrics_svg(before, after), encoding="utf-8")
    (out_dir / "playability_polyphony.svg").write_text(
        _render_playability_polyphony_svg(before, after, duration_sec=duration),
        encoding="utf-8",
    )
    (out_dir / "playability_roll.svg").write_text(
        _render_playability_roll_svg(before, after, duration_sec=duration),
        encoding="utf-8",
    )
    _write_playability_audition_audio(out_dir, before, after, duration_sec=duration)
    (out_dir / "playability_report.html").write_text(render_playability_report_html(payload), encoding="utf-8")


def render_refinement_dashboard_html(payload: Mapping[str, Any]) -> str:
    data_json = (
        json.dumps(payload, default=str, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Piano MIDI Refinement Workbench</title>
<style>
:root {{
  color-scheme: dark;
  --bg: #07111d;
  --panel: #111c2c;
  --line: #273b5c;
  --text: #f0f6ff;
  --muted: #9db0ca;
  --source: #58a6ff;
  --candidate: #69e6a3;
  --reference: #f4c95d;
  --bad: #ff6b7d;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: radial-gradient(circle at top left, rgba(105,230,163,.14), transparent 32rem), var(--bg);
  color: var(--text);
  font-family: "Aptos", "Segoe UI", sans-serif;
}}
header {{ padding: 28px 34px 18px; border-bottom: 1px solid var(--line); }}
h1 {{ margin: 0 0 8px; font-size: clamp(28px, 4vw, 46px); letter-spacing: -0.04em; }}
h2 {{ margin: 0 0 12px; font-size: 18px; }}
h3 {{ margin: 18px 0 8px; color: var(--muted); font-size: 13px; letter-spacing: .08em; text-transform: uppercase; }}
.muted {{ color: var(--muted); }}
main {{ display: grid; grid-template-columns: minmax(420px, .9fr) minmax(520px, 1.1fr); gap: 18px; padding: 22px 34px 36px; }}
section {{ background: rgba(17,28,44,.9); border: 1px solid var(--line); border-radius: 18px; padding: 18px; }}
.cards {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 14px 0; }}
.card {{ background: rgba(255,255,255,.04); border: 1px solid var(--line); border-radius: 14px; padding: 12px; }}
.card strong {{ display: block; font-size: 24px; }}
.pill {{ display: inline-flex; border: 1px solid var(--line); border-radius: 999px; padding: 5px 9px; margin: 3px; color: var(--muted); }}
select {{ width: 100%; border: 1px solid var(--line); border-radius: 12px; padding: 10px; background: #081322; color: var(--text); font: inherit; }}
.table-wrap {{ overflow: auto; border: 1px solid var(--line); border-radius: 14px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th, td {{ padding: 9px 10px; border-bottom: 1px solid rgba(255,255,255,.08); text-align: left; white-space: nowrap; }}
th {{ color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .07em; }}
tr[data-method] {{ cursor: pointer; }}
tr[data-method]:hover, tr.selected {{ background: rgba(105,230,163,.1); }}
.risk {{ color: var(--bad); }}
.ok {{ color: var(--candidate); }}
.roll-wrap {{ overflow: auto; border: 1px solid var(--line); border-radius: 14px; background: #060d17; }}
svg {{ display: block; min-width: 920px; }}
.legend {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 8px 0 14px; }}
.swatch {{ width: 13px; height: 13px; border-radius: 3px; display: inline-block; margin-right: 5px; vertical-align: -2px; }}
@media (max-width: 1100px) {{ main {{ grid-template-columns: 1fr; padding: 18px; }} header {{ padding: 22px 18px 14px; }} }}
</style>
</head>
<body>
<header>
  <h1>Piano MIDI Refinement Workbench</h1>
  <div id="meta"></div>
</header>
<main>
  <section>
    <h2>Candidate Review</h2>
    <div id="recommendation"></div>
    <div class="cards" id="cards"></div>
    <h3>Candidates</h3>
    <div class="table-wrap"><table>
      <thead><tr><th>Method</th><th>Ref F1</th><th>Source F1</th><th>Notes</th><th>Dup</th><th>Poly</th><th>Risk</th></tr></thead>
      <tbody id="candidate-rows"></tbody>
    </table></div>
    <h3>Selected Candidate</h3>
    <select id="candidate-select"></select>
    <div id="detail"></div>
  </section>
  <section>
    <h2>Before / After Piano Roll</h2>
    <div class="legend">
      <span><span class="swatch" style="background:var(--source)"></span>Source MIDI</span>
      <span><span class="swatch" style="background:var(--candidate)"></span>Candidate</span>
      <span><span class="swatch" style="background:var(--reference)"></span>Reference</span>
    </div>
    <div class="roll-wrap" id="roll"></div>
  </section>
</main>
<script id="payload" type="application/json">{data_json}</script>
<script>
const payload = JSON.parse(document.getElementById("payload").textContent);
let selected = (payload.recommendation && payload.recommendation.method) || ((payload.candidates || [])[0] || {{}}).method;
function esc(v) {{ return String(v ?? "").replace(/[&<>"']/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[c])); }}
function fmt(v, d=3) {{ return v === null || v === undefined || Number.isNaN(Number(v)) ? "n/a" : Number(v).toFixed(d); }}
function riskText(c) {{
  const flags = Object.entries((c.diagnostics || {{}}).risk_flags || {{}}).filter(([,v]) => v).map(([k]) => k);
  return flags.length ? flags.join(", ") : "clean";
}}
function resultByMethod(method) {{ return (payload.candidates || []).find(c => c.method === method) || (payload.candidates || [])[0] || {{ notes: [] }}; }}
function renderMeta() {{
  const inputs = payload.inputs || {{}};
  document.getElementById("meta").innerHTML = [
    `Generated ${{esc(payload.generated_at)}}`,
    `Audio ${{esc(inputs.audio_path)}}`,
    `Source ${{esc(inputs.source_midi_path)}}`,
    `Reference ${{esc(inputs.reference_midi_path || "none")}}`
  ].map(v => `<span class="pill">${{v}}</span>`).join("");
}}
function renderRecommendation() {{
  const rec = payload.recommendation || {{}};
  document.getElementById("recommendation").innerHTML = `<p><strong>Recommended review candidate:</strong> <code>${{esc(rec.method || "none")}}</code> <span class="muted">(${{esc(rec.basis || "n/a")}}; review required)</span></p>`;
}}
function renderCards(c) {{
  const diag = c.diagnostics || {{}};
  document.getElementById("cards").innerHTML = [
    ["Notes", c.note_count || 0],
    ["Max Polyphony", diag.max_polyphony || 0],
    ["Dup Rate", fmt(diag.duplicate_rate || 0)]
  ].map(([label, value]) => `<div class="card"><strong>${{esc(value)}}</strong><span class="muted">${{esc(label)}}</span></div>`).join("");
}}
function renderRows() {{
  const rows = payload.candidates || [];
  document.getElementById("candidate-rows").innerHTML = rows.map(c => {{
    const ref = c.reference_eval || {{}};
    const src = c.source_eval || {{}};
    const diag = c.diagnostics || {{}};
    const risk = riskText(c);
    return `<tr data-method="${{esc(c.method)}}" class="${{c.method === selected ? "selected" : ""}}">
      <td>${{esc(c.method)}}${{c.error ? " <span class='risk'>ERROR</span>" : ""}}</td>
      <td>${{fmt(ref.f1)}}</td>
      <td>${{fmt(src.f1)}}</td>
      <td>${{esc(c.note_count || 0)}}</td>
      <td>${{fmt(diag.duplicate_rate || 0)}}</td>
      <td>${{esc(diag.max_polyphony || 0)}}</td>
      <td class="${{risk === "clean" ? "ok" : "risk"}}">${{esc(risk)}}</td>
    </tr>`;
  }}).join("");
  document.querySelectorAll("tr[data-method]").forEach(row => row.addEventListener("click", () => {{ selected = row.dataset.method; render(); }}));
  const select = document.getElementById("candidate-select");
  select.innerHTML = rows.map(c => `<option value="${{esc(c.method)}}" ${{c.method === selected ? "selected" : ""}}>${{esc(c.method)}}</option>`).join("");
  select.onchange = event => {{ selected = event.target.value; render(); }};
}}
function noteRect(note, color, opacity, yOffset) {{
  const dur = Math.max(0.035, Number(note.t_off || 0) - Number(note.t_on || 0));
  const x = 72 + (Number(note.t_on || 0) * 90);
  const w = Math.max(4, dur * 90);
  const y = 22 + ((108 - Number(note.pitch || 60)) * 7) + yOffset;
  return `<rect x="${{x.toFixed(1)}}" y="${{y.toFixed(1)}}" width="${{w.toFixed(1)}}" height="5.2" rx="2" fill="${{color}}" opacity="${{opacity}}"><title>${{esc(note.pitch)}} ${{fmt(note.t_on,2)}}-${{fmt(note.t_off,2)}}s</title></rect>`;
}}
function renderRoll(c) {{
  const source = payload.source_notes || [];
  const ref = payload.reference_notes || [];
  const cand = c.notes || [];
  const duration = Math.max(payload.duration_sec || 0, ...source.map(n => n.t_off || 0), ...cand.map(n => n.t_off || 0), ...ref.map(n => n.t_off || 0), 4);
  const width = Math.max(920, 100 + duration * 90);
  const height = 650;
  const grid = [];
  for (let p = 24; p <= 108; p += 12) {{
    const y = 22 + ((108 - p) * 7);
    grid.push(`<line x1="68" y1="${{y}}" x2="${{width-20}}" y2="${{y}}" stroke="#223752"/><text x="14" y="${{y+4}}" fill="#9db0ca" font-size="11">MIDI ${{p}}</text>`);
  }}
  for (let t = 0; t <= duration; t += 1) {{
    const x = 72 + t * 90;
    grid.push(`<line x1="${{x}}" y1="12" x2="${{x}}" y2="${{height-24}}" stroke="#17263a"/><text x="${{x+3}}" y="${{height-8}}" fill="#9db0ca" font-size="10">${{t}}s</text>`);
  }}
  const rects = [
    ...source.map(n => noteRect(n, "var(--source)", .45, -2)),
    ...cand.map(n => noteRect(n, "var(--candidate)", .82, 2)),
    ...ref.map(n => noteRect(n, "var(--reference)", .55, 7))
  ].join("");
  document.getElementById("roll").innerHTML = `<svg width="${{width}}" height="${{height}}" viewBox="0 0 ${{width}} ${{height}}">${{grid.join("")}}${{rects}}</svg>`;
}}
function renderDetail(c) {{
  const ref = c.reference_eval || {{}};
  const src = c.source_eval || {{}};
  const diag = c.diagnostics || {{}};
  document.getElementById("detail").innerHTML = `<p class="${{c.error ? "risk" : "muted"}}">${{esc(c.error || c.candidate_kind || "")}}</p>
    <p><strong>Reference F1:</strong> ${{fmt(ref.f1)}} / <strong>Source F1:</strong> ${{fmt(src.f1)}} / <strong>Offset F1:</strong> ${{fmt((payload.reference_available ? ref : src).note_with_offset_f1)}}</p>
    <p><strong>Pitch range:</strong> ${{esc(diag.pitch_min ?? "n/a")}}-${{esc(diag.pitch_max ?? "n/a")}} / <strong>Left:</strong> ${{esc(diag.left_hand_notes || 0)}} / <strong>Right:</strong> ${{esc(diag.right_hand_notes || 0)}} / <strong>Density:</strong> ${{fmt(diag.note_density_per_min, 1)}} notes/min</p>`;
}}
function render() {{
  const c = resultByMethod(selected);
  renderRecommendation();
  renderCards(c);
  renderRows();
  renderDetail(c);
  renderRoll(c);
}}
renderMeta();
render();
</script>
</body>
</html>
"""
