from __future__ import annotations

from collections import Counter
import math
from pathlib import Path
from typing import Any

from aural_ingest.algorithms import aural_onset, dsp_bandpass_improved, dsp_spectral_flux
from aural_ingest.algorithms._common import (
    DrumCandidate,
    TranscriptionAlgorithm,
    candidates_to_events,
    clamp,
    fallback_events_from_classes,
    merge_candidate_clusters,
    preprocess_audio,
    timbral_features,
)
from aural_ingest.transcription import DrumEvent


class CombinedFilterAlgorithm(TranscriptionAlgorithm):
    name = "combined_filter"

    def transcribe(self, stem_path: Path) -> list[DrumEvent]:
        candidates, debug = _detect_candidates(stem_path, collect_debug=False)
        if candidates:
            events = candidates_to_events(candidates, stem_path=stem_path)
            if events:
                return events

        if debug.get("raw_candidate_count", 0) > 0:
            return []

        return fallback_events_from_classes(
            stem_path,
            ["kick", "snare", "tom_floor", "hh_closed", "hh_open", "tom_low", "crash", "tom_high", "ride"],
            step_sec=0.07,
            velocity_base=88,
        )


def _source_weight(source: str) -> float:
    if source == "dsp_bandpass_improved":
        return 1.0
    if source == "dsp_spectral_flux":
        return 0.8
    if source == "aural_onset":
        return 0.6
    return 0.5


def _fallback_candidate(cluster: list[DrumCandidate]) -> DrumCandidate:
    # Prefer stronger base detectors before heuristic support detector.
    ordered = sorted(
        cluster,
        key=lambda c: (
            1 if c.source == "dsp_bandpass_improved" else 0,
            1 if c.source == "dsp_spectral_flux" else 0,
            c.confidence,
            c.strength,
        ),
        reverse=True,
    )
    return ordered[0]


def _class_floor(drum_class: str) -> float:
    floors = {
        "hh_closed": 0.24,
        "hh_open": 0.32,
        "crash": 0.36,
        "ride": 0.36,
        "tom_high": 0.34,
        "tom_low": 0.34,
        "tom_floor": 0.36,
    }
    return floors.get(drum_class, 0.25)


def _stem_unanimous_classes(all_candidates: list[DrumCandidate]) -> dict[str, str]:
    """Return source -> class for sources whose candidates across the entire
    stem are class-unanimous and large enough to trust as signal.

    Implements the unanimous-detector boost path from
    docs/research-deep-dive-adt-2026-05-07.md (path 6). When one source
    detector emits the same drum class across all of its detections in a
    stem (e.g. aural_onset emits only kicks on a kick-only fixture), that
    is strong evidence the source is reliable for this stem. We use the
    information to boost its weight inside the cluster vote so it can
    overrule loud-but-noisy detectors that disagree on class.
    """

    by_source: dict[str, list[str]] = {}
    for candidate in all_candidates:
        by_source.setdefault(candidate.source, []).append(candidate.drum_class)

    unanimous: dict[str, str] = {}
    for source, classes in by_source.items():
        if len(classes) >= 4 and len(set(classes)) == 1:
            unanimous[source] = classes[0]
    return unanimous


def _effective_source_weight(
    candidate: DrumCandidate,
    base_weight: float,
    unanimous_classes: dict[str, str],
) -> float:
    """If this candidate's source is stem-unanimous and its class matches
    the source's unanimous class, boost the weight so the unanimous
    detector can overrule a loud-but-misclassifying detector."""

    if unanimous_classes.get(candidate.source) == candidate.drum_class:
        return base_weight * 1.8
    return base_weight


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
    """Measure whether this time has a local attack, not merely residual tail.

    The child detectors can fire on slowly modulated separator residue. A real
    drum hit should show some local post-vs-pre energy rise, a strong local
    peak, or enough sharpness to justify accepting the candidate.
    """

    pre = _window(samples, sr, time_sec - 0.05, time_sec - 0.008)
    post = _window(samples, sr, time_sec, time_sec + 0.035)
    pre_rms = _rms(pre)
    post_rms = _rms(post)
    peak = max((abs(x) for x in post), default=0.0)
    ratio = post_rms / max(pre_rms, 1e-6)
    rise_db = 20.0 * math.log10(max(post_rms, 1e-9) / max(pre_rms, 1e-9))
    return {
        "pre_rms": pre_rms,
        "post_rms": post_rms,
        "post_peak": peak,
        "rise_ratio": ratio,
        "rise_db": rise_db,
    }


def _class_activity(features: dict[str, float], drum_class: str) -> float:
    if drum_class == "kick":
        return max(features.get("sub", 0.0), features.get("low", 0.0))
    if drum_class == "snare":
        return max(features.get("mid", 0.0), features.get("snare_crack", 0.0))
    if drum_class in {"hh_closed", "hh_open", "crash", "ride"}:
        return max(features.get("high", 0.0), features.get("air", 0.0))
    if drum_class in {"tom_high", "tom_low", "tom_floor"}:
        return max(features.get("low", 0.0), features.get("mid", 0.0))
    return features.get("rms", 0.0)


def _weak_onset_reject_reason(
    drum_class: str,
    features: dict[str, float] | None,
    onset: dict[str, float] | None,
) -> str | None:
    if features is None or onset is None:
        return None

    activity = _class_activity(features, drum_class)
    # Use the short post-onset window for body/peak tests. `timbral_features`
    # intentionally looks 100 ms forward, which can include the next real hit
    # and falsely validate a pre-hit residual candidate.
    rms = onset.get("post_rms", features.get("rms", 0.0))
    peak = onset.get("post_peak", features.get("peak", 0.0))
    sharpness = features.get("sharpness", 0.0)
    rise_ratio = onset.get("rise_ratio", 0.0)
    rise_db = onset.get("rise_db", 0.0)

    if peak < 0.015 and rms < 0.006 and activity < 0.0015:
        return "no_local_hit_body"

    # Kick is the rhythmic backbone and can be low-frequency with modest
    # high-band sharpness, so keep the stricter attack check focused on
    # classes most prone to Psalm 19-style residual/tail false positives.
    guarded_classes = {"snare", "hh_open", "crash", "ride", "tom_high", "tom_low", "tom_floor"}
    if drum_class not in guarded_classes:
        return None

    has_attack = rise_ratio >= 1.45 or rise_db >= 3.2 or sharpness >= 2.25
    has_body = peak >= 0.045 or rms >= 0.014
    has_class_band = activity >= 0.0018

    if drum_class in {"crash", "ride", "hh_open"}:
        has_body = peak >= 0.055 or rms >= 0.016
        has_class_band = activity >= 0.0024
        if peak < 0.03 and rms < 0.012:
            return "weak_cymbal_body"

    if not has_class_band:
        return "weak_class_band"
    if not has_attack and not has_body:
        return "weak_onset_evidence"
    if drum_class in {"crash", "ride"} and rise_db < 1.0 and peak < 0.08:
        return "cymbal_tail_without_attack"
    return None


def _candidate_summary(candidate: DrumCandidate) -> dict[str, Any]:
    return {
        "time": round(candidate.time, 6),
        "class": candidate.drum_class,
        "strength": round(float(candidate.strength), 6),
        "confidence": round(float(candidate.confidence), 6),
        "source": candidate.source,
    }


def _count_by_class(candidates: list[DrumCandidate]) -> dict[str, int]:
    return dict(Counter(c.drum_class for c in candidates))


def detect_candidates(stem_path: Path) -> list[DrumCandidate]:
    candidates, _debug = _detect_candidates(stem_path, collect_debug=False)
    return candidates


def detect_candidates_with_debug(stem_path: Path) -> tuple[list[DrumCandidate], dict[str, Any]]:
    return _detect_candidates(stem_path, collect_debug=True)


def _detect_candidates(stem_path: Path, *, collect_debug: bool) -> tuple[list[DrumCandidate], dict[str, Any]]:
    base_a = dsp_bandpass_improved.detect_candidates(stem_path)
    base_b = dsp_spectral_flux.detect_candidates(stem_path)
    support = aural_onset.detect_candidates(stem_path)

    all_candidates = [*base_a, *base_b, *support]
    debug: dict[str, Any] = {
        "raw_candidate_count": len(all_candidates),
        "detectors": {
            "dsp_bandpass_improved": {
                "count": len(base_a),
                "classes": _count_by_class(base_a),
            },
            "dsp_spectral_flux": {
                "count": len(base_b),
                "classes": _count_by_class(base_b),
            },
            "aural_onset": {
                "count": len(support),
                "classes": _count_by_class(support),
            },
        },
        "decisions": [],
    }
    if not all_candidates:
        return [], debug

    samples, sr = preprocess_audio(
        stem_path,
        target_sr=44_100,
        pre_emphasis_coeff=0.92,
        high_pass_hz=35.0,
    )

    unanimous_classes = _stem_unanimous_classes(all_candidates)
    clusters = merge_candidate_clusters(all_candidates, window_sec=0.03)
    fused: list[DrumCandidate] = []

    for cluster in clusters:
        if not cluster:
            continue

        class_scores: dict[str, float] = {}
        total_vote = 0.0
        weighted_time_acc = 0.0
        weighted_time_w = 0.0
        feat: dict[str, float] | None = None
        onset: dict[str, float] | None = None

        for c in cluster:
            base_w = _source_weight(c.source)
            w = _effective_source_weight(c, base_w, unanimous_classes)
            vote = w * clamp(c.confidence, 0.0, 1.0)
            class_scores[c.drum_class] = class_scores.get(c.drum_class, 0.0) + vote
            total_vote += vote

            tw = max(1e-6, c.confidence * w)
            weighted_time_acc += c.time * tw
            weighted_time_w += tw

        cluster_time = (
            weighted_time_acc / weighted_time_w
            if weighted_time_w > 0.0
            else sum(c.time for c in cluster) / float(len(cluster))
        )

        if samples and sr > 0:
            feat = timbral_features(samples, sr, cluster_time)
            onset = _onset_context(samples, sr, cluster_time)
            low = feat["low"]
            mid = feat["mid"] + feat["snare_crack"]
            high = feat["high"] + (0.7 * feat["air"])
            total = max(1e-9, low + mid + high)

            low_dom = low / total
            snare_dom = feat["snare_crack"] / max(1e-9, total)
            high_dom = high / total
            high_decay = feat["high_decay"]

            class_scores["kick"] = class_scores.get("kick", 0.0) + (0.28 * low_dom)
            class_scores["snare"] = class_scores.get("snare", 0.0) + (0.24 * snare_dom)

            cym_boost = (0.24 * high_dom) + (0.22 * clamp(high_decay, 0.0, 1.0))
            class_scores["hh_closed"] = class_scores.get("hh_closed", 0.0) + (0.08 * high_dom)
            class_scores["hh_open"] = class_scores.get("hh_open", 0.0) + (0.13 * high_dom)
            class_scores["crash"] = class_scores.get("crash", 0.0) + cym_boost
            class_scores["ride"] = class_scores.get("ride", 0.0) + (0.15 * high_dom)
            if low_dom > 0.42 and snare_dom < 0.2:
                class_scores["tom_floor"] = class_scores.get("tom_floor", 0.0) + 0.08

        ranked = sorted(class_scores.items(), key=lambda kv: kv[1], reverse=True)
        if not ranked:
            continue

        top_class, top_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = top_score - second_score

        fallback = _fallback_candidate(cluster)
        selected_class = top_class
        kick_attack_correction = False

        support_sources = {c.source for c in cluster if c.drum_class == top_class}
        required_margin = 0.08 if len(support_sources) >= 2 else 0.12
        if margin < required_margin:
            selected_class = fallback.drum_class

        if top_score < _class_floor(selected_class):
            selected_class = fallback.drum_class
            top_score = class_scores.get(selected_class, fallback.confidence)

        # Keep the expanded-kit classes conservative to avoid noise bursts.
        if selected_class in {"hh_open", "crash", "ride", "tom_high", "tom_low", "tom_floor"}:
            if top_score < _class_floor(selected_class):
                selected_class = fallback.drum_class

        if feat is not None:
            # Low-band-energy guard. A bass-dominant onset cannot be a
            # high-frequency cymbal class. This addresses the failure mode
            # where dsp_bandpass_improved over-emits crash candidates on
            # bass-heavy hits and (with weight 1.0) outvotes the
            # correctly-kick-classified aural_onset detections (weight 0.6).
            # See docs/research-deep-dive-adt-2026-05-07.md, path 5.
            if low_dom > 0.55 and selected_class in {"crash", "ride", "hh_open"}:
                if class_scores.get("kick", 0.0) > 0.0:
                    selected_class = "kick"
                elif class_scores.get("tom_floor", 0.0) >= class_scores.get("tom_low", 0.0):
                    selected_class = "tom_floor"
                else:
                    selected_class = "tom_low"

            # Broadband kick attacks often contain enough high-frequency
            # noise to fool the high-band detectors into crash/open-hat. If
            # the onset is sharp, has meaningful sub support, and the high
            # energy decays quickly, treat it as a kick attack rather than a
            # cymbal. This targets the recognizer error directly: the child
            # detectors see a real onset, but the fusion class is wrong.
            if (
                selected_class in {"crash", "ride", "hh_open", "hh_closed"}
                and onset is not None
                and feat["sub"] >= 0.004
                and feat["sub"] >= feat["high"] * 0.42
                and feat["high_decay"] < 0.30
                and onset.get("rise_db", 0.0) >= 6.0
            ):
                selected_class = "kick"
                kick_attack_correction = True

            if selected_class == "hh_closed" and feat["high_decay"] > 0.24:
                selected_class = "hh_open"
            elif selected_class == "snare" and feat["high"] > feat["mid"] * 1.18 and feat["high_decay"] > 0.42:
                selected_class = "ride" if feat["centroid"] < 9000.0 else "crash"
            # (Dropped) The previous `kick + centroid > 520 -> tom_floor`
            # rule was a stale spectral-centroid heuristic that demoted
            # real kicks whose attack transient pushed centroid above
            # 520 Hz. The 2018 Wu/Lerch ADT survey and the 2026
            # Towards-Realistic-Synthetic-Data paper both flag hand-tuned
            # centroid thresholds as unreliable on real recordings.

        chosen_strength = max((c.strength for c in cluster if c.drum_class == selected_class), default=fallback.strength)
        confidence = clamp(top_score / max(1e-9, total_vote + 0.25), 0.0, 1.0)

        reject_reason = _weak_onset_reject_reason(selected_class, feat, onset)
        decision: dict[str, Any] | None = None
        if collect_debug:
            decision = {
                "time": round(cluster_time, 6),
                "cluster": [_candidate_summary(c) for c in cluster],
                "class_scores": {
                    key: round(float(value), 6)
                    for key, value in sorted(class_scores.items(), key=lambda item: item[1], reverse=True)
                },
                "top_class": top_class,
                "selected_class": selected_class,
                "fallback_class": fallback.drum_class,
                "margin": round(float(margin), 6),
                "features": {
                    key: round(float(value), 6)
                    for key, value in (feat or {}).items()
                },
                "onset": {
                    key: round(float(value), 6)
                    for key, value in (onset or {}).items()
                },
                "emitted": reject_reason is None,
                "reject_reason": reject_reason,
                "runner_up": None,
            }
            debug["decisions"].append(decision)

        if reject_reason is None:
            fused.append(
                DrumCandidate(
                    time=cluster_time,
                    drum_class=selected_class,
                    strength=chosen_strength,
                    confidence=confidence,
                    source="combined_filter",
                )
            )

        # Multi-label overlapping-hit emitter (path 9 of
        # docs/research-deep-dive-adt-2026-05-07.md). When the second-
        # ranked class also has a high score from a different physical
        # mechanism (e.g. kick + crash on the same beat), emit it as a
        # second event rather than suppressing it. Heuristic precursor to
        # a true multi-label CRNN (ISMIR 2025 "Performance Limitations in
        # ADT" reports simultaneous-hit handling as the dominant
        # performance constraint). Guards: only fires when the runner-up
        # class is from a *different* physical group than the selected
        # class (no kick+kick double emit), has its own dedicated
        # source-detector vote (not just a feat-based score boost), and
        # clears its own class floor.
        if len(ranked) >= 2:
            runner_up_class, runner_up_score = ranked[1]
            runner_up_reject_reason: str | None = None
            if (
                runner_up_score >= _class_floor(runner_up_class)
                and runner_up_score >= 0.7 * top_score
                and runner_up_class != selected_class
                and not _same_physical_group(selected_class, runner_up_class)
                and not (
                    kick_attack_correction
                    and runner_up_class in {"snare", "hh_closed", "hh_open", "crash", "ride"}
                )
            ):
                # Require a real source-detector vote for the runner-up
                # (not a synthesized feat-based boost) to avoid emitting
                # ghost cymbal events from broadband transient bleed.
                runner_up_sources = {
                    c.source
                    for c in cluster
                    if c.drum_class == runner_up_class and c.source != "combined_filter"
                }
                if runner_up_sources:
                    runner_up_reject_reason = _weak_onset_reject_reason(runner_up_class, feat, onset)
                    runner_up_strength = max(
                        (c.strength for c in cluster if c.drum_class == runner_up_class),
                        default=fallback.strength,
                    )
                    runner_up_conf = clamp(
                        runner_up_score / max(1e-9, total_vote + 0.25), 0.0, 1.0
                    )
                    if runner_up_reject_reason is None:
                        fused.append(
                            DrumCandidate(
                                time=cluster_time,
                                drum_class=runner_up_class,
                                strength=runner_up_strength,
                                confidence=runner_up_conf,
                                source="combined_filter",
                            )
                        )
                    if decision is not None:
                        decision["runner_up"] = {
                            "class": runner_up_class,
                            "score": round(float(runner_up_score), 6),
                            "sources": sorted(runner_up_sources),
                            "emitted": runner_up_reject_reason is None,
                            "reject_reason": runner_up_reject_reason,
                        }

    debug["fused_candidate_count"] = len(fused)
    debug["fused_classes"] = _count_by_class(fused)
    return fused, debug


_PHYSICAL_GROUPS: dict[str, str] = {
    "kick": "low_drum",
    "tom_floor": "low_drum",
    "tom_low": "low_drum",
    "snare": "snare",
    "tom_high": "tom",
    "hh_closed": "hat",
    "hh_open": "hat",
    "crash": "cymbal",
    "ride": "cymbal",
}


def _same_physical_group(class_a: str, class_b: str) -> bool:
    """True if two drum classes belong to the same physical drum group, so
    we don't emit two simultaneous events of the same family (e.g. would
    not co-emit kick + tom_floor since they are both low drums hit by
    different sticks but rarely simultaneously)."""

    return _PHYSICAL_GROUPS.get(class_a) == _PHYSICAL_GROUPS.get(class_b)


ALGORITHM = CombinedFilterAlgorithm()


def transcribe(stem_path: Path) -> list[DrumEvent]:
    return ALGORITHM.transcribe(stem_path)
