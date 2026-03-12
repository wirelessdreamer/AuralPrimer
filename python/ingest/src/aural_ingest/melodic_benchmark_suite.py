"""Melodic transcription benchmark suite — report generation.

Mirrors ``drum_benchmark_suite.py``: runs all algorithms on all songs,
generates timestamped output directories with SVG heatmaps, grouped bar
charts, and markdown/HTML reports.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from aural_ingest.melodic_benchmark import (
    MelodicBenchmarkEvent,
    MelodicEvalResult,
    benchmark_melodic_algorithms,
    evaluate_melodic,
    format_melodic_summary,
    parse_melodic_midi_reference,
    MELODIC_ALGORITHMS,
)

DEFAULT_OUTPUT_ROOT = Path("benchmarks") / "melodic" / "runs"

REQUIRED_VISUALIZATION_FILES = [
    "summary.json",
    "report.md",
    "report.html",
    "overall_f1_heatmap.svg",
    "pitch_accuracy_heatmap.svg",
    "algorithm_summary.svg",
    "instrument_summary.svg",
    "timing_mae.svg",
    "octave_error_heatmap.svg",
]


def _slugify(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in s).strip("-")


# ---------------------------------------------------------------------------
# SVG rendering
# ---------------------------------------------------------------------------

def _render_heatmap_svg(
    title: str,
    subtitle: str,
    row_labels: list[str],
    col_labels: list[str],
    values: list[list[float]],
    *,
    color_good: str = "#22c55e",
    color_bad: str = "#ef4444",
    color_mid: str = "#facc15",
    cell_w: int = 80,
    cell_h: int = 36,
) -> str:
    """Render a heatmap SVG with algorithms as rows and songs as columns."""
    n_rows = len(row_labels)
    n_cols = len(col_labels)
    left_margin = max(180, max((len(l) for l in row_labels), default=10) * 8 + 20)
    top_margin = 70
    bottom_margin = 60

    w = left_margin + n_cols * cell_w + 20
    h = top_margin + n_rows * cell_h + bottom_margin

    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
             f'style="background:#1e1e2e;font-family:monospace">']
    lines.append(f'<text x="{w//2}" y="22" fill="#cdd6f4" font-size="14" text-anchor="middle" font-weight="bold">{title}</text>')
    lines.append(f'<text x="{w//2}" y="40" fill="#a6adc8" font-size="10" text-anchor="middle">{subtitle}</text>')

    # Column labels (rotated)
    for c, label in enumerate(col_labels):
        x = left_margin + c * cell_w + cell_w // 2
        y = top_margin - 8
        short = label[:12] if len(label) > 12 else label
        lines.append(f'<text x="{x}" y="{y}" fill="#a6adc8" font-size="9" text-anchor="end" '
                     f'transform="rotate(-35 {x} {y})">{short}</text>')

    # Rows
    for r, row_label in enumerate(row_labels):
        y = top_margin + r * cell_h
        lines.append(f'<text x="{left_margin - 8}" y="{y + cell_h // 2 + 4}" fill="#cdd6f4" '
                     f'font-size="10" text-anchor="end">{row_label}</text>')
        for c in range(n_cols):
            x = left_margin + c * cell_w
            val = values[r][c] if r < len(values) and c < len(values[r]) else 0.0
            # Color interpolation: red→yellow→green
            if val <= 0.5:
                t2 = val * 2.0
                r_c = int(239 + (250 - 239) * t2)
                g_c = int(68 + (204 - 68) * t2)
                b_c = int(68 + (21 - 68) * t2)
            else:
                t2 = (val - 0.5) * 2.0
                r_c = int(250 + (34 - 250) * t2)
                g_c = int(204 + (197 - 204) * t2)
                b_c = int(21 + (94 - 21) * t2)
            fill = f"#{r_c:02x}{g_c:02x}{b_c:02x}"
            lines.append(f'<rect x="{x}" y="{y}" width="{cell_w - 2}" height="{cell_h - 2}" '
                         f'fill="{fill}" rx="3"/>')
            text_color = "#1e1e2e" if val > 0.4 else "#cdd6f4"
            lines.append(f'<text x="{x + cell_w // 2}" y="{y + cell_h // 2 + 4}" '
                         f'fill="{text_color}" font-size="11" text-anchor="middle">{val:.3f}</text>')

    lines.append("</svg>")
    return "\n".join(lines)


def _render_grouped_bar_svg(
    title: str,
    subtitle: str,
    algorithms: list[str],
    series: tuple[tuple[str, list[float | None], str], ...],
    *,
    y_label: str = "score",
    max_value: float = 1.0,
    bar_width: int = 28,
    group_gap: int = 20,
) -> str:
    """Render a grouped bar chart SVG."""
    n_alg = len(algorithms)
    n_series = len(series)
    group_w = n_series * bar_width + group_gap
    left_margin = 60
    top_margin = 70
    chart_h = 200
    bottom_margin = 80

    w = left_margin + n_alg * group_w + 40
    h = top_margin + chart_h + bottom_margin

    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
             f'style="background:#1e1e2e;font-family:monospace">']
    lines.append(f'<text x="{w//2}" y="22" fill="#cdd6f4" font-size="14" text-anchor="middle" font-weight="bold">{title}</text>')
    lines.append(f'<text x="{w//2}" y="40" fill="#a6adc8" font-size="10" text-anchor="middle">{subtitle}</text>')

    # Y axis
    for i in range(6):
        val = i * 0.2
        y = top_margin + chart_h - int(val / max_value * chart_h)
        lines.append(f'<line x1="{left_margin}" y1="{y}" x2="{w - 20}" y2="{y}" stroke="#313244" stroke-width="1"/>')
        lines.append(f'<text x="{left_margin - 5}" y="{y + 4}" fill="#a6adc8" font-size="9" text-anchor="end">{val:.1f}</text>')

    # Bars
    for a, alg in enumerate(algorithms):
        gx = left_margin + a * group_w + group_gap // 2
        for s, (s_name, s_vals, s_color) in enumerate(series):
            val = s_vals[a] if a < len(s_vals) and s_vals[a] is not None else 0.0
            bh = max(1, int(val / max_value * chart_h))
            bx = gx + s * bar_width
            by = top_margin + chart_h - bh
            lines.append(f'<rect x="{bx}" y="{by}" width="{bar_width - 2}" height="{bh}" fill="{s_color}" rx="2"/>')
            lines.append(f'<text x="{bx + bar_width // 2}" y="{by - 4}" fill="{s_color}" font-size="8" text-anchor="middle">{val:.3f}</text>')

        # Algorithm label
        lines.append(f'<text x="{gx + n_series * bar_width // 2}" y="{top_margin + chart_h + 16}" '
                     f'fill="#a6adc8" font-size="8" text-anchor="middle" '
                     f'transform="rotate(-20 {gx + n_series * bar_width // 2} {top_margin + chart_h + 16})">{alg}</text>')

    # Legend
    for s, (s_name, _, s_color) in enumerate(series):
        lx = left_margin + s * 160
        ly = h - 20
        lines.append(f'<rect x="{lx}" y="{ly - 8}" width="12" height="12" fill="{s_color}" rx="2"/>')
        lines.append(f'<text x="{lx + 16}" y="{ly + 2}" fill="#cdd6f4" font-size="9">{s_name}</text>')

    lines.append("</svg>")
    return "\n".join(lines)


def _render_timing_mae_svg(
    title: str,
    subtitle: str,
    algorithms: list[str],
    values: list[float | None],
) -> str:
    """Horizontal bar chart for timing MAE."""
    left_margin = 200
    bar_h = 30
    top_margin = 60
    max_val = max((v for v in values if v is not None), default=50.0)
    chart_w = 400

    h = top_margin + len(algorithms) * bar_h + 30
    w = left_margin + chart_w + 60

    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
             f'style="background:#1e1e2e;font-family:monospace">']
    lines.append(f'<text x="{w//2}" y="22" fill="#cdd6f4" font-size="14" text-anchor="middle" font-weight="bold">{title}</text>')
    lines.append(f'<text x="{w//2}" y="40" fill="#a6adc8" font-size="10" text-anchor="middle">{subtitle}</text>')

    for i, (alg, val) in enumerate(zip(algorithms, values)):
        y = top_margin + i * bar_h
        lines.append(f'<text x="{left_margin - 8}" y="{y + bar_h // 2 + 4}" fill="#cdd6f4" font-size="10" text-anchor="end">{alg}</text>')
        if val is not None and max_val > 0:
            bw = max(2, int(val / max_val * chart_w))
            color = "#5aa9e6" if val < 30 else "#facc15" if val < 50 else "#ef4444"
            lines.append(f'<rect x="{left_margin}" y="{y + 4}" width="{bw}" height="{bar_h - 8}" fill="{color}" rx="3"/>')
            lines.append(f'<text x="{left_margin + bw + 6}" y="{y + bar_h // 2 + 4}" fill="#cdd6f4" font-size="10">{val:.1f}ms</text>')
        else:
            lines.append(f'<text x="{left_margin + 8}" y="{y + bar_h // 2 + 4}" fill="#6c7086" font-size="10">n/a</text>')

    lines.append("</svg>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Suite summarization
# ---------------------------------------------------------------------------

def summarize_suite_results(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Aggregate per-song results into cross-song summaries."""
    algorithms = list(payload.get("algorithms", []))
    songs = payload.get("songs", [])
    song_ids = [s["song_id"] for s in songs]

    # Build matrices: [alg_idx][song_idx]
    f1_matrix = [[0.0] * len(songs) for _ in algorithms]
    pitch_acc_matrix = [[0.0] * len(songs) for _ in algorithms]
    octave_err_matrix = [[0.0] * len(songs) for _ in algorithms]

    alg_summaries = []
    for ai, alg in enumerate(algorithms):
        f1s, pitch_accs, maes, octave_errs = [], [], [], []
        for si, song in enumerate(songs):
            result = None
            for r in song.get("results", []):
                if r["algorithm"] == alg:
                    result = r
                    break
            if result and "overall" in result:
                o = result["overall"]
                f1 = o.get("f1", 0.0)
                pa = o.get("pitch_accuracy", 0.0)
                oe = o.get("octave_error_rate", 0.0)
                mae = o.get("timing_mae_ms")
                f1_matrix[ai][si] = f1
                pitch_acc_matrix[ai][si] = pa
                octave_err_matrix[ai][si] = oe
                f1s.append(f1)
                pitch_accs.append(pa)
                octave_errs.append(oe)
                if mae is not None:
                    maes.append(mae)

        alg_summaries.append({
            "algorithm": alg,
            "mean_f1": sum(f1s) / max(1, len(f1s)),
            "mean_pitch_accuracy": sum(pitch_accs) / max(1, len(pitch_accs)),
            "mean_octave_error_rate": sum(octave_errs) / max(1, len(octave_errs)),
            "mean_timing_mae_ms": sum(maes) / max(1, len(maes)) if maes else None,
        })

    return {
        "case_order": song_ids,
        "overall_f1_matrix": f1_matrix,
        "pitch_accuracy_matrix": pitch_acc_matrix,
        "octave_error_matrix": octave_err_matrix,
        "algorithm_summaries": alg_summaries,
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _render_report_markdown(payload: Mapping[str, Any], summary: dict) -> str:
    lines = ["# Melodic Transcription Benchmark Report", ""]
    lines.append(f"Generated: {datetime.now().isoformat()}")
    lines.append(f"Tolerance: {payload.get('tolerance_ms', 60)}ms")
    lines.append(f"Algorithms: {', '.join(payload.get('algorithms', []))}")
    lines.append("")

    lines.append("## Aggregate Summary")
    lines.append("")
    lines.append(f"| Algorithm | Mean F1 | Pitch Acc | Octave Err | Timing MAE |")
    lines.append(f"|---|---:|---:|---:|---:|")
    for s in summary.get("algorithm_summaries", []):
        mae = s.get("mean_timing_mae_ms")
        mae_str = f"{mae:.1f}ms" if mae is not None else "n/a"
        lines.append(
            f"| {s['algorithm']} | {s['mean_f1']:.3f} | {s['mean_pitch_accuracy']:.1%} "
            f"| {s['mean_octave_error_rate']:.1%} | {mae_str} |"
        )
    lines.append("")

    for song in payload.get("songs", []):
        lines.append(f"## {song.get('song_name', song.get('song_id', '?'))}")
        lines.append(f"Instrument: {song.get('instrument', '?')} | Reference notes: {song.get('reference_count', 0)}")
        lines.append("")
        lines.append(format_melodic_summary(song))
        lines.append("")

    return "\n".join(lines)


def _render_report_html(payload: Mapping[str, Any], summary: dict) -> str:
    md = _render_report_markdown(payload, summary)
    return f"""<!DOCTYPE html>
<html><head><title>Melodic Benchmark Report</title>
<style>
body {{ background: #1e1e2e; color: #cdd6f4; font-family: 'Fira Code', monospace; padding: 2em; }}
table {{ border-collapse: collapse; margin: 1em 0; }}
th, td {{ border: 1px solid #313244; padding: 6px 12px; text-align: right; }}
th {{ background: #313244; }}
pre {{ white-space: pre-wrap; font-size: 13px; }}
h1, h2 {{ color: #89b4fa; }}
</style>
</head><body><pre>{md}</pre></body></html>"""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_melodic_benchmark_suite(
    songs: list[dict[str, Any]],
    *,
    algorithms: list[str] | None = None,
    tolerance_ms: float = 60.0,
) -> dict[str, Any]:
    """Run all algorithms on all songs and return full payload."""
    if algorithms is None:
        algorithms = list(MELODIC_ALGORITHMS)

    tolerance_sec = tolerance_ms / 1000.0
    all_song_results = []

    print(f"Algorithms: {', '.join(algorithms)}")
    print(f"Tolerance:  {tolerance_ms}ms")
    print(f"Songs:      {len(songs)}")
    print()

    for song in songs:
        midi_path = Path(song["midi"])
        wav_path = Path(song["wav"])
        offset = song.get("offset_sec", 0.0)
        instrument = song.get("instrument", "melodic")

        if not midi_path.exists() or not wav_path.exists():
            print(f"SKIP {song.get('name', '?')} — files not found")
            continue

        print(f"{'=' * 60}")
        print(f"  {song.get('name', '?')}  [{instrument}]  (offset: {offset:+.3f}s)")
        print(f"{'=' * 60}")

        reference = parse_melodic_midi_reference(midi_path, offset)
        print(f"  Reference notes: {len(reference)}")

        t0 = time.time()
        results = benchmark_melodic_algorithms(
            wav_path, reference, algorithms,
            instrument=instrument,
            tolerance_sec=tolerance_sec,
        )
        elapsed = time.time() - t0
        print(f"  Evaluated in {elapsed:.1f}s")

        song_payload = {
            "song_id": song.get("id", "unknown"),
            "song_name": song.get("name", "?"),
            "instrument": instrument,
            "reference_path": str(midi_path),
            "reference_count": len(reference),
            "tolerance_ms": tolerance_ms,
            "midi_offset_sec": offset,
            "results": results,
        }

        print(format_melodic_summary(song_payload))
        print()
        all_song_results.append(song_payload)

    return {
        "algorithms": algorithms,
        "tolerance_ms": tolerance_ms,
        "songs": all_song_results,
    }


def write_melodic_suite_outputs(
    payload: Mapping[str, Any],
    *,
    output_root: Path | str | None = None,
    label: str | None = None,
) -> Path:
    """Write all suite artifacts to a timestamped directory."""
    root = Path(output_root) if output_root else Path("benchmarks") / "melodic" / "runs"
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = timestamp if not label else f"{timestamp}_{_slugify(label)}"
    out_dir = root / run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = summarize_suite_results(payload)
    enriched = {**payload, "summary": summary}
    algorithms = list(payload.get("algorithms", []))
    case_order = list(summary["case_order"])

    # --- Per-instrument F1 heatmaps ---
    # Group songs by instrument
    instrument_songs: dict[str, list[int]] = {}
    for si, song in enumerate(payload.get("songs", [])):
        inst = song.get("instrument", "melodic")
        instrument_songs.setdefault(inst, []).append(si)

    per_instrument_svgs = {}
    for inst, song_indices in instrument_songs.items():
        inst_cols = [case_order[i] for i in song_indices if i < len(case_order)]
        inst_vals = [[summary["overall_f1_matrix"][ai][si] for si in song_indices if si < len(summary["overall_f1_matrix"][ai])] for ai in range(len(algorithms))]
        per_instrument_svgs[f"{inst}_f1_heatmap.svg"] = _render_heatmap_svg(
            title=f"{inst.title()} F1 by Song",
            subtitle=f"Per-song F1 for {inst} instrument tracks.",
            row_labels=algorithms,
            col_labels=inst_cols,
            values=inst_vals,
        )

    artifacts = {
        "overall_f1_heatmap.svg": _render_heatmap_svg(
            title="Overall Note F1 by Song",
            subtitle="Rows are algorithms, columns are song/instrument combos. Higher is better.",
            row_labels=algorithms,
            col_labels=case_order,
            values=summary["overall_f1_matrix"],
        ),
        "pitch_accuracy_heatmap.svg": _render_heatmap_svg(
            title="Pitch Accuracy by Song",
            subtitle="Fraction of matched notes with correct pitch (±1 semitone).",
            row_labels=algorithms,
            col_labels=case_order,
            values=summary["pitch_accuracy_matrix"],
        ),
        "octave_error_heatmap.svg": _render_heatmap_svg(
            title="Octave Error Rate by Song",
            subtitle="Fraction of matched notes with correct chroma but wrong octave. Lower is better.",
            row_labels=algorithms,
            col_labels=case_order,
            values=summary["octave_error_matrix"],
            color_good="#ef4444",
            color_bad="#22c55e",
        ),
        "algorithm_summary.svg": _render_grouped_bar_svg(
            title="Aggregate Algorithm Summary",
            subtitle="Mean overall F1 and pitch accuracy across all songs.",
            algorithms=algorithms,
            series=(
                ("Mean F1", [s.get("mean_f1") for s in summary["algorithm_summaries"]], "#56b6c2"),
                ("Pitch Acc", [s.get("mean_pitch_accuracy") for s in summary["algorithm_summaries"]], "#ff8f5a"),
            ),
            y_label="score",
            max_value=1.0,
        ),
        "instrument_summary.svg": _render_grouped_bar_svg(
            title="Per-Instrument Mean F1",
            subtitle="Algorithm performance broken down by instrument.",
            algorithms=algorithms,
            series=tuple(
                (inst, [
                    sum(summary["overall_f1_matrix"][ai][si] for si in idxs) / max(1, len(idxs))
                    for ai in range(len(algorithms))
                ], ["#7bd389", "#ff8f5a", "#5aa9e6", "#c084fc"][ii % 4])
                for ii, (inst, idxs) in enumerate(instrument_songs.items())
            ),
            y_label="F1",
            max_value=1.0,
        ),
        "timing_mae.svg": _render_timing_mae_svg(
            title="Mean Timing MAE by Algorithm",
            subtitle="Mean matched onset absolute timing error. Lower is better.",
            algorithms=algorithms,
            values=[s.get("mean_timing_mae_ms") for s in summary["algorithm_summaries"]],
        ),
        "report.md": _render_report_markdown(enriched, summary),
        "report.html": _render_report_html(enriched, summary),
        "summary.json": json.dumps(enriched, indent=2, default=str),
        **per_instrument_svgs,
    }

    for name, content in artifacts.items():
        (out_dir / name).write_text(content, encoding="utf-8")

    missing = [name for name in REQUIRED_VISUALIZATION_FILES if not (out_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"benchmark run incomplete: missing {', '.join(missing)}")

    latest_marker = root.parent / "LATEST_RUN.txt"
    latest_marker.parent.mkdir(parents=True, exist_ok=True)
    latest_marker.write_text(str(out_dir), encoding="utf-8")
    return out_dir
