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

Decode threshold defaults to **0.20** (the F1-optimal point for the full-corpus
run-2 weights on the stratified-30 eval: F1 0.546 vs 0.537 at 0.15; at 0.5 the
precision-heavy model under-triggers and F1 drops to ~0.41). Override with
``AURAL_DRUM_CRNN_THRESHOLD``. ``min_gap`` defaults to 0.02 s. A proper per-class
threshold calibration on a guard set is deferred to the promotion-gate phase.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from aural_ingest.transcription import DrumEvent

_ENV_ONNX = "AURAL_DRUM_CRNN_ONNX"          # explicit path to the .onnx
_ENV_THRESHOLD = "AURAL_DRUM_CRNN_THRESHOLD"  # decode threshold override

DEFAULT_THRESHOLD = 0.20  # F1-optimal for run-2 full-corpus weights; NOT 0.5 (under-triggers)
DEFAULT_MIN_GAP_SEC = 0.02
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
    raw = os.getenv(_ENV_THRESHOLD)
    if not raw:
        return DEFAULT_THRESHOLD
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_THRESHOLD


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

    return decode_events(
        probs.astype(np.float32),
        feat,
        threshold=_decode_threshold(),
        min_gap_sec=DEFAULT_MIN_GAP_SEC,
    )
