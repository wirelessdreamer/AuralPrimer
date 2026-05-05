"""Unified transcription quality benchmark helpers.

This module is intentionally lightweight glue over the existing drum, melodic,
and piano benchmark suites. It adds gameplay-oriented metrics and corpus
inventory without replacing the focused benchmark runners.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import importlib
import importlib.util
import json
import os
import statistics
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from aural_ingest.drum_benchmark import benchmark_algorithms, load_drum_reference
from aural_ingest.melodic_benchmark import (
    benchmark_melodic_algorithms,
    parse_melodic_midi_reference,
)
from aural_ingest.piano_benchmark import (
    benchmark_piano_algorithms,
    parse_piano_midi_reference,
)
from aural_ingest.transcription import (
    DrumEvent,
    MelodicNote,
    available_mt3_modelpacks,
    build_default_drum_algorithm_registry,
    drum_engines_for_profile,
    melodic_methods_for_profile,
    resolve_basic_pitch_model_path,
    transcription_profile_metadata,
    validate_transcription_profile,
)

QUALITY_SUITE_VERSION = "0.4.0"
DEFAULT_OUTPUT_ROOT = Path("benchmarks") / "quality" / "runs"
QUALITY_METRIC_BACKENDS: dict[str, dict[str, Any]] = {
    "mir_eval": {
        "display_name": "MIR Eval",
        "module_names": ("mir_eval",),
        "purpose": "note-event transcription metrics",
        "protocols": ("mir_eval.transcription",),
        "failure_mode": "reference-backed MIR metrics omitted; native benchmark metrics still run",
    },
    "museval": {
        "display_name": "Museval",
        "module_names": ("museval",),
        "purpose": "source-separation SDR protocol",
        "protocols": ("BSSEval v4 / museval.evaluate",),
        "failure_mode": "separation SDR metrics omitted; transcription benchmarks still run",
    },
}
QUALITY_DATASET_CONTRACTS: dict[str, dict[str, Any]] = {
    "musdb18": {
        "display_name": "MUSDB18",
        "purpose": "controlled source-separation evaluation",
        "env_vars": ("AURAL_MUSDB18_ROOT",),
        "roles": ("drums", "bass", "other", "vocals", "mixture"),
        "ship_policy": "internal benchmarking only; do not ship dataset content or derived in-game content",
    },
    "musdb18_hq": {
        "display_name": "MUSDB18-HQ",
        "purpose": "controlled high-quality source-separation evaluation",
        "env_vars": ("AURAL_MUSDB18_HQ_ROOT",),
        "roles": ("drums", "bass", "other", "vocals", "mixture"),
        "ship_policy": "internal benchmarking only; do not ship dataset content or derived in-game content",
    },
    "enst_drums": {
        "display_name": "ENST-Drums",
        "purpose": "drum transcription regression fixtures",
        "env_vars": ("AURAL_ENST_DRUMS_ROOT",),
        "roles": ("drums",),
        "ship_policy": "internal benchmarking only; keep product fixtures synthetic/owned/cleared",
    },
    "idmt_smt_drums": {
        "display_name": "IDMT-SMT-Drums",
        "purpose": "drum transcription regression fixtures",
        "env_vars": ("AURAL_IDMT_SMT_DRUMS_ROOT",),
        "roles": ("drums",),
        "ship_policy": "internal benchmarking only; keep product fixtures synthetic/owned/cleared",
    },
}
OPTIONAL_MODEL_CONTRACTS: dict[str, dict[str, Any]] = {
    "basic_pitch": {
        "display_name": "Basic Pitch",
        "module_names": ("basic_pitch",),
        "roles": ("bass", "lead_guitar", "rhythm_guitar", "keys"),
        "methods": ("basic_pitch",),
        "decode": "event_decode",
        "modelpack_resolution": True,
        "checkpoint_env": (),
        "command_env": (),
        "failure_mode": "portable-safe fallback through melodic fallback chain",
    },
    "piano_transkun": {
        "display_name": "Transkun",
        "module_names": ("transkun",),
        "roles": ("keys",),
        "methods": ("piano_transkun", "piano_transkun_clean"),
        "decode": "temp_midi",
        "modelpack_resolution": False,
        "checkpoint_env": (),
        "command_env": (),
        "failure_mode": "clear RuntimeError, then melodic fallback when used through import",
    },
    "piano_pti": {
        "display_name": "piano_transcription_inference",
        "module_names": ("piano_transcription_inference",),
        "roles": ("keys",),
        "methods": ("piano_pti", "piano_pti_clean"),
        "decode": "temp_midi",
        "modelpack_resolution": True,
        "checkpoint_env": ("AURAL_PIANO_PTI_CHECKPOINT",),
        "command_env": (),
        "allow_download_env": "AURAL_PIANO_PTI_ALLOW_DOWNLOAD",
        "failure_mode": "clear RuntimeError unless checkpoint or explicit download opt-in is configured",
    },
    "piano_hft": {
        "display_name": "hFT-Transformer",
        "module_names": (),
        "roles": ("keys",),
        "methods": ("piano_hft", "piano_hft_clean"),
        "decode": "temp_midi",
        "modelpack_resolution": True,
        "checkpoint_env": ("AURAL_PIANO_HFT_CHECKPOINT",),
        "command_env": ("AURAL_PIANO_HFT_COMMAND",),
        "failure_mode": "clear RuntimeError unless checkpoint and command adapter are configured",
    },
    "torchcrepe": {
        "display_name": "torchcrepe",
        "module_names": ("torchcrepe", "torch"),
        "roles": ("bass", "lead_guitar", "rhythm_guitar"),
        "methods": ("torchcrepe",),
        "decode": "frame_f0_decode",
        "modelpack_resolution": False,
        "checkpoint_env": (),
        "command_env": (),
        "failure_mode": "clear RuntimeError, then melodic fallback when used through import",
    },
    "beatnet": {
        "display_name": "BeatNet",
        "module_names": ("BeatNet", "beatnet"),
        "roles": ("tempo", "drums"),
        "methods": (),
        "decode": "beat_downbeat_priors",
        "modelpack_resolution": False,
        "checkpoint_env": (),
        "command_env": (),
        "failure_mode": "availability-only until wired as a beat/downbeat prior",
    },
    "omnizart": {
        "display_name": "Omnizart",
        "module_names": ("omnizart",),
        "roles": ("research",),
        "methods": (),
        "decode": "research_comparator",
        "modelpack_resolution": False,
        "checkpoint_env": (),
        "command_env": (),
        "failure_mode": "availability-only research comparator; never required for portable import",
    },
}
IGNORED_CORPUS_DIR_NAMES = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    ".tmp-tests",
    "__pycache__",
    "node_modules",
}


@dataclass(frozen=True)
class QualityCase:
    case_id: str
    role: str
    wav_path: Path
    reference_path: Path | None = None
    family: str = "auto"
    name: str | None = None
    offset_sec: float = 0.0


def _safe_mean(values: Iterable[float | None]) -> float | None:
    usable = [float(v) for v in values if v is not None]
    return round(float(statistics.fmean(usable)), 6) if usable else None


def _slugify(value: str) -> str:
    out: list[str] = []
    last_dash = False
    for ch in value.strip().lower():
        if ch.isalnum():
            out.append(ch)
            last_dash = False
        elif not last_dash:
            out.append("-")
            last_dash = True
    return "".join(out).strip("-") or "quality-run"


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _dedupe(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out


def _role_family(role: str) -> str:
    normalized = str(role).strip().lower()
    if normalized == "drums":
        return "drums"
    if normalized in {"keys", "piano", "synth"}:
        return "piano"
    return "melodic"


def _module_available(module_names: Sequence[str]) -> tuple[bool, str | None]:
    for module_name in module_names:
        if importlib.util.find_spec(module_name) is not None:
            return True, module_name
    return False, None


def inspect_quality_metric_backends() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for backend_id, contract in QUALITY_METRIC_BACKENDS.items():
        ok, module_name = _module_available(tuple(contract.get("module_names", ())))
        out[backend_id] = {
            "ok": ok,
            "display_name": contract.get("display_name", backend_id),
            "module": module_name,
            "module_candidates": list(contract.get("module_names", ())),
            "purpose": contract.get("purpose"),
            "protocols": list(contract.get("protocols", ())),
            "failure_mode": contract.get("failure_mode"),
        }
    return out


def inspect_quality_dataset_sources() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for dataset_id, contract in QUALITY_DATASET_CONTRACTS.items():
        roots: list[dict[str, Any]] = []
        for env_var in contract.get("env_vars", ()):
            raw = os.environ.get(str(env_var), "").strip()
            path = Path(raw).expanduser() if raw else None
            roots.append(
                {
                    "env_var": str(env_var),
                    "configured": bool(raw),
                    "path": str(path) if path else None,
                    "exists": bool(path and path.exists()),
                }
            )
        out[dataset_id] = {
            "ok": any(root["exists"] for root in roots),
            "display_name": contract.get("display_name", dataset_id),
            "purpose": contract.get("purpose"),
            "roles": list(contract.get("roles", ())),
            "roots": roots,
            "ship_policy": contract.get("ship_policy"),
        }
    return out


def evaluate_museval_separation(
    reference_stems: Mapping[str, Path | str],
    estimated_stems: Mapping[str, Path | str],
    *,
    sample_rate: int | None = None,
) -> dict[str, Any]:
    """Evaluate source-separation estimates with Museval when available.

    The function is intentionally optional-data/optional-package safe. It is
    for internal benchmark runs only; callers provide local dataset paths.
    """

    if importlib.util.find_spec("museval") is None:
        return {
            "available": False,
            "backend": "museval",
            "protocol": "BSSEval v4 / museval.evaluate",
            "reason": "museval is not installed",
        }
    if importlib.util.find_spec("soundfile") is None:
        return {
            "available": False,
            "backend": "museval",
            "protocol": "BSSEval v4 / museval.evaluate",
            "reason": "soundfile is not installed",
        }

    roles = [role for role in reference_stems if role in estimated_stems]
    missing_reference = sorted(set(estimated_stems) - set(reference_stems))
    missing_estimate = sorted(set(reference_stems) - set(estimated_stems))
    if not roles:
        return {
            "available": True,
            "backend": "museval",
            "protocol": "BSSEval v4 / museval.evaluate",
            "status": "skipped",
            "reason": "no overlapping reference/estimate stem roles",
            "missing_reference_roles": missing_reference,
            "missing_estimate_roles": missing_estimate,
        }

    sf = importlib.import_module("soundfile")
    museval = importlib.import_module("museval")
    import numpy as np

    references = []
    estimates = []
    loaded_roles: list[str] = []
    errors: dict[str, str] = {}
    target_rate = sample_rate
    for role in roles:
        ref_path = Path(reference_stems[role])
        est_path = Path(estimated_stems[role])
        try:
            ref_audio, ref_rate = sf.read(str(ref_path), always_2d=True)
            est_audio, est_rate = sf.read(str(est_path), always_2d=True)
            if ref_rate != est_rate:
                raise ValueError(f"sample-rate mismatch: reference {ref_rate}, estimate {est_rate}")
            if target_rate is not None and int(ref_rate) != int(target_rate):
                raise ValueError(f"expected sample rate {target_rate}, got {ref_rate}")
            frame_count = min(len(ref_audio), len(est_audio))
            if frame_count <= 0:
                raise ValueError("empty stem audio")
            references.append(ref_audio[:frame_count])
            estimates.append(est_audio[:frame_count])
            loaded_roles.append(role)
            target_rate = int(ref_rate)
        except Exception as exc:
            errors[role] = str(exc)

    if not loaded_roles:
        return {
            "available": True,
            "backend": "museval",
            "protocol": "BSSEval v4 / museval.evaluate",
            "status": "failed",
            "errors": errors,
        }

    references_arr = np.stack(references, axis=0)
    estimates_arr = np.stack(estimates, axis=0)
    try:
        scores = museval.evaluate(references_arr, estimates_arr, win=1.0, hop=1.0)
    except Exception as exc:
        return {
            "available": True,
            "backend": "museval",
            "protocol": "BSSEval v4 / museval.evaluate",
            "status": "failed",
            "roles": loaded_roles,
            "errors": {**errors, "museval": str(exc)},
        }

    sdr = getattr(scores, "sdr", None)
    role_metrics: list[dict[str, Any]] = []
    if sdr is not None:
        for idx, role in enumerate(loaded_roles):
            values = np.asarray(sdr[idx]).astype(float)
            finite = values[np.isfinite(values)]
            role_metrics.append(
                {
                    "role": role,
                    "sdr_mean": round(float(np.mean(finite)), 6) if finite.size else None,
                    "sdr_median": round(float(np.median(finite)), 6) if finite.size else None,
                    "frame_count": int(finite.size),
                }
            )
    return {
        "available": True,
        "backend": "museval",
        "protocol": "BSSEval v4 / museval.evaluate",
        "status": "ok",
        "sample_rate": target_rate,
        "roles": loaded_roles,
        "role_metrics": role_metrics,
        "missing_reference_roles": missing_reference,
        "missing_estimate_roles": missing_estimate,
        "errors": errors,
    }


def _case_role_name(role: str) -> str:
    normalized = str(role).strip().lower()
    if normalized == "guitar":
        return "rhythm_guitar"
    return normalized


def _unique_case_id(base_id: str, seen: set[str], *, suffix: str | None = None) -> str:
    stem = _slugify(f"{base_id}-{suffix}") if suffix else _slugify(base_id)
    candidate = stem
    idx = 2
    while candidate in seen:
        candidate = f"{stem}-{idx}"
        idx += 1
    seen.add(candidate)
    return candidate


def _is_ignored_corpus_path(path: Path) -> bool:
    return any(part in IGNORED_CORPUS_DIR_NAMES for part in path.parts)


def _first_note_time(notes: Sequence[MelodicNote] | Sequence[Mapping[str, Any]]) -> float | None:
    times: list[float] = []
    for note in notes:
        raw = note.get("t_on") if isinstance(note, Mapping) else getattr(note, "t_on", None)
        try:
            times.append(float(raw))
        except Exception:
            continue
    return min(times) if times else None


def _first_drum_time(events: Sequence[DrumEvent] | Sequence[Mapping[str, Any]]) -> float | None:
    times: list[float] = []
    for event in events:
        raw = event.get("time") if isinstance(event, Mapping) else getattr(event, "time", None)
        try:
            times.append(float(raw))
        except Exception:
            continue
    return min(times) if times else None


def classify_start_offset(
    predicted_start_sec: float | None,
    reference_start_sec: float | None,
    *,
    quarantine_threshold_sec: float = 2.0,
) -> dict[str, Any]:
    if predicted_start_sec is None or reference_start_sec is None:
        return {
            "status": "unknown",
            "start_offset_sec": None,
            "quarantine": False,
            "reason": "missing predicted or reference start",
        }
    offset = float(predicted_start_sec) - float(reference_start_sec)
    quarantine = abs(offset) > float(quarantine_threshold_sec)
    return {
        "status": "quarantine" if quarantine else "ok",
        "start_offset_sec": round(offset, 6),
        "quarantine": quarantine,
        "threshold_sec": float(quarantine_threshold_sec),
    }


def compute_melodic_gameplay_metrics(
    notes: Sequence[MelodicNote] | Sequence[Mapping[str, Any]],
    *,
    duration_sec: float | None = None,
    role: str = "melodic",
    duplicate_window_sec: float = 0.035,
) -> dict[str, Any]:
    normalized: list[dict[str, float | int]] = []
    for note in notes:
        if isinstance(note, Mapping):
            t_on = note.get("t_on")
            t_off = note.get("t_off", t_on)
            pitch = note.get("pitch")
            velocity = note.get("velocity", 0)
        else:
            t_on = getattr(note, "t_on", None)
            t_off = getattr(note, "t_off", t_on)
            pitch = getattr(note, "pitch", None)
            velocity = getattr(note, "velocity", 0)
        try:
            on = max(0.0, float(t_on))
            off = max(on, float(t_off))
            normalized.append(
                {
                    "t_on": on,
                    "t_off": off,
                    "pitch": int(pitch),
                    "velocity": int(velocity),
                }
            )
        except Exception:
            continue

    normalized.sort(key=lambda n: (float(n["t_on"]), int(n["pitch"])))
    if duration_sec is None:
        duration_sec = max((float(n["t_off"]) for n in normalized), default=0.0)
    duration_min = max(float(duration_sec or 0.0) / 60.0, 1e-9)

    duplicates = 0
    chatter = 0
    polyphony_frames = 0
    max_polyphony = 0
    left_hand = 0
    right_hand = 0
    last_by_pitch: dict[int, float] = {}
    for i, note in enumerate(normalized):
        pitch = int(note["pitch"])
        on = float(note["t_on"])
        if pitch in last_by_pitch and on - last_by_pitch[pitch] <= duplicate_window_sec:
            duplicates += 1
        if pitch in last_by_pitch and on - last_by_pitch[pitch] <= 0.09:
            chatter += 1
        last_by_pitch[pitch] = on
        if role == "keys":
            if pitch < 60:
                left_hand += 1
            else:
                right_hand += 1

        active = 0
        for other in normalized[max(0, i - 48): i + 49]:
            if float(other["t_on"]) <= on < float(other["t_off"]):
                active += 1
        if active > 1:
            polyphony_frames += 1
        max_polyphony = max(max_polyphony, active)

    pitches = [int(n["pitch"]) for n in normalized]
    durations = [float(n["t_off"]) - float(n["t_on"]) for n in normalized]
    density = len(normalized) / duration_min
    density_target = 220.0 if role == "keys" else 115.0 if "guitar" in role else 95.0
    density_flag = density > density_target

    return {
        "role": role,
        "note_count": len(normalized),
        "duration_sec": round(float(duration_sec or 0.0), 6),
        "notes_per_minute": round(density, 3),
        "playable_density_flag": density_flag,
        "duplicate_count": duplicates,
        "duplicate_rate": round(duplicates / max(1, len(normalized)), 6),
        "chatter_count": chatter,
        "polyphony_event_rate": round(polyphony_frames / max(1, len(normalized)), 6),
        "max_polyphony": max_polyphony,
        "pitch_min": min(pitches) if pitches else None,
        "pitch_max": max(pitches) if pitches else None,
        "mean_duration_sec": round(_safe_mean(durations) or 0.0, 6),
        "piano_left_hand_notes": left_hand if role == "keys" else None,
        "piano_right_hand_notes": right_hand if role == "keys" else None,
    }


def compute_drum_gameplay_metrics(
    events: Sequence[DrumEvent] | Sequence[Mapping[str, Any]],
    *,
    duration_sec: float | None = None,
    overlap_window_sec: float = 0.025,
) -> dict[str, Any]:
    normalized: list[dict[str, float | int | str]] = []
    for event in events:
        if isinstance(event, Mapping):
            time = event.get("time", event.get("t"))
            note = event.get("note", event.get("class", event.get("drum_class")))
            velocity = event.get("velocity", 0)
        else:
            time = getattr(event, "time", None)
            note = getattr(event, "note", getattr(event, "drum_class", None))
            velocity = getattr(event, "velocity", 0)
        try:
            lane = str(note) if isinstance(note, str) and not str(note).isdigit() else str(int(note))
            normalized.append(
                {"time": max(0.0, float(time)), "note": lane, "velocity": int(velocity)}
            )
        except Exception:
            continue
    normalized.sort(key=lambda e: (float(e["time"]), str(e["note"])))
    if duration_sec is None:
        duration_sec = max((float(e["time"]) for e in normalized), default=0.0)
    duration_min = max(float(duration_sec or 0.0) / 60.0, 1e-9)

    duplicates = 0
    overlaps = 0
    last_by_note: dict[int, float] = {}
    for i, event in enumerate(normalized):
        note = str(event["note"])
        time = float(event["time"])
        if note in last_by_note and time - last_by_note[note] <= overlap_window_sec:
            duplicates += 1
        last_by_note[note] = time
        nearby = [
            other
            for other in normalized[max(0, i - 12): i + 13]
            if other is not event
            and abs(float(other["time"]) - time) <= overlap_window_sec
            and str(other["note"]) != note
        ]
        if nearby:
            overlaps += 1

    notes = {str(e["note"]) for e in normalized}
    return {
        "role": "drums",
        "event_count": len(normalized),
        "duration_sec": round(float(duration_sec or 0.0), 6),
        "events_per_minute": round(len(normalized) / duration_min, 3),
        "lane_coverage": len(notes),
        "lanes_present": sorted(notes),
        "duplicate_count": duplicates,
        "duplicate_rate": round(duplicates / max(1, len(normalized)), 6),
        "overlap_count": overlaps,
        "overlap_rate": round(overlaps / max(1, len(normalized)), 6),
    }


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 6)


def _count_metrics(*, label: str, reference_count: int, predicted_count: int, tp: int) -> dict[str, Any]:
    fp = max(0, int(predicted_count) - int(tp))
    fn = max(0, int(reference_count) - int(tp))
    precision = _ratio(int(tp), int(tp) + fp)
    recall = _ratio(int(tp), int(tp) + fn)
    f1 = 0.0 if precision + recall <= 0 else round((2.0 * precision * recall) / (precision + recall), 6)
    return {
        "label": str(label),
        "reference_count": int(reference_count),
        "predicted_count": int(predicted_count),
        "tp": int(tp),
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _pitch_label(pitch: int | str | None) -> str:
    try:
        midi = int(pitch)
    except Exception:
        return "unknown"
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    octave = (midi // 12) - 1
    return f"{names[midi % 12]}{octave}"


def _pitch_class_label(pitch: int | str | None) -> str:
    try:
        midi = int(pitch)
    except Exception:
        return "unknown"
    return ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")[midi % 12]


def _octave_label(pitch: int | str | None) -> str:
    try:
        midi = int(pitch)
    except Exception:
        return "unknown"
    return f"octave {(midi // 12) - 1}"


def _hand_zone_label(pitch: int | str | None) -> str:
    try:
        midi = int(pitch)
    except Exception:
        return "unknown"
    if midi < 48:
        return "low-left"
    if midi < 60:
        return "left"
    if midi < 72:
        return "right"
    return "high-right"


def _duration_bucket(duration_sec: float | int | None) -> str:
    try:
        duration = float(duration_sec)
    except Exception:
        return "unknown"
    if duration < 0.12:
        return "staccato"
    if duration < 0.55:
        return "short"
    if duration < 1.5:
        return "sustain"
    return "long-sustain"


def _event_time(value: Any) -> float:
    if isinstance(value, Mapping):
        raw = value.get("time", value.get("t_on", value.get("t", 0.0)))
    else:
        raw = getattr(value, "time", getattr(value, "t_on", 0.0))
    try:
        return max(0.0, float(raw))
    except Exception:
        return 0.0


def _event_pitch(value: Any) -> int | None:
    if isinstance(value, Mapping):
        raw = value.get("pitch", value.get("note"))
    else:
        raw = getattr(value, "pitch", getattr(value, "note", None))
    try:
        return int(raw)
    except Exception:
        return None


def _event_duration(value: Any) -> float:
    if isinstance(value, Mapping):
        if "duration" in value:
            raw = value.get("duration")
        else:
            raw = float(value.get("t_off", value.get("t_on", 0.0)) or 0.0) - float(value.get("t_on", 0.0) or 0.0)
    else:
        raw = getattr(value, "duration", None)
        if raw is None:
            raw = float(getattr(value, "t_off", 0.0) or 0.0) - float(getattr(value, "t_on", 0.0) or 0.0)
    try:
        return max(0.0, float(raw))
    except Exception:
        return 0.0


def _midi_pitch_to_hz(pitch: int | None) -> float:
    midi = 60 if pitch is None else int(pitch)
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def _notes_to_mir_arrays(notes: Sequence[Any]) -> tuple[Any, Any]:
    import numpy as np

    intervals: list[tuple[float, float]] = []
    pitches: list[float] = []
    for note in notes:
        start = _event_time(note)
        duration = _event_duration(note)
        end = start + max(0.001, duration)
        pitch = _event_pitch(note)
        if pitch is None:
            continue
        intervals.append((start, max(start + 0.001, end)))
        pitches.append(_midi_pitch_to_hz(pitch))
    return np.asarray(intervals, dtype=float).reshape((-1, 2)), np.asarray(pitches, dtype=float)


def _mir_prf_dict(values: Sequence[float]) -> dict[str, float]:
    precision, recall, f1, overlap = [float(v) for v in values]
    return {
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "average_overlap_ratio": round(overlap, 6),
    }


def evaluate_mir_eval_transcription(
    predicted_notes: Sequence[Any],
    reference_events: Sequence[Any],
    *,
    onset_tolerance_sec: float = 0.06,
    offset_ratio: float = 0.2,
    offset_min_tolerance_sec: float = 0.05,
) -> dict[str, Any]:
    """Evaluate note transcription with mir_eval when available.

    This is an optional standards-backed supplement to the native gameplay
    metrics. It never blocks the quality run when mir_eval is absent.
    """

    if importlib.util.find_spec("mir_eval") is None:
        return {
            "available": False,
            "backend": "mir_eval.transcription",
            "reason": "mir_eval is not installed",
        }
    if not reference_events:
        return {
            "available": True,
            "backend": "mir_eval.transcription",
            "status": "skipped",
            "reason": "no reference events",
        }

    transcription = importlib.import_module("mir_eval.transcription")
    ref_intervals, ref_pitches = _notes_to_mir_arrays(reference_events)
    est_intervals, est_pitches = _notes_to_mir_arrays(predicted_notes)
    if len(ref_intervals) == 0:
        return {
            "available": True,
            "backend": "mir_eval.transcription",
            "status": "skipped",
            "reason": "reference yielded no pitched intervals",
            "reference_count": 0,
            "predicted_count": int(len(est_intervals)),
        }
    if len(est_intervals) == 0:
        zero = {"precision": 0.0, "recall": 0.0, "f1": 0.0, "average_overlap_ratio": 0.0}
        return {
            "available": True,
            "backend": "mir_eval.transcription",
            "status": "ok",
            "reference_count": int(len(ref_intervals)),
            "predicted_count": 0,
            "onset": dict(zero),
            "onset_offset": dict(zero),
            "params": {
                "onset_tolerance_sec": float(onset_tolerance_sec),
                "offset_ratio": float(offset_ratio),
                "offset_min_tolerance_sec": float(offset_min_tolerance_sec),
            },
        }

    onset = transcription.precision_recall_f1_overlap(
        ref_intervals,
        ref_pitches,
        est_intervals,
        est_pitches,
        onset_tolerance=float(onset_tolerance_sec),
        offset_ratio=None,
    )
    onset_offset = transcription.precision_recall_f1_overlap(
        ref_intervals,
        ref_pitches,
        est_intervals,
        est_pitches,
        onset_tolerance=float(onset_tolerance_sec),
        offset_ratio=float(offset_ratio),
        offset_min_tolerance=float(offset_min_tolerance_sec),
    )
    return {
        "available": True,
        "backend": "mir_eval.transcription",
        "status": "ok",
        "reference_count": int(len(ref_intervals)),
        "predicted_count": int(len(est_intervals)),
        "onset": _mir_prf_dict(onset),
        "onset_offset": _mir_prf_dict(onset_offset),
        "params": {
            "onset_tolerance_sec": float(onset_tolerance_sec),
            "offset_ratio": float(offset_ratio),
            "offset_min_tolerance_sec": float(offset_min_tolerance_sec),
        },
    }


def _event_drum_label(value: Any) -> str:
    if isinstance(value, Mapping):
        raw = value.get("drum_class", value.get("class", value.get("note", "unknown")))
    else:
        raw = getattr(value, "drum_class", getattr(value, "note", "unknown"))
    if isinstance(raw, str) and not raw.isdigit():
        return raw
    mapping = {
        35: "kick",
        36: "kick",
        37: "snare",
        38: "snare",
        39: "snare",
        40: "snare",
        42: "hi_hat",
        44: "hi_hat",
        46: "hi_hat",
        49: "crash",
        51: "ride",
        57: "crash",
        59: "ride",
    }
    try:
        note = int(raw)
    except Exception:
        return "unknown"
    if note in mapping:
        return mapping[note]
    if note in {41, 43, 45, 47, 48, 50}:
        return "tom"
    return str(note)


def _classifier_match_dimension(
    *,
    name: str,
    predicted: Sequence[Any],
    reference: Sequence[Any],
    tolerance_sec: float,
    label_for: Any,
    pitch_aware: bool = False,
) -> dict[str, Any]:
    pred_items = [
        {
            "idx": idx,
            "time": _event_time(item),
            "label": str(label_for(item)),
            "pitch": _event_pitch(item),
            "duration": _event_duration(item),
        }
        for idx, item in enumerate(predicted)
    ]
    ref_items = [
        {
            "idx": idx,
            "time": _event_time(item),
            "label": str(label_for(item)),
            "pitch": _event_pitch(item),
            "duration": _event_duration(item),
        }
        for idx, item in enumerate(reference)
    ]
    matched_pred: set[int] = set()
    matched_ref: set[int] = set()
    exact_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for pred in sorted(pred_items, key=lambda item: item["time"]):
        candidates = [
            ref
            for ref in ref_items
            if ref["idx"] not in matched_ref
            and ref["label"] == pred["label"]
            and abs(float(ref["time"]) - float(pred["time"])) <= tolerance_sec
        ]
        if not candidates:
            continue
        ref = min(candidates, key=lambda item: abs(float(item["time"]) - float(pred["time"])))
        matched_pred.add(int(pred["idx"]))
        matched_ref.add(int(ref["idx"]))
        exact_pairs.append((pred, ref))

    confusion_counts: dict[tuple[str, str], int] = {}
    semitone_near = 0
    octave_errors = 0
    for pred in sorted(pred_items, key=lambda item: item["time"]):
        if pred["idx"] in matched_pred:
            continue
        candidates = [
            ref
            for ref in ref_items
            if ref["idx"] not in matched_ref
            and abs(float(ref["time"]) - float(pred["time"])) <= tolerance_sec
        ]
        if not candidates:
            continue
        ref = min(candidates, key=lambda item: abs(float(item["time"]) - float(pred["time"])))
        matched_pred.add(int(pred["idx"]))
        matched_ref.add(int(ref["idx"]))
        key = (str(ref["label"]), str(pred["label"]))
        confusion_counts[key] = confusion_counts.get(key, 0) + 1
        if pitch_aware and ref.get("pitch") is not None and pred.get("pitch") is not None:
            delta = abs(int(pred["pitch"]) - int(ref["pitch"]))
            if delta <= 1:
                semitone_near += 1
            elif delta % 12 == 0:
                octave_errors += 1

    labels = sorted({str(item["label"]) for item in [*pred_items, *ref_items]})
    class_metrics: list[dict[str, Any]] = []
    for label in labels:
        ref_count = sum(1 for item in ref_items if item["label"] == label)
        pred_count = sum(1 for item in pred_items if item["label"] == label)
        tp = sum(1 for pred, _ref in exact_pairs if pred["label"] == label)
        class_metrics.append(
            _count_metrics(
                label=label,
                reference_count=ref_count,
                predicted_count=pred_count,
                tp=tp,
            )
        )
    class_metrics.sort(key=lambda item: (float(item["f1"]), -int(item["reference_count"])))

    return {
        "name": name,
        "class_metrics": class_metrics,
        "confusions": [
            {"reference_class": ref, "predicted_class": pred, "count": count}
            for (ref, pred), count in sorted(confusion_counts.items(), key=lambda item: item[1], reverse=True)
        ],
        "matched_tp": len(exact_pairs),
        "matched_confusions": sum(confusion_counts.values()),
        "unmatched_predictions": sum(1 for item in pred_items if item["idx"] not in matched_pred),
        "unmatched_references": sum(1 for item in ref_items if item["idx"] not in matched_ref),
        "pitch_error_summary": {
            "semitone_near_confusions": semitone_near if pitch_aware else None,
            "octave_confusions": octave_errors if pitch_aware else None,
        },
        "_tp_times": [float(ref["time"]) for _pred, ref in exact_pairs],
        "_fp_times": [float(item["time"]) for item in pred_items if item["idx"] not in {pred["idx"] for pred, _ref in exact_pairs}],
        "_fn_times": [float(item["time"]) for item in ref_items if item["idx"] not in {ref["idx"] for _pred, ref in exact_pairs}],
        "_duration": max(
            [float(item["time"]) + float(item.get("duration", 0.0)) for item in [*pred_items, *ref_items]]
            or [0.0]
        ),
    }


def _timeline_buckets(
    *,
    tp_times: Sequence[float],
    fp_times: Sequence[float],
    fn_times: Sequence[float],
    duration_sec: float,
    bucket_count: int = 24,
) -> list[dict[str, Any]]:
    duration = max(0.001, float(duration_sec or 0.0))
    buckets = [
        {
            "start": round((idx / bucket_count) * duration, 6),
            "end": round(((idx + 1) / bucket_count) * duration, 6),
            "tp": 0,
            "fp": 0,
            "fn": 0,
        }
        for idx in range(bucket_count)
    ]

    def add(times: Sequence[float], key: str) -> None:
        for raw_time in times:
            idx = min(bucket_count - 1, max(0, int((float(raw_time) / duration) * bucket_count)))
            buckets[idx][key] += 1

    add(tp_times, "tp")
    add(fp_times, "fp")
    add(fn_times, "fn")
    return buckets


def _strip_internal_dimension_fields(dimension: Mapping[str, Any]) -> dict[str, Any]:
    return {str(k): v for k, v in dimension.items() if not str(k).startswith("_")}


def _drum_classifier_payload(
    result: Mapping[str, Any],
    reference_events: Sequence[Any],
    *,
    tolerance_sec: float,
) -> dict[str, Any]:
    predicted = list(result.get("predicted_events", []))
    primary = _classifier_match_dimension(
        name="drum_lane",
        predicted=predicted,
        reference=reference_events,
        tolerance_sec=tolerance_sec,
        label_for=_event_drum_label,
    )
    timeline = _timeline_buckets(
        tp_times=primary["_tp_times"],
        fp_times=primary["_fp_times"],
        fn_times=primary["_fn_times"],
        duration_sec=float(primary["_duration"]),
    )
    return {
        "kind": "drums",
        "primary_dimension": "drum_lane",
        "dimensions": [_strip_internal_dimension_fields(primary)],
        "timeline": timeline,
        "coverage": {
            "reference_events": len(reference_events),
            "predicted_events": len(predicted),
        },
    }


def _melodic_classifier_payload(
    predicted_notes: Sequence[Any],
    reference_events: Sequence[Any],
    *,
    role: str,
    tolerance_sec: float,
) -> dict[str, Any]:
    primary = _classifier_match_dimension(
        name="pitch",
        predicted=predicted_notes,
        reference=reference_events,
        tolerance_sec=tolerance_sec,
        label_for=lambda item: _pitch_label(_event_pitch(item)),
        pitch_aware=True,
    )
    pitch_class = _classifier_match_dimension(
        name="pitch_class",
        predicted=predicted_notes,
        reference=reference_events,
        tolerance_sec=tolerance_sec,
        label_for=lambda item: _pitch_class_label(_event_pitch(item)),
    )
    octave = _classifier_match_dimension(
        name="octave",
        predicted=predicted_notes,
        reference=reference_events,
        tolerance_sec=tolerance_sec,
        label_for=lambda item: _octave_label(_event_pitch(item)),
    )
    dimensions = [primary, pitch_class, octave]
    if role in {"keys", "piano", "synth"}:
        dimensions.append(
            _classifier_match_dimension(
                name="hand_zone",
                predicted=predicted_notes,
                reference=reference_events,
                tolerance_sec=tolerance_sec,
                label_for=lambda item: _hand_zone_label(_event_pitch(item)),
            )
        )
        dimensions.append(
            _classifier_match_dimension(
                name="sustain_bucket",
                predicted=predicted_notes,
                reference=reference_events,
                tolerance_sec=tolerance_sec,
                label_for=lambda item: _duration_bucket(_event_duration(item)),
            )
        )
    timeline = _timeline_buckets(
        tp_times=primary["_tp_times"],
        fp_times=primary["_fp_times"],
        fn_times=primary["_fn_times"],
        duration_sec=float(primary["_duration"]),
    )
    return {
        "kind": "piano" if role in {"keys", "piano", "synth"} else "melodic",
        "primary_dimension": "pitch",
        "dimensions": [_strip_internal_dimension_fields(dimension) for dimension in dimensions],
        "timeline": timeline,
        "coverage": {
            "reference_events": len(reference_events),
            "predicted_events": len(predicted_notes),
        },
    }


def _find_first_module(module_names: Sequence[str]) -> tuple[bool, str | None]:
    for module_name in module_names:
        if importlib.util.find_spec(module_name) is not None:
            return True, module_name
    return False, None


def _env_file_status(env_vars: Sequence[str]) -> dict[str, Any]:
    status: dict[str, Any] = {}
    for env_var in env_vars:
        raw = os.environ.get(env_var, "").strip()
        path = Path(raw).expanduser() if raw else None
        status[env_var] = {
            "configured": bool(raw),
            "path": str(path) if path is not None else None,
            "exists": bool(path and path.is_file()),
        }
    return status


def _env_value_status(env_vars: Sequence[str]) -> dict[str, Any]:
    return {
        env_var: {
            "configured": bool(os.environ.get(env_var, "").strip()),
            "value": os.environ.get(env_var, "").strip() or None,
        }
        for env_var in env_vars
    }


def _contract_ok(contract: Mapping[str, Any], module_available: bool, env_files: Mapping[str, Any], env_values: Mapping[str, Any]) -> bool:
    modules_required = bool(contract.get("module_names"))
    if modules_required and not module_available:
        return False
    for env_var in contract.get("checkpoint_env", ()):
        item = env_files.get(env_var, {})
        if item.get("configured") and not item.get("exists"):
            return False
    allow_download_env = str(contract.get("allow_download_env") or "").strip()
    if contract.get("checkpoint_env") and allow_download_env:
        has_checkpoint = any(item.get("exists") for item in env_files.values())
        allow_download = os.environ.get(allow_download_env, "").strip().lower() in {"1", "true", "yes"}
        if not has_checkpoint and not allow_download:
            return False
    for env_var in contract.get("command_env", ()):
        item = env_values.get(env_var, {})
        if item.get("configured") is False and contract.get("methods"):
            return False
    if contract.get("checkpoint_env") and str(contract.get("display_name", "")).lower().startswith("hft"):
        return bool(all(item.get("exists") for item in env_files.values())) and bool(
            all(item.get("configured") for item in env_values.values())
        )
    return True


def inspect_optional_model_backends(
    search_roots: Iterable[Path | str] | None = None,
) -> dict[str, dict[str, Any]]:
    roots = list(search_roots or [])
    mt3_engines = available_mt3_modelpacks(roots or None)
    out: dict[str, dict[str, Any]] = {
        "mt3": {
            "ok": any(item.get("ok") for item in mt3_engines.values()),
            "engines": mt3_engines,
            "methods": list(mt3_engines),
            "roles": ["drums"],
            "adapter_contract": {
                "availability_check": True,
                "modelpack_resolution": True,
                "decode": "temp_midi",
                "instrument_tagging": True,
                "clear_failure": True,
                "portable_safe_absence": True,
            },
            "failure_mode": "clear FileNotFoundError/RuntimeError, no portable requirement",
        }
    }
    basic_pitch_model = resolve_basic_pitch_model_path(roots) if roots else None
    for backend_id, contract in OPTIONAL_MODEL_CONTRACTS.items():
        module_available, module_name = _find_first_module(tuple(contract.get("module_names", ())))
        env_files = _env_file_status(tuple(contract.get("checkpoint_env", ())))
        env_values = _env_value_status(tuple(contract.get("command_env", ())))
        out[backend_id] = {
            "ok": _contract_ok(contract, module_available, env_files, env_values),
            "display_name": contract.get("display_name", backend_id),
            "module": module_name,
            "module_candidates": list(contract.get("module_names", ())),
            "module_available": module_available,
            "roles": list(contract.get("roles", ())),
            "methods": list(contract.get("methods", ())),
            "env_files": env_files,
            "env_values": env_values,
            "model_path": str(basic_pitch_model) if backend_id == "basic_pitch" and basic_pitch_model else None,
            "adapter_contract": {
                "availability_check": True,
                "modelpack_resolution": bool(contract.get("modelpack_resolution")),
                "decode": contract.get("decode"),
                "instrument_tagging": True,
                "clear_failure": True,
                "portable_safe_absence": True,
            },
            "failure_mode": contract.get("failure_mode"),
        }
    return out


def scan_corpus(root: Path | str) -> dict[str, Any]:
    root_path = Path(root)
    songpacks: list[dict[str, Any]] = []
    split_stem_folders: list[dict[str, Any]] = []
    benchmark_artifacts: list[dict[str, Any]] = []
    for manifest_path in sorted(root_path.rglob("manifest.json")):
        if _is_ignored_corpus_path(manifest_path):
            continue
        try:
            manifest = json.loads(manifest_path.read_text("utf-8"))
        except Exception:
            continue
        songpack_root = manifest_path.parent
        stems = {}
        for role in ("drums", "bass", "guitar", "lead_guitar", "rhythm_guitar", "keys", "vocals"):
            for candidate in (
                songpack_root / "audio" / "stems" / f"{role}.wav",
                songpack_root / "audio" / "stems" / f"{role.title()}.wav",
            ):
                if candidate.is_file():
                    stems[role] = str(candidate)
                    break
        songpacks.append(
            {
                "path": str(songpack_root),
                "title": manifest.get("title"),
                "song_id": manifest.get("song_id"),
                "duration_sec": manifest.get("duration_sec"),
                "has_notes_mid": (songpack_root / "features" / "notes.mid").is_file(),
                "midi_files": [str(p) for p in sorted((songpack_root / "features").glob("*.mid"))],
                "stems": stems,
                "transcription": manifest.get("pipeline", {}).get("transcription", {}),
            }
        )

    for folder in sorted(root_path.rglob("*")):
        if not folder.is_dir() or _is_ignored_corpus_path(folder):
            continue
        wavs = {p.stem.lower(): p for p in folder.glob("*.wav")}
        roles = sorted(role for role in ("drums", "bass", "guitar", "keys", "vocals") if role in wavs)
        if len(roles) >= 2:
            split_stem_folders.append(
                {
                    "path": str(folder),
                    "roles": roles,
                    "stems": {role: str(wavs[role]) for role in roles},
                }
            )

    for summary_path in sorted(root_path.rglob("summary.json")):
        if _is_ignored_corpus_path(summary_path):
            continue
        parts = {part.lower() for part in summary_path.parts}
        if "benchmarks" not in parts:
            continue
        run_dir = summary_path.parent
        benchmark_artifacts.append(
            {
                "path": str(run_dir),
                "summary": str(summary_path),
                "has_report_md": (run_dir / "report.md").is_file(),
                "has_report_html": (run_dir / "report.html").is_file(),
                "svg_count": len(list(run_dir.glob("*.svg"))),
                "prediction_midi_count": len(list((run_dir / "predictions").rglob("*.mid")))
                if (run_dir / "predictions").is_dir()
                else 0,
            }
        )

    return {
        "root": str(root_path),
        "songpack_count": len(songpacks),
        "split_stem_folder_count": len(split_stem_folders),
        "benchmark_artifact_count": len(benchmark_artifacts),
        "songpacks": songpacks,
        "split_stem_folders": split_stem_folders,
        "benchmark_artifacts": benchmark_artifacts,
    }


def _songpack_reference_midi(songpack_root: Path, role: str) -> Path | None:
    feature_root = songpack_root / "features"
    candidates = [
        feature_root / f"{role}.mid",
        feature_root / f"{role}.midi",
        feature_root / f"{role}_reference.mid",
        feature_root / "notes.mid",
    ]
    if role == "keys":
        candidates.extend([feature_root / "piano.mid", feature_root / "keyboard.mid"])
    if role == "drums":
        candidates.extend([feature_root / "events.mid", feature_root / "drums.mid"])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _split_folder_reference_midi(folder: Path, role: str) -> Path | None:
    candidates = [
        folder / f"{role}.mid",
        folder / f"{role}.midi",
        folder / f"{role}_reference.mid",
        folder / "features" / f"{role}.mid",
        folder / "features" / "notes.mid",
        folder / "notes.mid",
    ]
    if role == "keys":
        candidates.extend([folder / "piano.mid", folder / "keyboard.mid"])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _current_method_for_role(transcription: Mapping[str, Any], role: str) -> str | None:
    if role == "drums":
        for key in ("drum_filter_used", "drum_engine_used", "drum_filter", "drum_engine"):
            value = transcription.get(key)
            if value:
                return str(value)
        return None

    instrument_methods = transcription.get("instrument_melodic_methods")
    if isinstance(instrument_methods, Mapping):
        value = instrument_methods.get(role)
        if value:
            return str(value)

    for key in ("melodic_method_used", "melodic_method", "method"):
        value = transcription.get(key)
        if value:
            return str(value)
    return None


def build_quality_manifest_from_scan(
    scan_payload: Mapping[str, Any],
    *,
    include_unreferenced: bool = True,
) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()

    for songpack in scan_payload.get("songpacks", []):
        if not isinstance(songpack, Mapping):
            continue
        songpack_path = Path(str(songpack.get("path", "")))
        song_id = str(songpack.get("song_id") or songpack_path.stem)
        title = str(songpack.get("title") or songpack_path.stem)
        transcription = songpack.get("transcription", {})
        if not isinstance(transcription, Mapping):
            transcription = {}
        stems = songpack.get("stems", {})
        if not isinstance(stems, Mapping):
            continue
        for raw_role, raw_stem in sorted(stems.items()):
            role = _case_role_name(str(raw_role))
            stem = Path(str(raw_stem))
            reference = _songpack_reference_midi(songpack_path, role)
            if reference is None and not include_unreferenced:
                continue
            case_id = _unique_case_id(
                f"{song_id}-{role}",
                seen_case_ids,
                suffix=stem.stem if role in {"rhythm_guitar", "lead_guitar", "guitar"} else None,
            )
            cases.append(
                {
                    "id": case_id,
                    "name": f"{title} - {role}",
                    "family": _role_family(role),
                    "role": role,
                    "wav": str(stem),
                    "reference_midi": str(reference) if reference is not None else None,
                    "offset_sec": 0.0,
                    "duration_sec": songpack.get("duration_sec"),
                    "source": "songpack",
                    "source_songpack": str(songpack_path),
                    "stem_provenance": "songpack_audio_stems",
                    "current_method": _current_method_for_role(transcription, role),
                }
            )

    for folder in scan_payload.get("split_stem_folders", []):
        if not isinstance(folder, Mapping):
            continue
        folder_path = Path(str(folder.get("path", "")))
        stems = folder.get("stems", {})
        if not isinstance(stems, Mapping):
            continue
        for raw_role, raw_stem in sorted(stems.items()):
            role = _case_role_name(str(raw_role))
            stem = Path(str(raw_stem))
            reference = _split_folder_reference_midi(folder_path, role)
            if reference is None and not include_unreferenced:
                continue
            case_id = _unique_case_id(
                f"{folder_path.name}-{role}",
                seen_case_ids,
                suffix=stem.stem if role in {"rhythm_guitar", "lead_guitar", "guitar"} else None,
            )
            cases.append(
                {
                    "id": case_id,
                    "name": f"{folder_path.name} - {role}",
                    "family": _role_family(role),
                    "role": role,
                    "wav": str(stem),
                    "reference_midi": str(reference) if reference is not None else None,
                    "offset_sec": 0.0,
                    "duration_sec": None,
                    "source": "split_stem_folder",
                    "source_folder": str(folder_path),
                    "stem_provenance": "pre_split_folder",
                    "current_method": None,
                }
            )

    cases.sort(key=lambda item: (str(item.get("id")), str(item.get("role"))))
    return {
        "format": "auralprimer_quality_manifest.v1",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_root": scan_payload.get("root"),
        "include_unreferenced": bool(include_unreferenced),
        "notes": [
            "Generated from scan_corpus; review case coverage before using as a promotion gate.",
            "Cases without reference_midi can still report gameplay metrics but not reference F1.",
        ],
        "cases": cases,
    }


def write_quality_manifest_from_scan(
    root: Path | str,
    output_path: Path | str,
    *,
    include_unreferenced: bool = True,
) -> Path:
    scan_payload = scan_corpus(root)
    manifest = build_quality_manifest_from_scan(
        scan_payload,
        include_unreferenced=include_unreferenced,
    )
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, default=_json_default) + "\n", encoding="utf-8")
    return out


def load_quality_manifest(path: Path | str) -> list[QualityCase]:
    manifest_path = Path(path)
    payload = json.loads(manifest_path.read_text("utf-8"))
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError(f"quality manifest must contain cases[]: {manifest_path}")
    cases: list[QualityCase] = []
    for item in raw_cases:
        if not isinstance(item, Mapping):
            continue
        wav_path = Path(str(item.get("wav", "")))
        ref_raw = item.get("reference") or item.get("reference_midi")
        family = str(item.get("family") or item.get("kind") or "auto").strip().lower()
        role = str(item.get("role") or item.get("instrument") or "melodic").strip().lower()
        cases.append(
            QualityCase(
                case_id=str(item.get("id") or wav_path.stem),
                name=str(item.get("name") or item.get("title") or wav_path.stem),
                role=role,
                family=family,
                wav_path=wav_path,
                reference_path=Path(str(ref_raw)) if ref_raw else None,
                offset_sec=float(item.get("offset_sec", 0.0) or 0.0),
            )
        )
    if not cases:
        raise ValueError(f"quality manifest has no usable cases: {manifest_path}")
    return cases


def filter_quality_cases(
    cases: Sequence[QualityCase],
    *,
    case_filters: Sequence[str] | None = None,
    roles: Sequence[str] | None = None,
    max_cases: int | None = None,
) -> list[QualityCase]:
    patterns = [str(item).strip().lower() for item in case_filters or [] if str(item).strip()]
    role_set = {_case_role_name(str(item)) for item in roles or [] if str(item).strip()}
    out: list[QualityCase] = []
    for case in cases:
        role = _case_role_name(case.role)
        if role_set and role not in role_set:
            continue
        if patterns:
            haystack = " ".join(
                [
                    case.case_id,
                    case.name or "",
                    str(case.wav_path),
                    str(case.reference_path or ""),
                    case.family,
                    case.role,
                ]
            ).lower()
            if not any(pattern in haystack for pattern in patterns):
                continue
        out.append(case)
        if max_cases is not None and max_cases > 0 and len(out) >= max_cases:
            break
    return out


def _algorithms_for_case(case: QualityCase, profile: str, override: Sequence[str] | None) -> list[str]:
    if override:
        return _dedupe(str(item).strip().lower() for item in override)
    if case.role == "drums" or case.family == "drums":
        return drum_engines_for_profile(profile)
    role = "keys" if case.role in {"keys", "piano", "synth"} else case.role
    return melodic_methods_for_profile(profile, role)


def run_quality_benchmark_suite(
    cases: Sequence[QualityCase],
    *,
    profile: str = "gameplay_default",
    algorithms: Sequence[str] | None = None,
    tolerance_ms: float = 60.0,
) -> dict[str, Any]:
    normalized_profile = validate_transcription_profile(profile)
    if normalized_profile is None:
        raise ValueError(f"unknown transcription profile: {profile}")
    tolerance_sec = float(tolerance_ms) / 1000.0
    drum_registry = build_default_drum_algorithm_registry()
    case_payloads: list[dict[str, Any]] = []

    for case in cases:
        method_ids = _algorithms_for_case(case, normalized_profile, algorithms)
        if case.role == "drums" or case.family == "drums":
            reference, reference_meta = ([], {})
            if case.reference_path and case.reference_path.is_file():
                reference, reference_meta = load_drum_reference(case.reference_path)
            results = benchmark_algorithms(
                case.wav_path,
                reference,
                method_ids,
                drum_registry,
                tolerance_sec=tolerance_sec,
            )
            for result in results:
                result["gameplay"] = compute_drum_gameplay_metrics(
                    [
                        {
                            "time": event.get("time", 0.0),
                            "note": event.get("note", 0),
                            "velocity": event.get("velocity", 0),
                        }
                        for event in result.get("predicted_events", [])
                    ]
                )
                first_pred = _first_drum_time(result.get("predicted_events", []))
                first_ref = min((ev.time for ev in reference), default=None)
                result["sync"] = classify_start_offset(first_pred, first_ref)
                result["classifier"] = _drum_classifier_payload(
                    result,
                    reference,
                    tolerance_sec=tolerance_sec,
                )
            reference_count = len(reference)
        elif case.role in {"keys", "piano", "synth"} or case.family == "piano":
            reference_piano = None
            if case.reference_path and case.reference_path.is_file():
                reference_piano = parse_piano_midi_reference(
                    case.reference_path,
                    case.offset_sec,
                    role="keys",
                )
            results = benchmark_piano_algorithms(
                case.wav_path,
                reference_piano,
                method_ids,
                instrument="keys",
                tolerance_sec=tolerance_sec,
            )
            for result in results:
                notes = result.get("predicted_notes", [])
                result["gameplay"] = compute_melodic_gameplay_metrics(notes, role="keys")
                result["sync"] = classify_start_offset(
                    _first_note_time(notes),
                    min((ev.time for ev in reference_piano or []), default=None),
                )
                result["classifier"] = _melodic_classifier_payload(
                    notes,
                    reference_piano or [],
                    role="keys",
                    tolerance_sec=tolerance_sec,
                )
                result["mir_eval"] = evaluate_mir_eval_transcription(
                    notes,
                    reference_piano or [],
                    onset_tolerance_sec=tolerance_sec,
                )
            reference_count = len(reference_piano or [])
            reference_meta = {}
        else:
            reference_melodic = []
            if case.reference_path and case.reference_path.is_file():
                reference_melodic = parse_melodic_midi_reference(
                    case.reference_path,
                    case.offset_sec,
                    role=case.role,
                )
            results = benchmark_melodic_algorithms(
                case.wav_path,
                reference_melodic,
                method_ids,
                instrument=case.role,
                tolerance_sec=tolerance_sec,
            )
            for result in results:
                notes = result.get("predicted_notes", [])
                result["gameplay"] = compute_melodic_gameplay_metrics(notes, role=case.role)
                result["sync"] = classify_start_offset(
                    _first_note_time(notes),
                    min((ev.time for ev in reference_melodic), default=None),
                )
                result["classifier"] = _melodic_classifier_payload(
                    notes,
                    reference_melodic,
                    role=case.role,
                    tolerance_sec=tolerance_sec,
                )
                result["mir_eval"] = evaluate_mir_eval_transcription(
                    notes,
                    reference_melodic,
                    onset_tolerance_sec=tolerance_sec,
                )
            reference_count = len(reference_melodic)
            reference_meta = {}

        case_payloads.append(
            {
                "case_id": case.case_id,
                "name": case.name or case.case_id,
                "role": case.role,
                "family": case.family,
                "wav_path": str(case.wav_path),
                "reference_path": str(case.reference_path) if case.reference_path else None,
                "reference_count": reference_count,
                "reference_meta": reference_meta,
                "algorithms": method_ids,
                "results": results,
            }
        )

    return {
        "suite_version": QUALITY_SUITE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "profile": transcription_profile_metadata(normalized_profile),
        "tolerance_ms": float(tolerance_ms),
        "quality_metric_backends": inspect_quality_metric_backends(),
        "dataset_sources": inspect_quality_dataset_sources(),
        "model_backends": inspect_optional_model_backends(),
        "cases": case_payloads,
    }


def summarize_quality_results(payload: Mapping[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    worst_failures: list[dict[str, Any]] = []
    for case in payload.get("cases", []):
        for result in case.get("results", []):
            overall = result.get("overall", {})
            gameplay = result.get("gameplay", {})
            sync = result.get("sync", {})
            mir_eval = result.get("mir_eval", {}) or {}
            mir_onset = mir_eval.get("onset", {}) if isinstance(mir_eval, Mapping) else {}
            mir_onset_offset = mir_eval.get("onset_offset", {}) if isinstance(mir_eval, Mapping) else {}
            f1 = overall.get("f1")
            row = {
                "case_id": case.get("case_id"),
                "role": case.get("role"),
                "algorithm": result.get("algorithm"),
                "f1": f1,
                "mir_eval_onset_f1": mir_onset.get("f1") if isinstance(mir_onset, Mapping) else None,
                "mir_eval_onset_offset_f1": (
                    mir_onset_offset.get("f1") if isinstance(mir_onset_offset, Mapping) else None
                ),
                "note_count": result.get("note_count"),
                "error": bool(result.get("error")),
                "gameplay_flags": {
                    "error": bool(result.get("error")),
                    "density": bool(gameplay.get("playable_density_flag", False)),
                    "sync_quarantine": bool(sync.get("quarantine", False)),
                    "duplicates": float(gameplay.get("duplicate_rate", 0.0) or 0.0) > 0.08,
                },
            }
            rows.append(row)
            if any(row["gameplay_flags"].values()):
                worst_failures.append(row)

    by_algorithm: dict[str, list[float]] = {}
    mir_onset_by_algorithm: dict[str, list[float]] = {}
    mir_offset_by_algorithm: dict[str, list[float]] = {}
    for row in rows:
        if row["f1"] is None:
            pass
        else:
            by_algorithm.setdefault(str(row["algorithm"]), []).append(float(row["f1"]))
        if row.get("mir_eval_onset_f1") is not None:
            mir_onset_by_algorithm.setdefault(str(row["algorithm"]), []).append(float(row["mir_eval_onset_f1"]))
        if row.get("mir_eval_onset_offset_f1") is not None:
            mir_offset_by_algorithm.setdefault(str(row["algorithm"]), []).append(
                float(row["mir_eval_onset_offset_f1"])
            )

    promotion_candidates: list[dict[str, Any]] = []
    by_role_algorithm: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        role = str(row.get("role"))
        algorithm = str(row.get("algorithm"))
        by_role_algorithm.setdefault((role, algorithm), []).append(row)

    by_role: dict[str, list[dict[str, Any]]] = {}
    for (role, algorithm), alg_rows in by_role_algorithm.items():
        f1_values = [float(row["f1"]) for row in alg_rows if row.get("f1") is not None]
        if not f1_values:
            continue
        flag_count = sum(
            1
            for row in alg_rows
            if any(bool(value) for value in row.get("gameplay_flags", {}).values())
        )
        by_role.setdefault(role, []).append(
            {
                "role": role,
                "algorithm": algorithm,
                "mean_f1": round(float(statistics.fmean(f1_values)), 6),
                "case_count": len(f1_values),
                "flagged_case_count": flag_count,
            }
        )

    for role, candidates in sorted(by_role.items()):
        winner = sorted(
            candidates,
            key=lambda item: (float(item["mean_f1"]), -int(item["flagged_case_count"])),
            reverse=True,
        )[0]
        blocked = int(winner["flagged_case_count"]) > 0
        promotion_candidates.append(
            {
                **winner,
                "benchmark_winner": True,
                "promotion_status": (
                    "blocked_by_gameplay_flags" if blocked else "benchmark_winner_review_required"
                ),
                "can_promote_without_review": False,
                "review_required": True,
            }
        )

    return {
        "algorithm_summaries": [
            {
                "algorithm": algorithm,
                "mean_f1": round(float(statistics.fmean(values)), 6) if values else None,
                "mir_eval_mean_onset_f1": (
                    round(float(statistics.fmean(mir_onset_by_algorithm.get(algorithm, []))), 6)
                    if mir_onset_by_algorithm.get(algorithm)
                    else None
                ),
                "mir_eval_mean_onset_offset_f1": (
                    round(float(statistics.fmean(mir_offset_by_algorithm.get(algorithm, []))), 6)
                    if mir_offset_by_algorithm.get(algorithm)
                    else None
                ),
                "case_count": len(values),
            }
            for algorithm, values in sorted(by_algorithm.items())
        ],
        "promotion_candidates": promotion_candidates,
        "worst_failures": worst_failures[:25],
        "rows": rows,
    }


def render_quality_report_markdown(payload: Mapping[str, Any], summary: Mapping[str, Any]) -> str:
    profile = payload.get("profile", {})
    lines = ["# Full Transcription Quality Report", ""]
    lines.append(f"Generated: {payload.get('generated_at_utc')}")
    lines.append(f"Profile: {profile.get('profile')}")
    lines.append(f"Tolerance: {payload.get('tolerance_ms')}ms")
    lines.append("")
    lines.append("## Artifacts")
    lines.append("")
    lines.append("- `summary.json`: raw payload plus computed summary")
    lines.append("- `f1_heatmap.svg`: case/method F1 matrix")
    lines.append("- `gameplay_risk_heatmap.svg`: density, duplicate, sync, and error flag matrix")
    lines.append("- `classifier_performance.html`: self-contained classifier explorer, no server required")
    lines.append("")
    lines.append("## Quality Metric Backends")
    lines.append("")
    lines.append("| Backend | OK | Purpose | Protocols | Failure Mode |")
    lines.append("|---|---:|---|---|---|")
    for backend_id, backend in sorted((payload.get("quality_metric_backends") or {}).items()):
        protocols = ", ".join(str(item) for item in backend.get("protocols", [])) or "-"
        lines.append(
            f"| {backend_id} | {bool(backend.get('ok'))} | {backend.get('purpose', '-')} | "
            f"{protocols} | {backend.get('failure_mode', '-')} |"
        )
    lines.append("")
    lines.append("## Research Dataset Sources")
    lines.append("")
    lines.append("| Dataset | Available | Purpose | Root Env | Product Stance |")
    lines.append("|---|---:|---|---|---|")
    for dataset_id, dataset in sorted((payload.get("dataset_sources") or {}).items()):
        roots = ", ".join(str(item.get("env_var")) for item in dataset.get("roots", [])) or "-"
        lines.append(
            f"| {dataset_id} | {bool(dataset.get('ok'))} | {dataset.get('purpose', '-')} | "
            f"{roots} | {dataset.get('ship_policy', '-')} |"
        )
    lines.append("")
    lines.append("## Optional Model Backends")
    lines.append("")
    lines.append("| Backend | OK | Methods | Failure Mode |")
    lines.append("|---|---:|---|---|")
    for backend_id, backend in sorted((payload.get("model_backends") or {}).items()):
        methods = ", ".join(str(m) for m in backend.get("methods", [])) or "-"
        lines.append(
            f"| {backend_id} | {bool(backend.get('ok'))} | {methods} | {backend.get('failure_mode', '-')} |"
        )
    lines.append("")
    lines.append("## Algorithm Summary")
    lines.append("")
    lines.append("| Algorithm | Mean F1 | MIR Onset F1 | MIR Onset+Offset F1 | Cases |")
    lines.append("|---|---:|---:|---:|---:|")
    for row in summary.get("algorithm_summaries", []):
        mean_f1 = row.get("mean_f1")
        mean_text = "n/a" if mean_f1 is None else f"{float(mean_f1):.3f}"
        mir_onset = row.get("mir_eval_mean_onset_f1")
        mir_offset = row.get("mir_eval_mean_onset_offset_f1")
        mir_onset_text = "n/a" if mir_onset is None else f"{float(mir_onset):.3f}"
        mir_offset_text = "n/a" if mir_offset is None else f"{float(mir_offset):.3f}"
        lines.append(
            f"| {row.get('algorithm')} | {mean_text} | {mir_onset_text} | {mir_offset_text} | "
            f"{row.get('case_count', 0)} |"
        )
    lines.append("")
    lines.append("## Promotion Candidates")
    lines.append("")
    candidates = summary.get("promotion_candidates", [])
    if not candidates:
        lines.append("No benchmark winners can be labeled because no reference F1 values were available.")
    else:
        lines.append("| Role | Benchmark Winner | Mean F1 | Cases | Flags | Status |")
        lines.append("|---|---|---:|---:|---:|---|")
        for row in candidates:
            lines.append(
                f"| {row.get('role')} | {row.get('algorithm')} | {float(row.get('mean_f1', 0.0)):.3f} | "
                f"{row.get('case_count', 0)} | {row.get('flagged_case_count', 0)} | "
                f"{row.get('promotion_status')} |"
            )
        lines.append("")
        lines.append("Benchmark winners are labels only; `gameplay_default` still requires listening/in-game review.")
    lines.append("")
    lines.append("## Worst Failures")
    lines.append("")
    if not summary.get("worst_failures"):
        lines.append("No gameplay or sync quarantine flags.")
    else:
        lines.append("| Case | Role | Algorithm | F1 | Flags |")
        lines.append("|---|---|---|---:|---|")
        for row in summary.get("worst_failures", []):
            flags = ", ".join(k for k, v in row.get("gameplay_flags", {}).items() if v) or "-"
            f1 = row.get("f1")
            f1_text = "n/a" if f1 is None else f"{float(f1):.3f}"
            lines.append(
                f"| {row.get('case_id')} | {row.get('role')} | {row.get('algorithm')} | {f1_text} | {flags} |"
            )
    return "\n".join(lines) + "\n"


def _compact_classifier_app_data(payload: Mapping[str, Any], summary: Mapping[str, Any]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for case in payload.get("cases", []):
        compact_results: list[dict[str, Any]] = []
        for result in case.get("results", []):
            compact_results.append(
                {
                    "algorithm": result.get("algorithm"),
                    "overall": result.get("overall", {}),
                    "note_count": result.get("note_count"),
                    "event_count": result.get("event_count"),
                    "error": result.get("error"),
                    "gameplay": result.get("gameplay", {}),
                    "sync": result.get("sync", {}),
                    "mir_eval": result.get("mir_eval", {}),
                    "classifier": result.get("classifier", {}),
                }
            )
        cases.append(
            {
                "case_id": case.get("case_id"),
                "name": case.get("name"),
                "role": case.get("role"),
                "family": case.get("family"),
                "reference_count": case.get("reference_count", 0),
                "reference_path": case.get("reference_path"),
                "results": compact_results,
            }
        )
    return {
        "run": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "suite_version": payload.get("suite_version"),
            "profile": (payload.get("profile") or {}).get("profile"),
            "tolerance_ms": payload.get("tolerance_ms"),
        },
        "summary": {
            "algorithm_summaries": summary.get("algorithm_summaries", []),
            "promotion_candidates": summary.get("promotion_candidates", []),
            "worst_failures": summary.get("worst_failures", []),
        },
        "model_backends": payload.get("model_backends", {}),
        "quality_metric_backends": payload.get("quality_metric_backends", {}),
        "dataset_sources": payload.get("dataset_sources", {}),
        "cases": cases,
    }


def render_classifier_performance_app(payload: Mapping[str, Any], summary: Mapping[str, Any]) -> str:
    app_data = _compact_classifier_app_data(payload, summary)
    data_json = (
        json.dumps(app_data, default=_json_default, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    template = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Classifier Performance Explorer</title>
<style>
:root {
  color-scheme: dark;
  --bg: #08111d;
  --panel: #101b2a;
  --panel-2: #142338;
  --line: #263955;
  --text: #edf5ff;
  --muted: #9fb1c7;
  --accent: #47d7ac;
  --warn: #f3c663;
  --bad: #ff6b7d;
  --good: #74e39a;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background:
    radial-gradient(circle at top left, rgba(71, 215, 172, 0.17), transparent 34rem),
    radial-gradient(circle at 78% 12%, rgba(75, 119, 255, 0.12), transparent 26rem),
    var(--bg);
  color: var(--text);
  font-family: "Aptos", "Segoe UI", sans-serif;
}
header {
  padding: 28px 34px 18px;
  border-bottom: 1px solid var(--line);
}
h1 { margin: 0 0 8px; font-size: clamp(28px, 4vw, 48px); letter-spacing: -0.04em; }
h2 { margin: 0 0 14px; font-size: 18px; }
h3 { margin: 18px 0 10px; font-size: 14px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }
.subhead { margin: 0; color: var(--muted); max-width: 960px; }
.run-meta { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; color: var(--muted); font-size: 13px; }
.pill { border: 1px solid var(--line); border-radius: 999px; padding: 5px 9px; background: rgba(255,255,255,0.04); }
main { padding: 22px 34px 36px; display: grid; grid-template-columns: minmax(420px, 1.1fr) minmax(360px, 0.9fr); gap: 18px; }
section, .panel {
  background: rgba(16, 27, 42, 0.88);
  border: 1px solid var(--line);
  border-radius: 18px;
  box-shadow: 0 18px 48px rgba(0,0,0,0.28);
}
section { padding: 18px; }
.controls { display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 10px; margin-bottom: 14px; }
label { display: grid; gap: 5px; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.07em; }
select, input {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 11px;
  background: #07111f;
  color: var(--text);
  padding: 9px 10px;
  font: inherit;
}
.cards { display: grid; grid-template-columns: repeat(4, minmax(100px, 1fr)); gap: 10px; margin-bottom: 14px; }
.card { background: var(--panel-2); border: 1px solid var(--line); border-radius: 14px; padding: 12px; }
.card strong { display: block; font-size: 24px; }
.card span { color: var(--muted); font-size: 12px; }
.table-wrap { overflow: auto; border: 1px solid var(--line); border-radius: 14px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 9px 10px; border-bottom: 1px solid rgba(255,255,255,0.07); text-align: left; white-space: nowrap; }
th { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.07em; background: rgba(255,255,255,0.03); }
tr[data-row] { cursor: pointer; }
tr[data-row]:hover, tr.selected { background: rgba(71, 215, 172, 0.09); }
.score { font-family: "Cascadia Mono", "Consolas", monospace; }
.risk { color: var(--bad); }
.ok { color: var(--good); }
.muted { color: var(--muted); }
.heatmap {
  display: grid;
  grid-template-columns: minmax(180px, 1.2fr) repeat(var(--cols), minmax(72px, 1fr));
  gap: 4px;
  align-items: stretch;
  margin: 12px 0 16px;
}
.heat-cell, .heat-label {
  min-height: 32px;
  border-radius: 9px;
  padding: 8px;
  border: 1px solid rgba(255,255,255,0.06);
  font-size: 12px;
}
.heat-label { background: rgba(255,255,255,0.04); color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.heat-cell { font-family: "Cascadia Mono", "Consolas", monospace; text-align: center; }
.detail-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 12px; }
.detail-stat { border: 1px solid var(--line); background: rgba(255,255,255,0.04); border-radius: 14px; padding: 12px; }
.detail-stat strong { display: block; font-size: 22px; }
.confusion-list { display: grid; gap: 7px; }
.confusion-row { display: flex; justify-content: space-between; gap: 8px; border: 1px solid rgba(255,255,255,0.07); border-radius: 10px; padding: 8px 10px; background: rgba(255,255,255,0.03); }
.timeline { display: grid; grid-template-columns: repeat(24, 1fr); gap: 3px; height: 72px; align-items: end; padding: 8px; border: 1px solid var(--line); border-radius: 14px; background: #07111f; }
.bar { min-height: 2px; border-radius: 5px 5px 0 0; background: linear-gradient(to top, var(--bad) 0 33%, var(--warn) 33% 66%, var(--good) 66% 100%); }
.empty { color: var(--muted); padding: 20px; border: 1px dashed var(--line); border-radius: 14px; }
@media (max-width: 1100px) {
  main { grid-template-columns: 1fr; padding: 18px; }
  header { padding: 22px 18px 14px; }
  .controls, .cards { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
}
@media (max-width: 640px) {
  .controls, .cards, .detail-grid { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<header>
  <h1>Classifier Performance Explorer</h1>
  <p class="subhead">Single-file report for inspecting transcription classifier behavior across drums, melodic roles, and piano. It shows per-class precision/recall/F1, confusions, timing buckets, gameplay flags, and promotion-gate context without requiring a server.</p>
  <div class="run-meta" id="run-meta"></div>
</header>
<main>
  <section>
    <h2>Coverage</h2>
    <div class="controls">
      <label>Role<select id="role-filter"></select></label>
      <label>Algorithm<select id="algorithm-filter"></select></label>
      <label>Risk<select id="risk-filter"><option value="all">All</option><option value="flagged">Flagged only</option><option value="clean">Clean only</option></select></label>
      <label>Search<input id="search-filter" type="search" placeholder="case, method, role"></label>
    </div>
    <div class="cards" id="overview-cards"></div>
    <h3>F1 Heatmap</h3>
    <div id="heatmap"></div>
    <h3>Runs</h3>
    <div class="table-wrap"><table>
      <thead><tr><th>Case</th><th>Role</th><th>Method</th><th>F1</th><th>Refs</th><th>Pred</th><th>Risk</th></tr></thead>
      <tbody id="row-table"></tbody>
    </table></div>
  </section>
  <section>
    <h2>Classifier Detail</h2>
    <div id="detail"></div>
  </section>
</main>
<script id="classifier-data" type="application/json">__DATA__</script>
<script>
const data = JSON.parse(document.getElementById("classifier-data").textContent);
const state = { role: "all", algorithm: "all", risk: "all", query: "", selectedKey: "", dimension: "" };

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}
function fmt(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  return Number(value).toFixed(digits);
}
function allRows() {
  const rows = [];
  for (const c of data.cases || []) {
    for (const r of c.results || []) {
      rows.push({ key: `${c.case_id}::${r.algorithm}`, case: c, result: r });
    }
  }
  return rows;
}
function classifierPrimary(result) {
  const classifier = result.classifier || {};
  const dims = classifier.dimensions || [];
  return dims.find(d => d.name === classifier.primary_dimension) || dims[0] || {};
}
function predictedCount(row) {
  const coverage = (row.result.classifier || {}).coverage || {};
  return coverage.predicted_events ?? row.result.note_count ?? row.result.event_count ?? 0;
}
function isFlagged(row) {
  const gameplay = row.result.gameplay || {};
  const sync = row.result.sync || {};
  return Boolean(row.result.error)
    || Boolean(gameplay.playable_density_flag)
    || Boolean(sync.quarantine)
    || Number(gameplay.duplicate_rate || 0) > 0.08;
}
function riskLabel(row) {
  const flags = [];
  const gameplay = row.result.gameplay || {};
  const sync = row.result.sync || {};
  if (row.result.error) flags.push("error");
  if (gameplay.playable_density_flag) flags.push("density");
  if (Number(gameplay.duplicate_rate || 0) > 0.08) flags.push("duplicates");
  if (sync.quarantine) flags.push("sync");
  return flags.length ? flags.join(", ") : "clean";
}
function filteredRows() {
  const q = state.query.trim().toLowerCase();
  return allRows().filter(row => {
    const haystack = `${row.case.case_id} ${row.case.name || ""} ${row.case.role} ${row.result.algorithm}`.toLowerCase();
    if (state.role !== "all" && row.case.role !== state.role) return false;
    if (state.algorithm !== "all" && row.result.algorithm !== state.algorithm) return false;
    if (state.risk === "flagged" && !isFlagged(row)) return false;
    if (state.risk === "clean" && isFlagged(row)) return false;
    return !q || haystack.includes(q);
  });
}
function populateFilters() {
  const rows = allRows();
  const roles = [...new Set(rows.map(row => row.case.role).filter(Boolean))].sort();
  const algorithms = [...new Set(rows.map(row => row.result.algorithm).filter(Boolean))].sort();
  const roleFilter = document.getElementById("role-filter");
  const algorithmFilter = document.getElementById("algorithm-filter");
  roleFilter.innerHTML = `<option value="all">All roles</option>${roles.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join("")}`;
  algorithmFilter.innerHTML = `<option value="all">All methods</option>${algorithms.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join("")}`;
}
function renderMeta() {
  const run = data.run || {};
  document.getElementById("run-meta").innerHTML = [
    `Generated ${esc(run.generated_at_utc || "unknown")}`,
    `Suite ${esc(run.suite_version || "unknown")}`,
    `Profile ${esc(run.profile || "unknown")}`,
    `Tolerance ${fmt(run.tolerance_ms, 1)}ms`
  ].map(text => `<span class="pill">${text}</span>`).join("");
}
function renderOverview(rows) {
  const flagged = rows.filter(isFlagged).length;
  const withF1 = rows.map(row => row.result.overall && row.result.overall.f1).filter(v => v !== null && v !== undefined);
  const meanF1 = withF1.length ? withF1.reduce((a, b) => a + Number(b), 0) / withF1.length : null;
  const classes = rows.reduce((acc, row) => acc + ((classifierPrimary(row.result).class_metrics || []).length), 0);
  document.getElementById("overview-cards").innerHTML = [
    ["Runs", rows.length],
    ["Flagged", flagged],
    ["Mean F1", fmt(meanF1)],
    ["Class rows", classes]
  ].map(([label, value]) => `<div class="card"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>`).join("");
}
function colorFor(value, flagged) {
  if (value === null || value === undefined) return flagged ? "#653143" : "#263955";
  const v = Math.max(0, Math.min(1, Number(value)));
  const r = Math.round(220 - (130 * v));
  const g = Math.round(70 + (150 * v));
  const b = Math.round(92 + (30 * v));
  return `rgb(${r}, ${g}, ${b})`;
}
function renderHeatmap(rows) {
  const cases = [...new Map(rows.map(row => [row.case.case_id, row.case])).values()];
  const algorithms = [...new Set(rows.map(row => row.result.algorithm).filter(Boolean))].sort();
  if (!cases.length || !algorithms.length) {
    document.getElementById("heatmap").innerHTML = `<div class="empty">No rows match the current filters.</div>`;
    return;
  }
  const byKey = new Map(rows.map(row => [`${row.case.case_id}::${row.result.algorithm}`, row]));
  const maxCols = Math.max(1, algorithms.length);
  let html = `<div class="heatmap" style="--cols:${maxCols}"><div class="heat-label">Case / method</div>${algorithms.map(a => `<div class="heat-label">${esc(a)}</div>`).join("")}`;
  for (const c of cases) {
    html += `<div class="heat-label" title="${esc(c.name || c.case_id)}">${esc(c.case_id)}</div>`;
    for (const a of algorithms) {
      const row = byKey.get(`${c.case_id}::${a}`);
      if (!row) {
        html += `<div class="heat-cell muted">-</div>`;
      } else {
        const f1 = row.result.overall ? row.result.overall.f1 : null;
        html += `<div class="heat-cell" style="background:${colorFor(f1, isFlagged(row))}" title="${esc(riskLabel(row))}">${fmt(f1, 2)}</div>`;
      }
    }
  }
  html += `</div>`;
  document.getElementById("heatmap").innerHTML = html;
}
function renderRows(rows) {
  const tbody = document.getElementById("row-table");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="muted">No rows match the current filters.</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map((row, idx) => {
    const f1 = row.result.overall ? row.result.overall.f1 : null;
    const risk = riskLabel(row);
    const selected = row.key === state.selectedKey ? " selected" : "";
    return `<tr data-row="${idx}" class="${selected}">
      <td>${esc(row.case.case_id)}</td>
      <td>${esc(row.case.role)}</td>
      <td>${esc(row.result.algorithm)}</td>
      <td class="score">${fmt(f1)}</td>
      <td class="score">${esc(row.case.reference_count ?? 0)}</td>
      <td class="score">${esc(predictedCount(row))}</td>
      <td class="${risk === "clean" ? "ok" : "risk"}">${esc(risk)}</td>
    </tr>`;
  }).join("");
  tbody.querySelectorAll("tr[data-row]").forEach(tr => {
    tr.addEventListener("click", () => {
      const row = rows[Number(tr.dataset.row)];
      state.selectedKey = row.key;
      state.dimension = "";
      render();
    });
  });
}
function selectedRow(rows) {
  if (!rows.length) return null;
  return rows.find(row => row.key === state.selectedKey) || rows[0];
}
function renderTimeline(timeline) {
  if (!timeline || !timeline.length) return `<div class="empty">No timeline buckets available.</div>`;
  const maxCount = Math.max(1, ...timeline.map(b => Number(b.tp || 0) + Number(b.fp || 0) + Number(b.fn || 0)));
  return `<div class="timeline">${timeline.map(b => {
    const total = Number(b.tp || 0) + Number(b.fp || 0) + Number(b.fn || 0);
    const height = Math.max(3, Math.round((total / maxCount) * 100));
    const title = `${fmt(b.start, 2)}-${fmt(b.end, 2)}s TP:${b.tp || 0} FP:${b.fp || 0} FN:${b.fn || 0}`;
    return `<span class="bar" title="${esc(title)}" style="height:${height}%"></span>`;
  }).join("")}</div>`;
}
function renderDetail(rows) {
  const row = selectedRow(rows);
  const detail = document.getElementById("detail");
  if (!row) {
    detail.innerHTML = `<div class="empty">Select a classifier run.</div>`;
    return;
  }
  state.selectedKey = row.key;
  const classifier = row.result.classifier || {};
  const mir = row.result.mir_eval || {};
  const mirOnset = (mir.onset || {}).f1;
  const mirOffset = (mir.onset_offset || {}).f1;
  const dimensions = classifier.dimensions || [];
  const dimension = dimensions.find(d => d.name === state.dimension)
    || dimensions.find(d => d.name === classifier.primary_dimension)
    || dimensions[0]
    || {};
  state.dimension = dimension.name || "";
  const metrics = dimension.class_metrics || [];
  const confusions = dimension.confusions || [];
  const pitch = dimension.pitch_error_summary || {};
  const selector = dimensions.length
    ? `<label>Dimension<select id="dimension-select">${dimensions.map(d => `<option value="${esc(d.name)}" ${d.name === state.dimension ? "selected" : ""}>${esc(d.name)}</option>`).join("")}</select></label>`
    : "";
  const classRows = metrics.length
    ? metrics.map(m => `<tr><td>${esc(m.label)}</td><td class="score">${esc(m.reference_count)}</td><td class="score">${esc(m.predicted_count)}</td><td class="score">${esc(m.tp)}</td><td class="score">${fmt(m.precision)}</td><td class="score">${fmt(m.recall)}</td><td class="score">${fmt(m.f1)}</td></tr>`).join("")
    : `<tr><td colspan="7" class="muted">No per-class metrics available.</td></tr>`;
  const confusionRows = confusions.length
    ? confusions.slice(0, 16).map(c => `<div class="confusion-row"><span>${esc(c.reference_class)} -> ${esc(c.predicted_class)}</span><strong>${esc(c.count)}</strong></div>`).join("")
    : `<div class="empty">No near-time class confusions found.</div>`;
  detail.innerHTML = `
    <div class="panel" style="padding:14px; margin-bottom:12px;">
      <div class="muted">${esc(row.case.case_id)} / ${esc(row.case.role)}</div>
      <h2 style="margin:4px 0 10px;">${esc(row.result.algorithm)}</h2>
      ${selector}
    </div>
    <div class="detail-grid">
      <div class="detail-stat"><strong>${fmt(row.result.overall && row.result.overall.f1)}</strong><span class="muted">Native F1</span></div>
      <div class="detail-stat"><strong>${fmt(mirOnset)}</strong><span class="muted">MIR onset F1</span></div>
      <div class="detail-stat"><strong>${fmt(mirOffset)}</strong><span class="muted">MIR onset+offset F1</span></div>
      <div class="detail-stat"><strong>${esc(dimension.matched_tp ?? 0)}</strong><span class="muted">True positives</span></div>
      <div class="detail-stat"><strong>${esc(dimension.matched_confusions ?? 0)}</strong><span class="muted">Near-time confusions</span></div>
      <div class="detail-stat"><strong>${esc((dimension.unmatched_predictions ?? 0) + (dimension.unmatched_references ?? 0))}</strong><span class="muted">Unmatched events</span></div>
    </div>
    <h3>Per-Class Metrics</h3>
    <div class="table-wrap"><table><thead><tr><th>Class</th><th>Refs</th><th>Pred</th><th>TP</th><th>Precision</th><th>Recall</th><th>F1</th></tr></thead><tbody>${classRows}</tbody></table></div>
    <h3>Confusions</h3>
    <div class="confusion-list">${confusionRows}</div>
    <h3>Pitch Error Summary</h3>
    <div class="cards">
      <div class="card"><strong>${esc(pitch.semitone_near_confusions ?? "n/a")}</strong><span>semitone-near</span></div>
      <div class="card"><strong>${esc(pitch.octave_confusions ?? "n/a")}</strong><span>octave errors</span></div>
    </div>
    <h3>TP/FP/FN Timeline</h3>
    ${renderTimeline(classifier.timeline || [])}
  `;
  const dimSelect = document.getElementById("dimension-select");
  if (dimSelect) {
    dimSelect.addEventListener("change", event => {
      state.dimension = event.target.value;
      renderDetail(filteredRows());
    });
  }
}
function render() {
  const rows = filteredRows();
  renderOverview(rows);
  renderHeatmap(rows);
  renderRows(rows);
  renderDetail(rows);
}
document.getElementById("role-filter").addEventListener("change", event => { state.role = event.target.value; state.selectedKey = ""; render(); });
document.getElementById("algorithm-filter").addEventListener("change", event => { state.algorithm = event.target.value; state.selectedKey = ""; render(); });
document.getElementById("risk-filter").addEventListener("change", event => { state.risk = event.target.value; state.selectedKey = ""; render(); });
document.getElementById("search-filter").addEventListener("input", event => { state.query = event.target.value; state.selectedKey = ""; render(); });
populateFilters();
renderMeta();
render();
</script>
</body>
</html>
"""
    return template.replace("__DATA__", data_json)


def _svg_escape(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _color_lerp(lo: tuple[int, int, int], hi: tuple[int, int, int], t: float) -> str:
    t = max(0.0, min(1.0, float(t)))
    r = int(round(lo[0] + ((hi[0] - lo[0]) * t)))
    g = int(round(lo[1] + ((hi[1] - lo[1]) * t)))
    b = int(round(lo[2] + ((hi[2] - lo[2]) * t)))
    return f"#{r:02x}{g:02x}{b:02x}"


def _write_heatmap_svg(
    path: Path,
    *,
    title: str,
    rows: Sequence[str],
    columns: Sequence[str],
    values: Mapping[tuple[str, str], float | None],
    max_value: float = 1.0,
    invert: bool = False,
) -> None:
    cell_w = 92
    cell_h = 24
    row_label_w = 210
    col_h = 110
    width = row_label_w + (cell_w * max(1, len(columns))) + 24
    height = col_h + (cell_h * max(1, len(rows))) + 42
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#101722"/>',
        f'<text x="16" y="28" fill="#f3f7ff" font-family="Segoe UI, sans-serif" font-size="18" font-weight="700">{_svg_escape(title)}</text>',
    ]
    for col_idx, col in enumerate(columns):
        x = row_label_w + (col_idx * cell_w) + 12
        lines.append(
            f'<text x="{x}" y="{col_h - 8}" transform="rotate(-35 {x} {col_h - 8})" '
            f'fill="#b9c7d9" font-family="Segoe UI, sans-serif" font-size="11">{_svg_escape(col)}</text>'
        )
    for row_idx, row in enumerate(rows):
        y = col_h + (row_idx * cell_h)
        lines.append(
            f'<text x="16" y="{y + 16}" fill="#d8e0ee" font-family="Segoe UI, sans-serif" font-size="11">{_svg_escape(row)}</text>'
        )
        for col_idx, col in enumerate(columns):
            x = row_label_w + (col_idx * cell_w)
            raw = values.get((row, col))
            if raw is None:
                fill = "#1b2533"
                label = "-"
            else:
                ratio = max(0.0, min(1.0, float(raw) / max(max_value, 1e-9)))
                ratio = 1.0 - ratio if invert else ratio
                fill = _color_lerp((83, 29, 47), (31, 136, 95), ratio)
                label = f"{float(raw):.2f}"
            lines.append(f'<rect x="{x}" y="{y}" width="{cell_w - 2}" height="{cell_h - 2}" rx="3" fill="{fill}"/>')
            lines.append(
                f'<text x="{x + 8}" y="{y + 15}" fill="#f7fbff" font-family="Consolas, monospace" font-size="10">{label}</text>'
            )
    lines.append("</svg>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_quality_heatmaps(
    payload: Mapping[str, Any],
    summary: Mapping[str, Any],
    out_dir: Path,
) -> list[Path]:
    rows = _dedupe(str(row.get("case_id")) for row in summary.get("rows", []))
    columns = _dedupe(str(row.get("algorithm")) for row in summary.get("rows", []))
    if not rows or not columns:
        return []

    f1_values: dict[tuple[str, str], float | None] = {}
    risk_values: dict[tuple[str, str], float | None] = {}
    for case in payload.get("cases", []):
        case_id = str(case.get("case_id"))
        for result in case.get("results", []):
            algorithm = str(result.get("algorithm"))
            overall = result.get("overall", {})
            gameplay = result.get("gameplay", {})
            sync = result.get("sync", {})
            f1 = overall.get("f1")
            f1_values[(case_id, algorithm)] = float(f1) if f1 is not None else None
            risk = 0.0
            if result.get("error"):
                risk += 1.0
            if bool(gameplay.get("playable_density_flag", False)):
                risk += 1.0
            if float(gameplay.get("duplicate_rate", 0.0) or 0.0) > 0.08:
                risk += 1.0
            if bool(sync.get("quarantine", False)):
                risk += 1.0
            risk_values[(case_id, algorithm)] = risk

    f1_path = out_dir / "f1_heatmap.svg"
    risk_path = out_dir / "gameplay_risk_heatmap.svg"
    _write_heatmap_svg(f1_path, title="F1 by Case and Method", rows=rows, columns=columns, values=f1_values)
    _write_heatmap_svg(
        risk_path,
        title="Gameplay Risk Flags by Case and Method",
        rows=rows,
        columns=columns,
        values=risk_values,
        max_value=4.0,
        invert=True,
    )
    return [f1_path, risk_path]


def write_quality_outputs(
    payload: Mapping[str, Any],
    *,
    output_root: Path | str = DEFAULT_OUTPUT_ROOT,
    label: str = "full-corpus-quality",
) -> Path:
    summary = summarize_quality_results(payload)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(output_root) / f"{timestamp}_{_slugify(label)}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps({"payload": payload, "summary": summary}, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    report_md = render_quality_report_markdown(payload, summary)
    (out_dir / "report.md").write_text(report_md, encoding="utf-8")
    (out_dir / "classifier_performance.html").write_text(
        render_classifier_performance_app(payload, summary),
        encoding="utf-8",
    )
    write_quality_heatmaps(payload, summary, out_dir)
    (out_dir / "report.html").write_text(
        "<!DOCTYPE html><html><body><pre>"
        + report_md.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        + "</pre></body></html>",
        encoding="utf-8",
    )
    return out_dir
