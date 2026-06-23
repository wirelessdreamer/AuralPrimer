"""CLI option-resolution smoke tests for the drum stem-silence gate.

These confirm that the new CLI flags and config keys make it through
`_resolve_transcription_options` into the resolved tr_opts dict, and that
malformed inputs surface validation errors. The actual gating behavior
is covered in `test_drum_stem_silence_gate.py`.
"""
from __future__ import annotations

import argparse



def _build_args(**overrides: object) -> argparse.Namespace:
    """Build the minimal argparse namespace expected by
    `_resolve_transcription_options`. Tests override specific fields."""

    base: dict[str, object] = {
        "drum_filter": "combined_filter",
        "drum_stem_path": None,
        "melodic_method": "auto",
        "transcription_profile": "gameplay_default",
        "beat_analysis_mode": "high_accuracy",
        "stem_separation_provider": "auto",
        "stem_separation_provider_path": None,
        "shifts": 1,
        "multi_filter": False,
        "drum_silence_gate_dbfs": None,
        "drum_silence_gate_window_ms": None,
        "drum_silence_gate_disabled": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_default_options_have_none_gate_values_so_env_and_built_in_apply() -> None:
    """When neither the CLI nor config sets the gate flags, the resolved
    tr_opts must surface None so the runtime helper honors the env/built-in
    defaults inside `validate_drum_events_against_stem_silence`."""

    from aural_ingest.cli import _resolve_transcription_options

    args = _build_args()
    tr_opts, err = _resolve_transcription_options(args, config={})
    assert err is None
    assert tr_opts is not None
    assert tr_opts["drum_silence_gate_dbfs"] is None
    assert tr_opts["drum_silence_gate_window_ms"] is None
    assert tr_opts["drum_silence_gate_disabled"] is False


def test_cli_dbfs_flag_threads_through_to_tr_opts() -> None:
    from aural_ingest.cli import _resolve_transcription_options

    args = _build_args(drum_silence_gate_dbfs=-42.5, drum_silence_gate_window_ms=80.0)
    tr_opts, err = _resolve_transcription_options(args, config={})
    assert err is None
    assert tr_opts is not None
    assert tr_opts["drum_silence_gate_dbfs"] == -42.5
    assert tr_opts["drum_silence_gate_window_ms"] == 80.0


def test_config_key_dbfs_used_when_cli_not_set() -> None:
    """Project config keys provide the same knob without a CLI flag."""

    from aural_ingest.cli import _resolve_transcription_options

    args = _build_args()
    tr_opts, err = _resolve_transcription_options(
        args,
        config={"drum_silence_gate_dbfs": -38.0, "drum_silence_gate_window_ms": 25.0},
    )
    assert err is None
    assert tr_opts is not None
    assert tr_opts["drum_silence_gate_dbfs"] == -38.0
    assert tr_opts["drum_silence_gate_window_ms"] == 25.0


def test_cli_overrides_config_when_both_present() -> None:
    from aural_ingest.cli import _resolve_transcription_options

    args = _build_args(drum_silence_gate_dbfs=-44.0)
    tr_opts, err = _resolve_transcription_options(
        args, config={"drum_silence_gate_dbfs": -38.0}
    )
    assert err is None
    assert tr_opts is not None
    assert tr_opts["drum_silence_gate_dbfs"] == -44.0


def test_disabled_flag_threads_through() -> None:
    from aural_ingest.cli import _resolve_transcription_options

    args = _build_args(drum_silence_gate_disabled=True)
    tr_opts, err = _resolve_transcription_options(args, config={})
    assert err is None
    assert tr_opts is not None
    assert tr_opts["drum_silence_gate_disabled"] is True


def test_invalid_dbfs_value_returns_error() -> None:
    from aural_ingest.cli import _resolve_transcription_options

    args = _build_args(drum_silence_gate_dbfs="notanumber")
    tr_opts, err = _resolve_transcription_options(args, config={})
    assert tr_opts is None
    assert err is not None
    assert "drum-silence-gate-dbfs" in err


def test_invalid_window_value_returns_error() -> None:
    from aural_ingest.cli import _resolve_transcription_options

    args = _build_args(drum_silence_gate_window_ms="bad")
    tr_opts, err = _resolve_transcription_options(args, config={})
    assert tr_opts is None
    assert err is not None
    assert "drum-silence-gate-window-ms" in err


def test_non_positive_window_returns_error() -> None:
    from aural_ingest.cli import _resolve_transcription_options

    args = _build_args(drum_silence_gate_window_ms=0.0)
    tr_opts, err = _resolve_transcription_options(args, config={})
    assert tr_opts is None
    assert err is not None
    assert "drum-silence-gate-window-ms" in err


def test_argparse_registers_the_new_flags() -> None:
    """Argparse-level smoke: the new flags must parse without 'unrecognized
    argument' errors, including the disable switch."""

    from aural_ingest.cli import _add_transcription_options

    parser = argparse.ArgumentParser()
    _add_transcription_options(parser)
    ns = parser.parse_args(
        [
            "--drum-silence-gate-dbfs",
            "-48.5",
            "--drum-silence-gate-window-ms",
            "40",
            "--no-drum-silence-gate",
        ]
    )
    assert ns.drum_silence_gate_dbfs == -48.5
    assert ns.drum_silence_gate_window_ms == 40.0
    assert ns.drum_silence_gate_disabled is True
