from aural_ingest.algorithms.piano_union import (
    UnionConfig,
    notes_from_rows,
    union_sources,
)
from aural_ingest.transcription import MelodicNote


def note(t_on: float, pitch: int, *, dur: float = 0.5) -> MelodicNote:
    return MelodicNote(t_on=t_on, t_off=t_on + dur, pitch=pitch, velocity=100, instrument="keys")


def test_union_collapses_the_same_note_seen_by_several_sources():
    result = union_sources({
        "primary": [note(1.000, 60)],
        "keys_only": [note(1.020, 60)],
        "keys_guitar": [note(1.045, 60)],
    })
    assert len(result.notes) == 1
    assert result.merged_count == 2
    assert set(result.sources_for((1.0, 60))) == {"primary", "keys_only", "keys_guitar"}


def test_union_keeps_genuinely_repeated_notes_apart():
    result = union_sources({"primary": [note(0.0, 60, dur=0.2), note(0.4, 60, dur=0.2)]})
    assert len(result.notes) == 2


def test_union_prefers_the_primary_timing():
    result = union_sources({
        "wide_mix": [note(1.000, 60)],
        "primary": [note(1.040, 60)],
    })
    assert [round(n.t_on, 3) for n in result.notes] == [1.04]


def test_union_takes_the_longest_release():
    result = union_sources({
        "primary": [note(0.0, 60, dur=0.2)],
        "wide_mix": [note(0.02, 60, dur=1.5)],
    })
    assert result.notes[0].t_off == 1.52


def test_union_caps_a_runaway_sustain():
    config = UnionConfig(max_duration_sec=2.0)
    result = union_sources({
        "primary": [note(0.0, 60, dur=0.2)],
        "wide_mix": [note(0.01, 60, dur=30.0)],
    }, config=config)
    assert result.notes[0].t_off == 2.0


def test_union_records_notes_only_one_source_found():
    result = union_sources({
        "primary": [note(0.0, 60)],
        "wide_mix": [note(0.0, 60), note(2.0, 67)],
    })
    assert result.sources_for((2.0, 67)) == ("wide_mix",)
    assert result.per_source_counts == {"primary": 1, "wide_mix": 2}


def test_notes_from_rows_skips_malformed_and_zero_length():
    rows = [
        {"t_on": 0.0, "t_off": 0.5, "pitch": 60},
        {"t_on": 1.0, "t_off": 1.0, "pitch": 62},   # zero length
        {"t_on": "x", "t_off": 2.0, "pitch": 64},   # unparseable
        {"t_off": 3.0, "pitch": 65},                 # missing t_on
    ]
    out = notes_from_rows(rows)
    assert [n.pitch for n in out] == [60]
