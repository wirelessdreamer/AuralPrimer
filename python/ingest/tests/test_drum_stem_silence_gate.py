"""Tests for `validate_drum_events_against_stem_silence`.

The stem-silence gate is a defensive post-filter that drops drum events
whose neighborhood in the separated drum stem is below a configurable
dBFS threshold. The goal is to catch false-positive drum hits emitted by
heuristic engines (notably `combined_filter`) during silent stretches of
the drum stem -- a documented failure mode on Psalm 19, where the stem
has long instrumental gaps but the engine kept firing.

These tests use synthetic audio with known-loud and known-silent regions
so we can assert that real hits survive and ghost hits in silent regions
are dropped. We deliberately do *not* run any drum engine here: the gate
is engine-agnostic by design (works on `DrumEvent` lists from any
source) so testing the gate in isolation is the cleanest contract.
"""
from __future__ import annotations

import math
import os
import struct
import wave
from pathlib import Path

import pytest

from aural_ingest.transcription import (
    DEFAULT_DRUM_SILENCE_GATE_DBFS,
    DEFAULT_DRUM_SILENCE_GATE_WINDOW_MS,
    DRUM_SILENCE_GATE_ENV_DBFS,
    DRUM_SILENCE_GATE_ENV_DISABLED,
    DRUM_SILENCE_GATE_ENV_WINDOW_MS,
    DrumEvent,
    validate_drum_events_against_stem_silence,
)


SR = 44_100


def _write_stem_with_hits(
    path: Path,
    *,
    duration_sec: float,
    hit_times_sec: tuple[float, ...],
    hit_freq_hz: float = 80.0,
    hit_amp: float = 0.85,
    hit_len_sec: float = 0.08,
    background_amp: float = 0.0,
    sr: int = SR,
) -> None:
    """Write a mono 16-bit WAV with short percussive pulses at the given
    times. Outside the pulses, the file is silent (or has the requested
    background amplitude — useful for testing the gate at moderate levels)."""

    n = int(round(sr * duration_sec))
    samples = [background_amp] * n
    pulse_len = max(1, int(round(hit_len_sec * sr)))
    for ht in hit_times_sec:
        start = int(round(ht * sr))
        for i in range(pulse_len):
            idx = start + i
            if idx >= n:
                break
            env = math.exp(-6.0 * (i / float(pulse_len)))
            samples[idx] += hit_amp * math.sin(2.0 * math.pi * hit_freq_hz * (i / float(sr))) * env

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sr)
        for s in samples:
            v = max(-1.0, min(1.0, s))
            wav_file.writeframesraw(struct.pack("<h", int(v * 30_000)))


def _kick_event(t: float, *, note: int = 36, vel: int = 96) -> DrumEvent:
    return DrumEvent(time=float(t), note=int(note), velocity=int(vel))


# ---------------------------------------------------------------------------
# Behavior contracts
# ---------------------------------------------------------------------------


def test_default_gate_drops_events_in_silent_regions_keeps_real_hits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synthetic stem has 3 real kicks; we feed in 3 real events at the kick
    times plus 4 ghost events in silent stretches. The default gate must
    keep all 3 real events and drop all 4 ghost events."""

    # Ensure environment overrides don't pollute the test.
    monkeypatch.delenv(DRUM_SILENCE_GATE_ENV_DBFS, raising=False)
    monkeypatch.delenv(DRUM_SILENCE_GATE_ENV_WINDOW_MS, raising=False)
    monkeypatch.delenv(DRUM_SILENCE_GATE_ENV_DISABLED, raising=False)

    real_hits = (1.0, 2.5, 4.0)
    stem = tmp_path / "drums.wav"
    _write_stem_with_hits(stem, duration_sec=6.0, hit_times_sec=real_hits)

    ghost_times = (0.2, 1.7, 3.2, 5.5)  # all in silent regions
    events = [_kick_event(t) for t in real_hits] + [_kick_event(t, note=49, vel=72) for t in ghost_times]
    events.sort(key=lambda e: e.time)

    kept, meta = validate_drum_events_against_stem_silence(events, stem)

    kept_times = sorted(round(e.time, 3) for e in kept)
    assert kept_times == sorted(round(t, 3) for t in real_hits), (
        f"expected gate to retain only the real-hit times {real_hits}; got {kept_times}"
    )
    assert meta["events_in"] == len(events)
    assert meta["events_out"] == len(real_hits)
    assert meta["dropped"] == len(ghost_times)
    assert meta["disabled"] is False
    assert meta["gate_dbfs"] == DEFAULT_DRUM_SILENCE_GATE_DBFS
    assert meta["window_ms"] == DEFAULT_DRUM_SILENCE_GATE_WINDOW_MS
    assert meta["stem_load_ok"] is True
    # Quietest dropped should be close to negative infinity for pure silence;
    # we recorded it as None in that case (see _rms_to_dbfs returning -inf).
    qd = meta["quietest_dropped_dbfs"]
    assert qd is None or qd < DEFAULT_DRUM_SILENCE_GATE_DBFS


def test_gate_disabled_returns_events_unchanged(tmp_path: Path) -> None:
    """If callers explicitly disable the gate, every event survives,
    regardless of the stem audio. Metadata still records the resolved
    settings so manifests stay consistent."""

    real_hits = (1.0,)
    stem = tmp_path / "drums.wav"
    _write_stem_with_hits(stem, duration_sec=3.0, hit_times_sec=real_hits)

    events = [_kick_event(0.2), _kick_event(1.0), _kick_event(2.5)]
    kept, meta = validate_drum_events_against_stem_silence(events, stem, disabled=True)

    assert kept == events
    assert meta["disabled"] is True
    assert meta["events_in"] == 3
    assert meta["events_out"] == 3
    assert meta["dropped"] == 0


def test_env_disabled_skips_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AURALPRIMER_DRUM_SILENCE_GATE_DISABLED=1 disables the gate when the
    caller hasn't passed `disabled=` explicitly."""

    monkeypatch.setenv(DRUM_SILENCE_GATE_ENV_DISABLED, "1")
    stem = tmp_path / "drums.wav"
    _write_stem_with_hits(stem, duration_sec=2.0, hit_times_sec=(0.5,))

    events = [_kick_event(0.1), _kick_event(0.5)]
    kept, meta = validate_drum_events_against_stem_silence(events, stem)

    assert kept == events
    assert meta["disabled"] is True


def test_custom_gate_dbfs_kwarg_overrides_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two events sit in stem regions at roughly -45 dBFS RMS (low-level
    room tone). The default gate (-50 dBFS) would keep them; the strict
    gate (-30 dBFS) drops them. Confirms the threshold knob is wired
    through and meaningful, without depending on stem peak/silence
    extremes."""

    monkeypatch.delenv(DRUM_SILENCE_GATE_ENV_DBFS, raising=False)
    stem = tmp_path / "drums.wav"
    # Background ~0.0056 RMS = ~ -45 dBFS, plus one loud kick.
    _write_stem_with_hits(
        stem,
        duration_sec=3.0,
        hit_times_sec=(1.0,),
        background_amp=0.0056,
    )

    events = [_kick_event(0.1), _kick_event(1.0), _kick_event(2.5)]

    # Permissive default catches all three: real kick + background-noise events.
    kept_default, meta_default = validate_drum_events_against_stem_silence(events, stem)
    assert meta_default["gate_dbfs"] == DEFAULT_DRUM_SILENCE_GATE_DBFS
    assert len(kept_default) == 3

    # Strict gate at -30 dBFS keeps only the real kick.
    kept_strict, meta_strict = validate_drum_events_against_stem_silence(
        events, stem, gate_dbfs=-30.0
    )
    assert meta_strict["gate_dbfs"] == -30.0
    assert [round(e.time, 3) for e in kept_strict] == [1.0]
    assert meta_strict["dropped"] == 2


def test_env_dbfs_override_picked_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AURALPRIMER_DRUM_SILENCE_GATE_DBFS is the env knob; when set and the
    caller doesn't pass an explicit kwarg, the env value wins."""

    monkeypatch.setenv(DRUM_SILENCE_GATE_ENV_DBFS, "-20")
    monkeypatch.delenv(DRUM_SILENCE_GATE_ENV_DISABLED, raising=False)

    real_hits = (1.0, 2.5)
    stem = tmp_path / "drums.wav"
    _write_stem_with_hits(stem, duration_sec=4.0, hit_times_sec=real_hits)

    # All events at real hit timestamps; with the aggressive -20 dBFS gate,
    # they should still survive (kicks at 0.85 amplitude land near -3 dBFS
    # at peak, well above the gate). The point of the test is to confirm
    # the env reading path works, not that the gate drops the events.
    events = [_kick_event(t) for t in real_hits]
    kept, meta = validate_drum_events_against_stem_silence(events, stem)
    assert meta["gate_dbfs"] == -20.0
    assert kept, "kicks should still pass a -20 dBFS gate"


def test_window_ms_kwarg_controls_neighborhood(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tiny window (~1 ms) computed exactly on a silence-to-hit boundary
    can miss the hit; widening it brings the hit back in. Confirms the
    window knob is honored."""

    monkeypatch.delenv(DRUM_SILENCE_GATE_ENV_DBFS, raising=False)
    monkeypatch.delenv(DRUM_SILENCE_GATE_ENV_WINDOW_MS, raising=False)

    # Hit centered at 1.0s. We deliberately set the event time slightly
    # before the hit so the narrow window samples silence.
    real_hits = (1.0,)
    stem = tmp_path / "drums.wav"
    _write_stem_with_hits(stem, duration_sec=2.0, hit_times_sec=real_hits)
    event = _kick_event(0.95)  # 50 ms before the hit

    narrow_kept, narrow_meta = validate_drum_events_against_stem_silence(
        [event], stem, window_ms=1.0
    )
    wide_kept, wide_meta = validate_drum_events_against_stem_silence(
        [event], stem, window_ms=120.0
    )

    # With a 1 ms half-window at t=0.95s the window misses the hit at t=1.0s
    # (which starts 50 ms later), so the RMS is dominated by silence and the
    # event drops.
    assert narrow_meta["window_ms"] == 1.0
    assert narrow_meta["dropped"] == 1
    assert narrow_kept == []

    # With a 120 ms half-window, the window comfortably covers the hit and
    # the event survives.
    assert wide_meta["window_ms"] == 120.0
    assert wide_meta["dropped"] == 0
    assert wide_kept == [event]


def test_empty_events_returns_empty_with_metadata(tmp_path: Path) -> None:
    stem = tmp_path / "drums.wav"
    _write_stem_with_hits(stem, duration_sec=1.0, hit_times_sec=())

    kept, meta = validate_drum_events_against_stem_silence([], stem)
    assert kept == []
    assert meta["events_in"] == 0
    assert meta["events_out"] == 0
    assert meta["dropped"] == 0


def test_missing_stem_fails_open(tmp_path: Path) -> None:
    """If the stem path does not exist, the gate must fail open (return
    events unchanged) rather than dropping every event. We never want a
    missing stem to silently nuke the chart."""

    missing = tmp_path / "does-not-exist.wav"
    events = [_kick_event(0.0), _kick_event(1.0)]
    kept, meta = validate_drum_events_against_stem_silence(events, missing)
    assert kept == events
    assert meta["dropped"] == 0
    assert meta["stem_load_ok"] is False
    assert meta.get("skip_reason") == "stem_unavailable"


def test_none_stem_fails_open(tmp_path: Path) -> None:
    events = [_kick_event(0.0)]
    kept, meta = validate_drum_events_against_stem_silence(events, None)  # type: ignore[arg-type]
    assert kept == events
    assert meta["dropped"] == 0
    assert meta.get("skip_reason") == "stem_unavailable"


def test_unreadable_stem_fails_open(tmp_path: Path) -> None:
    """A file that exists but is not a valid WAV must not nuke the chart."""

    bad_path = tmp_path / "drums.wav"
    bad_path.write_bytes(b"NOT A WAV FILE")
    events = [_kick_event(0.0), _kick_event(1.0)]
    kept, meta = validate_drum_events_against_stem_silence(events, bad_path)
    assert kept == events
    assert meta["dropped"] == 0
    assert meta.get("skip_reason") == "stem_load_failed"


def test_completely_silent_stem_drops_everything_at_default_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A truly silent stem has no audio energy anywhere; the default gate
    drops every event. This is the dual of the 'silent-stem fallback'
    behavior in `combined_filter`: even when the engine emits a dense
    synthetic grid for a silent stem, the post-filter strips it back out."""

    monkeypatch.delenv(DRUM_SILENCE_GATE_ENV_DBFS, raising=False)
    monkeypatch.delenv(DRUM_SILENCE_GATE_ENV_DISABLED, raising=False)

    stem = tmp_path / "silent.wav"
    _write_stem_with_hits(stem, duration_sec=3.0, hit_times_sec=())

    events = [_kick_event(t) for t in (0.5, 1.0, 1.5, 2.0, 2.5)]
    kept, meta = validate_drum_events_against_stem_silence(events, stem)
    assert kept == []
    assert meta["events_in"] == 5
    assert meta["events_out"] == 0
    assert meta["dropped"] == 5


def test_explicit_kwarg_beats_env_dbfs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caller-provided kwarg overrides the env var (CLI > env precedence)."""

    monkeypatch.setenv(DRUM_SILENCE_GATE_ENV_DBFS, "-20")

    stem = tmp_path / "drums.wav"
    _write_stem_with_hits(stem, duration_sec=2.0, hit_times_sec=(1.0,))
    events = [_kick_event(1.0)]
    _, meta = validate_drum_events_against_stem_silence(events, stem, gate_dbfs=-60.0)
    assert meta["gate_dbfs"] == -60.0


def test_metadata_is_serializable_to_json(tmp_path: Path) -> None:
    """The metadata dict is meant for the songpack manifest, so it must
    round-trip through JSON without exotic types."""

    import json

    stem = tmp_path / "drums.wav"
    _write_stem_with_hits(stem, duration_sec=2.0, hit_times_sec=(1.0,))
    events = [_kick_event(0.1), _kick_event(1.0)]
    _, meta = validate_drum_events_against_stem_silence(events, stem)
    payload = json.dumps(meta)
    restored = json.loads(payload)
    assert restored["events_in"] == 2
    assert restored["gate_dbfs"] == DEFAULT_DRUM_SILENCE_GATE_DBFS
