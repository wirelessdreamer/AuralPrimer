"""Adapter for OaF-Drums (Magenta Onsets-and-Frames, retrained on E-GMD).

This is the drum analog of the piano PTI swap that carried keys from F1 0.706
to 0.928 (see ``docs/research-piano-cleanup-deep-dive-2026-06-20.md``). The
decision to adopt it, and the license reasoning, live in
``docs/research-drum-data-2026-06-23.md`` -- the short version:

- **Architecture:** Onsets-and-Frames CRNN (the same family as the piano PTI
  path), retrained by Magenta on **E-GMD** (Roland TD-17 e-kit, CC BY 4.0).
- **Output:** onsets + drum class + **velocity** for up to 7 classes. Velocity
  is a genuine advantage over every heuristic engine, which all fabricate it.
- **License:** Magenta code is Apache 2.0 and E-GMD is CC BY 4.0, so an
  E-GMD-trained checkpoint is shippable by *both* code and data license -- the
  cleanest story of any neural ADT model (ADTOF is CC BY-NC-SA and cannot ship).

**The open gate (why this ships as a scaffold):** the *published* E-GMD drum
checkpoint's download availability is unverified (magenta issues #1792/#1876/
#1931). This module therefore resolves a checkpoint the same way ``mr_mt3`` and
``piano_d3rm`` do -- via env var or a modelpack drop -- and is **inert when the
checkpoint (or the TF/magenta deps) is absent**: it returns ``[]`` rather than
raising, so it slots harmlessly at the head of a fallback chain on machines
that don't have the model. See ``scripts/SETUP-OaF-DRUMS.md``.

Resolution order (mirrors ``mr_mt3``/``piano_d3rm``):

1. ``AURAL_OAF_DRUMS_CHECKPOINT`` env var -- explicit path to the checkpoint
   file (a TF SavedModel dir, a ``.ckpt`` bundle root, or an exported ONNX
   file, depending on how the runtime is wired).
2. A modelpack drop discovered under the standard model search roots at
   ``<root>/drums_oaf/<version>/`` containing a ``modelpack.json`` and the
   checkpoint under ``files/checkpoints/drums_oaf/`` -- exactly the
   ``mr_mt3`` modelpack layout.

Engine id (for the drum registry, wired by a separate lane): ``drums_oaf``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Top-level import of the canonical dataclass mirrors piano_d3rm/piano_hft,
# which import MelodicNote at module scope. DrumEvent is a tiny frozen
# dataclass with no heavy transitive imports, so this stays cheap and safe.
from aural_ingest.transcription import DrumEvent

# Relative path of the checkpoint inside a ``drums_oaf`` modelpack, mirroring
# MT3_DRUM_ENGINE_MODEL_INFO[...]["checkpoint_path"] for mr_mt3. The final
# path component is intentionally left generic ("model") because the O&F drum
# checkpoint can be exported as a TF SavedModel directory, a Lightning-style
# ``.ckpt`` root, or an ONNX file; the runtime picks it up as-is.
_CHECKPOINT_RELPATH = Path("files") / "checkpoints" / "drums_oaf" / "model"

# Model input sample rate. Magenta Onsets-and-Frames operates on 16 kHz mono,
# the same rate the MT3 drum path loads audio at.
_MODEL_SAMPLE_RATE = 16000


def _candidate_model_roots(base: Path) -> list[Path]:
    """Replicate transcription._candidate_model_roots locally.

    Kept local (not imported) so this module never forces a change to a
    private helper in transcription.py; the layout is small and stable.
    """
    from aural_ingest.transcription import MT3_MODELPACK_DIRNAME

    return [
        base,
        base / MT3_MODELPACK_DIRNAME,
        base / "data" / MT3_MODELPACK_DIRNAME,
        base / "AuralPrimerPortable" / "data" / MT3_MODELPACK_DIRNAME,
    ]


def _default_search_roots(stem_path: Path | None) -> list[Path]:
    """Reuse the transcription module's search-root discovery so a drums_oaf
    modelpack is found in exactly the same places as an mr_mt3 one.

    We call the module's own ``_default_mt3_model_search_roots`` (read-only
    reuse -- we never modify it) so this engine tracks any future change to
    where modelpacks are searched.
    """
    from aural_ingest.transcription import _default_mt3_model_search_roots

    return list(_default_mt3_model_search_roots(stem_path))


def _iter_installed_modelpack_dirs(modelpack_id: str, search_roots: list[Path]) -> list[Path]:
    from aural_ingest.transcription import _iter_installed_modelpack_dirs as _iter

    return list(_iter(modelpack_id, search_roots))


def _resolve_checkpoint(stem_path: Path | None) -> Path | None:
    """Resolve an OaF-Drums checkpoint, or return None when unavailable.

    Never raises: absence of a checkpoint is the *expected* state until the
    model is acquired, so callers get an empty transcription and fall through
    to the next engine.
    """
    # 1. Explicit env override (highest priority), mirroring
    #    AURALPRIMER_<MODEL>_CHECKPOINT_PATH / AURAL_PIANO_D3RM_CHECKPOINT.
    explicit = os.environ.get("AURAL_OAF_DRUMS_CHECKPOINT", "").strip()
    if explicit:
        candidate = Path(explicit).expanduser()
        # A checkpoint may be a file (ONNX/.ckpt) or a dir (TF SavedModel).
        if candidate.exists():
            return candidate
        return None

    # 2. Modelpack drop discovered under the standard model search roots,
    #    matching the mr_mt3 modelpack layout (<root>/drums_oaf/<version>/).
    try:
        roots = _default_search_roots(stem_path)
    except Exception:
        return None

    for model_root in _iter_installed_modelpack_dirs("drums_oaf", roots):
        candidate = model_root / _CHECKPOINT_RELPATH
        if candidate.exists():
            return candidate
        # Tolerate a checkpoint file with a common extension appended to the
        # generic "model" stem (e.g. model.onnx), so the modelpack author
        # isn't forced into an exact filename.
        for suffix in (".onnx", ".ckpt", ".pth", ".pb"):
            with_suffix = candidate.with_suffix(suffix)
            if with_suffix.is_file():
                return with_suffix
    return None


def _load_audio_16k_mono(stem_path: Path):
    """Load audio at the model's expected 16 kHz mono, mirroring the MT3
    drum path's ``librosa.load(..., sr=16000, mono=True)``.
    """
    import librosa

    audio, _sr = librosa.load(str(stem_path), sr=_MODEL_SAMPLE_RATE, mono=True)
    return audio.astype("float32")


def _pitch_to_canonical_note(pitch: int) -> int | None:
    """Map an Onsets-and-Frames drum pitch to the project's canonical drum
    note, reusing the *exact same* taxonomy the mr_mt3 path uses.

    Magenta's O&F drum model, like MR-MT3, emits General-MIDI drum pitches
    (35/36 kick, 38 snare, 42/44/46 hi-hat, toms, 49/51 cymbals, ...), which
    is precisely the domain of ``_BENCHMARK_NOTE_TO_CLASS`` ->
    ``_CLASS_TO_CANONICAL_NOTE``. Delegating to the transcription module's
    ``_normalize_midi_note_to_canonical`` guarantees downstream (drum_tab,
    cleanup lanes) is byte-for-byte identical to the mr_mt3 output.
    """
    from aural_ingest.transcription import _normalize_midi_note_to_canonical

    return _normalize_midi_note_to_canonical(int(pitch))


def _run_inference(checkpoint: Path, audio, sample_rate: int) -> list[tuple[float, int, int, float]]:
    """Run OaF-Drums inference and return raw (time, pitch, velocity, dur)
    tuples in *model pitch* space (GM drum pitches), before canonicalization.

    This is the single seam that needs a real runtime once a checkpoint is
    available. It is guarded so its heavy deps (tensorflow/magenta or an ONNX
    runtime) are imported only here, and any failure degrades to an empty
    result rather than propagating. Until the model-loading path is wired to a
    concrete checkpoint format, this returns ``[]`` -- keeping the engine inert
    by construction even if a placeholder checkpoint is dropped in.

    See ``scripts/SETUP-OaF-DRUMS.md`` for the two supported runtime shapes
    (native magenta TF, or an exported ONNX graph) and how to fill this in.
    """
    # NOTE (scaffold): no checkpoint exists to validate a concrete decode
    # against, so the model forward pass is intentionally left unimplemented.
    # When a checkpoint is acquired, implement here:
    #   - native magenta: import magenta.models.onsets_frames_transcription,
    #     restore the E-GMD drums config + checkpoint, run predict, read
    #     pretty_midi drum notes (note.pitch, note.velocity, note.start).
    #   - ONNX export: onnxruntime.InferenceSession(checkpoint); build the
    #     mel/CQT front-end features from ``audio`` at ``sample_rate``; decode
    #     onset+velocity heads to (time, gm_pitch, velocity 1..127, duration).
    # Both paths MUST yield General-MIDI drum pitches so _pitch_to_canonical_note
    # maps them through the shared taxonomy unchanged.
    _ = (checkpoint, audio, sample_rate)
    return []


def transcribe(audio_path: Path, **kwargs: Any) -> list[DrumEvent]:
    """Transcribe drums with OaF-Drums, or return ``[]`` when unavailable.

    Contract (mirrors the mr_mt3 wrapper registered into the drum registry):
    takes a stem path, returns ``list[DrumEvent]`` with canonical drum notes
    and model-predicted velocities. **Never raises** -- missing checkpoint,
    missing torch/tensorflow/magenta, or any inference error all degrade to an
    empty list so the engine fails safe at the head of a fallback chain.

    Args:
        audio_path: path to the (drum) stem to transcribe.
        **kwargs: accepted for registry-call compatibility; ``stem_path`` is
            honored as an alias, everything else is ignored.
    """
    stem_path = Path(audio_path if audio_path is not None else kwargs.get("stem_path", ""))

    checkpoint = _resolve_checkpoint(stem_path)
    if checkpoint is None:
        # Inert: no model on this machine -> hand off to the next engine.
        return []

    try:
        audio = _load_audio_16k_mono(stem_path)
        raw = _run_inference(checkpoint, audio, _MODEL_SAMPLE_RATE)
    except Exception:
        # Any failure (missing deps, corrupt checkpoint, bad audio) fails safe.
        return []

    events: list[DrumEvent] = []
    for time_sec, pitch, velocity, duration in raw:
        canonical_note = _pitch_to_canonical_note(int(pitch))
        if canonical_note is None:
            continue
        events.append(
            DrumEvent(
                time=max(0.0, float(time_sec)),
                note=int(canonical_note),
                velocity=max(1, min(127, int(velocity))),
                duration=max(0.0, float(duration)) if duration else 0.05,
            )
        )
    events.sort(key=lambda ev: (ev.time, ev.note))
    return events
