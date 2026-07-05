"""Export a trained :class:`DrumCRNN` to ONNX and verify it with onnxruntime.

ONNX is the practical packaging route for the PyInstaller one-file sidecar:
onnxruntime is already a shipped dependency, so a trained model can run there
without dragging the full PyTorch stack into the frozen binary.

The exporter uses the legacy TorchScript path (``dynamo=False``) because the
newer torch.export path needs ``onnxscript``; TorchScript export only needs the
``onnx`` package. The time axis is a dynamic dimension so the exported graph
accepts clips of any length.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import torch

from .config import NUM_CLASSES
from .model import DrumCRNN


def export_to_onnx(
    model: DrumCRNN,
    out_path: str | Path,
    *,
    n_mels: int,
    example_frames: int = 128,
    opset: int = 17,
) -> Path:
    """Export ``model`` to ONNX at ``out_path``. Returns the written path.

    Input ``mel``: ``(batch, time, n_mels)``; output ``logits``:
    ``(batch, time, num_classes)``. ``batch`` and ``time`` are dynamic.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    model.eval()
    dummy = torch.randn(1, example_frames, n_mels, dtype=torch.float32)
    torch.onnx.export(
        model,
        (dummy,),
        str(out_path),
        dynamo=False,
        input_names=["mel"],
        output_names=["logits"],
        opset_version=opset,
        dynamic_axes={
            "mel": {0: "batch", 1: "time"},
            "logits": {0: "batch", 1: "time"},
        },
    )
    return out_path


def verify_onnx(
    model: DrumCRNN,
    onnx_path: str | Path,
    *,
    n_mels: int,
    frames: int = 97,
    atol: float = 1e-4,
) -> float:
    """Load the ONNX graph and check it matches the torch model.

    Runs both on the same random input and returns the max absolute
    difference. Raises ``AssertionError`` if it exceeds ``atol``.
    """
    import onnxruntime as ort

    model.eval()
    x = np.random.randn(2, frames, n_mels).astype(np.float32)
    with torch.no_grad():
        torch_out = model(torch.from_numpy(x)).numpy()

    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    onnx_out = sess.run(["logits"], {"mel": x})[0]

    if onnx_out.shape != torch_out.shape:
        raise AssertionError(f"shape mismatch: torch {torch_out.shape} vs onnx {onnx_out.shape}")
    if onnx_out.shape[-1] != NUM_CLASSES:
        raise AssertionError(f"onnx last dim {onnx_out.shape[-1]} != NUM_CLASSES {NUM_CLASSES}")
    max_diff = float(np.max(np.abs(torch_out - onnx_out)))
    if max_diff > atol:
        raise AssertionError(f"onnx output differs from torch by {max_diff} > atol {atol}")
    return max_diff
