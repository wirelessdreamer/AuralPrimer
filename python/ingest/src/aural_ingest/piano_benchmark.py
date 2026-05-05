"""Piano-focused benchmark evaluation.

Compared with the generic melodic benchmark, this module treats piano as a
polyphonic note-interval problem instead of a loose onset-only melodic line.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from aural_ingest.drum_benchmark import _compress_tempo_changes, _read_vlq, _tick_to_seconds
from aural_ingest.transcription import MelodicNote, build_default_melodic_algorithm_registry


@dataclass
class PianoBenchmarkEvent:
    time: float
    pitch: int
    duration: float = 0.0
    velocity: int = 0


@dataclass
class PianoEvalResult:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    onset_only_tp: int = 0
    onset_only_fp: int = 0
    onset_only_fn: int = 0
    offset_tp: int = 0
    offset_velocity_tp: int = 0
    duplicate_predictions: int = 0
    onset_timing_errors_ms: list[float] = field(default_factory=list)
    offset_timing_errors_ms: list[float] = field(default_factory=list)
    velocity_errors: list[float] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.tp / max(1, self.tp + self.fp)

    @property
    def recall(self) -> float:
        return self.tp / max(1, self.tp + self.fn)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / max(1e-9, p + r)

    @property
    def onset_only_precision(self) -> float:
        return self.onset_only_tp / max(1, self.onset_only_tp + self.onset_only_fp)

    @property
    def onset_only_recall(self) -> float:
        return self.onset_only_tp / max(1, self.onset_only_tp + self.onset_only_fn)

    @property
    def onset_only_f1(self) -> float:
        p, r = self.onset_only_precision, self.onset_only_recall
        return 2 * p * r / max(1e-9, p + r)

    @property
    def note_with_offset_precision(self) -> float:
        return self.offset_tp / max(1, self.tp + self.fp)

    @property
    def note_with_offset_recall(self) -> float:
        return self.offset_tp / max(1, self.tp + self.fn)

    @property
    def note_with_offset_f1(self) -> float:
        p, r = self.note_with_offset_precision, self.note_with_offset_recall
        return 2 * p * r / max(1e-9, p + r)

    @property
    def note_with_offset_velocity_precision(self) -> float:
        return self.offset_velocity_tp / max(1, self.tp + self.fp)

    @property
    def note_with_offset_velocity_recall(self) -> float:
        return self.offset_velocity_tp / max(1, self.tp + self.fn)

    @property
    def note_with_offset_velocity_f1(self) -> float:
        p, r = self.note_with_offset_velocity_precision, self.note_with_offset_velocity_recall
        return 2 * p * r / max(1e-9, p + r)

    @property
    def pitch_accuracy(self) -> float:
        return self.tp / max(1, self.onset_only_tp)

    @property
    def offset_accuracy(self) -> float:
        return self.offset_tp / max(1, self.tp)

    @property
    def onset_timing_mae_ms(self) -> float | None:
        if not self.onset_timing_errors_ms:
            return None
        return sum(abs(v) for v in self.onset_timing_errors_ms) / len(self.onset_timing_errors_ms)

    @property
    def offset_timing_mae_ms(self) -> float | None:
        if not self.offset_timing_errors_ms:
            return None
        return sum(abs(v) for v in self.offset_timing_errors_ms) / len(self.offset_timing_errors_ms)

    @property
    def velocity_mae(self) -> float | None:
        if not self.velocity_errors:
            return None
        return sum(abs(v) for v in self.velocity_errors) / len(self.velocity_errors)

    @property
    def duplicate_rate(self) -> float:
        total_predictions = self.tp + self.fp
        return self.duplicate_predictions / max(1, total_predictions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "f1": round(self.f1, 4),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "onset_only_f1": round(self.onset_only_f1, 4),
            "pitch_accuracy": round(self.pitch_accuracy, 4),
            "offset_accuracy": round(self.offset_accuracy, 4),
            "note_with_offset_f1": round(self.note_with_offset_f1, 4),
            "note_with_offset_velocity_f1": round(self.note_with_offset_velocity_f1, 4),
            "onset_timing_mae_ms": round(self.onset_timing_mae_ms, 2)
            if self.onset_timing_mae_ms is not None
            else None,
            "offset_timing_mae_ms": round(self.offset_timing_mae_ms, 2)
            if self.offset_timing_mae_ms is not None
            else None,
            "velocity_mae": round(self.velocity_mae, 2) if self.velocity_mae is not None else None,
            "duplicate_rate": round(self.duplicate_rate, 4),
        }


def parse_piano_midi_reference(
    midi_path: Path,
    offset_sec: float = 0.0,
    role: str | None = None,
) -> list[PianoBenchmarkEvent]:
    data = midi_path.read_bytes()
    if len(data) < 14 or data[0:4] != b"MThd":
        raise ValueError("reference midi missing MThd header")

    header_len = int.from_bytes(data[4:8], "big")
    if header_len < 6:
        raise ValueError("reference midi header too small")
    track_count = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big")
    if division & 0x8000:
        raise ValueError("smpte midi timing is not supported for piano benchmarks")

    pos = 8 + header_len
    tempo_changes: list[tuple[int, int]] = [(0, 500_000)]
    note_events: list[tuple[int, int, int, int]] = []
    all_note_events: list[tuple[int, int, int, int]] = []

    def track_matches(track_name: str | None) -> bool:
        if not role:
            return True
        normalized_role = str(role).strip().lower()
        name = (track_name or "").strip().lower().replace("_", " ")
        if not name:
            return False
        if normalized_role in {"keys", "piano", "synth"}:
            return any(token in name for token in ("key", "piano", "synth"))
        return normalized_role.replace("_", " ") in name

    for _track_index in range(track_count):
        if pos + 8 > len(data) or data[pos : pos + 4] != b"MTrk":
            raise ValueError("reference midi missing MTrk chunk")
        track_len = int.from_bytes(data[pos + 4 : pos + 8], "big")
        pos += 8
        end = pos + track_len
        if end > len(data):
            raise ValueError("reference midi track chunk truncated")

        tick = 0
        running_status: int | None = None
        open_notes: dict[tuple[int, int], tuple[int, int]] = {}
        track_name: str | None = None
        track_note_events: list[tuple[int, int, int, int]] = []

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
                pos += 1

            if event_type in {0xC0, 0xD0}:
                continue

            if pos >= end:
                raise ValueError("truncated midi channel event")
            data2 = data[pos]
            pos += 1

            key = (channel, int(data1))
            if event_type == 0x90 and data2 > 0:
                prior = open_notes.get(key)
                if prior is not None:
                    track_note_events.append((prior[0], tick, int(data1), prior[1]))
                open_notes[key] = (tick, int(data2))
            elif event_type == 0x80 or (event_type == 0x90 and data2 == 0):
                prior = open_notes.pop(key, None)
                if prior is not None:
                    track_note_events.append((prior[0], tick, int(data1), prior[1]))

        for (channel, midi), (start_tick, velocity) in open_notes.items():
            track_note_events.append((start_tick, tick, midi, velocity))

        all_note_events.extend(track_note_events)
        if track_matches(track_name):
            note_events.extend(track_note_events)

        pos = end

    if role and not note_events:
        note_events = all_note_events

    tempo = _compress_tempo_changes(tempo_changes)
    events: list[PianoBenchmarkEvent] = []
    for start_tick, end_tick, pitch, velocity in note_events:
        t_on = _tick_to_seconds(start_tick, tempo, division) + offset_sec
        t_off = _tick_to_seconds(max(start_tick, end_tick), tempo, division) + offset_sec
        if t_off <= t_on:
            continue
        events.append(
            PianoBenchmarkEvent(
                time=round(max(0.0, t_on), 6),
                pitch=int(pitch),
                duration=round(max(0.0, t_off - t_on), 6),
                velocity=int(velocity),
            )
        )

    return sorted(events, key=lambda event: (event.time, event.pitch, event.duration))


def _count_duplicate_predictions(predicted: list[MelodicNote], *, window_sec: float = 0.035) -> int:
    duplicates = 0
    last_by_pitch: dict[int, MelodicNote] = {}
    for note in sorted(predicted, key=lambda item: (item.t_on, item.pitch, item.t_off)):
        prev = last_by_pitch.get(note.pitch)
        if prev is not None and (note.t_on - prev.t_on) <= window_sec:
            duplicates += 1
        if prev is None or note.t_on >= prev.t_on:
            last_by_pitch[note.pitch] = note
    return duplicates


def melodic_notes_to_dicts(notes: Iterable[MelodicNote]) -> list[dict[str, Any]]:
    return [
        {
            "t_on": round(float(note.t_on), 6),
            "t_off": round(float(note.t_off), 6),
            "pitch": int(note.pitch),
            "velocity": int(note.velocity),
            "instrument": str(note.instrument),
        }
        for note in notes
    ]


def summarize_piano_predictions(predicted: list[MelodicNote]) -> dict[str, Any]:
    durations = [max(0.0, float(note.t_off) - float(note.t_on)) for note in predicted]
    velocities = [int(note.velocity) for note in predicted]
    pitches = [int(note.pitch) for note in predicted]
    duplicate_predictions = _count_duplicate_predictions(predicted)
    note_count = len(predicted)

    return {
        "note_count": note_count,
        "duplicate_predictions": duplicate_predictions,
        "duplicate_rate": round(duplicate_predictions / max(1, note_count), 4),
        "pitch_min": min(pitches) if pitches else None,
        "pitch_max": max(pitches) if pitches else None,
        "mean_duration_sec": round(sum(durations) / max(1, len(durations)), 4) if durations else None,
        "mean_velocity": round(sum(velocities) / max(1, len(velocities)), 2) if velocities else None,
    }


def _midi_vlq(value: int) -> bytes:
    value = max(0, int(value))
    buffer = value & 0x7F
    value >>= 7
    while value:
        buffer <<= 8
        buffer |= ((value & 0x7F) | 0x80)
        value >>= 7

    out = bytearray()
    while True:
        out.append(buffer & 0xFF)
        if buffer & 0x80:
            buffer >>= 8
            continue
        break
    return bytes(out)


def _note_value(note: MelodicNote | Mapping[str, Any], key: str, default: Any) -> Any:
    if isinstance(note, Mapping):
        return note.get(key, default)
    return getattr(note, key, default)


def write_melodic_notes_midi(
    notes: Iterable[MelodicNote | Mapping[str, Any]],
    out_path: Path,
    *,
    bpm: float = 120.0,
) -> None:
    ticks_per_quarter = 480
    bpm = max(1.0, float(bpm))
    ticks_per_second = ticks_per_quarter * bpm / 60.0
    tempo_us_per_quarter = max(1, int(round(60_000_000.0 / bpm)))

    events: list[tuple[int, int, bytes]] = [
        (0, 0, b"\xff\x51\x03" + tempo_us_per_quarter.to_bytes(3, "big")),
        (0, 1, b"\xff\x03\x05Piano"),
    ]
    for note in notes:
        t_on = max(0.0, float(_note_value(note, "t_on", 0.0) or 0.0))
        t_off = max(t_on, float(_note_value(note, "t_off", t_on) or t_on))
        on_tick = int(round(t_on * ticks_per_second))
        off_tick = max(on_tick + 1, int(round(t_off * ticks_per_second)))
        pitch = max(0, min(127, int(_note_value(note, "pitch", 60) or 60)))
        velocity = max(1, min(127, int(_note_value(note, "velocity", 90) or 90)))
        events.append((off_tick, 2, bytes([0x80, pitch, 0])))
        events.append((on_tick, 3, bytes([0x90, pitch, velocity])))

    body = bytearray()
    last_tick = 0
    for tick, _order, payload in sorted(events, key=lambda item: (item[0], item[1])):
        body.extend(_midi_vlq(tick - last_tick))
        body.extend(payload)
        last_tick = tick
    body.extend(_midi_vlq(0))
    body.extend(b"\xff\x2f\x00")

    header = (
        b"MThd"
        + (6).to_bytes(4, "big")
        + (0).to_bytes(2, "big")
        + (1).to_bytes(2, "big")
        + ticks_per_quarter.to_bytes(2, "big")
    )
    track = b"MTrk" + len(body).to_bytes(4, "big") + bytes(body)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(header + track)


def evaluate_piano(
    predicted: list[MelodicNote],
    reference: list[PianoBenchmarkEvent],
    *,
    tolerance_sec: float = 0.06,
    offset_tolerance_sec: float = 0.12,
    velocity_tolerance: int = 20,
) -> PianoEvalResult:
    result = PianoEvalResult()
    pred_sorted = sorted(predicted, key=lambda n: (n.t_on, n.pitch, n.t_off))
    ref_sorted = sorted(reference, key=lambda n: (n.time, n.pitch, n.duration))

    result.duplicate_predictions = _count_duplicate_predictions(pred_sorted)

    onset_ref_used = [False] * len(ref_sorted)
    exact_ref_used = [False] * len(ref_sorted)

    for pred in pred_sorted:
        best_onset_idx = -1
        best_onset_dist = tolerance_sec + 1.0
        best_exact_idx = -1
        best_exact_dist = tolerance_sec + 1.0

        for idx, ref in enumerate(ref_sorted):
            onset_dist = abs(float(pred.t_on) - float(ref.time))
            if onset_dist <= tolerance_sec and not onset_ref_used[idx] and onset_dist < best_onset_dist:
                best_onset_idx = idx
                best_onset_dist = onset_dist

            if (
                onset_dist <= tolerance_sec
                and not exact_ref_used[idx]
                and int(pred.pitch) == int(ref.pitch)
                and onset_dist < best_exact_dist
            ):
                best_exact_idx = idx
                best_exact_dist = onset_dist

        if best_onset_idx >= 0:
            onset_ref_used[best_onset_idx] = True
            result.onset_only_tp += 1
        else:
            result.onset_only_fp += 1

        if best_exact_idx >= 0:
            exact_ref_used[best_exact_idx] = True
            ref = ref_sorted[best_exact_idx]
            result.tp += 1
            result.onset_timing_errors_ms.append(best_exact_dist * 1000.0)

            ref_t_off = float(ref.time) + float(ref.duration)
            pred_t_off = float(pred.t_off)
            offset_err = abs(pred_t_off - ref_t_off)
            result.offset_timing_errors_ms.append(offset_err * 1000.0)
            if offset_err <= offset_tolerance_sec:
                result.offset_tp += 1
                if abs(int(pred.velocity) - int(ref.velocity)) <= int(velocity_tolerance):
                    result.offset_velocity_tp += 1

            result.velocity_errors.append(abs(int(pred.velocity) - int(ref.velocity)))
        else:
            result.fp += 1

    result.fn = sum(1 for matched in exact_ref_used if not matched)
    result.onset_only_fn = sum(1 for matched in onset_ref_used if not matched)
    return result


PIANO_ALGORITHMS = [
    "piano_auto",
    "piano_polyphonic_clean",
    "piano_polyphonic",
    "piano_transkun_clean",
    "piano_pti_clean",
    "piano_transkun",
    "piano_pti",
    "piano_hft_clean",
    "piano_hft",
    "melodic_hpss_combined",
    "melodic_octave_fix",
    "melodic_combined",
    "basic_pitch",
    "pyin",
]


def benchmark_piano_algorithms(
    wav_path: Path,
    reference: list[PianoBenchmarkEvent] | None,
    algorithms: list[str],
    *,
    instrument: str = "keys",
    tolerance_sec: float = 0.06,
    offset_tolerance_sec: float = 0.12,
    velocity_tolerance: int = 20,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    registry = build_default_melodic_algorithm_registry(instrument=instrument)

    for algorithm in algorithms:
        fn = registry.get(algorithm)
        if fn is None:
            results.append(
                {
                    "algorithm": algorithm,
                    "error": f"algorithm '{algorithm}' unavailable",
                    "note_count": 0,
                    "overall": PianoEvalResult().to_dict(),
                }
            )
            continue

        try:
            t0 = time.time()
            predicted = fn(wav_path)
            elapsed = time.time() - t0
            eval_result = (
                evaluate_piano(
                    predicted,
                    reference,
                    tolerance_sec=tolerance_sec,
                    offset_tolerance_sec=offset_tolerance_sec,
                    velocity_tolerance=velocity_tolerance,
                )
                if reference is not None
                else PianoEvalResult()
            )
            results.append(
                {
                    "algorithm": algorithm,
                    "note_count": len(predicted),
                    "elapsed_sec": round(elapsed, 2),
                    "prediction": summarize_piano_predictions(predicted),
                    "predicted_notes": melodic_notes_to_dicts(predicted),
                    "overall": eval_result.to_dict(),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "algorithm": algorithm,
                    "error": str(exc),
                    "note_count": 0,
                    "overall": PianoEvalResult().to_dict(),
                }
            )

    return results


def format_piano_summary(payload: Mapping[str, Any]) -> str:
    lines = []
    if not payload.get("reference_available", True):
        lines.append(f"  {'Algorithm':<28} {'Notes':>6} {'Dup':>6} {'Pitch':>9} {'Dur':>7} {'Vel':>7}")
        lines.append("  " + "-" * 68)

        for entry in payload.get("results", []):
            error = entry.get("error")
            if error:
                lines.append(f"  {entry['algorithm']:<28} ERROR: {error}")
                continue
            prediction = entry.get("prediction", {})
            pitch_min = prediction.get("pitch_min")
            pitch_max = prediction.get("pitch_max")
            pitch_text = f"{pitch_min}-{pitch_max}" if pitch_min is not None and pitch_max is not None else "n/a"
            mean_duration = prediction.get("mean_duration_sec")
            duration_text = f"{mean_duration:>6.2f}" if mean_duration is not None else "   n/a"
            mean_velocity = prediction.get("mean_velocity")
            velocity_text = f"{mean_velocity:>6.1f}" if mean_velocity is not None else "   n/a"
            lines.append(
                f"  {entry['algorithm']:<28} "
                f"{entry.get('note_count', 0):>6} "
                f"{prediction.get('duplicate_rate', 0.0):>6.1%} "
                f"{pitch_text:>9} "
                f"{duration_text} "
                f"{velocity_text}"
            )
        return "\n".join(lines)

    lines.append(
        f"  {'Algorithm':<28} {'F1':>6} {'Onset':>6} {'Offset':>6} {'OffVel':>6} {'Pitch':>6} {'VelMAE':>7} {'Dup':>6} {'Notes':>6}"
    )
    lines.append("  " + "-" * 96)

    for entry in payload.get("results", []):
        overall = entry.get("overall", {})
        error = entry.get("error")
        if error:
            lines.append(f"  {entry['algorithm']:<28} ERROR: {error}")
            continue
        vel_mae = overall.get("velocity_mae")
        vel_text = f"{vel_mae:>6.1f}" if vel_mae is not None else "   n/a"
        lines.append(
            f"  {entry['algorithm']:<28} "
            f"{overall.get('f1', 0.0):>6.3f} "
            f"{overall.get('onset_only_f1', 0.0):>6.3f} "
            f"{overall.get('note_with_offset_f1', 0.0):>6.3f} "
            f"{overall.get('note_with_offset_velocity_f1', 0.0):>6.3f} "
            f"{overall.get('pitch_accuracy', 0.0):>6.1%} "
            f"{vel_text} "
            f"{overall.get('duplicate_rate', 0.0):>6.1%} "
            f"{entry.get('note_count', 0):>6}"
        )

    return "\n".join(lines)
