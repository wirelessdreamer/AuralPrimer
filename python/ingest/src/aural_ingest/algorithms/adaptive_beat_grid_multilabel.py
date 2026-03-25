"""Multi-label adaptive beat grid — allows simultaneous kick + hat emission.

The standard adaptive_beat_grid forces one class per onset (winner-take-all).
This variant detects concurrent kick + hat evidence and emits both events,
addressing the dominant failure mode where 123 of 233 missed kicks on Psalm 2
were caused by hi-hat winning over kick due to simultaneous playing.
"""
from __future__ import annotations

from pathlib import Path

from aural_ingest.algorithms._common import (
    CLASS_REFRACTORY_SEC,
    DrumCandidate,
    TranscriptionAlgorithm,
    adaptive_peak_pick,
    candidates_to_events,
    classify_hat_or_cymbal,
    clamp,
    compute_band_envelopes,
    estimate_tempo_from_onset_env,
    fallback_events_from_classes,
    frame_to_time,
    normalize_series,
    onset_novelty,
    preprocess_audio,
    snap_time_to_grid,
    timbral_features,
)
from aural_ingest.transcription import DrumEvent

# Import grid helpers from ABG
from aural_ingest.algorithms.adaptive_beat_grid import (
    _choose_grid_step,
    _estimate_dense_kick_grid,
    _local_peak,
    _merge_supplemental_peaks,
)


class AdaptiveBeatGridMultilabelAlgorithm(TranscriptionAlgorithm):
    name = "adaptive_beat_grid_multilabel"

    def transcribe(self, stem_path: Path) -> list[DrumEvent]:
        candidates = _detect_multilabel(stem_path)
        if candidates:
            events = candidates_to_events(candidates, stem_path=stem_path)
            if events:
                return events

        return fallback_events_from_classes(
            stem_path, ["kick", "snare", "hh_closed"], step_sec=0.11, velocity_base=84,
        )


def _detect_multilabel(stem_path: Path) -> list[DrumCandidate]:
    samples, sr = preprocess_audio(
        stem_path, target_sr=44_100, pre_emphasis_coeff=0.9, high_pass_hz=35.0,
    )
    if not samples or sr <= 0:
        return []

    hop = 384
    hop_sec = hop / float(sr)
    env = compute_band_envelopes(
        samples, sr,
        {"low": (35.0, 180.0), "mid": (180.0, 2500.0), "high": (2500.0, 12_000.0)},
        hop_size=hop,
    )
    if not env:
        return []

    low = normalize_series(env["low"])
    mid = normalize_series(env["mid"])
    high = normalize_series(env["high"])

    n = min(len(low), len(mid), len(high))
    if n < 3:
        return []

    low_n = normalize_series(onset_novelty(low))
    mid_n = normalize_series(onset_novelty(mid))
    high_n = normalize_series(onset_novelty(high))
    novelty = normalize_series(
        [(0.45 * low_n[i]) + (0.3 * mid_n[i]) + (0.25 * high_n[i]) for i in range(n)]
    )

    # Standard peak picking
    peaks = adaptive_peak_pick(
        novelty, hop_sec=hop_sec, k=2.1, min_gap_sec=0.05, window_sec=0.38, percentile=0.85,
    )
    if not peaks:
        return []

    # Supplemental low-band peaks
    peaks = _merge_supplemental_peaks(
        peaks, novelty,
        adaptive_peak_pick(low_n, hop_sec=hop_sec, k=2.0, min_gap_sec=0.05, window_sec=0.34, percentile=0.82),
        novelty_ratio=0.94, min_strength=0.14,
        min_gap_frames=max(1, int(round(0.04 / hop_sec))),
    )
    # Supplemental high-band peaks
    peaks = _merge_supplemental_peaks(
        peaks, novelty,
        adaptive_peak_pick(high_n, hop_sec=hop_sec, k=1.75, min_gap_sec=0.045, window_sec=0.28, percentile=0.8),
        novelty_ratio=0.88, min_strength=0.14,
        min_gap_frames=max(2, int(round(0.04 / hop_sec))),
    )

    beat_period, beat_conf = estimate_tempo_from_onset_env(novelty, hop_sec)
    duration_sec = len(samples) / float(sr)
    step = _choose_grid_step(beat_period, len(peaks), duration_sec, beat_conf)
    tolerance = 0.050 if beat_conf < 0.22 else 0.038
    dense_kick = _estimate_dense_kick_grid(low_n, hop_sec=hop_sec)
    kick_step = dense_kick[0] if dense_kick else None
    kick_anchor = dense_kick[1] if dense_kick else 0.0

    candidates: list[DrumCandidate] = []

    for idx, strength in peaks:
        t_raw = frame_to_time(idx, hop, sr)
        feat = timbral_features(samples, sr, t_raw)
        low_hit = _local_peak(low_n, idx, radius=1)
        mid_hit = _local_peak(mid_n, idx, radius=1)
        high_hit = _local_peak(high_n, idx, radius=1)

        total = max(1e-9, feat["sub"] + feat["low"] + feat["mid"] + feat["snare_crack"] + feat["high"] + feat["air"])
        low_dom = clamp((feat["sub"] + 0.8 * feat["low"]) / total, 0.0, 1.0)
        snare_dom = clamp((feat["mid"] + 0.9 * feat["snare_crack"]) / total, 0.0, 1.0)
        high_dom = clamp((feat["high"] + 0.7 * feat["air"]) / total, 0.0, 1.0)
        crack_ratio = clamp(feat["snare_crack"] / max(1e-9, feat["low"] + feat["high"]), 0.0, 1.0)

        kick_score = (0.58 * low_hit) + (0.42 * low_dom)
        snare_score = (0.46 * mid_hit) + (0.38 * snare_dom) + (0.16 * crack_ratio)
        hat_score = (0.62 * high_hit) + (0.38 * high_dom)

        if high_hit > max(low_hit, mid_hit) * 0.98 and high_dom > 0.16:
            hat_score += 0.08
        if low_hit > mid_hit * 1.08 and low_dom > 0.18:
            kick_score += 0.06
        if mid_hit > high_hit * 1.04 and snare_dom > 0.17:
            snare_score += 0.06
        if kick_step is not None and low_hit >= 0.16 and low_dom >= 0.2:
            nearest_grid = round((t_raw - kick_anchor) / kick_step)
            grid_t = kick_anchor + nearest_grid * kick_step
            if abs(t_raw - grid_t) <= max(0.018, kick_step * 0.28):
                kick_score += 0.08

        # Primary class selection (same as standard ABG)
        drum_class, winner = max(
            (("kick", kick_score), ("snare", snare_score), ("hh_closed", hat_score)),
            key=lambda item: item[1],
        )

        # Cross-band snare→kick reclassifier (same as standard ABG)
        if drum_class == "snare" and low_hit > 0.12:
            kick_to_mid_ratio = low_hit / max(1e-9, mid_hit)
            if kick_to_mid_ratio > 0.85 and low_dom > 0.20 and crack_ratio < 0.50:
                drum_class = "kick"
                winner = kick_score

        # Cross-band hat→kick reclassifier (same as standard ABG)
        if drum_class in ("hh_closed", "hh_open") and low_hit > 0.14:
            kick_to_high = low_hit / max(1e-9, high_hit)
            if kick_to_high > 0.65 and low_dom > 0.22:
                drum_class = "kick"
                winner = kick_score
        if drum_class in ("hh_closed", "hh_open"):
            hat_margin = hat_score - kick_score
            if hat_margin < 0.06 and low_hit > 0.10 and low_dom > 0.16:
                drum_class = "kick"
                winner = kick_score

        # ══════════════════════════════════════════════════════
        # MULTI-LABEL: Detect concurrent kick + hat events
        # ══════════════════════════════════════════════════════
        # When both kick and hat bands show independent onset evidence,
        # emit BOTH events instead of forcing a single winner.
        emit_concurrent_hat = False
        emit_concurrent_kick = False

        if drum_class == "kick":
            # Kick won — should we also emit a hat?
            # Yes if: hat band has independent strong evidence
            if high_hit >= 0.12 and high_dom >= 0.12 and hat_score >= 0.15:
                emit_concurrent_hat = True

        elif drum_class in ("hh_closed", "hh_open", "crash"):
            # Hat/crash won — should we also emit a kick?
            # Yes if: low band has independent strong evidence
            if low_hit >= 0.10 and low_dom >= 0.14 and kick_score >= 0.12:
                emit_concurrent_kick = True

        # Resolve hat sub-class
        if drum_class == "hh_closed":
            hat_class = classify_hat_or_cymbal(feat, prefer_ride_when_on_grid=False)
            if hat_class == "crash" and (high_hit < 0.22 or strength < 0.62):
                hat_class = "hh_open"
            drum_class = hat_class

        # Grid snap with safety cap
        snapped_t = snap_time_to_grid(t_raw, anchor=0.0, step=step, tolerance=tolerance)
        if abs(snapped_t - t_raw) > 0.055:
            snapped_t = t_raw
        if drum_class == "kick" and kick_step is not None:
            kick_tolerance = min(0.028, max(0.016, kick_step * 0.22))
            snapped_t = snap_time_to_grid(t_raw, anchor=kick_anchor, step=kick_step, tolerance=kick_tolerance)
            if abs(snapped_t - t_raw) > 0.055:
                snapped_t = t_raw

        confidence = clamp((strength * 0.6) + (winner * 0.4), 0.0, 1.0)
        if drum_class == "crash" and confidence < 0.74:
            drum_class = "hh_open"

        # Emit primary event
        candidates.append(DrumCandidate(
            time=snapped_t,
            drum_class=drum_class,
            strength=float(strength),
            confidence=confidence,
            source="adaptive_beat_grid_multilabel",
        ))

        # Emit concurrent events
        if emit_concurrent_hat and drum_class == "kick":
            hat_cls = classify_hat_or_cymbal(feat, prefer_ride_when_on_grid=False)
            if hat_cls == "crash" and (high_hit < 0.22 or strength < 0.62):
                hat_cls = "hh_open"
            hat_conf = clamp((strength * 0.4) + (hat_score * 0.4), 0.0, 1.0)
            candidates.append(DrumCandidate(
                time=snapped_t,
                drum_class=hat_cls,
                strength=float(strength) * 0.7,
                confidence=hat_conf,
                source="adaptive_beat_grid_multilabel_concurrent",
            ))

        if emit_concurrent_kick:
            kick_conf = clamp((strength * 0.4) + (kick_score * 0.4), 0.0, 1.0)
            kick_snapped = snapped_t
            if kick_step is not None:
                kick_tolerance = min(0.028, max(0.016, kick_step * 0.22))
                kick_snapped = snap_time_to_grid(t_raw, anchor=kick_anchor, step=kick_step, tolerance=kick_tolerance)
                if abs(kick_snapped - t_raw) > 0.055:
                    kick_snapped = t_raw
            candidates.append(DrumCandidate(
                time=kick_snapped,
                drum_class="kick",
                strength=float(strength) * 0.7,
                confidence=kick_conf,
                source="adaptive_beat_grid_multilabel_concurrent",
            ))

    return candidates


ALGORITHM = AdaptiveBeatGridMultilabelAlgorithm()


def transcribe(stem_path: Path) -> list[DrumEvent]:
    return ALGORITHM.transcribe(stem_path)
