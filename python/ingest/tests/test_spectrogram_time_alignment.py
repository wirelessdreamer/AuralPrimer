"""Golden click-track alignment for the Cleanup/Edit spectrogram.

The refine workspace draws note/drum markers and the playhead over this
spectrogram, so if the spectrogram's time axis does not match the audio, the
markers visibly drift from the energy. This locks that axis with a synthetic
signal whose events sit at KNOWN times:

  1. the geometry is internally consistent (frames_per_sec == sr/hop,
     duration_sec == n_frames/frames_per_sec), so a hit at time T always maps
     to frame T*fps that the frontend then renders; and
  2. a tone burst emitted at time T actually shows up at frame ~= T*fps in the
     computed CQT (energy lands where the clock says it should).

This is the deterministic half of "do the visuals line up with the audio" — the
physical audio-output/display latency is hardware-specific and lives in the A/V
calibration, not here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
librosa = pytest.importorskip("librosa")
sf = pytest.importorskip("soundfile")

from aural_ingest.spectrogram import (  # noqa: E402
    compute_cqt_db,
    n_octaves_for_role,
    write_spectrogram_artifact,
)

SR = 22_050
BURST_HZ = 220.0  # A3, comfortably inside the CQT range (fmin ~ 32.7 Hz)
BURST_SEC = 0.08
EVENT_TIMES = (0.5, 1.0, 1.5, 2.0)
DURATION_SEC = 2.6


def _write_click_track(path: Path) -> None:
    y = np.zeros(int(SR * DURATION_SEC), dtype=np.float32)
    n = int(BURST_SEC * SR)
    env = np.hanning(n).astype(np.float32)
    tone = np.sin(2 * np.pi * BURST_HZ * np.arange(n) / SR).astype(np.float32)
    for t in EVENT_TIMES:
        i0 = int(round(t * SR))
        y[i0 : i0 + n] += 0.9 * tone * env
    sf.write(str(path), y, SR, subtype="PCM_16")


def test_geometry_is_internally_consistent(tmp_path: Path) -> None:
    wav = tmp_path / "clicks.wav"
    _write_click_track(wav)
    out = tmp_path / "spec"
    geom = write_spectrogram_artifact(str(wav), out, role="melodic", sr=SR)

    # The frontend derives frame = time * frames_per_sec and pixel from frame;
    # these identities are what keep the playhead/markers glued to the energy.
    assert geom["frames_per_sec"] == pytest.approx(geom["sample_rate"] / geom["hop_length"])
    assert geom["duration_sec"] == pytest.approx(
        geom["n_frames"] / geom["frames_per_sec"], rel=0, abs=1e-6
    )
    # n_frames must cover the real audio length (± one hop), not a stale guess.
    assert geom["duration_sec"] == pytest.approx(DURATION_SEC, abs=2 * geom["hop_length"] / SR)
    # Integer identity: n_frames is exactly duration*fps rounded (guards a
    # float reformulation that keeps the ratio but shifts where frames land).
    assert geom["n_frames"] == round(geom["duration_sec"] * geom["frames_per_sec"])
    # The last event must sit strictly inside the tiled content (not off the end).
    last_frame = round((max(EVENT_TIMES) + BURST_SEC / 2) * geom["frames_per_sec"])
    assert last_frame < geom["n_frames"]


def test_drums_role_gets_the_taller_octave_range(tmp_path: Path) -> None:
    # Drum lanes span kick..cymbals, so the drums spectrogram must be 8 octaves
    # (96 bins); shrinking it to the 7-octave melodic range drifts the hi-hat/
    # ride lanes off their energy — the vertical analogue of time drift.
    assert n_octaves_for_role("drums") == 8
    for role in ("melodic", "keys", "bass", "guitar"):
        assert n_octaves_for_role(role) == 7

    wav = tmp_path / "clicks.wav"
    _write_click_track(wav)
    geom = write_spectrogram_artifact(str(wav), tmp_path / "drums_spec", role="drums", sr=SR)
    assert geom["n_bins"] == 96  # 8 octaves * 12 bins
    assert geom["bins_per_octave"] == 12


def test_tone_bursts_land_on_their_frames(tmp_path: Path) -> None:
    wav = tmp_path / "clicks.wav"
    _write_click_track(wav)
    res = compute_cqt_db(str(wav), sr=SR)
    fps = res["sr"] / res["hop_length"]

    # Per-frame energy (linear, from the peak-referenced dB). Bursts dominate.
    energy = np.power(10.0, res["db"] / 20.0).sum(axis=0)

    # A Hanning-enveloped burst has its energy centroid at its CENTER, so that
    # is where the CQT column energy peaks — the honest alignment reference.
    tol_sec = 1.5 / fps  # ~1.5 CQT frames (~35 ms) — locks alignment tightly.
    for t in EVENT_TIMES:
        center = t + BURST_SEC / 2
        lo = int(round((center - 0.12) * fps))
        hi = int(round((center + 0.12) * fps))
        peak_frame = lo + int(np.argmax(energy[lo : hi + 1]))
        peak_time = peak_frame / fps
        assert peak_time == pytest.approx(center, abs=tol_sec), (
            f"burst at {t}s (center {center:.3f}s) peaked at {peak_time:.3f}s "
            f"(frame {peak_frame}, tol {tol_sec:.3f}s)"
        )
