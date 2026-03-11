from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Callable, Iterable

KNOWN_DRUM_FILTERS: tuple[str, ...] = (
    "combined_filter",
    "dsp_bandpass_improved",
    "dsp_spectral_flux",
    "aural_onset",
    "adaptive_beat_grid",
    "beat_conditioned_multiband_decoder",
    "dsp_bandpass",
    "librosa_superflux",
)

KNOWN_MELODIC_METHODS: tuple[str, ...] = ("auto", "pyin", "basic_pitch")

DEFAULT_DRUM_FILTER = "combined_filter"
DEFAULT_MELODIC_METHOD = "auto"


@dataclass(frozen=True)
class DrumEvent:
    time: float
    note: int
    velocity: int
    duration: float = 0.05


@dataclass(frozen=True)
class DrumTranscriptionResult:
    events: list[DrumEvent]
    used_algorithm: str | None
    attempted_algorithms: list[str]
    warnings: list[str]


DrumTranscriber = Callable[[Path], list[DrumEvent]]


@dataclass(frozen=True)
class MelodicNote:
    t_on: float
    t_off: float
    pitch: int
    velocity: int


@dataclass(frozen=True)
class MelodicTranscriptionResult:
    notes: list[MelodicNote]
    used_method: str | None
    attempted_methods: list[str]
    warnings: list[str]


MelodicTranscriber = Callable[[Path], list[MelodicNote]]


def build_default_drum_algorithm_registry() -> dict[str, DrumTranscriber]:
    # Import lazily to keep module import lightweight and avoid unnecessary startup costs.
    from aural_ingest.algorithms import (
        adaptive_beat_grid,
        aural_onset,
        beat_conditioned_multiband_decoder,
        combined_filter,
        dsp_bandpass,
        dsp_bandpass_improved,
        dsp_spectral_flux,
        librosa_superflux,
    )

    return {
        "combined_filter": combined_filter.transcribe,
        "dsp_bandpass_improved": dsp_bandpass_improved.transcribe,
        "dsp_spectral_flux": dsp_spectral_flux.transcribe,
        "aural_onset": aural_onset.transcribe,
        "adaptive_beat_grid": adaptive_beat_grid.transcribe,
        "beat_conditioned_multiband_decoder": beat_conditioned_multiband_decoder.transcribe,
        "dsp_bandpass": dsp_bandpass.transcribe,
        "librosa_superflux": librosa_superflux.transcribe,
    }


def _default_basic_pitch_model_roots() -> list[Path]:
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(str(meipass)))

    roots.append(Path.cwd())

    try:
        # Prefer repository-local roots when running from source.
        this_file = Path(__file__).resolve()
        roots.extend([this_file.parent, this_file.parents[2], this_file.parents[4]])
    except Exception:
        pass

    deduped: list[Path] = []
    seen: set[str] = set()
    for p in roots:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    return deduped


def resolve_basic_pitch_model_path(search_roots: Iterable[Path | str]) -> Path | None:
    suffixes = [
        Path("basic_pitch") / "saved_models" / "icassp_2022" / "nmp.onnx",
        Path("basic_pitch") / "saved_models" / "icassp_2022" / "nmp.tflite",
        Path("basic_pitch") / "saved_models" / "icassp_2022" / "nmp",
    ]

    for root in search_roots:
        if root is None:
            continue
        root_path = Path(root)
        for suffix in suffixes:
            candidate = root_path / suffix
            if candidate.is_file() or candidate.is_dir():
                return candidate
    return None


def build_default_melodic_algorithm_registry(
    model_search_roots: Iterable[Path | str] | None = None,
) -> dict[str, MelodicTranscriber]:
    # Import lazily to keep module import lightweight and avoid unnecessary startup costs.
    from aural_ingest.algorithms import melodic_basic_pitch, melodic_pyin

    roots = list(model_search_roots) if model_search_roots is not None else _default_basic_pitch_model_roots()
    basic_pitch_model_path = resolve_basic_pitch_model_path(roots)

    def _basic_pitch(stem_path: Path) -> list[MelodicNote]:
        return melodic_basic_pitch.transcribe(stem_path, model_path=basic_pitch_model_path)

    return {
        "basic_pitch": _basic_pitch,
        "pyin": melodic_pyin.transcribe,
    }


def resolve_drum_filter(requested_filter: str | None) -> tuple[str, list[str]]:
    if requested_filter is None:
        return DEFAULT_DRUM_FILTER, []

    rf = requested_filter.strip().lower()
    if not rf or rf == "auto":
        return DEFAULT_DRUM_FILTER, []
    if rf in KNOWN_DRUM_FILTERS:
        return rf, []

    return DEFAULT_DRUM_FILTER, [
        f"unknown drum filter '{requested_filter}', falling back to {DEFAULT_DRUM_FILTER}"
    ]


def validate_melodic_method(method: str | None) -> str | None:
    if method is None:
        return DEFAULT_MELODIC_METHOD
    m = method.strip().lower()
    if not m:
        return DEFAULT_MELODIC_METHOD
    if m in KNOWN_MELODIC_METHODS:
        return m
    return None


def drum_fallback_chain(requested_filter: str | None) -> list[str]:
    normalized, _warnings = resolve_drum_filter(requested_filter)

    if normalized == "combined_filter":
        chain = [
            "combined_filter",
            "dsp_bandpass_improved",
            "adaptive_beat_grid",
            "dsp_spectral_flux",
            "dsp_bandpass",
            "aural_onset",
        ]
    elif normalized == "adaptive_beat_grid":
        chain = [
            "adaptive_beat_grid",
            "combined_filter",
            "dsp_bandpass_improved",
            "dsp_spectral_flux",
            "dsp_bandpass",
            "aural_onset",
        ]
    elif normalized == "beat_conditioned_multiband_decoder":
        chain = [
            "beat_conditioned_multiband_decoder",
            "adaptive_beat_grid",
            "combined_filter",
            "dsp_bandpass_improved",
            "dsp_spectral_flux",
            "dsp_bandpass",
            "aural_onset",
        ]
    elif normalized == "aural_onset":
        chain = [
            "aural_onset",
            "combined_filter",
            "adaptive_beat_grid",
            "dsp_bandpass_improved",
            "dsp_spectral_flux",
            "dsp_bandpass",
        ]
    elif normalized == "librosa_superflux":
        chain = [
            "librosa_superflux",
            "combined_filter",
            "dsp_bandpass_improved",
            "dsp_spectral_flux",
            "dsp_bandpass",
            "aural_onset",
        ]
    else:
        chain = [
            normalized,
            "combined_filter",
            "dsp_bandpass_improved",
            "adaptive_beat_grid",
            "dsp_spectral_flux",
            "dsp_bandpass",
            "aural_onset",
        ]

    out: list[str] = []
    for x in chain:
        if x not in out:
            out.append(x)
    return out


def melodic_fallback_chain(requested_method: str | None) -> list[str]:
    normalized = validate_melodic_method(requested_method)
    if normalized is None:
        normalized = DEFAULT_MELODIC_METHOD

    if normalized in {"auto", "basic_pitch"}:
        chain = ["basic_pitch", "pyin"]
    else:
        chain = ["pyin"]

    out: list[str] = []
    for x in chain:
        if x not in out:
            out.append(x)
    return out


def transcribe_drums_dsp(
    stem_path: Path,
    requested_filter: str | None,
    algorithm_registry: dict[str, DrumTranscriber],
    logger: Callable[[str], None] | None = None,
) -> DrumTranscriptionResult:
    normalized, warnings = resolve_drum_filter(requested_filter)
    attempted: list[str] = []

    for algorithm_id in drum_fallback_chain(normalized):
        attempted.append(algorithm_id)
        fn = algorithm_registry.get(algorithm_id)
        if fn is None:
            msg = f"drum algorithm '{algorithm_id}' unavailable; trying next fallback"
            warnings.append(msg)
            if logger:
                logger(msg)
            continue

        try:
            events = fn(stem_path)
        except Exception as e:
            msg = f"drum algorithm '{algorithm_id}' failed: {e}"
            warnings.append(msg)
            if logger:
                logger(msg)
            continue

        if events:
            return DrumTranscriptionResult(
                events=events,
                used_algorithm=algorithm_id,
                attempted_algorithms=attempted,
                warnings=warnings,
            )

    return DrumTranscriptionResult(
        events=[],
        used_algorithm=None,
        attempted_algorithms=attempted,
        warnings=warnings,
    )


def transcribe_melodic(
    stem_path: Path,
    requested_method: str | None,
    algorithm_registry: dict[str, MelodicTranscriber],
    logger: Callable[[str], None] | None = None,
) -> MelodicTranscriptionResult:
    normalized = validate_melodic_method(requested_method)
    warnings: list[str] = []
    if normalized is None:
        normalized = DEFAULT_MELODIC_METHOD
        warnings.append(
            f"unknown melodic method '{requested_method}', falling back to {DEFAULT_MELODIC_METHOD}"
        )

    attempted: list[str] = []
    for method in melodic_fallback_chain(normalized):
        attempted.append(method)
        fn = algorithm_registry.get(method)
        if fn is None:
            msg = f"melodic method '{method}' unavailable; trying next fallback"
            warnings.append(msg)
            if logger:
                logger(msg)
            continue

        try:
            notes = fn(stem_path)
        except Exception as e:
            msg = f"melodic method '{method}' failed: {e}"
            warnings.append(msg)
            if logger:
                logger(msg)
            continue

        if notes:
            return MelodicTranscriptionResult(
                notes=notes,
                used_method=method,
                attempted_methods=attempted,
                warnings=warnings,
            )

    return MelodicTranscriptionResult(
        notes=[],
        used_method=None,
        attempted_methods=attempted,
        warnings=warnings,
    )
