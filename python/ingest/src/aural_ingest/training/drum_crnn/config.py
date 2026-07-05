"""Fixed, documented configuration for the drum-CRNN training harness.

Everything that determines the shape of a training example (audio front-end
and target construction) lives here so features and targets stay in lockstep
across extraction, training, and inference. Change a value here and every
consumer picks it up; there is no second source of truth.

The audio front-end mirrors the conventions already used elsewhere in the
package (22.05 kHz mono, 512-sample hop, log-mel) so tooling stays coherent.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# The five-class drum taxonomy this model predicts, in a FIXED order. Index i
# in the model's output channel dimension corresponds to CLASSES[i]. Matches
# ``STANDARD_5CLASS_DRUM_VOCABULARY`` in ``algorithms/_common.py``.
CLASSES: tuple[str, ...] = ("kick", "snare", "hi_hat", "toms", "cymbals")
NUM_CLASSES: int = len(CLASSES)

# Canonical GM MIDI note per 5-class bucket, used when decoding predicted
# onsets back to ``DrumEvent``s. Mirrors ``DRUM_5CLASS_TO_MIDI`` in
# ``algorithms/_common.py`` (kick=36, snare=38, hi_hat=42, toms=47, cymbals=49);
# duplicated here as a plain literal so the training package stays importable
# without pulling in the transcription/algorithms stack.
DRUM_5CLASS_TO_MIDI_FALLBACK: dict[str, int] = {
    "kick": 36,
    "snare": 38,
    "hi_hat": 42,
    "toms": 47,
    "cymbals": 49,
}


@dataclass(frozen=True)
class FeatureConfig:
    """Log-mel spectrogram front-end. Deterministic given the same input.

    frames_per_second = sample_rate / hop_length. With the defaults below the
    frame rate is 22050 / 220 = 100.2 Hz (~10 ms/frame), fine enough to
    resolve closely-spaced drum onsets while keeping sequences short.
    """

    sample_rate: int = 22050
    n_fft: int = 1024
    hop_length: int = 220
    n_mels: int = 84
    fmin: float = 30.0
    fmax: float = 11025.0
    # Natural-log compression of the mel power spectrogram with a small floor
    # so log(0) never appears. (log-mel, the standard ADT front-end input.)
    log_offset: float = 1e-6

    @property
    def frames_per_second(self) -> float:
        return self.sample_rate / float(self.hop_length)


@dataclass(frozen=True)
class TargetConfig:
    """Per-frame multi-label onset targets.

    Each ground-truth onset is written into the target for its class as a
    small triangular bump centred on the onset frame (``smoothing_frames``
    either side). This target-smoothing / label-widening is the standard ADT
    trick: it tolerates the few-ms quantisation between the true onset and the
    frame grid and gives the loss a gentler gradient than a single hot frame.
    """

    smoothing_frames: int = 1  # +/- this many frames around each onset


@dataclass(frozen=True)
class ModelConfig:
    """Compact CRNN. Kept small for CPU inference in the sidecar."""

    n_mels: int = 84  # must equal FeatureConfig.n_mels
    conv_channels: tuple[int, ...] = (32, 32, 64)
    gru_hidden: int = 64
    gru_layers: int = 1
    dropout: float = 0.2
    num_classes: int = NUM_CLASSES


@dataclass(frozen=True)
class TrainConfig:
    """Training-loop hyper-parameters and I/O."""

    corpus_root: str = r"E:\AudioSourceOfTruthData\extracted\e_gmd"
    output_dir: str = ""  # required at launch; keep OUT of the repo tree
    epochs: int = 30
    batch_size: int = 8
    learning_rate: float = 1e-3
    clip_seconds: float = 8.0  # crop/pad each example to a fixed length
    train_limit: int | None = None  # cap #files (None = whole split)
    val_limit: int | None = None
    num_workers: int = 0
    seed: int = 0
    # "auto" -> cuda when available else cpu; "cpu"/"cuda" force a device.
    device: str = "auto"
    # Frame is counted a positive prediction when sigmoid >= this (for F1).
    frame_threshold: float = 0.5
    feature: FeatureConfig = field(default_factory=FeatureConfig)
    target: TargetConfig = field(default_factory=TargetConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
