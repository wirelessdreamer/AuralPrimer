"""Pin the 5-class taxonomy scaffolding from `_common.py`.

This is forward-looking scaffolding for path 1 of
`docs/research-deep-dive-adt-2026-05-07.md` (adopt 5-class as the
production default). No production path uses these helpers yet; the test
exists so the mapping does not silently drift before integration.
"""
from __future__ import annotations


def test_5class_vocabulary_matches_expected_size_and_order() -> None:
    from aural_ingest.algorithms._common import STANDARD_5CLASS_DRUM_VOCABULARY

    assert STANDARD_5CLASS_DRUM_VOCABULARY == (
        "kick",
        "snare",
        "hi_hat",
        "toms",
        "cymbals",
    )


def test_9class_to_5class_covers_every_internal_class() -> None:
    from aural_ingest.algorithms._common import (
        DRUM_9CLASS_TO_5CLASS,
        DRUM_CLASS_TO_MIDI,
        STANDARD_5CLASS_DRUM_VOCABULARY,
    )

    # Every 9-class internal name has a 5-class mapping.
    for name in DRUM_CLASS_TO_MIDI:
        assert name in DRUM_9CLASS_TO_5CLASS, f"missing 5-class mapping for {name}"
        assert DRUM_9CLASS_TO_5CLASS[name] in STANDARD_5CLASS_DRUM_VOCABULARY


def test_5class_midi_canonical_values() -> None:
    from aural_ingest.algorithms._common import DRUM_5CLASS_TO_MIDI

    assert DRUM_5CLASS_TO_MIDI == {
        "kick": 36,
        "snare": 38,
        "hi_hat": 42,
        "toms": 47,
        "cymbals": 49,
    }


def test_map_9class_drum_to_5class_grouping() -> None:
    from aural_ingest.algorithms._common import map_9class_drum_to_5class

    assert map_9class_drum_to_5class("kick") == "kick"
    assert map_9class_drum_to_5class("hh_closed") == "hi_hat"
    assert map_9class_drum_to_5class("hh_open") == "hi_hat"
    assert map_9class_drum_to_5class("crash") == "cymbals"
    assert map_9class_drum_to_5class("ride") == "cymbals"
    assert map_9class_drum_to_5class("tom_floor") == "toms"
    assert map_9class_drum_to_5class("tom_low") == "toms"
    assert map_9class_drum_to_5class("tom_high") == "toms"


def test_map_9class_drum_to_5class_unknown_passes_through() -> None:
    """Unknown classes pass through so callers can detect and report
    taxonomy mismatches rather than silently coercing them."""
    from aural_ingest.algorithms._common import map_9class_drum_to_5class

    assert map_9class_drum_to_5class("rim_shot") == "rim_shot"
    assert map_9class_drum_to_5class("") == ""


def test_map_midi_drum_to_5class_midi_known_values() -> None:
    from aural_ingest.algorithms._common import map_midi_drum_to_5class_midi

    # 9-class kick (36) -> 5-class kick (36)
    assert map_midi_drum_to_5class_midi(36) == 36
    # 9-class snare (38) -> 5-class snare (38)
    assert map_midi_drum_to_5class_midi(38) == 38
    # 9-class hh_closed (42) -> 5-class hi_hat (42)
    assert map_midi_drum_to_5class_midi(42) == 42
    # 9-class hh_open (46) -> 5-class hi_hat (42)
    assert map_midi_drum_to_5class_midi(46) == 42
    # 9-class crash (49) -> 5-class cymbals (49)
    assert map_midi_drum_to_5class_midi(49) == 49
    # 9-class ride (51) -> 5-class cymbals (49)
    assert map_midi_drum_to_5class_midi(51) == 49
    # 9-class tom_floor (41) -> 5-class toms (47)
    assert map_midi_drum_to_5class_midi(41) == 47
    # 9-class tom_high (50) -> 5-class toms (47)
    assert map_midi_drum_to_5class_midi(50) == 47


def test_map_midi_drum_to_5class_midi_unknown_returns_none() -> None:
    from aural_ingest.algorithms._common import map_midi_drum_to_5class_midi

    # Notes outside the 9-class mapping return None so callers can decide
    # whether to drop or pass through.
    assert map_midi_drum_to_5class_midi(40) is None  # electric snare
    assert map_midi_drum_to_5class_midi(60) is None  # outside drum range
    assert map_midi_drum_to_5class_midi(0) is None
