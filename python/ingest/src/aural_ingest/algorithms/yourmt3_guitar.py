"""Benchmark adapter for YourMT3 guitar transcription.

The ground-truth benchmark imports melodic algorithms as
``aural_ingest.algorithms.<id>`` modules and passes the case instrument through
to ``transcribe()``. YourMT3 is already resolved and loaded by the MT3 modelpack
machinery used for drum benchmarks; this module reuses that path lazily and
decodes the returned MIDI as melodic notes for guitar roles.
"""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import tempfile
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from aural_ingest.transcription import MelodicNote


ENGINE_ID = "yourmt3_guitar"
_MT3_ENGINE_ID = "yourmt3_drums"


def _transcribe_yourmt3_midi(stem_path: Path) -> Any:
    import librosa

    from aural_ingest.device import select_device
    from aural_ingest.mt3_compat import (
        ensure_mt3_transformers_compat,
        suppress_mt3_runtime_warnings,
    )
    from aural_ingest.transcription import resolve_mt3_modelpack

    resolved = resolve_mt3_modelpack(_MT3_ENGINE_ID, stem_path=stem_path)
    checkpoint_path = Path(resolved["checkpoint_path_resolved"])
    modelpack_root = Path(resolved["modelpack_root"])
    os.environ["MT3_CHECKPOINT_DIR"] = str(modelpack_root)

    audio, sr = librosa.load(str(stem_path), sr=16000, mono=True)
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    try:
        with suppress_mt3_runtime_warnings():
            with contextlib.redirect_stdout(captured_stdout), contextlib.redirect_stderr(
                captured_stderr
            ):
                ensure_mt3_transformers_compat()
                from mt3_infer import load_model

                model = load_model(
                    str(resolved["model_id"]),
                    checkpoint_path=str(checkpoint_path),
                    device=select_device("AURAL_MT3_DEVICE"),
                    auto_download=False,
                )
                return model.transcribe(audio.astype("float32"), sr=sr)
    except Exception as exc:
        detail = "\n".join(
            part
            for part in (captured_stdout.getvalue().strip(), captured_stderr.getvalue().strip())
            if part
        )
        if detail:
            raise RuntimeError(f"YourMT3 guitar inference failed: {exc}\n{detail}") from exc
        raise RuntimeError(f"YourMT3 guitar inference failed: {exc}") from exc


def _midi_to_melodic_notes(midi: Any, *, instrument: str) -> list[MelodicNote]:
    from aural_ingest.algorithms.piano_midi import decode_midi_notes

    if isinstance(midi, (str, Path)):
        return decode_midi_notes(Path(midi), instrument=instrument)
    if isinstance(midi, (bytes, bytearray)):
        with tempfile.TemporaryDirectory(prefix="aural_yourmt3_") as tmp:
            midi_path = Path(tmp) / "yourmt3.mid"
            midi_path.write_bytes(bytes(midi))
            return decode_midi_notes(midi_path, instrument=instrument)
    if hasattr(midi, "save"):
        with tempfile.TemporaryDirectory(prefix="aural_yourmt3_") as tmp:
            midi_path = Path(tmp) / "yourmt3.mid"
            midi.save(str(midi_path))
            return decode_midi_notes(midi_path, instrument=instrument)
    raise RuntimeError(f"{ENGINE_ID} returned unsupported MIDI object: {type(midi).__name__}")


def transcribe(
    stem_path: Path | str,
    instrument: str = "lead_guitar",
    **_kwargs: Any,
) -> list[MelodicNote]:
    """Transcribe one guitar stem with YourMT3.

    Missing checkpoints are surfaced as ``RuntimeError`` so gt-benchmark records
    a case error instead of silently scoring bogus empty predictions. This
    mirrors the MT3 drum benchmark shims; unlike production fallback-chain
    adapters such as RMVPE, this adapter is intended as an explicit benchmark
    engine.
    """
    try:
        midi = _transcribe_yourmt3_midi(Path(stem_path))
    except FileNotFoundError as exc:
        raise RuntimeError(f"{ENGINE_ID} modelpack/checkpoint unavailable: {exc}") from exc
    return _midi_to_melodic_notes(midi, instrument=instrument)
