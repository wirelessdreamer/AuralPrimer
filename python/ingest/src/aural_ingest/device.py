"""Unified inference device selection.

Single source of truth for picking ``"cuda"`` vs ``"cpu"`` across the
transcription adapters. Honors an explicit env override when given,
otherwise auto-detects: CUDA when a GPU is available on this machine,
else CPU. This must stay machine-agnostic -- the project runs on several
boxes (some GPU, some CPU-only), so device choice is detected at runtime,
never hardcoded. Learned models (MT3 drums, demucs) run ~5-10x faster on
GPU.
"""
from __future__ import annotations

import os


def select_device(env_var: str | None = None, *, prefer_gpu: bool = True) -> str:
    """Pick the inference device.

    Args:
        env_var: optional env var name whose value, when set and non-empty,
            is returned verbatim (e.g. ``AURAL_PIANO_DEVICE``).
        prefer_gpu: when True (default) and no override is set, return
            ``"cuda"`` if a GPU is available; otherwise ``"cpu"``.

    Returns:
        ``"cuda"`` or ``"cpu"`` (or the raw override value when provided).
    """
    if env_var:
        override = os.environ.get(env_var, "").strip()
        if override:
            return override

    if prefer_gpu:
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
        except Exception:
            pass

    return "cpu"
