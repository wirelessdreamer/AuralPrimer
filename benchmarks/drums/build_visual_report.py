from __future__ import annotations

from datetime import datetime
import html
import json
import os
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[2]
BENCH_ROOT = ROOT / "benchmarks" / "drums"
OUT_PATH = BENCH_ROOT / "benchmark_visual_report.html"
SHOOTOUT_LATEST = BENCH_ROOT / "shootouts" / "LATEST_RUN.txt"

MILESTONE_PATHS = {
    "core_lanes": BENCH_ROOT / "runs" / "20260310_113817_hybrid-research-core-lanes" / "summary.json",
    "hybrid_mvp": BENCH_ROOT / "runs" / "20260310_124533_hybrid-mvp" / "summary.json",
    "adaptive_refined": BENCH_ROOT / "runs" / "20260311_134907_adaptive-double-bass-fix-v2" / "summary.json",
    "retry_validation": BENCH_ROOT / "runs" / "20260310_213515_final-retry-validation" / "summary.json",
    "king_holdout": BENCH_ROOT / "king_in_zion_final_retry_validation.json",
    "tracker": BENCH_ROOT / "HYBRID_RESEARCH_AND_TRACKER.md",
    "process": BENCH_ROOT / "PROCESS.md",
}

PALETTE = [
    "#56b5ff",
    "#4ad0a6",
    "#f1c75b",
    "#ff8b5e",
    "#b28dff",
    "#7dd3fc",
    "#d1fae5",
    "#f9a8d4",
]

ALGORITHM_LABELS = {
    "adaptive_beat_grid": "Adaptive beat grid",
    "adaptive_beat_grid_multilabel": "Adaptive beat grid multilabel",
    "aural_onset": "Aural onset",
    "beat_conditioned_multiband_decoder": "Beat-conditioned multiband",
    "combined_filter": "Combined filter",
    "dsp_bandpass": "DSP bandpass",
    "dsp_bandpass_improved": "DSP bandpass improved",
    "dsp_spectral_flux": "DSP spectral flux",
    "hybrid_kick_grid": "Hybrid kick grid",
    "librosa_superflux": "Librosa superflux",
    "multi_resolution": "Multi-resolution",
    "multi_resolution_template": "Multi-resolution template",
    "onset_aligned": "Onset aligned",
    "probabilistic_pattern": "Probabilistic pattern",
    "spectral_flux_multiband": "Spectral flux multiband",
    "spectral_template_multipass": "Template multipass",
    "spectral_template_with_grid": "Template + grid",
    "template_xcorr": "Template xcorr",
}

DISTORTION_NOTES = {
    "beat_conditioned_multiband_decoder": "Best trusted method collapses on suspect Suno reference. Strong evidence that the reference is bad, not that the decoder regressed.",
    "hybrid_kick_grid": "Another top trusted method gets pushed down sharply by suspect Suno MIDI. The tuning signal would move in the wrong direction if Suno were treated as truth.",
    "dsp_bandpass_improved": "Legacy DSP path gets artificially promoted by suspect Suno MIDI. This is exactly the kind of false optimization target the shootout was designed to catch.",
    "dsp_spectral_flux": "Another weak legacy path looks stronger than it should when measured against suspect Suno MIDI.",
    "combined_filter": "The only material positive delta. It is still weak on trusted synthetic, so the Suno reference is rewarding the wrong behavior.",
    "adaptive_beat_grid_multilabel": "Looks best on suspect Suno, but only at 0.202 overall. That is not a real absolute win; it is a distorted ranking.",
}

RESEARCH_SOURCES = [
    {
        "title": "Wu et al. 2018 - A Review of Automatic Drum Transcription",
        "href": "https://doi.org/10.1109/TASLP.2018.2830113",
        "why": "Frames the main ADT families and reinforces that overlap, class imbalance, and dataset mismatch remain the core failure sources.",
    },
    {
        "title": "Vogl et al. 2017 - Drum transcription from polyphonic music with recurrent neural networks",
        "href": "https://doi.org/10.1109/ICASSP.2017.7952146",
        "why": "Supports sequence-aware decoding. The current kick, snare, and hat failures are sequence failures, not isolated frame errors.",
    },
    {
        "title": "Yeh et al. 2023 - Joint Drum Transcription and Metrical Analysis Based on Periodicity-Aware Multi-Task Learning",
        "href": "https://doi.org/10.1109/APSIPAASC58517.2023.10317285",
        "why": "Supports using metrical context as a model input rather than a blunt post-hoc quantizer.",
    },
    {
        "title": "Ishizuka et al. 2022 - Global Structure-Aware Drum Transcription Based on Self-Attention Mechanisms",
        "href": "https://doi.org/10.3390/signals2030031",
        "why": "Adds support for longer-range structure cues, especially for odd meter, repeated sections, and phrase-level pattern continuity.",
    },
    {
        "title": "Roebel et al. 2015 - Drum transcription using partially fixed non-negative matrix factorization",
        "href": "https://doi.org/10.1109/EUSIPCO.2015.7362590",
        "why": "Keeps template and factorization methods relevant for local overlap disambiguation, especially kick + hat and snare + hat collisions.",
    },
    {
        "title": "Foscarin et al. 2024 - STAR Drums",
        "href": "https://doi.org/10.5334/TISMIR.244",
        "why": "Reinforces that benchmark quality matters. Useful for future external evaluation, but not a replacement for the local rendered suite and audited holdouts.",
    },
    {
        "title": "Heyen et al. 2023 - High-Quality and Reproducible Automatic Drum Transcription from Crowdsourced Data",
        "href": "https://doi.org/10.3390/app13031549",
        "why": "Aligns with the repo's insistence on reproducible evaluation and static report review after every run.",
    },
    {
        "title": "Elhussein et al. 2025 - Enhanced Automatic Drum Transcription via Drum Stem Source Separation",
        "href": "http://arxiv.org/abs/2509.24853v1",
        "why": "Still emerging, but worth tracking because separation-assisted ADT could reduce overlap failures on dense mixed audio.",
    },
]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text("utf-8"))


def _load_json_any_encoding(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            return json.loads(raw.decode(encoding))
        except Exception:
            continue
    raise RuntimeError(f"unable to decode json file: {path}")


def _read_latest_shootout_dir() -> Path:
    latest = SHOOTOUT_LATEST.read_text("utf-8").strip()
    if not latest:
        raise RuntimeError(f"empty latest shootout marker: {SHOOTOUT_LATEST}")
    return Path(latest)


def _rel_href(target: Path) -> str:
    return os.path.relpath(target, OUT_PATH.parent).replace("\\", "/")


def _format_score(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.3f}"


def _format_ms(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.1f} ms"


def _format_delta(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):+.3f}"


def _pretty_algorithm(name: str) -> str:
    return ALGORITHM_LABELS.get(name, name.replace("_", " "))


def _score_color(value: float, *, positive: bool = True) -> str:
    safe = max(0.0, min(abs(float(value)), 1.0))
    hue = 162 if positive else 8
    sat = 56 + int(round(24.0 * safe))
    light = 26 + int(round(22.0 * safe))
    return f"hsl({hue}, {sat}%, {light}%)"


def _svg_wrap(width: int, height: int, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">{body}</svg>'
    )


def _render_horizontal_bar_chart(
    *,
    title: str,
    subtitle: str,
    items: Sequence[tuple[str, float]],
    max_value: float,
    color: str,
    width: int = 920,
) -> str:
    left = 290
    top = 84
    row_h = 30
    bar_h = 20
    chart_w = width - left - 80
    height = top + (len(items) * row_h) + 28
    parts = [
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="18" fill="#121821" stroke="#243041" />',
        f'<text x="26" y="36" fill="#f2f6fb" font-size="24" font-family="Segoe UI, Arial">{html.escape(title)}</text>',
        f'<text x="26" y="60" fill="#91a4bb" font-size="13" font-family="Segoe UI, Arial">{html.escape(subtitle)}</text>',
    ]
    for idx, (label, raw_value) in enumerate(items):
        value = max(0.0, float(raw_value))
        y = top + idx * row_h
        bar_w = chart_w * min(value / max(max_value, 1e-6), 1.0)
        parts.append(
            f'<text x="26" y="{y + 15}" fill="#d9e2ec" font-size="13" font-family="Segoe UI, Arial">{html.escape(label)}</text>'
        )
        parts.append(f'<rect x="{left}" y="{y}" width="{chart_w}" height="{bar_h}" rx="10" fill="#1a2230" />')
        parts.append(f'<rect x="{left}" y="{y}" width="{bar_w:.2f}" height="{bar_h}" rx="10" fill="{color}" />')
        parts.append(
            f'<text x="{left + chart_w + 10}" y="{y + 15}" fill="#d9e2ec" font-size="12" font-family="Consolas, monospace">{value:.3f}</text>'
        )
    return _svg_wrap(width, height, "".join(parts))


def _render_diverging_bar_chart(
    *,
    title: str,
    subtitle: str,
    items: Sequence[tuple[str, float]],
    width: int = 920,
    abs_max: float | None = None,
    value_format: str = "{:+.3f}",
) -> str:
    left = 290
    top = 84
    row_h = 30
    bar_h = 18
    chart_w = width - left - 80
    zero_x = left + chart_w / 2.0
    max_mag = abs_max if abs_max is not None else max(abs(float(value)) for _label, value in items)
    max_mag = max(max_mag, 1e-6)
    height = top + (len(items) * row_h) + 28
    parts = [
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="18" fill="#121821" stroke="#243041" />',
        f'<text x="26" y="36" fill="#f2f6fb" font-size="24" font-family="Segoe UI, Arial">{html.escape(title)}</text>',
        f'<text x="26" y="60" fill="#91a4bb" font-size="13" font-family="Segoe UI, Arial">{html.escape(subtitle)}</text>',
        f'<line x1="{zero_x:.2f}" y1="{top - 8}" x2="{zero_x:.2f}" y2="{height - 18}" stroke="#66768c" stroke-width="1.5" />',
    ]
    for idx, (label, raw_value) in enumerate(items):
        value = float(raw_value)
        y = top + idx * row_h
        bar_w = (chart_w / 2.0) * min(abs(value) / max_mag, 1.0)
        x = zero_x if value >= 0 else zero_x - bar_w
        fill = _score_color(value, positive=value >= 0)
        parts.append(
            f'<text x="26" y="{y + 14}" fill="#d9e2ec" font-size="13" font-family="Segoe UI, Arial">{html.escape(label)}</text>'
        )
        parts.append(f'<rect x="{left}" y="{y}" width="{chart_w}" height="{bar_h}" rx="9" fill="#1a2230" />')
        parts.append(f'<rect x="{x:.2f}" y="{y}" width="{bar_w:.2f}" height="{bar_h}" rx="9" fill="{fill}" />')
        parts.append(
            f'<text x="{left + chart_w + 10}" y="{y + 14}" fill="#d9e2ec" font-size="12" font-family="Consolas, monospace">{html.escape(value_format.format(value))}</text>'
        )
    return _svg_wrap(width, height, "".join(parts))


def _render_grouped_bar_chart(
    *,
    title: str,
    subtitle: str,
    categories: Sequence[str],
    series: Sequence[tuple[str, Sequence[float], str]],
    max_value: float,
    width: int = 920,
) -> str:
    left = 84
    right = 30
    top = 86
    bottom = 88
    chart_w = width - left - right
    chart_h = 320
    height = top + chart_h + bottom
    group_w = chart_w / max(1, len(categories))
    inner_gap = 8
    bar_w = (group_w - 20 - (inner_gap * max(0, len(series) - 1))) / max(1, len(series))
    parts = [
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="18" fill="#121821" stroke="#243041" />',
        f'<text x="26" y="36" fill="#f2f6fb" font-size="24" font-family="Segoe UI, Arial">{html.escape(title)}</text>',
        f'<text x="26" y="60" fill="#91a4bb" font-size="13" font-family="Segoe UI, Arial">{html.escape(subtitle)}</text>',
        f'<line x1="{left}" y1="{top + chart_h}" x2="{left + chart_w}" y2="{top + chart_h}" stroke="#66768c" stroke-width="1.5" />',
    ]
    for tick in range(6):
        value = max_value * tick / 5.0
        y = top + chart_h - (chart_h * (value / max_value if max_value > 0 else 0.0))
        parts.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left + chart_w}" y2="{y:.2f}" stroke="#233041" stroke-width="1" />')
        parts.append(
            f'<text x="{left - 12}" y="{y + 4:.2f}" text-anchor="end" fill="#91a4bb" font-size="11" font-family="Consolas, monospace">{value:.2f}</text>'
        )
    for cat_idx, category in enumerate(categories):
        group_x = left + cat_idx * group_w + 10
        for series_idx, (_label, values, fill) in enumerate(series):
            value = float(values[cat_idx])
            h = chart_h * min(max(value / max_value, 0.0), 1.0)
            x = group_x + series_idx * (bar_w + inner_gap)
            y = top + chart_h - h
            parts.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_w:.2f}" height="{h:.2f}" rx="8" fill="{fill}" />')
        label_x = left + cat_idx * group_w + (group_w / 2.0)
        parts.append(
            f'<text x="{label_x:.2f}" y="{top + chart_h + 22}" text-anchor="middle" fill="#d9e2ec" font-size="11" font-family="Segoe UI, Arial">{html.escape(category)}</text>'
        )
    legend_x = 26
    legend_y = height - 28
    for idx, (label, _values, fill) in enumerate(series):
        x = legend_x + idx * 140
        parts.append(f'<rect x="{x}" y="{legend_y - 10}" width="14" height="14" rx="4" fill="{fill}" />')
        parts.append(
            f'<text x="{x + 20}" y="{legend_y + 1}" fill="#d9e2ec" font-size="12" font-family="Segoe UI, Arial">{html.escape(label)}</text>'
        )
    return _svg_wrap(width, height, "".join(parts))


def _render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    head = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    body = []
    for row in rows:
        body.append("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>")
    return "<table><thead><tr>" + head + "</tr></thead><tbody>" + "".join(body) + "</tbody></table>"


def _summary_lookup(run_summary: dict[str, Any], algorithm: str) -> dict[str, Any]:
    for item in run_summary["summary"]["algorithm_summaries"]:
        if item["algorithm"] == algorithm:
            return item
    raise KeyError(f"algorithm not present in summary: {algorithm}")


def _casewise_best(run_summary: dict[str, Any], algorithms: Sequence[str]) -> dict[str, Any]:
    summary = run_summary["summary"]
    case_order = summary["case_order"]

    def mean_of_max(matrix_key: str) -> float:
        matrix = summary[matrix_key]
        values = [max(float(matrix[algorithm][case_id]) for algorithm in algorithms) for case_id in case_order]
        return sum(values) / len(values)

    kick = mean_of_max("kick_f1_matrix")
    snare = mean_of_max("snare_f1_matrix")
    hi_hat = mean_of_max("hi_hat_f1_matrix")
    return {
        "algorithm": "casewise_best_of_two",
        "mean_overall_f1": mean_of_max("overall_f1_matrix"),
        "mean_kick_f1": kick,
        "mean_snare_f1": snare,
        "mean_hi_hat_f1": hi_hat,
        "mean_core_f1": (kick + snare + hi_hat) / 3.0,
        "mean_timing_mae_ms": None,
    }


def _milestone_entry(label: str, summary_item: dict[str, Any], note: str) -> dict[str, Any]:
    return {
        "label": label,
        "overall": float(summary_item["mean_overall_f1"]),
        "kick": float(summary_item["mean_kick_f1"]),
        "snare": float(summary_item["mean_snare_f1"]),
        "hi_hat": float(summary_item["mean_hi_hat_f1"]),
        "timing": None if summary_item.get("mean_timing_mae_ms") is None else float(summary_item["mean_timing_mae_ms"]),
        "note": note,
    }


def _card(title: str, value: str, detail: str) -> str:
    return (
        '<div class="card stat-card">'
        f"<div class=\"eyebrow\">{html.escape(title)}</div>"
        f"<div class=\"stat-value\">{html.escape(value)}</div>"
        f"<p>{html.escape(detail)}</p>"
        "</div>"
    )


def _link_item(label: str, target: str) -> str:
    return f'<li><a href="{html.escape(target)}">{html.escape(label)}</a></li>'


def build_report() -> Path:
    shootout_dir = _read_latest_shootout_dir()
    shootout = _load_json(shootout_dir / "summary.json")

    trusted_summaries = sorted(
        shootout["trusted"]["summary"]["algorithm_summaries"],
        key=lambda item: item["mean_overall_f1"],
        reverse=True,
    )
    suspect_summaries = sorted(
        shootout["suspect"]["summary"]["algorithm_summaries"],
        key=lambda item: item["mean_overall_f1"],
        reverse=True,
    )
    comparison_rows = shootout["comparison"]["rows"]

    core_lanes = _load_json(MILESTONE_PATHS["core_lanes"])
    hybrid_mvp = _load_json(MILESTONE_PATHS["hybrid_mvp"])
    adaptive_refined = _load_json(MILESTONE_PATHS["adaptive_refined"])
    retry_validation = _load_json(MILESTONE_PATHS["retry_validation"])
    king_holdout = _load_json_any_encoding(MILESTONE_PATHS["king_holdout"])

    milestone_entries = [
        _milestone_entry(
            "Aural baseline",
            _summary_lookup(core_lanes, "aural_onset"),
            "Strongest early baseline on snare feel, but effectively zero hi-hat recovery.",
        ),
        _milestone_entry(
            "Adaptive baseline",
            _summary_lookup(core_lanes, "adaptive_beat_grid"),
            "Better kick timing and slightly better overall F1, but hats still collapse into kick or snare.",
        ),
        _milestone_entry(
            "Case-wise upper bound",
            _casewise_best(core_lanes, ["aural_onset", "adaptive_beat_grid"]),
            "Naive fusion barely helped. That forced a structural model change instead of more ensembling.",
        ),
        _milestone_entry(
            "Hybrid MVP",
            _summary_lookup(hybrid_mvp, "beat_conditioned_multiband_decoder"),
            "First real step-change. Hi-hat finally becomes usable while overall and core-lane F1 jump together.",
        ),
        _milestone_entry(
            "Adaptive refined",
            _summary_lookup(adaptive_refined, "adaptive_beat_grid"),
            "Real kick gain, especially on double-bass material, but snare tradeoff means it is not the universal winner.",
        ),
        _milestone_entry(
            "Retained retry",
            _summary_lookup(retry_validation, "beat_conditioned_multiband_decoder"),
            "Conservative dual-hit retry gives a small but real retained improvement without blowing up the holdout.",
        ),
        _milestone_entry(
            "Current trusted best",
            trusted_summaries[0],
            "Best current performer on the rendered trusted suite. This remains the optimization gate.",
        ),
    ]

    trusted_top_items = [(_pretty_algorithm(item["algorithm"]), item["mean_overall_f1"]) for item in trusted_summaries[:8]]
    suspect_top_items = [(_pretty_algorithm(item["algorithm"]), item["mean_overall_f1"]) for item in suspect_summaries[:8]]

    paired_top_rows = comparison_rows[:]
    paired_top_rows.sort(key=lambda row: row["trusted"]["mean_overall_f1"], reverse=True)
    paired_top_rows = paired_top_rows[:6]

    delta_rows = sorted(
        comparison_rows,
        key=lambda row: row["delta_suspect_minus_trusted"]["mean_overall_f1"],
        reverse=True,
    )
    rank_shift_rows = sorted(comparison_rows, key=lambda row: abs(row["rank_shift"]), reverse=True)

    biggest_false_promotion = min(comparison_rows, key=lambda row: row["rank_shift"])
    biggest_false_demotion = max(comparison_rows, key=lambda row: row["rank_shift"])

    holdout_results = {row["algorithm"]: row for row in king_holdout["results"]}
    holdout_algorithms = [
        "beat_conditioned_multiband_decoder",
        "adaptive_beat_grid",
        "dsp_bandpass_improved",
    ]
    holdout_series = []
    for idx, algorithm in enumerate(holdout_algorithms):
        row = holdout_results.get(algorithm)
        if not row:
            continue
        holdout_series.append(
            (
                _pretty_algorithm(algorithm),
                [
                    float(row["overall"]["f1"]),
                    float(row["per_class"]["kick"]["f1"]),
                    float(row["per_class"]["snare"]["f1"]),
                    float(row["per_class"]["hi_hat"]["f1"]),
                ],
                PALETTE[idx],
            )
        )

    milestone_chart = _render_grouped_bar_chart(
        title="Research milestones across core lanes",
        subtitle="How the local program moved from early baselines to the current trusted winner.",
        categories=[entry["label"] for entry in milestone_entries],
        series=[
            ("Overall", [entry["overall"] for entry in milestone_entries], "#56b5ff"),
            ("Kick", [entry["kick"] for entry in milestone_entries], "#4ad0a6"),
            ("Snare", [entry["snare"] for entry in milestone_entries], "#f1c75b"),
            ("Hi-hat", [entry["hi_hat"] for entry in milestone_entries], "#ff8b5e"),
        ],
        max_value=0.5,
        width=1020,
    )

    timing_chart = _render_horizontal_bar_chart(
        title="Timing budget across milestones",
        subtitle="Lower is better. The hybrid gains did not come from a large timing penalty.",
        items=[(entry["label"], entry["timing"]) for entry in milestone_entries if entry["timing"] is not None],
        max_value=35.0,
        color="#7dd3fc",
        width=1020,
    )

    paired_chart = _render_grouped_bar_chart(
        title="Top trusted algorithms measured against trusted vs suspect references",
        subtitle="The same algorithms are scored twice: once against known-good rendered MIDI and once against suspect Suno MIDI.",
        categories=[_pretty_algorithm(row["algorithm"]) for row in paired_top_rows],
        series=[
            ("Trusted synthetic", [row["trusted"]["mean_overall_f1"] for row in paired_top_rows], "#4ad0a6"),
            ("Suspect Suno", [row["suspect"]["mean_overall_f1"] for row in paired_top_rows], "#ff8b5e"),
        ],
        max_value=0.4,
        width=1020,
    )

    trusted_chart = _render_horizontal_bar_chart(
        title="Trusted synthetic leaderboard",
        subtitle="18 algorithms on the rendered known-good fixture suite.",
        items=trusted_top_items,
        max_value=0.36,
        color="#4ad0a6",
        width=1020,
    )

    suspect_chart = _render_horizontal_bar_chart(
        title="Suspect Suno leaderboard",
        subtitle="The same 18 algorithms, but scored against the suspect King in Zion Suno MIDI reference.",
        items=suspect_top_items,
        max_value=0.24,
        color="#ff8b5e",
        width=1020,
    )

    delta_chart = _render_diverging_bar_chart(
        title="Overall F1 distortion when Suno MIDI is used as reference",
        subtitle="Positive means the algorithm looks better on suspect Suno than on trusted synthetic. Negative means it gets punished by the suspect reference.",
        items=[
            (_pretty_algorithm(row["algorithm"]), row["delta_suspect_minus_trusted"]["mean_overall_f1"])
            for row in delta_rows
        ],
        width=1020,
        abs_max=0.18,
        value_format="{:+.3f}",
    )

    rank_shift_chart = _render_diverging_bar_chart(
        title="Leaderboard rank shift caused by suspect Suno MIDI",
        subtitle="Negative means promoted by suspect Suno. Positive means demoted by suspect Suno.",
        items=[(_pretty_algorithm(row["algorithm"]), float(row["rank_shift"])) for row in rank_shift_rows],
        width=1020,
        abs_max=12.0,
        value_format="{:+.0f}",
    )

    holdout_chart = _render_grouped_bar_chart(
        title="Historic King in Zion holdout view",
        subtitle="Useful as a diagnostic snapshot, but no longer acceptable as truth because the Suno MIDI reference is suspect.",
        categories=["Overall", "Kick", "Snare", "Hi-hat"],
        series=holdout_series,
        max_value=0.35,
        width=1020,
    )

    milestone_rows = [
        [
            entry["label"],
            _format_score(entry["overall"]),
            _format_score(entry["kick"]),
            _format_score(entry["snare"]),
            _format_score(entry["hi_hat"]),
            _format_ms(entry["timing"]),
            entry["note"],
        ]
        for entry in milestone_entries
    ]

    distortion_focus = []
    for algorithm in [
        "beat_conditioned_multiband_decoder",
        "hybrid_kick_grid",
        "adaptive_beat_grid_multilabel",
        "dsp_bandpass_improved",
        "dsp_spectral_flux",
        "combined_filter",
    ]:
        row = next(item for item in comparison_rows if item["algorithm"] == algorithm)
        distortion_focus.append(
            [
                _pretty_algorithm(algorithm),
                f"#{row['trusted_rank']}",
                f"#{row['suspect_rank']}",
                _format_delta(row["delta_suspect_minus_trusted"]["mean_overall_f1"]),
                DISTORTION_NOTES.get(algorithm, ""),
            ]
        )

    holdout_rows = []
    for algorithm in holdout_algorithms:
        row = holdout_results.get(algorithm)
        if not row:
            continue
        holdout_rows.append(
            [
                _pretty_algorithm(algorithm),
                _format_score(row["overall"]["f1"]),
                _format_score(row["per_class"]["kick"]["f1"]),
                _format_score(row["per_class"]["snare"]["f1"]),
                _format_score(row["per_class"]["hi_hat"]["f1"]),
                _format_ms(row["overall"]["timing_mae_ms"]),
            ]
        )

    generated_local = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    trusted_best = trusted_summaries[0]
    suspect_best = suspect_summaries[0]

    source_links = [
        _link_item("Latest shootout report.html", _rel_href(shootout_dir / "report.html")),
        _link_item("Latest shootout report.md", _rel_href(shootout_dir / "report.md")),
        _link_item("Latest shootout summary.json", _rel_href(shootout_dir / "summary.json")),
        _link_item("Hybrid research tracker", _rel_href(MILESTONE_PATHS["tracker"])),
        _link_item("Benchmark process", _rel_href(MILESTONE_PATHS["process"])),
        _link_item(
            "Hybrid research core-lanes report",
            _rel_href(MILESTONE_PATHS["core_lanes"].parent / "report.html"),
        ),
        _link_item(
            "Hybrid MVP report",
            _rel_href(MILESTONE_PATHS["hybrid_mvp"].parent / "report.html"),
        ),
        _link_item(
            "Adaptive refined report",
            _rel_href(MILESTONE_PATHS["adaptive_refined"].parent / "report.html"),
        ),
        _link_item(
            "Final retry validation report",
            _rel_href(MILESTONE_PATHS["retry_validation"].parent / "report.html"),
        ),
        _link_item(
            "Historic King in Zion holdout JSON",
            _rel_href(MILESTONE_PATHS["king_holdout"]),
        ),
    ]

    research_links = "".join(
        f'<li><a href="{html.escape(item["href"])}">{html.escape(item["title"])}</a><span>{html.escape(item["why"])}</span></li>'
        for item in RESEARCH_SOURCES
    )

    html_out = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AuralPrimer Drum Benchmark Visual Report</title>
  <style>
    :root {{
      --bg: #081018;
      --panel: #101924;
      --panel-2: #121d2a;
      --text: #f2f6fb;
      --muted: #93a7bc;
      --line: #223144;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(86,181,255,0.12), transparent 28%),
        radial-gradient(circle at top right, rgba(74,208,166,0.10), transparent 22%),
        linear-gradient(180deg, #081018 0%, #09131d 30%, #081018 100%);
    }}
    a {{ color: #8cc8ff; }}
    .page {{
      width: min(1200px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 64px;
    }}
    .hero {{
      border: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(18,29,42,0.95), rgba(11,18,27,0.95));
      border-radius: 24px;
      padding: 28px 28px 22px;
      box-shadow: 0 20px 60px rgba(0,0,0,0.28);
    }}
    .eyebrow {{
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 10px;
    }}
    h1 {{
      font-size: clamp(32px, 4.5vw, 54px);
      line-height: 1.02;
      margin: 0 0 14px;
      letter-spacing: -0.03em;
    }}
    h2 {{ font-size: 26px; margin: 0 0 10px; }}
    h3 {{ font-size: 19px; margin: 0 0 8px; }}
    p {{ margin: 0 0 12px; color: #dce7f2; line-height: 1.6; }}
    .lede {{ max-width: 950px; font-size: 18px; color: #d7e3ef; }}
    .nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 20px;
    }}
    .nav a {{
      text-decoration: none;
      padding: 10px 14px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: rgba(14, 22, 33, 0.8);
      color: #d9e2ec;
      font-size: 14px;
    }}
    .grid {{
      display: grid;
      gap: 18px;
      grid-template-columns: repeat(12, 1fr);
      margin-top: 18px;
    }}
    .card {{
      background: linear-gradient(180deg, rgba(16,25,36,0.98), rgba(12,20,30,0.98));
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 20px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.18);
    }}
    .stat-card {{ min-height: 160px; }}
    .stat-value {{
      font-size: 34px;
      font-weight: 700;
      letter-spacing: -0.03em;
      margin-bottom: 10px;
    }}
    .span-3 {{ grid-column: span 3; }}
    .span-4 {{ grid-column: span 4; }}
    .span-5 {{ grid-column: span 5; }}
    .span-6 {{ grid-column: span 6; }}
    .span-7 {{ grid-column: span 7; }}
    .span-8 {{ grid-column: span 8; }}
    .span-12 {{ grid-column: span 12; }}
    .section {{ margin-top: 28px; }}
    .section-header {{
      display: flex;
      justify-content: space-between;
      gap: 20px;
      align-items: end;
      margin-bottom: 14px;
    }}
    .kicker {{
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.10em;
      font-size: 12px;
      margin-bottom: 8px;
    }}
    .chart {{ overflow-x: auto; }}
    .chart svg {{
      width: 100%;
      height: auto;
      display: block;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      padding: 11px 12px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
      text-align: left;
    }}
    th {{
      color: #a9bacb;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.09em;
    }}
    tbody tr:last-child td {{ border-bottom: 0; }}
    ul {{ margin: 0; padding-left: 18px; color: #dce7f2; }}
    li {{ margin-bottom: 10px; line-height: 1.55; }}
    .findings li {{ margin-bottom: 12px; }}
    .source-list {{
      list-style: none;
      padding-left: 0;
      margin: 0;
    }}
    .source-list li {{
      padding: 12px 0;
      border-bottom: 1px solid var(--line);
    }}
    .source-list li:last-child {{ border-bottom: 0; }}
    .source-list span {{
      display: block;
      margin-top: 6px;
      color: var(--muted);
      font-size: 14px;
    }}
    .pill {{
      display: inline-block;
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.03);
      color: #dfe9f3;
      font-size: 13px;
      margin-right: 8px;
      margin-bottom: 8px;
    }}
    .footer {{
      margin-top: 26px;
      color: var(--muted);
      font-size: 13px;
    }}
    @media (max-width: 980px) {{
      .span-3, .span-4, .span-5, .span-6, .span-7, .span-8 {{
        grid-column: span 12;
      }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <section class="hero" id="top">
      <div class="eyebrow">AuralPrimer / Drum Benchmark Program</div>
      <h1>Visual benchmark report: trusted synthetic truth vs suspect Suno MIDI</h1>
      <p class="lede">This report consolidates the current drum-transcription research path, the retained benchmark milestones, the old King in Zion holdout context, and the new trusted-vs-suspect shootout. The core conclusion is stable: the rendered fixture suite with known-good MIDI remains the truth set, while Suno-exported MIDI is diagnostic-only and cannot be used as a tuning target.</p>
      <div class="nav">
        <a href="#overview">Overview</a>
        <a href="#milestones">Milestones</a>
        <a href="#shootout">Current shootout</a>
        <a href="#holdout">Historic holdout</a>
        <a href="#research">Research synthesis</a>
        <a href="#sources">Sources</a>
      </div>
    </section>

    <section class="section" id="overview">
      <div class="section-header">
        <div>
          <div class="kicker">Executive summary</div>
          <h2>What the current benchmark evidence says</h2>
        </div>
      </div>
      <div class="grid">
        <div class="span-3">{_card("Best trusted result", _format_score(trusted_best["mean_overall_f1"]), f"{_pretty_algorithm(trusted_best['algorithm'])} is still the top performer on the rendered known-good fixture suite.")}</div>
        <div class="span-3">{_card("Best suspect Suno result", _format_score(suspect_best["mean_overall_f1"]), f"{_pretty_algorithm(suspect_best['algorithm'])} tops the suspect Suno ranking, but only at an absolute score of {_format_score(suspect_best['mean_overall_f1'])}.")}</div>
        <div class="span-3">{_card("Biggest false promotion", f"{_pretty_algorithm(biggest_false_promotion['algorithm'])} #{biggest_false_promotion['trusted_rank']} to #{biggest_false_promotion['suspect_rank']}", "Suspect Suno MIDI rewards behavior that does not survive the trusted synthetic suite.")}</div>
        <div class="span-3">{_card("Biggest false demotion", f"{_pretty_algorithm(biggest_false_demotion['algorithm'])} #{biggest_false_demotion['trusted_rank']} to #{biggest_false_demotion['suspect_rank']}", "A top performer gets pushed down by the suspect reference, which is why Suno MIDI can no longer be treated as truth.")}</div>
      </div>
      <div class="grid">
        <div class="card span-8">
          <h3>Current operational policy</h3>
          <p>The benchmark program now has a clean rule: optimize against the trusted rendered suite, keep a manually audited real-audio holdout for sanity checks, and treat Suno-exported MIDI as advisory metadata only. The latest shootout exists specifically to show how badly the suspect Suno timing can distort the leaderboard.</p>
          <div>
            <span class="pill">18 algorithms</span>
            <span class="pill">10 rendered trusted fixtures</span>
            <span class="pill">1 suspect Suno case</span>
            <span class="pill">60 ms matching tolerance</span>
            <span class="pill">Offline HTML, no server required</span>
          </div>
        </div>
        <div class="card span-4">
          <h3>Key findings</h3>
          <ul class="findings">
            <li>The hybrid line was the first real structural win because it lifted overall, kick, snare, and hi-hat together.</li>
            <li>The adaptive refinement is real, but mainly as a kick-specific improvement.</li>
            <li>The latest trusted-vs-suspect shootout proves Suno MIDI can reward weaker legacy DSP paths and punish the strongest trusted methods.</li>
          </ul>
        </div>
      </div>
    </section>

    <section class="section" id="milestones">
      <div class="section-header">
        <div>
          <div class="kicker">Milestones</div>
          <h2>How the local research program evolved</h2>
        </div>
      </div>
      <div class="card chart span-12">{milestone_chart}</div>
      <div class="card chart span-12" style="margin-top:18px;">{timing_chart}</div>
      <div class="grid">
        <div class="card span-12">
          <h3>Milestone details</h3>
          <p>The timeline below is the retained local path: early baselines, the failed "just ensemble it" upper bound, the first hybrid breakthrough, the kick-focused adaptive refinement, the retained retry, and the current trusted winner.</p>
          {_render_table(["Milestone", "Overall", "Kick", "Snare", "Hi-hat", "Timing", "Interpretation"], milestone_rows)}
        </div>
      </div>
    </section>

    <section class="section" id="shootout">
      <div class="section-header">
        <div>
          <div class="kicker">Current shootout</div>
          <h2>Trusted synthetic truth vs suspect Suno MIDI</h2>
        </div>
      </div>
      <div class="grid">
        <div class="card span-12 chart">{paired_chart}</div>
        <div class="card span-6 chart">{trusted_chart}</div>
        <div class="card span-6 chart">{suspect_chart}</div>
        <div class="card span-12 chart">{delta_chart}</div>
        <div class="card span-12 chart">{rank_shift_chart}</div>
        <div class="card span-12">
          <h3>What the shootout proves</h3>
          <p>On trusted synthetic, the top of the board is still led by the hybrid line. On suspect Suno, weaker legacy methods get promoted and strong trusted methods get punished. That is exactly the failure mode the benchmark needed to catch before those suspect references could bias further tuning.</p>
          {_render_table(["Algorithm", "Trusted rank", "Suspect rank", "Overall delta", "Why it matters"], distortion_focus)}
        </div>
      </div>
    </section>

    <section class="section" id="holdout">
      <div class="section-header">
        <div>
          <div class="kicker">Historic context</div>
          <h2>King in Zion holdout, reinterpreted correctly</h2>
        </div>
      </div>
      <div class="grid">
        <div class="card span-12 chart">{holdout_chart}</div>
        <div class="card span-7">
          <h3>How to read this now</h3>
          <p>The older King in Zion holdout is still useful as a diagnostic snapshot of real-audio behavior, but it is no longer acceptable as a truth benchmark because the Suno MIDI reference itself is suspect. That means the chart below should inform error analysis, not optimization targets.</p>
          {_render_table(["Algorithm", "Overall", "Kick", "Snare", "Hi-hat", "Timing"], holdout_rows)}
        </div>
        <div class="card span-5">
          <h3>Diagnostic takeaway</h3>
          <ul>
            <li>The retained retry did improve the old holdout slightly, but only by a small margin.</li>
            <li>Manual inspection of King in Zion is what exposed that the Suno-exported MIDI timing could not be trusted.</li>
            <li>The correct follow-up is to keep real audio in the loop, but only with validated references or manual audit, not raw Suno MIDI exports.</li>
          </ul>
        </div>
      </div>
    </section>

    <section class="section" id="research">
      <div class="section-header">
        <div>
          <div class="kicker">Research synthesis</div>
          <h2>What the literature says and how it maps to this repo</h2>
        </div>
      </div>
      <div class="grid">
        <div class="card span-5">
          <h3>Local synthesis</h3>
          <ul>
            <li>Beat context matters, but the evidence favors soft conditioning rather than hard grid overwrite.</li>
            <li>Sequence-aware and structure-aware models fit the failure patterns better than isolated frame heuristics.</li>
            <li>Template and factorization ideas still matter for local overlap resolution.</li>
            <li>Benchmark hygiene matters as much as model quality. The Suno shootout is now part of that hygiene story.</li>
          </ul>
        </div>
        <div class="card span-7">
          <h3>Primary-source reading that still matters</h3>
          <ul class="source-list">{research_links}</ul>
        </div>
      </div>
      <div class="grid">
        <div class="card span-12">
          <h3>Current direction</h3>
          <p>The retained path is still to improve the hybrid family on trusted synthetic while keeping a manually audited real-audio gate. The new trusted-vs-suspect shootout did not replace the older research program; it clarified the data policy around it. Future work should keep Suno assets for import, lyrics, and diagnostics, but never let suspect Suno MIDI become a source-of-truth benchmark again.</p>
        </div>
      </div>
    </section>

    <section class="section" id="sources">
      <div class="section-header">
        <div>
          <div class="kicker">Artifacts</div>
          <h2>Local files behind this report</h2>
        </div>
      </div>
      <div class="grid">
        <div class="card span-8">
          <h3>Direct local sources</h3>
          <ul class="source-list">{"".join(source_links)}</ul>
        </div>
        <div class="card span-4">
          <h3>Report metadata</h3>
          <p><strong>Generated:</strong> {html.escape(generated_local)}</p>
          <p><strong>Output:</strong> {html.escape(str(OUT_PATH))}</p>
          <p><strong>Generator:</strong> {html.escape(str(Path(__file__).resolve()))}</p>
          <p><strong>Latest shootout folder:</strong> {html.escape(str(shootout_dir))}</p>
        </div>
      </div>
      <div class="footer">Open this file directly from disk. All charts are inline SVG and the page does not require a web server.</div>
    </section>
  </main>
</body>
</html>
"""

    OUT_PATH.write_text(html_out, encoding="utf-8")
    return OUT_PATH


def main() -> None:
    out_path = build_report()
    print(out_path)


if __name__ == "__main__":
    main()
