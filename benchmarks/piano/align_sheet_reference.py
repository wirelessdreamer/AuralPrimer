#!/usr/bin/env python3
"""Turn official sheet music into a reference aligned to a specific recording.

Published sheet music states what the *arrangement* says at a nominal tempo.
A recording is a performance: different tempo, rubato, a live intro, a repeat
the chart does not have. Scoring transcriptions against the raw chart would
mostly measure that mismatch, so the chart is warped onto the recording's
timeline before anything is scored.

Pipeline
--------
1. Parse the score (MusicXML/.mxl via the sidecar parser, or plain MIDI) and
   keep the piano / keys part.
2. Pick the transposition: the chart may be published in a different key from
   the recording (SongSelect publishes Center in D, What A God in G). All 12
   rotations are tried and the cheapest DTW alignment wins.
3. Chroma-DTW the score against the audio and warp every note onto the
   recording's timeline.
4. Write ``<run>/references/<item>.json`` for the report to score against.

The result is a *sheet-derived* reference, which is not the same as ground
truth: published arrangements are simplified, so genuine embellishment by the
player scores as a false positive. The report labels it accordingly and
reports score-covered vs score-absent regions separately.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "python" / "ingest" / "src"))

from aural_ingest.transcription import MelodicNote  # noqa: E402

KEYS_ROLES = {"keys", "piano", "synth"}


# ---------------------------------------------------------------------------
# score loading
# ---------------------------------------------------------------------------

def load_score(path: Path) -> tuple[list[MelodicNote], str]:
    """Return (notes, source_kind) for the piano/keys part of a score file."""
    suffix = path.suffix.lower()
    if suffix in {".musicxml", ".mxl", ".xml"}:
        from aural_ingest.musicxml_import import parse_musicxml

        by_role = parse_musicxml(path)
        notes: list[MelodicNote] = []
        for role in ("keys", "piano", "synth"):
            notes.extend(by_role.get(role, []))
        if not notes:  # single-staff lead sheets often land in another bucket
            notes = [n for role_notes in by_role.values() for n in role_notes]
        return sorted(notes, key=lambda n: (n.t_on, n.pitch)), "musicxml"

    if suffix in {".mid", ".midi"}:
        from aural_ingest.piano_benchmark import parse_piano_midi_reference

        events = parse_piano_midi_reference(path, 0.0, role="keys")
        notes = [
            MelodicNote(t_on=float(e.time), t_off=float(e.time) + float(e.duration),
                        pitch=int(e.pitch), velocity=int(e.velocity), instrument="keys")
            for e in events
        ]
        return notes, "midi"

    raise ValueError(f"unsupported score format: {path.suffix} (want .musicxml/.mxl/.mid)")


# ---------------------------------------------------------------------------
# alignment
# ---------------------------------------------------------------------------

FS = 22050
FEATURE_RATE = 50
# DLNCO's decay kernel, reused so the score-side onset feature has the same
# shape as the audio-side one synctoolbox computes.
_DECAY = [1.0, 0.70710678, 0.57735027, 0.5, 0.4472136,
          0.40824829, 0.37796447, 0.35355339, 0.33333333, 0.31622777]


def _score_features(notes: list[MelodicNote], frames: int):
    """Build synctoolbox-shaped features straight from the score.

    The score already *is* a pitch activation matrix, so sonifying it and then
    re-estimating pitch from the synthetic audio only injects error (measured:
    median 1.9 s vs 54 ms). Building the piano roll and a DLNCO-style decaying
    chroma onset feature directly keeps the score side exact.
    """
    import numpy as np
    from synctoolbox.feature.chroma import pitch_to_chroma, quantize_chroma

    decay = np.array(_DECAY)
    roll = np.zeros((128, frames), dtype=np.float64)
    onset = np.zeros((12, frames), dtype=np.float64)
    for note in notes:
        pitch = int(note.pitch)
        if not 0 <= pitch < 128:
            continue
        a = int(round(float(note.t_on) * FEATURE_RATE))
        if a >= frames:
            continue
        b = min(frames, max(a + 1, int(round(float(note.t_off) * FEATURE_RATE))))
        amp = int(note.velocity) / 127.0
        roll[pitch, a:b] += amp
        k = min(len(decay), frames - a)
        onset[pitch % 12, a:a + k] += decay[:k] * amp
    onset /= np.maximum(np.linalg.norm(onset, axis=0, keepdims=True), 1e-8)
    return quantize_chroma(pitch_to_chroma(f_pitch=roll)), onset


def _audio_features(y, tuning: int):
    from synctoolbox.feature.chroma import pitch_to_chroma, quantize_chroma
    from synctoolbox.feature.dlnco import pitch_onset_features_to_DLNCO
    from synctoolbox.feature.pitch import audio_to_pitch_features
    from synctoolbox.feature.pitch_onset import audio_to_pitch_onset_features

    f_pitch = audio_to_pitch_features(f_audio=y, Fs=FS, tuning_offset=tuning,
                                      feature_rate=FEATURE_RATE, verbose=False)
    f_chroma = quantize_chroma(pitch_to_chroma(f_pitch=f_pitch))
    peaks = audio_to_pitch_onset_features(f_audio=y, Fs=FS, tuning_offset=tuning, verbose=False)
    f_dlnco = pitch_onset_features_to_DLNCO(f_peaks=peaks, feature_rate=FEATURE_RATE,
                                            feature_sequence_length=f_chroma.shape[1],
                                            visualize=False)
    return f_chroma, f_dlnco


def align_mrmsdtw(audio_path: Path, notes: list[MelodicNote], *,
                  transpose: str | int = "auto") -> dict[str, Any]:
    """Align a score to a recording with synctoolbox MrMsDTW + DLNCO onsets.

    Multi-resolution DTW keeps a 17-minute recording tractable, and the DLNCO
    onset features supply the attack precision that chroma alone cannot.
    A constant residual lag remains (the pitch-feature window trails the true
    attack); it is measured against the audio's own onset envelope and removed,
    which needs no ground truth.

    Validated on a synthetic case (a Psalm's reference MIDI re-tempoed 6% and
    transposed down a fifth): median 13 ms, p90 41 ms, 95% of notes inside the
    60 ms scoring tolerance -- against 55 ms / 740 ms / 55% for plain chroma-DTW.
    """
    import librosa
    import numpy as np
    from synctoolbox.dtw.mrmsdtw import sync_via_mrmsdtw
    from synctoolbox.dtw.utils import (compute_optimal_chroma_shift,
                                       make_path_strictly_monotonic, shift_chroma_vectors)
    from synctoolbox.feature.chroma import quantized_chroma_to_CENS

    y, _ = librosa.load(str(audio_path), sr=FS, mono=True)
    tuning = int(round(float(librosa.estimate_tuning(y=y, sr=FS)) * 100))
    c_audio, d_audio = _audio_features(y, tuning)

    span = max(float(n.t_off) for n in notes)
    c_score, d_score = _score_features(notes, int(round(span * FEATURE_RATE)) + 1)

    if transpose == "auto":
        shift = int(compute_optimal_chroma_shift(
            quantized_chroma_to_CENS(c_score, 201, 50, FEATURE_RATE)[0],
            quantized_chroma_to_CENS(c_audio, 201, 50, FEATURE_RATE)[0]))
    else:
        shift = (-int(transpose)) % 12
    # compute_optimal_chroma_shift(X, Y) returns the shift to apply to *Y* --
    # applying it to X instead inverts the correction and the path wanders by
    # seconds (measured: median 4.8 s).
    c_audio = shift_chroma_vectors(c_audio, shift)
    d_audio = shift_chroma_vectors(d_audio, shift)

    wp = sync_via_mrmsdtw(f_chroma1=c_score, f_onset1=d_score,
                          f_chroma2=c_audio, f_onset2=d_audio,
                          input_feature_rate=FEATURE_RATE,
                          step_weights=np.array([1.5, 1.5, 2.0]),
                          threshold_rec=10 ** 6, verbose=False)
    wp = make_path_strictly_monotonic(wp)
    src, dst = wp[0] / FEATURE_RATE, wp[1] / FEATURE_RATE

    semitones = (-shift) % 12
    if semitones > 6:
        semitones -= 12
    warped = [
        MelodicNote(t_on=round(float(np.interp(float(n.t_on), src, dst)), 6),
                    t_off=round(float(np.interp(float(n.t_off), src, dst)), 6),
                    pitch=int(n.pitch) + semitones, velocity=int(n.velocity), instrument="keys")
        for n in notes
    ]
    warped.sort(key=lambda n: (n.t_on, n.pitch))

    from report_piano_shootout import calibrate_offset
    from aural_ingest.piano_benchmark import PianoBenchmarkEvent

    events = [PianoBenchmarkEvent(time=n.t_on, pitch=n.pitch,
                                  duration=max(0.02, n.t_off - n.t_on), velocity=n.velocity)
              for n in warped]
    lag, lag_corr = calibrate_offset(audio_path, events, max_shift_sec=0.3)
    if lag:
        warped = [MelodicNote(t_on=round(n.t_on + lag, 6), t_off=round(n.t_off + lag, 6),
                              pitch=n.pitch, velocity=n.velocity, instrument="keys")
                  for n in warped]

    return {
        "notes": warped,
        "transpose_semitones": semitones,
        "dtw_cost": None,
        "residual_lag_sec": lag,
        "residual_lag_corr": lag_corr,
        "onsets_snapped": 0,
        "audio_duration_sec": round(len(y) / FS, 3),
        "score_span_sec": round(span, 3),
        "method": "mrmsdtw",
    }


def _unit_columns(chroma):
    """L2-normalise each frame, giving silent frames a uniform vector.

    A rest is an all-zero column, and cosine distance against a zero vector is
    undefined -- it propagates NaN through the whole DTW cost matrix. Silent
    frames become uniform instead, which is equidistant from every real chroma
    rather than poisonous.
    """
    import numpy as np

    norm = np.linalg.norm(chroma, axis=0, keepdims=True)
    silent = norm[0] < 1e-8
    out = np.divide(chroma, np.maximum(norm, 1e-8), dtype=np.float32)
    if silent.any():
        out[:, silent] = np.float32(1.0 / np.sqrt(12.0))
    return out


def _score_chroma(notes: list[MelodicNote], frames: int, sr: int, hop: int):
    import numpy as np

    chroma = np.zeros((12, frames), dtype=np.float32)
    for note in notes:
        lo = max(0, int(round(float(note.t_on) * sr / hop)))
        hi = min(frames, int(round(float(note.t_off) * sr / hop)) + 1)
        if hi <= lo:
            hi = min(frames, lo + 1)
        chroma[int(note.pitch) % 12, lo:hi] += 1.0
    return _unit_columns(chroma)


def _pool(chroma, factor: int):
    """Average-pool chroma along time, renormalised per frame."""
    import numpy as np

    frames = chroma.shape[1]
    usable = (frames // factor) * factor
    if usable < factor:
        return chroma
    pooled = chroma[:, :usable].reshape(12, usable // factor, factor).mean(axis=2)
    return _unit_columns(pooled)


def _refine_chunked(X, Y, centers, *, chunk: int, pad: int):
    """Refine a coarse alignment chunk by chunk at full resolution.

    A single full-resolution cost matrix is impossible here -- a 17-minute
    recording against a 4-minute chart is billions of cells. The coarse pass
    localises each chunk of the score to a short window of audio, and each of
    those small pairs is aligned with librosa's own DTW. Keeps the well-tested
    implementation and stays linear in score length.
    """
    import librosa
    import numpy as np

    n, m = X.shape[1], Y.shape[1]
    score_idx: list[int] = []
    audio_idx: list[int] = []
    costs: list[float] = []

    for start in range(0, n, chunk):
        end = min(n, start + chunk)
        if end - start < 8:
            break
        a0 = int(max(0, centers[start] - pad))
        a1 = int(min(m, centers[end - 1] + pad))
        if a1 - a0 < 8:
            continue
        cost, path = librosa.sequence.dtw(X=X[:, start:end], Y=Y[:, a0:a1],
                                          metric="cosine", subseq=True, backtrack=True)
        pairs = np.array(path)[::-1]
        score_idx.extend((pairs[:, 0] + start).tolist())
        audio_idx.extend((pairs[:, 1] + a0).tolist())
        costs.append(float(cost[-1].min()) / max(1, len(pairs)))

    if not score_idx:
        return np.zeros((0, 2), dtype=np.int64), float("inf")

    # One audio frame per score frame (median), then force monotonicity so the
    # stitched per-chunk paths cannot run backwards at a boundary.
    order = np.argsort(np.asarray(score_idx, dtype=np.int64), kind="stable")
    s_sorted = np.asarray(score_idx, dtype=np.int64)[order]
    a_sorted = np.asarray(audio_idx, dtype=np.int64)[order]
    uniq, starts = np.unique(s_sorted, return_index=True)
    medians = np.array([np.median(a_sorted[lo:hi]) for lo, hi in
                        zip(starts, list(starts[1:]) + [len(a_sorted)])])
    medians = np.maximum.accumulate(medians)
    return np.stack([uniq, medians.astype(np.int64)], axis=1), float(np.mean(costs))


def _snap_to_onsets(notes: list[MelodicNote], audio_path: Path, *, tol: float = 0.15):
    """Snap warped chord onsets onto detected audio onsets.

    Chroma resolves harmony, not attacks: the CQT windows needed for low notes
    smear onsets by ~50-100 ms, which is on the order of the whole scoring
    tolerance. DTW therefore lands the reference in the right bar but not on
    the right millisecond. Snapping each *chord* (all notes sharing an onset
    move together, so voicings stay intact) to the nearest detected attack
    recovers that precision.
    """
    import librosa
    import numpy as np

    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
    detected = librosa.onset.onset_detect(y=y, sr=sr, hop_length=256, units="time",
                                          backtrack=True)
    if detected.size == 0 or not notes:
        return notes, 0

    grouped: dict[float, list[MelodicNote]] = {}
    for note in notes:
        grouped.setdefault(round(float(note.t_on), 3), []).append(note)

    snapped = 0
    out: list[MelodicNote] = []
    for t_on, members in grouped.items():
        idx = int(np.searchsorted(detected, t_on))
        best, best_d = None, tol
        for cand in detected[max(0, idx - 1): idx + 2]:
            d = abs(float(cand) - t_on)
            if d < best_d:
                best, best_d = float(cand), d
        delta = (best - t_on) if best is not None else 0.0
        if best is not None:
            snapped += 1
        for note in members:
            out.append(MelodicNote(
                t_on=round(float(note.t_on) + delta, 6),
                t_off=round(float(note.t_off) + delta, 6),
                pitch=note.pitch, velocity=note.velocity, instrument=note.instrument))
    out.sort(key=lambda n: (n.t_on, n.pitch))
    return out, snapped


def align(
    audio_path: Path,
    notes: list[MelodicNote],
    *,
    transpose: str | int = "auto",
    sr: int = 22050,
    hop: int = 512,
    coarse_factor: int = 4,
    band_sec: float = 0.75,
    snap: bool = True,
) -> dict[str, Any]:
    import librosa
    import numpy as np

    y, _ = librosa.load(str(audio_path), sr=sr, mono=True)
    audio_chroma = _unit_columns(librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop))
    audio_frames = audio_chroma.shape[1]

    span = max((float(n.t_off) for n in notes), default=0.0)
    score_frames = max(1, int(round(span * sr / hop)) + 1)
    base = _score_chroma(notes, score_frames, sr, hop)

    audio_coarse = _pool(audio_chroma, coarse_factor)
    candidates = range(12) if transpose == "auto" else [int(transpose) % 12]

    # Coarse pass over all 12 rotations: the chart may be published in a
    # different key from the recording, and the cheapest alignment reveals it.
    best: dict[str, Any] | None = None
    for semitones in candidates:
        rotated = np.roll(base, semitones, axis=0)
        coarse = _pool(rotated, coarse_factor)
        # subseq=True: a 4-minute chart against a 17-minute performance must be
        # allowed to match a *portion* of the recording, not be stretched across
        # all of it.
        cost, path = librosa.sequence.dtw(X=coarse, Y=audio_coarse, metric="cosine",
                                          subseq=True, backtrack=True)
        normalized = float(cost[-1].min()) / max(1, len(path))
        if best is None or normalized < best["cost"]:
            best = {"semitones": semitones, "cost": normalized, "coarse_path": path}
    assert best is not None

    semitones = int(best["semitones"])
    rotated = np.roll(base, semitones, axis=0)

    # Fine pass, banded around the coarse path.
    coarse_pairs = np.array(best["coarse_path"])[::-1].astype(np.float64)
    centers = np.interp(
        np.arange(rotated.shape[1]),
        coarse_pairs[:, 0] * coarse_factor,
        coarse_pairs[:, 1] * coarse_factor,
    )
    pad = max(8, int(round(band_sec * sr / hop)))
    chunk = max(64, int(round(30.0 * sr / hop)))
    pairs, fine_cost = _refine_chunked(rotated, audio_chroma, centers, chunk=chunk, pad=pad)
    if pairs.size == 0:
        raise RuntimeError("alignment failed: no DTW path recovered")

    score_f = pairs[:, 0].astype(np.float64)
    audio_f = pairs[:, 1].astype(np.float64)
    best["cost"] = round(fine_cost, 5)

    def warp(t: float) -> float:
        frame = t * sr / hop
        return float(np.interp(frame, score_f, audio_f) * hop / sr)

    semitones = int(best["semitones"])
    warped = [
        MelodicNote(
            t_on=round(warp(float(n.t_on)), 6),
            t_off=round(max(warp(float(n.t_off)), warp(float(n.t_on)) + 0.02), 6),
            pitch=int(n.pitch) + semitones,
            velocity=int(n.velocity),
            instrument="keys",
        )
        for n in notes
    ]
    warped.sort(key=lambda n: (n.t_on, n.pitch))

    snapped = 0
    if snap:
        warped, snapped = _snap_to_onsets(warped, audio_path)

    return {
        "notes": warped,
        "onsets_snapped": snapped,
        "transpose_semitones": semitones,
        "dtw_cost": round(float(best["cost"]), 5),
        "audio_duration_sec": round(audio_frames * hop / sr, 3),
        "score_span_sec": round(span, 3),
    }


def write_reference(run_dir: Path, item_id: str, result: dict[str, Any], meta: dict[str, Any]) -> Path:
    out = run_dir / "references" / f"{item_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "item_id": item_id,
        "kind": "sheet_aligned",
        **meta,
        "transpose_semitones": result["transpose_semitones"],
        "dtw_cost": result.get("dtw_cost"),
        "method": result.get("method", "chroma"),
        "residual_lag_sec": result.get("residual_lag_sec"),
        "audio_duration_sec": result["audio_duration_sec"],
        "score_span_sec": result["score_span_sec"],
        "events": [
            {"time": n.t_on, "duration": round(n.t_off - n.t_on, 6),
             "pitch": n.pitch, "velocity": n.velocity}
            for n in result["notes"]
        ],
    }, indent=2), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", required=True)
    p.add_argument("--item", required=True, help="manifest item id, e.g. center")
    p.add_argument("--sheet", required=True, help=".musicxml / .mxl / .mid score file")
    p.add_argument("--transpose", default="auto", help="'auto' or a semitone count")
    p.add_argument("--method", default="mrmsdtw", choices=("mrmsdtw", "chroma"),
                   help="mrmsdtw = synctoolbox MrMsDTW + DLNCO (default, far more accurate); "
                        "chroma = the plain chroma-DTW fallback")
    args = p.parse_args(argv)

    run_dir = Path(args.run)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    item = next(i for i in manifest["items"] if i["id"] == args.item)

    notes, kind = load_score(Path(args.sheet))
    if not notes:
        print(f"no notes found in {args.sheet}", file=sys.stderr)
        return 2
    print(f"score: {len(notes)} notes ({kind}), span {max(n.t_off for n in notes):.1f}s")

    if args.method == "mrmsdtw":
        result = align_mrmsdtw(Path(item["wav"]), notes, transpose=args.transpose)
    else:
        result = align(Path(item["wav"]), notes, transpose=args.transpose)
    out = write_reference(run_dir, args.item, result, {
        "source": str(Path(args.sheet)), "source_kind": kind, "note_count": len(result["notes"]),
    })
    print(f"method {result.get('method', 'chroma')}, transpose {result['transpose_semitones']:+d} "
          f"semitones, residual lag {result.get('residual_lag_sec', 0.0):+.3f}s")
    print(f"warped {len(result['notes'])} notes onto {result['audio_duration_sec']:.1f}s of audio")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
