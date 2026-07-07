"""In-house drum-CRNN adapter (opt-in neural drum engine).

Runs the compact 5-class CRNN (kick / snare / hi_hat / toms / cymbals) trained
on E-GMD (CC BY 4.0), exported to ONNX. Inference is in-process via
``onnxruntime`` -- already a shipped sidecar dependency (basic_pitch uses it) --
so this is a real shippable engine, NOT a research-only subprocess adapter like
``magenta_egmd_drums``.

Gating discipline (mirrors ``magenta_egmd_drums`` but in-process):

  * This module MUST stay importable even when ``onnxruntime`` OR the model is
    absent -- the drum-algorithm registry imports every algorithm at sidecar
    startup. So ALL heavy imports (onnxruntime) and model resolution happen
    INSIDE :func:`transcribe`, never at import time.
  * If the ONNX can't be resolved at inference time, :func:`transcribe` raises a
    clear ``RuntimeError``. The DSP orchestrator (``transcribe_drums_dsp`` via
    ``drum_fallback_chain``) catches that and falls through to the heuristic
    engines, so an un-installed model never breaks a drum run.

Model resolution order:

  1. ``AURAL_DRUM_CRNN_ONNX`` env var -- explicit path to the ``.onnx``.
  2. an installed ``assets/models/drum_crnn/<version>/`` modelpack, discovered
     with the same search-root logic the MT3 resolver uses.

Decode threshold: a per-class mapping, resolved with this priority (highest
first), one class at a time so a partial override doesn't lose calibration
for the classes it doesn't mention:

  1. ``AURAL_DRUM_CRNN_THRESHOLDS`` env var, e.g.
     ``"kick:0.2,snare:0.25,hi_hat:0.2,toms:0.2,cymbals:0.12"``.
  2. a ``decode_thresholds`` block in the resolved modelpack's
     ``modelpack.json`` (written by ``install_drum_crnn_modelpack.py
     --thresholds ...`` -- calibration ships WITH the model).
  3. the scalar default (env ``AURAL_DRUM_CRNN_THRESHOLD``, else
     ``DEFAULT_THRESHOLD`` -- 0.20, the F1-optimal point for the run-2
     full-corpus weights at a uniform threshold: F1 0.546 vs 0.537 at 0.15;
     at 0.5 the precision-heavy model under-triggers and F1 drops to ~0.41).

``min_gap`` defaults to 0.02 s.

Tatum-conditioned decode (OPT-IN, off by default -- ``AURAL_DRUM_CRNN_TATUM_PRIOR=1``):
when enabled, resolves a beat grid via ``meter_tracker.track_meter`` (Beat
This!, itself fail-safe/modelpack-gated -- returns ``None`` on any failure,
in which case this is a silent no-op) and nudges per-frame probabilities
toward the resulting tatum grid before the per-class threshold runs (see
``training.drum_crnn.tatum``). Targets the observed precision-heavy /
recall-limited shape (P ~0.7-0.9, R ~0.4-0.5 at calibrated thresholds) by
recovering onsets that land just under threshold at a metrically-plausible
position. Off by default because it re-runs Beat This! per drum stem rather
than reusing a pipeline-level beat grid -- an efficiency follow-up, not a
correctness blocker.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from aural_ingest.transcription import DrumEvent

_ENV_ONNX = "AURAL_DRUM_CRNN_ONNX"          # explicit path to the .onnx
_ENV_THRESHOLD = "AURAL_DRUM_CRNN_THRESHOLD"  # scalar decode threshold override
_ENV_THRESHOLDS = "AURAL_DRUM_CRNN_THRESHOLDS"  # per-class override
_ENV_TATUM_PRIOR = "AURAL_DRUM_CRNN_TATUM_PRIOR"  # "1" to enable (opt-in, off by default)

DEFAULT_THRESHOLD = 0.20  # F1-optimal for run-2 full-corpus weights; NOT 0.5 (under-triggers)
DEFAULT_MIN_GAP_SEC = 0.02
DEFAULT_TATUM_SUBDIVISIONS = 4
DEFAULT_TATUM_BOOST = 0.12
DEFAULT_TATUM_SIGMA_SEC = 0.025
MODELPACK_ID = "drum_crnn"

# One cached onnxruntime session per resolved model path. Guarded by a lock so a
# concurrent sidecar can't build the session twice.
_SESSION_LOCK = threading.Lock()
_SESSION_CACHE: dict[str, object] = {}


def _resolve_onnx_path(stem_path: Path | None = None) -> Path:
    """Resolve the drum-CRNN ONNX. Raises ``RuntimeError`` if it can't be found.

    (a) ``AURAL_DRUM_CRNN_ONNX`` explicit path, then (b) an installed
    ``assets/models/drum_crnn/<version>/`` modelpack found via the MT3 resolver's
    search roots.
    """
    explicit = os.getenv(_ENV_ONNX)
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return candidate
        raise RuntimeError(
            f"{_ENV_ONNX} points at a missing file: {candidate}"
        )

    # Reuse the MT3 resolver's search-root + installed-modelpack discovery so
    # this engine finds its modelpack exactly where the MT3 engines find theirs.
    from aural_ingest.transcription import (
        _default_mt3_model_search_roots,
        _iter_installed_modelpack_dirs,
        _read_json_file,
    )

    roots = _default_mt3_model_search_roots(stem_path)
    for model_root in _iter_installed_modelpack_dirs(MODELPACK_ID, roots):
        manifest_path = model_root / "modelpack.json"
        try:
            manifest = _read_json_file(manifest_path)
        except Exception:
            manifest = {}
        # Prefer the manifest's checkpoint entry; fall back to the default relpath.
        rel = None
        for item in manifest.get("checkpoints", []) or []:
            if isinstance(item, dict) and str(item.get("path", "")).strip():
                rel = str(item["path"]).strip()
                break
        candidates = []
        if rel:
            candidates.append(model_root / Path(rel))
        candidates.append(model_root / "files" / "drum_crnn.onnx")
        for candidate in candidates:
            if candidate.is_file():
                return candidate

    searched = ", ".join(str(r) for r in roots)
    raise RuntimeError(
        f"drum_crnn ONNX not found: set {_ENV_ONNX} to the .onnx, or install the "
        f"'{MODELPACK_ID}' modelpack (assets/models/{MODELPACK_ID}/<version>/files/"
        f"drum_crnn.onnx via scripts/install_drum_crnn_modelpack.py). searched: {searched}"
    )


def _get_session(onnx_path: Path):
    """Return a cached onnxruntime session for ``onnx_path`` (built once)."""
    key = str(onnx_path)
    session = _SESSION_CACHE.get(key)
    if session is not None:
        return session
    with _SESSION_LOCK:
        session = _SESSION_CACHE.get(key)
        if session is not None:
            return session
        import onnxruntime as ort

        session = ort.InferenceSession(
            str(onnx_path), providers=["CPUExecutionProvider"]
        )
        _SESSION_CACHE[key] = session
        return session


def _decode_threshold() -> float:
    """Scalar fallback threshold (env ``AURAL_DRUM_CRNN_THRESHOLD`` or
    ``DEFAULT_THRESHOLD``) -- the last-resort layer in :func:`_resolve_decode_threshold`.
    """
    raw = os.getenv(_ENV_THRESHOLD)
    if not raw:
        return DEFAULT_THRESHOLD
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_THRESHOLD


def _manifest_decode_thresholds(onnx_path: Path) -> dict[str, float] | None:
    """Read a ``decode_thresholds`` block from the modelpack.json next to
    ``onnx_path`` (the installer's ``<id>/<version>/files/drum_crnn.onnx``
    layout -> ``<id>/<version>/modelpack.json``).

    Returns ``None`` if there is no adjacent manifest, it doesn't parse, or it
    has no usable ``decode_thresholds`` block -- best-effort, never raises,
    since an explicit ``AURAL_DRUM_CRNN_ONNX`` override may point at a bare
    ``.onnx`` with no sibling manifest at all.
    """
    manifest_path = onnx_path.parent.parent / "modelpack.json"
    if not manifest_path.is_file():
        return None
    try:
        from aural_ingest.transcription import _read_json_file

        manifest = _read_json_file(manifest_path)
        thresholds = manifest.get("decode_thresholds")
        if not isinstance(thresholds, dict) or not thresholds:
            return None
        return {str(k): float(v) for k, v in thresholds.items()}
    except Exception:
        return None


def _resolve_decode_threshold(onnx_path: Path) -> dict[str, float]:
    """Compose a full per-class decode-threshold mapping.

    Resolved per class (not per source), so a partial override at a higher
    priority still leaves the other classes calibrated instead of falling all
    the way back to the scalar default:

      1. ``AURAL_DRUM_CRNN_THRESHOLDS`` env var.
      2. the resolved modelpack's ``decode_thresholds`` manifest block.
      3. the scalar default (:func:`_decode_threshold`).
    """
    from aural_ingest.training.drum_crnn.config import CLASSES, parse_class_thresholds

    resolved = {name: _decode_threshold() for name in CLASSES}

    manifest_thresholds = _manifest_decode_thresholds(onnx_path) or {}
    for name, value in manifest_thresholds.items():
        if name in resolved:
            resolved[name] = value

    raw_env = os.getenv(_ENV_THRESHOLDS)
    if raw_env:
        for name, value in parse_class_thresholds(raw_env).items():
            if name in resolved:
                resolved[name] = value

    return resolved


def _tatum_prior_enabled() -> bool:
    return os.getenv(_ENV_TATUM_PRIOR, "").strip().lower() in ("1", "true", "yes", "on")


def _resolve_tatum_grid(stem_path: Path, duration_sec: float) -> list[float]:
    """Beat grid for the tatum prior, or ``[]`` on any failure.

    Wraps ``meter_tracker.track_meter`` (Beat This!, itself fail-safe and
    modelpack-gated) -- a resolution failure here (model absent, inference
    error, no usable downbeats) is exactly equivalent to the caller not
    enabling the prior at all: :func:`apply_tatum_prior` no-ops on an empty
    grid, so there is nothing extra for callers to handle.
    """
    try:
        from aural_ingest.meter_tracker import track_meter
        from aural_ingest.training.drum_crnn.tatum import build_tatum_grid

        result = track_meter(stem_path, duration_sec=duration_sec)
        if result is None:
            return []
        _bpm, beats_dict, _tempo_map, _meta = result
        beat_times = [float(row["t"]) for row in beats_dict.get("beats", [])]
        return build_tatum_grid(
            beat_times, subdivisions=DEFAULT_TATUM_SUBDIVISIONS, duration_sec=duration_sec
        )
    except Exception:
        return []


def transcribe(stem_path: Path) -> list[DrumEvent]:
    """Transcribe one drum stem with the in-house CRNN. -> list[DrumEvent].

    Raises ``RuntimeError`` if onnxruntime or the ONNX model is unavailable, so
    the DSP orchestrator falls through to the heuristic engines.
    """
    stem_path = Path(stem_path)

    # Heavy imports live here (not at module import) so the module is importable
    # with neither onnxruntime nor the model present.
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - numpy is always present
        raise RuntimeError(f"drum_crnn requires numpy: {exc}") from exc
    try:
        import onnxruntime  # noqa: F401  (import-guard; used via _get_session)
    except ImportError as exc:
        raise RuntimeError(
            f"drum_crnn requires onnxruntime (a shipped sidecar dep): {exc}"
        ) from exc

    from aural_ingest.training.drum_crnn.config import FeatureConfig
    from aural_ingest.training.drum_crnn.decode import decode_events
    from aural_ingest.training.drum_crnn.features import (
        load_audio_mono,
        logmel_from_audio,
    )

    onnx_path = _resolve_onnx_path(stem_path)
    session = _get_session(onnx_path)

    feat = FeatureConfig()
    # Same audio front-end used in training so ONNX features match the weights.
    audio = load_audio_mono(stem_path, feat.sample_rate)
    logmel = logmel_from_audio(audio, feat)  # (n_frames, n_mels) float32
    if logmel.shape[0] == 0:
        return []

    mel = logmel[np.newaxis, :, :].astype(np.float32)  # (1, T, n_mels)
    logits = session.run(["logits"], {"mel": mel})[0]  # (1, T, num_classes)
    probs = 1.0 / (1.0 + np.exp(-logits[0]))  # sigmoid -> (T, num_classes)
    probs = probs.astype(np.float32)

    if _tatum_prior_enabled():
        from aural_ingest.training.drum_crnn.tatum import apply_tatum_prior

        duration_sec = len(audio) / float(feat.sample_rate)
        tatum_grid = _resolve_tatum_grid(stem_path, duration_sec)
        if tatum_grid:
            probs = apply_tatum_prior(
                probs,
                feat,
                tatum_grid,
                boost=DEFAULT_TATUM_BOOST,
                sigma_sec=DEFAULT_TATUM_SIGMA_SEC,
            )

    return decode_events(
        probs,
        feat,
        threshold=_resolve_decode_threshold(onnx_path),
        min_gap_sec=DEFAULT_MIN_GAP_SEC,
    )
