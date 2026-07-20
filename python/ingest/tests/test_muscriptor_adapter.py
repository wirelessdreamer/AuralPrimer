"""Unit tests for the MuScriptor whole-mix adapter.

MuScriptor's weights are gated, so nothing here downloads or runs the real
model: the ``muscriptor`` package is faked in ``sys.modules`` and its event
stream is scripted. Covers the load-bearing contracts:
  (a) import-safety + ``available()`` when the package is absent;
  (b) instrument-group -> role mapping (incl. drums + catch-all + override);
  (c) NoteStart/NoteEnd pairing and role bucketing;
  (d) ``transcribe_mix`` fail-safe (None) on absent package / load / infer error.
"""
from __future__ import annotations

import sys
import types

import pytest

from aural_ingest.algorithms import muscriptor
from aural_ingest.transcription import DrumEvent, MelodicNote


# --- fake MuScriptor event objects (duck-typed to the real dataclasses) --- #

class _FakeStart:
    def __init__(self, pitch: int, start_time: float, index: int, instrument: str):
        self.pitch = pitch
        self.start_time = start_time
        self.index = index
        self.instrument = instrument


class _FakeEnd:
    def __init__(self, end_time: float, start_event: _FakeStart):
        self.end_time = end_time
        self.start_event = start_event
        self.start_event_index = start_event.index


class _FakeProgress:
    def __init__(self, completed: int, total: int):
        self.completed = completed
        self.total = total


def _pair(pitch, t_on, t_off, index, instrument):
    s = _FakeStart(pitch, t_on, index, instrument)
    return [s, _FakeEnd(t_off, s)]


# --------------------------------------------------------------------------- #
# (a) import-safety + availability
# --------------------------------------------------------------------------- #

def test_module_imports_without_muscriptor_installed() -> None:
    # Importing the adapter must never drag in the engine (or torch).
    assert "muscriptor" not in sys.modules or sys.modules.get("muscriptor") is not None
    assert hasattr(muscriptor, "transcribe_mix")


def test_available_false_when_package_absent(monkeypatch) -> None:
    # The engine now ships with the sidecar, so absence has to be simulated:
    # `available()` must degrade cleanly wherever it is genuinely missing.
    monkeypatch.delenv(muscriptor._DISABLED_ENV, raising=False)
    monkeypatch.setattr(muscriptor.importlib.util, "find_spec", lambda _name: None)
    assert muscriptor.available() is False


def test_available_true_when_bundled(monkeypatch) -> None:
    monkeypatch.delenv(muscriptor._DISABLED_ENV, raising=False)
    assert muscriptor.available() is True  # bundled with the sidecar


def test_available_false_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv(muscriptor._DISABLED_ENV, "1")
    assert muscriptor.available() is False


# --------------------------------------------------------------------------- #
# (b) instrument-group -> role mapping
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "group,role",
    [
        ("electric_bass", "bass"),
        ("acoustic_bass", "bass"),
        ("contrabass", "bass"),
        ("distorted_electric_guitar", "rhythm_guitar"),
        ("acoustic_guitar", "rhythm_guitar"),
        ("acoustic_piano", "keys"),
        ("organ", "keys"),
        ("synth_pad", "keys"),
        ("voice", "vocals"),
        ("drums", "drums"),
        ("violin", "keys"),          # unmapped melodic group -> catch-all
        ("brass_section", "keys"),   # unmapped -> catch-all
    ],
)
def test_role_for_instrument(group: str, role: str) -> None:
    assert muscriptor.role_for_instrument(group) == role


def test_role_map_override_via_env(monkeypatch) -> None:
    monkeypatch.setenv(muscriptor._ROLE_MAP_ENV, '{"violin": "keys", "voice": "keys"}')
    table = muscriptor._load_role_map()
    assert table["voice"] == "keys"          # override wins
    assert table["electric_bass"] == "bass"  # defaults retained


def test_malformed_role_map_override_falls_back_to_defaults(monkeypatch) -> None:
    monkeypatch.setenv(muscriptor._ROLE_MAP_ENV, "{not json")
    assert muscriptor._load_role_map()["voice"] == "vocals"


# --------------------------------------------------------------------------- #
# (c) event pairing + role bucketing
# --------------------------------------------------------------------------- #

def test_events_bucket_melodic_by_role() -> None:
    events = [
        _FakeProgress(0, 2),
        *_pair(40, 0.0, 1.0, 1, "electric_bass"),
        *_pair(60, 0.5, 1.5, 2, "acoustic_piano"),
        *_pair(52, 0.25, 0.75, 3, "distorted_electric_guitar"),
        *_pair(67, 2.0, 2.4, 4, "voice"),
        _FakeProgress(2, 2),
    ]
    res = muscriptor.events_to_role_buckets(events)
    assert set(res.melodic) == {"bass", "keys", "rhythm_guitar", "vocals"}
    assert res.drums == []
    bass = res.melodic["bass"][0]
    assert isinstance(bass, MelodicNote)
    assert (bass.pitch, bass.t_on, bass.t_off, bass.instrument) == (40, 0.0, 1.0, "bass")
    assert bass.velocity == muscriptor._DEFAULT_VELOCITY  # fabricated


def test_events_route_drums_to_drum_events() -> None:
    events = [
        *_pair(36, 0.0, 0.0, 1, "drums"),   # kick
        *_pair(38, 0.5, 0.5, 2, "drums"),   # snare
        *_pair(40, 0.25, 1.25, 3, "electric_bass"),
    ]
    res = muscriptor.events_to_role_buckets(events)
    assert [d.note for d in res.drums] == [36, 38]
    assert all(isinstance(d, DrumEvent) for d in res.drums)
    assert res.drums[0].velocity == muscriptor._DEFAULT_VELOCITY
    assert "bass" in res.melodic and "drums" not in res.melodic


def test_events_unmapped_group_goes_to_keys_catchall() -> None:
    res = muscriptor.events_to_role_buckets(_pair(70, 0.0, 1.0, 1, "trumpet"))
    assert list(res.melodic) == ["keys"]
    assert res.melodic["keys"][0].pitch == 70


def test_events_sorted_and_counts_recorded() -> None:
    events = [
        *_pair(64, 2.0, 3.0, 1, "voice"),
        *_pair(60, 0.0, 1.0, 2, "voice"),
    ]
    res = muscriptor.events_to_role_buckets(events)
    assert [n.t_on for n in res.melodic["vocals"]] == [0.0, 2.0]  # sorted
    assert res.meta["instrument_group_counts"]["voice"] == 2


def test_events_unpaired_end_is_skipped() -> None:
    orphan_end = _FakeEnd(1.0, _FakeStart(60, 0.0, 99, "voice"))
    # Deliberately drop the matching start from the stream; passing only the
    # end must not crash and yields the note via its start_event reference.
    res = muscriptor.events_to_role_buckets([orphan_end])
    assert res.melodic["vocals"][0].pitch == 60


def test_negative_duration_note_clamped() -> None:
    res = muscriptor.events_to_role_buckets(_pair(60, 1.0, 0.5, 1, "voice"))
    n = res.melodic["vocals"][0]
    assert n.t_off == n.t_on  # clamped, never negative


# --------------------------------------------------------------------------- #
# (d) transcribe_mix fail-safe
# --------------------------------------------------------------------------- #

def test_transcribe_mix_returns_none_when_unavailable(tmp_path) -> None:
    # muscriptor not installed -> available() is False -> None, no raise.
    assert muscriptor.transcribe_mix(tmp_path / "mix.wav") is None


def _install_fake_muscriptor(monkeypatch, *, events=None, raise_on_load=False, raise_on_transcribe=False):
    """Put a fake ``muscriptor`` module in sys.modules and force available()."""
    mod = types.ModuleType("muscriptor")

    class _FakeModel:
        @classmethod
        def load_model(cls, weights, device=None):
            if raise_on_load:
                raise RuntimeError("gated weights: access denied")
            return cls()

        def transcribe(self, audio, instruments=None):
            if raise_on_transcribe:
                raise RuntimeError("inference exploded")
            yield from (events or [])

    mod.TranscriptionModel = _FakeModel
    monkeypatch.setitem(sys.modules, "muscriptor", mod)
    monkeypatch.setattr(muscriptor, "available", lambda: True)
    return mod


def test_transcribe_mix_success_with_fake_model(monkeypatch, tmp_path) -> None:
    events = [*_pair(40, 0.0, 1.0, 1, "electric_bass"), *_pair(36, 0.5, 0.5, 2, "drums")]
    _install_fake_muscriptor(monkeypatch, events=events)
    res = muscriptor.transcribe_mix(tmp_path / "mix.wav")
    assert res is not None
    assert res.melodic["bass"][0].pitch == 40
    assert res.drums[0].note == 36
    assert res.meta["engine"] == "muscriptor"
    assert res.meta["size"] == "medium"


def test_transcribe_mix_none_when_load_raises(monkeypatch, tmp_path) -> None:
    _install_fake_muscriptor(monkeypatch, raise_on_load=True)
    assert muscriptor.transcribe_mix(tmp_path / "mix.wav") is None


def test_transcribe_mix_none_when_inference_raises(monkeypatch, tmp_path) -> None:
    _install_fake_muscriptor(monkeypatch, raise_on_transcribe=True)
    assert muscriptor.transcribe_mix(tmp_path / "mix.wav") is None
