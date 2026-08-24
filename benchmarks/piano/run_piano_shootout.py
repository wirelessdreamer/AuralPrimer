#!/usr/bin/env python3
"""Parallel, resumable piano-transcription shootout.

Modes
-----
``run``     execute every (item, algorithm) task, caching predictions to disk
``task``    internal single-task worker; runs in its own process so a crash,
            hang or CUDA OOM in one engine cannot take down the whole run
``report``  build metrics + report.md/report.html + the PROCESS.md SVGs

Predictions are cached per (item, algorithm) under ``predictions/``, so
``report`` can be re-run after new ground truth lands (e.g. official sheet
music aligned to the recording) without re-running any transcription.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
INGEST_SRC = REPO_ROOT / "python" / "ingest" / "src"
DEFAULT_RUN_ROOT = REPO_ROOT / "benchmarks" / "piano" / "runs"

# Engines that put real work on the GPU; throttled separately so a wide CPU
# fan-out does not oversubscribe CUDA.
GPU_ALGORITHMS = {
    "piano_auto",
    "piano_pti",
    "piano_pti_clean",
    "piano_pti_clean_dedup",
    "piano_pti_clean_dedup_pyin",
    "piano_pti_consensus",
    "piano_pti_consensus_clean",
    "piano_transkun",
    "piano_transkun_clean",
    "melodic_rmvpe",
}

# The full roster for this round. HFT and D3RM are deliberately absent: HFT has
# no local checkpoint and D3RM ships only a MAESTRO-test-set Lightning harness,
# so neither can transcribe arbitrary audio today. Both are reported as
# unavailable rather than silently scored as zero.
ALGORITHMS = [
    "piano_auto",
    "piano_basic_pitch",
    "piano_basic_pitch_clean",
    "piano_basic_pitch_playable",
    "piano_polyphonic",
    "piano_polyphonic_clean",
    "piano_transkun",
    "piano_transkun_clean",
    "piano_pti",
    "piano_pti_clean",
    "piano_pti_clean_dedup",
    "piano_pti_clean_dedup_pyin",
    "piano_pti_consensus",
    "piano_pti_consensus_clean",
    "piano_chord_supplement",
    "melodic_hpss_combined",
    "melodic_octave_fix",
    "melodic_combined",
    "melodic_adaptive",
    "melodic_template_multipass",
    "melodic_yin_octave_hps_fix",
    "melodic_rmvpe",
    "basic_pitch",
    "pyin",
]


def _task_env() -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(INGEST_SRC) + (os.pathsep + existing if existing else "")
    # The torch>=2 gate in transcription.piano_pti_enabled() disables every PTI
    # producer. Re-testing whether that gate is still warranted is one of this
    # round's questions, so force them on and let the metrics decide.
    env["AURAL_ENABLE_PIANO_PTI"] = "1"
    # Many workers x a BLAS that grabs every core thrashes on a 32-thread box.
    # Cap per-task threads so the fan-out is the parallelism, not oversubscription.
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env.setdefault(var, "4")
    return env


def _prediction_path(run_dir: Path, item_id: str, algorithm: str) -> Path:
    return run_dir / "predictions" / item_id / f"{algorithm}.json"


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        import shutil

        shutil.copy2(src, dst)


def build_song_layout(run_dir: Path, item: dict[str, Any]) -> Path:
    """Materialise an AuralSong-shaped layout for one item and return the stem path.

    ``piano_pti_consensus`` locates the full mix by walking up from the stem
    (``<pack>/audio/stems/<x>.wav`` -> ``<pack>/audio/mix.wav``). Our stems live
    in a demucs output tree, so without this the consensus variants silently
    degrade to a plain stem-only pass. Hardlinks, so this costs no disk.
    """
    audio_dir = run_dir / "song_layout" / item["id"] / "audio"
    stem_dst = audio_dir / "stems" / "keys.wav"
    _link_or_copy(Path(item["wav"]), stem_dst)
    mix = item.get("mix")
    if mix:
        _link_or_copy(Path(mix), audio_dir / "mix.wav")
    return stem_dst


def run_task(run_dir: Path, item: dict[str, Any], algorithm: str, wav: Path) -> dict[str, Any]:
    """Worker body: transcribe one item with one engine, write JSON + MIDI."""
    sys.path.insert(0, str(INGEST_SRC))
    from aural_ingest.piano_benchmark import (
        melodic_notes_to_dicts,
        summarize_piano_predictions,
        write_melodic_notes_midi,
    )
    from aural_ingest.transcription import build_default_melodic_algorithm_registry

    out_json = _prediction_path(run_dir, item["id"], algorithm)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    registry = build_default_melodic_algorithm_registry(instrument=item.get("instrument", "keys"))
    fn = registry.get(algorithm)
    record: dict[str, Any] = {
        "item_id": item["id"],
        "algorithm": algorithm,
        "wav": str(wav),
        "tier": item.get("tier"),
    }
    if fn is None:
        record.update(status="unavailable", error="algorithm not in registry", notes=[])
    else:
        t0 = time.time()
        try:
            notes = fn(wav)
        except Exception as exc:  # engine failures are data, not a run abort
            record.update(
                status="error",
                elapsed_sec=round(time.time() - t0, 2),
                error=f"{type(exc).__name__}: {exc}"[:2000],
                notes=[],
            )
        else:
            record.update(
                status="ok",
                elapsed_sec=round(time.time() - t0, 2),
                note_count=len(notes),
                prediction=summarize_piano_predictions(notes),
                notes=melodic_notes_to_dicts(notes),
            )
            # Convenience artifact only -- a failure here must not be reported
            # as a transcription failure, the notes above are the real result.
            try:
                write_melodic_notes_midi(notes, out_json.with_suffix(".mid"))
            except Exception as exc:
                record["midi_write_error"] = f"{type(exc).__name__}: {exc}"[:500]
    out_json.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def orchestrate(args: argparse.Namespace) -> int:
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    items = manifest["items"]
    if args.item:
        wanted = set(args.item)
        items = [i for i in items if i["id"] in wanted]
    algorithms = args.algorithm or ALGORITHMS

    run_dir = Path(args.out) if args.out else (
        DEFAULT_RUN_ROOT / f"{datetime.now():%Y%m%d_%H%M%S}_{manifest.get('label', 'piano-shootout')}"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    stem_for: dict[str, Path] = {i["id"]: build_song_layout(run_dir, i) for i in items}

    tasks = [
        (item, algo)
        for item in items
        for algo in algorithms
        if not (args.resume and _prediction_path(run_dir, item["id"], algo).is_file())
    ]
    total = len(items) * len(algorithms)
    print(f"run dir : {run_dir}")
    print(f"items   : {len(items)}   algorithms: {len(algorithms)}")
    print(f"tasks   : {len(tasks)} to run, {total - len(tasks)} already cached")
    print(f"workers : {args.workers} ({args.gpu_workers} concurrent GPU)")
    sys.stdout.flush()

    gpu_slots = threading.Semaphore(args.gpu_workers)
    log_lock = threading.Lock()
    log_path = run_dir / "tasks.jsonl"
    done = [0]
    started = time.time()

    def worker(task: tuple[dict[str, Any], str]) -> None:
        item, algo = task
        gated = algo in GPU_ALGORITHMS
        if gated:
            gpu_slots.acquire()
        t0 = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "task",
                 "--manifest", str(args.manifest), "--out", str(run_dir),
                 "--item", item["id"], "--algorithm", algo,
                 "--wav", str(stem_for[item["id"]])],
                env=_task_env(), capture_output=True, text=True, timeout=args.timeout,
            )
            status = "ok" if proc.returncode == 0 else f"subprocess_rc_{proc.returncode}"
            detail = (proc.stderr or "")[-600:]
        except subprocess.TimeoutExpired:
            status, detail = "timeout", f"exceeded {args.timeout}s"
        finally:
            if gated:
                gpu_slots.release()
        elapsed = time.time() - t0

        rec = _prediction_path(run_dir, item["id"], algo)
        note_count = None
        if rec.is_file():
            # The subprocess exiting 0 only means the worker ran; the engine
            # itself may have raised or returned nothing. Report what the
            # record says, so a silent zero cannot masquerade as a pass.
            try:
                payload = json.loads(rec.read_text(encoding="utf-8"))
                note_count = payload.get("note_count")
                inner = str(payload.get("status") or "")
                if inner and inner != "ok":
                    status = inner
                    detail = str(payload.get("error") or "")[:600]
                elif status == "ok" and not note_count:
                    status = "ok_but_empty"
            except Exception:
                pass
        elif status != "ok":
            # Guarantee a record exists so `report` sees the failure and
            # `--resume` does not retry a task that will just fail again.
            rec.parent.mkdir(parents=True, exist_ok=True)
            rec.write_text(json.dumps({
                "item_id": item["id"], "algorithm": algo, "tier": item.get("tier"),
                "status": status, "error": detail, "elapsed_sec": round(elapsed, 2), "notes": [],
            }, indent=2) + "\n", encoding="utf-8")

        with log_lock:
            done[0] += 1
            rate = done[0] / max(1e-9, time.time() - started)
            eta = (len(tasks) - done[0]) / rate if rate else 0.0
            shown = note_count if note_count is not None else "-"
            print(f"[{done[0]:4d}/{len(tasks)}] {status:16s} {elapsed:7.1f}s "
                  f"notes={shown:>6} {item['id']}/{algo}   ETA {eta / 3600:.1f}h", flush=True)
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"item": item["id"], "algorithm": algo, "status": status,
                                     "elapsed_sec": round(elapsed, 2), "notes": note_count,
                                     "detail": detail if status != "ok" else ""}) + "\n")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(worker, tasks))

    wall = (time.time() - started) / 3600
    print(f"\nALL TASKS COMPLETE in {wall:.2f}h -> {run_dir}", flush=True)
    (run_dir / "RUN_COMPLETE").write_text(
        json.dumps({"finished": datetime.now().isoformat(timespec="seconds"),
                    "tasks": len(tasks), "wall_hours": round(wall, 3)}, indent=2) + "\n",
        encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="mode", required=True)

    r = sub.add_parser("run", help="execute all (item, algorithm) tasks")
    r.add_argument("--manifest", required=True)
    r.add_argument("--out")
    r.add_argument("--algorithm", action="append")
    r.add_argument("--item", action="append")
    r.add_argument("--workers", type=int, default=12)
    r.add_argument("--gpu-workers", type=int, default=3)
    r.add_argument("--timeout", type=int, default=5400)
    r.add_argument("--resume", action="store_true", default=True)
    r.add_argument("--no-resume", dest="resume", action="store_false")

    t = sub.add_parser("task", help="internal: run one (item, algorithm)")
    t.add_argument("--manifest", required=True)
    t.add_argument("--out", required=True)
    t.add_argument("--item", required=True)
    t.add_argument("--algorithm", required=True)
    t.add_argument("--wav", required=True)

    args = p.parse_args(argv)
    if args.mode == "run":
        return orchestrate(args)
    if args.mode == "task":
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        item = next(i for i in manifest["items"] if i["id"] == args.item)
        rec = run_task(Path(args.out), item, args.algorithm, Path(args.wav))
        print(json.dumps({k: v for k, v in rec.items() if k != "notes"}))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
