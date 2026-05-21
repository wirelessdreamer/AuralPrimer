"""Adapter for D3RM piano transcription (Kim, Kwon, Nam, ICASSP 2025).

D3RM is a discrete denoising-diffusion refinement model. Per the paper
(arxiv:2501.05068), it achieves 97.55 onset F1 on MAESTRO -- the strongest
reported single-model number we know of. Public implementation is at
https://github.com/hanshounsu/d3rm under MIT.

The reference repo isn't packaged for PyPI and depends on ``natten`` (which has
spotty Windows wheels) plus ``pytorch-lightning 2.5``, so we run it out of
process the same way ``piano_hft`` does: the user wires up a command via env
var and we invoke it as a subprocess.

Required environment:

- ``AURAL_PIANO_D3RM_COMMAND`` -- command template containing the placeholders
  ``{audio}``, ``{midi}``, ``{checkpoint}``, and optionally ``{config}``.
- ``AURAL_PIANO_D3RM_CHECKPOINT`` -- path to the D3RM ``.ckpt`` (auto-discovered
  under ``<modelpack>/piano_d3rm/`` if unset).
- ``AURAL_PIANO_D3RM_CONFIG`` -- optional yaml config path. If the command
  template references ``{config}``, this must be set or auto-discovered next to
  the checkpoint as ``D3RM_cli.yaml``.

Example::

    AURAL_PIANO_D3RM_COMMAND="python -m d3rm.main_cli test \
        --audio {audio} --output {midi} --ckpt_path {checkpoint} -c {config}"
"""
from __future__ import annotations

import importlib
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from aural_ingest.algorithms.piano_midi import decode_midi_notes
from aural_ingest.transcription import MelodicNote


def _resolve_bundled_checkpoint() -> Path | None:
    from aural_ingest.transcription import (
        _default_basic_pitch_model_roots,
        resolve_piano_d3rm_checkpoint_path,
    )

    return resolve_piano_d3rm_checkpoint_path(_default_basic_pitch_model_roots())


def _resolve_bundled_config(checkpoint: Path) -> Path | None:
    candidates = [
        checkpoint.with_suffix(".yaml"),
        checkpoint.parent / "D3RM_cli.yaml",
        checkpoint.parent / "config.yaml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _checkpoint_path() -> Path:
    raw = os.environ.get("AURAL_PIANO_D3RM_CHECKPOINT", "").strip()
    if raw:
        checkpoint = Path(raw)
        if not checkpoint.is_file():
            raise RuntimeError(f"piano_d3rm checkpoint not found: {checkpoint}")
        return checkpoint
    bundled = _resolve_bundled_checkpoint()
    if bundled is not None:
        return bundled
    raise RuntimeError(
        "piano_d3rm requires AURAL_PIANO_D3RM_CHECKPOINT or a bundled checkpoint "
        "under <modelpack>/piano_d3rm/"
    )


def _config_path(checkpoint: Path) -> Path | None:
    raw = os.environ.get("AURAL_PIANO_D3RM_CONFIG", "").strip()
    if raw:
        config = Path(raw)
        if not config.is_file():
            raise RuntimeError(f"piano_d3rm config not found: {config}")
        return config
    return _resolve_bundled_config(checkpoint)


def _configured_command(stem_path: Path, out_midi: Path, checkpoint: Path) -> list[str]:
    raw = os.environ.get("AURAL_PIANO_D3RM_COMMAND", "").strip()
    if not raw:
        raise RuntimeError(
            "piano_d3rm requires AURAL_PIANO_D3RM_COMMAND with {audio}, {midi}, "
            "{checkpoint} (and optionally {config}) placeholders"
        )

    format_kwargs: dict[str, str] = {
        "audio": str(stem_path),
        "midi": str(out_midi),
        "checkpoint": str(checkpoint),
    }
    if "{config}" in raw:
        config = _config_path(checkpoint)
        if config is None:
            raise RuntimeError(
                "piano_d3rm command references {config} but no config was found "
                "via AURAL_PIANO_D3RM_CONFIG or alongside the checkpoint"
            )
        format_kwargs["config"] = str(config)

    rendered = raw.format(**format_kwargs)
    return shlex.split(rendered, posix=False)


def _summarize_process_error(result: subprocess.CompletedProcess[str]) -> str:
    detail = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    return f": {detail[:800]}" if detail else ""


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "keys",
) -> list[MelodicNote]:
    checkpoint = _checkpoint_path()
    with tempfile.TemporaryDirectory(prefix="aural_d3rm_") as tmp:
        out_midi = Path(tmp) / "d3rm.mid"
        cmd = _configured_command(stem_path, out_midi, checkpoint)
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"piano_d3rm failed{_summarize_process_error(result)}")
        if not out_midi.is_file():
            raise RuntimeError("piano_d3rm completed without writing a MIDI file")
        return decode_midi_notes(out_midi, instrument=instrument)
