r"""Run a MUSDB18/MUSDB18-HQ source-separation SDR benchmark.

Example:
    $env:AURAL_MUSDB18_HQ_ROOT = "E:\AudioSourceOfTruthData\musdb18hq"
    $env:AURAL_MODEL_UPGRADE_EVIDENCE_ROOT = "D:\AuralPrimer"
    py -3 benchmarks/quality/run_musdb_separation_sdr.py --provider demucs --split test --limit 10 --write-gate-evidence

This is an internal benchmark runner: it reads local MUSDB stems, runs the
configured AuralPrimer separation provider, maps AuralPrimer's stems back to
MUSDB's vocals/drums/bass/other targets, and evaluates with museval.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "python" / "ingest" / "src"
sys.path.insert(0, str(SRC))

from aural_ingest.cli import _run_stem_separation, _sha256_file  # noqa: E402
from aural_ingest.quality_benchmark import (  # noqa: E402
    discover_musdb18_tracks,
    evaluate_museval_separation,
    prepare_musdb_estimate_stems,
    summarize_museval_separation_runs,
)

MODEL_UPGRADE_EVIDENCE_ROOT_ENV = "AURAL_MODEL_UPGRADE_EVIDENCE_ROOT"
MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST = Path("benchmarks") / "runtime" / "model_upgrade_gate_evidence.md"
QUALITY_EVIDENCE_DIR = Path("benchmarks") / "quality" / "runs"
QUALITY_EXPLORATORY_DIR = Path("benchmarks") / "quality" / "exploratory_runs"
MUSDB_PROMOTION_MIN_TRACKS = 10


def _default_musdb_root() -> Path | None:
    for env in ("AURAL_MUSDB18_HQ_ROOT", "AURAL_MUSDB18_ROOT"):
        raw = os.environ.get(env, "").strip()
        if raw:
            return Path(raw).expanduser()
    return None


def _resolve_estimated_stems(stems_dir: Path, stem_paths: dict[str, Any]) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for role, raw in stem_paths.items():
        if not isinstance(raw, str) or not raw.strip():
            continue
        rel = Path(raw)
        if rel.is_absolute():
            out[str(role)] = rel
            continue
        candidate = stems_dir / rel.name
        if candidate.is_file():
            out[str(role)] = candidate
            continue
        candidate = stems_dir / rel
        if candidate.is_file():
            out[str(role)] = candidate
    return out


def _load_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _safe_filename_token(value: object) -> str:
    text = str(value).strip().lower()
    token = "".join(ch if ch.isalnum() else "_" for ch in text).strip("_")
    return token or "unknown"


def _model_upgrade_evidence_root() -> Path:
    raw = os.environ.get(MODEL_UPGRADE_EVIDENCE_ROOT_ENV, "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    cwd = Path.cwd().resolve()
    if (cwd / MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST).is_file():
        return cwd
    return ROOT


def _gate_evidence_output_path(provider: str, config: dict[str, Any]) -> Path:
    root = _model_upgrade_evidence_root()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    modelpack_id = config.get("stem_separation_modelpack_id")
    parts = [_safe_filename_token(provider)]
    if isinstance(modelpack_id, str) and modelpack_id.strip():
        parts.append(_safe_filename_token(modelpack_id))
    stem = "_".join(parts + ["musdb_separation_sdr"])
    return root / QUALITY_EVIDENCE_DIR / f"{stamp}_{stem}.json"


def main() -> None:
    parser = argparse.ArgumentParser(prog="run_musdb_separation_sdr")
    parser.add_argument("--musdb-root", default=None)
    parser.add_argument("--split", choices=["train", "test"], default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--provider", default="demucs")
    parser.add_argument("--provider-path", default=None)
    parser.add_argument("--shifts", type=int, default=1)
    parser.add_argument("--config-json", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument(
        "--write-gate-evidence",
        action="store_true",
        help="Write the report to the runtime-check model-upgrade gate evidence directory.",
    )
    parser.add_argument("--keep-estimates", action="store_true")
    args = parser.parse_args()
    if args.write_gate_evidence and args.output:
        parser.error("--write-gate-evidence cannot be combined with --output")
    if args.write_gate_evidence and args.split != "test":
        parser.error("--write-gate-evidence requires --split test")

    musdb_root = Path(args.musdb_root).expanduser() if args.musdb_root else _default_musdb_root()
    if musdb_root is None:
        raise SystemExit("set --musdb-root or AURAL_MUSDB18_HQ_ROOT / AURAL_MUSDB18_ROOT")

    tracks = discover_musdb18_tracks(musdb_root, split=args.split, limit=args.limit)
    if not tracks:
        raise SystemExit(f"no MUSDB tracks found under {musdb_root}")

    config = _load_config(args.config_json)
    config.setdefault("stem_separation_provider", args.provider)
    if args.write_gate_evidence:
        output = _gate_evidence_output_path(str(args.provider), config)
    elif args.output:
        output = Path(args.output)
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        output = ROOT / QUALITY_EXPLORATORY_DIR / f"{timestamp}_musdb_separation_sdr.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="auralprimer_musdb_sdr_") as tmp_name:
        tmp_root = Path(tmp_name)
        estimates_root = output.parent / f"{output.stem}_estimates" if args.keep_estimates else tmp_root
        estimates_root.mkdir(parents=True, exist_ok=True)

        for index, track in enumerate(tracks, start=1):
            track_work = estimates_root / f"{index:03d}_{track.name}"
            stems_dir = track_work / "stems"
            eval_dir = track_work / "musdb_estimates"
            stems_dir.mkdir(parents=True, exist_ok=True)
            try:
                separation = _run_stem_separation(
                    track.mixture_path,
                    stems_dir,
                    mix_sha256=_sha256_file(track.mixture_path),
                    shifts=max(1, int(args.shifts)),
                    config=config,
                    provider_name=str(args.provider),
                    provider_path=args.provider_path,
                    protected_roles=(),
                )
                stem_paths = separation.get("stem_paths") if isinstance(separation, dict) else {}
                if not isinstance(stem_paths, dict) or not stem_paths:
                    evaluation = {
                        "available": True,
                        "backend": "museval",
                        "status": "skipped",
                        "reason": "separation provider returned no stem_paths",
                    }
                else:
                    estimated = _resolve_estimated_stems(stems_dir, stem_paths)
                    musdb_estimated = prepare_musdb_estimate_stems(
                        estimated,
                        eval_dir,
                        stems_dir=stems_dir,
                    )
                    evaluation = evaluate_museval_separation(
                        track.reference_stems,
                        musdb_estimated,
                    )
            except Exception as exc:  # noqa: BLE001 - benchmark records failures per track.
                separation = {"ok": False, "status": "failed", "error": str(exc)}
                evaluation = {
                    "available": True,
                    "backend": "museval",
                    "status": "failed",
                    "errors": {"runner": str(exc)},
                }

            row = {
                "track_id": track.track_id,
                "name": track.name,
                "split": track.split,
                "mixture": str(track.mixture_path),
                "separation": separation,
                "evaluation": evaluation,
            }
            results.append(row)
            status = evaluation.get("status") if isinstance(evaluation, dict) else "unknown"
            print(f"[{index:>3}/{len(tracks)}] {track.track_id} {status}", file=sys.stderr)

    summary = summarize_museval_separation_runs(results)
    tracks_ok = int(summary.get("tracks_ok") or 0)
    tracks_failed = int(summary.get("tracks_failed") or 0)
    tracks_skipped = int(summary.get("tracks_skipped") or 0)
    ok = tracks_ok > 0 and tracks_failed == 0 and tracks_skipped == 0
    promotion_usable = ok and tracks_ok >= MUSDB_PROMOTION_MIN_TRACKS
    payload = {
        "ok": ok,
        "promotion_usable": promotion_usable,
        "promotion_min_tracks": MUSDB_PROMOTION_MIN_TRACKS,
        "dataset": "musdb18_or_musdb18_hq",
        "musdb_root": str(musdb_root),
        "provider": args.provider,
        "provider_path": args.provider_path,
        "config": config,
        "split": args.split,
        "limit": args.limit,
        "tracks": results,
        "summary": summary,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(output)
    if not ok:
        raise SystemExit(f"MUSDB SDR run had failed/skipped/no successful evaluations; wrote {output}")
    if args.write_gate_evidence and not promotion_usable:
        raise SystemExit(
            f"MUSDB gate evidence requires at least {MUSDB_PROMOTION_MIN_TRACKS} successful tracks; wrote {output}"
        )


if __name__ == "__main__":
    main()
