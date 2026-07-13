"""Neural beat + downbeat + meter tracking.

librosa's ``beat_track`` returns beat TIMES only, so the sidecar historically
faked meter by counting beats in fours from the first tracked beat — giving an
arbitrary downbeat phase (accent lands on the wrong beat) and a hardcoded 4/4
that is simply wrong on 3/4, 6/8, etc.

This module wraps Beat This! (CPJKU, ISMIR 2024 — MIT-licensed code AND weights)
to produce real joint beats + downbeats, from which the true bar structure
(measure starts + beats-per-bar) falls out. The heavy model is optional and
modelpack-gated exactly like mr_mt3: absent checkpoint / missing package / any
inference error => ``track_meter`` returns ``None`` and the caller keeps the
librosa path. Beat This!'s madmom DBN post-processor is enabled by default for
better downbeat phase; if madmom is missing, the same Beat This! model is
retried with its minimal post-processor instead of falling back to librosa.

The pure derivation helpers (bar assignment, beats-per-bar, bpm) take plain
lists and are unit-tested without the model.
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Any

# Modelpack-gate constants (mirror the mr_mt3 pattern).
BEAT_THIS_MODELPACK_ID = "beat_this"
BEAT_THIS_CHECKPOINT_ENV = "AURALPRIMER_BEAT_THIS_CHECKPOINT_PATH"
_MODELS_SUBDIR = "assets/models"
_DEV_CACHE_CKPT = "~/.cache/torch/hub/checkpoints/beat_this-final0.ckpt"
METER_DBN_ENV = "AURALPRIMER_METER_DBN"


def _search_roots() -> list[Path]:
    """Roots to look for an installed beat_this modelpack, mirroring the MT3
    resolver: PyInstaller bundle dir, exe dir, cwd, and their data/ variants."""
    roots: list[Path] = []
    meipass = getattr(__import__("sys"), "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    try:
        import sys

        roots.append(Path(sys.executable).resolve().parent)
    except Exception:
        pass
    roots.append(Path.cwd())
    expanded: list[Path] = []
    for r in roots:
        expanded.extend([r, r / "data", r / "AuralPrimerPortable" / "data"])
    return expanded


def resolve_checkpoint(config: dict[str, Any] | None = None) -> Path | None:
    """Resolve a Beat This! checkpoint, or None if unavailable.

    Order: explicit config path, env var, installed modelpack
    (<root>/assets/models/beat_this/<version>/files/checkpoints/beat_this/*.ckpt),
    then the dev torch-hub cache. Never raises.
    """
    try:
        if config:
            p = config.get("beat_this_checkpoint_path")
            if p and Path(p).is_file():
                return Path(p)
        env = os.getenv(BEAT_THIS_CHECKPOINT_ENV)
        if env and Path(env).is_file():
            return Path(env)
        for root in _search_roots():
            base = root / _MODELS_SUBDIR / BEAT_THIS_MODELPACK_ID
            if not base.is_dir():
                continue
            # newest version subdir first
            for version_dir in sorted(base.iterdir(), reverse=True):
                ckpt_dir = version_dir / "files" / "checkpoints" / BEAT_THIS_MODELPACK_ID
                if ckpt_dir.is_dir():
                    ckpts = sorted(ckpt_dir.glob("*.ckpt"))
                    if ckpts:
                        return ckpts[0]
        dev = Path(os.path.expanduser(_DEV_CACHE_CKPT))
        if dev.is_file():
            return dev
    except Exception:
        return None
    return None


def available(config: dict[str, Any] | None = None) -> bool:
    """Whether the beat_this package imports and a checkpoint resolves."""
    if resolve_checkpoint(config) is None:
        return False
    try:
        import beat_this.inference  # noqa: F401
    except Exception:
        return False
    return True


def _parse_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "dbn"}:
            return True
        if normalized in {"0", "false", "no", "off", "minimal"}:
            return False
    return default


def _meter_dbn_enabled(config: dict[str, Any] | None = None) -> bool:
    """Whether to request Beat This!'s madmom DBN post-processor.

    Explicit ``config["meter_dbn"]`` wins over the environment; the default is
    enabled because the DBN gate has passed on the supported ingest runtime.
    """
    if config and "meter_dbn" in config:
        return _parse_bool(config.get("meter_dbn"), default=True)
    return _parse_bool(os.getenv(METER_DBN_ENV), default=True)


# --------------------------------------------------------------------------
# Pure derivation helpers (no model, no audio) — unit-tested.
# --------------------------------------------------------------------------

def assign_bars_from_downbeats(
    beat_times: list[float],
    downbeat_times: list[float],
) -> list[dict[str, Any]]:
    """Assign each beat a (bar, beat-in-bar) index using the MODEL'S downbeats
    for phase — not a blind count-by-4.

    ``beat == 0`` marks a measure start (a model downbeat), which is what
    feedpak_writer._derive_measures consumes. Beats before the first downbeat are
    a pickup/anacrusis: kept in bar 0 with a non-zero beat index so they never
    masquerade as a downbeat. Bar length is taken from the model, so irregular
    bars (the model's known occasional wobble) don't desync everything after.
    """
    beats = sorted(float(t) for t in beat_times if t == t)  # drop NaN
    n = len(beats)
    if n == 0:
        return []

    # Nearest beat index for each downbeat (downbeats snap to beats in the model,
    # but be robust and match to the closest).
    db_idx: set[int] = set()
    if downbeat_times:
        import bisect

        for db in sorted(float(d) for d in downbeat_times if d == d):
            j = bisect.bisect_left(beats, db)
            cands = [k for k in (j - 1, j) if 0 <= k < n]
            if cands:
                db_idx.add(min(cands, key=lambda k: abs(beats[k] - db)))

    first_db = min(db_idx) if db_idx else 0
    has_pickup = first_db > 0

    rows: list[dict[str, Any]] = []
    bar = 0
    beat_in_bar = 0
    seen_downbeat = False
    for i, t in enumerate(beats):
        is_db = i in db_idx
        if is_db:
            if seen_downbeat:
                bar += 1
            else:
                seen_downbeat = True
                bar = 1 if has_pickup else 0
            beat_in_bar = 0
        else:
            if seen_downbeat:
                beat_in_bar += 1
            else:
                beat_in_bar = i + 1  # pickup: 1,2,3... never 0
        rows.append(
            {
                "t": round(max(0.0, t), 6),
                "bar": int(bar),
                "beat": int(beat_in_bar),
                "strength": 1.0 if is_db else 0.5,
            }
        )
    return rows


def derive_beats_per_bar(rows: list[dict[str, Any]]) -> int:
    """Modal number of beats between consecutive measure starts (beat==0).

    Mode (not mean) so the model's occasional short/long bar doesn't skew it.
    Defaults to 4 when there are too few downbeats to tell.
    """
    starts = [i for i, r in enumerate(rows) if int(r.get("beat", -1)) == 0]
    lengths = [starts[k + 1] - starts[k] for k in range(len(starts) - 1)]
    lengths = [n for n in lengths if n >= 1]
    if not lengths:
        return 4
    return Counter(lengths).most_common(1)[0][0]


def derive_bpm(beat_times: list[float]) -> float:
    """Tempo from the MEDIAN inter-beat interval — robust, and it avoids
    librosa's discretized tempo-lag bins (both shipped packs carried an
    identical 139.675 bpm fingerprint from that scalar)."""
    beats = sorted(float(t) for t in beat_times if t == t)
    if len(beats) < 2:
        return 120.0
    diffs = [beats[i + 1] - beats[i] for i in range(len(beats) - 1)]
    diffs = [d for d in diffs if d > 1e-6]
    if not diffs:
        return 120.0
    diffs.sort()
    med = diffs[len(diffs) // 2]
    return float(round(60.0 / med, 3)) if med > 0 else 120.0


def _time_signature_string(beats_per_bar: int) -> str:
    """Map beats-per-bar to a "n/4" signature. NOTE (v1): the denominator is a
    simplification — a compound (6/8) song whose felt bar the model groups as N
    beats is written N/4. The bar STRUCTURE (measure starts + accent) is correct;
    only the denominator label is provisional pending the compound-meter pass
    (see docs/research-meter-tracking-2026-07-05.md, Q3)."""
    n = beats_per_bar if beats_per_bar >= 1 else 4
    return f"{n}/4"


# --------------------------------------------------------------------------
# Model call (isolated, fail-safe).
# --------------------------------------------------------------------------

def track_meter(
    wav_path: str | Path,
    *,
    duration_sec: float,
    config: dict[str, Any] | None = None,
) -> tuple[float, dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """Run Beat This! and return (bpm, beats_dict, tempo_map, meta) in the same
    shape as the librosa analyzers, or None on any failure (caller falls back).
    """
    checkpoint = resolve_checkpoint(config)
    if checkpoint is None:
        return None
    try:
        from beat_this.inference import File2Beats  # heavy import guarded here
    except Exception:
        return None

    use_dbn = _meter_dbn_enabled(config)
    postprocessor = "dbn" if use_dbn else "minimal"
    try:
        f2b = File2Beats(checkpoint_path=str(checkpoint), device="cpu", dbn=use_dbn)
        beats_arr, downbeats_arr = f2b(str(wav_path))
    except (ImportError, ModuleNotFoundError):
        if not use_dbn:
            return None
        try:
            f2b = File2Beats(checkpoint_path=str(checkpoint), device="cpu", dbn=False)
            beats_arr, downbeats_arr = f2b(str(wav_path))
            postprocessor = "minimal"
        except Exception:
            return None
    except Exception:
        return None
    beat_times = [float(t) for t in list(beats_arr)]
    downbeat_times = [float(t) for t in list(downbeats_arr)]

    if not beat_times:
        return None

    # Clamp to song and dedup, preserving order.
    beat_times = sorted({round(max(0.0, min(duration_sec, t)), 6) for t in beat_times if t <= duration_sec + 1e-9})
    if not beat_times:
        return None
    downbeat_times = [t for t in downbeat_times if 0.0 <= t <= duration_sec + 1e-9]

    rows = assign_bars_from_downbeats(beat_times, downbeat_times)
    n_downbeats = sum(1 for r in rows if int(r["beat"]) == 0)
    if n_downbeats == 0:
        # No usable downbeats -> we'd only produce a phase-less bar=0 grid, which
        # is no better than librosa's count-by-4. Let the caller fall back.
        return None
    beats_per_bar = derive_beats_per_bar(rows)
    bpm = derive_bpm(beat_times)
    ts = _time_signature_string(beats_per_bar)

    beats = {"beats_version": "1.0.0", "beats": rows}
    from_time = 0.0
    tempo_map = {
        "tempo_version": "1.0.0",
        "segments": [
            {
                "t0": from_time,
                "t1": round(max(0.0, duration_sec), 6),
                "bpm": bpm,
                "time_signature": ts,
            }
        ],
    }
    meta = {
        "mode": "high_accuracy",
        "tempo_source": "beat_this.median_ibi",
        "beat_source": "beat_this",
        "meter_source": "beat_this_downbeats",
        "postprocessor": postprocessor,
        "estimated_bpm": bpm,
        "beats_per_bar": beats_per_bar,
        "time_signature": ts,
        "detected_beat_count": len(beat_times),
        "detected_downbeat_count": n_downbeats,
        "checkpoint": checkpoint.name,
        # Provisional-denominator flag for the compound-meter follow-up (Q3).
        "meter_denominator_provisional": True,
    }
    return bpm, beats, tempo_map, meta
