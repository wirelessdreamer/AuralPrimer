from __future__ import annotations

from pathlib import Path

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
        candidates = detect_candidates(stem_path)
        if candidates:
            events = candidates_to_events(candidates, stem_path=stem_path)
            if events:
                return events

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


def detect_candidates(stem_path: Path) -> list[DrumCandidate]:
    base_a = dsp_bandpass_improved.detect_candidates(stem_path)
    base_b = dsp_spectral_flux.detect_candidates(stem_path)
    support = aural_onset.detect_candidates(stem_path)

    all_candidates = [*base_a, *base_b, *support]
    if not all_candidates:
        return []

    samples, sr = preprocess_audio(
        stem_path,
        target_sr=44_100,
        pre_emphasis_coeff=0.92,
        high_pass_hz=35.0,
    )

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

        for c in cluster:
            w = _source_weight(c.source)
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
            if selected_class == "hh_closed" and feat["high_decay"] > 0.24:
                selected_class = "hh_open"
            elif selected_class == "snare" and feat["high"] > feat["mid"] * 1.18 and feat["high_decay"] > 0.42:
                selected_class = "ride" if feat["centroid"] < 9000.0 else "crash"
            elif selected_class == "kick" and feat["low"] > feat["snare_crack"] * 1.4 and feat["centroid"] > 520.0:
                selected_class = "tom_floor"

        chosen_strength = max((c.strength for c in cluster if c.drum_class == selected_class), default=fallback.strength)
        confidence = clamp(top_score / max(1e-9, total_vote + 0.25), 0.0, 1.0)

        fused.append(
            DrumCandidate(
                time=cluster_time,
                drum_class=selected_class,
                strength=chosen_strength,
                confidence=confidence,
                source="combined_filter",
            )
        )

    return fused


ALGORITHM = CombinedFilterAlgorithm()


def transcribe(stem_path: Path) -> list[DrumEvent]:
    return ALGORITHM.transcribe(stem_path)
