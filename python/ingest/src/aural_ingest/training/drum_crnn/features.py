"""WAV -> log-mel spectrogram frames for the drum CRNN.

Deterministic: the same WAV and the same :class:`FeatureConfig` always yield
the same array. Output layout is ``(n_frames, n_mels)`` (time-major) so it
lines up row-for-row with the per-frame targets and feeds the CRNN, which
treats the mel axis as features and the frame axis as the recurrent sequence.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .config import FeatureConfig


def load_audio_mono(path: str | Path, sample_rate: int) -> np.ndarray:
    """Load an audio file as mono float32 at ``sample_rate`` (librosa)."""
    import librosa

    y, _ = librosa.load(str(path), sr=sample_rate, mono=True)
    return np.asarray(y, dtype=np.float32)


def logmel_from_audio(audio: np.ndarray, cfg: FeatureConfig) -> np.ndarray:
    """Compute log-mel frames from a mono waveform.

    Returns ``(n_frames, n_mels)`` float32. Uses ``center=True`` framing so
    frame ``t`` is centred on sample ``t * hop_length`` -- this is the same
    convention the target builder assumes when it maps an onset time to a
    frame index (``round(t * sr / hop)``).
    """
    import librosa

    mel_power = librosa.feature.melspectrogram(
        y=np.asarray(audio, dtype=np.float32),
        sr=cfg.sample_rate,
        n_fft=cfg.n_fft,
        hop_length=cfg.hop_length,
        n_mels=cfg.n_mels,
        fmin=cfg.fmin,
        fmax=cfg.fmax,
        power=2.0,
        center=True,
    )  # (n_mels, n_frames)
    log_mel = np.log(mel_power + cfg.log_offset)
    return np.ascontiguousarray(log_mel.T.astype(np.float32))  # (n_frames, n_mels)


def logmel_from_path(path: str | Path, cfg: FeatureConfig) -> np.ndarray:
    """Convenience: load ``path`` and return its ``(n_frames, n_mels)`` log-mel."""
    audio = load_audio_mono(path, cfg.sample_rate)
    return logmel_from_audio(audio, cfg)


def n_frames_for_samples(n_samples: int, cfg: FeatureConfig) -> int:
    """Frame count librosa produces for ``n_samples`` with ``center=True``.

    librosa pads by ``n_fft // 2`` on each side, so the frame count is
    ``1 + n_samples // hop_length``. Kept as a helper so the target builder
    can size its target array identically without re-running the STFT.
    """
    return 1 + int(n_samples) // int(cfg.hop_length)
