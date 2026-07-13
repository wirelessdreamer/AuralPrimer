"""Run official ADTOF inference from an isolated runtime.

This script is intentionally launched by ``algorithms/adtof_drums.py`` in a
dedicated Python environment. It may import TensorFlow, ADTOF, and pretty_midi;
the main ingest sidecar never imports those packages directly.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile


def _load_contract(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("contract must be a JSON object")
    for key in ("wav_path", "out_json", "repo_path"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise ValueError(f"contract missing {key!r}")
    return data


def _midi_events(midi_path: Path) -> list[list[float | int]]:
    import pretty_midi

    midi = pretty_midi.PrettyMIDI(str(midi_path))
    events: list[list[float | int]] = []
    for instrument in midi.instruments:
        for note in instrument.notes:
            events.append([float(note.start), int(note.pitch), int(note.velocity) or 100, float(note.end - note.start)])
    events.sort(key=lambda event: (event[0], event[1]))
    return events


def _predict(repo_path: Path, wav_path: Path) -> list[list[float | int]]:
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    sys.path.insert(0, str(repo_path))

    from adtof.model.model import Model

    model, hparams = Model.modelFactory(modelName="Frame_RNN", scenario="adtofAll", fold=0)
    if "peakThreshold" not in hparams:
        raise RuntimeError("official ADTOF model hparams did not include peakThreshold")
    if not getattr(model, "weightLoadedFlag", False):
        raise RuntimeError("official ADTOF weights were not loaded from adtof/models")

    with tempfile.TemporaryDirectory(prefix="adtof_out_") as temp_dir:
        out_dir = Path(temp_dir)
        model.predictFolder(str(wav_path), str(out_dir), writeMidi=True, **hparams)
        midi_files = sorted(out_dir.glob("*.mid"))
        if not midi_files:
            raise RuntimeError("official ADTOF predictFolder produced no MIDI output")
        return _midi_events(midi_files[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract_json", type=Path)
    args = parser.parse_args(argv)

    contract = _load_contract(args.contract_json)
    repo_path = Path(contract["repo_path"]).expanduser().resolve()
    wav_path = Path(contract["wav_path"]).expanduser().resolve()
    out_json = Path(contract["out_json"]).expanduser().resolve()

    events = _predict(repo_path, wav_path)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps({"events": events}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
