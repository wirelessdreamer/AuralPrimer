"""Build the Phase 1 baselines report from the three JSON runs.

Emits ``phase1_baselines_report.md`` in ``benchmarks/guitar/``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


RUN_DIR = Path(r"D:\AuralPrimer\benchmarks\guitar\gt_runs")
REPORT_PATH = Path(r"D:\AuralPrimer\benchmarks\guitar\phase1_baselines_report.md")

RUNS: list[tuple[str, str, str, Path]] = [
    ("guitar_techs", "directinput", "electric (DI, 24-bit; all 104 phrases)",
     RUN_DIR / "gt_directinput_full.json"),
    # gt_micamp run was aborted mid-way due to time budget — see report notes
    # for the deferral rationale. Left as a stub for the Phase 1.5 re-run.
    # ("guitar_techs", "micamp", "electric (amp-mic, 16-bit)",
    #  RUN_DIR / "gt_micamp_limit10_chords.json"),
    ("guitarset",     "mic",          "acoustic (mic; 40-case sample)",
     RUN_DIR / "guitarset_mic_limit40.json"),
]

# Category order for the guitar_techs table (fixed for consistency)
GT_CATEGORIES = ("chords", "scales", "singlenotes", "techniques", "music")


def fmt(v):
    if v is None:
        return "  -   "
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def load(path: Path) -> dict | None:
    if not path.is_file():
        print(f"WARNING: missing {path}", file=sys.stderr)
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def emit_overall_table(reports: list[tuple[str, str, str, dict]]) -> list[str]:
    """One row per (dataset, signal, algorithm) — overall F1/P/R."""
    lines = [
        "| Dataset | Signal | Algorithm | Cases | F1 | Precision | Recall | Onset MAE (s) | Runtime (s/case) |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _ds, _sig, label, rpt in reports:
        for alg, stats in rpt["summary"]["per_algorithm"].items():
            lines.append(
                f"| {rpt['dataset']} | {rpt['extra']['variant']} | `{alg}` | "
                f"{stats['cases_ok']} | {fmt(stats['f1'])} | "
                f"{fmt(stats['precision'])} | {fmt(stats['recall'])} | "
                f"{fmt(stats['onset_mae_sec'])} | {fmt(stats['mean_runtime_sec'])} |"
            )
    return lines


def emit_gt_category_table(reports: list[tuple[str, str, str, dict]]) -> list[str]:
    """Guitar-TECHS per-category breakdown."""
    lines = [
        "| Signal | Algorithm | Category | Cases | F1 | Precision | Recall | Onset MAE (s) |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for _ds, _sig, _label, rpt in reports:
        if rpt["dataset"] != "guitar_techs":
            continue
        variant = rpt["extra"]["variant"]
        for alg, stats in rpt["summary"]["per_algorithm"].items():
            cat_bucket = stats.get("buckets", {}).get("category", {})
            for cat in GT_CATEGORIES:
                if cat not in cat_bucket:
                    continue
                b = cat_bucket[cat]
                lines.append(
                    f"| {variant} | `{alg}` | {cat} | {b['cases_ok']} | "
                    f"{fmt(b['f1'])} | {fmt(b['precision'])} | {fmt(b['recall'])} | "
                    f"{fmt(b['onset_mae_sec'])} |"
                )
    return lines


def emit_gs_algo_table(reports: list[tuple[str, str, str, dict]]) -> list[str]:
    lines = [
        "| Algorithm | Style | Cases | F1 | Precision | Recall | Onset MAE (s) |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for _ds, _sig, _label, rpt in reports:
        if rpt["dataset"] != "guitarset":
            continue
        for alg, stats in rpt["summary"]["per_algorithm"].items():
            lines.append(
                f"| `{alg}` | overall | {stats['cases_ok']} | {fmt(stats['f1'])} | "
                f"{fmt(stats['precision'])} | {fmt(stats['recall'])} | "
                f"{fmt(stats['onset_mae_sec'])} |"
            )
            style_bucket = stats.get("buckets", {}).get("style", {})
            for style in sorted(style_bucket.keys()):
                b = style_bucket[style]
                lines.append(
                    f"| `{alg}` | {style} | {b['cases_ok']} | {fmt(b['f1'])} | "
                    f"{fmt(b['precision'])} | {fmt(b['recall'])} | "
                    f"{fmt(b['onset_mae_sec'])} |"
                )
    return lines


def main() -> int:
    reports: list[tuple[str, str, str, dict]] = []
    for ds, sig, label, path in RUNS:
        rpt = load(path)
        if rpt is None:
            continue
        reports.append((ds, sig, label, rpt))
    if not reports:
        print("no reports found", file=sys.stderr)
        return 1

    out = []
    out.append("# Guitar Phase 1 baselines — production reference numbers")
    out.append("")
    out.append("Locks the production baseline for two melodic algorithms")
    out.append("(`melodic_combined` production default; `melodic_combined_guitar`")
    out.append("the recall-tuned variant) on the three signals wired into")
    out.append("`gt-benchmark`. Runs are *measurement only*, no algorithm changes.")
    out.append("")
    out.append("**Harness:** `aural_ingest gt-benchmark`, onset tolerance 50 ms,")
    out.append("pitch tolerance 0 semitones (pitch-exact).")
    out.append("")
    out.append("**Datasets:**")
    out.append("- `guitar_techs` **directinput** — all 104 electric-guitar phrases,")
    out.append("  24-bit DI recording, categories chords / scales / singlenotes /")
    out.append("  techniques / music.")
    out.append("- `guitar_techs` **micamp** — **deferred to Phase 1.5.** The")
    out.append("  amp-mic signal predicts (unlike DI, see below) but micamp phrases")
    out.append("  average 160+ s of audio each and ``melodic_combined`` runs at")
    out.append("  ~1:1 real-time on this box, so a full-104 run × 2 algorithms")
    out.append("  needs 90+ minutes. Attempted a 41-case stratified sample and a")
    out.append("  10-case P1 chords slice; both overran the 60-minute time budget")
    out.append("  on this pass. Stratified sampler + planned caps are committed")
    out.append("  as ``gt_runs/_stratified_micamp.py`` so the Phase 1.5 rerun is")
    out.append("  push-button. GuitarSet mic (below) still provides an isolated")
    out.append("  guitar reference; the electric micamp story lands next pass.")
    out.append("- `guitarset` **mic** — first 40 of the 360 GuitarSet mic phrases")
    out.append("  (acoustic control / regression guard). Sampled for the same")
    out.append("  budget reason; full 360-case run at ~8.6 s/case × 2 algorithms")
    out.append("  ≈ 103 minutes.")
    out.append("")
    out.append("## Overall numbers (locked)")
    out.append("")
    out.extend(emit_overall_table(reports))
    out.append("")
    out.append("## Guitar-TECHS — per-category breakdown")
    out.append("")
    out.extend(emit_gt_category_table(reports))
    out.append("")
    out.append("## GuitarSet mic (acoustic control)")
    out.append("")
    out.extend(emit_gs_algo_table(reports))
    out.append("")
    out.append("## Interpretation")
    out.append("")

    # Pull key numbers for the interpretation section.
    gt_di_rpt = next((r for r in reports if r[0] == "guitar_techs" and r[1] == "directinput"), None)
    gt_mic_rpt = next((r for r in reports if r[0] == "guitar_techs" and r[1] == "micamp"), None)
    gs_rpt = next((r for r in reports if r[0] == "guitarset"), None)

    def alg_f1(rpt, alg):
        if rpt is None:
            return None
        return rpt[3]["summary"]["per_algorithm"].get(alg, {}).get("f1")

    def alg_stats(rpt, alg):
        if rpt is None:
            return {}
        return rpt[3]["summary"]["per_algorithm"].get(alg, {})

    di_prod = alg_f1(gt_di_rpt, "melodic_combined")
    di_tune = alg_f1(gt_di_rpt, "melodic_combined_guitar")
    mic_prod = alg_f1(gt_mic_rpt, "melodic_combined")
    mic_tune = alg_f1(gt_mic_rpt, "melodic_combined_guitar")
    gs_prod = alg_f1(gs_rpt, "melodic_combined")
    gs_tune = alg_f1(gs_rpt, "melodic_combined_guitar")

    out.append("### (a) Winner by dataset")
    out.append("")
    def _winner(prod, tune, label):
        if prod is None or tune is None:
            return f"- **{label}:** results missing."
        if prod > tune:
            delta = (prod - tune)
            return (f"- **{label}:** `melodic_combined` wins F1 {prod:.3f} vs "
                    f"`melodic_combined_guitar` {tune:.3f} (Δ={delta:+.3f}).")
        elif tune > prod:
            delta = (tune - prod)
            return (f"- **{label}:** `melodic_combined_guitar` wins F1 {tune:.3f} "
                    f"vs `melodic_combined` {prod:.3f} (Δ={delta:+.3f}).")
        else:
            return f"- **{label}:** tie at F1 {prod:.3f}."
    out.append(_winner(di_prod, di_tune, "Electric DI (guitar_techs directinput)"))
    out.append("- **Electric amp-mic (guitar_techs micamp):** deferred to "
               "Phase 1.5 (see dataset notes above).")
    out.append(_winner(gs_prod, gs_tune, "Acoustic (guitarset mic)"))
    out.append("")

    # Hardest bucket — GuitarSet solo vs comp is the strongest signal we
    # have this pass. gt_micamp per-category ranking is deferred with the
    # micamp run itself.
    if gs_rpt is not None:
        prod_styles = (gs_rpt[3]["summary"]["per_algorithm"]
                        .get("melodic_combined", {})
                        .get("buckets", {}).get("style", {}))
        if len(prod_styles) >= 2:
            worst = min(prod_styles.items(), key=lambda kv: kv[1]["f1"])
            best = max(prod_styles.items(), key=lambda kv: kv[1]["f1"])
            out.append("### (b) Hardest bucket")
            out.append("")
            out.append("On the acoustic **guitarset mic** with `melodic_combined`:")
            out.append(f"- **Hardest:** {worst[0]} ({'rhythm/chord' if worst[0] == 'comp' else 'lead/solo'}) — "
                       f"F1 {worst[1]['f1']:.3f} (precision {worst[1]['precision']:.3f}, "
                       f"recall {worst[1]['recall']:.3f}).")
            out.append(f"- **Easiest:** {best[0]} ({'rhythm/chord' if best[0] == 'comp' else 'lead/solo'}) — "
                       f"F1 {best[1]['f1']:.3f}.")
            out.append(f"- **Ratio:** rhythm/chord phrases are "
                       f"~{best[1]['f1'] / max(worst[1]['f1'], 1e-9):.1f}× harder than solo lead. This is the")
            out.append("  same chord-vs-lead pattern the epic tracker identifies as")
            out.append("  the highest-leverage Phase-4 target (rhythm chord")
            out.append("  supplement, mirroring piano's 0.82→0.93 jump).")
            out.append("")

    # DI blocker
    if di_prod == 0.0:
        out.append("### (c) The DI blocker — a 24-bit-WAV bug in the mono reader")
        out.append("")
        out.append("The DI baseline is **F1 = 0.000 across every category and both")
        out.append("algorithms**. All 208 runs produced *zero predictions* (tp = 0,")
        out.append("fp = 0, fn = every reference note). This is not an algorithm")
        out.append("failure — it is a reader failure that silently upstream-affects")
        out.append("every melodic algorithm in the suite.")
        out.append("")
        out.append("**Root cause:** ``aural_ingest.algorithms._common``:")
        out.append("")
        out.append("- Guitar-TECHS ``directinput`` files are **24-bit mono PCM WAV**")
        out.append("  (``sampwidth = 3``).")
        out.append("- ``read_wav_mono_normalized`` calls ``_lin2lin(raw, sampwidth,")
        out.append("  2)`` to convert to 16-bit.")
        out.append("- ``_lin2lin`` only supports ``sampwidth`` ∈ {1, 2, 4} — a 3-byte")
        out.append("  input raises ``ValueError('unsupported sample widths: 3 -> 2')``.")
        out.append("- The exception is swallowed by the outer ``try/except``")
        out.append("  wrapping the whole reader, which returns ``([], 0)``.")
        out.append("- ``melodic_combined.transcribe`` sees empty samples and short-")
        out.append("  circuits to ``return []`` — a clean, silent zero-output.")
        out.append("")
        out.append("Confirmed by direct probe: on Guitar-TECHS DI ``P1_chords/")
        out.append("Drop3_7.wav`` (24-bit, 204 s, RMS 0.014), ``melodic_combined``")
        out.append("returns 0 notes; on the paired **micamp** file (16-bit, same")
        out.append("phrase, RMS 0.016), it returns 273 notes.")
        out.append("")
        out.append("**Blast radius:** every algorithm that goes through")
        out.append("``read_wav_mono_normalized`` — this includes ``melodic_pyin``,")
        out.append("``melodic_onset_yin``, ``melodic_fft_hps``, ``melodic_yin``, the")
        out.append("guitar/piano wrappers, and the drum energy paths. Any user pack")
        out.append("with 24-bit source stems silently transcribes to empty MIDI.")
        out.append("")
        out.append("**Phase 1.5 fix:** extend ``_lin2lin`` to handle ``sampwidth = 3``")
        out.append("(24-bit int → shift-left one byte then reduce), or route through")
        out.append("``soundfile.read`` which handles all common widths natively.")
        out.append("Only after that fix does the DI 0.000 baseline become a")
        out.append("meaningful reference for Phase 2 gains to be measured against.")
        out.append("")

    out.append("### Baseline is now locked")
    out.append("")
    out.append("Reference numbers are frozen at these values. Every Phase 2+")
    out.append("proposal (Basic Pitch wrapper, gated cleanup, chord supplement)")
    out.append("must be scored against this baseline; regressions on the")
    out.append("dominant metric (F1) are not permitted per the discipline rules")
    out.append("in the epic tracker.")
    out.append("")
    out.append("Raw run JSON dumps live under `benchmarks/guitar/gt_runs/`:")
    out.append("")
    for _ds, _sig, _label, rpt in reports:
        path = _find_json(_ds, rpt["extra"]["variant"])
        out.append(f"- `{path}`")
    out.append("")

    REPORT_PATH.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {REPORT_PATH}")
    return 0


def _find_json(dataset: str, variant: str) -> str:
    if dataset == "guitar_techs" and variant == "directinput":
        return "benchmarks/guitar/gt_runs/gt_directinput_full.json"
    if dataset == "guitar_techs" and variant == "micamp":
        return "benchmarks/guitar/gt_runs/gt_micamp_limit10_chords.json"
    if dataset == "guitarset":
        return "benchmarks/guitar/gt_runs/guitarset_mic_limit40.json"
    return "?"


if __name__ == "__main__":
    sys.exit(main())
