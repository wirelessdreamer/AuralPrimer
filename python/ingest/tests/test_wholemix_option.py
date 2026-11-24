"""Tests for the --wholemix-transcriber option plumbing (cmd_import whole-mix
orchestration entry point). The whole-mix run itself is exercised via the
mocked-package adapter tests; here we pin the option parsing + validation and
the fail-safe default (unset -> empty -> per-stem path)."""
from __future__ import annotations

import argparse
import pytest

from aural_ingest.cli import _add_transcription_options, _resolve_transcription_options


def _opts(argv, config=None):
    p = argparse.ArgumentParser()
    _add_transcription_options(p)
    return _resolve_transcription_options(p.parse_args(argv), config or {})


def test_wholemix_flag_parses_muscriptor() -> None:
    opts, err = _opts(["--wholemix-transcriber", "muscriptor"])
    assert err is None
    assert opts["wholemix_transcriber"] == "muscriptor"


def test_wholemix_defaults_to_empty_when_unset() -> None:
    opts, err = _opts([])
    assert err is None
    assert opts["wholemix_transcriber"] == ""  # -> per-stem pipeline


def test_wholemix_from_config() -> None:
    opts, err = _opts([], {"wholemix_transcriber": "muscriptor"})
    assert err is None
    assert opts["wholemix_transcriber"] == "muscriptor"


def test_wholemix_cli_overrides_config() -> None:
    # An explicit flag wins over config (mirrors the other transcription opts).
    opts, err = _opts(["--wholemix-transcriber", "muscriptor"], {"wholemix_transcriber": ""})
    assert err is None
    assert opts["wholemix_transcriber"] == "muscriptor"


def test_wholemix_invalid_value_is_rejected() -> None:
    opts, err = _opts(["--wholemix-transcriber", "bogus"])
    assert opts is None
    assert err is not None and "wholemix-transcriber" in err


def test_wholemix_case_insensitive() -> None:
    opts, err = _opts(["--wholemix-transcriber", "MuScriptor"])
    assert err is None
    assert opts["wholemix_transcriber"] == "muscriptor"


def test_keyboard_only_forces_muscriptor_and_bypasses_separation() -> None:
    opts, err = _opts(["--keyboard-only"])
    assert err is None
    assert opts["keyboard_only"] is True
    assert opts["wholemix_transcriber"] == "muscriptor"
    assert opts["stem_separation_provider"] == "none"


def test_keyboard_only_config_and_explicit_opt_out() -> None:
    opts, err = _opts([], {"keyboard_only": True})
    assert err is None and opts["keyboard_only"] is True
    opts, err = _opts(["--no-keyboard-only"], {"keyboard_only": True})
    assert err is None and opts["keyboard_only"] is False


def test_keyboard_only_rejects_ambiguous_config_string() -> None:
    opts, err = _opts([], {"keyboard_only": "false"})
    assert opts is None and "boolean" in err


def test_keyboard_only_has_a_distinct_song_identity() -> None:
    from aural_ingest.cli import _stable_song_id

    defaults, _ = _opts([])
    keyboard = {**defaults, "keyboard_only": True}
    assert _stable_song_id("same-audio", "full", defaults) != _stable_song_id("same-audio", "full", keyboard)


def test_selectable_recognition_options_and_identity() -> None:
    from aural_ingest.cli import _stable_song_id

    opts, err = _opts(["--recognition-instruments", "organ,acoustic_guitar,organ", "--recognition-stems", "other,keys"])
    assert err is None
    assert opts["recognition_instruments"] == ["acoustic_guitar", "organ"]
    assert opts["recognition_stems"] == ["keys", "other"]
    assert opts["stem_separation_provider"] == "none"
    assert opts["wholemix_transcriber"] == "muscriptor"
    assert not opts["keyboard_only"]
    changed = {**opts, "recognition_instruments": ["electric_piano"]}
    assert _stable_song_id("same", "full", opts) != _stable_song_id("same", "full", changed)
    changed = {**opts, "recognition_stem_hashes": {"keys": "new-audio"}}
    assert _stable_song_id("same", "full", opts) != _stable_song_id("same", "full", changed)


@pytest.mark.parametrize("config", [
    {"recognition_instruments": []}, {"recognition_instruments": ["banjo"]},
    {"recognition_instruments": True}, {"recognition_stems": ["mix"]},
    {"source_stem_role": "mix"},
    {"source_stem_role": "keys", "recognition_stems": ["guitar"]},
    {"keyboard_only": True, "recognition_instruments": ["organ"]},
])
def test_bad_recognition_selection_is_rejected(config) -> None:
    opts, err = _opts([], config)
    assert opts is None and err


def test_single_stem_and_config_recognition_selection() -> None:
    opts, err = _opts(["--source-stem-role", "guitar"], {"recognition_instruments": ["acoustic_guitar"]})
    assert err is None
    assert opts["source_stem_role"] == "guitar"
    assert opts["stem_separation_provider"] == "none"
