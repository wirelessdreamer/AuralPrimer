from __future__ import annotations

import importlib
import os
import tempfile
from pathlib import Path

from aural_ingest.algorithms.piano_midi import decode_midi_notes
from aural_ingest.transcription import MelodicNote


def _allow_download() -> bool:
    return os.environ.get("AURAL_PIANO_PTI_ALLOW_DOWNLOAD", "").strip().lower() in {"1", "true", "yes"}


def _resolve_bundled_checkpoint() -> Path | None:
    from aural_ingest.transcription import (
        _default_basic_pitch_model_roots,
        resolve_piano_pti_checkpoint_path,
    )

    return resolve_piano_pti_checkpoint_path(_default_basic_pitch_model_roots())


def _checkpoint_path() -> str | None:
    raw = os.environ.get("AURAL_PIANO_PTI_CHECKPOINT", "").strip()
    if raw:
        checkpoint = Path(raw)
        if not checkpoint.is_file():
            raise RuntimeError(f"piano_pti checkpoint not found: {checkpoint}")
        return str(checkpoint)
    bundled = _resolve_bundled_checkpoint()
    if bundled is not None:
        return str(bundled)
    if _allow_download():
        return None
    raise RuntimeError(
        "piano_pti requires AURAL_PIANO_PTI_CHECKPOINT, a bundled checkpoint under "
        "<modelpack>/piano_pti/, or AURAL_PIANO_PTI_ALLOW_DOWNLOAD=1 for the "
        "upstream downloader"
    )


def _device() -> str:
    return os.environ.get("AURAL_PIANO_DEVICE", "cpu").strip() or "cpu"


import contextlib
from typing import Iterator


@contextlib.contextmanager
def _torch_load_compat() -> Iterator[None]:
    """Allow loading the Kong / Edwards-robust checkpoint under PyTorch >= 2.6.

    PyTorch 2.6 flipped ``torch.load``'s ``weights_only`` default to ``True``,
    which refuses to unpickle the numpy arrays embedded in the Kong-style
    checkpoint. ``piano_transcription_inference`` calls ``torch.load`` without
    passing ``weights_only=False``, so we monkey-patch the default for the
    duration of the load. The Zenodo checkpoint is a known artifact downloaded
    by the bundled script with a verified SHA-256, so disabling weights-only
    is safe here.
    """
    try:
        torch = importlib.import_module("torch")
    except Exception:
        # No torch installed -- piano_transcription_inference would have
        # failed earlier; let the caller surface that error.
        yield
        return

    original = torch.load

    def _load(*args, **kwargs):
        kwargs.setdefault("weights_only", False)
        return original(*args, **kwargs)

    torch.load = _load
    try:
        yield
    finally:
        torch.load = original


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "keys",
) -> list[MelodicNote]:
    try:
        pti = importlib.import_module("piano_transcription_inference")
        librosa = importlib.import_module("librosa")
    except Exception as exc:  # pragma: no cover - depends on optional package
        raise RuntimeError(
            "piano_pti requires the optional 'piano_transcription_inference' package in the ingest runtime"
        ) from exc

    checkpoint_path = _checkpoint_path()
    sample_rate = int(getattr(pti, "sample_rate"))
    transcriptor_cls = getattr(pti, "PianoTranscription")
    audio, _sr = librosa.load(path=str(stem_path), sr=sample_rate, mono=True)

    # Default model_type is "Note_pedal" which expects ``checkpoint['model']
    # = {'note_model': ..., 'pedal_model': ...}``. The Edwards-robust Kong
    # checkpoint omits ``pedal_model`` (and the original Kong checkpoint
    # works with the underlying ``Regress_onset_offset_frame_velocity_CRNN``
    # too), so we pin to the no-pedal variant.
    model_type = os.environ.get(
        "AURAL_PIANO_PTI_MODEL_TYPE", "Regress_onset_offset_frame_velocity_CRNN"
    ).strip() or "Regress_onset_offset_frame_velocity_CRNN"

    with tempfile.TemporaryDirectory(prefix="aural_pti_") as tmp:
        out_midi = Path(tmp) / "pti.mid"
        with _torch_load_compat():
            transcriptor = transcriptor_cls(
                model_type=model_type,
                device=_device(),
                checkpoint_path=checkpoint_path,
            )
            transcriptor.transcribe(audio, str(out_midi))
        if not out_midi.is_file():
            raise RuntimeError("piano_pti completed without writing a MIDI file")
        return decode_midi_notes(out_midi, instrument=instrument)
