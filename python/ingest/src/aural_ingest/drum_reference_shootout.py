from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import html
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from aural_ingest.drum_benchmark import benchmark_algorithms, load_drum_reference
from aural_ingest.drum_benchmark_suite import (
    DEFAULT_FIXTURES_DIR,
    run_benchmark_suite,
    summarize_suite_results,
)
from aural_ingest.transcription import KNOWN_DRUM_FILTERS, build_default_drum_algorithm_registry


SHOOTOUT_VERSION = "1.0.0"
DEFAULT_OUTPUT_ROOT = Path("benchmarks") / "drums" / "shootouts" / "runs"
REQUIRED_OUTPUT_FILES: tuple[str, ...] = ("report.md", "report.html", "summary.json")


@dataclass(frozen=True)
class ShootoutCase:
    case_id: str
    title: str
    wav_path: Path
    reference_path: Path
    summary: str = ""
    tags: tuple[str, ...] = ()
    focus: tuple[str, ...] = ()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _default_fixtures_dir() -> Path:
    return _repo_root() / DEFAULT_FIXTURES_DIR


def _default_output_root() -> Path:
    return _repo_root() / DEFAULT_OUTPUT_ROOT


def _dedupe_preserve_order(values: Sequence[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def _collect_algorithm_ids(requested: Sequence[str] | None) -> list[str]:
    if not requested:
        return list(KNOWN_DRUM_FILTERS)
    normalized = []
    for value in requested:
        token = str(value).strip().lower()
        if token:
            normalized.append(token)
    return _dedupe_preserve_order(normalized or list(KNOWN_DRUM_FILTERS))


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


def _format_delta(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):+.3f}"


def _format_ms(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.1f} ms"


def _format_delta_ms(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):+.1f} ms"


def _safe_diff(lhs: float | None, rhs: float | None) -> float | None:
    if lhs is None or rhs is None:
        return None
    return float(lhs) - float(rhs)


def _metric_snapshot(item: Mapping[str, Any] | None) -> dict[str, Any]:
    item = item or {}
    return {
        "case_count": int(item.get("case_count", 0) or 0),
        "successful_cases": int(item.get("successful_cases", 0) or 0),
        "mean_overall_f1": None if item.get("mean_overall_f1") is None else float(item["mean_overall_f1"]),
        "mean_core_f1": None if item.get("mean_core_f1") is None else float(item["mean_core_f1"]),
        "mean_kick_f1": None if item.get("mean_kick_f1") is None else float(item["mean_kick_f1"]),
        "mean_snare_f1": None if item.get("mean_snare_f1") is None else float(item["mean_snare_f1"]),
        "mean_hi_hat_f1": None if item.get("mean_hi_hat_f1") is None else float(item["mean_hi_hat_f1"]),
        "mean_precision": None if item.get("mean_precision") is None else float(item["mean_precision"]),
        "mean_recall": None if item.get("mean_recall") is None else float(item["mean_recall"]),
        "mean_timing_mae_ms": None
        if item.get("mean_timing_mae_ms") is None
        else float(item["mean_timing_mae_ms"]),
        "median_timing_mae_ms": None
        if item.get("median_timing_mae_ms") is None
        else float(item["median_timing_mae_ms"]),
        "error_cases": list(item.get("error_cases", [])),
    }


def _build_rank_map(algorithm_summaries: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    ordered = sorted(
        algorithm_summaries,
        key=lambda item: (
            -(float(item["mean_overall_f1"]) if item.get("mean_overall_f1") is not None else -1.0),
            -(float(item["mean_core_f1"]) if item.get("mean_core_f1") is not None else -1.0),
            str(item.get("algorithm", "")),
        ),
    )
    return {
        str(item.get("algorithm")): index + 1
        for index, item in enumerate(ordered)
        if item.get("algorithm")
    }


def load_manual_corpus_manifest(manifest_path: Path | str) -> tuple[dict[str, Any], list[str], list[ShootoutCase]]:
    path = Path(manifest_path)
    data = json.loads(path.read_text("utf-8"))
    warnings: list[str] = []

    corpus_id = str(data.get("corpus_id", "suspect_reference")).strip() or "suspect_reference"
    title = str(data.get("title", corpus_id)).strip() or corpus_id
    reference_trust = str(data.get("reference_trust", "suspect")).strip().lower() or "suspect"
    description = str(data.get("description", "")).strip()

    cases: list[ShootoutCase] = []
    for index, item in enumerate(data.get("cases", []), start=1):
        if not isinstance(item, Mapping):
            warnings.append(f"{path.name}: case #{index} is not an object")
            continue
        case_id = str(item.get("id", f"case_{index}")).strip() or f"case_{index}"
        title_value = str(item.get("title", case_id)).strip() or case_id
        wav_path = Path(str(item.get("wav_path", "")).strip())
        reference_path = Path(str(item.get("reference_path", "")).strip())
        if not wav_path.is_file():
            warnings.append(f"{case_id}: missing wav file {wav_path}")
            continue
        if not reference_path.is_file():
            warnings.append(f"{case_id}: missing reference file {reference_path}")
            continue
        cases.append(
            ShootoutCase(
                case_id=case_id,
                title=title_value,
                wav_path=wav_path,
                reference_path=reference_path,
                summary=str(item.get("summary", "")).strip(),
                tags=tuple(str(tag) for tag in item.get("tags", [])),
                focus=tuple(str(focus) for focus in item.get("focus", [])),
            )
        )

    return {
        "corpus_id": corpus_id,
        "title": title,
        "reference_trust": reference_trust,
        "description": description,
        "manifest_path": str(path),
    }, warnings, cases


def run_manual_corpus_benchmark(
    manifest_path: Path | str,
    *,
    algorithms: Sequence[str] | None = None,
    tolerance_ms: float = 60.0,
) -> dict[str, Any]:
    if float(tolerance_ms) <= 0.0:
        raise ValueError("tolerance_ms must be > 0")

    corpus_meta, warnings, cases = load_manual_corpus_manifest(manifest_path)
    if not cases:
        raise ValueError(f"no usable cases found in {manifest_path}")

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
                "bpm": 0.0,
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
        "suite_version": SHOOTOUT_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fixtures_dir": str(Path(manifest_path).parent),
        "algorithms": algorithm_ids,
        "tolerance_ms": round(float(tolerance_ms), 3),
        "class_order": [],
        "manifest_format": "auralprimer_manual_corpus_v1",
        "cases": case_payloads,
        "warnings": warnings,
        "corpus": corpus_meta,
    }


def build_reference_shootout_payload(
    trusted_payload: Mapping[str, Any],
    suspect_payload: Mapping[str, Any],
) -> dict[str, Any]:
    trusted_summary = summarize_suite_results(trusted_payload)
    suspect_summary = summarize_suite_results(suspect_payload)
    algorithms = list(trusted_payload.get("algorithms", []))
    if not algorithms:
        algorithms = list(suspect_payload.get("algorithms", []))
    algorithms = _dedupe_preserve_order(algorithms)

    trusted_lookup = {
        str(item["algorithm"]): item for item in trusted_summary.get("algorithm_summaries", [])
    }
    suspect_lookup = {
        str(item["algorithm"]): item for item in suspect_summary.get("algorithm_summaries", [])
    }
    trusted_ranks = _build_rank_map(trusted_summary.get("algorithm_summaries", []))
    suspect_ranks = _build_rank_map(suspect_summary.get("algorithm_summaries", []))

    comparison_rows: list[dict[str, Any]] = []
    for algorithm in algorithms:
        trusted_metrics = _metric_snapshot(trusted_lookup.get(algorithm))
        suspect_metrics = _metric_snapshot(suspect_lookup.get(algorithm))
        comparison_rows.append(
            {
                "algorithm": algorithm,
                "trusted_rank": trusted_ranks.get(algorithm),
                "suspect_rank": suspect_ranks.get(algorithm),
                "rank_shift": None
                if trusted_ranks.get(algorithm) is None or suspect_ranks.get(algorithm) is None
                else int(suspect_ranks[algorithm]) - int(trusted_ranks[algorithm]),
                "trusted": trusted_metrics,
                "suspect": suspect_metrics,
                "delta_suspect_minus_trusted": {
                    "mean_overall_f1": _safe_diff(
                        suspect_metrics["mean_overall_f1"], trusted_metrics["mean_overall_f1"]
                    ),
                    "mean_core_f1": _safe_diff(
                        suspect_metrics["mean_core_f1"], trusted_metrics["mean_core_f1"]
                    ),
                    "mean_kick_f1": _safe_diff(
                        suspect_metrics["mean_kick_f1"], trusted_metrics["mean_kick_f1"]
                    ),
                    "mean_snare_f1": _safe_diff(
                        suspect_metrics["mean_snare_f1"], trusted_metrics["mean_snare_f1"]
                    ),
                    "mean_hi_hat_f1": _safe_diff(
                        suspect_metrics["mean_hi_hat_f1"], trusted_metrics["mean_hi_hat_f1"]
                    ),
                    "mean_timing_mae_ms": _safe_diff(
                        suspect_metrics["mean_timing_mae_ms"], trusted_metrics["mean_timing_mae_ms"]
                    ),
                },
            }
        )

    return {
        "shootout_version": SHOOTOUT_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "algorithms": algorithms,
        "tolerance_ms": float(trusted_payload.get("tolerance_ms", suspect_payload.get("tolerance_ms", 60.0))),
        "trusted": {
            "meta": dict(trusted_payload.get("corpus", {})),
            "payload": trusted_payload,
            "summary": trusted_summary,
        },
        "suspect": {
            "meta": dict(suspect_payload.get("corpus", {})),
            "payload": suspect_payload,
            "summary": suspect_summary,
        },
        "comparison": {
            "rows": comparison_rows,
        },
    }


def run_reference_shootout(
    *,
    suspect_manifest_path: Path | str,
    trusted_fixtures_dir: Path | str | None = None,
    trusted_summary_json: Path | str | None = None,
    algorithms: Sequence[str] | None = None,
    tolerance_ms: float = 60.0,
) -> dict[str, Any]:
    algorithm_ids = _collect_algorithm_ids(algorithms)
    if trusted_summary_json is not None:
        trusted_payload = json.loads(Path(trusted_summary_json).read_text("utf-8"))
        trusted_payload["algorithms"] = _collect_algorithm_ids(trusted_payload.get("algorithms", algorithm_ids))
        trusted_payload["corpus"] = {
            "corpus_id": "synthetic_trusted",
            "title": "Synthetic rendered fixture suite",
            "reference_trust": "trusted",
            "description": "Rendered drum benchmark fixtures with known-good authored MIDI references.",
            "manifest_path": str(Path(trusted_payload.get("fixtures_dir", "")) / "manifest.json"),
            "source_kind": "cached_summary_json",
            "source_path": str(Path(trusted_summary_json)),
        }
    else:
        trusted_path = Path(trusted_fixtures_dir) if trusted_fixtures_dir is not None else _default_fixtures_dir()
        trusted_payload = run_benchmark_suite(
            fixtures_dir=trusted_path,
            algorithms=algorithm_ids,
            tolerance_ms=tolerance_ms,
        )
        trusted_payload["corpus"] = {
            "corpus_id": "synthetic_trusted",
            "title": "Synthetic rendered fixture suite",
            "reference_trust": "trusted",
            "description": "Rendered drum benchmark fixtures with known-good authored MIDI references.",
            "manifest_path": str(trusted_path / "manifest.json"),
            "source_kind": "fresh_benchmark_run",
        }

    suspect_payload = run_manual_corpus_benchmark(
        suspect_manifest_path,
        algorithms=algorithm_ids,
        tolerance_ms=tolerance_ms,
    )

    return build_reference_shootout_payload(trusted_payload, suspect_payload)


def _render_markdown_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    divider = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([head, divider, *body])


def _render_html_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    head = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    body = []
    for row in rows:
        cells = "".join(f"<td>{html.escape(cell)}</td>" for cell in row)
        body.append(f"<tr>{cells}</tr>")
    return (
        '<table><thead><tr>'
        + head
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _trusted_rows(payload: Mapping[str, Any]) -> list[list[str]]:
    rows = []
    for item in payload["trusted"]["summary"]["algorithm_summaries"]:
        algorithm = str(item["algorithm"])
        rank = next(
            (
                row["trusted_rank"]
                for row in payload["comparison"]["rows"]
                if row["algorithm"] == algorithm
            ),
            None,
        )
        rows.append(
            [
                str(rank or "-"),
                algorithm,
                _format_score(item.get("mean_overall_f1")),
                _format_score(item.get("mean_core_f1")),
                _format_score(item.get("mean_kick_f1")),
                _format_score(item.get("mean_snare_f1")),
                _format_score(item.get("mean_hi_hat_f1")),
                _format_ms(item.get("mean_timing_mae_ms")),
            ]
        )
    return rows


def _suspect_rows(payload: Mapping[str, Any]) -> list[list[str]]:
    rows = []
    for item in payload["suspect"]["summary"]["algorithm_summaries"]:
        algorithm = str(item["algorithm"])
        rank = next(
            (
                row["suspect_rank"]
                for row in payload["comparison"]["rows"]
                if row["algorithm"] == algorithm
            ),
            None,
        )
        rows.append(
            [
                str(rank or "-"),
                algorithm,
                _format_score(item.get("mean_overall_f1")),
                _format_score(item.get("mean_core_f1")),
                _format_score(item.get("mean_kick_f1")),
                _format_score(item.get("mean_snare_f1")),
                _format_score(item.get("mean_hi_hat_f1")),
                _format_ms(item.get("mean_timing_mae_ms")),
            ]
        )
    return rows


def _delta_rows(payload: Mapping[str, Any]) -> list[list[str]]:
    rows = []
    for row in payload["comparison"]["rows"]:
        delta = row["delta_suspect_minus_trusted"]
        rows.append(
            [
                row["algorithm"],
                str(row["trusted_rank"] or "-"),
                str(row["suspect_rank"] or "-"),
                str(row["rank_shift"] if row.get("rank_shift") is not None else "-"),
                _format_delta(delta.get("mean_overall_f1")),
                _format_delta(delta.get("mean_core_f1")),
                _format_delta(delta.get("mean_kick_f1")),
                _format_delta(delta.get("mean_snare_f1")),
                _format_delta(delta.get("mean_hi_hat_f1")),
                _format_delta_ms(delta.get("mean_timing_mae_ms")),
            ]
        )
    return rows


def _render_report_markdown(payload: Mapping[str, Any]) -> str:
    trusted_meta = payload["trusted"]["meta"]
    suspect_meta = payload["suspect"]["meta"]
    trusted_cases = payload["trusted"]["payload"]["cases"]
    suspect_cases = payload["suspect"]["payload"]["cases"]
    lines = [
        "# Drum Reference Shootout",
        "",
        f"- Generated: `{payload['generated_at_utc']}`",
        f"- Tolerance: `{payload['tolerance_ms']}` ms",
        f"- Trusted corpus: `{trusted_meta.get('title', 'trusted')}` ({len(trusted_cases)} case(s), trust=`{trusted_meta.get('reference_trust', '')}`)",
        f"- Suspect corpus: `{suspect_meta.get('title', 'suspect')}` ({len(suspect_cases)} case(s), trust=`{suspect_meta.get('reference_trust', '')}`)",
        "",
        "Interpretation: negative F1 deltas mean the algorithm scored worse against the suspect Suno references than it did on the trusted synthetic fixture corpus. Positive timing deltas mean worse timing error on the suspect corpus.",
        "",
        "## Delta (Suspect - Trusted)",
        "",
        _render_markdown_table(
            ("Algorithm", "Trusted Rank", "Suspect Rank", "Rank Shift", "Overall Δ", "Core Δ", "Kick Δ", "Snare Δ", "Hi-Hat Δ", "Timing Δ"),
            _delta_rows(payload),
        ),
        "",
        "## Trusted Synthetic Corpus",
        "",
        _render_markdown_table(
            ("Rank", "Algorithm", "Overall", "Core", "Kick", "Snare", "Hi-Hat", "Timing"),
            _trusted_rows(payload),
        ),
        "",
        "## Suspect Suno Corpus",
        "",
        _render_markdown_table(
            ("Rank", "Algorithm", "Overall", "Core", "Kick", "Snare", "Hi-Hat", "Timing"),
            _suspect_rows(payload),
        ),
        "",
        "## Suspect Cases",
        "",
    ]

    for case in suspect_cases:
        lines.append(
            f"- `{case['case_id']}`: {case['title']}  "
            f"(audio=`{case['wav_path']}`, reference=`{case['reference_path']}`)"
        )
        if case.get("summary"):
            lines.append(f"  {case['summary']}")
    return "\n".join(lines)


def _render_report_html(payload: Mapping[str, Any]) -> str:
    trusted_meta = payload["trusted"]["meta"]
    suspect_meta = payload["suspect"]["meta"]
    suspect_cases = payload["suspect"]["payload"]["cases"]
    suspect_case_list = "".join(
        "<li><strong>"
        + html.escape(str(case["case_id"]))
        + "</strong>: "
        + html.escape(str(case["title"]))
        + " <code>"
        + html.escape(str(case["wav_path"]))
        + "</code> vs <code>"
        + html.escape(str(case["reference_path"]))
        + "</code>"
        + (
            "<br />" + html.escape(str(case["summary"]))
            if case.get("summary")
            else ""
        )
        + "</li>"
        for case in suspect_cases
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Drum Reference Shootout</title>
  <style>
    body {{
      font-family: Segoe UI, Arial, sans-serif;
      background: #0f141b;
      color: #eef2f7;
      margin: 0;
      padding: 24px;
    }}
    h1, h2 {{
      margin: 0 0 12px;
    }}
    p, li {{
      color: #c8d3df;
      line-height: 1.45;
    }}
    .meta {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin: 16px 0 24px;
    }}
    .card {{
      background: #161d27;
      border: 1px solid #273142;
      border-radius: 12px;
      padding: 14px 16px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin: 12px 0 28px;
      background: #161d27;
      border: 1px solid #273142;
    }}
    th, td {{
      border-bottom: 1px solid #273142;
      padding: 8px 10px;
      text-align: left;
      font-size: 14px;
    }}
    th {{
      background: #1d2633;
    }}
    code {{
      color: #a6e3ff;
    }}
  </style>
</head>
<body>
  <h1>Drum Reference Shootout</h1>
  <div class="meta">
    <div class="card"><strong>Generated</strong><br />{html.escape(str(payload['generated_at_utc']))}</div>
    <div class="card"><strong>Tolerance</strong><br />{html.escape(str(payload['tolerance_ms']))} ms</div>
    <div class="card"><strong>Trusted corpus</strong><br />{html.escape(str(trusted_meta.get('title', 'trusted')))}</div>
    <div class="card"><strong>Suspect corpus</strong><br />{html.escape(str(suspect_meta.get('title', 'suspect')))}</div>
  </div>
  <p>Negative F1 deltas mean the algorithm scored worse against the suspect Suno references than it did on the trusted synthetic fixture corpus. Positive timing deltas mean worse timing error on the suspect corpus.</p>
  <h2>Delta (Suspect - Trusted)</h2>
  {_render_html_table(("Algorithm", "Trusted Rank", "Suspect Rank", "Rank Shift", "Overall Δ", "Core Δ", "Kick Δ", "Snare Δ", "Hi-Hat Δ", "Timing Δ"), _delta_rows(payload))}
  <h2>Trusted Synthetic Corpus</h2>
  {_render_html_table(("Rank", "Algorithm", "Overall", "Core", "Kick", "Snare", "Hi-Hat", "Timing"), _trusted_rows(payload))}
  <h2>Suspect Suno Corpus</h2>
  {_render_html_table(("Rank", "Algorithm", "Overall", "Core", "Kick", "Snare", "Hi-Hat", "Timing"), _suspect_rows(payload))}
  <h2>Suspect Cases</h2>
  <ul>{suspect_case_list}</ul>
</body>
</html>"""


def write_reference_shootout_outputs(
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

    artifacts = {
        "report.md": _render_report_markdown(payload),
        "report.html": _render_report_html(payload),
        "summary.json": json.dumps(payload, indent=2),
    }
    for name, content in artifacts.items():
        (out_dir / name).write_text(content, encoding="utf-8")

    missing = [name for name in REQUIRED_OUTPUT_FILES if not (out_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"shootout run incomplete: missing {', '.join(missing)}")

    latest_marker = out_dir.parent.parent / "LATEST_RUN.txt"
    latest_marker.parent.mkdir(parents=True, exist_ok=True)
    latest_marker.write_text(str(out_dir), encoding="utf-8")
    return out_dir
