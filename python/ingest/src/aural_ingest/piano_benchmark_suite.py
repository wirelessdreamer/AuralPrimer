"""Piano benchmark suite and static report generation."""
from __future__ import annotations

import json
import time
import wave
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from aural_ingest.melodic_benchmark_suite import (
    _render_grouped_bar_svg,
    _render_heatmap_svg,
    _render_timing_mae_svg,
    _slugify,
)
from aural_ingest.piano_benchmark import (
    PIANO_ALGORITHMS,
    PianoBenchmarkEvent,
    benchmark_piano_algorithms,
    format_piano_summary,
    parse_piano_midi_reference,
    write_melodic_notes_midi,
)

DEFAULT_OUTPUT_ROOT = Path("benchmarks") / "piano" / "runs"

REQUIRED_VISUALIZATION_FILES = [
    "summary.json",
    "report.md",
    "report.html",
    "overall_f1_heatmap.svg",
    "offset_f1_heatmap.svg",
    "pitch_accuracy_heatmap.svg",
    "algorithm_summary.svg",
    "instrument_summary.svg",
    "velocity_mae.svg",
    "duplicate_rate.svg",
]


def _slice_wav_for_benchmark(
    wav_path: Path,
    *,
    song_id: str,
    start_sec: float,
    duration_sec: float | None,
) -> Path:
    if duration_sec is None and start_sec <= 0.0:
        return wav_path
    if wav_path.suffix.lower() != ".wav":
        raise ValueError("windowed piano benchmark cases currently require WAV input")

    duration_label = "end" if duration_sec is None else f"{duration_sec:.3f}"
    out_dir = Path("benchmarks") / "piano" / ".cache" / "excerpts"
    out_path = out_dir / f"{_slugify(song_id)}_{start_sec:.3f}_{duration_label}.wav"
    out_dir.mkdir(parents=True, exist_ok=True)

    with wave.open(str(wav_path), "rb") as src:
        frame_rate = src.getframerate()
        start_frame = max(0, int(round(start_sec * frame_rate)))
        src.setpos(min(start_frame, src.getnframes()))
        if duration_sec is None:
            frame_count = src.getnframes() - start_frame
        else:
            frame_count = max(0, int(round(duration_sec * frame_rate)))
        frames = src.readframes(frame_count)
        params = src.getparams()

    with wave.open(str(out_path), "wb") as dst:
        dst.setparams(params)
        dst.writeframes(frames)
    return out_path


def _trim_reference_to_window(
    reference: list[PianoBenchmarkEvent],
    *,
    start_sec: float,
    duration_sec: float | None,
) -> list[PianoBenchmarkEvent]:
    window_start = max(0.0, float(start_sec))
    window_end = None if duration_sec is None else window_start + max(0.0, float(duration_sec))

    out: list[PianoBenchmarkEvent] = []
    for event in reference:
        event_start = float(event.time)
        event_end = float(event.time) + float(event.duration)
        if event_end <= window_start:
            continue
        if window_end is not None and event_start >= window_end:
            continue

        t_on = max(event_start, window_start) - window_start
        t_off_abs = event_end if window_end is None else min(window_end, event_end)
        t_off = t_off_abs - window_start
        if t_off <= t_on:
            continue
        out.append(replace(event, time=round(t_on, 6), duration=round(t_off - t_on, 6)))
    return out


def summarize_piano_suite_results(payload: Mapping[str, Any]) -> dict[str, Any]:
    algorithms = list(payload.get("algorithms", []))
    songs = payload.get("songs", [])
    case_order = [song["song_id"] for song in songs]

    overall_f1_matrix = [[0.0] * len(songs) for _ in algorithms]
    onset_f1_matrix = [[0.0] * len(songs) for _ in algorithms]
    offset_f1_matrix = [[0.0] * len(songs) for _ in algorithms]
    offset_velocity_f1_matrix = [[0.0] * len(songs) for _ in algorithms]
    pitch_accuracy_matrix = [[0.0] * len(songs) for _ in algorithms]
    duplicate_rate_matrix = [[0.0] * len(songs) for _ in algorithms]

    algorithm_summaries = []
    for ai, algorithm in enumerate(algorithms):
        f1s: list[float] = []
        onsets: list[float] = []
        offsets: list[float] = []
        offset_velocities: list[float] = []
        pitch_accuracies: list[float] = []
        vel_maes: list[float] = []
        dup_rates: list[float] = []
        onset_maes: list[float] = []

        for si, song in enumerate(songs):
            result = next((item for item in song.get("results", []) if item["algorithm"] == algorithm), None)
            if not result:
                continue
            if not song.get("reference_available", True):
                continue
            overall = result.get("overall", {})
            f1 = float(overall.get("f1", 0.0) or 0.0)
            onset_f1 = float(overall.get("onset_only_f1", 0.0) or 0.0)
            offset_f1 = float(overall.get("note_with_offset_f1", 0.0) or 0.0)
            offset_velocity_f1 = float(overall.get("note_with_offset_velocity_f1", 0.0) or 0.0)
            pitch_accuracy = float(overall.get("pitch_accuracy", 0.0) or 0.0)
            duplicate_rate = float(overall.get("duplicate_rate", 0.0) or 0.0)
            vel_mae = overall.get("velocity_mae")
            onset_mae = overall.get("onset_timing_mae_ms")

            overall_f1_matrix[ai][si] = f1
            onset_f1_matrix[ai][si] = onset_f1
            offset_f1_matrix[ai][si] = offset_f1
            offset_velocity_f1_matrix[ai][si] = offset_velocity_f1
            pitch_accuracy_matrix[ai][si] = pitch_accuracy
            duplicate_rate_matrix[ai][si] = duplicate_rate

            f1s.append(f1)
            onsets.append(onset_f1)
            offsets.append(offset_f1)
            offset_velocities.append(offset_velocity_f1)
            pitch_accuracies.append(pitch_accuracy)
            dup_rates.append(duplicate_rate)
            if vel_mae is not None:
                vel_maes.append(float(vel_mae))
            if onset_mae is not None:
                onset_maes.append(float(onset_mae))

        algorithm_summaries.append(
            {
                "algorithm": algorithm,
                "mean_f1": sum(f1s) / max(1, len(f1s)),
                "mean_onset_only_f1": sum(onsets) / max(1, len(onsets)),
                "mean_note_with_offset_f1": sum(offsets) / max(1, len(offsets)),
                "mean_note_with_offset_velocity_f1": sum(offset_velocities) / max(1, len(offset_velocities)),
                "mean_pitch_accuracy": sum(pitch_accuracies) / max(1, len(pitch_accuracies)),
                "mean_velocity_mae": sum(vel_maes) / max(1, len(vel_maes)) if vel_maes else None,
                "mean_duplicate_rate": sum(dup_rates) / max(1, len(dup_rates)),
                "mean_onset_timing_mae_ms": sum(onset_maes) / max(1, len(onset_maes)) if onset_maes else None,
            }
        )

    return {
        "case_order": case_order,
        "overall_f1_matrix": overall_f1_matrix,
        "onset_f1_matrix": onset_f1_matrix,
        "offset_f1_matrix": offset_f1_matrix,
        "offset_velocity_f1_matrix": offset_velocity_f1_matrix,
        "pitch_accuracy_matrix": pitch_accuracy_matrix,
        "duplicate_rate_matrix": duplicate_rate_matrix,
        "algorithm_summaries": algorithm_summaries,
    }


def _render_report_markdown(payload: Mapping[str, Any], summary: Mapping[str, Any]) -> str:
    lines = ["# Piano Transcription Benchmark Report", ""]
    lines.append(f"Generated: {datetime.now().isoformat()}")
    lines.append(f"Tolerance: {payload.get('tolerance_ms', 60)}ms onset / {payload.get('offset_tolerance_ms', 120)}ms offset")
    lines.append(f"Algorithms: {', '.join(payload.get('algorithms', []))}")
    lines.append("")
    lines.append("## Aggregate Summary")
    lines.append("")
    lines.append("| Algorithm | Mean F1 | Onset F1 | Offset F1 | Off+Vel F1 | Pitch Acc | Velocity MAE | Duplicate Rate |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for entry in summary.get("algorithm_summaries", []):
        velocity_mae = entry.get("mean_velocity_mae")
        velocity_text = f"{velocity_mae:.1f}" if velocity_mae is not None else "n/a"
        lines.append(
            f"| {entry['algorithm']} | {entry['mean_f1']:.3f} | {entry['mean_onset_only_f1']:.3f} "
            f"| {entry['mean_note_with_offset_f1']:.3f} | {entry['mean_note_with_offset_velocity_f1']:.3f} | {entry['mean_pitch_accuracy']:.1%} "
            f"| {velocity_text} | {entry['mean_duplicate_rate']:.1%} |"
        )
    lines.append("")

    for song in payload.get("songs", []):
        lines.append(f"## {song.get('song_name', song.get('song_id', '?'))}")
        reference_text = (
            str(song.get("reference_count", 0))
            if song.get("reference_available", True)
            else "none; listening/no-reference case"
        )
        lines.append(f"Instrument: {song.get('instrument', '?')} | Reference notes: {reference_text}")
        lines.append("")
        lines.append(format_piano_summary(song))
        lines.append("")

    return "\n".join(lines)


def _render_report_html(payload: Mapping[str, Any], summary: Mapping[str, Any]) -> str:
    md = _render_report_markdown(payload, summary)
    return f"""<!DOCTYPE html>
<html><head><title>Piano Benchmark Report</title>
<style>
body {{ background: #1e1e2e; color: #cdd6f4; font-family: 'Fira Code', monospace; padding: 2em; }}
pre {{ white-space: pre-wrap; font-size: 13px; }}
h1, h2 {{ color: #89b4fa; }}
</style>
</head><body><pre>{md}</pre></body></html>"""


def run_piano_benchmark_suite(
    songs: list[dict[str, Any]],
    *,
    algorithms: list[str] | None = None,
    tolerance_ms: float = 60.0,
    offset_tolerance_ms: float = 120.0,
    velocity_tolerance: int = 20,
) -> dict[str, Any]:
    if algorithms is None:
        algorithms = list(PIANO_ALGORITHMS)

    tolerance_sec = tolerance_ms / 1000.0
    offset_tolerance_sec = offset_tolerance_ms / 1000.0
    all_song_results = []

    print(f"Algorithms: {', '.join(algorithms)}")
    print(f"Tolerance:  {tolerance_ms}ms onset / {offset_tolerance_ms}ms offset")
    print(f"Songs:      {len(songs)}")
    print()

    for song in songs:
        midi_raw = song.get("midi")
        midi_path = Path(midi_raw) if midi_raw else None
        wav_path = Path(song["wav"])
        offset = float(song.get("offset_sec", 0.0) or 0.0)
        instrument = song.get("instrument", "keys")
        window_start = float(song.get("start_sec", 0.0) or 0.0)
        window_duration_raw = song.get("duration_sec")
        window_duration = float(window_duration_raw) if window_duration_raw is not None else None

        if not wav_path.exists():
            print(f"SKIP {song.get('name', '?')} — files not found")
            continue

        eval_wav_path = _slice_wav_for_benchmark(
            wav_path,
            song_id=str(song.get("id", "unknown")),
            start_sec=window_start,
            duration_sec=window_duration,
        )

        print("=" * 60)
        print(f"  {song.get('name', '?')}  [{instrument}]  (offset: {offset:+.3f}s)")
        if eval_wav_path != wav_path:
            window_end = "end" if window_duration is None else f"{window_start + window_duration:.2f}s"
            print(f"  Window: {window_start:.2f}s -> {window_end}")
        print("=" * 60)

        reference = None
        if midi_path is not None and midi_path.exists():
            reference = parse_piano_midi_reference(midi_path, offset)
            reference = _trim_reference_to_window(reference, start_sec=window_start, duration_sec=window_duration)
            print(f"  Reference notes: {len(reference)}")
        else:
            print("  Reference notes: none (listening/no-reference case)")

        t0 = time.time()
        results = benchmark_piano_algorithms(
            eval_wav_path,
            reference,
            algorithms,
            instrument=instrument,
            tolerance_sec=tolerance_sec,
            offset_tolerance_sec=offset_tolerance_sec,
            velocity_tolerance=velocity_tolerance,
        )
        elapsed = time.time() - t0
        print(f"  Evaluated in {elapsed:.1f}s")

        song_payload = {
            "song_id": song.get("id", "unknown"),
            "song_name": song.get("name", "?"),
            "instrument": instrument,
            "source_wav_path": str(wav_path),
            "wav_path": str(eval_wav_path),
            "reference_path": str(midi_path) if midi_path is not None else None,
            "reference_available": reference is not None,
            "reference_count": len(reference) if reference is not None else 0,
            "tolerance_ms": tolerance_ms,
            "offset_tolerance_ms": offset_tolerance_ms,
            "midi_offset_sec": offset,
            "window_start_sec": window_start,
            "window_duration_sec": window_duration,
            "results": results,
        }
        print(format_piano_summary(song_payload))
        print()
        all_song_results.append(song_payload)

    return {
        "algorithms": algorithms,
        "tolerance_ms": tolerance_ms,
        "offset_tolerance_ms": offset_tolerance_ms,
        "songs": all_song_results,
    }


def _write_prediction_artifacts(out_dir: Path, payload: Mapping[str, Any]) -> None:
    prediction_root = out_dir / "predictions"
    index: list[dict[str, Any]] = []
    for song in payload.get("songs", []):
        song_id = str(song.get("song_id") or "unknown")
        song_dir = prediction_root / _slugify(song_id)
        for result in song.get("results", []):
            if result.get("error"):
                continue
            notes = result.get("predicted_notes") or []
            algorithm = str(result.get("algorithm") or "unknown")
            algorithm_slug = _slugify(algorithm)
            if not notes:
                continue

            midi_path = song_dir / f"{algorithm_slug}.mid"
            notes_path = song_dir / f"{algorithm_slug}.notes.json"
            write_melodic_notes_midi(notes, midi_path)
            notes_path.write_text(json.dumps(notes, indent=2), encoding="utf-8")
            index.append(
                {
                    "song_id": song_id,
                    "song_name": song.get("song_name"),
                    "algorithm": algorithm,
                    "midi": str(midi_path.relative_to(out_dir)),
                    "notes": str(notes_path.relative_to(out_dir)),
                    "note_count": result.get("note_count", len(notes)),
                    "reference_available": bool(song.get("reference_available", True)),
                }
            )

    if index:
        prediction_root.mkdir(parents=True, exist_ok=True)
        (prediction_root / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")


def write_piano_suite_outputs(
    payload: Mapping[str, Any],
    *,
    output_root: Path | str | None = None,
    label: str | None = None,
) -> Path:
    root = Path(output_root) if output_root else DEFAULT_OUTPUT_ROOT
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = timestamp if not label else f"{timestamp}_{_slugify(label)}"
    out_dir = root / run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = summarize_piano_suite_results(payload)
    enriched = {**payload, "summary": summary}
    algorithms = list(payload.get("algorithms", []))
    case_order = list(summary["case_order"])

    instrument_songs: dict[str, list[int]] = {}
    for si, song in enumerate(payload.get("songs", [])):
        if not song.get("reference_available", True):
            continue
        instrument_songs.setdefault(song.get("instrument", "keys"), []).append(si)

    artifacts = {
        "overall_f1_heatmap.svg": _render_heatmap_svg(
            title="Piano Exact-Note F1 by Song",
            subtitle="Pitch-aware note matches with onset tolerance. Higher is better.",
            row_labels=algorithms,
            col_labels=case_order,
            values=summary["overall_f1_matrix"],
        ),
        "offset_f1_heatmap.svg": _render_heatmap_svg(
            title="Piano Note+Offset F1 by Song",
            subtitle="Pitch-aware notes whose offsets also land inside tolerance.",
            row_labels=algorithms,
            col_labels=case_order,
            values=summary["offset_f1_matrix"],
        ),
        "pitch_accuracy_heatmap.svg": _render_heatmap_svg(
            title="Pitch Accuracy by Song",
            subtitle="Exact-pitch matches divided by onset-only matches.",
            row_labels=algorithms,
            col_labels=case_order,
            values=summary["pitch_accuracy_matrix"],
        ),
        "algorithm_summary.svg": _render_grouped_bar_svg(
            title="Aggregate Piano Algorithm Summary",
            subtitle="Mean exact-note F1 vs note+offset F1 across the suite.",
            algorithms=algorithms,
            series=(
                ("Mean F1", [entry.get("mean_f1") for entry in summary["algorithm_summaries"]], "#56b6c2"),
                (
                    "Offset F1",
                    [entry.get("mean_note_with_offset_f1") for entry in summary["algorithm_summaries"]],
                    "#ff8f5a",
                ),
            ),
            y_label="score",
            max_value=1.0,
        ),
        "instrument_summary.svg": _render_grouped_bar_svg(
            title="Per-Instrument Mean F1",
            subtitle="Useful when the piano suite mixes solo piano and stems.",
            algorithms=algorithms,
            series=tuple(
                (
                    inst,
                    [
                        sum(summary["overall_f1_matrix"][ai][si] for si in idxs) / max(1, len(idxs))
                        for ai in range(len(algorithms))
                    ],
                    ["#7bd389", "#ff8f5a", "#5aa9e6", "#c084fc"][ii % 4],
                )
                for ii, (inst, idxs) in enumerate(instrument_songs.items())
            ),
            y_label="F1",
            max_value=1.0,
        ),
        "velocity_mae.svg": _render_timing_mae_svg(
            title="Mean Velocity MAE by Algorithm",
            subtitle="Absolute error between predicted and reference MIDI velocities. Lower is better.",
            algorithms=algorithms,
            values=[entry.get("mean_velocity_mae") for entry in summary["algorithm_summaries"]],
        ),
        "duplicate_rate.svg": _render_heatmap_svg(
            title="Duplicate Prediction Rate by Song",
            subtitle="Fraction of predictions that look like same-pitch micro-duplicates. Lower is better.",
            row_labels=algorithms,
            col_labels=case_order,
            values=summary["duplicate_rate_matrix"],
            color_good="#ef4444",
            color_bad="#22c55e",
        ),
        "report.md": _render_report_markdown(enriched, summary),
        "report.html": _render_report_html(enriched, summary),
        "summary.json": json.dumps(enriched, indent=2, default=str),
    }

    for name, content in artifacts.items():
        (out_dir / name).write_text(content, encoding="utf-8")

    _write_prediction_artifacts(out_dir, payload)

    missing = [name for name in REQUIRED_VISUALIZATION_FILES if not (out_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"piano benchmark run incomplete: missing {', '.join(missing)}")

    latest_marker = root.parent / "LATEST_RUN.txt"
    latest_marker.parent.mkdir(parents=True, exist_ok=True)
    latest_marker.write_text(str(out_dir), encoding="utf-8")
    return out_dir
