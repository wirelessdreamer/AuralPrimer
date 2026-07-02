"""In-house drum-transcription CRNN training harness (trained on E-GMD).

Builds and trains OUR OWN 5-class drum-onset model on the Expanded Groove MIDI
Dataset (E-GMD, CC BY 4.0) so the product can ship its own weights instead of
depending on license-blocked models. See ``README.md`` for the architecture
citation, feature config, how to launch a real run, and the E-GMD attribution
requirement.

Nothing here is wired into the runtime transcription pipeline
(``KNOWN_DRUM_ENGINES``); it is offline tooling that produces an exportable
ONNX model.
"""
from __future__ import annotations

from .config import (
    CLASSES,
    NUM_CLASSES,
    FeatureConfig,
    ModelConfig,
    TargetConfig,
    TrainConfig,
)
from .model import DrumCRNN, count_parameters

__all__ = [
    "CLASSES",
    "NUM_CLASSES",
    "FeatureConfig",
    "ModelConfig",
    "TargetConfig",
    "TrainConfig",
    "DrumCRNN",
    "count_parameters",
]
