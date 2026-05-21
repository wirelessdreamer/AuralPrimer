"""Lightweight pre-transcription denoise for stem-separated piano audio.

Demucs (and similar source-separation models) leave two recognizable artifacts
in the ``keys`` stem that hurt downstream transcription:

1.  A low-level wideband noise floor in passages where the piano is silent.
2.  Spectral coloration / reverb-tail residue bled from the ``other`` stem.

We apply a stationary spectral subtraction (Wiener-style) using a noise profile
estimated from the lowest-energy frames. The transform is conservative
(``floor=0.1`` of original magnitude, ``oversubtract=1.2``) to avoid eating
real piano attacks. Pitch and timing are untouched.

This step is opt-out via ``AURAL_PIANO_DENOISE_STEM=0``. It silently passes
through the original path if librosa/soundfile aren't importable, if the audio
can't be decoded (fake test bytes), or if the file is too short to estimate a
noise floor reliably.
"""
from __future__ import annotations

import contextlib
import importlib
import os
import tempfile
from pathlib import Path
from typing import Iterator


_MIN_DURATION_SEC_FOR_DENOISE = 2.0
_NOISE_FRAME_PERCENTILE = 0.10  # bottom 10% lowest-energy frames define the noise floor
_OVERSUBTRACT = 1.2
_FLOOR_GAIN = 0.10


def _enabled() -> bool:
    flag = os.environ.get("AURAL_PIANO_DENOISE_STEM", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def _try_import():
    try:
        librosa = importlib.import_module("librosa")
        np = importlib.import_module("numpy")
        sf = importlib.import_module("soundfile")
    except Exception:
        return None
    return librosa, np, sf


def _spectral_subtract(audio, sr: int, np_mod):
    n_fft = 2048
    hop_length = 512

    librosa = importlib.import_module("librosa")
    stft = librosa.stft(y=audio, n_fft=n_fft, hop_length=hop_length, window="hann")
    magnitude = np_mod.abs(stft)
    phase = np_mod.angle(stft)

    if magnitude.shape[1] < 8:
        return None

    frame_energy = np_mod.sum(magnitude ** 2, axis=0)
    threshold = np_mod.quantile(frame_energy, _NOISE_FRAME_PERCENTILE)
    noise_mask = frame_energy <= max(threshold, 1e-12)
    if not bool(noise_mask.any()):
        return None

    noise_profile = np_mod.mean(magnitude[:, noise_mask], axis=1, keepdims=True)
    cleaned_magnitude = magnitude - _OVERSUBTRACT * noise_profile
    floor = _FLOOR_GAIN * magnitude
    cleaned_magnitude = np_mod.maximum(cleaned_magnitude, floor)

    cleaned_stft = cleaned_magnitude * np_mod.exp(1j * phase)
    cleaned = librosa.istft(cleaned_stft, hop_length=hop_length, length=len(audio))
    return cleaned


@contextlib.contextmanager
def maybe_denoised_stem(stem_path: Path) -> Iterator[Path]:
    """Yield a denoised copy of ``stem_path`` when possible, else the original.

    Any failure during load / processing / write yields the original path,
    so the caller never has to worry about denoise breaking the pipeline.
    """
    if not _enabled():
        yield stem_path
        return

    bundle = _try_import()
    if bundle is None:
        yield stem_path
        return
    librosa, np_mod, sf = bundle

    try:
        # Load at native sample rate (mono) so we don't introduce
        # resampling artifacts.
        audio, sr = librosa.load(path=str(stem_path), sr=None, mono=True)
    except Exception:
        yield stem_path
        return

    if audio is None or len(audio) < int(_MIN_DURATION_SEC_FOR_DENOISE * max(sr, 1)):
        yield stem_path
        return

    try:
        cleaned = _spectral_subtract(audio, int(sr), np_mod)
    except Exception:
        cleaned = None

    if cleaned is None:
        yield stem_path
        return

    tmp_dir: tempfile.TemporaryDirectory | None = None
    try:
        tmp_dir = tempfile.TemporaryDirectory(prefix="aural_denoise_")
        out_path = Path(tmp_dir.name) / "denoised.wav"
        try:
            sf.write(str(out_path), cleaned, int(sr), subtype="FLOAT")
        except Exception:
            yield stem_path
            return
        yield out_path
    finally:
        if tmp_dir is not None:
            try:
                tmp_dir.cleanup()
            except Exception:
                pass
