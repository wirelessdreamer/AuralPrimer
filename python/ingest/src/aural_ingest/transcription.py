from __future__ import annotations

import contextlib
import io
import importlib.util
import json
import math
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
import sys
from typing import Any, Callable, Iterable, Mapping

from aural_ingest.device import select_device
from aural_ingest.mt3_compat import ensure_mt3_transformers_compat, suppress_mt3_runtime_warnings

KNOWN_HEURISTIC_DRUM_FILTERS: tuple[str, ...] = (
    "combined_filter",
    "dsp_bandpass_improved",
    "dsp_spectral_flux",
    "aural_onset",
    "adaptive_beat_grid",
    "beat_conditioned_multiband_decoder",
    "spectral_flux_multiband",
    "dsp_bandpass",
    "librosa_superflux",
    "librosa_superflux_dense",
    "spectral_template_multipass",
    "spectral_template_with_grid",
    "multi_resolution",
    "template_xcorr",
    "probabilistic_pattern",
    "onset_aligned",
    "multi_resolution_template",
    "hybrid_kick_grid",
    "adaptive_beat_grid_multilabel",
    "drum_crnn",
)

KNOWN_MT3_DRUM_ENGINES: tuple[str, ...] = (
    "mr_mt3_drums",
    "yourmt3_drums",
)

KNOWN_DRUM_ENGINES: tuple[str, ...] = KNOWN_HEURISTIC_DRUM_FILTERS + KNOWN_MT3_DRUM_ENGINES
KNOWN_DRUM_FILTERS: tuple[str, ...] = KNOWN_DRUM_ENGINES

KNOWN_MELODIC_METHODS: tuple[str, ...] = (
    "auto",
    "piano_auto",
    "piano_basic_pitch_playable",
    "piano_basic_pitch",
    "piano_basic_pitch_clean",
    "piano_polyphonic",
    "piano_polyphonic_clean",
    "piano_transkun",
    "piano_transkun_clean",
    "piano_pti",
    "piano_pti_clean",
    "piano_pti_clean_dedup",
    "piano_pti_clean_dedup_pyin",
    "piano_chord_supplement",
    "piano_pti_consensus",
    "piano_pti_consensus_clean",
    "piano_hft",
    "piano_hft_clean",
    "piano_d3rm",
    "piano_d3rm_clean",
    "pyin",
    "basic_pitch",
    "melodic_combined",
    "melodic_combined_guitar",
    "melodic_octave_fix",
    "melodic_yin_octave_hps_fix",
    "melodic_adaptive",
    "melodic_yin_bass80",
    "melodic_pyin_bass_strict",
    "melodic_hpss_combined",
    "melodic_template_multipass",
    "torchcrepe",
)

KNOWN_TRANSCRIPTION_PROFILES: tuple[str, ...] = (
    "gameplay_default",
    "fidelity_midi",
    "research_ab",
)

TRANSCRIPTION_PROFILES: dict[str, dict[str, Any]] = {
    "gameplay_default": {
        "description": "Prefer in-game recognizability, stable density, and fail-safe local defaults.",
        "drum_engines": [
            # Neural ADT (MT3) leads when its checkpoint is installed: it
            # catches the dense hi-hats / ghost notes the DSP engines miss
            # (their E-GMD recall collapse, F1 ~0.13). transcribe_drums_with_profile
            # falls through to the DSP chain when the checkpoint or runtime
            # is absent, so checkpoint-less machines still import. Device is
            # auto-detected (GPU if present, else CPU) -- never hardcoded.
            "mr_mt3_drums",
            "beat_conditioned_multiband_decoder",
            "spectral_flux_multiband",
            "adaptive_beat_grid",
            "combined_filter",
            "dsp_bandpass_improved",
        ],
        "melodic_methods_by_instrument": {
            "bass": [
                # torchcrepe (neural monophonic pitch tracker) is the primary:
                # on real bass stems it matches the YIN+HPS octave-cleanliness
                # (~0.3% octave-jump vs basic-pitch's 45%) with a tighter, lower
                # register and runs ~9x faster. The YIN chain stays as fallback
                # for the score-gated path if torchcrepe scores below threshold.
                "torchcrepe",
                "melodic_yin_octave_hps_fix",
                "melodic_adaptive",
                "melodic_yin_bass80",
                "melodic_octave_fix",
            ],
            "keys": [
                "piano_auto",
                "piano_basic_pitch_playable",
                "piano_basic_pitch",
                "piano_basic_pitch_clean",
                "piano_pti_consensus_clean",
                "piano_pti_clean",
                "piano_polyphonic_clean",
                "melodic_octave_fix",
                "melodic_hpss_combined",
            ],
            "lead_guitar": [
                # Lead guitar is monophonic: torchcrepe leads (octave-clean,
                # ~6-8x faster), DSP chain stays as the score-gated fallback.
                "torchcrepe",
                "melodic_adaptive",
                "melodic_octave_fix",
                "melodic_combined",
                "basic_pitch",
            ],
            "rhythm_guitar": [
                # Rhythm guitar is polyphonic (chords): keep the polyphonic
                # HPSS+onset engine first; a monophonic tracker would drop the
                # chord voices despite scoring well on the cleanliness heuristic.
                "melodic_hpss_combined",
                "melodic_adaptive",
                "melodic_octave_fix",
                "melodic_combined",
            ],
        },
    },
    "fidelity_midi": {
        "description": "Prefer denser symbolic output for A/B review and piano-roll export.",
        "drum_engines": [
            "yourmt3_drums",
            "mr_mt3_drums",
            "beat_conditioned_multiband_decoder",
            "spectral_template_with_grid",
        ],
        "melodic_methods_by_instrument": {
            "bass": ["basic_pitch", "melodic_yin_octave_hps_fix", "melodic_octave_fix"],
            "keys": [
                "piano_basic_pitch",
                "piano_basic_pitch_clean",
                "piano_basic_pitch_playable",
                "piano_d3rm_clean",
                "piano_pti_consensus_clean",
                "piano_pti_clean",
                "piano_hft_clean",
                "piano_transkun_clean",
                "piano_polyphonic_clean",
            ],
            "lead_guitar": ["basic_pitch", "melodic_hpss_combined", "melodic_octave_fix"],
            "rhythm_guitar": ["basic_pitch", "melodic_hpss_combined", "melodic_octave_fix"],
        },
    },
    "research_ab": {
        "description": "Expose all plausible local research candidates without changing defaults.",
        "drum_engines": list(KNOWN_DRUM_ENGINES),
        "melodic_methods_by_instrument": {
            "bass": [
                "melodic_yin_octave_hps_fix",
                "melodic_yin_bass80",
                "melodic_adaptive",
                "melodic_octave_fix",
                "melodic_hpss_combined",
                "melodic_combined",
                "basic_pitch",
                "torchcrepe",
                "pyin",
                "melodic_pyin_bass_strict",
            ],
            "keys": [
                "piano_auto",
                "piano_basic_pitch_playable",
                "piano_basic_pitch",
                "piano_basic_pitch_clean",
                "piano_polyphonic_clean",
                "piano_transkun_clean",
                "piano_pti_consensus_clean",
                "piano_pti_clean",
                "piano_hft_clean",
                "piano_d3rm_clean",
                "melodic_octave_fix",
                "melodic_hpss_combined",
                "melodic_combined",
                "basic_pitch",
                "torchcrepe",
                "pyin",
            ],
            "lead_guitar": [
                "melodic_adaptive",
                "melodic_octave_fix",
                "melodic_hpss_combined",
                "melodic_combined",
                "melodic_combined_guitar",
                "basic_pitch",
                "torchcrepe",
                "pyin",
            ],
            "rhythm_guitar": [
                "melodic_hpss_combined",
                "melodic_adaptive",
                "melodic_octave_fix",
                "melodic_combined",
                "melodic_combined_guitar",
                "basic_pitch",
                "torchcrepe",
                "pyin",
            ],
        },
    },
}

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
    "guitar": (75.0, 1400.0),        # ~D2 (73 Hz) to ~F6 (1397 Hz) — one guitar
    "rhythm_guitar": (75.0, 1400.0), # legacy split (kept for old packs)
    "lead_guitar": (75.0, 1400.0),   # legacy split (kept for old packs)
    "keys": (27.0, 4200.0),          # ~A0 (27.5 Hz) to ~C8 (4186 Hz)
    "melodic": (45.0, 1700.0),       # legacy default
}

# `combined_filter` was the historical default, but it is the worst-recall
# heuristic engine on the E-GMD test set (per-drum-class F1 ~0.05 vs ~0.14
# for `adaptive_beat_grid` / `dsp_bandpass_improved`). Its single max-merged
# peak track plus the crash/ride cymbal-boost route nearly all hi-hat energy
# to crash — on a real funk/groove drum stem it emitted hi_hat=1 / crash=47
# where the groove is hat-on-every-eighth, collapsing recall. The
# `gameplay_default` profile already lists `beat_conditioned_multiband_decoder`
# first (dedicated hat peak track + kick/snare+hat overlay co-emission, so it
# recovers the hat pattern), so this aligns the global default — used by the
# explicit-engine path and the resilience fallback — with the engine
# production's profile path already prefers.
DEFAULT_DRUM_ENGINE = "beat_conditioned_multiband_decoder"
DEFAULT_DRUM_FILTER = DEFAULT_DRUM_ENGINE
DEFAULT_MELODIC_METHOD = "auto"
DEFAULT_TRANSCRIPTION_PROFILE = "gameplay_default"

MT3_MODELPACK_DIRNAME = "assets/models"

MT3_DRUM_ENGINE_MODEL_INFO: dict[str, dict[str, Any]] = {
    "mr_mt3_drums": {
        "engine": "mr_mt3_drums",
        "backend": "mt3",
        "model_id": "mr_mt3",
        "modelpack_id": "mr_mt3",
        "checkpoint_path": Path("files") / "checkpoints" / "mr_mt3" / "mt3.pth",
        "format": "pytorch",
        "size_mb": 176.0,
        "speed_x_realtime": 57.0,
        "description": "MR-MT3 drum transcription baseline",
    },
    "yourmt3_drums": {
        "engine": "yourmt3_drums",
        "backend": "mt3",
        "model_id": "yourmt3",
        "modelpack_id": "yourmt3",
        "checkpoint_path": Path("files")
        / "checkpoints"
        / "yourmt3"
        / "mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops"
        / "last.ckpt",
        "format": "pytorch_lightning",
        "size_mb": 536.0,
        "speed_x_realtime": 15.0,
        "description": "YourMT3 drum transcription research candidate",
    },
}

# In-house neural drum engine. NOT an MT3 engine (kept out of
# KNOWN_MT3_DRUM_ENGINES): a compact 5-class CRNN exported to ONNX, run
# in-process via onnxruntime. Opt-in only -- selectable via --drum-filter
# drum_crnn; not wired into any profile default.
DRUM_CRNN_ENGINE_MODEL_INFO: dict[str, dict[str, Any]] = {
    "drum_crnn": {
        "engine": "drum_crnn",
        "backend": "crnn",
        "model_id": "drum_crnn",
        "modelpack_id": "drum_crnn",
        "onnx_path": Path("files") / "drum_crnn.onnx",
        "format": "onnx",
        "num_classes": 5,
        "size_mb": 1.2,
        "description": "In-house drum CRNN (5-class: kick/snare/hi_hat/toms/cymbals), ONNX",
    },
}

_BENCHMARK_NOTE_TO_CLASS: dict[int, str] = {
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

_CLASS_TO_CANONICAL_NOTE: dict[str, int] = {
    "kick": 36,
    "snare": 38,
    "hi_hat": 42,
    "crash": 49,
    "ride": 51,
    "tom1": 48,
    "tom2": 47,
    "tom3": 41,
}


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
    meta: dict[str, Any] = field(default_factory=dict)


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
    used_score: float | None = None
    attempt_scores: dict[str, float] = field(default_factory=dict)


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
    used_score: float | None = None
    attempt_scores: dict[str, float] = field(default_factory=dict)


# Minimum plausibility score a producer's output must clear to be accepted
# immediately. Below this, the orchestrators keep looking for a better
# producer and only fall back to the best-scoring non-empty result.
MIN_TRANSCRIPTION_SCORE = 0.5
KEYS_PLAYABLE_MAX_POLYPHONY = 7
KEYS_PLAYABLE_ATTACK_CLUSTER_SEC = 0.055
KEYS_PLAYABLE_MAX_NOTES_PER_ATTACK = 7
KEYS_PLAYABLE_CONFIDENCE_PRUNE_MIN_NOTES = 32
KEYS_PLAYABLE_CONFIDENCE_PRUNE_MIN_DENSITY_PER_MIN = 300.0
KEYS_PLAYABLE_CONFIDENCE_PRUNE_MIN_VELOCITY = 70
KEYS_PLAYABLE_CONFIDENCE_PRUNE_MIN_RETAINED_RATIO = 0.25
KEYS_PLAYABLE_VELOCITY_CALIBRATION_MIN_NOTES = 20
KEYS_PLAYABLE_VELOCITY_CALIBRATION_MIN_DENSITY_PER_MIN = 240.0
KEYS_PLAYABLE_VELOCITY_CALIBRATION_MIN_MEDIAN_VELOCITY = 70.0
KEYS_PLAYABLE_VELOCITY_CALIBRATION_SCALE = 0.5
KEYS_PLAYABLE_VELOCITY_CALIBRATION_MIN_VELOCITY = 24
KEYS_PLAYABLE_VELOCITY_CALIBRATION_MAX_VELOCITY = 96
KEYS_PLAYABLE_FULL_SONG_VELOCITY_MIN_NOTES = 500
KEYS_PLAYABLE_FULL_SONG_VELOCITY_MIN_SPAN_SEC = 90.0
KEYS_PLAYABLE_FULL_SONG_VELOCITY_MAX_DENSITY_PER_MIN = 220.0
KEYS_PLAYABLE_FULL_SONG_VELOCITY_MIN_MEDIAN_VELOCITY = 70.0
KEYS_PLAYABLE_FULL_SONG_VELOCITY_MIN_LOWEST_VELOCITY = 50
KEYS_PLAYABLE_FULL_SONG_VELOCITY_SCALE = 0.45
KEYS_PLAYABLE_FULL_SONG_VELOCITY_MIN_VELOCITY = 24
KEYS_PLAYABLE_FULL_SONG_VELOCITY_MAX_VELOCITY = 96
KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MIN_NOTES = 120
KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MAX_NOTES = 320
KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MIN_SPAN_SEC = 45.0
KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MAX_SPAN_SEC = 90.0
KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MIN_DENSITY_PER_MIN = 200.0
KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MAX_DENSITY_PER_MIN = 300.0
KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MIN_MEAN_DURATION_SEC = 0.80
KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_LOW_PITCH_CUTOFF = 36
KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MIN_MAX_PITCH = 84
KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MAX_DURATION_SEC = 0.80
KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MIN_RETAINED_RATIO = 0.45
KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_MIN_NOTES = 70
KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_MAX_NOTES = 220
KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_MIN_SPAN_SEC = 120.0
KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_MAX_DENSITY_PER_MIN = 80.0
KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_MAX_MEAN_DURATION_SEC = 0.70
KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_MAX_MEDIAN_DURATION_SEC = 0.50
KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_LOW_PITCH_FLOOR = 36
KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_MIN_REPEATED_PITCH_RATIO = 0.15
KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_LOW_RANGE_MAX_PITCH = 70
KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_LOW_RANGE_MIN_DURATION_SEC = 1.0
KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_UPPER_RANGE_MIN_DURATION_SEC = 0.5
KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_MIN_RETAINED_NOTES = 12
KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_MIN_RETAINED_RATIO = 0.10
KEYS_PLAYABLE_SUSTAIN_MIN_NOTES = 24
KEYS_PLAYABLE_SUSTAIN_MIN_DENSITY_PER_MIN = 180.0
KEYS_PLAYABLE_SUSTAIN_MIN_DURATION_SEC = 0.5
KEYS_PLAYABLE_SUSTAIN_MAX_DURATION_SEC = 1.2
KEYS_PLAYABLE_SUSTAIN_NEXT_CLUSTER_TAIL_SEC = 0.1
KEYS_PLAYABLE_SUSTAIN_SAME_PITCH_GAP_SEC = 0.03
KEYS_PLAYABLE_RECALL_RESTORE_MIN_DENSITY_PER_MIN = 300.0
KEYS_PLAYABLE_RECALL_RESTORE_MIN_VELOCITY = 73
KEYS_PLAYABLE_RECALL_RESTORE_MIN_DURATION_SEC = 0.26
KEYS_PLAYABLE_RECALL_RESTORE_DUPLICATE_SEC = 0.075
KEYS_PLAYABLE_RECALL_RESTORE_MAX_ADDED_NOTES = 16
KEYS_PLAYABLE_RELEASE_SUSTAIN_MIN_NOTES = 24
KEYS_PLAYABLE_RELEASE_SUSTAIN_MIN_DENSITY_PER_MIN = 180.0
KEYS_PLAYABLE_RELEASE_SUSTAIN_MIN_DURATION_SEC = 1.0
KEYS_PLAYABLE_RELEASE_SUSTAIN_MAX_DURATION_SEC = 2.0
KEYS_PLAYABLE_RELEASE_SUSTAIN_NEXT_CLUSTER_TAIL_SEC = 0.1
KEYS_PLAYABLE_RELEASE_SUSTAIN_SAME_PITCH_GAP_SEC = 0.06
KEYS_PLAYABLE_RELEASE_SUSTAIN_MIN_PITCH = 52
KEYS_PLAYABLE_SPARSE_RELEASE_SUSTAIN_MIN_NOTES = 12
KEYS_PLAYABLE_SPARSE_RELEASE_SUSTAIN_MAX_NOTES = 60
KEYS_PLAYABLE_SPARSE_RELEASE_SUSTAIN_MIN_DENSITY_PER_MIN = 80.0
KEYS_PLAYABLE_SPARSE_RELEASE_SUSTAIN_MAX_DENSITY_PER_MIN = 180.0
KEYS_PLAYABLE_SPARSE_RELEASE_SUSTAIN_MIN_CLUSTER_MEAN = 1.2
KEYS_PLAYABLE_SPARSE_RELEASE_SUSTAIN_MIN_DURATION_SEC = 2.0
KEYS_PLAYABLE_SPARSE_RELEASE_SUSTAIN_MAX_DURATION_SEC = 2.0
KEYS_PLAYABLE_LOW_DENSE_RELEASE_SUSTAIN_MIN_NOTES = 80
KEYS_PLAYABLE_LOW_DENSE_RELEASE_SUSTAIN_MIN_DENSITY_PER_MIN = 500.0
KEYS_PLAYABLE_LOW_DENSE_RELEASE_SUSTAIN_LOW_REGISTER_MAX_PITCH = 45
KEYS_PLAYABLE_LOW_DENSE_RELEASE_SUSTAIN_MAX_MEAN_DURATION_SEC = 0.8
KEYS_PLAYABLE_LOW_DENSE_RELEASE_SUSTAIN_MIN_PITCH = 45
KEYS_PLAYABLE_LOW_DENSE_RELEASE_SUSTAIN_MIN_DURATION_SEC = 2.0
KEYS_PLAYABLE_LOW_DENSE_RELEASE_SUSTAIN_MAX_DURATION_SEC = 2.0
KEYS_PLAYABLE_DENSE_HIGH_STACCATO_TRIM_MIN_NOTES = 120
KEYS_PLAYABLE_DENSE_HIGH_STACCATO_TRIM_MIN_DENSITY_PER_MIN = 600.0
KEYS_PLAYABLE_DENSE_HIGH_STACCATO_TRIM_MIN_PITCH_FLOOR = 50
KEYS_PLAYABLE_DENSE_HIGH_STACCATO_TRIM_MIN_MEAN_DURATION_SEC = 0.42
KEYS_PLAYABLE_DENSE_HIGH_STACCATO_TRIM_MAX_MEAN_DURATION_SEC = 0.70
KEYS_PLAYABLE_DENSE_HIGH_STACCATO_TRIM_MIN_PITCH = 56
KEYS_PLAYABLE_DENSE_HIGH_STACCATO_TRIM_MAX_DURATION_SEC = 0.25
KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_MIN_NOTES = 20
KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_MAX_NOTES = 60
KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_MIN_DENSITY_PER_MIN = 180.0
KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_MAX_DENSITY_PER_MIN = 260.0
KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_LOW_REGISTER_MAX_PITCH = 40
KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_MIN_MEAN_DURATION_SEC = 1.0
KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_MIN_PITCH = 58
KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_MAX_DURATION_SEC = 0.35
KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_VELOCITY_SCALE = 0.5
KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_MIN_VELOCITY = 24
KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_MAX_VELOCITY = 96
KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MIN_NOTES = 40
KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MAX_NOTES = 60
KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MIN_DENSITY_PER_MIN = 180.0
KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MAX_DENSITY_PER_MIN = 260.0
KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_LOW_REGISTER_MAX_PITCH = 40
KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MIN_LOW_NOTES = 15
KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_LOW_NOTE_MAX_PITCH = 54
KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_HIGH_NOTE_MIN_PITCH = 76
KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MAX_HIGH_NOTES = 4
KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MIN_MAX_PITCH = 80
KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MIN_MEAN_DURATION_SEC = 0.65
KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_SOURCE_MAX_PITCH = 53
KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_TARGET_MIN_PITCH = 55
KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_TARGET_MAX_PITCH = 94
KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_VELOCITY_SCALE = 0.55
KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MIN_VELOCITY = 24
KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MAX_VELOCITY = 96
KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MAX_ADDED_NOTES = 16
KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_MIN_NOTES = 40
KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_MAX_NOTES = 70
KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_MIN_DENSITY_PER_MIN = 260.0
KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_MAX_DENSITY_PER_MIN = 330.0
KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_LOW_REGISTER_MAX_PITCH = 40
KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_MIN_LOW_NOTES = 15
KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_LOW_NOTE_MAX_PITCH = 54
KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_HIGH_NOTE_MIN_PITCH = 76
KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_MAX_HIGH_NOTES = 4
KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_MIN_MAX_PITCH = 80
KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_MIN_MEAN_DURATION_SEC = 0.65
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MIN_NOTES = 40
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MAX_NOTES = 70
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MIN_DENSITY_PER_MIN = 250.0
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MAX_DENSITY_PER_MIN = 330.0
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LOW_REGISTER_MAX_PITCH = 40
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MIN_LOW_NOTES = 15
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LOW_NOTE_MAX_PITCH = 54
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_HIGH_NOTE_MIN_PITCH = 76
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MAX_HIGH_NOTES = 4
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MIN_MAX_PITCH = 80
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MIN_MEAN_DURATION_SEC = 0.65
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_CLUSTER_MERGE_SEC = 0.16
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_NEAR_STRONG_SEC = 0.60
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_FOLLOWING_STRONG_MIN_SEC = 0.12
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LONG_LOW_DURATION_SEC = 0.80
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_ACTIVE_LOW_PITCH = 45
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_ONSET_LOW_PITCH = 50
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_INITIAL_SEC = 0.10
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_PICKUP_MAX_SEC = 0.80
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_PICKUP_MIN_PITCH = 80
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MIN_PITCH = 31
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MAX_PITCH = 94
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LOW_RATIO = 8.0
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MID_RATIO = 5.0
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_HIGH_RATIO = 4.0
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_EXISTING_RATIO = 3.0
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_ADJACENT_RATIO = 0.80
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_CHORD_GAP_SEC = 0.06
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_PICKUP_GAP_SEC = 0.03
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MIN_DURATION_SEC = 0.22
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MAX_DURATION_SEC = 2.0
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_PICKUP_MIN_DURATION_SEC = 0.08
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_PICKUP_MAX_DURATION_SEC = 0.22
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_VELOCITY = 24
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_SCORE_TOLERANCE = 0.001
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LOCAL_MIN_NOTES = 120
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LOCAL_WINDOW_SEC = 12.0
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LOCAL_STEP_SEC = 3.0
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LOCAL_MAX_NOTE_RATIO = 1.10
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LOCAL_SCORE_TOLERANCE = 0.012
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_FFT_PRE_SEC = 0.04
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_FFT_WINDOW_SEC = 0.24
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_FFT_OVERSAMPLE = 4
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_NOISE_MIN_HZ = 80.0
KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_NOISE_MAX_HZ = 1800.0
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MIN_NOTES = 95
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_NOTES = 125
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MIN_DENSITY_PER_MIN = 430.0
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_DENSITY_PER_MIN = 510.0
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MIN_CLUSTER_MEAN = 2.1
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_LOW_REGISTER_MAX_PITCH = 45
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_EXISTING_PITCH = 82
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MIN_LOW_NOTES = 10
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_LOW_NOTES = 28
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_LOW_NOTE_MAX_PITCH = 54
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MIN_UPPER_NOTES = 8
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_UPPER_NOTES = 26
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_UPPER_NOTE_MIN_PITCH = 74
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_HIGH_NOTE_MIN_PITCH = 84
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_HIGH_NOTES = 0
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MIN_MEAN_DURATION_SEC = 0.5
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_MEAN_DURATION_SEC = 0.85
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MIN_PITCH = 58
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_PITCH = 96
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MID_RATIO = 45.0
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_HIGH_RATIO = 30.0
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_HIGH_RELATIVE_RATIO = 0.25
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_CHORD_GAP_SEC = 0.06
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MIN_DURATION_SEC = 0.12
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_DURATION_SEC = 1.2
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_VELOCITY = 35
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_ADDED_NOTES = 20
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_ADDED_PER_CLUSTER = 2
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_RELATION_INTERVALS = (
    7,
    12,
    14,
    15,
    16,
    17,
    19,
    21,
    24,
    26,
    28,
    31,
    36,
)
KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_HIGH_RELATIVE_INTERVALS = (7, 12, 19, 24)
PIANO_BASIC_PITCH_PLAYABLE_ONSET_THRESHOLD = 0.6
PIANO_BASIC_PITCH_PLAYABLE_FRAME_THRESHOLD = 0.25
PIANO_BASIC_PITCH_PLAYABLE_MIN_NOTE_LENGTH_MS = 100.0
PIANO_BASIC_PITCH_PLAYABLE_AGGRESSIVE_ONSET_THRESHOLD = 0.7
PIANO_BASIC_PITCH_PLAYABLE_AGGRESSIVE_FRAME_THRESHOLD = 0.25
PIANO_BASIC_PITCH_PLAYABLE_AGGRESSIVE_MIN_NOTE_LENGTH_MS = 127.7
PIANO_BASIC_PITCH_PLAYABLE_MID_ONSET_THRESHOLD = 0.65
PIANO_BASIC_PITCH_PLAYABLE_MID_FRAME_THRESHOLD = 0.25
PIANO_BASIC_PITCH_PLAYABLE_MID_MIN_NOTE_LENGTH_MS = 127.7
PIANO_BASIC_PITCH_PLAYABLE_LOOSE_ONSET_THRESHOLD = 0.55
PIANO_BASIC_PITCH_PLAYABLE_LOOSE_FRAME_THRESHOLD = 0.20
PIANO_BASIC_PITCH_PLAYABLE_LOOSE_MIN_NOTE_LENGTH_MS = 80.0
KEYS_PLAYABLE_PROFILE_VERY_DENSE_MIN_DENSITY_PER_MIN = 550.0
KEYS_PLAYABLE_PROFILE_VERY_DENSE_MIN_NOTES = 120
KEYS_PLAYABLE_PROFILE_UPPER_SPARSE_MAX_DENSITY_PER_MIN = 300.0
KEYS_PLAYABLE_PROFILE_UPPER_SPARSE_MIN_PITCH = 45
KEYS_PLAYABLE_PROFILE_UPPER_SPARSE_MAX_MEAN_DURATION_SEC = 1.0
KEYS_PLAYABLE_PROFILE_CHORDAL_MIN_CLUSTER_MEAN = 2.0
KEYS_PLAYABLE_PROFILE_CHORDAL_MAX_NOTES = 100
KEYS_PLAYABLE_DEFAULT_CANDIDATE_SPARSE_MAX_DENSITY_PER_MIN = 260.0
KEYS_PLAYABLE_DEFAULT_CANDIDATE_MIN_SCORE_MARGIN = 0.01
KEYS_PLAYABLE_DEFAULT_CANDIDATE_FEWER_LOW_ARTIFACT_MAX_MIN_PITCH = 30
KEYS_PLAYABLE_DEFAULT_CANDIDATE_FEWER_MAX_NOTE_RATIO = 0.95
KEYS_PLAYABLE_DEFAULT_CANDIDATE_FEWER_SCORE_TOLERANCE = 0.001
KEYS_PLAYABLE_DEFAULT_CANDIDATE_DENSE_MIN_DENSITY_PER_MIN = 500.0
KEYS_PLAYABLE_DEFAULT_CANDIDATE_DENSE_MAX_NOTE_RATIO = 1.15
KEYS_PLAYABLE_DEFAULT_CANDIDATE_DENSE_SCORE_TOLERANCE = 0.001
KEYS_PLAYABLE_LOOSE_CANDIDATE_SELECTED_MAX_DENSITY_PER_MIN = 260.0
KEYS_PLAYABLE_LOOSE_CANDIDATE_SELECTED_MAX_MEAN_DURATION_SEC = 1.0
KEYS_PLAYABLE_LOOSE_CANDIDATE_SELECTED_LOW_ARTIFACT_PITCH = 45
KEYS_PLAYABLE_LOOSE_CANDIDATE_MIN_NOTES = 20
KEYS_PLAYABLE_LOOSE_CANDIDATE_MIN_PITCH = 45
KEYS_PLAYABLE_LOOSE_CANDIDATE_MAX_DENSITY_PER_MIN = 260.0
KEYS_PLAYABLE_LOOSE_CANDIDATE_MAX_NOTE_RATIO = 0.85
KEYS_PLAYABLE_LOOSE_CANDIDATE_MIN_SCORE_MARGIN = 0.03
KEYS_PLAYABLE_SPARSE_LOW_FLOOD_MIN_NOTES = 120
KEYS_PLAYABLE_SPARSE_LOW_FLOOD_MAX_DENSITY_PER_MIN = 120.0
KEYS_PLAYABLE_SPARSE_LOW_FLOOD_MIN_LOW_RATIO = 0.30
KEYS_PLAYABLE_SPARSE_LOW_FLOOD_MIN_SPAN_SEC = 90.0
KEYS_PLAYABLE_SPARSE_LOW_FLOOD_LOW_PITCH_CUTOFF = 36
KEYS_PLAYABLE_SPARSE_LOW_FLOOD_HIGH_PITCH_CUTOFF = 88
KEYS_PLAYABLE_SPARSE_LOW_FLOOD_MIN_RETAINED_RATIO = 0.30
KEYS_PLAYABLE_AUDIO_ALIGN_MIN_NOTES = 80
KEYS_PLAYABLE_AUDIO_ALIGN_MIN_DENSITY_PER_MIN = 500.0
KEYS_PLAYABLE_AUDIO_ALIGN_LOW_REGISTER_MAX_PITCH = 45
KEYS_PLAYABLE_AUDIO_ALIGN_MAX_MEAN_DURATION_SEC = 0.8
KEYS_PLAYABLE_AUDIO_ALIGN_SEARCH_SEC = 0.06
KEYS_PLAYABLE_AUDIO_ALIGN_MAX_SHIFT_SEC = 0.04
KEYS_PLAYABLE_AUDIO_ALIGN_MIN_SHIFT_SEC = 0.012
KEYS_PLAYABLE_AUDIO_ALIGN_MIN_PEAK = 0.06
KEYS_PLAYABLE_AUDIO_ALIGN_MIN_LOCAL_MEDIAN_RATIO = 1.3
KEYS_PLAYABLE_AUDIO_ALIGN_SAMPLE_RATE = 22050
KEYS_PLAYABLE_AUDIO_ALIGN_HOP_LENGTH = 256
KEYS_PLAYABLE_AUDIO_ALIGN_PEAK_PROMINENCE = 0.01
KEYS_PLAYABLE_AUDIO_ALIGN_PEAK_MIN_DISTANCE_SEC = 0.035


@dataclass(frozen=True)
class _KeysBasicPitchProfile:
    name: str
    onset_threshold: float
    frame_threshold: float
    minimum_note_length_ms: float


KEYS_BASIC_PITCH_BALANCED_PROFILE = _KeysBasicPitchProfile(
    name="balanced",
    onset_threshold=PIANO_BASIC_PITCH_PLAYABLE_ONSET_THRESHOLD,
    frame_threshold=PIANO_BASIC_PITCH_PLAYABLE_FRAME_THRESHOLD,
    minimum_note_length_ms=PIANO_BASIC_PITCH_PLAYABLE_MIN_NOTE_LENGTH_MS,
)
KEYS_BASIC_PITCH_AGGRESSIVE_PROFILE = _KeysBasicPitchProfile(
    name="aggressive",
    onset_threshold=PIANO_BASIC_PITCH_PLAYABLE_AGGRESSIVE_ONSET_THRESHOLD,
    frame_threshold=PIANO_BASIC_PITCH_PLAYABLE_AGGRESSIVE_FRAME_THRESHOLD,
    minimum_note_length_ms=PIANO_BASIC_PITCH_PLAYABLE_AGGRESSIVE_MIN_NOTE_LENGTH_MS,
)
KEYS_BASIC_PITCH_MID_PROFILE = _KeysBasicPitchProfile(
    name="mid",
    onset_threshold=PIANO_BASIC_PITCH_PLAYABLE_MID_ONSET_THRESHOLD,
    frame_threshold=PIANO_BASIC_PITCH_PLAYABLE_MID_FRAME_THRESHOLD,
    minimum_note_length_ms=PIANO_BASIC_PITCH_PLAYABLE_MID_MIN_NOTE_LENGTH_MS,
)
KEYS_BASIC_PITCH_LOOSE_PROFILE = _KeysBasicPitchProfile(
    name="loose",
    onset_threshold=PIANO_BASIC_PITCH_PLAYABLE_LOOSE_ONSET_THRESHOLD,
    frame_threshold=PIANO_BASIC_PITCH_PLAYABLE_LOOSE_FRAME_THRESHOLD,
    minimum_note_length_ms=PIANO_BASIC_PITCH_PLAYABLE_LOOSE_MIN_NOTE_LENGTH_MS,
)


@dataclass(frozen=True)
class _KeysPlayableProfileFeatures:
    note_count: int
    density_per_minute: float
    mean_attack_cluster_size: float
    min_pitch: int | None
    mean_duration_sec: float


def _event_onset(event: Any) -> float:
    """Onset time of a MelodicNote (``t_on``) or DrumEvent (``time``)."""
    if hasattr(event, "t_on"):
        return float(getattr(event, "t_on", 0.0))
    return float(getattr(event, "time", 0.0))


def _event_pitch(event: Any) -> int | None:
    """MIDI pitch of a MelodicNote (``pitch``); None when not pitch-meaningful."""
    if hasattr(event, "pitch"):
        return int(getattr(event, "pitch"))
    return None


def _stem_active_seconds(stem_path: Path) -> tuple[float, float, list[tuple[float, float]]]:
    """Estimate the active (non-silent) duration of a stem.

    Returns ``(active_seconds, total_seconds, silent_windows)`` where
    ``silent_windows`` is a list of ``(start, end)`` time ranges (seconds)
    judged silent by an RMS floor. Returns ``(0.0, 0.0, [])`` when the audio
    can't be read.
    """
    from aural_ingest.algorithms._common import frame_rms_series, read_wav_mono_normalized

    try:
        samples, sr = read_wav_mono_normalized(stem_path)
    except Exception:
        return 0.0, 0.0, []
    if not samples or sr <= 0:
        return 0.0, 0.0, []

    frame = max(1, int(0.05 * sr))
    hop = frame
    rms = frame_rms_series(samples, frame, hop)
    if not rms:
        return 0.0, 0.0, []
    peak = max(rms)
    if peak <= 0.0:
        return 0.0, 0.0, []
    floor = max(1e-4, peak * 0.05)
    hop_sec = hop / float(sr)
    total = len(rms) * hop_sec
    active = 0.0
    silent_windows: list[tuple[float, float]] = []
    for idx, value in enumerate(rms):
        start = idx * hop_sec
        if value >= floor:
            active += hop_sec
        else:
            silent_windows.append((start, start + hop_sec))
    return active, total, silent_windows


def _octave_spread_score(pitches: list[int]) -> float:
    """0..1 sanity score from the pitch spread.

    A plausible melodic/poly part spans a few octaves; a hallucinating model
    sprays notes across the full keyboard (or collapses to a single repeated
    pitch). Penalize both extremes.
    """
    if not pitches:
        return 0.0
    spread = (max(pitches) - min(pitches)) / 12.0  # octaves
    if spread <= 0.0:
        # Single repeated pitch -- suspicious unless there's only one note.
        return 1.0 if len(pitches) <= 1 else 0.4
    if spread <= 4.0:
        return 1.0
    # Decay past 4 octaves; ~7 octaves (full keyboard) -> ~0.4.
    return max(0.2, 1.0 - (spread - 4.0) * 0.2)


def score_transcription(events: list[Any], stem_path: Path | None = None) -> float:
    """Plausibility score in ``[0, 1]`` for a transcription result.

    Combines (where information is available):
      * note density vs the stem's active (non-silent) duration,
      * octave-spread sanity (melodic/poly notes only),
      * the fraction of notes whose onset lands in a silent stem region.

    With no stem, scores from octave-spread plus a coarse density check
    against the transcription's own time span. An empty result scores 0.
    """
    if not events:
        return 0.0

    onsets = [_event_onset(e) for e in events]
    pitches = [p for p in (_event_pitch(e) for e in events) if p is not None]

    components: list[float] = []

    if pitches:
        components.append(_octave_spread_score(pitches))

    active_sec = total_sec = 0.0
    silent_windows: list[tuple[float, float]] = []
    if stem_path is not None:
        active_sec, total_sec, silent_windows = _stem_active_seconds(stem_path)

    # Only trust the stem-energy reference when the stem reads as reliably
    # active. Mostly-silent stems (click tracks, near-silent or unreadable
    # audio) make the RMS-based density/silence map pathological, so we fall
    # back to the self-consistent span-based density check below.
    stem_reliable = total_sec > 0.0 and (active_sec / total_sec) >= 0.25

    if stem_reliable:
        # Notes per active second. Real parts rarely exceed ~25 onsets/sec;
        # well above that signals onset-spam hallucination.
        density = len(events) / active_sec
        if density <= 12.0:
            density_score = 1.0
        else:
            density_score = max(0.1, 1.0 - (density - 12.0) * 0.05)
        components.append(density_score)

        # Fraction of onsets landing in silent stem regions.
        if silent_windows:
            silent = 0
            for t in onsets:
                for lo, hi in silent_windows:
                    if lo <= t < hi:
                        silent += 1
                        break
            silent_frac = silent / float(len(onsets))
            components.append(max(0.0, 1.0 - silent_frac))
    else:
        # No reliable stem energy reference: density against the result's own
        # time span.
        span = max(onsets) - min(onsets) if len(onsets) > 1 else 0.0
        if span > 0.0:
            density = len(events) / span
            density_score = 1.0 if density <= 12.0 else max(0.1, 1.0 - (density - 12.0) * 0.05)
            components.append(density_score)

    if not components:
        return 0.5
    return max(0.0, min(1.0, sum(components) / len(components)))


def is_mt3_drum_engine(engine_id: str | None) -> bool:
    if engine_id is None:
        return False
    return str(engine_id).strip().lower() in KNOWN_MT3_DRUM_ENGINES


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    return value


def drum_engine_metadata(engine_id: str) -> dict[str, Any]:
    normalized = str(engine_id).strip().lower()
    if normalized in MT3_DRUM_ENGINE_MODEL_INFO:
        return _json_safe_value(MT3_DRUM_ENGINE_MODEL_INFO[normalized])
    if normalized in DRUM_CRNN_ENGINE_MODEL_INFO:
        return _json_safe_value(DRUM_CRNN_ENGINE_MODEL_INFO[normalized])
    return {
        "engine": normalized,
        "backend": "heuristic",
        "description": "Heuristic/DSP drum transcription engine",
    }


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def _candidate_model_roots(base: Path) -> list[Path]:
    return [
        base,
        base / MT3_MODELPACK_DIRNAME,
        base / "data" / MT3_MODELPACK_DIRNAME,
        base / "AuralPrimerPortable" / "data" / MT3_MODELPACK_DIRNAME,
    ]


def _default_mt3_model_search_roots(stem_path: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        meipass_path = Path(str(meipass))
        roots.extend([meipass_path, meipass_path.parent])

    try:
        exe_dir = Path(sys.executable).resolve().parent
        roots.extend([exe_dir, exe_dir.parent, exe_dir.parent.parent])
    except Exception:
        pass

    cwd = Path.cwd()
    roots.extend([cwd, cwd.parent])

    try:
        this_file = Path(__file__).resolve()
        roots.extend([this_file.parent, this_file.parents[2], this_file.parents[4]])
    except Exception:
        pass

    if stem_path is not None:
        try:
            roots.extend(list(stem_path.resolve().parents[:6]))
        except Exception:
            pass

    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        for candidate in _candidate_model_roots(root):
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
    return deduped


def _iter_installed_modelpack_dirs(modelpack_id: str, search_roots: Iterable[Path]) -> list[Path]:
    matches: list[Path] = []
    for root in search_roots:
        candidate_root = Path(root)
        if candidate_root.name == modelpack_id and (candidate_root / "modelpack.json").is_file():
            matches.append(candidate_root)
            continue

        id_root = candidate_root / modelpack_id
        if not id_root.is_dir():
            continue
        for version_dir in sorted(
            [child for child in id_root.iterdir() if child.is_dir()],
            key=lambda item: item.name,
            reverse=True,
        ):
            if (version_dir / "modelpack.json").is_file():
                matches.append(version_dir)
    return matches


def _read_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text("utf-8"))


def _resolve_mt3_checkpoint_from_manifest(
    model_root: Path,
    manifest: dict[str, Any],
    model_id: str,
    default_relpath: Path,
) -> Path | None:
    checkpoints = manifest.get("checkpoints")
    if isinstance(checkpoints, list):
        for item in checkpoints:
            if not isinstance(item, dict):
                continue
            candidate_model = str(item.get("model", "")).strip().lower()
            candidate_path = str(item.get("path", "")).strip()
            if candidate_model and candidate_model != model_id:
                continue
            if not candidate_path:
                continue
            candidate = model_root / Path(candidate_path)
            if candidate.is_file():
                return candidate

    candidate = model_root / default_relpath
    if candidate.is_file():
        return candidate

    trimmed = default_relpath
    if list(trimmed.parts[:2]) == ["files", "checkpoints"]:
        candidate = model_root / Path("checkpoints") / Path(*trimmed.parts[2:])
        if candidate.is_file():
            return candidate
    return None


def resolve_mt3_modelpack(
    engine_id: str,
    *,
    stem_path: Path | None = None,
    search_roots: Iterable[Path | str] | None = None,
) -> dict[str, Any]:
    engine = str(engine_id).strip().lower()
    info = MT3_DRUM_ENGINE_MODEL_INFO.get(engine)
    if info is None:
        raise FileNotFoundError(f"unknown MT3 drum engine '{engine_id}'")

    explicit_checkpoint = os.getenv(f"AURALPRIMER_{str(info['model_id']).upper()}_CHECKPOINT_PATH")
    if explicit_checkpoint:
        checkpoint_path = Path(explicit_checkpoint).expanduser()
        if checkpoint_path.is_file():
            return _json_safe_value({
                **info,
                "checkpoint_path_resolved": checkpoint_path,
                "modelpack_root": checkpoint_path.parent,
                "modelpack_manifest": {},
                "modelpack_version": "explicit",
            })
        raise FileNotFoundError(f"configured checkpoint does not exist: {checkpoint_path}")

    env_checkpoint_root = os.getenv("MT3_CHECKPOINT_DIR")
    if env_checkpoint_root:
        env_candidate = Path(env_checkpoint_root).expanduser() / Path(info["checkpoint_path"])
        if env_candidate.is_file():
            return _json_safe_value({
                **info,
                "checkpoint_path_resolved": env_candidate,
                "modelpack_root": Path(env_checkpoint_root).expanduser(),
                "modelpack_manifest": {},
                "modelpack_version": "env",
            })

    if search_roots is not None:
        roots = []
        seen: set[str] = set()
        for raw_root in search_roots:
            for candidate in _candidate_model_roots(Path(raw_root)):
                key = str(candidate)
                if key in seen:
                    continue
                seen.add(key)
                roots.append(candidate)
    else:
        roots = _default_mt3_model_search_roots(stem_path)
    for model_root in _iter_installed_modelpack_dirs(str(info["modelpack_id"]), roots):
        manifest_path = model_root / "modelpack.json"
        manifest = _read_json_file(manifest_path)
        checkpoint_path = _resolve_mt3_checkpoint_from_manifest(
            model_root,
            manifest,
            str(info["model_id"]),
            Path(info["checkpoint_path"]),
        )
        if checkpoint_path is None:
            continue
        return _json_safe_value({
            **info,
            "checkpoint_path_resolved": checkpoint_path,
            "modelpack_root": model_root,
            "modelpack_manifest": manifest,
            "modelpack_version": str(manifest.get("version", "unknown")).strip() or "unknown",
        })

    searched = ", ".join(str(root) for root in roots)
    raise FileNotFoundError(
        f"missing modelpack for {engine}: expected installed '{info['modelpack_id']}' checkpoint under files/checkpoints; searched {searched}"
    )


def available_mt3_modelpacks(
    search_roots: Iterable[Path | str] | None = None,
    *,
    stem_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for engine_id in KNOWN_MT3_DRUM_ENGINES:
        info = MT3_DRUM_ENGINE_MODEL_INFO[engine_id]
        try:
            resolved = resolve_mt3_modelpack(engine_id, stem_path=stem_path, search_roots=search_roots)
            out[engine_id] = {
                "ok": True,
                "backend": "mt3",
                "engine": engine_id,
                "model_id": resolved["model_id"],
                "modelpack_id": resolved["modelpack_id"],
                "modelpack_version": resolved["modelpack_version"],
                "checkpoint_path": str(resolved["checkpoint_path_resolved"]),
                "modelpack_root": str(resolved["modelpack_root"]),
                "size_mb": resolved.get("size_mb"),
                "speed_x_realtime": resolved.get("speed_x_realtime"),
                "description": resolved.get("description"),
            }
        except Exception as exc:
            out[engine_id] = {
                "ok": False,
                "backend": "mt3",
                "engine": engine_id,
                "model_id": info["model_id"],
                "modelpack_id": info["modelpack_id"],
                "size_mb": info.get("size_mb"),
                "speed_x_realtime": info.get("speed_x_realtime"),
                "description": info.get("description"),
                "error": str(exc),
            }
    return out


def _midi_note_to_benchmark_class(note: int) -> str | None:
    return _BENCHMARK_NOTE_TO_CLASS.get(int(note))


def _normalize_midi_note_to_canonical(note: int) -> int | None:
    drum_class = _midi_note_to_benchmark_class(note)
    if drum_class is None:
        return None
    return _CLASS_TO_CANONICAL_NOTE[drum_class]


def _midi_to_drum_events(midi_file: Any) -> list[DrumEvent]:
    import mido

    merged = mido.merge_tracks(midi_file.tracks)
    ticks_per_beat = int(getattr(midi_file, "ticks_per_beat", 480) or 480)
    tempo = 500000
    current_time_sec = 0.0
    events: list[DrumEvent] = []
    saw_drum_channel = False

    for msg in merged:
        current_time_sec += mido.tick2second(msg.time, ticks_per_beat, tempo)
        if msg.type == "set_tempo":
            tempo = int(msg.tempo)
            continue
        if msg.type != "note_on" or int(getattr(msg, "velocity", 0)) <= 0:
            continue
        channel = getattr(msg, "channel", None)
        note = int(getattr(msg, "note", 0))
        if channel == 9:
            saw_drum_channel = True
        canonical_note = _normalize_midi_note_to_canonical(note)
        if canonical_note is None:
            continue
        if saw_drum_channel and channel not in (None, 9):
            continue
        events.append(
            DrumEvent(
                time=max(0.0, float(current_time_sec)),
                note=canonical_note,
                velocity=max(1, min(127, int(getattr(msg, "velocity", 100)))),
            )
        )
    return events


def _transcribe_drums_mt3_events(
    stem_path: Path,
    engine_id: str,
    *,
    search_roots: Iterable[Path | str] | None = None,
) -> tuple[list[DrumEvent], dict[str, Any]]:
    import librosa

    resolved = resolve_mt3_modelpack(engine_id, stem_path=stem_path, search_roots=search_roots)
    checkpoint_path = Path(resolved["checkpoint_path_resolved"])
    modelpack_root = Path(resolved["modelpack_root"])
    os.environ["MT3_CHECKPOINT_DIR"] = str(modelpack_root)
    audio, sr = librosa.load(str(stem_path), sr=16000, mono=True)
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    try:
        with suppress_mt3_runtime_warnings():
            with contextlib.redirect_stdout(captured_stdout), contextlib.redirect_stderr(captured_stderr):
                ensure_mt3_transformers_compat()
                from mt3_infer import load_model

                model = load_model(
                    str(resolved["model_id"]),
                    checkpoint_path=str(checkpoint_path),
                    device=select_device("AURAL_MT3_DEVICE"),
                    auto_download=False,
                )
                midi = model.transcribe(audio.astype("float32"), sr=sr)
    except Exception as exc:
        detail = "\n".join(
            part for part in (captured_stdout.getvalue().strip(), captured_stderr.getvalue().strip()) if part
        )
        if detail:
            raise RuntimeError(f"MT3 inference failed: {exc}\n{detail}") from exc
        raise
    return _midi_to_drum_events(midi), {
        "backend": "mt3",
        "model_id": resolved["model_id"],
        "modelpack_id": resolved["modelpack_id"],
        "modelpack_version": resolved["modelpack_version"],
        "checkpoint_path": str(checkpoint_path),
        "modelpack_root": str(modelpack_root),
        "size_mb": resolved.get("size_mb"),
        "speed_x_realtime": resolved.get("speed_x_realtime"),
    }


def build_default_drum_algorithm_registry() -> dict[str, DrumTranscriber]:
    # Import lazily to keep module import lightweight and avoid unnecessary startup costs.
    from aural_ingest.algorithms import (
        adaptive_beat_grid,
        adaptive_beat_grid_multilabel,
        aural_onset,
        beat_conditioned_multiband_decoder,
        combined_filter,
        drum_crnn,
        dsp_bandpass,
        dsp_bandpass_improved,
        dsp_spectral_flux,
        hpss_percussive,
        hybrid_kick_grid,
        librosa_superflux,
        librosa_superflux_dense,
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

    registry: dict[str, DrumTranscriber] = {
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
        "librosa_superflux_dense": librosa_superflux_dense.transcribe,
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
        # In-house neural engine (opt-in). Raises RuntimeError at inference if
        # its ONNX/onnxruntime is absent, so the DSP fallback chain takes over.
        "drum_crnn": drum_crnn.transcribe,
    }

    def _wrap_mt3(engine_id: str) -> DrumTranscriber:
        def _runner(stem_path: Path) -> list[DrumEvent]:
            events, _meta = _transcribe_drums_mt3_events(stem_path, engine_id)
            return events

        return _runner

    for engine_id in KNOWN_MT3_DRUM_ENGINES:
        registry[engine_id] = _wrap_mt3(engine_id)

    return registry


def _ascend_past_worktree(start: Path) -> Path | None:
    """If ``start`` is inside a ``.claude/worktrees/<name>/`` directory, return
    the path *above* ``.claude`` (i.e. the main checkout root). Otherwise None.

    Lets model auto-discovery find checkpoints stored under the main repo's
    ``assets/models/`` when callers run from a worktree.
    """
    try:
        parts = start.parts
    except Exception:
        return None
    for idx in range(len(parts) - 1):
        if parts[idx] == ".claude" and parts[idx + 1] == "worktrees":
            return Path(*parts[:idx])
    return None


def _default_basic_pitch_model_roots() -> list[Path]:
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(str(meipass)))

    cwd = Path.cwd()
    roots.append(cwd)
    main_repo_from_cwd = _ascend_past_worktree(cwd)
    if main_repo_from_cwd is not None:
        roots.append(main_repo_from_cwd)

    try:
        # Prefer repository-local roots when running from source.
        this_file = Path(__file__).resolve()
        roots.extend([this_file.parent, this_file.parents[2], this_file.parents[4]])
        main_repo_from_module = _ascend_past_worktree(this_file)
        if main_repo_from_module is not None:
            roots.append(main_repo_from_module)
    except Exception:
        pass

    try:
        basic_pitch_spec = importlib.util.find_spec("basic_pitch")
        if basic_pitch_spec and basic_pitch_spec.submodule_search_locations:
            package_dir = Path(next(iter(basic_pitch_spec.submodule_search_locations))).resolve()
            roots.append(package_dir.parent)
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


# Edwards et al. (arxiv 2402.01424) re-train of Kong et al. with pitch-shift +
# reverb augmentation. Same Regress_onset_offset_frame_velocity_CRNN
# architecture, so piano_transcription_inference loads it unchanged. Lifts MAPS
# (out-of-distribution) F1 from 82.4 -> 88.4 with no MAESTRO regression.
PIANO_PTI_ROBUST_CHECKPOINT_FILENAME = "high_resolution_MAESTRO_augmentations.pth"
PIANO_PTI_ROBUST_CHECKPOINT_URL = (
    "https://zenodo.org/records/10610212/files/high_resolution_MAESTRO_augmentations.pth"
)


def _piano_pti_model_subdir() -> Path:
    return Path("piano_pti")


def _expanded_model_roots(root: Path) -> list[Path]:
    """Expand a search root with the same conventional subdirectories the MT3
    code uses, so checkpoints are found under ``<root>``, ``<root>/assets/models``,
    or ``<root>/data/assets/models``.
    """
    return [
        root,
        root / MT3_MODELPACK_DIRNAME,
        root / "data" / MT3_MODELPACK_DIRNAME,
        root / "AuralPrimerPortable" / "data" / MT3_MODELPACK_DIRNAME,
    ]


def resolve_piano_pti_checkpoint_path(search_roots: Iterable[Path | str]) -> Path | None:
    """Find a bundled piano_pti checkpoint in the local model search roots.

    Prefers the Edwards et al. robust checkpoint; falls back to any other .pth
    inside a piano_pti/ directory if the named file isn't present.
    """
    subdir = _piano_pti_model_subdir()
    primary = subdir / PIANO_PTI_ROBUST_CHECKPOINT_FILENAME

    fallback_match: Path | None = None
    for root in search_roots:
        if root is None:
            continue
        for expanded in _expanded_model_roots(Path(root)):
            candidate = expanded / primary
            if candidate.is_file():
                return candidate
            if fallback_match is None:
                piano_dir = expanded / subdir
                if piano_dir.is_dir():
                    for pth in sorted(piano_dir.glob("*.pth")):
                        fallback_match = pth
                        break
    return fallback_match


def _piano_d3rm_model_subdir() -> Path:
    return Path("piano_d3rm")


def resolve_piano_d3rm_checkpoint_path(search_roots: Iterable[Path | str]) -> Path | None:
    """Find a bundled D3RM checkpoint (``.ckpt``) under ``<root>/piano_d3rm/``."""
    subdir = _piano_d3rm_model_subdir()
    for root in search_roots:
        if root is None:
            continue
        for expanded in _expanded_model_roots(Path(root)):
            piano_dir = expanded / subdir
            if not piano_dir.is_dir():
                continue
            for ckpt in sorted(piano_dir.glob("*.ckpt")):
                # Prefer the headline D3RM weight file when multiple ckpts coexist
                # (the NAR-HC baseline is also a .ckpt).
                if ckpt.name.lower().startswith("d3rm"):
                    return ckpt
            for ckpt in sorted(piano_dir.glob("*.ckpt")):
                return ckpt
    return None


def build_default_melodic_algorithm_registry(
    model_search_roots: Iterable[Path | str] | None = None,
    instrument: str = "melodic",
) -> dict[str, MelodicTranscriber]:
    # Import lazily to keep module import lightweight and avoid unnecessary startup costs.
    from aural_ingest.algorithms import (
        melodic_adaptive,
        melodic_basic_pitch,
        melodic_combined_guitar,
        melodic_pyin,
        melodic_pyin_bass_strict,
        melodic_combined,
        melodic_hpss_combined,
        melodic_octave_fix,
        melodic_template_multipass,
        melodic_torchcrepe,
        melodic_yin_bass80,
        melodic_yin_octave_hps_fix,
        piano_cleanup,
        piano_d3rm,
        piano_chord_supplement,
        piano_denoise,
        piano_hft,
        piano_polyphonic,
        piano_pti,
        piano_pti_clean_dedup,
        piano_pti_clean_dedup_pyin,
        piano_transkun,
    )

    roots = list(model_search_roots) if model_search_roots is not None else _default_basic_pitch_model_roots()
    basic_pitch_model_path = resolve_basic_pitch_model_path(roots)

    _inst = instrument  # capture for closures

    def _basic_pitch(stem_path: Path) -> list[MelodicNote]:
        return melodic_basic_pitch.transcribe(stem_path, model_path=basic_pitch_model_path, instrument=_inst)

    def _piano_basic_pitch(stem_path: Path) -> list[MelodicNote]:
        return melodic_basic_pitch.transcribe(
            stem_path,
            model_path=basic_pitch_model_path,
            instrument=_inst,
            allow_fallback=False,
        )

    def _piano_basic_pitch_playable(stem_path: Path) -> list[MelodicNote]:
        def _transcribe_profile(profile: _KeysBasicPitchProfile) -> list[MelodicNote]:
            return melodic_basic_pitch.transcribe(
                stem_path,
                model_path=basic_pitch_model_path,
                instrument=_inst,
                allow_fallback=False,
                onset_threshold=profile.onset_threshold,
                frame_threshold=profile.frame_threshold,
                minimum_note_length_ms=profile.minimum_note_length_ms,
            )

        notes = _transcribe_profile(KEYS_BASIC_PITCH_BALANCED_PROFILE)
        if _inst != "keys":
            return notes

        recall_notes: list[MelodicNote] | None = None
        recall_failed = False

        def _default_recall_notes() -> list[MelodicNote] | None:
            nonlocal recall_failed, recall_notes
            if recall_notes is not None:
                return recall_notes
            if recall_failed:
                return None
            try:
                recall_notes = melodic_basic_pitch.transcribe(
                    stem_path,
                    model_path=basic_pitch_model_path,
                    instrument=_inst,
                    allow_fallback=False,
                )
            except Exception:
                recall_failed = True
                return None
            return recall_notes

        def _finish_playable(primary_notes: list[MelodicNote]) -> list[MelodicNote]:
            playable = apply_role_playability_cleanup(primary_notes, _inst)
            if (
                _keys_note_density_per_minute(playable)
                <= KEYS_PLAYABLE_RECALL_RESTORE_MIN_DENSITY_PER_MIN
            ):
                return _apply_keys_basic_pitch_release_sustain(playable)
            recall = _default_recall_notes()
            if recall is None:
                return _apply_keys_basic_pitch_release_sustain(playable)
            return _apply_keys_basic_pitch_release_sustain(
                _apply_keys_recall_restore(playable, recall)
            )

        def _finalize_selected(selected_notes: list[MelodicNote]) -> list[MelodicNote]:
            spectral_notes = _apply_keys_sparse_low_spectral_chord_candidate(
                selected_notes,
                stem_path,
            )
            spectral_notes = _restore_keys_dense_low_spectral_treble(
                spectral_notes,
                stem_path,
            )
            aligned_notes = _apply_keys_audio_onset_alignment(
                spectral_notes,
                stem_path,
            )
            return _apply_keys_full_song_velocity_calibration(
                _apply_keys_long_sparse_duration_cull(
                    _apply_keys_sparse_low_flood_clamp(
                        _apply_keys_sparse_synth_transient_cull(aligned_notes)
                    )
                )
            )

        balanced = _finish_playable(notes)
        if _keys_should_try_sparse_low_mid_profile(balanced):
            selected_profile = KEYS_BASIC_PITCH_MID_PROFILE
        else:
            selected_profile = _choose_keys_basic_pitch_playable_profile(
                _keys_playable_profile_features(balanced)
            )
        if selected_profile == KEYS_BASIC_PITCH_BALANCED_PROFILE:
            selected = balanced
        else:
            try:
                alternate_notes = _transcribe_profile(selected_profile)
            except Exception:
                selected = balanced
            else:
                selected = _finish_playable(alternate_notes)

        if selected_profile == KEYS_BASIC_PITCH_AGGRESSIVE_PROFILE:
            return _finalize_selected(selected)

        default_notes = _default_recall_notes()
        if default_notes is None:
            return _finalize_selected(selected)
        default_playable = _apply_keys_basic_pitch_release_sustain(
            apply_role_playability_cleanup(default_notes, _inst)
        )
        selected = _choose_keys_playable_default_candidate(
            selected,
            default_playable,
            stem_path=stem_path,
        )
        selected_features = _keys_playable_profile_features(selected)
        if (
            selected_features.density_per_minute
            <= KEYS_PLAYABLE_LOOSE_CANDIDATE_SELECTED_MAX_DENSITY_PER_MIN
            and selected_features.mean_duration_sec
            <= KEYS_PLAYABLE_LOOSE_CANDIDATE_SELECTED_MAX_MEAN_DURATION_SEC
            and selected_features.min_pitch is not None
            and selected_features.min_pitch
            < KEYS_PLAYABLE_LOOSE_CANDIDATE_SELECTED_LOW_ARTIFACT_PITCH
        ):
            try:
                loose_notes = _finish_playable(_transcribe_profile(KEYS_BASIC_PITCH_LOOSE_PROFILE))
            except Exception:
                return _finalize_selected(selected)
            return _finalize_selected(
                _choose_keys_playable_loose_candidate(
                    selected,
                    loose_notes,
                    stem_path=stem_path,
                )
            )
        return _finalize_selected(selected)

    def _piano_basic_pitch_clean(stem_path: Path) -> list[MelodicNote]:
        notes = _piano_basic_pitch(stem_path)
        return piano_cleanup.cleanup_notes(notes, stem_path=stem_path, instrument=_inst)

    def _torchcrepe(stem_path: Path) -> list[MelodicNote]:
        return melodic_torchcrepe.transcribe(stem_path, instrument=_inst)

    def _pyin(stem_path: Path) -> list[MelodicNote]:
        return melodic_pyin.transcribe(stem_path, instrument=_inst)

    def _combined(stem_path: Path) -> list[MelodicNote]:
        return melodic_combined.transcribe(stem_path, instrument=_inst)

    def _combined_guitar(stem_path: Path) -> list[MelodicNote]:
        return melodic_combined_guitar.transcribe(stem_path, instrument=_inst)

    def _pyin_bass_strict(stem_path: Path) -> list[MelodicNote]:
        return melodic_pyin_bass_strict.transcribe(stem_path, instrument=_inst)

    def _octave_fix(stem_path: Path) -> list[MelodicNote]:
        return melodic_octave_fix.transcribe(stem_path, instrument=_inst)

    def _yin_octave_hps_fix(stem_path: Path) -> list[MelodicNote]:
        return melodic_yin_octave_hps_fix.transcribe(stem_path, instrument=_inst)

    def _adaptive(stem_path: Path) -> list[MelodicNote]:
        return melodic_adaptive.transcribe(stem_path, instrument=_inst)

    def _yin_bass80(stem_path: Path) -> list[MelodicNote]:
        return melodic_yin_bass80.transcribe(stem_path, instrument=_inst)

    def _hpss_combined(stem_path: Path) -> list[MelodicNote]:
        return melodic_hpss_combined.transcribe(stem_path, instrument=_inst)

    def _template_multipass(stem_path: Path) -> list[MelodicNote]:
        return melodic_template_multipass.transcribe(stem_path, instrument=_inst)

    def _piano_polyphonic(stem_path: Path) -> list[MelodicNote]:
        return piano_polyphonic.transcribe(stem_path, instrument=_inst)

    def _piano_polyphonic_clean(stem_path: Path) -> list[MelodicNote]:
        notes = piano_polyphonic.transcribe(stem_path, instrument=_inst)
        return piano_cleanup.cleanup_notes(notes, stem_path=stem_path, instrument=_inst)

    def _piano_auto(stem_path: Path) -> list[MelodicNote]:
        # Learned models lead: prefer real model-backed per-note transcribers
        # (bundled Basic Pitch first, then Edwards robust-Kong via piano_pti,
        # hFT, Transkun, D3RM) and fall back to the heuristic polyphonic
        # estimator only when every learned model is unavailable or returns
        # nothing. Reversed from the original ordering, which let the
        # always-firing heuristic shadow the learned models.
        #
        # Scored gate: instead of returning the first non-empty producer,
        # score each candidate's plausibility and accept the first that clears
        # MIN_TRANSCRIPTION_SCORE; otherwise fall back to the best-scoring
        # non-empty candidate. Per-producer failures + scores are recorded on
        # ``_piano_auto.last_run`` so transcribe_melodic can surface them.
        warnings: list[str] = []
        attempted: list[str] = []
        scores: dict[str, float] = {}
        best: tuple[float, list[MelodicNote]] | None = None
        chosen: list[MelodicNote] = []
        chosen_score: float | None = None
        for producer in (
            # Basic Pitch is packaged with the sidecar today and dominates the
            # current Psalm 5 keyboard evidence. Prefer the playable wrapper
            # for gameplay so dense low-confidence chord fragments are pruned
            # before falling back to raw/cleanup A/B variants.
            _piano_basic_pitch_playable,
            _piano_basic_pitch,
            _piano_basic_pitch_clean,
            # Consensus filter first: same model as piano_pti_clean but
            # gated by a second pass on the full mix, which kills the
            # stem-bleed hallucinations that pti+cleanup alone can't
            # always catch on Demucs-separated keys stems.
            _piano_pti_consensus_clean,
            _piano_pti_clean,
            _piano_hft_clean,
            _piano_transkun_clean,
            _piano_d3rm_clean,
            _piano_pti,
            _piano_hft,
            _piano_transkun,
            _piano_d3rm,
            _piano_polyphonic_clean,
            _piano_polyphonic,
            _hpss_combined,
            _adaptive,
            _octave_fix,
            _combined,
            _pyin,
        ):
            name = getattr(producer, "__name__", "producer").lstrip("_")
            attempted.append(name)
            try:
                notes = producer(stem_path)
            except Exception as exc:
                warnings.append(f"piano_auto producer '{name}' failed: {exc!r}")
                continue
            if not notes:
                continue
            score = score_transcription(notes, stem_path)
            scores[name] = round(score, 4)
            if best is None or score > best[0]:
                best = (score, notes)
            if score >= MIN_TRANSCRIPTION_SCORE:
                chosen, chosen_score = notes, score
                break
        if not chosen and best is not None:
            chosen, chosen_score = best[1], best[0]
        _piano_auto.last_run = {
            "warnings": warnings,
            "attempted": attempted,
            "scores": scores,
            "used_score": chosen_score,
        }
        return chosen

    def _piano_transkun(stem_path: Path) -> list[MelodicNote]:
        return piano_transkun.transcribe(stem_path, instrument=_inst)

    def _piano_transkun_clean(stem_path: Path) -> list[MelodicNote]:
        with piano_denoise.maybe_denoised_stem(stem_path) as in_path:
            notes = piano_transkun.transcribe(in_path, instrument=_inst)
        return piano_cleanup.cleanup_notes(notes, stem_path=stem_path, instrument=_inst)

    def _piano_pti(stem_path: Path) -> list[MelodicNote]:
        return piano_pti.transcribe(stem_path, instrument=_inst)

    def _piano_pti_clean(stem_path: Path) -> list[MelodicNote]:
        with piano_denoise.maybe_denoised_stem(stem_path) as in_path:
            notes = piano_pti.transcribe(in_path, instrument=_inst)
        return piano_cleanup.cleanup_notes(notes, stem_path=stem_path, instrument=_inst)

    def _piano_pti_clean_dedup(stem_path: Path) -> list[MelodicNote]:
        return piano_pti_clean_dedup.transcribe(stem_path, instrument=_inst)

    def _piano_pti_clean_dedup_pyin(stem_path: Path) -> list[MelodicNote]:
        return piano_pti_clean_dedup_pyin.transcribe(stem_path, instrument=_inst)

    def _piano_chord_supplement(stem_path: Path) -> list[MelodicNote]:
        return piano_chord_supplement.transcribe(stem_path, instrument=_inst)

    def _piano_pti_consensus(stem_path: Path) -> list[MelodicNote]:
        # Two PTI passes (stem + full mix) intersected by onset+pitch.
        # Mix discovery walks up from <pack>/audio/stems/keys.wav to
        # <pack>/audio/mix.{wav,mp3,ogg}; if no mix is present the wrapper
        # silently falls back to the stem-only result.
        return piano_pti.transcribe_consensus(stem_path, instrument=_inst)

    def _piano_pti_consensus_clean(stem_path: Path) -> list[MelodicNote]:
        # Resolve the mix from the ORIGINAL stem location before denoising:
        # maybe_denoised_stem may hand back a temp path whose parent isn't
        # the auralsong's audio/stems/ dir, which would defeat mix discovery
        # and silently collapse consensus back to a stem-only pass.
        mix_path = piano_pti._find_mix_audio(stem_path)
        with piano_denoise.maybe_denoised_stem(stem_path) as in_path:
            notes = piano_pti.transcribe_consensus(in_path, instrument=_inst, mix_path=mix_path)
        return piano_cleanup.cleanup_notes(notes, stem_path=stem_path, instrument=_inst)

    def _piano_hft(stem_path: Path) -> list[MelodicNote]:
        return piano_hft.transcribe(stem_path, instrument=_inst)

    def _piano_hft_clean(stem_path: Path) -> list[MelodicNote]:
        with piano_denoise.maybe_denoised_stem(stem_path) as in_path:
            notes = piano_hft.transcribe(in_path, instrument=_inst)
        return piano_cleanup.cleanup_notes(notes, stem_path=stem_path, instrument=_inst)

    def _piano_d3rm(stem_path: Path) -> list[MelodicNote]:
        return piano_d3rm.transcribe(stem_path, instrument=_inst)

    def _piano_d3rm_clean(stem_path: Path) -> list[MelodicNote]:
        with piano_denoise.maybe_denoised_stem(stem_path) as in_path:
            notes = piano_d3rm.transcribe(in_path, instrument=_inst)
        return piano_cleanup.cleanup_notes(notes, stem_path=stem_path, instrument=_inst)

    return {
        "piano_auto": _piano_auto,
        "piano_basic_pitch_playable": _piano_basic_pitch_playable,
        "piano_basic_pitch": _piano_basic_pitch,
        "piano_basic_pitch_clean": _piano_basic_pitch_clean,
        "piano_polyphonic": _piano_polyphonic,
        "piano_polyphonic_clean": _piano_polyphonic_clean,
        "piano_transkun": _piano_transkun,
        "piano_transkun_clean": _piano_transkun_clean,
        "piano_pti": _piano_pti,
        "piano_pti_clean": _piano_pti_clean,
        "piano_pti_clean_dedup": _piano_pti_clean_dedup,
        "piano_pti_clean_dedup_pyin": _piano_pti_clean_dedup_pyin,
        "piano_chord_supplement": _piano_chord_supplement,
        "piano_pti_consensus": _piano_pti_consensus,
        "piano_pti_consensus_clean": _piano_pti_consensus_clean,
        "piano_hft": _piano_hft,
        "piano_hft_clean": _piano_hft_clean,
        "piano_d3rm": _piano_d3rm,
        "piano_d3rm_clean": _piano_d3rm_clean,
        "basic_pitch": _basic_pitch,
        "pyin": _pyin,
        "melodic_combined": _combined,
        "melodic_combined_guitar": _combined_guitar,
        "melodic_octave_fix": _octave_fix,
        "melodic_yin_octave_hps_fix": _yin_octave_hps_fix,
        "melodic_adaptive": _adaptive,
        "melodic_yin_bass80": _yin_bass80,
        "melodic_pyin_bass_strict": _pyin_bass_strict,
        "melodic_hpss_combined": _hpss_combined,
        "melodic_template_multipass": _template_multipass,
        "torchcrepe": _torchcrepe,
    }


def resolve_drum_engine(requested_engine: str | None) -> tuple[str, list[str]]:
    if requested_engine is None:
        return DEFAULT_DRUM_ENGINE, []

    rf = requested_engine.strip().lower()
    if not rf or rf == "auto":
        return DEFAULT_DRUM_ENGINE, []
    if rf in KNOWN_DRUM_ENGINES:
        return rf, []

    return DEFAULT_DRUM_ENGINE, [
        f"unknown drum engine '{requested_engine}', falling back to {DEFAULT_DRUM_ENGINE}"
    ]


def resolve_drum_filter(requested_filter: str | None) -> tuple[str, list[str]]:
    return resolve_drum_engine(requested_filter)


def validate_melodic_method(method: str | None) -> str | None:
    if method is None:
        return DEFAULT_MELODIC_METHOD
    m = method.strip().lower()
    if not m:
        return DEFAULT_MELODIC_METHOD
    if m in KNOWN_MELODIC_METHODS:
        return m
    return None


def validate_transcription_profile(profile: str | None) -> str | None:
    if profile is None:
        return DEFAULT_TRANSCRIPTION_PROFILE
    normalized = str(profile).strip().lower()
    if not normalized:
        return DEFAULT_TRANSCRIPTION_PROFILE
    if normalized in KNOWN_TRANSCRIPTION_PROFILES:
        return normalized
    return None


def transcription_profile_metadata(profile: str | None = None) -> dict[str, Any]:
    normalized = validate_transcription_profile(profile)
    if normalized is None:
        normalized = DEFAULT_TRANSCRIPTION_PROFILE
    payload = dict(TRANSCRIPTION_PROFILES[normalized])
    payload["profile"] = normalized
    payload["known_profiles"] = list(KNOWN_TRANSCRIPTION_PROFILES)
    return _json_safe_value(payload)


def melodic_methods_for_profile(profile: str | None, instrument: str) -> list[str]:
    normalized = validate_transcription_profile(profile)
    if normalized is None:
        normalized = DEFAULT_TRANSCRIPTION_PROFILE
    by_instrument = TRANSCRIPTION_PROFILES[normalized]["melodic_methods_by_instrument"]
    methods = list(by_instrument.get(instrument, by_instrument.get("lead_guitar", [])))
    if not methods:
        methods = melodic_fallback_chain("auto", instrument=instrument)
    return _dedupe_preserve_order([m for m in methods if m in KNOWN_MELODIC_METHODS])


def drum_engines_for_profile(profile: str | None) -> list[str]:
    normalized = validate_transcription_profile(profile)
    if normalized is None:
        normalized = DEFAULT_TRANSCRIPTION_PROFILE
    engines = list(TRANSCRIPTION_PROFILES[normalized]["drum_engines"])
    return _dedupe_preserve_order([e for e in engines if e in KNOWN_DRUM_ENGINES])


def drum_fallback_chain(requested_filter: str | None) -> list[str]:
    normalized, _warnings = resolve_drum_engine(requested_filter)

    if normalized in KNOWN_MT3_DRUM_ENGINES:
        return [normalized]

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
            chain = [
                # torchcrepe (neural monophonic pitch tracker) leads: on real
                # bass it matches YIN+HPS octave-cleanliness (~0.3% octave-jump
                # vs basic-pitch's 45%) with a tighter, lower register and runs
                # ~9x faster. YIN stays next as the score-gated fallback.
                "torchcrepe",
                "melodic_yin_octave_hps_fix",
                "melodic_adaptive",
                "melodic_yin_bass80",
                "melodic_octave_fix",
                "melodic_combined",
                "basic_pitch",
                "pyin",
                "melodic_pyin_bass_strict",
            ]
        elif instrument == "keys":
            chain = [
                "piano_auto",
                "piano_basic_pitch_playable",
                "piano_basic_pitch",
                "piano_basic_pitch_clean",
                "piano_polyphonic_clean",
                "melodic_octave_fix",
                "melodic_hpss_combined",
                "melodic_combined",
                "basic_pitch",
                "pyin",
            ]
        elif instrument == "lead_guitar":
            chain = [
                # Lead guitar is predominantly monophonic (single-note lines),
                # so torchcrepe (neural monophonic pitch tracker) leads here as
                # it does for bass: on real lead stems it is octave-clean
                # (~1.6-4% exactly-octave jumps vs basic-pitch's ~15-23%) and
                # runs ~6-8x faster than the YIN/HPSS DSP engines. The polyphonic
                # DSP chain stays as the score-gated fallback.
                "torchcrepe",
                "melodic_adaptive",
                "melodic_octave_fix",
                "melodic_combined",
                "basic_pitch",
                "pyin",
            ]
        elif instrument == "rhythm_guitar":
            chain = [
                # Rhythm guitar is polyphonic (chords), so a monophonic tracker
                # like torchcrepe is the wrong default — it scores well on the
                # octave-cleanliness heuristic but collapses chords to a single
                # voice (~3.5x fewer notes). Keep the polyphonic HPSS+onset
                # engine first; torchcrepe sits late as a last resort only.
                "melodic_hpss_combined",
                "melodic_adaptive",
                "melodic_octave_fix",
                "melodic_combined",
                "basic_pitch",
                "pyin",
            ]
        elif instrument == "guitar":
            chain = [
                # One guitar stem (chords + lead together) is polyphonic, so use
                # the same HPSS+onset-first chain as rhythm guitar; the
                # monophonic torchcrepe would collapse chords.
                "melodic_hpss_combined",
                "melodic_adaptive",
                "melodic_octave_fix",
                "melodic_combined",
                "basic_pitch",
                "pyin",
            ]
        else:
            chain = [
                "melodic_adaptive",
                "melodic_octave_fix",
                "melodic_combined",
                "basic_pitch",
                "pyin",
            ]
    elif normalized == "piano_auto":
        chain = [
            "piano_auto",
            "piano_basic_pitch_playable",
            "piano_basic_pitch",
            "piano_basic_pitch_clean",
            "piano_polyphonic_clean",
            "melodic_hpss_combined",
            "melodic_octave_fix",
            "melodic_combined",
            "basic_pitch",
            "pyin",
        ]
    elif normalized in {"piano_basic_pitch_playable", "piano_basic_pitch", "piano_basic_pitch_clean"}:
        alternates = [
            "piano_basic_pitch_playable",
            "piano_basic_pitch",
            "piano_basic_pitch_clean",
        ]
        chain = [
            normalized,
            *[method for method in alternates if method != normalized],
            "piano_polyphonic_clean",
            "melodic_hpss_combined",
            "melodic_octave_fix",
            "melodic_combined",
            "pyin",
        ]
    elif normalized in {"piano_polyphonic", "piano_polyphonic_clean"}:
        chain = [
            normalized,
            "piano_auto",
            "piano_basic_pitch_playable",
            "piano_basic_pitch",
            "piano_basic_pitch_clean",
            "melodic_hpss_combined",
            "melodic_octave_fix",
            "melodic_combined",
            "basic_pitch",
            "pyin",
        ]
    elif normalized in {"piano_transkun", "piano_transkun_clean"}:
        chain = [
            normalized,
            "piano_auto",
            "piano_basic_pitch_playable",
            "piano_basic_pitch",
            "piano_basic_pitch_clean",
            "melodic_hpss_combined",
            "melodic_octave_fix",
            "melodic_combined",
            "basic_pitch",
            "pyin",
        ]
    elif normalized in {"piano_pti", "piano_pti_clean"}:
        chain = [
            normalized,
            "piano_auto",
            "piano_basic_pitch_playable",
            "piano_basic_pitch",
            "piano_basic_pitch_clean",
            "melodic_hpss_combined",
            "melodic_octave_fix",
            "melodic_combined",
            "basic_pitch",
            "pyin",
        ]
    elif normalized in {"piano_hft", "piano_hft_clean"}:
        chain = [
            normalized,
            "piano_auto",
            "piano_basic_pitch_playable",
            "piano_basic_pitch",
            "piano_basic_pitch_clean",
            "melodic_hpss_combined",
            "melodic_octave_fix",
            "melodic_combined",
            "basic_pitch",
            "pyin",
        ]
    elif normalized in {"piano_d3rm", "piano_d3rm_clean"}:
        chain = [
            normalized,
            "piano_pti_clean",
            "piano_auto",
            "piano_basic_pitch_playable",
            "piano_basic_pitch",
            "piano_basic_pitch_clean",
            "melodic_hpss_combined",
            "melodic_octave_fix",
            "melodic_combined",
            "basic_pitch",
            "pyin",
        ]
    elif normalized == "basic_pitch":
        chain = ["basic_pitch", "pyin"]
    elif normalized == "torchcrepe":
        chain = [
            "torchcrepe",
            "melodic_yin_octave_hps_fix",
            "melodic_adaptive",
            "melodic_octave_fix",
            "pyin",
        ]
    else:
        chain = [
            normalized,
            "melodic_adaptive",
            "melodic_octave_fix",
            "melodic_yin_octave_hps_fix",
            "melodic_hpss_combined",
            "melodic_combined",
            "basic_pitch",
            "pyin",
        ]

    out: list[str] = []
    for x in chain:
        if x not in out:
            out.append(x)
    return out


def remap_drum_events_to_taxonomy(
    events: list[DrumEvent],
    taxonomy: str,
) -> list[DrumEvent]:
    """Remap drum events from the internal 9-class vocabulary onto the
    selected output taxonomy. Currently supports `5class` (the standard
    ADTOF / production benchmark vocabulary) and `9class` (legacy
    internal). Unknown taxonomies are passed through as-is.

    Implements path 1 of `docs/research-deep-dive-adt-2026-05-07.md`. The
    default production taxonomy stays `9class` until path 2 (model
    upgrade) lands; users who want the standardized output today can opt
    in via `--drum-taxonomy=5class` or the equivalent profile setting.
    """

    if not events:
        return events

    if taxonomy == "5class":
        # Lazy import to avoid circular dependency.
        from aural_ingest.algorithms._common import map_midi_drum_to_5class_midi

        remapped: list[DrumEvent] = []
        for event in events:
            new_note = map_midi_drum_to_5class_midi(event.note)
            if new_note is None:
                # Class not in the 9-class mapping; pass through unchanged.
                remapped.append(event)
                continue
            if new_note == event.note:
                remapped.append(event)
            else:
                remapped.append(
                    DrumEvent(
                        time=event.time,
                        note=new_note,
                        velocity=event.velocity,
                        duration=event.duration,
                    )
                )
        return remapped

    # `9class` and unknowns pass through.
    return events


KNOWN_DRUM_TAXONOMIES: tuple[str, ...] = ("9class", "5class")


# Drum stem-silence gate (defensive post-filter applied after the drum
# engine emits events). Some engines — especially `combined_filter` and
# its DSP siblings — emit hits in regions where the separated drum stem
# is essentially silent (separator residual or detector hallucination on
# background hum). Rather than retune every engine, we run a final RMS
# gate against the stem itself: if the local audio at an event's time is
# below the gate, the event is dropped.
#
# The default (-50 dBFS) is intentionally conservative. Music with real
# drum hits typically sits at -30 to -10 dBFS locally; anything below
# -50 dBFS is inaudible at normal listening levels and far below any
# plausible separator artifact level. Override with the env var
# AURALPRIMER_DRUM_SILENCE_GATE_DBFS (number, in dBFS) or the CLI flag
# `--drum-silence-gate-dbfs`. Disable entirely with `--no-drum-silence-gate`
# or AURALPRIMER_DRUM_SILENCE_GATE_DISABLED=1.
DEFAULT_DRUM_SILENCE_GATE_DBFS: float = -50.0
DEFAULT_DRUM_SILENCE_GATE_WINDOW_MS: float = 30.0
DRUM_SILENCE_GATE_ENV_DBFS: str = "AURALPRIMER_DRUM_SILENCE_GATE_DBFS"
DRUM_SILENCE_GATE_ENV_WINDOW_MS: str = "AURALPRIMER_DRUM_SILENCE_GATE_WINDOW_MS"
DRUM_SILENCE_GATE_ENV_DISABLED: str = "AURALPRIMER_DRUM_SILENCE_GATE_DISABLED"


def _local_rms_at(samples: list[float], sr: int, time_sec: float, half_window_samples: int) -> float:
    if not samples or sr <= 0 or half_window_samples <= 0:
        return 0.0
    center = int(round(float(time_sec) * sr))
    lo = max(0, center - half_window_samples)
    hi = min(len(samples), center + half_window_samples)
    if hi <= lo:
        return 0.0
    seg = samples[lo:hi]
    n = float(len(seg))
    if n <= 0:
        return 0.0
    acc = 0.0
    for x in seg:
        acc += x * x
    return math.sqrt(acc / n)


def _rms_to_dbfs(rms: float) -> float:
    if rms <= 0.0:
        return -math.inf
    return 20.0 * math.log10(rms)


def _resolve_drum_silence_gate_settings(
    gate_dbfs: float | None,
    window_ms: float | None,
    disabled: bool | None,
) -> tuple[float, float, bool]:
    """Resolve effective gate settings, with env vars overriding defaults
    and explicit kwargs overriding env. Returns (gate_dbfs, window_ms,
    disabled). The disabled flag short-circuits to (default, default, True)
    so callers can record the resolved metadata uniformly."""

    if disabled is None:
        env_disabled = os.getenv(DRUM_SILENCE_GATE_ENV_DISABLED, "").strip().lower()
        disabled = env_disabled in {"1", "true", "yes", "on"}

    if gate_dbfs is None:
        env_gate = os.getenv(DRUM_SILENCE_GATE_ENV_DBFS, "").strip()
        if env_gate:
            try:
                gate_dbfs = float(env_gate)
            except ValueError:
                gate_dbfs = DEFAULT_DRUM_SILENCE_GATE_DBFS
        else:
            gate_dbfs = DEFAULT_DRUM_SILENCE_GATE_DBFS

    if window_ms is None:
        env_window = os.getenv(DRUM_SILENCE_GATE_ENV_WINDOW_MS, "").strip()
        if env_window:
            try:
                window_ms = float(env_window)
            except ValueError:
                window_ms = DEFAULT_DRUM_SILENCE_GATE_WINDOW_MS
        else:
            window_ms = DEFAULT_DRUM_SILENCE_GATE_WINDOW_MS

    if window_ms <= 0.0:
        window_ms = DEFAULT_DRUM_SILENCE_GATE_WINDOW_MS

    return float(gate_dbfs), float(window_ms), bool(disabled)


def validate_drum_events_against_stem_silence(
    events: list[DrumEvent],
    stem_path: Path,
    *,
    gate_dbfs: float | None = None,
    window_ms: float | None = None,
    disabled: bool | None = None,
    logger: Callable[[str], None] | None = None,
) -> tuple[list[DrumEvent], dict[str, Any]]:
    """Drop drum events whose neighborhood in the separated drum stem is
    below the silence gate.

    Why this exists: heuristic drum engines (combined_filter and its DSP
    siblings) emit candidate hits during stretches where the separated
    drum stem is effectively silent (separator residual + transient-detector
    confabulation). The result is a chart cluttered with false-positive
    hits during verses/breakdowns. This function is a defensive post-filter
    that compares each event time to the local stem energy and drops events
    in obvious silence. It is conservative by default (-50 dBFS, which is
    well below any plausible musical drum hit) so it cannot remove real
    hits on normally-mixed audio.

    Args:
        events: ordered drum events to validate.
        stem_path: path to the separated drum stem WAV.
        gate_dbfs: silence gate in dBFS. Events whose local RMS is strictly
            below this value are dropped. Defaults to -50 dBFS, or the env
            var AURALPRIMER_DRUM_SILENCE_GATE_DBFS if set.
        window_ms: half-window radius (so the window is +/- window_ms)
            around each event time used to compute local RMS. Defaults to
            30 ms, or AURALPRIMER_DRUM_SILENCE_GATE_WINDOW_MS.
        disabled: if True, skip the gate entirely (returns events unchanged
            with metadata recording skip). When None, honors
            AURALPRIMER_DRUM_SILENCE_GATE_DISABLED.
        logger: optional log callback for human-readable warnings.

    Returns:
        Tuple (kept_events, metadata). The metadata dict always carries
        the resolved settings (`gate_dbfs`, `window_ms`, `disabled`) plus
        accounting (`events_in`, `events_out`, `dropped`, `quietest_dropped_dbfs`,
        `stem_path_used`, `stem_load_ok`) so callers can serialize it
        into the auralsong manifest for traceability.
    """

    # Lazy import to keep transcription.py importable without WAV helpers.
    from aural_ingest.algorithms._common import read_wav_mono_normalized

    resolved_gate, resolved_window_ms, resolved_disabled = _resolve_drum_silence_gate_settings(
        gate_dbfs, window_ms, disabled
    )

    base_meta: dict[str, Any] = {
        "gate_dbfs": resolved_gate,
        "window_ms": resolved_window_ms,
        "disabled": resolved_disabled,
        "events_in": len(events),
        "events_out": len(events),
        "dropped": 0,
        "stem_path_used": str(stem_path) if stem_path is not None else None,
        "stem_load_ok": False,
        "quietest_dropped_dbfs": None,
    }

    if resolved_disabled or not events:
        return list(events), base_meta

    if stem_path is None or not Path(stem_path).is_file():
        # Fail-open: no stem available to gate against, do not drop anything.
        msg = f"drum silence gate skipped: stem not available ({stem_path})"
        if logger:
            logger(msg)
        base_meta["skip_reason"] = "stem_unavailable"
        return list(events), base_meta

    samples, sr = read_wav_mono_normalized(Path(stem_path))
    if not samples or sr <= 0:
        msg = f"drum silence gate skipped: could not load stem ({stem_path})"
        if logger:
            logger(msg)
        base_meta["skip_reason"] = "stem_load_failed"
        return list(events), base_meta

    base_meta["stem_load_ok"] = True

    half_window_samples = max(1, int(round((resolved_window_ms / 1000.0) * sr)))
    kept: list[DrumEvent] = []
    quietest_dropped: float | None = None
    for ev in events:
        rms = _local_rms_at(samples, sr, float(ev.time), half_window_samples)
        local_db = _rms_to_dbfs(rms)
        if local_db < resolved_gate:
            if quietest_dropped is None or local_db < quietest_dropped:
                quietest_dropped = local_db
            continue
        kept.append(ev)

    dropped = len(events) - len(kept)
    base_meta["events_out"] = len(kept)
    base_meta["dropped"] = dropped
    base_meta["quietest_dropped_dbfs"] = (
        None if quietest_dropped is None or quietest_dropped == -math.inf else round(quietest_dropped, 2)
    )

    if dropped and logger:
        logger(
            f"drum silence gate: dropped {dropped}/{len(events)} events below "
            f"{resolved_gate:.1f} dBFS (window +/- {resolved_window_ms:.0f}ms)"
        )

    return kept, base_meta


def validate_drum_taxonomy(taxonomy: str | None) -> str:
    """Normalize and validate a drum taxonomy choice. Returns `9class` for
    None or unknown values (preserving backward compatibility with imports
    that predate the option)."""

    if taxonomy is None:
        return "9class"
    normalized = str(taxonomy).strip().lower()
    if normalized in KNOWN_DRUM_TAXONOMIES:
        return normalized
    return "9class"


def transcribe_drums_dsp(
    stem_path: Path,
    requested_filter: str | None,
    algorithm_registry: dict[str, DrumTranscriber],
    logger: Callable[[str], None] | None = None,
    *,
    taxonomy: str | None = None,
) -> DrumTranscriptionResult:
    normalized, warnings = resolve_drum_engine(requested_filter)
    attempted: list[str] = []
    scores: dict[str, float] = {}
    best: tuple[float, str, list[DrumEvent]] | None = None
    taxonomy_resolved = validate_drum_taxonomy(taxonomy)

    def _build(algorithm_id: str, events: list[DrumEvent], score: float | None) -> DrumTranscriptionResult:
        mapped_events = remap_drum_events_to_taxonomy(events, taxonomy_resolved)
        return DrumTranscriptionResult(
            events=mapped_events,
            used_algorithm=algorithm_id,
            attempted_algorithms=attempted,
            warnings=warnings,
            meta={
                "backend": "heuristic",
                "taxonomy": taxonomy_resolved,
                "used_score": round(score, 4) if score is not None else None,
                "attempt_scores": scores,
            },
        )

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
            msg = f"drum algorithm '{algorithm_id}' failed: {e!r}"
            warnings.append(msg)
            if logger:
                logger(msg)
            continue

        if not events:
            continue

        score = score_transcription(events, stem_path)
        scores[algorithm_id] = round(score, 4)
        if best is None or score > best[0]:
            best = (score, algorithm_id, events)
        if score >= MIN_TRANSCRIPTION_SCORE:
            return _build(algorithm_id, events, score)

    if best is not None:
        return _build(best[1], best[2], best[0])

    return DrumTranscriptionResult(
        events=[],
        used_algorithm=None,
        attempted_algorithms=attempted,
        warnings=warnings,
        meta={
            "backend": "heuristic",
            "taxonomy": taxonomy_resolved,
            "used_score": None,
            "attempt_scores": scores,
        },
    )


def transcribe_drums(
    stem_path: Path,
    requested_engine: str | None,
    algorithm_registry: dict[str, DrumTranscriber],
    logger: Callable[[str], None] | None = None,
    *,
    taxonomy: str | None = None,
) -> DrumTranscriptionResult:
    normalized, warnings = resolve_drum_engine(requested_engine)

    if normalized in KNOWN_MT3_DRUM_ENGINES:
        attempted = [normalized]
        try:
            events, meta = _transcribe_drums_mt3_events(stem_path, normalized)
        except Exception as exc:
            msg = f"drum engine '{normalized}' failed: {exc}"
            warnings.append(msg)
            if logger:
                logger(msg)
            taxonomy_resolved = validate_drum_taxonomy(taxonomy)
            return DrumTranscriptionResult(
                events=[],
                used_algorithm=None,
                attempted_algorithms=attempted,
                warnings=warnings,
                meta={"backend": "mt3", "engine": normalized, "taxonomy": taxonomy_resolved},
            )

        taxonomy_resolved = validate_drum_taxonomy(taxonomy)
        mapped_events = remap_drum_events_to_taxonomy(events, taxonomy_resolved)
        meta_with_taxonomy = dict(meta)
        meta_with_taxonomy["taxonomy"] = taxonomy_resolved
        return DrumTranscriptionResult(
            events=mapped_events,
            used_algorithm=normalized,
            attempted_algorithms=attempted,
            warnings=warnings,
            meta=meta_with_taxonomy,
        )

    return transcribe_drums_dsp(
        stem_path,
        requested_filter=normalized,
        algorithm_registry=algorithm_registry,
        logger=logger,
        taxonomy=taxonomy,
    )


def transcribe_drums_with_profile(
    stem_path: Path,
    profile: str | None,
    algorithm_registry: dict[str, DrumTranscriber],
    logger: Callable[[str], None] | None = None,
    *,
    taxonomy: str | None = None,
) -> DrumTranscriptionResult:
    """Profile-aware drum orchestration. Walks the profile's `drum_engines`
    list in order — MT3 engines fall through to the next entry on
    failure (rather than ending the chain like the per-engine path does)
    and DSP engines use their normal fallback chains.

    Implements path 4 of `docs/research-deep-dive-adt-2026-05-07.md`:
    `fidelity_midi` profile lists MT3 engines first; this function lets
    the orchestration actually try MT3 first and silently fall back to
    DSP when MT3 weights/runtime are absent. Behavior on the
    `gameplay_default` profile is unchanged because its drum_engines
    list begins with `beat_conditioned_multiband_decoder` already.

    The MT3 path remains opt-in via profile selection; this function
    does NOT change which engine is the global default."""

    profile_normalized = validate_transcription_profile(profile)
    engines = drum_engines_for_profile(profile_normalized) or []

    aggregated_warnings: list[str] = []
    aggregated_attempts: list[str] = []
    last_meta: dict[str, Any] = {}

    if not engines:
        return transcribe_drums(
            stem_path,
            requested_engine=None,
            algorithm_registry=algorithm_registry,
            logger=logger,
            taxonomy=taxonomy,
        )

    for engine_id in engines:
        result = transcribe_drums(
            stem_path,
            requested_engine=engine_id,
            algorithm_registry=algorithm_registry,
            logger=logger,
            taxonomy=taxonomy,
        )
        aggregated_warnings.extend(result.warnings)
        for attempt in result.attempted_algorithms:
            if attempt not in aggregated_attempts:
                aggregated_attempts.append(attempt)
        last_meta = dict(result.meta)
        if result.events:
            merged_meta = dict(last_meta)
            merged_meta["profile"] = profile_normalized or "default"
            return DrumTranscriptionResult(
                events=result.events,
                used_algorithm=result.used_algorithm,
                attempted_algorithms=aggregated_attempts,
                warnings=aggregated_warnings,
                meta=merged_meta,
            )

    last_meta["profile"] = profile_normalized or "default"
    if "taxonomy" not in last_meta:
        last_meta["taxonomy"] = validate_drum_taxonomy(taxonomy)
    return DrumTranscriptionResult(
        events=[],
        used_algorithm=None,
        attempted_algorithms=aggregated_attempts,
        warnings=aggregated_warnings,
        meta=last_meta,
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
    scores: dict[str, float] = {}
    best: tuple[float, str, list[MelodicNote]] | None = None
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
            msg = f"melodic method '{method}' failed: {e!r}"
            warnings.append(msg)
            if logger:
                logger(msg)
            continue

        # Surface piano_auto's internal per-producer diagnostics.
        last_run = getattr(fn, "last_run", None)
        if isinstance(last_run, dict):
            warnings.extend(last_run.get("warnings", []))
            for inner_name, inner_score in last_run.get("scores", {}).items():
                scores[f"{method}.{inner_name}"] = inner_score

        if not notes:
            continue

        score = score_transcription(notes, stem_path)
        scores[method] = round(score, 4)
        if best is None or score > best[0]:
            best = (score, method, notes)
        if score >= MIN_TRANSCRIPTION_SCORE:
            return MelodicTranscriptionResult(
                notes=notes,
                used_method=method,
                attempted_methods=attempted,
                warnings=warnings,
                used_score=round(score, 4),
                attempt_scores=scores,
            )

    if best is not None:
        return MelodicTranscriptionResult(
            notes=best[2],
            used_method=best[1],
            attempted_methods=attempted,
            warnings=warnings,
            used_score=round(best[0], 4),
            attempt_scores=scores,
        )

    return MelodicTranscriptionResult(
        notes=[],
        used_method=None,
        attempted_methods=attempted,
        warnings=warnings,
        used_score=None,
        attempt_scores=scores,
    )


def _keys_max_polyphony(notes: Iterable[MelodicNote]) -> int:
    points: list[tuple[float, int]] = []
    for note in notes:
        if float(note.t_off) <= float(note.t_on):
            continue
        points.append((float(note.t_on), 1))
        points.append((float(note.t_off), -1))
    active = 0
    peak = 0
    for _time, delta in sorted(points, key=lambda item: (item[0], item[1])):
        active += delta
        peak = max(peak, active)
    return peak


def _keys_cluster_note_indices_by_onset(
    notes: list[MelodicNote],
    *,
    window_sec: float,
) -> dict[int, int]:
    cluster_for_index: dict[int, int] = {}
    cluster_id = -1
    cluster_start: float | None = None
    for idx, note in sorted(enumerate(notes), key=lambda item: (item[1].t_on, item[1].pitch)):
        onset = float(note.t_on)
        if cluster_start is None or onset - cluster_start > window_sec:
            cluster_id += 1
            cluster_start = onset
        cluster_for_index[idx] = cluster_id
    return cluster_for_index


def _keys_cluster_extreme_pitches(
    notes: list[MelodicNote],
    cluster_for_index: dict[int, int],
) -> dict[int, dict[str, int]]:
    grouped: dict[int, list[MelodicNote]] = {}
    for idx, note in enumerate(notes):
        grouped.setdefault(int(cluster_for_index[idx]), []).append(note)

    out: dict[int, dict[str, int]] = {}
    for cluster_id, cluster_notes in grouped.items():
        pitches = [int(note.pitch) for note in cluster_notes]
        useful_left_pitches = [pitch for pitch in pitches if 35 <= pitch < 60]
        left_pitches = useful_left_pitches or [pitch for pitch in pitches if pitch < 60]
        out[cluster_id] = {
            "highest": max(pitches),
            "lowest_left": min(left_pitches) if left_pitches else min(pitches),
        }
    return out


def _keys_playable_priority(note: MelodicNote, *, cluster_extremes: dict[str, int]) -> float:
    pitch = int(note.pitch)
    duration = max(0.0, float(note.t_off) - float(note.t_on))
    score = 0.0

    if pitch == int(cluster_extremes.get("highest", pitch)):
        score += 10_000.0
    if pitch == int(cluster_extremes.get("lowest_left", pitch)) and pitch < 60:
        score += 8_000.0

    if pitch >= 60:
        score += 3_000.0
    elif pitch >= 48:
        score += 1_900.0
    elif pitch >= 35:
        score += 1_450.0
    else:
        score -= 2_500.0

    if pitch > 96:
        score -= 600.0
    if 52 <= pitch <= 84:
        score += 350.0

    score += min(2.5, duration) * 90.0
    score += max(1, min(127, int(note.velocity))) * 3.0
    return score


def _keys_would_exceed_polyphony(
    selected: list[MelodicNote],
    candidate: MelodicNote,
    *,
    max_polyphony: int,
) -> bool:
    if max_polyphony <= 0:
        return True
    if float(candidate.t_off) <= float(candidate.t_on):
        return False
    return _keys_max_polyphony([*selected, candidate]) > max_polyphony


def _keys_note_density_per_minute(notes: list[MelodicNote]) -> float:
    if not notes:
        return 0.0
    start = min(float(note.t_on) for note in notes)
    end = max(float(note.t_off) for note in notes)
    span = max(1.0, end - start)
    return len(notes) / span * 60.0


def _apply_keys_confidence_density_prune(notes: list[MelodicNote]) -> list[MelodicNote]:
    if len(notes) < KEYS_PLAYABLE_CONFIDENCE_PRUNE_MIN_NOTES:
        return notes
    density = _keys_note_density_per_minute(notes)
    if density <= KEYS_PLAYABLE_CONFIDENCE_PRUNE_MIN_DENSITY_PER_MIN:
        return notes

    pruned = [
        note
        for note in notes
        if int(note.velocity) >= KEYS_PLAYABLE_CONFIDENCE_PRUNE_MIN_VELOCITY
    ]
    min_retained = max(
        1,
        int(math.ceil(len(notes) * KEYS_PLAYABLE_CONFIDENCE_PRUNE_MIN_RETAINED_RATIO)),
    )
    if len(pruned) < min_retained:
        return notes
    return pruned


def _median_velocity(notes: list[MelodicNote]) -> float:
    velocities = sorted(max(1, min(127, int(note.velocity))) for note in notes)
    if not velocities:
        return 0.0
    mid = len(velocities) // 2
    if len(velocities) % 2:
        return float(velocities[mid])
    return (float(velocities[mid - 1]) + float(velocities[mid])) / 2.0


def _apply_keys_playable_velocity_calibration(notes: list[MelodicNote]) -> list[MelodicNote]:
    if len(notes) < KEYS_PLAYABLE_VELOCITY_CALIBRATION_MIN_NOTES:
        return notes
    if _keys_note_density_per_minute(notes) <= KEYS_PLAYABLE_VELOCITY_CALIBRATION_MIN_DENSITY_PER_MIN:
        return notes
    if _median_velocity(notes) < KEYS_PLAYABLE_VELOCITY_CALIBRATION_MIN_MEDIAN_VELOCITY:
        return notes

    out: list[MelodicNote] = []
    for note in notes:
        calibrated = int(
            round(
                max(1, min(127, int(note.velocity)))
                * KEYS_PLAYABLE_VELOCITY_CALIBRATION_SCALE
            )
        )
        out.append(
            replace(
                note,
                velocity=max(
                    KEYS_PLAYABLE_VELOCITY_CALIBRATION_MIN_VELOCITY,
                    min(KEYS_PLAYABLE_VELOCITY_CALIBRATION_MAX_VELOCITY, calibrated),
                ),
            )
        )
    return out


def _apply_keys_full_song_velocity_calibration(notes: list[MelodicNote]) -> list[MelodicNote]:
    if len(notes) < KEYS_PLAYABLE_FULL_SONG_VELOCITY_MIN_NOTES:
        return notes
    start = min(float(note.t_on) for note in notes)
    end = max(float(note.t_off) for note in notes)
    span = end - start
    if span < KEYS_PLAYABLE_FULL_SONG_VELOCITY_MIN_SPAN_SEC:
        return notes
    if _keys_note_density_per_minute(notes) > KEYS_PLAYABLE_FULL_SONG_VELOCITY_MAX_DENSITY_PER_MIN:
        return notes
    if _median_velocity(notes) < KEYS_PLAYABLE_FULL_SONG_VELOCITY_MIN_MEDIAN_VELOCITY:
        return notes
    if min(int(note.velocity) for note in notes) < KEYS_PLAYABLE_FULL_SONG_VELOCITY_MIN_LOWEST_VELOCITY:
        return notes

    out: list[MelodicNote] = []
    for note in notes:
        calibrated = int(
            round(
                max(1, min(127, int(note.velocity)))
                * KEYS_PLAYABLE_FULL_SONG_VELOCITY_SCALE
            )
        )
        out.append(
            replace(
                note,
                velocity=max(
                    KEYS_PLAYABLE_FULL_SONG_VELOCITY_MIN_VELOCITY,
                    min(KEYS_PLAYABLE_FULL_SONG_VELOCITY_MAX_VELOCITY, calibrated),
                ),
            )
        )
    return out


def _apply_keys_sparse_synth_transient_cull(notes: list[MelodicNote]) -> list[MelodicNote]:
    if not (
        KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MIN_NOTES
        <= len(notes)
        <= KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MAX_NOTES
    ):
        return notes

    features = _keys_playable_profile_features(notes)
    if features.min_pitch is None:
        return notes

    start = min(float(note.t_on) for note in notes)
    end = max(float(note.t_off) for note in notes)
    span = max(0.0, end - start)
    if not (
        KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MIN_SPAN_SEC
        <= span
        <= KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MAX_SPAN_SEC
    ):
        return notes

    max_pitch = max(int(note.pitch) for note in notes)
    if not (
        KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MIN_DENSITY_PER_MIN
        <= features.density_per_minute
        <= KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MAX_DENSITY_PER_MIN
        and features.mean_duration_sec
        >= KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MIN_MEAN_DURATION_SEC
        and features.min_pitch < KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_LOW_PITCH_CUTOFF
        and max_pitch >= KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MIN_MAX_PITCH
    ):
        return notes

    retained = [
        note
        for note in notes
        if max(0.0, float(note.t_off) - float(note.t_on))
        >= KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MAX_DURATION_SEC
    ]
    if len(retained) >= len(notes):
        return notes
    min_retained = math.ceil(
        len(notes) * KEYS_PLAYABLE_SPARSE_SYNTH_TRANSIENT_CULL_MIN_RETAINED_RATIO
    )
    if len(retained) < min_retained:
        return notes
    if _keys_max_polyphony(retained) > KEYS_PLAYABLE_MAX_POLYPHONY:
        return notes
    return sorted(retained, key=lambda item: (item.t_on, item.pitch, item.t_off))


def _keys_median_duration(notes: list[MelodicNote]) -> float:
    durations = sorted(max(0.0, float(note.t_off) - float(note.t_on)) for note in notes)
    if not durations:
        return 0.0
    midpoint = len(durations) // 2
    if len(durations) % 2:
        return durations[midpoint]
    return (durations[midpoint - 1] + durations[midpoint]) / 2.0


def _keys_largest_pitch_share(notes: list[MelodicNote]) -> float:
    if not notes:
        return 0.0
    pitch_counts: dict[int, int] = {}
    for note in notes:
        pitch = int(note.pitch)
        pitch_counts[pitch] = pitch_counts.get(pitch, 0) + 1
    return max(pitch_counts.values()) / len(notes)


def _apply_keys_long_sparse_duration_cull(notes: list[MelodicNote]) -> list[MelodicNote]:
    if not (
        KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_MIN_NOTES
        <= len(notes)
        <= KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_MAX_NOTES
    ):
        return notes

    features = _keys_playable_profile_features(notes)
    if features.min_pitch is None:
        return notes

    start = min(float(note.t_on) for note in notes)
    end = max(float(note.t_off) for note in notes)
    span = max(0.0, end - start)
    if span < KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_MIN_SPAN_SEC:
        return notes
    if (
        features.density_per_minute
        > KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_MAX_DENSITY_PER_MIN
    ):
        return notes
    if (
        features.mean_duration_sec
        > KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_MAX_MEAN_DURATION_SEC
    ):
        return notes
    if (
        _keys_median_duration(notes)
        > KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_MAX_MEDIAN_DURATION_SEC
    ):
        return notes
    if features.min_pitch > KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_LOW_PITCH_FLOOR:
        return notes
    if (
        _keys_largest_pitch_share(notes)
        < KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_MIN_REPEATED_PITCH_RATIO
    ):
        return notes

    max_pitch = max(int(note.pitch) for note in notes)
    if max_pitch <= KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_LOW_RANGE_MAX_PITCH:
        min_duration = KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_LOW_RANGE_MIN_DURATION_SEC
    else:
        min_duration = KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_UPPER_RANGE_MIN_DURATION_SEC

    retained = [
        note
        for note in notes
        if max(0.0, float(note.t_off) - float(note.t_on)) >= min_duration
    ]
    if len(retained) >= len(notes):
        return notes
    min_retained = max(
        KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_MIN_RETAINED_NOTES,
        math.ceil(len(notes) * KEYS_PLAYABLE_LONG_SPARSE_DURATION_CULL_MIN_RETAINED_RATIO),
    )
    if len(retained) < min_retained:
        return notes
    if _keys_max_polyphony(retained) > KEYS_PLAYABLE_MAX_POLYPHONY:
        return notes
    return sorted(retained, key=lambda item: (item.t_on, item.pitch, item.t_off))


def _keys_calibrated_confidence_velocity(velocity: int) -> int:
    calibrated = int(
        round(
            max(1, min(127, int(velocity)))
            * KEYS_PLAYABLE_VELOCITY_CALIBRATION_SCALE
        )
    )
    return max(
        KEYS_PLAYABLE_VELOCITY_CALIBRATION_MIN_VELOCITY,
        min(KEYS_PLAYABLE_VELOCITY_CALIBRATION_MAX_VELOCITY, calibrated),
    )


def _keys_has_near_duplicate(
    notes: Iterable[MelodicNote],
    candidate: MelodicNote,
    *,
    tolerance_sec: float,
) -> bool:
    candidate_on = float(candidate.t_on)
    candidate_pitch = int(candidate.pitch)
    return any(
        int(note.pitch) == candidate_pitch
        and abs(float(note.t_on) - candidate_on) <= tolerance_sec
        for note in notes
    )


def _keys_attack_cluster_count(
    notes: Iterable[MelodicNote],
    candidate: MelodicNote,
    *,
    window_sec: float,
) -> int:
    candidate_on = float(candidate.t_on)
    return sum(1 for note in notes if abs(float(note.t_on) - candidate_on) <= window_sec)


def _keys_recall_restore_priority(note: MelodicNote) -> float:
    pitch = int(note.pitch)
    duration = max(0.0, float(note.t_off) - float(note.t_on))
    pitch_bonus = 12.0 if 52 <= pitch <= 84 else 6.0 if 36 <= pitch < 52 else -12.0
    return (
        max(1, min(127, int(note.velocity))) * 3.0
        + min(1.2, duration) * 80.0
        + pitch_bonus
    )


def _apply_keys_recall_restore(
    playable_notes: list[MelodicNote],
    recall_notes: list[MelodicNote],
) -> list[MelodicNote]:
    if not playable_notes or not recall_notes:
        return playable_notes
    if _keys_note_density_per_minute(playable_notes) <= KEYS_PLAYABLE_RECALL_RESTORE_MIN_DENSITY_PER_MIN:
        return playable_notes

    out = list(playable_notes)
    candidates: list[tuple[float, MelodicNote]] = []
    for note in recall_notes:
        t_on = max(0.0, float(note.t_on))
        t_off = max(t_on, float(note.t_off))
        duration = t_off - t_on
        if duration < KEYS_PLAYABLE_RECALL_RESTORE_MIN_DURATION_SEC:
            continue
        if int(note.velocity) < KEYS_PLAYABLE_RECALL_RESTORE_MIN_VELOCITY:
            continue
        priority = _keys_recall_restore_priority(note)
        candidate = MelodicNote(
            t_on=round(t_on, 6),
            t_off=round(t_off, 6),
            pitch=max(21, min(108, int(note.pitch))),
            velocity=_keys_calibrated_confidence_velocity(int(note.velocity)),
            instrument=note.instrument,
        )
        if _keys_has_near_duplicate(
            out,
            candidate,
            tolerance_sec=KEYS_PLAYABLE_RECALL_RESTORE_DUPLICATE_SEC,
        ):
            continue
        candidates.append((priority, candidate))

    added = 0
    for _priority, candidate in sorted(candidates, key=lambda item: item[0], reverse=True):
        if added >= KEYS_PLAYABLE_RECALL_RESTORE_MAX_ADDED_NOTES:
            break
        if _keys_has_near_duplicate(
            out,
            candidate,
            tolerance_sec=KEYS_PLAYABLE_RECALL_RESTORE_DUPLICATE_SEC,
        ):
            continue
        if _keys_attack_cluster_count(
            out,
            candidate,
            window_sec=KEYS_PLAYABLE_ATTACK_CLUSTER_SEC,
        ) >= KEYS_PLAYABLE_MAX_NOTES_PER_ATTACK:
            continue
        if _keys_would_exceed_polyphony(
            out,
            candidate,
            max_polyphony=KEYS_PLAYABLE_MAX_POLYPHONY,
        ):
            continue
        out.append(candidate)
        added += 1

    return sorted(out, key=lambda item: (item.t_on, item.pitch, item.t_off))


def _keys_cluster_starts(notes: list[MelodicNote], *, window_sec: float) -> list[float]:
    starts: list[float] = []
    for note in sorted(notes, key=lambda item: (item.t_on, item.pitch)):
        onset = float(note.t_on)
        if not starts or onset - starts[-1] > window_sec:
            starts.append(onset)
    return starts


def _keys_playable_profile_features(notes: list[MelodicNote]) -> _KeysPlayableProfileFeatures:
    if not notes:
        return _KeysPlayableProfileFeatures(
            note_count=0,
            density_per_minute=0.0,
            mean_attack_cluster_size=0.0,
            min_pitch=None,
            mean_duration_sec=0.0,
        )

    cluster_starts = _keys_cluster_starts(
        notes,
        window_sec=KEYS_PLAYABLE_ATTACK_CLUSTER_SEC,
    )
    cluster_sizes = [
        sum(
            1
            for note in notes
            if abs(float(note.t_on) - start) <= KEYS_PLAYABLE_ATTACK_CLUSTER_SEC
        )
        for start in cluster_starts
    ]
    durations = [max(0.0, float(note.t_off) - float(note.t_on)) for note in notes]
    return _KeysPlayableProfileFeatures(
        note_count=len(notes),
        density_per_minute=_keys_note_density_per_minute(notes),
        mean_attack_cluster_size=sum(cluster_sizes) / max(1, len(cluster_sizes)),
        min_pitch=min(int(note.pitch) for note in notes),
        mean_duration_sec=sum(durations) / max(1, len(durations)),
    )


def _choose_keys_basic_pitch_playable_profile(
    features: _KeysPlayableProfileFeatures,
) -> _KeysBasicPitchProfile:
    if (
        features.density_per_minute >= KEYS_PLAYABLE_PROFILE_VERY_DENSE_MIN_DENSITY_PER_MIN
        or features.note_count >= KEYS_PLAYABLE_PROFILE_VERY_DENSE_MIN_NOTES
    ):
        return KEYS_BASIC_PITCH_MID_PROFILE

    if (
        features.density_per_minute < KEYS_PLAYABLE_PROFILE_UPPER_SPARSE_MAX_DENSITY_PER_MIN
        and features.min_pitch is not None
        and features.min_pitch >= KEYS_PLAYABLE_PROFILE_UPPER_SPARSE_MIN_PITCH
        and features.mean_duration_sec <= KEYS_PLAYABLE_PROFILE_UPPER_SPARSE_MAX_MEAN_DURATION_SEC
    ):
        return KEYS_BASIC_PITCH_AGGRESSIVE_PROFILE

    if (
        features.mean_attack_cluster_size >= KEYS_PLAYABLE_PROFILE_CHORDAL_MIN_CLUSTER_MEAN
        and features.note_count <= KEYS_PLAYABLE_PROFILE_CHORDAL_MAX_NOTES
    ):
        return KEYS_BASIC_PITCH_AGGRESSIVE_PROFILE

    return KEYS_BASIC_PITCH_BALANCED_PROFILE


def _choose_keys_playable_default_candidate(
    profile_notes: list[MelodicNote],
    default_notes: list[MelodicNote],
    *,
    stem_path: Path | None = None,
) -> list[MelodicNote]:
    if not profile_notes or not default_notes:
        return profile_notes

    profile_features = _keys_playable_profile_features(profile_notes)
    default_features = _keys_playable_profile_features(default_notes)
    profile_score = score_transcription(profile_notes, stem_path)
    default_score = score_transcription(default_notes, stem_path)
    default_score_margin = default_score - profile_score

    if (
        profile_features.density_per_minute
        <= KEYS_PLAYABLE_DEFAULT_CANDIDATE_SPARSE_MAX_DENSITY_PER_MIN
        and default_score_margin >= KEYS_PLAYABLE_DEFAULT_CANDIDATE_MIN_SCORE_MARGIN
    ):
        return default_notes

    if (
        profile_features.density_per_minute
        <= KEYS_PLAYABLE_DEFAULT_CANDIDATE_SPARSE_MAX_DENSITY_PER_MIN
        and profile_features.min_pitch is not None
        and profile_features.min_pitch
        <= KEYS_PLAYABLE_DEFAULT_CANDIDATE_FEWER_LOW_ARTIFACT_MAX_MIN_PITCH
        and default_features.min_pitch is not None
        and default_features.min_pitch
        <= KEYS_PLAYABLE_DEFAULT_CANDIDATE_FEWER_LOW_ARTIFACT_MAX_MIN_PITCH
        and default_features.note_count
        <= math.floor(
            profile_features.note_count
            * KEYS_PLAYABLE_DEFAULT_CANDIDATE_FEWER_MAX_NOTE_RATIO
        )
        and default_score
        + KEYS_PLAYABLE_DEFAULT_CANDIDATE_FEWER_SCORE_TOLERANCE
        >= profile_score
    ):
        return default_notes

    if (
        profile_features.density_per_minute
        >= KEYS_PLAYABLE_DEFAULT_CANDIDATE_DENSE_MIN_DENSITY_PER_MIN
        and default_features.note_count >= profile_features.note_count
        and default_features.note_count
        <= math.ceil(
            profile_features.note_count
            * KEYS_PLAYABLE_DEFAULT_CANDIDATE_DENSE_MAX_NOTE_RATIO
        )
        and default_score + KEYS_PLAYABLE_DEFAULT_CANDIDATE_DENSE_SCORE_TOLERANCE
        >= profile_score
    ):
        return default_notes

    return profile_notes


def _choose_keys_playable_loose_candidate(
    selected_notes: list[MelodicNote],
    loose_notes: list[MelodicNote],
    *,
    stem_path: Path | None = None,
) -> list[MelodicNote]:
    if not selected_notes or not loose_notes:
        return selected_notes

    selected_features = _keys_playable_profile_features(selected_notes)
    loose_features = _keys_playable_profile_features(loose_notes)
    if (
        selected_features.density_per_minute
        > KEYS_PLAYABLE_LOOSE_CANDIDATE_SELECTED_MAX_DENSITY_PER_MIN
        or selected_features.mean_duration_sec
        > KEYS_PLAYABLE_LOOSE_CANDIDATE_SELECTED_MAX_MEAN_DURATION_SEC
        or selected_features.min_pitch is None
        or selected_features.min_pitch
        >= KEYS_PLAYABLE_LOOSE_CANDIDATE_SELECTED_LOW_ARTIFACT_PITCH
    ):
        return selected_notes

    if (
        loose_features.note_count < KEYS_PLAYABLE_LOOSE_CANDIDATE_MIN_NOTES
        or loose_features.density_per_minute
        > KEYS_PLAYABLE_LOOSE_CANDIDATE_MAX_DENSITY_PER_MIN
        or loose_features.min_pitch is None
        or loose_features.min_pitch < KEYS_PLAYABLE_LOOSE_CANDIDATE_MIN_PITCH
        or loose_features.note_count
        > math.floor(
            selected_features.note_count
            * KEYS_PLAYABLE_LOOSE_CANDIDATE_MAX_NOTE_RATIO
        )
    ):
        return selected_notes

    selected_score = score_transcription(selected_notes, stem_path)
    loose_score = score_transcription(loose_notes, stem_path)
    if (
        loose_score - selected_score
        >= KEYS_PLAYABLE_LOOSE_CANDIDATE_MIN_SCORE_MARGIN
    ):
        return loose_notes
    return selected_notes


def _apply_keys_sparse_low_flood_clamp(notes: list[MelodicNote]) -> list[MelodicNote]:
    if len(notes) < KEYS_PLAYABLE_SPARSE_LOW_FLOOD_MIN_NOTES:
        return notes

    features = _keys_playable_profile_features(notes)
    if (
        features.density_per_minute > KEYS_PLAYABLE_SPARSE_LOW_FLOOD_MAX_DENSITY_PER_MIN
        or features.min_pitch is None
        or features.min_pitch >= KEYS_PLAYABLE_SPARSE_LOW_FLOOD_LOW_PITCH_CUTOFF
    ):
        return notes

    start = min(float(note.t_on) for note in notes)
    end = max(float(note.t_off) for note in notes)
    if end - start < KEYS_PLAYABLE_SPARSE_LOW_FLOOD_MIN_SPAN_SEC:
        return notes

    low_notes = sum(
        1
        for note in notes
        if int(note.pitch) < KEYS_PLAYABLE_SPARSE_LOW_FLOOD_LOW_PITCH_CUTOFF
    )
    if low_notes / max(1, len(notes)) < KEYS_PLAYABLE_SPARSE_LOW_FLOOD_MIN_LOW_RATIO:
        return notes

    clamped = [
        note
        for note in notes
        if KEYS_PLAYABLE_SPARSE_LOW_FLOOD_LOW_PITCH_CUTOFF
        <= int(note.pitch)
        <= KEYS_PLAYABLE_SPARSE_LOW_FLOOD_HIGH_PITCH_CUTOFF
    ]
    if len(clamped) < math.ceil(len(notes) * KEYS_PLAYABLE_SPARSE_LOW_FLOOD_MIN_RETAINED_RATIO):
        return notes
    if _keys_max_polyphony(clamped) > KEYS_PLAYABLE_MAX_POLYPHONY:
        return notes
    return clamped


def _keys_next_same_pitch_onsets(notes: list[MelodicNote]) -> dict[int, float]:
    by_pitch: dict[int, list[MelodicNote]] = {}
    for note in sorted(notes, key=lambda item: (item.t_on, item.pitch, item.t_off)):
        by_pitch.setdefault(int(note.pitch), []).append(note)

    out: dict[int, float] = {}
    for pitch_notes in by_pitch.values():
        for idx, note in enumerate(pitch_notes[:-1]):
            out[id(note)] = float(pitch_notes[idx + 1].t_on)
    return out


def _keys_sustain_target_off(
    note: MelodicNote,
    *,
    cluster_starts: list[float],
    next_same_pitch_onsets: dict[int, float],
) -> float:
    t_on = float(note.t_on)
    current_off = float(note.t_off)
    duration = max(0.0, current_off - t_on)
    target = t_on + max(duration, KEYS_PLAYABLE_SUSTAIN_MIN_DURATION_SEC)

    next_cluster = next(
        (
            start
            for start in cluster_starts
            if start > t_on + KEYS_PLAYABLE_ATTACK_CLUSTER_SEC
        ),
        None,
    )
    if next_cluster is not None:
        target = max(target, next_cluster + KEYS_PLAYABLE_SUSTAIN_NEXT_CLUSTER_TAIL_SEC)

    target = min(target, t_on + KEYS_PLAYABLE_SUSTAIN_MAX_DURATION_SEC)
    next_same = next_same_pitch_onsets.get(id(note))
    if next_same is not None:
        target = min(target, next_same - KEYS_PLAYABLE_SUSTAIN_SAME_PITCH_GAP_SEC)
    return round(max(current_off, target), 6)


def _apply_keys_playable_sustain(notes: list[MelodicNote]) -> list[MelodicNote]:
    if len(notes) < KEYS_PLAYABLE_SUSTAIN_MIN_NOTES:
        return notes
    if _keys_note_density_per_minute(notes) <= KEYS_PLAYABLE_SUSTAIN_MIN_DENSITY_PER_MIN:
        return notes

    ordered = sorted(notes, key=lambda item: (item.t_on, item.pitch, item.t_off))
    cluster_starts = _keys_cluster_starts(
        ordered,
        window_sec=KEYS_PLAYABLE_ATTACK_CLUSTER_SEC,
    )
    next_same_pitch_onsets = _keys_next_same_pitch_onsets(ordered)
    targets = {
        id(note): _keys_sustain_target_off(
            note,
            cluster_starts=cluster_starts,
            next_same_pitch_onsets=next_same_pitch_onsets,
        )
        for note in ordered
    }

    out = list(ordered)
    index_by_id = {id(note): idx for idx, note in enumerate(out)}
    for note in ordered:
        target_off = targets[id(note)]
        if target_off <= float(note.t_off) + 1e-6:
            continue
        idx = index_by_id[id(note)]
        candidate = list(out)
        candidate[idx] = replace(note, t_off=target_off)
        if _keys_max_polyphony(candidate) <= KEYS_PLAYABLE_MAX_POLYPHONY:
            out = candidate

    return sorted(out, key=lambda item: (item.t_on, item.pitch, item.t_off))


def _keys_release_sustain_target_off(
    note: MelodicNote,
    *,
    cluster_starts: list[float],
    next_same_pitch_onsets: dict[int, float],
    min_duration_sec: float = KEYS_PLAYABLE_RELEASE_SUSTAIN_MIN_DURATION_SEC,
    max_duration_sec: float = KEYS_PLAYABLE_RELEASE_SUSTAIN_MAX_DURATION_SEC,
    min_pitch: int = KEYS_PLAYABLE_RELEASE_SUSTAIN_MIN_PITCH,
) -> float:
    if int(note.pitch) < min_pitch:
        return float(note.t_off)

    t_on = float(note.t_on)
    current_off = float(note.t_off)
    duration = max(0.0, current_off - t_on)
    target = t_on + max(duration, min_duration_sec)

    next_cluster = next(
        (
            start
            for start in cluster_starts
            if start > t_on + KEYS_PLAYABLE_ATTACK_CLUSTER_SEC
        ),
        None,
    )
    if next_cluster is not None:
        target = max(target, next_cluster + KEYS_PLAYABLE_RELEASE_SUSTAIN_NEXT_CLUSTER_TAIL_SEC)

    target = min(target, t_on + max_duration_sec)
    next_same = next_same_pitch_onsets.get(id(note))
    if next_same is not None:
        target = min(target, next_same - KEYS_PLAYABLE_RELEASE_SUSTAIN_SAME_PITCH_GAP_SEC)
    return round(max(current_off, target), 6)


def _apply_keys_release_sustain_bounds(
    notes: list[MelodicNote],
    *,
    min_duration_sec: float,
    max_duration_sec: float,
    min_pitch: int,
    prefer_short_notes: bool = False,
) -> list[MelodicNote]:
    ordered = sorted(notes, key=lambda item: (item.t_on, item.pitch, item.t_off))
    cluster_starts = _keys_cluster_starts(
        ordered,
        window_sec=KEYS_PLAYABLE_ATTACK_CLUSTER_SEC,
    )
    next_same_pitch_onsets = _keys_next_same_pitch_onsets(ordered)
    targets = {
        id(note): _keys_release_sustain_target_off(
            note,
            cluster_starts=cluster_starts,
            next_same_pitch_onsets=next_same_pitch_onsets,
            min_duration_sec=min_duration_sec,
            max_duration_sec=max_duration_sec,
            min_pitch=min_pitch,
        )
        for note in ordered
    }

    out = list(ordered)
    index_by_id = {id(note): idx for idx, note in enumerate(out)}
    if prefer_short_notes:
        sustain_order = sorted(
            ordered,
            key=lambda item: (
                float(item.t_off) - float(item.t_on),
                -int(item.velocity),
                int(item.pitch),
            ),
        )
    else:
        sustain_order = sorted(
            ordered,
            key=lambda item: (
                float(item.t_off) - float(item.t_on),
                int(item.velocity),
                int(item.pitch),
            ),
            reverse=True,
        )

    for note in sustain_order:
        target_off = targets[id(note)]
        if target_off <= float(note.t_off) + 1e-6:
            continue
        idx = index_by_id[id(note)]
        candidate = list(out)
        candidate[idx] = replace(note, t_off=target_off)
        if _keys_max_polyphony(candidate) <= KEYS_PLAYABLE_MAX_POLYPHONY:
            out = candidate

    return sorted(out, key=lambda item: (item.t_on, item.pitch, item.t_off))


def _keys_should_apply_sparse_release_sustain(notes: list[MelodicNote]) -> bool:
    features = _keys_playable_profile_features(notes)
    return (
        KEYS_PLAYABLE_SPARSE_RELEASE_SUSTAIN_MIN_NOTES
        <= features.note_count
        <= KEYS_PLAYABLE_SPARSE_RELEASE_SUSTAIN_MAX_NOTES
        and features.density_per_minute
        >= KEYS_PLAYABLE_SPARSE_RELEASE_SUSTAIN_MIN_DENSITY_PER_MIN
        and features.density_per_minute
        <= KEYS_PLAYABLE_SPARSE_RELEASE_SUSTAIN_MAX_DENSITY_PER_MIN
        and features.mean_attack_cluster_size
        >= KEYS_PLAYABLE_SPARSE_RELEASE_SUSTAIN_MIN_CLUSTER_MEAN
    )


def _keys_should_apply_low_dense_release_sustain(notes: list[MelodicNote]) -> bool:
    features = _keys_playable_profile_features(notes)
    return (
        features.note_count >= KEYS_PLAYABLE_LOW_DENSE_RELEASE_SUSTAIN_MIN_NOTES
        and features.density_per_minute
        >= KEYS_PLAYABLE_LOW_DENSE_RELEASE_SUSTAIN_MIN_DENSITY_PER_MIN
        and features.min_pitch is not None
        and features.min_pitch
        < KEYS_PLAYABLE_LOW_DENSE_RELEASE_SUSTAIN_LOW_REGISTER_MAX_PITCH
        and features.mean_duration_sec
        <= KEYS_PLAYABLE_LOW_DENSE_RELEASE_SUSTAIN_MAX_MEAN_DURATION_SEC
    )


def _keys_should_trim_sparse_low_tails(notes: list[MelodicNote]) -> bool:
    features = _keys_playable_profile_features(notes)
    return (
        KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_MIN_NOTES
        <= features.note_count
        <= KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_MAX_NOTES
        and features.density_per_minute
        >= KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_MIN_DENSITY_PER_MIN
        and features.density_per_minute
        <= KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_MAX_DENSITY_PER_MIN
        and features.min_pitch is not None
        and features.min_pitch < KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_LOW_REGISTER_MAX_PITCH
        and features.mean_duration_sec
        >= KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_MIN_MEAN_DURATION_SEC
    )


def _keys_should_trim_dense_high_staccato_tails(notes: list[MelodicNote]) -> bool:
    features = _keys_playable_profile_features(notes)
    return (
        features.note_count >= KEYS_PLAYABLE_DENSE_HIGH_STACCATO_TRIM_MIN_NOTES
        and features.density_per_minute
        >= KEYS_PLAYABLE_DENSE_HIGH_STACCATO_TRIM_MIN_DENSITY_PER_MIN
        and features.min_pitch is not None
        and features.min_pitch >= KEYS_PLAYABLE_DENSE_HIGH_STACCATO_TRIM_MIN_PITCH_FLOOR
        and features.mean_duration_sec
        >= KEYS_PLAYABLE_DENSE_HIGH_STACCATO_TRIM_MIN_MEAN_DURATION_SEC
        and features.mean_duration_sec
        <= KEYS_PLAYABLE_DENSE_HIGH_STACCATO_TRIM_MAX_MEAN_DURATION_SEC
    )


def _trim_keys_dense_high_staccato_tails(notes: list[MelodicNote]) -> list[MelodicNote]:
    if not _keys_should_trim_dense_high_staccato_tails(notes):
        return notes

    out: list[MelodicNote] = []
    for note in notes:
        duration = max(0.0, float(note.t_off) - float(note.t_on))
        if (
            int(note.pitch) >= KEYS_PLAYABLE_DENSE_HIGH_STACCATO_TRIM_MIN_PITCH
            and duration > KEYS_PLAYABLE_DENSE_HIGH_STACCATO_TRIM_MAX_DURATION_SEC
        ):
            out.append(
                replace(
                    note,
                    t_off=round(
                        float(note.t_on)
                        + KEYS_PLAYABLE_DENSE_HIGH_STACCATO_TRIM_MAX_DURATION_SEC,
                        6,
                    ),
                )
            )
        else:
            out.append(note)
    return sorted(out, key=lambda item: (item.t_on, item.pitch, item.t_off))


def _trim_keys_sparse_low_tails(notes: list[MelodicNote]) -> list[MelodicNote]:
    if not _keys_should_trim_sparse_low_tails(notes):
        return notes

    out: list[MelodicNote] = []
    for note in notes:
        duration = float(note.t_off) - float(note.t_on)
        calibrated_velocity = max(
            KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_MIN_VELOCITY,
            min(
                KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_MAX_VELOCITY,
                int(
                    round(
                        max(1, min(127, int(note.velocity)))
                        * KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_VELOCITY_SCALE
                    )
                ),
            ),
        )
        if (
            int(note.pitch) >= KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_MIN_PITCH
            and duration > KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_MAX_DURATION_SEC
        ):
            out.append(
                replace(
                    note,
                    velocity=calibrated_velocity,
                    t_off=round(
                        float(note.t_on)
                        + KEYS_PLAYABLE_SPARSE_LOW_TAIL_TRIM_MAX_DURATION_SEC,
                        6,
                    ),
                )
            )
        else:
            out.append(replace(note, velocity=calibrated_velocity))
    return sorted(out, key=lambda item: (item.t_on, item.pitch, item.t_off))


def _keys_should_restore_sparse_low_treble(notes: list[MelodicNote]) -> bool:
    features = _keys_playable_profile_features(notes)
    if not (
        KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MIN_NOTES
        <= features.note_count
        <= KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MAX_NOTES
        and features.density_per_minute
        >= KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MIN_DENSITY_PER_MIN
        and features.density_per_minute
        <= KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MAX_DENSITY_PER_MIN
        and features.min_pitch is not None
        and features.min_pitch
        < KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_LOW_REGISTER_MAX_PITCH
        and features.mean_duration_sec
        >= KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MIN_MEAN_DURATION_SEC
    ):
        return False

    pitches = [int(note.pitch) for note in notes]
    low_notes = sum(
        1
        for pitch in pitches
        if pitch <= KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_LOW_NOTE_MAX_PITCH
    )
    high_notes = sum(
        1
        for pitch in pitches
        if pitch >= KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_HIGH_NOTE_MIN_PITCH
    )
    return (
        low_notes >= KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MIN_LOW_NOTES
        and high_notes <= KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MAX_HIGH_NOTES
        and max(pitches, default=0) >= KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MIN_MAX_PITCH
    )


def _keys_should_try_sparse_low_mid_profile(notes: list[MelodicNote]) -> bool:
    features = _keys_playable_profile_features(notes)
    if not (
        KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_MIN_NOTES
        <= features.note_count
        <= KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_MAX_NOTES
        and features.density_per_minute
        >= KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_MIN_DENSITY_PER_MIN
        and features.density_per_minute
        <= KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_MAX_DENSITY_PER_MIN
        and features.min_pitch is not None
        and features.min_pitch < KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_LOW_REGISTER_MAX_PITCH
        and features.mean_duration_sec
        >= KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_MIN_MEAN_DURATION_SEC
    ):
        return False

    pitches = [int(note.pitch) for note in notes]
    low_notes = sum(
        1 for pitch in pitches if pitch <= KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_LOW_NOTE_MAX_PITCH
    )
    high_notes = sum(
        1 for pitch in pitches if pitch >= KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_HIGH_NOTE_MIN_PITCH
    )
    return (
        low_notes >= KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_MIN_LOW_NOTES
        and high_notes <= KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_MAX_HIGH_NOTES
        and max(pitches, default=0) >= KEYS_PLAYABLE_SPARSE_LOW_MID_PROFILE_MIN_MAX_PITCH
    )


def _keys_should_try_sparse_low_spectral_candidate(notes: list[MelodicNote]) -> bool:
    features = _keys_playable_profile_features(notes)
    if not (
        KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MIN_NOTES
        <= features.note_count
        <= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MAX_NOTES
        and features.density_per_minute
        >= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MIN_DENSITY_PER_MIN
        and features.density_per_minute
        <= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MAX_DENSITY_PER_MIN
        and features.min_pitch is not None
        and features.min_pitch < KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LOW_REGISTER_MAX_PITCH
        and features.mean_duration_sec
        >= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MIN_MEAN_DURATION_SEC
    ):
        return False

    pitches = [int(note.pitch) for note in notes]
    low_notes = sum(
        1 for pitch in pitches if pitch <= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LOW_NOTE_MAX_PITCH
    )
    high_notes = sum(
        1 for pitch in pitches if pitch >= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_HIGH_NOTE_MIN_PITCH
    )
    return (
        low_notes >= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MIN_LOW_NOTES
        and high_notes <= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MAX_HIGH_NOTES
        and max(pitches, default=0) >= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MIN_MAX_PITCH
    )


def _keys_should_restore_dense_low_spectral_treble(notes: list[MelodicNote]) -> bool:
    features = _keys_playable_profile_features(notes)
    if not (
        KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MIN_NOTES
        <= features.note_count
        <= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_NOTES
        and features.density_per_minute
        >= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MIN_DENSITY_PER_MIN
        and features.density_per_minute
        <= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_DENSITY_PER_MIN
        and features.mean_attack_cluster_size
        >= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MIN_CLUSTER_MEAN
        and features.min_pitch is not None
        and features.min_pitch < KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_LOW_REGISTER_MAX_PITCH
        and features.mean_duration_sec
        >= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MIN_MEAN_DURATION_SEC
        and features.mean_duration_sec
        <= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_MEAN_DURATION_SEC
    ):
        return False

    pitches = [int(note.pitch) for note in notes]
    low_notes = sum(
        1
        for pitch in pitches
        if pitch <= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_LOW_NOTE_MAX_PITCH
    )
    upper_notes = sum(
        1
        for pitch in pitches
        if pitch >= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_UPPER_NOTE_MIN_PITCH
    )
    high_notes = sum(
        1
        for pitch in pitches
        if pitch >= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_HIGH_NOTE_MIN_PITCH
    )
    return (
        low_notes >= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MIN_LOW_NOTES
        and low_notes <= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_LOW_NOTES
        and upper_notes >= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MIN_UPPER_NOTES
        and upper_notes <= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_UPPER_NOTES
        and high_notes <= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_HIGH_NOTES
        and max(pitches, default=0) <= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_EXISTING_PITCH
    )


def _keys_sparse_low_spectral_bass_score(notes: list[MelodicNote]) -> float:
    return max(
        (
            float(KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_ONSET_LOW_PITCH - int(note.pitch))
            + max(0.0, float(note.t_off) - float(note.t_on))
            for note in notes
            if int(note.pitch) <= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_ONSET_LOW_PITCH
        ),
        default=-99.0,
    )


def _keys_sparse_low_spectral_starts(notes: list[MelodicNote]) -> list[tuple[float, bool]]:
    ordered = sorted(notes, key=lambda item: (item.t_on, item.pitch, item.t_off))
    starts = _keys_cluster_starts(ordered, window_sec=KEYS_PLAYABLE_ATTACK_CLUSTER_SEC)
    low_groups: list[dict[str, Any]] = []
    pickups: list[dict[str, Any]] = []

    for start in starts:
        onset = [
            note
            for note in ordered
            if abs(float(note.t_on) - start) <= KEYS_PLAYABLE_ATTACK_CLUSTER_SEC
        ]
        if not onset:
            continue
        active = [
            note
            for note in ordered
            if float(note.t_on) <= start + KEYS_PLAYABLE_ATTACK_CLUSTER_SEC
            and float(note.t_off) > start
        ]
        low_onsets = [
            note
            for note in onset
            if int(note.pitch) <= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_ONSET_LOW_PITCH
        ]
        long_low_onset = any(
            max(0.0, float(note.t_off) - float(note.t_on))
            >= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LONG_LOW_DURATION_SEC
            for note in low_onsets
        )
        active_long_low = any(
            int(note.pitch) <= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_ACTIVE_LOW_PITCH
            and max(0.0, float(note.t_off) - float(note.t_on))
            >= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LONG_LOW_DURATION_SEC
            for note in active
        )
        initial = start <= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_INITIAL_SEC
        pickup = (
            start <= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_PICKUP_MAX_SEC
            and any(int(note.pitch) >= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_PICKUP_MIN_PITCH for note in onset)
            and len(onset) <= 2
        )
        if pickup and not initial:
            pickups.append(
                {
                    "start": start,
                    "pickup": True,
                    "strong": False,
                }
            )
            continue
        if initial or (low_onsets and (long_low_onset or active_long_low)):
            low_groups.append(
                {
                    "start": start,
                    "pickup": False,
                    "strong": bool(initial or long_low_onset),
                    "bass_score": _keys_sparse_low_spectral_bass_score(onset),
                }
            )

    merged: list[dict[str, Any]] = []
    for group in sorted(low_groups, key=lambda item: float(item["start"])):
        if (
            not merged
            or float(group["start"]) - float(merged[-1]["start"])
            > KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_CLUSTER_MERGE_SEC
        ):
            merged.append(dict(group))
            continue
        merged[-1]["strong"] = bool(merged[-1].get("strong")) or bool(group.get("strong"))
        if float(group.get("bass_score", -99.0)) > float(merged[-1].get("bass_score", -99.0)) + 0.5:
            merged[-1]["start"] = float(group["start"])
            merged[-1]["bass_score"] = float(group.get("bass_score", -99.0))

    strong_starts = [
        float(group["start"])
        for group in merged
        if bool(group.get("strong"))
    ]
    filtered_low_groups: list[dict[str, Any]] = []
    for group in merged:
        start = float(group["start"])
        if not bool(group.get("strong")) and any(
            abs(strong - start) <= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_NEAR_STRONG_SEC
            and strong > start + KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_FOLLOWING_STRONG_MIN_SEC
            for strong in strong_starts
        ):
            continue
        if not bool(group.get("strong")) and any(
            abs(strong - start) <= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_NEAR_STRONG_SEC
            and strong < start
            for strong in strong_starts
        ):
            continue
        filtered_low_groups.append(group)

    entries = [
        (float(group["start"]), bool(group.get("pickup", False)))
        for group in filtered_low_groups
    ]
    entries.extend(
        (float(group["start"]), True)
        for group in pickups
        if not any(
            abs(float(group["start"]) - start) <= KEYS_PLAYABLE_ATTACK_CLUSTER_SEC
            for start, _pickup in entries
        )
    )
    return sorted(entries, key=lambda item: item[0])


def _keys_sparse_low_spectral_required_ratio(pitch: int) -> float:
    if pitch < 55:
        return KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LOW_RATIO
    if pitch >= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_HIGH_NOTE_MIN_PITCH:
        return KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_HIGH_RATIO
    return KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MID_RATIO


def _keys_sparse_low_spectral_pitches(
    notes: list[MelodicNote],
    start: float,
    ratios: Mapping[int, float],
) -> list[int]:
    onset = [
        note
        for note in notes
        if abs(float(note.t_on) - start) <= KEYS_PLAYABLE_ATTACK_CLUSTER_SEC
    ]
    selected: set[int] = set()
    for note in onset:
        pitch = int(note.pitch)
        required = (
            KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LOW_RATIO
            if pitch < 55
            else KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_EXISTING_RATIO
        )
        if float(ratios.get(pitch, 0.0)) >= required:
            selected.add(pitch)

    candidates = [
        (float(ratio), int(pitch))
        for pitch, ratio in ratios.items()
        if KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MIN_PITCH
        <= int(pitch)
        <= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MAX_PITCH
        and float(ratio) >= _keys_sparse_low_spectral_required_ratio(int(pitch))
    ]
    onset_pitches = {int(note.pitch) for note in onset}
    for ratio, pitch in sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True):
        if len(selected) >= KEYS_PLAYABLE_MAX_NOTES_PER_ATTACK:
            break
        if pitch < 50 and sum(1 for selected_pitch in selected if selected_pitch < 50) >= 2:
            continue
        if pitch not in onset_pitches and any(
            abs(pitch - selected_pitch) == 1
            and float(ratios.get(selected_pitch, 0.0))
            >= ratio * KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_ADJACENT_RATIO
            for selected_pitch in selected
        ):
            continue
        selected.add(pitch)
    return sorted(selected)


def _build_keys_sparse_low_spectral_candidate(
    notes: list[MelodicNote],
    energy_ratios: Callable[[float, Iterable[int]], Mapping[int, float]],
) -> list[MelodicNote]:
    if not _keys_should_try_sparse_low_spectral_candidate(notes):
        return []

    ordered = sorted(notes, key=lambda item: (item.t_on, item.pitch, item.t_off))
    starts = _keys_sparse_low_spectral_starts(ordered)
    if not starts:
        return []

    out: list[MelodicNote] = []
    end_limit = max((float(note.t_off) for note in ordered), default=0.0)
    pitch_range = range(
        KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MIN_PITCH,
        KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MAX_PITCH + 1,
    )
    for idx, (start, pickup) in enumerate(starts):
        next_start = starts[idx + 1][0] if idx + 1 < len(starts) else None
        ratios = energy_ratios(start, pitch_range)
        if pickup:
            onset = [
                note
                for note in ordered
                if abs(float(note.t_on) - start) <= KEYS_PLAYABLE_ATTACK_CLUSTER_SEC
                and int(note.pitch) >= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_PICKUP_MIN_PITCH
                and float(ratios.get(int(note.pitch), 0.0))
                >= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_EXISTING_RATIO
            ]
            pitches = sorted({int(note.pitch) for note in onset})
        else:
            pitches = _keys_sparse_low_spectral_pitches(ordered, start, ratios)
        if not pitches:
            continue

        if next_start is None:
            available = KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MAX_DURATION_SEC
        else:
            gap = (
                KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_PICKUP_GAP_SEC
                if pickup
                else KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_CHORD_GAP_SEC
            )
            available = max(0.0, next_start - start - gap)

        if pickup:
            duration = max(
                KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_PICKUP_MIN_DURATION_SEC,
                min(KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_PICKUP_MAX_DURATION_SEC, available),
            )
        else:
            duration = max(
                KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MIN_DURATION_SEC,
                min(KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MAX_DURATION_SEC, available),
            )
        t_off = round(min(end_limit, start + duration), 6)
        if t_off <= start:
            continue
        instrument = next((note.instrument for note in ordered), "keys")
        out.extend(
            MelodicNote(
                t_on=round(start, 6),
                t_off=t_off,
                pitch=pitch,
                velocity=KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_VELOCITY,
                instrument=instrument,
            )
            for pitch in pitches[:KEYS_PLAYABLE_MAX_NOTES_PER_ATTACK]
        )

    out = sorted(out, key=lambda item: (item.t_on, item.pitch, item.t_off))
    if not out or _keys_max_polyphony(out) > KEYS_PLAYABLE_MAX_POLYPHONY:
        return []
    return out


def _shift_keys_note_times(notes: list[MelodicNote], offset_sec: float) -> list[MelodicNote]:
    if abs(float(offset_sec)) <= 1e-9:
        return list(notes)
    return [
        replace(
            note,
            t_on=round(float(note.t_on) + float(offset_sec), 6),
            t_off=round(float(note.t_off) + float(offset_sec), 6),
        )
        for note in notes
    ]


def _keys_midi_frequency_hz(pitch: int) -> float:
    return 440.0 * (2.0 ** ((int(pitch) - 69) / 12.0))


def _keys_sparse_low_spectral_energy_probe(
    stem_path: Path,
) -> Callable[[float, Iterable[int]], Mapping[int, float]]:
    import numpy as np

    from aural_ingest.algorithms._common import read_wav_mono_normalized

    audio_raw, sample_rate = read_wav_mono_normalized(stem_path)
    audio = np.asarray(audio_raw, dtype=np.float32)
    cache: dict[float, dict[int, float]] = {}

    def _probe(center: float, pitches: Iterable[int]) -> Mapping[int, float]:
        pitch_list = [int(pitch) for pitch in pitches]
        cache_key = round(float(center), 6)
        cached = cache.get(cache_key)
        if cached is not None:
            return {pitch: cached.get(pitch, 0.0) for pitch in pitch_list}

        if sample_rate <= 0 or audio.size <= 0:
            return {pitch: 0.0 for pitch in pitch_list}
        start = max(
            0,
            int(
                round(
                    (float(center) - KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_FFT_PRE_SEC)
                    * sample_rate
                )
            ),
        )
        frame_count = max(
            1,
            int(round(KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_FFT_WINDOW_SEC * sample_rate)),
        )
        segment = audio[start : min(audio.size, start + frame_count)]
        if segment.size < 64:
            return {pitch: 0.0 for pitch in pitch_list}

        window = np.hanning(segment.size).astype(np.float32)
        segment = segment * window
        nfft = 1
        target_nfft = max(1, int(segment.size) * KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_FFT_OVERSAMPLE)
        while nfft < target_nfft:
            nfft *= 2
        spectrum = np.abs(np.fft.rfft(segment, nfft))
        freqs = np.fft.rfftfreq(nfft, 1.0 / float(sample_rate))
        noise_mask = (
            (freqs >= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_NOISE_MIN_HZ)
            & (freqs <= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_NOISE_MAX_HZ)
        )
        if not bool(np.any(noise_mask)):
            return {pitch: 0.0 for pitch in pitch_list}
        noise = float(np.percentile(spectrum[noise_mask], 75))
        if noise <= 1e-9:
            return {pitch: 0.0 for pitch in pitch_list}

        values: dict[int, float] = {}
        all_pitches = range(
            KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MIN_PITCH,
            KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MAX_PITCH + 1,
        )
        for pitch in all_pitches:
            target_hz = _keys_midi_frequency_hz(pitch)
            bin_index = int(np.argmin(np.abs(freqs - target_hz)))
            lo = max(0, bin_index - 2)
            hi = min(len(spectrum), bin_index + 3)
            values[pitch] = float(np.max(spectrum[lo:hi])) / noise
        cache[cache_key] = values
        return {pitch: values.get(pitch, 0.0) for pitch in pitch_list}

    return _probe


def _keys_should_try_local_sparse_low_spectral_candidate(notes: list[MelodicNote]) -> bool:
    features = _keys_playable_profile_features(notes)
    if not (
        features.note_count >= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LOCAL_MIN_NOTES
        and features.min_pitch is not None
        and features.min_pitch < KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LOW_REGISTER_MAX_PITCH
        and features.mean_duration_sec >= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MIN_MEAN_DURATION_SEC
    ):
        return False
    pitches = [int(note.pitch) for note in notes]
    return max(pitches, default=0) >= KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_MIN_MAX_PITCH


def _keys_sparse_low_spectral_window_candidate(
    notes: list[MelodicNote],
    window_start: float,
    window_end: float,
    energy_ratios: Callable[[float, Iterable[int]], Mapping[int, float]],
) -> tuple[list[MelodicNote], list[MelodicNote]]:
    original = [
        note
        for note in notes
        if float(window_start) <= float(note.t_on) < float(window_end)
    ]
    if not original:
        return [], []

    shifted = _shift_keys_note_times(original, -float(window_start))

    def _window_energy(center: float, pitches: Iterable[int]) -> Mapping[int, float]:
        return energy_ratios(float(center) + float(window_start), pitches)

    candidate = _build_keys_sparse_low_spectral_candidate(shifted, _window_energy)
    if not candidate:
        return original, []
    return original, _shift_keys_note_times(candidate, float(window_start))


def _candidate_overlaps_accepted(
    start: float,
    end: float,
    accepted: list[tuple[float, float, list[MelodicNote]]],
) -> bool:
    return any(not (float(end) <= prior_start or float(start) >= prior_end) for prior_start, prior_end, _ in accepted)


def _apply_keys_sparse_low_spectral_local_windows(
    notes: list[MelodicNote],
    energy_ratios: Callable[[float, Iterable[int]], Mapping[int, float]],
    stem_path: Path | None,
) -> list[MelodicNote]:
    if not _keys_should_try_local_sparse_low_spectral_candidate(notes):
        return notes

    first_onset = min(float(note.t_on) for note in notes)
    last_offset = max(float(note.t_off) for note in notes)
    window_sec = KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LOCAL_WINDOW_SEC
    step_sec = KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LOCAL_STEP_SEC
    if last_offset - first_onset <= window_sec or step_sec <= 0.0:
        return notes

    candidates: list[tuple[float, float, float, list[MelodicNote]]] = []
    start = first_onset
    while start < last_offset:
        end = start + window_sec
        original, candidate = _keys_sparse_low_spectral_window_candidate(
            notes,
            start,
            end,
            energy_ratios,
        )
        if candidate:
            max_notes = max(
                1,
                int(
                    math.ceil(
                        len(original)
                        * KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LOCAL_MAX_NOTE_RATIO
                    )
                ),
            )
            if len(candidate) > max_notes:
                start += step_sec
                continue
            if _keys_max_polyphony(candidate) > KEYS_PLAYABLE_MAX_POLYPHONY:
                start += step_sec
                continue
            original_score = score_transcription(original, stem_path)
            candidate_score = score_transcription(candidate, stem_path)
            if (
                candidate_score
                + KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LOCAL_SCORE_TOLERANCE
                >= original_score
            ):
                replacement_start = min(float(note.t_on) for note in candidate)
                replacement_end = max(float(note.t_off) for note in candidate)
                candidates.append(
                    (
                        candidate_score - original_score,
                        replacement_start,
                        replacement_end,
                        candidate,
                    )
                )
        start += step_sec

    if not candidates:
        return notes

    accepted: list[tuple[float, float, list[MelodicNote]]] = []
    for _score_delta, start, end, candidate in sorted(
        candidates,
        key=lambda item: (item[0], -item[1]),
        reverse=True,
    ):
        if _candidate_overlaps_accepted(start, end, accepted):
            continue
        accepted.append((start, end, candidate))

    if not accepted:
        return notes

    def _inside_replaced_window(note: MelodicNote) -> bool:
        onset = float(note.t_on)
        offset = float(note.t_off)
        return any(onset < end and offset > start for start, end, _candidate in accepted)

    out = [note for note in notes if not _inside_replaced_window(note)]
    for _start, _end, candidate in accepted:
        out.extend(candidate)
    out = sorted(out, key=lambda item: (item.t_on, item.pitch, item.t_off))
    if _keys_max_polyphony(out) > KEYS_PLAYABLE_MAX_POLYPHONY:
        return notes
    if (
        score_transcription(out, stem_path)
        + KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_LOCAL_SCORE_TOLERANCE
        < score_transcription(notes, stem_path)
    ):
        return notes
    return out


def _apply_keys_sparse_low_spectral_chord_candidate_with_probe(
    notes: list[MelodicNote],
    energy_ratios: Callable[[float, Iterable[int]], Mapping[int, float]],
    *,
    stem_path: Path | None,
) -> list[MelodicNote]:
    if not notes:
        return notes

    if _keys_should_try_sparse_low_spectral_candidate(notes):
        candidate = _build_keys_sparse_low_spectral_candidate(notes, energy_ratios)
        if (
            candidate
            and _keys_max_polyphony(candidate) <= KEYS_PLAYABLE_MAX_POLYPHONY
            and score_transcription(candidate, stem_path)
            + KEYS_PLAYABLE_SPARSE_LOW_SPECTRAL_SCORE_TOLERANCE
            >= score_transcription(notes, stem_path)
        ):
            return candidate

    return _apply_keys_sparse_low_spectral_local_windows(notes, energy_ratios, stem_path)


def _apply_keys_sparse_low_spectral_chord_candidate(
    notes: list[MelodicNote],
    stem_path: Path | None,
) -> list[MelodicNote]:
    if stem_path is None or not notes:
        return notes
    try:
        return _apply_keys_sparse_low_spectral_chord_candidate_with_probe(
            notes,
            _keys_sparse_low_spectral_energy_probe(stem_path),
            stem_path=stem_path,
        )
    except Exception:
        return notes


def _keys_dense_low_spectral_candidate_pitches(
    onset: list[MelodicNote],
    active: list[MelodicNote],
    ratios: Mapping[int, float],
) -> list[tuple[float, int]]:
    existing = {int(note.pitch) for note in onset}
    context = onset + active
    candidates: list[tuple[float, int]] = []
    for pitch_raw, ratio_raw in ratios.items():
        pitch = int(pitch_raw)
        ratio = float(ratio_raw)
        if not (
            KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_UPPER_NOTE_MIN_PITCH
            <= pitch
            <= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_PITCH
        ):
            continue
        if any(abs(pitch - existing_pitch) <= 1 for existing_pitch in existing):
            continue
        if pitch >= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_HIGH_NOTE_MIN_PITCH:
            lower_ratio = max(
                (
                    float(ratios.get(pitch - interval, 0.0))
                    for interval in KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_HIGH_RELATIVE_INTERVALS
                    if pitch - interval >= 21
                ),
                default=0.0,
            )
            if ratio < KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_HIGH_RATIO:
                continue
            if (
                lower_ratio > 0.0
                and ratio
                < lower_ratio * KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_HIGH_RELATIVE_RATIO
            ):
                continue
        elif ratio < KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MID_RATIO:
            continue
        if not any(
            (pitch - int(note.pitch)) in KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_RELATION_INTERVALS
            for note in context
            if int(note.pitch) < pitch
        ):
            continue
        candidates.append((ratio, pitch))

    return sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True)


def _restore_keys_dense_low_spectral_treble_with_probe(
    notes: list[MelodicNote],
    energy_ratios: Callable[[float, Iterable[int]], Mapping[int, float]],
) -> list[MelodicNote]:
    if not _keys_should_restore_dense_low_spectral_treble(notes):
        return notes

    ordered = sorted(notes, key=lambda item: (item.t_on, item.pitch, item.t_off))
    starts = _keys_cluster_starts(
        ordered,
        window_sec=KEYS_PLAYABLE_ATTACK_CLUSTER_SEC,
    )
    if not starts:
        return notes

    out = list(ordered)
    added = 0
    end_limit = max((float(note.t_off) for note in ordered), default=0.0)
    pitch_range = range(
        KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MIN_PITCH,
        KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_PITCH + 1,
    )
    for start in starts:
        onset = [
            note
            for note in out
            if abs(float(note.t_on) - start) <= KEYS_PLAYABLE_ATTACK_CLUSTER_SEC
        ]
        active = [
            note
            for note in out
            if float(note.t_on) <= start + KEYS_PLAYABLE_ATTACK_CLUSTER_SEC
            and float(note.t_off) > start
        ]
        if len(onset) < 2 and not any(
            int(note.pitch) <= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_LOW_NOTE_MAX_PITCH
            for note in active
        ):
            continue
        if not any(int(note.pitch) <= 70 for note in onset + active):
            continue

        ratios = energy_ratios(start, pitch_range)
        cluster_added = 0
        for ratio, pitch in _keys_dense_low_spectral_candidate_pitches(onset, active, ratios):
            next_start = next((value for value in starts if value > start + 0.08), None)
            if next_start is None:
                duration = KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_DURATION_SEC
            else:
                duration = max(
                    KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MIN_DURATION_SEC,
                    min(
                        KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_DURATION_SEC,
                        next_start
                        - start
                        - KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_CHORD_GAP_SEC,
                    ),
                )
            candidate = MelodicNote(
                t_on=round(start, 6),
                t_off=round(min(end_limit, start + duration), 6),
                pitch=pitch,
                velocity=KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_VELOCITY,
                instrument=ordered[0].instrument if ordered else "keys",
            )
            if float(candidate.t_off) <= float(candidate.t_on):
                continue
            if _keys_has_near_duplicate(
                out,
                candidate,
                tolerance_sec=KEYS_PLAYABLE_ATTACK_CLUSTER_SEC,
            ):
                continue
            if (
                _keys_attack_cluster_count(
                    out,
                    candidate,
                    window_sec=KEYS_PLAYABLE_ATTACK_CLUSTER_SEC,
                )
                >= KEYS_PLAYABLE_MAX_NOTES_PER_ATTACK
            ):
                continue
            if _keys_would_exceed_polyphony(
                out,
                candidate,
                max_polyphony=KEYS_PLAYABLE_MAX_POLYPHONY,
            ):
                continue
            out.append(candidate)
            added += 1
            cluster_added += 1
            if (
                added >= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_ADDED_NOTES
                or cluster_added >= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_ADDED_PER_CLUSTER
            ):
                break
        if added >= KEYS_PLAYABLE_DENSE_LOW_SPECTRAL_MAX_ADDED_NOTES:
            break

    if added <= 0:
        return notes
    out = sorted(out, key=lambda item: (item.t_on, item.pitch, item.t_off))
    if _keys_max_polyphony(out) > KEYS_PLAYABLE_MAX_POLYPHONY:
        return notes
    return out


def _restore_keys_dense_low_spectral_treble(
    notes: list[MelodicNote],
    stem_path: Path | None,
) -> list[MelodicNote]:
    if stem_path is None or not notes or not _keys_should_restore_dense_low_spectral_treble(notes):
        return notes
    try:
        return _restore_keys_dense_low_spectral_treble_with_probe(
            notes,
            _keys_sparse_low_spectral_energy_probe(stem_path),
        )
    except Exception:
        return notes


def _restore_keys_sparse_low_treble(notes: list[MelodicNote]) -> list[MelodicNote]:
    if not _keys_should_restore_sparse_low_treble(notes):
        return notes

    out = sorted(notes, key=lambda item: (item.t_on, item.pitch, item.t_off))
    added = 0
    for note in out[:]:
        if int(note.pitch) > KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_SOURCE_MAX_PITCH:
            continue
        for octave in (12, 24):
            target_pitch = int(note.pitch) + octave
            if not (
                KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_TARGET_MIN_PITCH
                <= target_pitch
                <= KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_TARGET_MAX_PITCH
            ):
                continue
            velocity = max(
                KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MIN_VELOCITY,
                min(
                    KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MAX_VELOCITY,
                    int(
                        round(
                            max(1, min(127, int(note.velocity)))
                            * KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_VELOCITY_SCALE
                        )
                    ),
                ),
            )
            candidate = replace(note, pitch=target_pitch, velocity=velocity)
            if _keys_has_near_duplicate(
                out,
                candidate,
                tolerance_sec=KEYS_PLAYABLE_ATTACK_CLUSTER_SEC,
            ):
                continue
            if (
                _keys_attack_cluster_count(
                    out,
                    candidate,
                    window_sec=KEYS_PLAYABLE_ATTACK_CLUSTER_SEC,
                )
                >= KEYS_PLAYABLE_MAX_NOTES_PER_ATTACK
            ):
                continue
            if _keys_would_exceed_polyphony(
                out,
                candidate,
                max_polyphony=KEYS_PLAYABLE_MAX_POLYPHONY,
            ):
                continue
            out.append(candidate)
            added += 1
            if added >= KEYS_PLAYABLE_SPARSE_LOW_TREBLE_RESTORE_MAX_ADDED_NOTES:
                return sorted(out, key=lambda item: (item.t_on, item.pitch, item.t_off))

    return sorted(out, key=lambda item: (item.t_on, item.pitch, item.t_off))


def _apply_keys_basic_pitch_release_sustain(notes: list[MelodicNote]) -> list[MelodicNote]:
    sparse_chordal_release = _keys_should_apply_sparse_release_sustain(notes)
    if (
        len(notes) < KEYS_PLAYABLE_RELEASE_SUSTAIN_MIN_NOTES
        and not sparse_chordal_release
    ):
        return notes
    density = _keys_note_density_per_minute(notes)
    if density <= KEYS_PLAYABLE_RELEASE_SUSTAIN_MIN_DENSITY_PER_MIN and not sparse_chordal_release:
        return notes

    min_duration_sec = (
        KEYS_PLAYABLE_SPARSE_RELEASE_SUSTAIN_MIN_DURATION_SEC
        if sparse_chordal_release
        else KEYS_PLAYABLE_RELEASE_SUSTAIN_MIN_DURATION_SEC
    )
    max_duration_sec = (
        KEYS_PLAYABLE_SPARSE_RELEASE_SUSTAIN_MAX_DURATION_SEC
        if sparse_chordal_release
        else KEYS_PLAYABLE_RELEASE_SUSTAIN_MAX_DURATION_SEC
    )

    out = _apply_keys_release_sustain_bounds(
        notes,
        min_duration_sec=min_duration_sec,
        max_duration_sec=max_duration_sec,
        min_pitch=KEYS_PLAYABLE_RELEASE_SUSTAIN_MIN_PITCH,
    )
    if _keys_should_apply_low_dense_release_sustain(out):
        out = _apply_keys_release_sustain_bounds(
            out,
            min_duration_sec=KEYS_PLAYABLE_LOW_DENSE_RELEASE_SUSTAIN_MIN_DURATION_SEC,
            max_duration_sec=KEYS_PLAYABLE_LOW_DENSE_RELEASE_SUSTAIN_MAX_DURATION_SEC,
            min_pitch=KEYS_PLAYABLE_LOW_DENSE_RELEASE_SUSTAIN_MIN_PITCH,
            prefer_short_notes=True,
        )
    out = _trim_keys_sparse_low_tails(out)
    out = _restore_keys_sparse_low_treble(out)
    return _trim_keys_dense_high_staccato_tails(out)


def _keys_onset_alignment_clusters(notes: list[MelodicNote]) -> list[list[int]]:
    clusters: list[list[int]] = []
    cluster_start: float | None = None
    for idx, note in sorted(enumerate(notes), key=lambda item: (item[1].t_on, item[1].pitch)):
        onset = float(note.t_on)
        if cluster_start is None or onset - cluster_start > KEYS_PLAYABLE_ATTACK_CLUSTER_SEC:
            clusters.append([idx])
            cluster_start = onset
        else:
            clusters[-1].append(idx)
    return clusters


def _median_float(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _apply_keys_onset_peak_alignment(
    notes: list[MelodicNote],
    *,
    envelope_times: list[float],
    envelope_values: list[float],
    peak_times: list[float],
    peak_values: list[float],
) -> list[MelodicNote]:
    if not notes or not envelope_times or not envelope_values or not peak_times or not peak_values:
        return notes

    out = list(sorted(notes, key=lambda item: (item.t_on, item.pitch, item.t_off)))
    for cluster in _keys_onset_alignment_clusters(out):
        onset = min(float(out[idx].t_on) for idx in cluster)
        lo = onset - KEYS_PLAYABLE_AUDIO_ALIGN_SEARCH_SEC
        hi = onset + KEYS_PLAYABLE_AUDIO_ALIGN_SEARCH_SEC
        candidates = [
            (float(value), abs(float(time) - onset), float(time))
            for time, value in zip(peak_times, peak_values, strict=False)
            if lo <= float(time) <= hi
        ]
        if not candidates:
            continue

        peak_value, _distance, peak_time = max(
            candidates,
            key=lambda item: (item[0], -item[1]),
        )
        local_values = [
            float(value)
            for time, value in zip(envelope_times, envelope_values, strict=False)
            if lo <= float(time) <= hi
        ]
        local_median = _median_float(local_values)
        if peak_value < max(
            KEYS_PLAYABLE_AUDIO_ALIGN_MIN_PEAK,
            local_median * KEYS_PLAYABLE_AUDIO_ALIGN_MIN_LOCAL_MEDIAN_RATIO,
        ):
            continue

        shift = max(
            -KEYS_PLAYABLE_AUDIO_ALIGN_MAX_SHIFT_SEC,
            min(KEYS_PLAYABLE_AUDIO_ALIGN_MAX_SHIFT_SEC, peak_time - onset),
        )
        if abs(shift) < KEYS_PLAYABLE_AUDIO_ALIGN_MIN_SHIFT_SEC:
            continue

        candidate = list(out)
        for idx in cluster:
            note = out[idx]
            t_on = max(0.0, float(note.t_on) + shift)
            t_off = max(t_on + 0.03, float(note.t_off) + shift)
            candidate[idx] = replace(
                note,
                t_on=round(t_on, 6),
                t_off=round(t_off, 6),
            )
        if _keys_max_polyphony(candidate) > KEYS_PLAYABLE_MAX_POLYPHONY:
            continue
        out = candidate

    return sorted(out, key=lambda item: (item.t_on, item.pitch, item.t_off))


def _apply_keys_audio_onset_alignment(
    notes: list[MelodicNote],
    stem_path: Path | None,
) -> list[MelodicNote]:
    if stem_path is None or not notes:
        return notes
    features = _keys_playable_profile_features(notes)
    if (
        features.note_count < KEYS_PLAYABLE_AUDIO_ALIGN_MIN_NOTES
        or features.density_per_minute < KEYS_PLAYABLE_AUDIO_ALIGN_MIN_DENSITY_PER_MIN
        or features.min_pitch is None
        or features.min_pitch >= KEYS_PLAYABLE_AUDIO_ALIGN_LOW_REGISTER_MAX_PITCH
        or features.mean_duration_sec > KEYS_PLAYABLE_AUDIO_ALIGN_MAX_MEAN_DURATION_SEC
    ):
        return notes

    try:
        import librosa
        import numpy as np
        from scipy.signal import find_peaks

        audio, sample_rate = librosa.load(
            str(stem_path),
            sr=KEYS_PLAYABLE_AUDIO_ALIGN_SAMPLE_RATE,
            mono=True,
        )
        envelope = librosa.onset.onset_strength(
            y=audio,
            sr=sample_rate,
            hop_length=KEYS_PLAYABLE_AUDIO_ALIGN_HOP_LENGTH,
        )
        if len(envelope) <= 0:
            return notes
        peak = float(np.max(envelope))
        if peak <= 1e-9:
            return notes
        normalized = envelope / peak
        frames = np.arange(len(normalized))
        times = librosa.frames_to_time(
            frames,
            sr=sample_rate,
            hop_length=KEYS_PLAYABLE_AUDIO_ALIGN_HOP_LENGTH,
        )
        min_distance = max(
            1,
            int(
                round(
                    KEYS_PLAYABLE_AUDIO_ALIGN_PEAK_MIN_DISTANCE_SEC
                    * sample_rate
                    / KEYS_PLAYABLE_AUDIO_ALIGN_HOP_LENGTH
                )
            ),
        )
        peak_indices, _props = find_peaks(
            normalized,
            height=0.03,
            prominence=KEYS_PLAYABLE_AUDIO_ALIGN_PEAK_PROMINENCE,
            distance=min_distance,
        )
        if len(peak_indices) <= 0:
            return notes
        return _apply_keys_onset_peak_alignment(
            notes,
            envelope_times=[float(value) for value in times],
            envelope_values=[float(value) for value in normalized],
            peak_times=[float(times[idx]) for idx in peak_indices],
            peak_values=[float(normalized[idx]) for idx in peak_indices],
        )
    except Exception:
        return notes


def _apply_keys_playability_cleanup(notes: list[MelodicNote]) -> list[MelodicNote]:
    normalized: list[MelodicNote] = []
    for note in notes:
        if float(note.t_off) <= float(note.t_on):
            continue
        t_on = max(0.0, float(note.t_on))
        t_off = max(t_on, float(note.t_off))
        normalized.append(
            MelodicNote(
                t_on=round(t_on, 6),
                t_off=round(t_off, 6),
                pitch=max(21, min(108, int(note.pitch))),
                velocity=max(1, min(127, int(note.velocity))),
                instrument=note.instrument,
            )
        )
    normalized.sort(key=lambda item: (item.t_on, item.pitch, item.t_off))
    if len(normalized) <= 1:
        return normalized
    if _keys_max_polyphony(normalized) <= KEYS_PLAYABLE_MAX_POLYPHONY:
        return _apply_keys_playable_velocity_calibration(
            _apply_keys_playable_sustain(_apply_keys_confidence_density_prune(normalized))
        )

    cluster_for_index = _keys_cluster_note_indices_by_onset(
        normalized,
        window_sec=KEYS_PLAYABLE_ATTACK_CLUSTER_SEC,
    )
    extremes_by_cluster = _keys_cluster_extreme_pitches(normalized, cluster_for_index)
    ranked: list[tuple[float, float, int, int, MelodicNote]] = []
    for idx, note in enumerate(normalized):
        cluster_id = int(cluster_for_index[idx])
        score = _keys_playable_priority(note, cluster_extremes=extremes_by_cluster[cluster_id])
        ranked.append((score, -float(note.t_on), int(note.pitch), idx, note))

    selected: list[MelodicNote] = []
    selected_per_cluster: dict[int, int] = {}
    for _score, _neg_time, _pitch, idx, note in sorted(ranked, reverse=True):
        cluster_id = int(cluster_for_index[idx])
        if selected_per_cluster.get(cluster_id, 0) >= KEYS_PLAYABLE_MAX_NOTES_PER_ATTACK:
            continue
        if _keys_would_exceed_polyphony(
            selected,
            note,
            max_polyphony=KEYS_PLAYABLE_MAX_POLYPHONY,
        ):
            continue
        selected.append(note)
        selected_per_cluster[cluster_id] = selected_per_cluster.get(cluster_id, 0) + 1

    return _apply_keys_playable_velocity_calibration(
        _apply_keys_playable_sustain(
            _apply_keys_confidence_density_prune(
                sorted(selected, key=lambda item: (item.t_on, item.pitch, item.t_off))
            )
        )
    )


def apply_role_playability_cleanup(notes: list[MelodicNote], instrument: str) -> list[MelodicNote]:
    if instrument == "keys":
        return _apply_keys_playability_cleanup(notes)
    if instrument not in {"bass", "guitar", "lead_guitar", "rhythm_guitar"} or len(notes) <= 1:
        return notes

    if instrument == "bass":
        cluster_window = 0.035
        min_cluster_gap = 0.045
        max_polyphony = 1
    elif instrument == "lead_guitar":
        cluster_window = 0.03
        min_cluster_gap = 0.04
        max_polyphony = 2
    else:
        cluster_window = 0.035
        min_cluster_gap = 0.065
        max_polyphony = 3

    ordered = sorted(notes, key=lambda n: (float(n.t_on), -int(n.velocity), -float(n.t_off - n.t_on)))
    clusters: list[list[MelodicNote]] = []
    for note in ordered:
        if not clusters or abs(float(note.t_on) - float(clusters[-1][0].t_on)) > cluster_window:
            clusters.append([note])
        else:
            clusters[-1].append(note)

    kept: list[MelodicNote] = []
    last_cluster_time: float | None = None
    for cluster in clusters:
        cluster_time = min(float(n.t_on) for n in cluster)
        if last_cluster_time is not None and cluster_time - last_cluster_time < min_cluster_gap:
            continue
        selected = sorted(
            cluster,
            key=lambda n: (int(n.velocity), float(n.t_off - n.t_on)),
            reverse=True,
        )[:max_polyphony]
        kept.extend(selected)
        last_cluster_time = cluster_time

    return sorted(kept, key=lambda n: (float(n.t_on), int(n.pitch)))


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
        tagged_notes = apply_role_playability_cleanup(tagged_notes, instrument)

        results.append(
            InstrumentTranscriptionResult(
                instrument=instrument,
                notes=tagged_notes,
                used_method=result.used_method,
                attempted_methods=result.attempted_methods,
                warnings=result.warnings,
                stem_path=str(stem_path),
                used_score=result.used_score,
                attempt_scores=result.attempt_scores,
            )
        )

    return results
