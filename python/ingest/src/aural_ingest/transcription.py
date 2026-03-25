from __future__ import annotations

from dataclasses import dataclass, field
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
    "spectral_flux_multiband",
    "dsp_bandpass",
    "librosa_superflux",
    "spectral_template_multipass",
    "spectral_template_with_grid",
    "multi_resolution",
    "template_xcorr",
    "probabilistic_pattern",
    "onset_aligned",
    "multi_resolution_template",
    "hybrid_kick_grid",
    "adaptive_beat_grid_multilabel",
)

KNOWN_MELODIC_METHODS: tuple[str, ...] = (
    "auto", 
    "pyin", 
    "basic_pitch",
    "melodic_combined",
    "melodic_octave_fix",
    "melodic_yin_octave_hps_fix",
)

INSTRUMENT_ROLES: tuple[str, ...] = (
    "bass",
    "rhythm_guitar",
    "lead_guitar",
    "keys",
)

# Instrument-specific frequency ranges for melodic transcription.
# (min_freq_hz, max_freq_hz) — tuned to practical pitch range of each instrument.
INSTRUMENT_FREQ_RANGES: dict[str, tuple[float, float]] = {
    "bass": (30.0, 400.0),           # ~B0 (31 Hz) to ~G4 (392 Hz)
    "rhythm_guitar": (75.0, 1400.0), # ~D2 (73 Hz) to ~F6 (1397 Hz)
    "lead_guitar": (75.0, 1400.0),   # same range, different stem
    "keys": (27.0, 4200.0),          # ~A0 (27.5 Hz) to ~C8 (4186 Hz)
    "melodic": (45.0, 1700.0),       # legacy default
}

DEFAULT_DRUM_FILTER = "adaptive_beat_grid"
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
    instrument: str = "melodic"


@dataclass(frozen=True)
class MelodicTranscriptionResult:
    notes: list[MelodicNote]
    used_method: str | None
    attempted_methods: list[str]
    warnings: list[str]


MelodicTranscriber = Callable[[Path], list[MelodicNote]]


@dataclass(frozen=True)
class InstrumentTranscriptionResult:
    """Transcription result for a single instrument stem."""
    instrument: str
    notes: list[MelodicNote]
    used_method: str | None
    attempted_methods: list[str]
    warnings: list[str]
    stem_path: str | None = None


def build_default_drum_algorithm_registry() -> dict[str, DrumTranscriber]:
    # Import lazily to keep module import lightweight and avoid unnecessary startup costs.
    from aural_ingest.algorithms import (
        adaptive_beat_grid,
        adaptive_beat_grid_multilabel,
        aural_onset,
        beat_conditioned_multiband_decoder,
        combined_filter,
        dsp_bandpass,
        dsp_bandpass_improved,
        dsp_spectral_flux,
        hpss_percussive,
        hybrid_kick_grid,
        librosa_superflux,
        mfcc_cepstral,
        multi_resolution,
        multi_resolution_template,
        nmf_decomposition,
        onset_aligned,
        probabilistic_pattern,
        spectral_flux_multiband,
        spectral_template_multipass,
        spectral_template_with_grid,
        template_xcorr,
    )

    return {
        "combined_filter": combined_filter.transcribe,
        "dsp_bandpass_improved": dsp_bandpass_improved.transcribe,
        "dsp_spectral_flux": dsp_spectral_flux.transcribe,
        "aural_onset": aural_onset.transcribe,
        "adaptive_beat_grid": adaptive_beat_grid.transcribe,
        "adaptive_beat_grid_multilabel": adaptive_beat_grid_multilabel.transcribe,
        "beat_conditioned_multiband_decoder": beat_conditioned_multiband_decoder.transcribe,
        "spectral_flux_multiband": spectral_flux_multiband.transcribe,
        "dsp_bandpass": dsp_bandpass.transcribe,
        "librosa_superflux": librosa_superflux.transcribe,
        "spectral_template_multipass": spectral_template_multipass.transcribe,
        "spectral_template_with_grid": spectral_template_with_grid.transcribe,
        "multi_resolution": multi_resolution.transcribe,
        "template_xcorr": template_xcorr.transcribe,
        "probabilistic_pattern": probabilistic_pattern.transcribe,
        "onset_aligned": onset_aligned.transcribe,
        "multi_resolution_template": multi_resolution_template.transcribe,
        "hybrid_kick_grid": hybrid_kick_grid.transcribe,
        "nmf_decomposition": nmf_decomposition.transcribe,
        "mfcc_cepstral": mfcc_cepstral.transcribe,
        "hpss_percussive": hpss_percussive.transcribe,
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
    instrument: str = "melodic",
) -> dict[str, MelodicTranscriber]:
    # Import lazily to keep module import lightweight and avoid unnecessary startup costs.
    from aural_ingest.algorithms import (
        melodic_basic_pitch,
        melodic_pyin,
        melodic_combined,
        melodic_octave_fix,
        melodic_yin_octave_hps_fix,
    )

    roots = list(model_search_roots) if model_search_roots is not None else _default_basic_pitch_model_roots()
    basic_pitch_model_path = resolve_basic_pitch_model_path(roots)

    _inst = instrument  # capture for closures

    def _basic_pitch(stem_path: Path) -> list[MelodicNote]:
        return melodic_basic_pitch.transcribe(stem_path, model_path=basic_pitch_model_path, instrument=_inst)

    def _pyin(stem_path: Path) -> list[MelodicNote]:
        return melodic_pyin.transcribe(stem_path, instrument=_inst)

    def _combined(stem_path: Path) -> list[MelodicNote]:
        return melodic_combined.transcribe(stem_path, instrument=_inst)

    def _octave_fix(stem_path: Path) -> list[MelodicNote]:
        return melodic_octave_fix.transcribe(stem_path, instrument=_inst)

    def _yin_octave_hps_fix(stem_path: Path) -> list[MelodicNote]:
        return melodic_yin_octave_hps_fix.transcribe(stem_path, instrument=_inst)

    return {
        "basic_pitch": _basic_pitch,
        "pyin": _pyin,
        "melodic_combined": _combined,
        "melodic_octave_fix": _octave_fix,
        "melodic_yin_octave_hps_fix": _yin_octave_hps_fix,
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

    if normalized == "spectral_template_with_grid":
        chain = [
            "spectral_template_with_grid",
            "spectral_template_multipass",
            "adaptive_beat_grid",
            "combined_filter",
            "dsp_bandpass_improved",
            "dsp_spectral_flux",
            "dsp_bandpass",
            "aural_onset",
        ]
    elif normalized == "combined_filter":
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
            "spectral_flux_multiband",
            "adaptive_beat_grid",
            "combined_filter",
            "dsp_bandpass_improved",
            "dsp_spectral_flux",
            "dsp_bandpass",
            "aural_onset",
        ]
    elif normalized == "spectral_flux_multiband":
        chain = [
            "spectral_flux_multiband",
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


def melodic_fallback_chain(requested_method: str | None, instrument: str = "melodic") -> list[str]:
    normalized = validate_melodic_method(requested_method)
    if normalized is None:
        normalized = DEFAULT_MELODIC_METHOD

    if normalized == "auto":
        if instrument == "bass":
            chain = ["melodic_yin_octave_hps_fix", "melodic_octave_fix", "melodic_combined", "basic_pitch", "pyin"]
        else:
            chain = ["melodic_octave_fix", "melodic_combined", "basic_pitch", "pyin"]
    elif normalized == "basic_pitch":
        chain = ["basic_pitch", "pyin"]
    else:
        chain = [normalized, "melodic_octave_fix", "melodic_yin_octave_hps_fix", "melodic_combined", "basic_pitch", "pyin"]

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
    instrument: str = "melodic",
) -> MelodicTranscriptionResult:
    normalized = validate_melodic_method(requested_method)
    warnings: list[str] = []
    if normalized is None:
        normalized = DEFAULT_MELODIC_METHOD
        warnings.append(
            f"unknown melodic method '{requested_method}', falling back to {DEFAULT_MELODIC_METHOD}"
        )

    attempted: list[str] = []
    for method in melodic_fallback_chain(normalized, instrument=instrument):
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


def transcribe_all_melodic_stems(
    stems: dict[str, Path],
    requested_method: str | None,
    logger: Callable[[str], None] | None = None,
) -> list[InstrumentTranscriptionResult]:
    """Transcribe each available instrument stem.

    Builds a per-instrument algorithm registry so that each stem is transcribed
    with appropriate frequency ranges for that instrument.

    Args:
        stems: map of instrument role (e.g. "bass", "lead_guitar") to stem wav path.
        requested_method: user-requested transcription method.
        logger: optional log callback.

    Returns:
        A list of InstrumentTranscriptionResult, one per stem that was transcribed.
    """
    results: list[InstrumentTranscriptionResult] = []

    for instrument, stem_path in sorted(stems.items()):
        if not stem_path.is_file():
            if logger:
                logger(f"melodic stem for '{instrument}' not found: {stem_path}")
            continue

        if logger:
            logger(f"transcribing {instrument} from {stem_path.name}")

        # Build a per-instrument registry so frequency ranges are correct.
        inst_registry = build_default_melodic_algorithm_registry(instrument=instrument)

        result = transcribe_melodic(
            stem_path,
            requested_method=requested_method,
            algorithm_registry=inst_registry,
            logger=logger,
            instrument=instrument,
        )

        # Tag each note with the instrument role.
        tagged_notes = [
            MelodicNote(
                t_on=n.t_on,
                t_off=n.t_off,
                pitch=n.pitch,
                velocity=n.velocity,
                instrument=instrument,
            )
            for n in result.notes
        ]

        results.append(
            InstrumentTranscriptionResult(
                instrument=instrument,
                notes=tagged_notes,
                used_method=result.used_method,
                attempted_methods=result.attempted_methods,
                warnings=result.warnings,
                stem_path=str(stem_path),
            )
        )

    return results


