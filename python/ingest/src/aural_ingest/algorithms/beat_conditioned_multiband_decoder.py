from __future__ import annotations

from collections import Counter
import math
from pathlib import Path
from typing import Any

from aural_ingest.algorithms import adaptive_beat_grid, aural_onset
from aural_ingest.algorithms._common import (
    DrumCandidate,
    TranscriptionAlgorithm,
    adaptive_peak_pick,
    candidates_to_events,
    classify_hat_or_cymbal,
    classify_tom,
    clamp,
    compute_band_envelopes,
    estimate_tempo_from_onset_env,
    fallback_events_from_classes,
    frame_to_time,
    merge_candidate_clusters,
    normalize_series,
    onset_novelty,
    preprocess_audio,
    time_to_frame,
    timbral_features,
)
from aural_ingest.transcription import DrumEvent


class BeatConditionedMultibandDecoderAlgorithm(TranscriptionAlgorithm):
    name = "beat_conditioned_multiband_decoder"

    def transcribe(self, stem_path: Path) -> list[DrumEvent]:
        candidates, debug = _detect_candidates(stem_path, collect_debug=False)
        if candidates:
            events = candidates_to_events(candidates, stem_path=stem_path)
            if events:
                return events

        if debug.get("raw_seed_count", 0) > 0:
            return []

        return fallback_events_from_classes(
            stem_path,
            ["kick", "hh_closed", "snare", "hh_closed", "kick", "hh_open", "crash", "tom_low"],
            step_sec=0.082,
            velocity_base=87,
        )


def _source_weight(source: str, bucket: str) -> float:
    if source == "adaptive_beat_grid":
        return 1.0 if bucket == "kick" else 0.9
    if source == "aural_onset":
        return 0.98 if bucket == "snare" else 0.86
    if source == "hybrid_hat_peak":
        return 0.9 if bucket in {"hi_hat", "cymbal"} else 0.48
    if source == "hybrid_main_peak":
        return 0.72
    return 0.6


def _support_bucket(drum_class: str) -> str:
    if drum_class == "kick":
        return "kick"
    if drum_class == "snare":
        return "snare"
    if drum_class in {"hh_closed", "hh_open"}:
        return "hi_hat"
    if drum_class in {"crash", "ride"}:
        return "cymbal"
    if drum_class in {"tom_high", "tom_low", "tom_floor"}:
        return "tom"
    return "other"


def _choose_grid_step(period_sec: float, onset_count: int, duration_sec: float, confidence: float) -> float:
    if period_sec <= 0.0:
        return 0.25

    beats = max(1.0, duration_sec / period_sec)
    density = onset_count / beats
    if confidence >= 0.2 and density >= 1.8:
        return period_sec / 4.0
    if density >= 0.95:
        return period_sec / 2.0
    return period_sec


def _grid_alignment(time_sec: float, step: float) -> tuple[float, float]:
    if step <= 0.0:
        return time_sec, 0.0

    idx = round(time_sec / step)
    snapped = idx * step
    distance = abs(time_sec - snapped)
    tolerance = min(0.03, step * 0.22)
    if tolerance <= 0.0:
        return time_sec, 0.0

    align = clamp(1.0 - (distance / tolerance), 0.0, 1.0)
    if distance <= tolerance:
        # Preserve most microtiming while still borrowing a small rhythmic prior.
        return (time_sec * 0.82) + (snapped * 0.18), align
    return time_sec, 0.0


def _local_value(values: list[float], idx: int, radius: int = 1) -> float:
    if not values:
        return 0.0
    start = max(0, idx - radius)
    end = min(len(values), idx + radius + 1)
    if end <= start:
        return 0.0
    return max(values[start:end])


def _rough_peak_class(*, low: float, snare: float, high: float, cym: float) -> str:
    if high >= max(low, snare) * 1.04 and high >= cym * 0.92:
        return "hh_closed"
    if cym >= max(low, snare) * 1.08:
        return "crash"
    if snare >= low * 0.94:
        return "snare"
    return "kick"


def _weighted_time(cluster: list[DrumCandidate]) -> float:
    total = 0.0
    acc = 0.0
    for cand in cluster:
        bucket = _support_bucket(cand.drum_class)
        weight = _source_weight(cand.source, bucket) * max(0.08, (0.58 * cand.confidence) + (0.42 * cand.strength))
        acc += cand.time * weight
        total += weight
    if total <= 0.0:
        return sum(c.time for c in cluster) / float(len(cluster))
    return acc / total


def _bucket_votes(cluster: list[DrumCandidate]) -> dict[str, float]:
    votes = {"kick": 0.0, "snare": 0.0, "hi_hat": 0.0, "cymbal": 0.0, "tom": 0.0}
    for cand in cluster:
        bucket = _support_bucket(cand.drum_class)
        if bucket not in votes:
            continue
        votes[bucket] += _source_weight(cand.source, bucket) * (
            (0.56 * cand.confidence) + (0.44 * cand.strength)
        )

    total = sum(votes.values())
    if total <= 1e-9:
        return votes
    return {bucket: value / total for bucket, value in votes.items()}


def _bucket_strength(cluster: list[DrumCandidate], bucket: str) -> float:
    matched = [cand.strength for cand in cluster if _support_bucket(cand.drum_class) == bucket]
    return max(matched, default=0.0)


def _should_emit_secondary_core(
    *,
    primary_bucket: str,
    primary_score: float,
    secondary_bucket: str,
    secondary_score: float,
    votes: dict[str, float],
    low_hit: float,
    snare_hit: float,
    low_dom: float,
    snare_dom: float,
    high_dom: float,
    sharp: float,
    zcr: float,
) -> bool:
    if primary_bucket == secondary_bucket:
        return False
    if primary_bucket not in {"kick", "snare"} or secondary_bucket not in {"kick", "snare"}:
        return False
    if secondary_score < 0.33:
        return False
    if (primary_score - secondary_score) > 0.11:
        return False
    if high_dom >= 0.32:
        return False

    if secondary_bucket == "kick":
        return votes["kick"] >= 0.18 and low_hit >= 0.24 and low_dom >= 0.18 and sharp >= 0.12
    return votes["snare"] >= 0.18 and snare_hit >= 0.24 and snare_dom >= 0.16 and zcr >= 0.18


def _should_emit_hat_overlay(
    *,
    emit_core: bool,
    hat_conf: float,
    core_score: float,
    votes: dict[str, float],
    high_hit: float,
    high_dom: float,
) -> bool:
    if not emit_core:
        return True
    if (hat_conf >= core_score * 0.62) or (votes["hi_hat"] >= 0.22):
        return True
    # Real drum grooves often have kick+hat or snare+hat at the same onset.
    return votes["hi_hat"] >= 0.16 and high_hit >= 0.2 and high_dom >= 0.12 and hat_conf >= 0.28


def _emit_candidate(
    out: list[DrumCandidate],
    *,
    time_sec: float,
    drum_class: str,
    score: float,
    strength: float,
) -> None:
    out.append(
        DrumCandidate(
            time=max(0.0, float(time_sec)),
            drum_class=drum_class,
            strength=clamp(max(strength, score), 0.0, 1.0),
            confidence=clamp(score, 0.0, 1.0),
            source="beat_conditioned_multiband_decoder",
        )
    )


def _rms(samples: list[float]) -> float:
    if not samples:
        return 0.0
    return (sum(x * x for x in samples) / float(len(samples))) ** 0.5


def _window(samples: list[float], sr: int, start_sec: float, end_sec: float) -> list[float]:
    if not samples or sr <= 0:
        return []
    start = max(0, int(round(start_sec * sr)))
    end = min(len(samples), int(round(end_sec * sr)))
    if end <= start:
        return []
    return samples[start:end]


def _onset_context(samples: list[float], sr: int, time_sec: float) -> dict[str, float]:
    pre = _window(samples, sr, time_sec - 0.05, time_sec - 0.008)
    post = _window(samples, sr, time_sec, time_sec + 0.035)
    pre_rms = _rms(pre)
    post_rms = _rms(post)
    post_peak = max((abs(x) for x in post), default=0.0)
    rise_ratio = post_rms / max(pre_rms, 1e-6)
    rise_db = 20.0 * math.log10(max(post_rms, 1e-9) / max(pre_rms, 1e-9))
    return {
        "pre_rms": pre_rms,
        "post_rms": post_rms,
        "post_peak": post_peak,
        "rise_ratio": rise_ratio,
        "rise_db": rise_db,
    }


def _candidate_summary(candidate: DrumCandidate) -> dict[str, Any]:
    return {
        "time": round(float(candidate.time), 6),
        "class": candidate.drum_class,
        "strength": round(float(candidate.strength), 6),
        "confidence": round(float(candidate.confidence), 6),
        "source": candidate.source,
    }


def _count_by_class(candidates: list[DrumCandidate]) -> dict[str, int]:
    return dict(Counter(c.drum_class for c in candidates))


def _weak_decoder_reject_reason(
    drum_class: str,
    *,
    onset: dict[str, float],
    feat: dict[str, float],
    support_strength: float,
    bucket_score: float,
    novelty_hit: float,
) -> str | None:
    post_rms = onset.get("post_rms", 0.0)
    post_peak = onset.get("post_peak", 0.0)
    rise_db = onset.get("rise_db", 0.0)

    if post_peak < 0.004 and post_rms < 0.0015:
        return "no_local_drum_body"

    if post_peak < 0.012 and post_rms < 0.003 and support_strength < 0.16:
        return "no_local_drum_body"

    # Piano/guitar leakage into a drum stem often has relative high-band
    # dominance and sharpness, so the decoder must require absolute local
    # body before turning a weak high-band peak into a gameplay hat note.
    if drum_class in {"hh_open", "crash", "ride"}:
        if post_peak < 0.018 and post_rms < 0.006:
            return "weak_hat_body"
        if post_peak < 0.055 and post_rms < 0.012 and support_strength < 0.20:
            return "weak_hat_body"
        if rise_db < 1.0 and post_peak < 0.08 and bucket_score < 0.52:
            return "hat_tail_without_attack"

    if drum_class == "hh_closed":
        if post_peak < 0.018 and post_rms < 0.006:
            return "weak_hat_body"
        if post_peak < 0.035 and post_rms < 0.008 and support_strength < 0.20:
            return "weak_hat_body"

    if drum_class == "snare":
        if post_peak < 0.018 and post_rms < 0.006:
            return "weak_snare_body"
        if post_peak < 0.040 and post_rms < 0.010 and support_strength < 0.18:
            return "weak_snare_body"
        if novelty_hit < 0.02 and post_peak < 0.055 and feat.get("rms", 0.0) > post_rms * 4.0:
            return "snare_forward_window_bleed"

    if drum_class in {"tom_high", "tom_low", "tom_floor"}:
        if post_peak < 0.040 and post_rms < 0.010 and support_strength < 0.18:
            return "weak_tom_body"

    if drum_class == "kick":
        if post_peak < 0.006 and post_rms < 0.002:
            return "weak_kick_body"
        if post_peak < 0.020 and post_rms < 0.006 and support_strength < 0.16:
            return "weak_kick_body"

    return None


def detect_candidates(stem_path: Path) -> list[DrumCandidate]:
    candidates, _debug = _detect_candidates(stem_path, collect_debug=False)
    return candidates


def detect_candidates_with_debug(stem_path: Path) -> tuple[list[DrumCandidate], dict[str, Any]]:
    return _detect_candidates(stem_path, collect_debug=True)


def _detect_candidates(stem_path: Path, *, collect_debug: bool) -> tuple[list[DrumCandidate], dict[str, Any]]:
    support_a = aural_onset.detect_candidates(stem_path)
    support_b = adaptive_beat_grid.detect_candidates(stem_path)
    debug: dict[str, Any] = {
        "detectors": {
            "aural_onset": {"count": len(support_a), "classes": _count_by_class(support_a)},
            "adaptive_beat_grid": {"count": len(support_b), "classes": _count_by_class(support_b)},
            "hybrid_main_peak": {"count": 0, "classes": {}},
            "hybrid_hat_peak": {"count": 0, "classes": {}},
        },
        "raw_seed_count": len(support_a) + len(support_b),
        "decisions": [],
    }

    samples, sr = preprocess_audio(
        stem_path,
        target_sr=44_100,
        pre_emphasis_coeff=0.94,
        high_pass_hz=35.0,
    )
    if not samples or sr <= 0:
        return [], debug

    hop = 320
    hop_sec = hop / float(sr)
    env = compute_band_envelopes(
        samples,
        sr,
        {
            "low": (35.0, 160.0),
            "sub": (35.0, 120.0),
            "mid": (160.0, 2200.0),
            "crack": (1800.0, 4200.0),
            "high": (5500.0, 12_000.0),
            "air": (7000.0, 15_000.0),
            "cym": (3500.0, 10_000.0),
        },
        hop_size=hop,
        frame_size=1024,
    )
    if not env:
        return [], debug

    low_env = normalize_series([(0.7 * a) + (0.3 * b) for a, b in zip(env["low"], env["sub"])])
    snare_env = normalize_series([(0.45 * a) + (1.0 * b) for a, b in zip(env["mid"], env["crack"])])
    high_env = normalize_series([(0.82 * a) + (0.18 * b) for a, b in zip(env["high"], env["air"])])
    cym_env = normalize_series([(0.58 * a) + (0.42 * b) for a, b in zip(env["cym"], env["air"])])

    n = min(len(low_env), len(snare_env), len(high_env), len(cym_env))
    if n < 4:
        return [], debug

    low_n = normalize_series(onset_novelty(low_env[:n]))
    snare_n = normalize_series(onset_novelty(snare_env[:n]))
    high_n = normalize_series(onset_novelty(high_env[:n]))
    cym_n = normalize_series(onset_novelty(cym_env[:n]))
    combined_n = normalize_series(
        [
            (0.33 * low_n[i]) + (0.31 * snare_n[i]) + (0.24 * high_n[i]) + (0.12 * cym_n[i])
            for i in range(n)
        ]
    )

    peaks_main = adaptive_peak_pick(
        combined_n,
        hop_sec=hop_sec,
        k=1.88,
        min_gap_sec=0.042,
        window_sec=0.32,
        percentile=0.76,
        density_boost=0.12,
    )
    peaks_hat = adaptive_peak_pick(
        high_n,
        hop_sec=hop_sec,
        k=1.46,
        min_gap_sec=0.036,
        window_sec=0.24,
        percentile=0.66,
        density_boost=0.08,
    )

    period_sec, beat_conf = estimate_tempo_from_onset_env(combined_n, hop_sec)
    duration_sec = len(samples) / float(sr)
    step = _choose_grid_step(period_sec, len(peaks_main) + len(peaks_hat), duration_sec, beat_conf)

    seed_candidates: list[DrumCandidate] = [*support_a, *support_b]
    hybrid_main_candidates: list[DrumCandidate] = []
    hybrid_hat_candidates: list[DrumCandidate] = []
    for idx, strength in peaks_main:
        candidate = DrumCandidate(
            time=frame_to_time(idx, hop, sr),
            drum_class=_rough_peak_class(
                low=_local_value(low_n, idx),
                snare=_local_value(snare_n, idx),
                high=_local_value(high_n, idx),
                cym=_local_value(cym_n, idx),
            ),
            strength=float(strength),
            confidence=clamp((0.35 + (0.65 * strength)), 0.0, 1.0),
            source="hybrid_main_peak",
        )
        hybrid_main_candidates.append(candidate)
        seed_candidates.append(candidate)
    for idx, strength in peaks_hat:
        candidate = DrumCandidate(
            time=frame_to_time(idx, hop, sr),
            drum_class="hh_closed",
            strength=float(strength),
            confidence=clamp((0.4 + (0.6 * strength)), 0.0, 1.0),
            source="hybrid_hat_peak",
        )
        hybrid_hat_candidates.append(candidate)
        seed_candidates.append(candidate)

    debug["detectors"]["hybrid_main_peak"] = {
        "count": len(hybrid_main_candidates),
        "classes": _count_by_class(hybrid_main_candidates),
    }
    debug["detectors"]["hybrid_hat_peak"] = {
        "count": len(hybrid_hat_candidates),
        "classes": _count_by_class(hybrid_hat_candidates),
    }
    debug["raw_seed_count"] = len(seed_candidates)

    if not seed_candidates:
        return [], debug

    clusters = merge_candidate_clusters(seed_candidates, window_sec=0.028)
    decoded: list[DrumCandidate] = []

    for cluster in clusters:
        if not cluster:
            continue

        raw_time = _weighted_time(cluster)
        refined_time, grid_align = _grid_alignment(raw_time, step if beat_conf >= 0.12 else 0.0)
        feat = timbral_features(samples, sr, refined_time)
        onset = _onset_context(samples, sr, refined_time)
        idx = max(0, min(n - 1, time_to_frame(raw_time, hop, sr)))

        votes = _bucket_votes(cluster)
        low_hit = _local_value(low_n, idx, radius=1)
        snare_hit = _local_value(snare_n, idx, radius=1)
        high_hit = _local_value(high_n, idx, radius=1)
        cym_hit = _local_value(cym_n, idx, radius=1)

        total_energy = max(
            1e-9,
            feat["low"] + feat["sub"] + feat["mid"] + feat["snare_crack"] + feat["high"] + feat["air"],
        )
        low_dom = clamp((feat["low"] + (0.55 * feat["sub"])) / total_energy, 0.0, 1.0)
        snare_dom = clamp((feat["snare_crack"] + (0.4 * feat["mid"])) / total_energy, 0.0, 1.0)
        high_dom = clamp((feat["high"] + (0.3 * feat["air"])) / total_energy, 0.0, 1.0)
        mid_dom = clamp(feat["mid"] / total_energy, 0.0, 1.0)
        high_decay = clamp(feat["high_decay"], 0.0, 1.0)
        sharp = clamp((feat["sharpness"] - 1.25) / 1.75, 0.0, 1.0)
        zcr = clamp((feat["zcr"] - 0.05) / 0.18, 0.0, 1.0)
        centroid = clamp((feat["centroid"] - 2500.0) / 8500.0, 0.0, 1.0)

        kick_score = (
            (0.21 * low_hit)
            + (0.13 * _local_value(low_env, idx))
            + (0.22 * low_dom)
            + (0.21 * votes["kick"])
            + (0.11 * grid_align)
            + (0.07 * sharp)
            - (0.08 * high_dom)
        )
        snare_score = (
            (0.22 * snare_hit)
            + (0.11 * _local_value(snare_env, idx))
            + (0.2 * snare_dom)
            + (0.19 * votes["snare"])
            + (0.1 * ((0.58 * zcr) + (0.42 * sharp)))
            + (0.05 * (1.0 - low_dom))
        )
        hat_score = (
            (0.26 * high_hit)
            + (0.14 * _local_value(high_env, idx))
            + (0.22 * high_dom)
            + (0.18 * votes["hi_hat"])
            + (0.11 * grid_align)
            + (0.08 * (1.0 - low_dom))
        )
        cym_score = (
            (0.14 * high_hit)
            + (0.14 * cym_hit)
            + (0.16 * high_dom)
            + (0.13 * votes["cymbal"])
            + (0.18 * high_decay)
            + (0.07 * centroid)
            + (0.06 * grid_align)
        )
        tom_score = (
            (0.15 * low_hit)
            + (0.13 * snare_hit)
            + (0.16 * votes["tom"])
            + (0.15 * mid_dom)
            + (0.12 * low_dom)
            - (0.08 * high_dom)
            - (0.05 * votes["hi_hat"])
        )

        if votes["kick"] > 0.24 and low_dom > 0.21:
            kick_score += 0.08
        if votes["snare"] > 0.2 and snare_dom > 0.18:
            snare_score += 0.08
        if votes["hi_hat"] > 0.18 and high_dom > 0.16:
            hat_score += 0.1
        if votes["kick"] > 0.12 and votes["hi_hat"] > 0.12:
            hat_score += 0.06
        if votes["snare"] > 0.12 and votes["hi_hat"] > 0.12:
            hat_score += 0.07
        if high_decay > 0.38 and high_dom > max(low_dom, snare_dom) * 0.88:
            cym_score += 0.08
        if low_dom > snare_dom * 1.12 and zcr < 0.48:
            kick_score += 0.05
        if snare_dom > low_dom * 1.04 and zcr > 0.22:
            snare_score += 0.05

        kick_score = clamp(kick_score, 0.0, 1.0)
        snare_score = clamp(snare_score, 0.0, 1.0)
        hat_score = clamp(hat_score, 0.0, 1.0)
        cym_score = clamp(cym_score, 0.0, 1.0)
        tom_score = clamp(tom_score, 0.0, 1.0)

        core_scores = {"kick": kick_score, "snare": snare_score, "tom": tom_score}
        core_ranking = sorted(core_scores.items(), key=lambda item: item[1], reverse=True)
        core_bucket, core_score = core_ranking[0]
        secondary_core_bucket, secondary_core_score = core_ranking[1]

        decision: dict[str, Any] | None = None
        if collect_debug:
            decision = {
                "time": round(float(refined_time), 6),
                "raw_time": round(float(raw_time), 6),
                "cluster": [_candidate_summary(c) for c in cluster],
                "votes": {key: round(float(value), 6) for key, value in votes.items()},
                "scores": {
                    "kick": round(float(kick_score), 6),
                    "snare": round(float(snare_score), 6),
                    "hat": round(float(hat_score), 6),
                    "cymbal": round(float(cym_score), 6),
                    "tom": round(float(tom_score), 6),
                },
                "features": {key: round(float(value), 6) for key, value in feat.items()},
                "onset": {key: round(float(value), 6) for key, value in onset.items()},
                "emitted": [],
                "rejected": [],
            }
            debug["decisions"].append(decision)

        def emit_with_validation(
            *,
            drum_class: str,
            bucket: str,
            score: float,
            strength: float,
            novelty_hit: float,
        ) -> None:
            reject_reason = _weak_decoder_reject_reason(
                drum_class,
                onset=onset,
                feat=feat,
                support_strength=_bucket_strength(cluster, bucket),
                bucket_score=score,
                novelty_hit=novelty_hit,
            )
            record = {
                "class": drum_class,
                "bucket": bucket,
                "score": round(float(score), 6),
                "strength": round(float(strength), 6),
                "reject_reason": reject_reason,
            }
            if reject_reason is None:
                _emit_candidate(
                    decoded,
                    time_sec=refined_time,
                    drum_class=drum_class,
                    score=score,
                    strength=strength,
                )
                if decision is not None:
                    decision["emitted"].append(record)
            elif decision is not None:
                decision["rejected"].append(record)

        emit_core = core_score >= 0.34 or max(votes["kick"], votes["snare"], votes["tom"]) >= 0.3
        if core_bucket == "kick" and low_dom < 0.16 and votes["kick"] < 0.18:
            emit_core = False
        if core_bucket == "snare" and snare_dom < 0.13 and votes["snare"] < 0.18:
            emit_core = False
        if core_bucket == "tom" and max(low_dom, mid_dom) < 0.17:
            emit_core = False

        if emit_core:
            if core_bucket == "tom":
                drum_class = classify_tom(feat)
                novelty_hit = max(low_hit, snare_hit)
            else:
                drum_class = core_bucket
                novelty_hit = low_hit if core_bucket == "kick" else snare_hit
            emit_with_validation(
                drum_class=drum_class,
                bucket=core_bucket,
                score=core_score,
                strength=max(_bucket_strength(cluster, core_bucket), core_score),
                novelty_hit=novelty_hit,
            )
            if _should_emit_secondary_core(
                primary_bucket=core_bucket,
                primary_score=core_score,
                secondary_bucket=secondary_core_bucket,
                secondary_score=secondary_core_score,
                votes=votes,
                low_hit=low_hit,
                snare_hit=snare_hit,
                low_dom=low_dom,
                snare_dom=snare_dom,
                high_dom=high_dom,
                sharp=sharp,
                zcr=zcr,
            ):
                emit_with_validation(
                    drum_class=secondary_core_bucket,
                    bucket=secondary_core_bucket,
                    score=secondary_core_score,
                    strength=max(_bucket_strength(cluster, secondary_core_bucket), secondary_core_score),
                    novelty_hit=low_hit if secondary_core_bucket == "kick" else snare_hit,
                )

        hat_family_score = max(hat_score, cym_score)
        emit_hat_family = hat_family_score >= 0.32 and high_dom >= 0.11
        if emit_hat_family:
            if cym_score >= max(0.46, hat_score + 0.08) and high_decay >= 0.38:
                hat_class = classify_hat_or_cymbal(
                    feat,
                    prefer_ride_when_on_grid=True,
                    on_grid=grid_align >= 0.65,
                )
                hat_strength = max(_bucket_strength(cluster, "cymbal"), cym_score)
                hat_conf = cym_score
                hat_bucket = "cymbal"
                novelty_hit = max(high_hit, cym_hit)
            else:
                hat_class = "hh_open" if high_decay >= 0.28 else "hh_closed"
                hat_strength = max(_bucket_strength(cluster, "hi_hat"), hat_score)
                hat_conf = hat_score
                hat_bucket = "hi_hat"
                novelty_hit = high_hit

            if _should_emit_hat_overlay(
                emit_core=emit_core,
                hat_conf=hat_conf,
                core_score=core_score,
                votes=votes,
                high_hit=high_hit,
                high_dom=high_dom,
            ):
                emit_with_validation(
                    drum_class=hat_class,
                    bucket=hat_bucket,
                    score=hat_conf,
                    strength=hat_strength,
                    novelty_hit=novelty_hit,
                )

    debug["fused_candidate_count"] = len(decoded)
    debug["fused_classes"] = _count_by_class(decoded)
    return decoded, debug


ALGORITHM = BeatConditionedMultibandDecoderAlgorithm()


def transcribe(stem_path: Path) -> list[DrumEvent]:
    return ALGORITHM.transcribe(stem_path)
