from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import statistics
from typing import Any, Mapping, Sequence

from aural_ingest.drum_benchmark import BENCHMARK_CLASS_ORDER, benchmark_algorithms, load_drum_reference
from aural_ingest.transcription import KNOWN_DRUM_FILTERS, build_default_drum_algorithm_registry


SUITE_VERSION = "1.0.0"
DEFAULT_FIXTURES_DIR = Path("assets") / "test_fixtures" / "drum_benchmark_midis"
DEFAULT_OUTPUT_ROOT = Path("benchmarks") / "drums" / "runs"
PRIORITY_LANES: tuple[str, ...] = ("kick", "snare", "hi_hat")
REQUIRED_VISUALIZATION_FILES: tuple[str, ...] = (
    "overall_f1_heatmap.svg",
    "kick_f1_heatmap.svg",
    "snare_f1_heatmap.svg",
    "hi_hat_f1_heatmap.svg",
    "algorithm_summary.svg",
    "core_lane_summary.svg",
    "timing_mae.svg",
    "snare_confusion_heatmap.svg",
    "hi_hat_confusion_heatmap.svg",
    "report.md",
    "report.html",
    "summary.json",
)


@dataclass(frozen=True)
class SuiteCase:
    case_id: str
    title: str
    bpm: float
    tags: tuple[str, ...]
    focus: tuple[str, ...]
    summary: str
    wav_path: Path
    reference_path: Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _default_fixtures_dir() -> Path:
    return _repo_root() / DEFAULT_FIXTURES_DIR


def _default_output_root() -> Path:
    return _repo_root() / DEFAULT_OUTPUT_ROOT


def _safe_mean(values: Sequence[float | None]) -> float | None:
    usable = [float(value) for value in values if value is not None]
    if not usable:
        return None
    return statistics.fmean(usable)


def _safe_median(values: Sequence[float | None]) -> float | None:
    usable = [float(value) for value in values if value is not None]
    if not usable:
        return None
    return float(statistics.median(usable))


def _dedupe_preserve_order(values: Sequence[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def _slugify(value: str) -> str:
    out = []
    last_dash = False
    for char in value.strip().lower():
        if char.isalnum():
            out.append(char)
            last_dash = False
        elif not last_dash:
            out.append("-")
            last_dash = True
    slug = "".join(out).strip("-")
    return slug or "run"


def _format_score(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.3f}"


def _format_ms(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.1f} ms"


def _manifest_case_audio_path(fixtures_dir: Path, case_id: str) -> Path:
    return fixtures_dir / f"{case_id}.wav"


def load_suite_cases(
    fixtures_dir: Path | str | None = None,
    *,
    selected_case_ids: Sequence[str] | None = None,
) -> tuple[list[SuiteCase], dict[str, Any], list[str]]:
    fixtures_path = Path(fixtures_dir) if fixtures_dir is not None else _default_fixtures_dir()
    manifest_path = fixtures_path / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing benchmark manifest: {manifest_path}")

    manifest = json.loads(manifest_path.read_text("utf-8"))
    wanted = set(selected_case_ids or [])
    cases: list[SuiteCase] = []
    warnings: list[str] = []
    seen: set[str] = set()

    for item in manifest.get("cases", []):
        case_id = str(item.get("id", "")).strip()
        if not case_id:
            continue
        seen.add(case_id)
        if wanted and case_id not in wanted:
            continue

        reference_path = fixtures_path / str(item.get("midi_path", "")).strip()
        wav_path = _manifest_case_audio_path(fixtures_path, case_id)
        if not reference_path.is_file():
            warnings.append(f"{case_id}: missing reference midi {reference_path.name}")
            continue
        if not wav_path.is_file():
            warnings.append(f"{case_id}: missing rendered audio {wav_path.name}")
            continue

        cases.append(
            SuiteCase(
                case_id=case_id,
                title=str(item.get("title", case_id)),
                bpm=float(item.get("bpm", 0.0) or 0.0),
                tags=tuple(str(tag) for tag in item.get("tags", [])),
                focus=tuple(str(focus) for focus in item.get("focus", [])),
                summary=str(item.get("summary", "")),
                wav_path=wav_path,
                reference_path=reference_path,
            )
        )

    if wanted:
        missing = sorted(wanted - seen)
        for case_id in missing:
            warnings.append(f"{case_id}: case id not found in benchmark manifest")

    return cases, manifest, warnings


def _collect_algorithm_ids(requested: Sequence[str] | None) -> list[str]:
    if not requested:
        return list(KNOWN_DRUM_FILTERS)
    normalized = []
    for value in requested:
        token = value.strip().lower()
        if token:
            normalized.append(token)
    return _dedupe_preserve_order(normalized or list(KNOWN_DRUM_FILTERS))


def run_benchmark_suite(
    *,
    fixtures_dir: Path | str | None = None,
    algorithms: Sequence[str] | None = None,
    tolerance_ms: float = 60.0,
    selected_case_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    if float(tolerance_ms) <= 0.0:
        raise ValueError("tolerance_ms must be > 0")

    cases, manifest, warnings = load_suite_cases(fixtures_dir, selected_case_ids=selected_case_ids)
    if not cases:
        raise ValueError("no benchmark cases available with both rendered audio and reference midi")

    fixtures_path = Path(fixtures_dir) if fixtures_dir is not None else _default_fixtures_dir()
    algorithm_ids = _collect_algorithm_ids(algorithms)
    registry = build_default_drum_algorithm_registry()
    case_payloads: list[dict[str, Any]] = []

    for case in cases:
        reference_events, reference_meta = load_drum_reference(case.reference_path)
        results = benchmark_algorithms(
            case.wav_path,
            reference_events,
            algorithm_ids,
            registry,
            tolerance_sec=float(tolerance_ms) / 1000.0,
        )
        case_payloads.append(
            {
                "case_id": case.case_id,
                "title": case.title,
                "bpm": case.bpm,
                "tags": list(case.tags),
                "focus": list(case.focus),
                "summary": case.summary,
                "wav_path": str(case.wav_path),
                "reference_path": str(case.reference_path),
                "reference_count": len(reference_events),
                "reference_meta": reference_meta,
                "results": results,
            }
        )

    return {
        "suite_version": SUITE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fixtures_dir": str(fixtures_path),
        "algorithms": algorithm_ids,
        "tolerance_ms": round(float(tolerance_ms), 3),
        "class_order": list(BENCHMARK_CLASS_ORDER),
        "manifest_format": manifest.get("format"),
        "cases": case_payloads,
        "warnings": warnings,
    }


def summarize_suite_results(payload: Mapping[str, Any]) -> dict[str, Any]:
    algorithms = list(payload.get("algorithms", []))
    cases = list(payload.get("cases", []))
    case_lookup = {case["case_id"]: case for case in cases}
    case_order = [case["case_id"] for case in cases]
    case_titles = {case["case_id"]: case["title"] for case in cases}
    tag_map = {case["case_id"]: list(case.get("tags", [])) for case in cases}

    algorithm_summaries: list[dict[str, Any]] = []
    overall_matrix: dict[str, dict[str, float | None]] = {algorithm: {} for algorithm in algorithms}
    lane_matrices: dict[str, dict[str, dict[str, float | None]]] = {
        lane: {algorithm: {} for algorithm in algorithms} for lane in PRIORITY_LANES
    }
    confusion_matrices: dict[str, dict[str, dict[str, int]]] = {
        lane: {algorithm: {} for algorithm in algorithms} for lane in ("snare", "hi_hat")
    }

    for algorithm in algorithms:
        overall_f1s: list[float | None] = []
        lane_f1s: dict[str, list[float | None]] = {lane: [] for lane in PRIORITY_LANES}
        timing_maes: list[float | None] = []
        overall_precisions: list[float | None] = []
        overall_recalls: list[float | None] = []
        error_cases: list[str] = []

        for case_id in case_order:
            case = case_lookup[case_id]
            result = next(
                (item for item in case.get("results", []) if item.get("algorithm") == algorithm),
                None,
            )
            if result is None or result.get("error"):
                overall_matrix[algorithm][case_id] = None
                for lane in PRIORITY_LANES:
                    lane_matrices[lane][algorithm][case_id] = None
                if result is not None and result.get("error"):
                    error_cases.append(case_id)
                continue

            overall = result["overall"]
            overall_f1s.append(float(overall["f1"]))
            timing_maes.append(
                None if overall.get("timing_mae_ms") is None else float(overall["timing_mae_ms"])
            )
            overall_precisions.append(float(overall["precision"]))
            overall_recalls.append(float(overall["recall"]))
            overall_matrix[algorithm][case_id] = float(overall["f1"])
            per_class = result.get("per_class", {})
            for lane in PRIORITY_LANES:
                lane_result = per_class.get(lane)
                lane_value = None if lane_result is None else float(lane_result["f1"])
                lane_f1s[lane].append(lane_value)
                lane_matrices[lane][algorithm][case_id] = lane_value

            for confusion in result.get("confusions", []):
                reference_class = str(confusion.get("reference_class"))
                if reference_class not in confusion_matrices:
                    continue
                pred = str(confusion.get("predicted_class"))
                confusion_matrices[reference_class][algorithm][pred] = (
                    confusion_matrices[reference_class][algorithm].get(pred, 0)
                    + int(confusion.get("count", 0))
                )

        mean_kick_f1 = _safe_mean(lane_f1s["kick"])
        mean_snare_f1 = _safe_mean(lane_f1s["snare"])
        mean_hi_hat_f1 = _safe_mean(lane_f1s["hi_hat"])
        algorithm_summaries.append(
            {
                "algorithm": algorithm,
                "case_count": len(case_order),
                "successful_cases": len([value for value in overall_f1s if value is not None]),
                "error_cases": error_cases,
                "mean_overall_f1": _safe_mean(overall_f1s),
                "mean_kick_f1": mean_kick_f1,
                "mean_snare_f1": mean_snare_f1,
                "mean_hi_hat_f1": mean_hi_hat_f1,
                "mean_core_f1": _safe_mean([mean_kick_f1, mean_snare_f1, mean_hi_hat_f1]),
                "mean_precision": _safe_mean(overall_precisions),
                "mean_recall": _safe_mean(overall_recalls),
                "mean_timing_mae_ms": _safe_mean(timing_maes),
                "median_timing_mae_ms": _safe_median(timing_maes),
                "snare_confusions": confusion_matrices["snare"][algorithm],
                "hi_hat_confusions": confusion_matrices["hi_hat"][algorithm],
            }
        )

    genre_names = sorted({tags[0] for tags in tag_map.values() if tags})
    genre_summaries: list[dict[str, Any]] = []
    for genre in genre_names:
        genre_case_ids = [case_id for case_id, tags in tag_map.items() if tags and tags[0] == genre]
        per_algorithm = []
        for algorithm in algorithms:
            per_algorithm.append(
                {
                    "algorithm": algorithm,
                    "mean_overall_f1": _safe_mean(
                        [overall_matrix[algorithm].get(case_id) for case_id in genre_case_ids]
                    ),
                    "mean_kick_f1": _safe_mean(
                        [lane_matrices["kick"][algorithm].get(case_id) for case_id in genre_case_ids]
                    ),
                    "mean_snare_f1": _safe_mean(
                        [lane_matrices["snare"][algorithm].get(case_id) for case_id in genre_case_ids]
                    ),
                    "mean_hi_hat_f1": _safe_mean(
                        [lane_matrices["hi_hat"][algorithm].get(case_id) for case_id in genre_case_ids]
                    ),
                }
            )
        genre_summaries.append(
            {
                "genre": genre,
                "case_ids": genre_case_ids,
                "algorithms": per_algorithm,
            }
        )

    return {
        "algorithm_summaries": algorithm_summaries,
        "case_order": case_order,
        "case_titles": case_titles,
        "overall_f1_matrix": overall_matrix,
        "kick_f1_matrix": lane_matrices["kick"],
        "snare_f1_matrix": lane_matrices["snare"],
        "hi_hat_f1_matrix": lane_matrices["hi_hat"],
        "genre_summaries": genre_summaries,
        "snare_confusion_matrix": confusion_matrices["snare"],
        "hi_hat_confusion_matrix": confusion_matrices["hi_hat"],
    }


def _score_to_color(value: float | None) -> str:
    if value is None:
        return "#2c3440"
    safe = max(0.0, min(float(value), 1.0))
    hue = int(round(120.0 * safe))
    light = 22 + int(round(32.0 * safe))
    return f"hsl({hue}, 70%, {light}%)"


def _count_to_color(value: int, max_value: int) -> str:
    if value <= 0 or max_value <= 0:
        return "#1f2430"
    ratio = max(0.0, min(float(value) / float(max_value), 1.0))
    hue = 12
    sat = 40 + int(round(45.0 * ratio))
    light = 18 + int(round(38.0 * ratio))
    return f"hsl({hue}, {sat}%, {light}%)"


def _case_short_label(case_id: str) -> str:
    return case_id.split("_", 1)[0]


def _svg_wrap(width: int, height: int, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">{body}</svg>'
    )


def _render_heatmap_svg(
    *,
    title: str,
    subtitle: str,
    row_labels: Sequence[str],
    col_labels: Sequence[str],
    values: Mapping[str, Mapping[str, float | None]],
) -> str:
    cell_w = 78
    cell_h = 34
    left = 190
    top = 100
    right = 40
    bottom = 70
    width = left + (cell_w * len(col_labels)) + right
    height = top + (cell_h * len(row_labels)) + bottom

    parts = [
        '<rect x="0" y="0" width="100%" height="100%" fill="#0d1117"/>',
        f'<text x="{left}" y="34" fill="#f0f6fc" font-size="24" font-family="Segoe UI, Arial">{html.escape(title)}</text>',
        f'<text x="{left}" y="58" fill="#9fb0c3" font-size="13" font-family="Segoe UI, Arial">{html.escape(subtitle)}</text>',
    ]

    for col_index, case_id in enumerate(col_labels):
        x = left + (col_index * cell_w) + (cell_w / 2)
        parts.append(
            f'<text x="{x:.1f}" y="{top - 20}" fill="#c9d1d9" font-size="12" text-anchor="middle" '
            f'font-family="Consolas, monospace">{html.escape(_case_short_label(case_id))}</text>'
        )

    for row_index, algorithm in enumerate(row_labels):
        y = top + (row_index * cell_h)
        parts.append(
            f'<text x="{left - 12}" y="{y + 22}" fill="#c9d1d9" font-size="12" text-anchor="end" '
            f'font-family="Segoe UI, Arial">{html.escape(algorithm)}</text>'
        )
        row = values.get(algorithm, {})
        for col_index, case_id in enumerate(col_labels):
            x = left + (col_index * cell_w)
            value = row.get(case_id)
            fill = _score_to_color(value)
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell_w - 2}" height="{cell_h - 2}" rx="5" '
                f'fill="{fill}" stroke="#11161d" stroke-width="1"/>'
            )
            label = "-" if value is None else f"{float(value):.2f}"
            parts.append(
                f'<text x="{x + (cell_w / 2) - 1:.1f}" y="{y + 22}" fill="#f8fbff" font-size="12" '
                f'text-anchor="middle" font-family="Consolas, monospace">{label}</text>'
            )

    legend_x = left
    legend_y = height - 38
    for idx, value in enumerate((0.0, 0.25, 0.5, 0.75, 1.0)):
        x = legend_x + (idx * 66)
        parts.append(
            f'<rect x="{x}" y="{legend_y}" width="54" height="14" rx="3" '
            f'fill="{_score_to_color(value)}" stroke="#11161d" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x + 27}" y="{legend_y + 29}" fill="#8ea1b5" font-size="11" text-anchor="middle" '
            f'font-family="Segoe UI, Arial">{value:.2f}</text>'
        )

    return _svg_wrap(width, height, "".join(parts))


def _render_grouped_bar_svg(
    *,
    title: str,
    subtitle: str,
    algorithms: Sequence[str],
    series: Sequence[tuple[str, Sequence[float | None], str]],
    y_label: str,
    max_value: float,
) -> str:
    width = max(760, 160 + (120 * len(algorithms)))
    height = 420
    left = 86
    right = 36
    top = 88
    bottom = 88
    chart_w = width - left - right
    chart_h = height - top - bottom

    parts = [
        '<rect x="0" y="0" width="100%" height="100%" fill="#0d1117"/>',
        f'<text x="{left}" y="34" fill="#f0f6fc" font-size="24" font-family="Segoe UI, Arial">{html.escape(title)}</text>',
        f'<text x="{left}" y="58" fill="#9fb0c3" font-size="13" font-family="Segoe UI, Arial">{html.escape(subtitle)}</text>',
    ]

    ticks = 5
    for idx in range(ticks + 1):
        value = (max_value / ticks) * idx
        y = top + chart_h - (chart_h * (value / max_value)) if max_value > 0 else top + chart_h
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" '
            f'stroke="#1f2833" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{left - 10}" y="{y + 4:.1f}" fill="#8ea1b5" font-size="11" text-anchor="end" '
            f'font-family="Consolas, monospace">{value:.2f}</text>'
        )

    group_w = chart_w / max(1, len(algorithms))
    bar_w = min(28.0, (group_w - 20.0) / max(1, len(series)))
    for alg_index, algorithm in enumerate(algorithms):
        group_left = left + (alg_index * group_w)
        for series_index, (_series_name, values, color) in enumerate(series):
            value = values[alg_index]
            if value is None:
                continue
            bar_height = chart_h * (float(value) / max_value) if max_value > 0 else 0.0
            x = group_left + 12 + (series_index * (bar_w + 8))
            y = top + chart_h - bar_height
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_height:.1f}" rx="5" '
                f'fill="{color}"/>'
            )
            parts.append(
                f'<text x="{x + (bar_w / 2):.1f}" y="{y - 6:.1f}" fill="#c9d1d9" font-size="11" '
                f'text-anchor="middle" font-family="Consolas, monospace">{float(value):.2f}</text>'
            )
        parts.append(
            f'<text x="{group_left + (group_w / 2):.1f}" y="{height - 30}" fill="#c9d1d9" font-size="11" '
            f'text-anchor="middle" font-family="Segoe UI, Arial">{html.escape(algorithm)}</text>'
        )

    parts.append(
        f'<text x="22" y="{top + (chart_h / 2):.1f}" fill="#8ea1b5" font-size="12" '
        f'font-family="Segoe UI, Arial" transform="rotate(-90 22 {top + (chart_h / 2):.1f})">'
        f'{html.escape(y_label)}</text>'
    )

    legend_x = left
    for idx, (series_name, _values, color) in enumerate(series):
        x = legend_x + (idx * 170)
        parts.append(f'<rect x="{x}" y="{height - 64}" width="16" height="16" rx="3" fill="{color}"/>')
        parts.append(
            f'<text x="{x + 24}" y="{height - 51}" fill="#c9d1d9" font-size="12" '
            f'font-family="Segoe UI, Arial">{html.escape(series_name)}</text>'
        )

    return _svg_wrap(width, height, "".join(parts))


def _render_timing_mae_svg(
    *,
    title: str,
    subtitle: str,
    algorithms: Sequence[str],
    values: Sequence[float | None],
) -> str:
    width = 860
    row_h = 34
    left = 180
    right = 48
    top = 88
    bottom = 40
    usable = [float(value) for value in values if value is not None]
    max_value = max(usable, default=1.0)
    height = top + bottom + (row_h * len(algorithms))

    parts = [
        '<rect x="0" y="0" width="100%" height="100%" fill="#0d1117"/>',
        f'<text x="{left}" y="34" fill="#f0f6fc" font-size="24" font-family="Segoe UI, Arial">{html.escape(title)}</text>',
        f'<text x="{left}" y="58" fill="#9fb0c3" font-size="13" font-family="Segoe UI, Arial">{html.escape(subtitle)}</text>',
    ]

    for idx, algorithm in enumerate(algorithms):
        y = top + (idx * row_h)
        parts.append(
            f'<text x="{left - 14}" y="{y + 21}" fill="#c9d1d9" font-size="12" text-anchor="end" '
            f'font-family="Segoe UI, Arial">{html.escape(algorithm)}</text>'
        )
        value = values[idx]
        if value is None:
            parts.append(f'<rect x="{left}" y="{y + 6}" width="12" height="16" rx="4" fill="#2c3440"/>')
            parts.append(
                f'<text x="{left + 22}" y="{y + 19}" fill="#8ea1b5" font-size="11" '
                f'font-family="Consolas, monospace">no data</text>'
            )
            continue
        bar_w = ((width - left - right) * float(value) / max_value) if max_value > 0 else 0.0
        parts.append(
            f'<rect x="{left}" y="{y + 6}" width="{bar_w:.1f}" height="16" rx="5" fill="#f2a65a"/>'
        )
        parts.append(
            f'<text x="{left + bar_w + 8:.1f}" y="{y + 19}" fill="#c9d1d9" font-size="11" '
            f'font-family="Consolas, monospace">{float(value):.1f} ms</text>'
        )

    return _svg_wrap(width, height, "".join(parts))


def _render_confusion_heatmap_svg(
    *,
    title: str,
    subtitle: str,
    row_labels: Sequence[str],
    col_labels: Sequence[str],
    values: Mapping[str, Mapping[str, int]],
) -> str:
    cell_w = 78
    cell_h = 34
    left = 190
    top = 100
    right = 40
    bottom = 40
    width = left + (cell_w * len(col_labels)) + right
    height = top + (cell_h * len(row_labels)) + bottom
    max_value = max([count for row in values.values() for count in row.values()], default=0)

    parts = [
        '<rect x="0" y="0" width="100%" height="100%" fill="#0d1117"/>',
        f'<text x="{left}" y="34" fill="#f0f6fc" font-size="24" font-family="Segoe UI, Arial">{html.escape(title)}</text>',
        f'<text x="{left}" y="58" fill="#9fb0c3" font-size="13" font-family="Segoe UI, Arial">{html.escape(subtitle)}</text>',
    ]

    for col_index, label in enumerate(col_labels):
        x = left + (col_index * cell_w) + (cell_w / 2)
        parts.append(
            f'<text x="{x:.1f}" y="{top - 20}" fill="#c9d1d9" font-size="12" text-anchor="middle" '
            f'font-family="Segoe UI, Arial">{html.escape(label)}</text>'
        )

    for row_index, algorithm in enumerate(row_labels):
        y = top + (row_index * cell_h)
        parts.append(
            f'<text x="{left - 12}" y="{y + 22}" fill="#c9d1d9" font-size="12" text-anchor="end" '
            f'font-family="Segoe UI, Arial">{html.escape(algorithm)}</text>'
        )
        row = values.get(algorithm, {})
        for col_index, label in enumerate(col_labels):
            x = left + (col_index * cell_w)
            count = int(row.get(label, 0))
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell_w - 2}" height="{cell_h - 2}" rx="5" '
                f'fill="{_count_to_color(count, max_value)}" stroke="#11161d" stroke-width="1"/>'
            )
            parts.append(
                f'<text x="{x + (cell_w / 2) - 1:.1f}" y="{y + 22}" fill="#f8fbff" font-size="12" '
                f'text-anchor="middle" font-family="Consolas, monospace">{count}</text>'
            )

    return _svg_wrap(width, height, "".join(parts))


def _summary_table_markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        "| Algorithm | Mean Overall F1 | Mean Kick F1 | Mean Snare F1 | Mean Hi-Hat F1 | Mean Timing MAE | Errors |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in summary["algorithm_summaries"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    item["algorithm"],
                    _format_score(item.get("mean_overall_f1")),
                    _format_score(item.get("mean_kick_f1")),
                    _format_score(item.get("mean_snare_f1")),
                    _format_score(item.get("mean_hi_hat_f1")),
                    _format_ms(item.get("mean_timing_mae_ms")),
                    ", ".join(item.get("error_cases", [])) or "-",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _case_table_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "| Case | Title | BPM | Tags | Focus |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for case in payload.get("cases", []):
        lines.append(
            "| "
            + " | ".join(
                [
                    case["case_id"],
                    case["title"],
                    str(int(round(float(case.get("bpm", 0.0))))),
                    ", ".join(case.get("tags", [])),
                    ", ".join(case.get("focus", [])),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _render_report_markdown(payload: Mapping[str, Any], summary: Mapping[str, Any]) -> str:
    lines = [
        "# Drum Benchmark Report",
        "",
        f"- Generated: `{payload['generated_at_utc']}`",
        f"- Fixtures: `{payload['fixtures_dir']}`",
        f"- Tolerance: `{payload['tolerance_ms']} ms`",
        f"- Algorithms: `{', '.join(payload['algorithms'])}`",
        "",
        "## Requirement",
        "",
        "Every benchmark run is incomplete unless the generated visualizations are reviewed.",
        "Open `report.html` or the SVG files directly from disk after each run.",
        "",
        "## Visualizations",
        "",
        "![Overall F1 Heatmap](overall_f1_heatmap.svg)",
        "",
        "![Kick F1 Heatmap](kick_f1_heatmap.svg)",
        "",
        "![Snare F1 Heatmap](snare_f1_heatmap.svg)",
        "",
        "![Hi-Hat F1 Heatmap](hi_hat_f1_heatmap.svg)",
        "",
        "![Algorithm Summary](algorithm_summary.svg)",
        "",
        "![Core Lane Summary](core_lane_summary.svg)",
        "",
        "![Timing MAE](timing_mae.svg)",
        "",
        "![Snare Confusion Heatmap](snare_confusion_heatmap.svg)",
        "",
        "![Hi-Hat Confusion Heatmap](hi_hat_confusion_heatmap.svg)",
        "",
        "## Algorithm Summary",
        "",
        _summary_table_markdown(summary),
        "",
        "## Cases",
        "",
        _case_table_markdown(payload),
        "",
    ]
    if payload.get("warnings"):
        lines.extend(["## Warnings", ""])
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")
    return "\n".join(lines)


def _render_html_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    head = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{html.escape(value)}</td>" for value in row)
        body_rows.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def _render_report_html(payload: Mapping[str, Any], summary: Mapping[str, Any]) -> str:
    alg_rows = [
        [
            item["algorithm"],
            _format_score(item.get("mean_overall_f1")),
            _format_score(item.get("mean_kick_f1")),
            _format_score(item.get("mean_snare_f1")),
            _format_score(item.get("mean_hi_hat_f1")),
            _format_ms(item.get("mean_timing_mae_ms")),
            ", ".join(item.get("error_cases", [])) or "-",
        ]
        for item in summary["algorithm_summaries"]
    ]
    case_rows = [
        [
            case["case_id"],
            case["title"],
            str(int(round(float(case.get("bpm", 0.0))))),
            ", ".join(case.get("tags", [])),
            ", ".join(case.get("focus", [])),
        ]
        for case in payload.get("cases", [])
    ]
    warning_list = "".join(f"<li>{html.escape(warning)}</li>" for warning in payload.get("warnings", []))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Drum Benchmark Report</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0d1117;
      --panel: #121923;
      --text: #e6edf3;
      --muted: #9fb0c3;
      --line: #263243;
      --accent: #56b6c2;
    }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      background: radial-gradient(circle at top left, #122033, #0d1117 55%);
      color: var(--text);
    }}
    main {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 32px 28px 48px;
    }}
    h1, h2 {{ margin: 0 0 12px; }}
    p, li {{ color: var(--muted); line-height: 1.45; }}
    .meta {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin: 18px 0 28px;
    }}
    .card {{
      background: rgba(18, 25, 35, 0.94);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px 16px;
    }}
    .viz {{
      margin: 18px 0 28px;
      background: rgba(18, 25, 35, 0.72);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 16px;
    }}
    img {{ max-width: 100%; display: block; border-radius: 12px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin: 12px 0 28px;
      background: rgba(18, 25, 35, 0.72);
      border: 1px solid var(--line);
      border-radius: 14px;
      overflow: hidden;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 10px 12px;
      text-align: left;
      vertical-align: top;
    }}
    th {{ color: var(--text); background: rgba(31, 40, 51, 0.76); }}
    td {{ color: var(--muted); }}
    .requirement {{
      border-left: 4px solid var(--accent);
      padding-left: 14px;
      margin: 16px 0 26px;
    }}
  </style>
</head>
<body>
  <main>
    <h1>Drum Benchmark Report</h1>
    <div class="meta">
      <div class="card"><strong>Generated</strong><br />{html.escape(str(payload['generated_at_utc']))}</div>
      <div class="card"><strong>Fixtures</strong><br />{html.escape(str(payload['fixtures_dir']))}</div>
      <div class="card"><strong>Tolerance</strong><br />{html.escape(str(payload['tolerance_ms']))} ms</div>
      <div class="card"><strong>Algorithms</strong><br />{html.escape(', '.join(payload['algorithms']))}</div>
    </div>
    <div class="requirement">
      <h2>Requirement</h2>
      <p>Every benchmark run is incomplete unless the generated visualizations are reviewed. Open this file or the SVG charts directly from disk after each run.</p>
    </div>
    <div class="viz"><h2>Overall F1 by Case</h2><img src="overall_f1_heatmap.svg" alt="Overall F1 heatmap" /></div>
    <div class="viz"><h2>Kick F1 by Case</h2><img src="kick_f1_heatmap.svg" alt="Kick F1 heatmap" /></div>
    <div class="viz"><h2>Snare F1 by Case</h2><img src="snare_f1_heatmap.svg" alt="Snare F1 heatmap" /></div>
    <div class="viz"><h2>Hi-Hat F1 by Case</h2><img src="hi_hat_f1_heatmap.svg" alt="Hi-hat F1 heatmap" /></div>
    <div class="viz"><h2>Aggregate Algorithm Summary</h2><img src="algorithm_summary.svg" alt="Algorithm summary chart" /></div>
    <div class="viz"><h2>Kick / Snare / Hi-Hat Summary</h2><img src="core_lane_summary.svg" alt="Core lane summary chart" /></div>
    <div class="viz"><h2>Timing MAE</h2><img src="timing_mae.svg" alt="Timing MAE chart" /></div>
    <div class="viz"><h2>Snare Confusions</h2><img src="snare_confusion_heatmap.svg" alt="Snare confusion heatmap" /></div>
    <div class="viz"><h2>Hi-Hat Confusions</h2><img src="hi_hat_confusion_heatmap.svg" alt="Hi-hat confusion heatmap" /></div>
    <h2>Algorithm Summary</h2>
    {_render_html_table(
        ["Algorithm", "Mean Overall F1", "Mean Kick F1", "Mean Snare F1", "Mean Hi-Hat F1", "Mean Timing MAE", "Errors"],
        alg_rows,
    )}
    <h2>Cases</h2>
    {_render_html_table(["Case", "Title", "BPM", "Tags", "Focus"], case_rows)}
    {"<h2>Warnings</h2><ul>" + warning_list + "</ul>" if warning_list else ""}
  </main>
</body>
</html>"""


def write_suite_outputs(
    payload: Mapping[str, Any],
    *,
    output_root: Path | str | None = None,
    label: str | None = None,
) -> Path:
    root = Path(output_root) if output_root is not None else _default_output_root()
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = timestamp if not label else f"{timestamp}_{_slugify(label)}"
    out_dir = root / run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = summarize_suite_results(payload)
    enriched = {**payload, "summary": summary}
    algorithms = list(payload.get("algorithms", []))
    case_order = list(summary["case_order"])

    artifacts = {
        "overall_f1_heatmap.svg": _render_heatmap_svg(
            title="Overall F1 by Case",
            subtitle="Rows are algorithms, columns are benchmark cases. Higher is better.",
            row_labels=algorithms,
            col_labels=case_order,
            values=summary["overall_f1_matrix"],
        ),
        "kick_f1_heatmap.svg": _render_heatmap_svg(
            title="Kick F1 by Case",
            subtitle="Primary review chart for kick stability, especially double-bass and syncopated pocket cases.",
            row_labels=algorithms,
            col_labels=case_order,
            values=summary["kick_f1_matrix"],
        ),
        "snare_f1_heatmap.svg": _render_heatmap_svg(
            title="Snare F1 by Case",
            subtitle="Direct snare accuracy is isolated here because it is the recurring complaint.",
            row_labels=algorithms,
            col_labels=case_order,
            values=summary["snare_f1_matrix"],
        ),
        "hi_hat_f1_heatmap.svg": _render_heatmap_svg(
            title="Hi-Hat F1 by Case",
            subtitle="Mandatory review chart because hats are currently collapsing into snare or kick.",
            row_labels=algorithms,
            col_labels=case_order,
            values=summary["hi_hat_f1_matrix"],
        ),
        "algorithm_summary.svg": _render_grouped_bar_svg(
            title="Aggregate Algorithm Summary",
            subtitle="Mean overall F1 vs mean kick/snare/hi-hat F1 across the full rendered fixture set.",
            algorithms=algorithms,
            series=(
                (
                    "Mean overall F1",
                    [item.get("mean_overall_f1") for item in summary["algorithm_summaries"]],
                    "#56b6c2",
                ),
                (
                    "Mean core-lane F1",
                    [item.get("mean_core_f1") for item in summary["algorithm_summaries"]],
                    "#ff8f5a",
                ),
            ),
            y_label="score",
            max_value=1.0,
        ),
        "core_lane_summary.svg": _render_grouped_bar_svg(
            title="Kick / Snare / Hi-Hat Summary",
            subtitle="Lane-specific means for the three priority classes.",
            algorithms=algorithms,
            series=(
                (
                    "Kick F1",
                    [item.get("mean_kick_f1") for item in summary["algorithm_summaries"]],
                    "#7bd389",
                ),
                (
                    "Snare F1",
                    [item.get("mean_snare_f1") for item in summary["algorithm_summaries"]],
                    "#ff8f5a",
                ),
                (
                    "Hi-Hat F1",
                    [item.get("mean_hi_hat_f1") for item in summary["algorithm_summaries"]],
                    "#5aa9e6",
                ),
            ),
            y_label="score",
            max_value=1.0,
        ),
        "timing_mae.svg": _render_timing_mae_svg(
            title="Mean Timing MAE by Algorithm",
            subtitle="Mean matched onset absolute timing error across successful cases. Lower is better.",
            algorithms=algorithms,
            values=[item.get("mean_timing_mae_ms") for item in summary["algorithm_summaries"]],
        ),
        "snare_confusion_heatmap.svg": _render_confusion_heatmap_svg(
            title="Snare Confusion Heatmap",
            subtitle="Counts of reference snare hits that landed in another lane within the time tolerance.",
            row_labels=algorithms,
            col_labels=[label for label in BENCHMARK_CLASS_ORDER if label != "snare"],
            values=summary["snare_confusion_matrix"],
        ),
        "hi_hat_confusion_heatmap.svg": _render_confusion_heatmap_svg(
            title="Hi-Hat Confusion Heatmap",
            subtitle="Counts of reference hi-hat hits that landed in another lane within the time tolerance.",
            row_labels=algorithms,
            col_labels=[label for label in BENCHMARK_CLASS_ORDER if label != "hi_hat"],
            values=summary["hi_hat_confusion_matrix"],
        ),
        "report.md": _render_report_markdown(enriched, summary),
        "report.html": _render_report_html(enriched, summary),
        "summary.json": json.dumps(enriched, indent=2),
    }

    for name, content in artifacts.items():
        (out_dir / name).write_text(content, encoding="utf-8")

    missing = [name for name in REQUIRED_VISUALIZATION_FILES if not (out_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"benchmark run incomplete: missing visualization artifacts {', '.join(missing)}")

    latest_marker = out_dir.parent.parent / "LATEST_RUN.txt"
    latest_marker.parent.mkdir(parents=True, exist_ok=True)
    latest_marker.write_text(str(out_dir), encoding="utf-8")
    return out_dir
