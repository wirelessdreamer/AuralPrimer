"""Tests for the --wholemix-transcriber option plumbing (cmd_import whole-mix
orchestration entry point). The whole-mix run itself is exercised via the
mocked-package adapter tests; here we pin the option parsing + validation and
the fail-safe default (unset -> empty -> per-stem path)."""
from __future__ import annotations

import argparse

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
