from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from aural_ingest.transcription import DrumEvent


BENCHMARK_CLASS_ORDER: tuple[str, ...] = (
    "kick",
    "snare",
    "hi_hat",
    "crash",
    "ride",
    "tom1",
    "tom2",
    "tom3",
)

_DISPLAY_CLASS_NAMES: dict[str, str] = {
    "kick": "kick",
    "snare": "snare",
    "hi_hat": "hi-hat",
    "crash": "crash",
    "ride": "ride",
    "tom1": "tom1",
    "tom2": "tom2",
    "tom3": "tom3",
}

_CLASS_INDEX = {name: idx for idx, name in enumerate(BENCHMARK_CLASS_ORDER)}

_ALIAS_TO_CLASS: dict[str, str] = {
    "bd": "kick",
    "bass_drum": "kick",
    "kick": "kick",
    "kick_drum": "kick",
    "sd": "snare",
    "snare": "snare",
    "snare_drum": "snare",
    "hh": "hi_hat",
    "hh_closed": "hi_hat",
    "hh_open": "hi_hat",
    "hat": "hi_hat",
    "hat_closed": "hi_hat",
    "hat_open": "hi_hat",
    "hihat": "hi_hat",
    "hi_hat": "hi_hat",
    "closed_hat": "hi_hat",
    "open_hat": "hi_hat",
    "cy": "crash",
    "crash": "crash",
    "crash_cymbal": "crash",
    "cymbal": "crash",
    "rd": "ride",
    "ride": "ride",
    "ride_cymbal": "ride",
    "ft": "tom3",
    "floor_tom": "tom3",
    "ht": "tom1",
    "high_tom": "tom1",
    "lt": "tom2",
    "low_tom": "tom2",
    "mid_tom": "tom2",
    "rack_tom": "tom1",
    "tom": "tom2",
    "tom_1": "tom1",
    "tom_2": "tom2",
    "tom_3": "tom3",
    "tom_floor": "tom3",
    "tom_high": "tom1",
    "tom_low": "tom2",
    "tom_mid": "tom2",
    "tom1": "tom1",
    "tom2": "tom2",
    "tom3": "tom3",
}

_MIDI_TO_CLASS: dict[int, str] = {
    35: "kick",
    36: "kick",
    37: "snare",
    38: "snare",
    39: "snare",
    40: "snare",
    41: "tom3",
    42: "hi_hat",
    43: "tom3",
    44: "hi_hat",
    45: "tom2",
    46: "hi_hat",
    47: "tom2",
    48: "tom1",
    49: "crash",
    50: "tom1",
    51: "ride",
    52: "crash",
    53: "ride",
    55: "crash",
    57: "crash",
    59: "ride",
}


@dataclass(frozen=True)
class BenchmarkEvent:
    time: float
    drum_class: str


@dataclass(frozen=True)
class ClassMetrics:
    reference_count: int
    predicted_count: int
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    timing_mae_ms: float | None


@dataclass(frozen=True)
class _MidiNoteOn:
    tick: int
    channel: int
    note: int
    velocity: int
    track_index: int
    track_name: str | None


def normalize_drum_class(value: str | None) -> str | None:
    if value is None:
        return None
    token = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    if not token:
        return None
    return _ALIAS_TO_CLASS.get(token)


def normalize_drum_note(note: int | str | None) -> str | None:
    if note is None:
        return None
    try:
        return _MIDI_TO_CLASS.get(int(note))
    except Exception:
        return None


def normalize_transcribed_events(events: Iterable[DrumEvent]) -> tuple[list[BenchmarkEvent], int]:
    normalized: list[BenchmarkEvent] = []
    ignored = 0

    for ev in events:
        drum_class = normalize_drum_note(getattr(ev, "note", None))
        if drum_class is None:
            ignored += 1
            continue
        try:
            t = max(0.0, float(getattr(ev, "time", 0.0) or 0.0))
        except Exception:
            ignored += 1
            continue
        normalized.append(BenchmarkEvent(time=t, drum_class=drum_class))

    normalized.sort(key=lambda event: (event.time, _CLASS_INDEX[event.drum_class]))
    return normalized, ignored


def _coerce_time(item: Mapping[str, Any]) -> float | None:
    for key in ("t", "time", "t_on", "onset", "seconds"):
        if key not in item:
            continue
        try:
            return max(0.0, float(item[key]))
        except Exception:
            return None
    return None


def _coerce_note(item: Mapping[str, Any]) -> int | str | None:
    if "note" in item:
        return item["note"]
    pitch = item.get("pitch")
    if isinstance(pitch, Mapping):
        return pitch.get("value")
    return pitch


def _normalize_event_mapping(item: Mapping[str, Any]) -> BenchmarkEvent | None:
    t = _coerce_time(item)
    if t is None:
        return None

    drum_class = None
    for key in ("class", "drum_class", "label"):
        raw = item.get(key)
        if raw is None:
            continue
        drum_class = normalize_drum_class(str(raw))
        if drum_class is not None:
            break

    if drum_class is None:
        drum_class = normalize_drum_note(_coerce_note(item))
    if drum_class is None:
        return None
    return BenchmarkEvent(time=t, drum_class=drum_class)


def _extract_json_event_items(data: Any) -> tuple[list[Mapping[str, Any]], dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, Mapping)], {}
    if not isinstance(data, Mapping):
        raise ValueError("reference json must be an object or an array of onset objects")

    track_ids: set[str] = set()
    tracks = data.get("tracks")
    if isinstance(tracks, list):
        for track in tracks:
            if not isinstance(track, Mapping):
                continue
            role = str(track.get("role", "")).strip().lower()
            track_id = str(track.get("track_id", "")).strip()
            if role == "drums" and track_id:
                track_ids.add(track_id)

    events: list[Mapping[str, Any]] = []
    source_key = ""
    for candidate_key in ("onsets", "events", "notes"):
        raw = data.get(candidate_key)
        if isinstance(raw, list):
            events = [item for item in raw if isinstance(item, Mapping)]
            source_key = candidate_key
            break
    if not events:
        raise ValueError("reference json must contain onsets, events, or notes")

    if source_key == "onsets" and track_ids:
        filtered = []
        for item in events:
            track_id = str(item.get("track_id", "")).strip()
            if not track_id or track_id in track_ids:
                filtered.append(item)
        if filtered:
            events = filtered

    return events, {"selected_track_ids": sorted(track_ids), "json_source_key": source_key}


def _load_reference_json(reference_path: Path) -> tuple[list[BenchmarkEvent], dict[str, Any]]:
    data = json.loads(reference_path.read_text("utf-8"))
    items, meta = _extract_json_event_items(data)
    events: list[BenchmarkEvent] = []
    ignored = 0

    for item in items:
        event = _normalize_event_mapping(item)
        if event is None:
            ignored += 1
            continue
        events.append(event)

    events.sort(key=lambda event: (event.time, _CLASS_INDEX[event.drum_class]))
    return events, {
        "format": "json",
        "ignored_reference_events": ignored,
        **meta,
    }


def _read_vlq(data: bytes, pos: int) -> tuple[int, int]:
    if pos >= len(data):
        raise ValueError("unexpected end of midi data while reading vlq")

    value = 0
    for _ in range(4):
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not (byte & 0x80):
            return value, pos
    raise ValueError("invalid midi vlq")


def _parse_midi_note_ons(reference_path: Path) -> tuple[list[_MidiNoteOn], list[tuple[int, int]], int]:
    data = reference_path.read_bytes()
    if len(data) < 14 or data[0:4] != b"MThd":
        raise ValueError("reference midi missing MThd header")

    header_len = int.from_bytes(data[4:8], "big")
    if header_len < 6:
        raise ValueError("reference midi header too small")
    track_count = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big")
    if division & 0x8000:
        raise ValueError("smpte midi timing is not supported for drum benchmarks")

    pos = 8 + header_len
    note_ons: list[_MidiNoteOn] = []
    tempo_changes: list[tuple[int, int]] = [(0, 500_000)]

    for track_index in range(track_count):
        if pos + 8 > len(data) or data[pos : pos + 4] != b"MTrk":
            raise ValueError("reference midi missing MTrk chunk")
        track_len = int.from_bytes(data[pos + 4 : pos + 8], "big")
        pos += 8
        end = pos + track_len
        if end > len(data):
            raise ValueError("reference midi track chunk truncated")

        tick = 0
        running_status: int | None = None
        track_name: str | None = None

        while pos < end:
            delta, pos = _read_vlq(data, pos)
            tick += delta
            if pos >= end:
                break

            status_byte = data[pos]
            if status_byte & 0x80:
                pos += 1
                status = status_byte
                if status < 0xF0:
                    running_status = status
            else:
                if running_status is None:
                    raise ValueError("midi running status encountered without prior status")
                status = running_status

            if status == 0xFF:
                if pos >= end:
                    raise ValueError("truncated midi meta event")
                meta_type = data[pos]
                pos += 1
                length, pos = _read_vlq(data, pos)
                payload = data[pos : pos + length]
                pos += length
                if meta_type == 0x03:
                    track_name = payload.decode("utf-8", errors="replace").strip() or None
                elif meta_type == 0x51 and len(payload) == 3:
                    tempo_changes.append((tick, int.from_bytes(payload, "big")))
                elif meta_type == 0x2F:
                    break
                running_status = None
                continue

            if status in {0xF0, 0xF7}:
                length, pos = _read_vlq(data, pos)
                pos += length
                running_status = None
                continue

            event_type = status & 0xF0
            channel = status & 0x0F

            if status_byte & 0x80:
                if pos >= end:
                    raise ValueError("truncated midi channel event")
                data1 = data[pos]
                pos += 1
            else:
                data1 = status_byte

            if event_type in {0xC0, 0xD0}:
                continue

            if pos >= end:
                raise ValueError("truncated midi channel event")
            data2 = data[pos]
            pos += 1

            if event_type == 0x90 and data2 > 0:
                note_ons.append(
                    _MidiNoteOn(
                        tick=tick,
                        channel=channel,
                        note=int(data1),
                        velocity=int(data2),
                        track_index=track_index,
                        track_name=track_name,
                    )
                )

        pos = end

    return note_ons, tempo_changes, division


def _compress_tempo_changes(changes: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    ordered = sorted((int(tick), int(tempo)) for tick, tempo in changes if tempo > 0)
    if not ordered:
        return [(0, 500_000)]

    out: list[tuple[int, int]] = []
    for tick, tempo in ordered:
        if out and out[-1][0] == tick:
            out[-1] = (tick, tempo)
        else:
            out.append((tick, tempo))
    if out[0][0] != 0:
        out.insert(0, (0, 500_000))
    return out


def _tick_to_seconds(tick: int, tempo_changes: list[tuple[int, int]], ticks_per_quarter: int) -> float:
    if tick <= 0:
        return 0.0

    total_sec = 0.0
    prev_tick = 0
    prev_tempo = tempo_changes[0][1]

    for tempo_tick, tempo in tempo_changes[1:]:
        if tick <= tempo_tick:
            break
        delta_ticks = max(0, tempo_tick - prev_tick)
        total_sec += (float(delta_ticks) * float(prev_tempo)) / (1_000_000.0 * float(ticks_per_quarter))
        prev_tick = tempo_tick
        prev_tempo = tempo

    delta_ticks = max(0, tick - prev_tick)
    total_sec += (float(delta_ticks) * float(prev_tempo)) / (1_000_000.0 * float(ticks_per_quarter))
    return total_sec


def _load_reference_midi(reference_path: Path) -> tuple[list[BenchmarkEvent], dict[str, Any]]:
    note_ons, tempo_changes_raw, ticks_per_quarter = _parse_midi_note_ons(reference_path)

    strict = [
        event
        for event in note_ons
        if event.channel == 9 or ("drum" in (event.track_name or "").strip().lower())
    ]
    relaxed = [event for event in note_ons if normalize_drum_note(event.note) is not None]
    selected = strict if strict else relaxed
    tempo_changes = _compress_tempo_changes(tempo_changes_raw)

    events: list[BenchmarkEvent] = []
    ignored = 0
    for event in selected:
        drum_class = normalize_drum_note(event.note)
        if drum_class is None:
            ignored += 1
            continue
        events.append(
            BenchmarkEvent(
                time=_tick_to_seconds(event.tick, tempo_changes, ticks_per_quarter),
                drum_class=drum_class,
            )
        )

    events.sort(key=lambda event: (event.time, _CLASS_INDEX[event.drum_class]))
    return events, {
        "format": "midi",
        "ignored_reference_events": ignored,
        "selected_mode": "strict" if strict else "relaxed",
        "ticks_per_quarter": ticks_per_quarter,
    }


def load_drum_reference(reference_path: Path | str) -> tuple[list[BenchmarkEvent], dict[str, Any]]:
    path = Path(reference_path)
    suffix = path.suffix.lower()
    if suffix in {".mid", ".midi"}:
        return _load_reference_midi(path)
    return _load_reference_json(path)


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _build_metrics(
    *,
    reference_count: int,
    predicted_count: int,
    tp: int,
    fp: int,
    fn: int,
    timing_errors_sec: list[float],
) -> ClassMetrics:
    precision = _safe_ratio(tp, tp + fp)
    recall = _safe_ratio(tp, tp + fn)
    f1 = _safe_ratio(2 * tp, (2 * tp) + fp + fn)
    timing_mae_ms = None
    if timing_errors_sec:
        timing_mae_ms = 1000.0 * (sum(abs(err) for err in timing_errors_sec) / float(len(timing_errors_sec)))
    return ClassMetrics(
        reference_count=reference_count,
        predicted_count=predicted_count,
        tp=tp,
        fp=fp,
        fn=fn,
        precision=precision,
        recall=recall,
        f1=f1,
        timing_mae_ms=timing_mae_ms,
    )


def _greedy_match_pairs(
    reference_events: list[tuple[int, BenchmarkEvent]],
    predicted_events: list[tuple[int, BenchmarkEvent]],
    tolerance_sec: float,
) -> list[tuple[int, int, float]]:
    matches: list[tuple[int, int, float]] = []
    used_predicted: set[int] = set()
    start = 0

    for ref_index, ref_event in reference_events:
        while start < len(predicted_events) and predicted_events[start][1].time < ref_event.time - tolerance_sec:
            start += 1

        best_pred_global: int | None = None
        best_error: float | None = None
        j = start
        while j < len(predicted_events):
            pred_global, pred_event = predicted_events[j]
            if pred_event.time > ref_event.time + tolerance_sec:
                break
            if pred_global not in used_predicted:
                error = abs(pred_event.time - ref_event.time)
                if best_error is None or error < best_error:
                    best_pred_global = pred_global
                    best_error = error
            j += 1

        if best_pred_global is not None and best_error is not None:
            used_predicted.add(best_pred_global)
            matches.append((ref_index, best_pred_global, best_error))

    return matches


def _metrics_to_dict(metrics: ClassMetrics) -> dict[str, Any]:
    return {
        "reference_count": metrics.reference_count,
        "predicted_count": metrics.predicted_count,
        "tp": metrics.tp,
        "fp": metrics.fp,
        "fn": metrics.fn,
        "precision": round(metrics.precision, 6),
        "recall": round(metrics.recall, 6),
        "f1": round(metrics.f1, 6),
        "timing_mae_ms": None if metrics.timing_mae_ms is None else round(metrics.timing_mae_ms, 3),
    }


def evaluate_drum_transcription(
    reference_events: Iterable[BenchmarkEvent],
    predicted_events: Iterable[BenchmarkEvent],
    *,
    tolerance_sec: float = 0.06,
) -> dict[str, Any]:
    tolerance_sec = max(0.001, float(tolerance_sec))
    reference_sorted = sorted(reference_events, key=lambda event: (event.time, _CLASS_INDEX[event.drum_class]))
    predicted_sorted = sorted(predicted_events, key=lambda event: (event.time, _CLASS_INDEX[event.drum_class]))

    indexed_reference = list(enumerate(reference_sorted))
    indexed_predicted = list(enumerate(predicted_sorted))
    matched_reference: set[int] = set()
    matched_predicted: set[int] = set()
    timing_errors_sec: list[float] = []
    per_class: dict[str, dict[str, Any]] = {}

    for drum_class in BENCHMARK_CLASS_ORDER:
        class_reference = [item for item in indexed_reference if item[1].drum_class == drum_class]
        class_predicted = [item for item in indexed_predicted if item[1].drum_class == drum_class]
        class_matches = _greedy_match_pairs(class_reference, class_predicted, tolerance_sec)
        matched_reference.update(ref_idx for ref_idx, _pred_idx, _err in class_matches)
        matched_predicted.update(pred_idx for _ref_idx, pred_idx, _err in class_matches)
        class_errors = [err for _ref_idx, _pred_idx, err in class_matches]
        timing_errors_sec.extend(class_errors)

        tp = len(class_matches)
        reference_count = len(class_reference)
        predicted_count = len(class_predicted)
        per_class[drum_class] = _metrics_to_dict(
            _build_metrics(
                reference_count=reference_count,
                predicted_count=predicted_count,
                tp=tp,
                fp=predicted_count - tp,
                fn=reference_count - tp,
                timing_errors_sec=class_errors,
            )
        )

    overall = _metrics_to_dict(
        _build_metrics(
            reference_count=len(reference_sorted),
            predicted_count=len(predicted_sorted),
            tp=len(matched_reference),
            fp=len(predicted_sorted) - len(matched_predicted),
            fn=len(reference_sorted) - len(matched_reference),
            timing_errors_sec=timing_errors_sec,
        )
    )

    unmatched_reference = [item for item in indexed_reference if item[0] not in matched_reference]
    unmatched_predicted = [item for item in indexed_predicted if item[0] not in matched_predicted]
    confusion_matches = _greedy_match_pairs(unmatched_reference, unmatched_predicted, tolerance_sec)
    confusion_counts: dict[tuple[str, str], int] = {}

    for ref_idx, pred_idx, _err in confusion_matches:
        reference_class = reference_sorted[ref_idx].drum_class
        predicted_class = predicted_sorted[pred_idx].drum_class
        if reference_class == predicted_class:
            continue
        key = (reference_class, predicted_class)
        confusion_counts[key] = confusion_counts.get(key, 0) + 1

    confusions = [
        {
            "reference_class": ref_class,
            "predicted_class": pred_class,
            "count": count,
        }
        for (ref_class, pred_class), count in sorted(
            confusion_counts.items(),
            key=lambda item: (
                -item[1],
                _CLASS_INDEX[item[0][0]],
                _CLASS_INDEX[item[0][1]],
            ),
        )
    ]

    return {
        "reference_count": len(reference_sorted),
        "predicted_count": len(predicted_sorted),
        "tolerance_ms": round(tolerance_sec * 1000.0, 3),
        "overall": overall,
        "per_class": per_class,
        "confusions": confusions,
    }


def benchmark_algorithms(
    stem_path: Path | str,
    reference_events: Iterable[BenchmarkEvent],
    algorithm_ids: Iterable[str],
    algorithm_registry: Mapping[str, Any],
    *,
    tolerance_sec: float = 0.06,
) -> list[dict[str, Any]]:
    stem = Path(stem_path)
    reference = list(reference_events)
    results: list[dict[str, Any]] = []

    for algorithm_id in algorithm_ids:
        fn = algorithm_registry.get(algorithm_id)
        if fn is None:
            results.append({"algorithm": algorithm_id, "error": "algorithm unavailable"})
            continue

        try:
            raw_events = fn(stem)
        except Exception as exc:
            results.append({"algorithm": algorithm_id, "error": str(exc)})
            continue

        predicted_events, ignored = normalize_transcribed_events(raw_events)
        evaluation = evaluate_drum_transcription(
            reference,
            predicted_events,
            tolerance_sec=tolerance_sec,
        )
        results.append(
            {
                "algorithm": algorithm_id,
                "raw_predicted_count": len(raw_events),
                "ignored_predicted_events": ignored,
                **evaluation,
            }
        )

    return results


def _format_metric_line(label: str, metrics: Mapping[str, Any]) -> str:
    mae = metrics.get("timing_mae_ms")
    mae_text = "-" if mae is None else f"{float(mae):.1f}ms"
    return (
        f"  {label:<8} F1 {float(metrics['f1']):.3f}  "
        f"P {float(metrics['precision']):.3f}  "
        f"R {float(metrics['recall']):.3f}  "
        f"tp {int(metrics['tp'])} fp {int(metrics['fp'])} fn {int(metrics['fn'])}  "
        f"mae {mae_text}"
    )


def format_benchmark_summary(payload: Mapping[str, Any]) -> str:
    lines = [
        (
            f"reference: {payload['reference_path']}  "
            f"events={int(payload['reference_count'])}  "
            f"tolerance={float(payload['tolerance_ms']):.1f}ms"
        )
    ]

    for result in payload.get("results", []):
        algorithm = str(result.get("algorithm", "unknown"))
        lines.append("")
        lines.append(algorithm)
        error = result.get("error")
        if error:
            lines.append(f"  error: {error}")
            continue

        lines.append(_format_metric_line("overall", result["overall"]))
        lines.append(_format_metric_line("snare", result["per_class"]["snare"]))

        top_confusions = result.get("confusions", [])[:3]
        if top_confusions:
            rendered = ", ".join(
                f"{_DISPLAY_CLASS_NAMES[item['reference_class']]}->{_DISPLAY_CLASS_NAMES[item['predicted_class']]} x{item['count']}"
                for item in top_confusions
            )
            lines.append(f"  confusions {rendered}")
        else:
            lines.append("  confusions none within tolerance")

    return "\n".join(lines)
