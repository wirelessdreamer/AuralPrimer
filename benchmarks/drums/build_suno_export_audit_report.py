from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BENCH_ROOT = ROOT / "benchmarks" / "drums"
OUT_HTML = BENCH_ROOT / "suno_export_midi_audit_report.html"
OUT_JSON = BENCH_ROOT / "suno_export_midi_audit_summary.json"
CORPUS_MANIFEST = BENCH_ROOT / "suno_suspect_diagnostics.json"
NORMALIZED_SUMMARY = (
    BENCH_ROOT / "shootouts" / "runs" / "20260404_231248_suno-normalized-full-corpus-rerun" / "summary.json"
)
TRUSTED_SUMMARY = BENCH_ROOT / "runs" / "20260325_121140_trusted-synthetic-with-mt3-fixed" / "summary.json"
RAW_IMPORT_EXE = ROOT / "apps" / "desktop" / "src-tauri" / "target" / "debug" / "raw_import.exe"
TMP_IMPORT_ROOT = ROOT / "tmp" / "suno_export_audit_imports"

PYTHON_SRC = ROOT / "python" / "ingest" / "src"
if str(PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(PYTHON_SRC))

from aural_ingest.drum_benchmark import (  # noqa: E402
    _measure_reference_start_offset_sec,
    _parse_midi_note_ons,
)


GM_REFERENCE_LINKS = [
    {
        "title": "MIDI.org General MIDI Level 1 overview",
        "href": "https://midi.org/general-midi-1",
    },
    {
        "title": "CMU General MIDI percussion note map",
        "href": "https://www.cs.cmu.edu/~music/cmp/archives/cmsip/readings/GMSpecs_PercMap.htm",
    },
]

PITCH_LABELS = {
    32: "32",
    35: "35 kick",
    36: "36 kick",
    37: "37 snare",
    38: "38 snare",
    41: "41 tom3",
    42: "42 hat",
    43: "43 tom3",
    44: "44 hat",
    45: "45 tom2",
    46: "46 hat",
    47: "47 tom2",
    48: "48 tom1",
    49: "49 crash",
    50: "50 tom1",
    51: "51 ride",
    52: "52 crash",
    53: "53 ride",
    55: "55 crash",
    57: "57 crash",
    58: "58",
    59: "59 ride",
}

INTERESTING_PITCHES = [32, 35, 36, 37, 38, 41, 42, 44, 45, 46, 47, 49, 55, 57, 58]

ALGO_LABELS = {
    "adaptive_beat_grid": "Adaptive beat grid",
    "hybrid_kick_grid": "Hybrid kick grid",
    "onset_aligned": "Onset aligned",
    "probabilistic_pattern": "Probabilistic pattern",
    "spectral_template_with_grid": "Template + grid",
    "yourmt3_drums": "YourMT3 drums",
}


@dataclass
class CaseAudit:
    case_id: str
    title: str
    wav_path: str
    midi_path: str
    raw_note_counts: dict[int, int]
    raw_total_notes: int
    selected_mode: str
    observed_start_offset_sec: float | None
    audio_start_sec: float | None
    midi_start_sec: float | None
    start_alignment_applied: bool
    start_alignment_reason: str
    importer_warnings: list[str]
    importer_canonicalization: list[dict[str, int]]
    importer_normalization_pairs: int | None
    importer_median_correction_sec: float | None
    importer_median_correction_direction: str | None
    drums_excluded_from_chart: bool
    imported_pack_path: str | None
    imported_note_counts: dict[int, int]
    imported_total_notes: int


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text("utf-8"))


def _format_pitch(pitch: int) -> str:
    return PITCH_LABELS.get(pitch, str(pitch))


def _format_sec(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.3f}s"


def _format_sec_plain(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}s"


def _percent(part: float, whole: float) -> float:
    if whole <= 0:
        return 0.0
    return 100.0 * float(part) / float(whole)


def _parse_canonicalization_warning(warning: str) -> list[dict[str, int]]:
    match = re.search(r"Canonicalized Suno drum MIDI pitches against the drum stem:\s*(.+?)\.$", warning)
    if not match:
        return []
    out: list[dict[str, int]] = []
    for item in match.group(1).split(","):
        token = item.strip()
        if "->" not in token:
            continue
        src, dst = token.split("->", 1)
        out.append({"source": int(src.strip()), "target": int(dst.strip())})
    return out


def _parse_normalization_warning(warning: str) -> tuple[int | None, float | None, str | None]:
    match = re.search(
        r"Applied source MIDI start normalization using (\d+) matched audio/MIDI pair\(s\); median correction ([0-9.]+)s (earlier|later)\.",
        warning,
    )
    if not match:
        return None, None, None
    return int(match.group(1)), float(match.group(2)), match.group(3)


def _read_raw_note_counts(reference_path: Path) -> tuple[dict[int, int], int, str]:
    note_ons, _tempo_changes, _tpq = _parse_midi_note_ons(reference_path)
    strict = [
        event
        for event in note_ons
        if event.channel == 9 or ("drum" in (event.track_name or "").strip().lower())
    ]
    selected = strict if strict else note_ons
    counts: dict[int, int] = {}
    for event in selected:
        counts[event.note] = counts.get(event.note, 0) + 1
    return counts, sum(counts.values()), ("strict" if strict else "relaxed")


def _read_imported_drum_note_counts(songpack_path: Path) -> tuple[dict[int, int], int]:
    import mido

    midi_path = songpack_path / "features" / "notes.mid"
    if not midi_path.is_file():
        return {}, 0
    midi = mido.MidiFile(midi_path)
    counts: dict[int, int] = {}
    for track in midi.tracks:
        if track.name != "Drums":
            continue
        for msg in track:
            if msg.type == "note_on" and msg.velocity > 0:
                counts[msg.note] = counts.get(msg.note, 0) + 1
    return counts, sum(counts.values())


def _run_raw_import(case: dict[str, Any], output_root: Path) -> tuple[list[str], Path | None]:
    before = {path.name for path in output_root.glob("*.songpack")}
    proc = subprocess.run(
        [str(RAW_IMPORT_EXE), str(Path(case["wav_path"]).parent), str(output_root), str(case["title"])],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"raw_import failed for {case['id']}: {proc.stderr.strip()}")
    payload = json.loads(proc.stdout)
    warnings = list(payload.get("warnings", []))
    after = list(output_root.glob("*.songpack"))
    new_dirs = [path for path in after if path.name not in before]
    if len(new_dirs) == 1:
        return warnings, new_dirs[0]
    if new_dirs:
        return warnings, max(new_dirs, key=lambda path: path.stat().st_mtime)
    return warnings, None


def _collect_case_audit(case: dict[str, Any], output_root: Path) -> CaseAudit:
    wav_path = Path(case["wav_path"])
    midi_path = Path(case["reference_path"])
    offset_meta = _measure_reference_start_offset_sec(
        midi_path,
        wav_path,
        min_abs_offset_sec=0.05,
        max_abs_offset_sec=2.0,
    )
    raw_counts, raw_total, selected_mode = _read_raw_note_counts(midi_path)
    warnings, pack_path = _run_raw_import(case, output_root)
    importer_canonicalization: list[dict[str, int]] = []
    norm_pairs = None
    norm_sec = None
    norm_dir = None
    drums_excluded = False
    for warning in warnings:
        importer_canonicalization.extend(_parse_canonicalization_warning(warning))
        pairs, seconds, direction = _parse_normalization_warning(warning)
        if pairs is not None:
            norm_pairs, norm_sec, norm_dir = pairs, seconds, direction
        if "Drums source MIDI timing differed" in warning and "excluded from the auto-normalized gameplay chart" in warning:
            drums_excluded = True
    imported_counts, imported_total = ({}, 0) if pack_path is None else _read_imported_drum_note_counts(pack_path)
    return CaseAudit(
        case_id=str(case["id"]),
        title=str(case["title"]),
        wav_path=str(wav_path),
        midi_path=str(midi_path),
        raw_note_counts=raw_counts,
        raw_total_notes=raw_total,
        selected_mode=selected_mode,
        observed_start_offset_sec=None if offset_meta is None else float(offset_meta["observed_start_offset_sec"]),
        audio_start_sec=None if offset_meta is None else float(offset_meta["audio_start_sec"]),
        midi_start_sec=None if offset_meta is None else float(offset_meta["midi_start_sec"]),
        start_alignment_applied=False if offset_meta is None else bool(offset_meta["start_alignment_applied"]),
        start_alignment_reason="" if offset_meta is None else str(offset_meta["start_alignment_reason"]),
        importer_warnings=warnings,
        importer_canonicalization=importer_canonicalization,
        importer_normalization_pairs=norm_pairs,
        importer_median_correction_sec=norm_sec,
        importer_median_correction_direction=norm_dir,
        drums_excluded_from_chart=drums_excluded,
        imported_pack_path=None if pack_path is None else str(pack_path),
        imported_note_counts=imported_counts,
        imported_total_notes=imported_total,
    )


def _build_corpus_audit() -> dict[str, Any]:
    if not RAW_IMPORT_EXE.is_file():
        raise RuntimeError(f"missing raw_import binary: {RAW_IMPORT_EXE}")
    manifest = _load_json(CORPUS_MANIFEST)
    if TMP_IMPORT_ROOT.exists():
        shutil.rmtree(TMP_IMPORT_ROOT)
    TMP_IMPORT_ROOT.mkdir(parents=True, exist_ok=True)
    cases = [_collect_case_audit(case, TMP_IMPORT_ROOT) for case in manifest.get("cases", [])]
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "corpus_manifest": str(CORPUS_MANIFEST),
        "normalized_summary_path": str(NORMALIZED_SUMMARY),
        "trusted_summary_path": str(TRUSTED_SUMMARY),
        "cases": [asdict(case) for case in cases],
    }


def _card(title: str, value: str, detail: str) -> str:
    return (
        '<div class="card">'
        f'<div class="card-title">{html.escape(title)}</div>'
        f'<div class="card-value">{html.escape(value)}</div>'
        f'<div class="card-detail">{html.escape(detail)}</div>'
        "</div>"
    )


def _svg_wrap(width: int, height: int, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">{body}</svg>'
    )


def _render_offset_chart(cases: list[dict[str, Any]]) -> str:
    width = 980
    top = 84
    left = 280
    row_h = 34
    bar_h = 20
    chart_w = 620
    height = top + row_h * len(cases) + 36
    values = [abs(float(case["observed_start_offset_sec"])) for case in cases if case["observed_start_offset_sec"] is not None]
    max_abs = max(values) if values else 1.0
    max_abs = max(max_abs, 1.0)
    zero_x = left + chart_w / 2.0
    parts = [
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="18" fill="#101723" stroke="#263246"/>',
        '<text x="24" y="34" fill="#f4f7fb" font-size="24" font-family="Segoe UI, Arial">Observed drum start offset</text>',
        '<text x="24" y="58" fill="#97a9be" font-size="13" font-family="Segoe UI, Arial">Signed difference = first MIDI note minus first audio onset. Positive means MIDI starts later than audio.</text>',
        f'<line x1="{zero_x:.1f}" y1="{top - 10}" x2="{zero_x:.1f}" y2="{height - 24}" stroke="#6b7a90" stroke-dasharray="4 5"/>',
        f'<text x="{zero_x - 12:.1f}" y="{top - 18}" fill="#8ea1b8" font-size="11" font-family="Segoe UI, Arial">0s</text>',
    ]
    for idx, case in enumerate(cases):
        y = top + idx * row_h
        title = str(case["title"])
        value = case["observed_start_offset_sec"]
        applied = bool(case["start_alignment_applied"])
        reason = str(case["start_alignment_reason"])
        parts.append(
            f'<text x="24" y="{y + 15}" fill="#dce5ef" font-size="13" font-family="Segoe UI, Arial">{html.escape(title)}</text>'
        )
        parts.append(f'<rect x="{left}" y="{y}" width="{chart_w}" height="{bar_h}" rx="10" fill="#182130"/>')
        if value is not None:
            magnitude = min(abs(float(value)) / max_abs, 1.0) * (chart_w / 2.0)
            x = zero_x if float(value) >= 0 else zero_x - magnitude
            fill = "#43d3a0" if applied else "#ff9f5c"
            parts.append(f'<rect x="{x:.1f}" y="{y}" width="{magnitude:.1f}" height="{bar_h}" rx="10" fill="{fill}"/>')
            parts.append(
                f'<text x="{left + chart_w + 12}" y="{y + 14}" fill="#dce5ef" font-size="12" font-family="Consolas, monospace">{float(value):+.3f}s</text>'
            )
            label = "safe normalization" if applied else reason
            parts.append(
                f'<text x="{left + chart_w + 100}" y="{y + 14}" fill="#8ea1b8" font-size="11" font-family="Segoe UI, Arial">{html.escape(label)}</text>'
            )
    return _svg_wrap(width, height, "".join(parts))


def _render_pitch_heatmap(cases: list[dict[str, Any]]) -> str:
    width = 980
    left = 210
    top = 92
    cell_w = 46
    cell_h = 34
    height = top + cell_h * len(cases) + 40
    parts = [
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="18" fill="#101723" stroke="#263246"/>',
        '<text x="24" y="34" fill="#f4f7fb" font-size="24" font-family="Segoe UI, Arial">Raw Suno drum MIDI pitch heatmap</text>',
        '<text x="24" y="58" fill="#97a9be" font-size="13" font-family="Segoe UI, Arial">Each cell is the percent share of that pitch in the raw drum MIDI. Red outlines mark pitches our importer had to remap using the audio stem.</text>',
    ]
    for col, pitch in enumerate(INTERESTING_PITCHES):
        x = left + col * cell_w
        parts.append(
            f'<text x="{x + cell_w / 2:.1f}" y="{top - 16}" text-anchor="middle" fill="#cbd7e5" font-size="11" font-family="Segoe UI, Arial">{html.escape(_format_pitch(pitch))}</text>'
        )
    for row, case in enumerate(cases):
        y = top + row * cell_h
        counts = {int(key): int(value) for key, value in case["raw_note_counts"].items()}
        total = max(int(case["raw_total_notes"]), 1)
        remapped_sources = {int(item["source"]) for item in case["importer_canonicalization"]}
        parts.append(
            f'<text x="24" y="{y + 21}" fill="#dce5ef" font-size="13" font-family="Segoe UI, Arial">{html.escape(str(case["title"]))}</text>'
        )
        for col, pitch in enumerate(INTERESTING_PITCHES):
            x = left + col * cell_w
            count = counts.get(pitch, 0)
            share = count / total
            light = 18 + int(round(share * 170.0))
            fill = f"rgb(57, {min(light + 25, 220)}, {min(light + 35, 235)})" if count else "#172131"
            stroke = "#ff7e6b" if pitch in remapped_sources else "#2b3748"
            stroke_w = 2 if pitch in remapped_sources else 1
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell_w - 4}" height="{cell_h - 6}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_w}"/>'
            )
            if count:
                label = f"{_percent(count, total):.0f}%"
                parts.append(
                    f'<text x="{x + (cell_w - 4) / 2:.1f}" y="{y + 19}" text-anchor="middle" fill="#09111d" font-size="11" font-family="Consolas, monospace">{label}</text>'
                )
    return _svg_wrap(width, height, "".join(parts))


def _render_case_study_chart(case: dict[str, Any]) -> str:
    width = 900
    height = 260
    left = 96
    top = 86
    row_gap = 74
    chart_w = 740
    pitches: list[int] = sorted(
        {
            *[int(key) for key in case["raw_note_counts"].keys()],
            *[int(key) for key in case["imported_note_counts"].keys()],
        }
    )
    pitches = [pitch for pitch in pitches if pitch in INTERESTING_PITCHES or pitch in {36, 38, 46, 47, 49, 50}]
    if len(pitches) > 10:
        pitches = pitches[:10]
    raw_counts = {int(key): int(value) for key, value in case["raw_note_counts"].items()}
    imported_counts = {int(key): int(value) for key, value in case["imported_note_counts"].items()}
    max_count = max([1, *raw_counts.values(), *imported_counts.values()])
    parts = [
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="18" fill="#101723" stroke="#263246"/>',
        f'<text x="24" y="34" fill="#f4f7fb" font-size="22" font-family="Segoe UI, Arial">{html.escape(str(case["title"]))}</text>',
        '<text x="24" y="58" fill="#97a9be" font-size="13" font-family="Segoe UI, Arial">Raw source pitch counts vs importer-corrected gameplay pitch counts.</text>',
    ]
    rows = [("Raw source MIDI", raw_counts), ("Corrected notes.mid", imported_counts)]
    for row_idx, (label, counts) in enumerate(rows):
        y = top + row_idx * row_gap
        parts.append(f'<text x="24" y="{y + 15}" fill="#dce5ef" font-size="13" font-family="Segoe UI, Arial">{html.escape(label)}</text>')
        parts.append(f'<line x1="{left}" y1="{y + 28}" x2="{left + chart_w}" y2="{y + 28}" stroke="#2d3a4d"/>')
        bar_space = chart_w / max(len(pitches), 1)
        for col, pitch in enumerate(pitches):
            x = left + col * bar_space + 10
            value = counts.get(pitch, 0)
            bar_h = 0 if value <= 0 else (42 * value / max_count)
            fill = "#4ad0a6" if row_idx else "#56b5ff"
            if any(int(item["source"]) == pitch for item in case["importer_canonicalization"]):
                fill = "#ff9f5c"
            if any(int(item["target"]) == pitch for item in case["importer_canonicalization"]) and row_idx == 1:
                fill = "#7ae582"
            parts.append(
                f'<rect x="{x:.1f}" y="{y + 28 - bar_h:.1f}" width="{bar_space - 18:.1f}" height="{bar_h:.1f}" rx="6" fill="{fill}"/>'
            )
            parts.append(
                f'<text x="{x + (bar_space - 18) / 2:.1f}" y="{y + 46}" text-anchor="middle" fill="#cbd7e5" font-size="10" font-family="Segoe UI, Arial">{html.escape(_format_pitch(pitch))}</text>'
            )
            if value:
                parts.append(
                    f'<text x="{x + (bar_space - 18) / 2:.1f}" y="{y + 20 - bar_h:.1f}" text-anchor="middle" fill="#dce5ef" font-size="10" font-family="Consolas, monospace">{value}</text>'
                )
    mapping_label = ", ".join(f"{item['source']}->{item['target']}" for item in case["importer_canonicalization"])
    parts.append(
        f'<text x="24" y="{height - 20}" fill="#97a9be" font-size="12" font-family="Segoe UI, Arial">Importer mapping: {html.escape(mapping_label or "none")}</text>'
    )
    return _svg_wrap(width, height, "".join(parts))


def _render_benchmark_context(normalized_payload: dict[str, Any], trusted_payload: dict[str, Any]) -> str:
    suspect_rows = {
        item["algorithm"]: item for item in normalized_payload["suspect"]["summary"]["algorithm_summaries"]
    }
    trusted_rows = {
        item["algorithm"]: item for item in trusted_payload["summary"]["algorithm_summaries"]
    }
    ranked = sorted(
        suspect_rows.values(),
        key=lambda item: float(item.get("mean_core_f1", 0.0)),
        reverse=True,
    )
    chosen: list[str] = []
    for item in ranked:
        algorithm = str(item["algorithm"])
        if algorithm not in trusted_rows:
            continue
        chosen.append(algorithm)
        if len(chosen) >= 6:
            break
    width = 980
    left = 210
    top = 90
    group_w = 110
    bar_w = 28
    chart_h = 210
    height = top + chart_h + 60
    max_val = max(
        [0.35]
        + [float(trusted_rows[item]["mean_core_f1"]) for item in chosen]
        + [float(suspect_rows[item]["mean_core_f1"]) for item in chosen]
    )
    parts = [
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="18" fill="#101723" stroke="#263246"/>',
        '<text x="24" y="34" fill="#f4f7fb" font-size="24" font-family="Segoe UI, Arial">Downstream benchmark impact</text>',
        '<text x="24" y="58" fill="#97a9be" font-size="13" font-family="Segoe UI, Arial">Trusted synthetic core F1 vs normalized Suno core F1. Once start offsets are corrected, the suspect corpus becomes directionally informative instead of obviously misleading.</text>',
        f'<line x1="{left}" y1="{top + chart_h}" x2="{left + group_w * len(chosen)}" y2="{top + chart_h}" stroke="#2d3a4d"/>',
        '<rect x="24" y="76" width="14" height="14" rx="4" fill="#56b5ff"/>',
        '<text x="46" y="88" fill="#dce5ef" font-size="12" font-family="Segoe UI, Arial">Trusted synthetic core F1</text>',
        '<rect x="210" y="76" width="14" height="14" rx="4" fill="#4ad0a6"/>',
        '<text x="232" y="88" fill="#dce5ef" font-size="12" font-family="Segoe UI, Arial">Normalized Suno diagnostic core F1</text>',
    ]
    for idx, algorithm in enumerate(chosen):
        x = left + idx * group_w + 18
        trusted = float(trusted_rows[algorithm]["mean_core_f1"])
        suspect = float(suspect_rows[algorithm]["mean_core_f1"])
        trusted_h = chart_h * trusted / max_val
        suspect_h = chart_h * suspect / max_val
        parts.append(f'<rect x="{x}" y="{top + chart_h - trusted_h:.1f}" width="{bar_w}" height="{trusted_h:.1f}" rx="8" fill="#56b5ff"/>')
        parts.append(f'<rect x="{x + 38}" y="{top + chart_h - suspect_h:.1f}" width="{bar_w}" height="{suspect_h:.1f}" rx="8" fill="#4ad0a6"/>')
        parts.append(
            f'<text x="{x + 14}" y="{top + chart_h - trusted_h - 8:.1f}" text-anchor="middle" fill="#dce5ef" font-size="11" font-family="Consolas, monospace">{trusted:.3f}</text>'
        )
        parts.append(
            f'<text x="{x + 52}" y="{top + chart_h - suspect_h - 8:.1f}" text-anchor="middle" fill="#dce5ef" font-size="11" font-family="Consolas, monospace">{suspect:.3f}</text>'
        )
        parts.append(
            f'<text x="{x + 26}" y="{top + chart_h + 18}" text-anchor="middle" fill="#cbd7e5" font-size="11" font-family="Segoe UI, Arial">{html.escape(ALGO_LABELS.get(algorithm, algorithm))}</text>'
        )
    return _svg_wrap(width, height, "".join(parts))


def _rel_href(path: Path) -> str:
    return os.path.relpath(path, OUT_HTML.parent).replace("\\", "/")


def _render_html(summary: dict[str, Any]) -> str:
    cases = summary["cases"]
    safe_offsets = [case for case in cases if case["start_alignment_applied"]]
    remapped_cases = [case for case in cases if case["importer_canonicalization"]]
    excluded_cases = [case for case in cases if case["drums_excluded_from_chart"]]
    safe_values = [float(case["observed_start_offset_sec"]) for case in safe_offsets if case["observed_start_offset_sec"] is not None]
    median_safe = statistics.median(safe_values) if safe_values else None
    total_affected = 0
    total_raw_notes = 0
    for case in remapped_cases:
        raw_counts = {int(key): int(value) for key, value in case["raw_note_counts"].items()}
        total_raw_notes += int(case["raw_total_notes"])
        total_affected += sum(raw_counts.get(int(item["source"]), 0) for item in case["importer_canonicalization"])

    normalized_payload = _load_json(NORMALIZED_SUMMARY)
    trusted_payload = _load_json(TRUSTED_SUMMARY)
    benchmark_chart = _render_benchmark_context(normalized_payload, trusted_payload)
    case_study_svgs = "".join(
        f'<div class="case-study">{_render_case_study_chart(case)}</div>' for case in remapped_cases
    )
    summary_cards = "".join(
        [
            _card("Songs audited", str(len(cases)), "Seven Suno drum stem + MIDI pairs from the Psalms corpus."),
            _card(
                "Safe start offsets",
                f"{len(safe_offsets)}/{len(cases)}",
                "Four songs clustered around a roughly +0.54s MIDI-late offset that Studio can normalize safely.",
            ),
            _card(
                "Drum charts excluded",
                f"{len(excluded_cases)}/{len(cases)}",
                "Three songs were too far out of bounds for safe auto-normalization, so Studio drops the raw drum MIDI instead of trusting it.",
            ),
            _card(
                "Audio-driven remaps",
                f"{total_affected} notes",
                f"{len(remapped_cases)} songs needed drum pitch canonicalization; that is {_percent(total_affected, max(total_raw_notes, 1)):.1f}% of their raw drum notes.",
            ),
        ]
    )
    case_rows = []
    for case in cases:
        raw_counts = {int(key): int(value) for key, value in case["raw_note_counts"].items()}
        top_pitches = ", ".join(
            f"{_format_pitch(pitch)} ({count})"
            for pitch, count in sorted(raw_counts.items(), key=lambda item: (-item[1], item[0]))[:6]
        )
        mapping = ", ".join(f"{item['source']}→{item['target']}" for item in case["importer_canonicalization"]) or "none"
        correction = "n/a"
        if case["importer_median_correction_sec"] is not None:
            correction = (
                f"{case['importer_median_correction_sec']:.3f}s {case['importer_median_correction_direction']}"
            )
        outcome = "excluded" if case["drums_excluded_from_chart"] else ("imported" if case["imported_total_notes"] else "no drum chart")
        case_rows.append(
            "<tr>"
            f"<td>{html.escape(str(case['title']))}</td>"
            f"<td><code>{html.escape(_format_sec(case['observed_start_offset_sec']))}</code></td>"
            f"<td>{'yes' if case['start_alignment_applied'] else 'no'}</td>"
            f"<td>{html.escape(mapping)}</td>"
            f"<td>{html.escape(correction)}</td>"
            f"<td>{html.escape(outcome)}</td>"
            f"<td>{html.escape(top_pitches)}</td>"
            "</tr>"
        )

    recommendations = [
        "Export each drum stem WAV and its paired MIDI from the same absolute zero-point. Four songs showed a stable ~0.54s MIDI-late shift; three were much farther off.",
        "Canonicalize drum note numbers before export. If the source model internally uses alternate labels, convert them to a declared GM-compatible drum map at export time.",
        "If exact zero alignment is not guaranteed, emit machine-readable offset metadata per stem so downstream tools do not have to infer it from audio.",
        "Add export validation: compare the first few stem onsets against the first MIDI notes and warn when the offset exceeds a small threshold.",
    ]

    source_links = [
        ("Suno suspect corpus manifest", CORPUS_MANIFEST),
        ("Trusted synthetic benchmark summary", TRUSTED_SUMMARY),
        ("Normalized Suno diagnostic summary", NORMALIZED_SUMMARY),
        ("Generated audit summary JSON", OUT_JSON),
    ]
    source_html = "".join(
        f'<li><a href="{html.escape(_rel_href(path))}">{html.escape(label)}</a></li>' for label, path in source_links
    )
    external_html = "".join(
        f'<li><a href="{html.escape(item["href"])}">{html.escape(item["title"])}</a></li>' for item in GM_REFERENCE_LINKS
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Suno Drum MIDI Export Audit</title>
  <style>
    :root {{
      --bg: #09111a;
      --panel: #101723;
      --panel-2: #141d2b;
      --text: #eaf1f8;
      --muted: #9eb0c5;
      --line: #273347;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(86,181,255,0.16), transparent 30%),
        radial-gradient(circle at bottom right, rgba(74,208,166,0.12), transparent 34%),
        var(--bg);
    }}
    main {{ max-width: 1200px; margin: 0 auto; padding: 36px 24px 56px; }}
    .hero, .section {{
      background: rgba(12,19,30,0.88);
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 28px 30px;
      box-shadow: 0 18px 54px rgba(0,0,0,0.26);
    }}
    .section {{ margin-top: 28px; }}
    h1, h2, h3 {{ margin: 0 0 12px; }}
    h1 {{ font-size: 38px; line-height: 1.08; }}
    h2 {{ font-size: 28px; }}
    h3 {{ font-size: 20px; }}
    p {{ color: var(--muted); line-height: 1.6; }}
    .eyebrow {{
      color: #56b5ff;
      text-transform: uppercase;
      letter-spacing: 0.14em;
      font-size: 12px;
      margin-bottom: 12px;
      font-weight: 700;
    }}
    .hero-grid {{ display: grid; grid-template-columns: 2fr 1fr; gap: 24px; }}
    .hero-side {{
      background: rgba(20,29,43,0.68);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 18px;
    }}
    .hero-side ul, .section ul {{ margin: 0; padding-left: 18px; color: var(--muted); }}
    .hero-side li, .section li {{ margin-bottom: 10px; line-height: 1.5; }}
    .meta {{ display: flex; gap: 18px; flex-wrap: wrap; color: var(--muted); font-size: 13px; margin-top: 14px; }}
    .cards {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin-top: 22px; }}
    .card {{ background: var(--panel-2); border: 1px solid var(--line); border-radius: 18px; padding: 18px; }}
    .card-title {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 10px; }}
    .card-value {{ font-size: 34px; font-weight: 700; margin-bottom: 8px; }}
    .card-detail {{ color: var(--muted); font-size: 13px; line-height: 1.5; }}
    .figure, .case-study {{ margin-top: 18px; overflow-x: auto; }}
    .pill {{
      display: inline-block;
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid rgba(74,208,166,0.3);
      background: rgba(74,208,166,0.12);
      color: #c9fff1;
      font-size: 12px;
      margin-right: 8px;
      margin-top: 8px;
    }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 14px; font-size: 14px; }}
    th, td {{ text-align: left; padding: 12px 10px; border-bottom: 1px solid rgba(39,51,71,0.72); vertical-align: top; }}
    th {{ color: #dbe7f3; font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; }}
    td {{ color: var(--muted); line-height: 1.45; }}
    code {{
      color: #dff5ff;
      background: rgba(86,181,255,0.1);
      border: 1px solid rgba(86,181,255,0.14);
      border-radius: 8px;
      padding: 2px 6px;
      font-family: Consolas, monospace;
      font-size: 12px;
    }}
    a {{ color: #8dd1ff; }}
    @media (max-width: 980px) {{
      .hero-grid, .cards {{ grid-template-columns: 1fr; }}
      main {{ padding: 24px 16px 42px; }}
      h1 {{ font-size: 31px; }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <div class="eyebrow">Suno Export Audit</div>
      <div class="hero-grid">
        <div>
          <h1>Drum MIDI export issues across seven Suno songs</h1>
          <p>This report audits seven Suno drum stem + MIDI export pairs from the Psalms corpus. It focuses on two export defects: mismatched audio/MIDI start times and drum-note labeling that is not safe to treat as literal gameplay mapping.</p>
          <p>The evidence below is multi-song, reproducible, and built from the raw exported WAV and MIDI files plus the importer corrections required to make them usable.</p>
          <div class="meta">
            <span>Generated {html.escape(summary["generated_at_utc"])}</span>
            <span>Corpus: 7 songs</span>
            <span>Median safe drum offset: {html.escape(_format_sec_plain(median_safe)) if median_safe is not None else "n/a"}</span>
          </div>
        </div>
        <div class="hero-side">
          <h3>Presentation summary</h3>
          <ul>
            <li>Four songs have a repeatable drum MIDI start offset of about +0.54s, with MIDI starting later than audio.</li>
            <li>Three songs are much farther off and cannot be safely normalized by a simple start shift.</li>
            <li>Three songs required audio-driven drum note remapping to turn suspicious source pitches into usable kick/snare/hat lanes.</li>
            <li>The downstream benchmark picture becomes materially more coherent once Suno MIDI is aligned to audio timebase.</li>
          </ul>
        </div>
      </div>
      <div class="cards">{summary_cards}</div>
    </section>
    <section class="section">
      <h2>Key findings</h2>
      <span class="pill">Timing drift is systematic on part of the corpus</span>
      <span class="pill">Pitch labeling is inconsistent on several songs</span>
      <span class="pill">Importer workaround proves the source defect is real</span>
      <span class="pill">Raw Suno MIDI should not be treated as zero-aligned truth</span>
      <p>The safe-offset cluster is narrow enough to look systematic, not random: four songs landed between roughly +0.53s and +0.55s on the drum pair. The outliers are not small variations around that cluster; they are structural misalignments ranging from about -2.0s to about -8.9s.</p>
      <p>Timing is not the only issue. In <strong>King in Zion</strong>, kick-like content was exported heavily on <code>45</code> and <code>47</code> (GM tom notes). In <strong>Every Morning</strong>, hat-like content appeared on <code>32</code> and <code>58</code>. The importer had to correct those against the drum stem before gameplay mapping made sense.</p>
    </section>
    <section class="section">
      <h2>Timing alignment across the corpus</h2>
      <p>Signed offset is measured as first drum MIDI note minus first audible drum-stem onset. Positive values mean the MIDI starts late. Green bars are within the current safe normalization window; orange bars were too large to trust automatically.</p>
      <div class="figure">{_render_offset_chart(cases)}</div>
    </section>
    <section class="section">
      <h2>Raw drum MIDI pitch distributions</h2>
      <p>This heatmap shows how much of each song's raw drum MIDI is spent on specific note numbers. Red outlines indicate source pitches that Studio had to remap using the audio stem. That is the strongest evidence that the export note labels are not always safe as-is.</p>
      <div class="figure">{_render_pitch_heatmap(cases)}</div>
    </section>
    <section class="section">
      <h2>Representative correction cases</h2>
      <p>These are the songs where the importer had to actively canonicalize drum pitches. The point is not that the MIDI is useless; it is that the raw export is not self-describing enough to trust literally.</p>
      {case_study_svgs}
    </section>
    <section class="section">
      <h2>Case-by-case audit table</h2>
      <p>This table combines the raw offset measurement with the importer action that was required to produce a usable chart.</p>
      <table>
        <thead>
          <tr>
            <th>Song</th>
            <th>Drum offset</th>
            <th>Safe shift?</th>
            <th>Pitch remap</th>
            <th>Median import correction</th>
            <th>Drum chart outcome</th>
            <th>Top raw MIDI pitches</th>
          </tr>
        </thead>
        <tbody>{"".join(case_rows)}</tbody>
      </table>
    </section>
    <section class="section">
      <h2>Downstream benchmark context</h2>
      <p>The normalized Suno corpus is still diagnostic-only, but once the start offset is corrected it becomes directionally much closer to the trusted synthetic gate. That is consistent with the subjective review: the MIDI has value, but only after explicit time-base reconciliation.</p>
      <div class="figure">{benchmark_chart}</div>
    </section>
    <section class="section">
      <h2>Recommendations for Suno export</h2>
      <ul>{"".join(f"<li>{html.escape(item)}</li>" for item in recommendations)}</ul>
    </section>
    <section class="section">
      <h2>Sources and references</h2>
      <p>The primary evidence here is the local multi-song audit corpus and the project's benchmark outputs. The external references below are included only to frame what a stable drum-note export should look like.</p>
      <h3>Local evidence</h3>
      <ul>{source_html}</ul>
      <h3>External references</h3>
      <ul>{external_html}</ul>
    </section>
  </main>
</body>
</html>
"""


def main() -> None:
    summary = _build_corpus_audit()
    OUT_JSON.write_text(json.dumps(summary, indent=2), "utf-8")
    OUT_HTML.write_text(_render_html(summary), "utf-8")
    print(json.dumps({"html": str(OUT_HTML), "summary": str(OUT_JSON)}, indent=2))


if __name__ == "__main__":
    main()
