"""Pitch-aligned CQT spectrogram artifact for the interactive guided-edit overlay.

Produces a quantized Constant-Q magnitude MATRIX (NOT a pre-coloured image) so
the frontend can recolor / gain / threshold / zoom it live in a WebGL shader,
the way Sonic Visualiser separates its cached FFT model from rendering.

Layout written under ``<pack>/features/spectrogram/<role>/``::

    spectrogram.json     geometry + dequantization + tile index
    tile_000.png         8-bit grayscale data tile (rows = CQT bins, cols = time)
    tile_001.png         ...

Alignment: ``MIDI(row r) = fmin_midi + r / bins_per_semitone`` (row 0 = lowest
pitch). With bins_per_octave=12, fmin=C1 (MIDI 24) this is exactly bin k = MIDI
24+k. Magnitude is 16-bit, RG-packed into an RGB PNG (R=high byte, G=low byte);
the client reconstructs ``level16 = R*256 + G`` then
``dB = db_floor + (level16/65535) * (db_ceil - db_floor)``.

See docs/research-spectrogram-overlay-2026-06-22.md.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

# Defaults. fmin = C1 (MIDI 24); 7 octaves -> MIDI 24..107 (covers bass..keys).
FMIN_MIDI = 24
BINS_PER_OCTAVE = 12
N_OCTAVES = 7
# Drums: hi-hats/cymbals live mostly above the 7-octave (~4 kHz) melodic
# ceiling, so the drum-cleanup overlay gets one more octave (~7.9 kHz). Kick /
# snare / toms sit comfortably inside the shared fmin.
N_OCTAVES_DRUMS = 8


def n_octaves_for_role(role: str) -> int:
    """Octave count for a stem role's spectrogram (drums see higher)."""
    return N_OCTAVES_DRUMS if role == "drums" else N_OCTAVES
SAMPLE_RATE = 22050
HOP_LENGTH = 512
# Cross-platform-safe WebGL MAX_TEXTURE_SIZE floor (see research) -> tile width.
TILE_WIDTH = 4096
# dB window the 8-bit levels span (per-file peak-referenced, so ceil = 0 dB).
DB_FLOOR = -80.0
DB_CEIL = 0.0


def compute_cqt_db(
    audio_path: str | Path,
    *,
    sr: int = SAMPLE_RATE,
    hop_length: int = HOP_LENGTH,
    fmin_midi: int = FMIN_MIDI,
    bins_per_octave: int = BINS_PER_OCTAVE,
    n_octaves: int = N_OCTAVES,
) -> dict[str, Any]:
    """Compute a peak-referenced dB Constant-Q magnitude matrix (n_bins, n_frames)."""
    import librosa

    y, sr = librosa.load(str(audio_path), sr=sr, mono=True)
    n_bins = bins_per_octave * n_octaves
    fmin = float(librosa.midi_to_hz(fmin_midi))
    cqt = librosa.cqt(
        y,
        sr=sr,
        hop_length=hop_length,
        fmin=fmin,
        n_bins=n_bins,
        bins_per_octave=bins_per_octave,
    )
    mag = np.abs(cqt)
    # dB in [DB_FLOOR, 0], referenced to the file's peak.
    db = librosa.amplitude_to_db(mag, ref=np.max, top_db=float(-DB_FLOOR))
    return {
        "db": db.astype(np.float32),
        "sr": int(sr),
        "hop_length": int(hop_length),
        "n_bins": int(n_bins),
        "fmin_midi": int(fmin_midi),
        "bins_per_octave": int(bins_per_octave),
    }


def _quantize_u16(db: np.ndarray) -> np.ndarray:
    """dB -> 16-bit level over [DB_FLOOR, DB_CEIL]."""
    q = (db - DB_FLOOR) / (DB_CEIL - DB_FLOOR)
    q = np.clip(q, 0.0, 1.0)
    return (q * 65535.0 + 0.5).astype(np.uint16)


def _pack_rg(u16: np.ndarray) -> np.ndarray:
    """Pack a uint16 matrix into an (H, W, 3) uint8 RGB image: R=high, G=low.

    Browsers decode 16-bit grayscale PNG down to 8-bit, so we carry the two
    bytes in separate channels; the WebGL shader reconstructs the magnitude as
    (R*256 + G) / 65535.
    """
    h, w = u16.shape
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    rgb[:, :, 0] = (u16 >> 8).astype(np.uint8)
    rgb[:, :, 1] = (u16 & 0xFF).astype(np.uint8)
    return rgb


def write_spectrogram_artifact(
    audio_path: str | Path,
    out_dir: str | Path,
    *,
    role: str = "melodic",
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute the CQT and write tiled grayscale data PNGs + spectrogram.json.

    Returns the geometry dict. Unless the caller overrides ``n_octaves``, the
    octave count follows the role (drums get the taller range).
    """
    from PIL import Image

    kwargs.setdefault("n_octaves", n_octaves_for_role(role))
    res = compute_cqt_db(audio_path, **kwargs)
    rgb = _pack_rg(_quantize_u16(res["db"]))  # (n_bins, n_frames, 3)
    n_bins, n_frames = rgb.shape[0], rgb.shape[1]

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    tiles: list[dict[str, Any]] = []
    for i, c0 in enumerate(range(0, max(1, n_frames), TILE_WIDTH)):
        c1 = min(c0 + TILE_WIDTH, n_frames)
        fname = f"tile_{i:03d}.png"
        Image.fromarray(np.ascontiguousarray(rgb[:, c0:c1, :]), mode="RGB").save(
            out / fname, optimize=True
        )
        tiles.append({"file": fname, "col_start": int(c0), "col_end": int(c1)})

    bins_per_semitone = res["bins_per_octave"] // 12
    fps = res["sr"] / res["hop_length"]
    geom: dict[str, Any] = {
        "version": 1,
        "role": role,
        "transform": "cqt",
        "fmin_midi": res["fmin_midi"],
        "bins_per_octave": res["bins_per_octave"],
        "bins_per_semitone": int(bins_per_semitone),
        "n_bins": int(n_bins),
        "midi_low": res["fmin_midi"],
        "midi_high": res["fmin_midi"] + (n_bins - 1) / max(1, bins_per_semitone),
        "sample_rate": res["sr"],
        "hop_length": res["hop_length"],
        "frames_per_sec": fps,
        "n_frames": int(n_frames),
        "duration_sec": n_frames / fps if fps else 0.0,
        "db_floor": DB_FLOOR,
        "db_ceil": DB_CEIL,
        "bit_depth": 16,
        "packing": "rg16",
        "dequant": "level16 = R*256 + G ; dB = db_floor + (level16/65535)*(db_ceil - db_floor)",
        "row_0_is": "lowest_pitch",
        "tile_width": TILE_WIDTH,
        "tiles": tiles,
    }
    (out / "spectrogram.json").write_text(json.dumps(geom, indent=2), encoding="utf-8")
    return geom
