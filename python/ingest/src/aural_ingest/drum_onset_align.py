"""Onset-driven drum-hit timing.

A neural drum transcriber (mr_mt3) decides WHICH drum was hit and how hard, but
its onset *times* wobble by tens of milliseconds. This module refines the time
of each hit by snapping it to the nearest real audio transient in that voice's
frequency band, so the drum-tab markers land on the energy in the spectrogram
(and the game charts on the true attack).

Low/mid voices (kick, snare, toms) align tightly. High voices (hi-hats, cymbals)
are NOT snapped at all (``_NO_SNAP_LANES``): onset detection on a separated
stem's high band is smeared/late and, with dense hi-hats, the snap window grabs
a neighboring transient and drags hits ~30 ms late — measurably worse than the
raw mr_mt3 times, which already sit ~on the onset. So those lanes keep the
transcriber's timing.

Pure-ish: imports numpy/librosa lazily and degrades to the input unchanged if
audio or those libs are unavailable, so callers never hard-fail on it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Per-lane (band_lo_hz, band_hi_hz, onset_delta). Higher/quieter voices get a
# lower peak-pick threshold. A lane not listed here (or in _NO_SNAP_LANES) is
# never moved.
_LANE_BANDS: dict[str, tuple[float, float, float]] = {
    "kick": (30.0, 130.0, 0.12),
    "tom_low": (60.0, 200.0, 0.12),
    "tom_mid": (100.0, 300.0, 0.12),
    "tom_high": (150.0, 450.0, 0.12),
    "snare": (150.0, 500.0, 0.10),
    "hihat_closed": (5000.0, 11000.0, 0.06),
    "hihat_open": (5000.0, 11000.0, 0.06),
    "ride": (4000.0, 11000.0, 0.06),
    "crash": (2500.0, 11000.0, 0.06),
}

# High voices are NOT snapped. Measured on real songs (Beat It, ~150-170s):
# the raw mr_mt3 hi-hats sit ~on the onset (median -7 ms) but the snap dragged
# them ~+30 ms LATE — the 5-11 kHz onset detection is smeared/late and the 90 ms
# window grabs a *neighboring* dense-hi-hat transient. So we keep the
# transcriber's times for these lanes; kick/snare/toms still snap tightly.
_NO_SNAP_LANES: frozenset[str] = frozenset({"hihat_closed", "hihat_open", "ride", "crash"})
_SNAP_WINDOW_SEC = 0.09  # mr_mt3 is often off by >50ms; still < a 16th @ ~150 BPM
_HOP = 128  # ~5.8ms resolution at 22050 Hz


def align_drum_tab_to_onsets(
    drum_tab: dict[str, Any],
    drums_wav: str | Path,
    *,
    sr: int = 22050,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Snap each hit's time to the nearest audio transient in its voice's band.

    Returns ``(new_drum_tab, stats)`` where stats has ``moved`` / ``kept`` /
    ``total``. Conflict-free greedy assignment (the closest hit claims each onset
    first) means two hits never collapse onto one transient; hit count, lane
    (``p``) and velocity (``v``) are preserved. On any failure the input is
    returned unchanged with ``moved=0``.
    """
    hits = drum_tab.get("hits")
    if not isinstance(hits, list) or not hits:
        return drum_tab, {"moved": 0, "kept": len(hits or []), "total": len(hits or [])}

    try:
        import librosa
        import numpy as np
    except Exception:  # noqa: BLE001 — numpy/librosa absent (minimal env)
        return drum_tab, {"moved": 0, "kept": len(hits), "total": len(hits)}

    try:
        y, sr = librosa.load(str(drums_wav), sr=sr, mono=True)
    except Exception:  # noqa: BLE001 — missing/unreadable stem
        return drum_tab, {"moved": 0, "kept": len(hits), "total": len(hits)}
    if y.size == 0:
        return drum_tab, {"moved": 0, "kept": len(hits), "total": len(hits)}

    stft = np.abs(librosa.stft(y, n_fft=2048, hop_length=_HOP))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)

    def band_onsets(lo: float, hi: float, delta: float):
        mask = (freqs >= lo) & (freqs <= hi)
        if not mask.any():
            return np.empty(0)
        env = librosa.onset.onset_strength(
            S=librosa.amplitude_to_db(stft[mask], ref=np.max), sr=sr, hop_length=_HOP
        )
        peak = env.max()
        if peak <= 0:
            return np.empty(0)
        env = env / peak
        return np.sort(
            librosa.onset.onset_detect(
                onset_envelope=env, sr=sr, hop_length=_HOP,
                units="time", delta=delta, wait=2, backtrack=True,
            )
        )

    by_lane: dict[str, list[int]] = {}
    for i, h in enumerate(hits):
        by_lane.setdefault(str(h.get("p", "")), []).append(i)

    new_t: dict[int, float] = {}
    moved = 0
    for lane, idxs in by_lane.items():
        band = None if lane in _NO_SNAP_LANES else _LANE_BANDS.get(lane)
        det = band_onsets(*band) if band is not None else np.empty(0)
        if det.size == 0:
            for i in idxs:
                new_t[i] = float(hits[i].get("t", 0.0))
            continue
        # Greedy: hits closest to a transient claim it first, so no two hits land
        # on the same onset and a nearer hit is never displaced by a farther one.
        idxs_sorted = sorted(
            idxs, key=lambda i: float(np.min(np.abs(det - float(hits[i].get("t", 0.0)))))
        )
        used: set[int] = set()
        for i in idxs_sorted:
            t = float(hits[i].get("t", 0.0))
            placed = False
            for j in np.argsort(np.abs(det - t)):
                if abs(float(det[j]) - t) > _SNAP_WINDOW_SEC:
                    break
                if int(j) in used:
                    continue
                used.add(int(j))
                snapped = float(det[j])
                new_t[i] = snapped
                if abs(snapped - t) > 1e-4:
                    moved += 1
                placed = True
                break
            if not placed:
                new_t[i] = t

    out_hits: list[dict[str, Any]] = []
    for i, h in enumerate(hits):
        nh = dict(h)
        nh["t"] = round(new_t.get(i, float(h.get("t", 0.0))), 4)
        out_hits.append(nh)
    out_hits.sort(key=lambda h: (h.get("t", 0.0), str(h.get("p", ""))))

    result = dict(drum_tab)
    result["hits"] = out_hits
    return result, {"moved": moved, "kept": len(hits) - moved, "total": len(hits)}
