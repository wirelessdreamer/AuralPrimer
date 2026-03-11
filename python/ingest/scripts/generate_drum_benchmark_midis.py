from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "assets" / "test_fixtures" / "drum_benchmark_midis"
TPQ = 480
DRUM_CHANNEL = 9

NOTE_MAP = {
    "kick": 36,
    "snare": 38,
    "hi_hat": 42,
    "open_hat": 46,
    "crash": 49,
    "ride": 51,
    "tom1": 50,
    "tom2": 47,
    "tom3": 41,
}

NOTE_DURATION_TICKS = {
    "kick": 60,
    "snare": 72,
    "hi_hat": 40,
    "open_hat": 96,
    "crash": 140,
    "ride": 110,
    "tom1": 96,
    "tom2": 100,
    "tom3": 108,
}


@dataclass(frozen=True)
class Section:
    label: str
    bars: int
    numerator: int
    denominator: int = 4


@dataclass(frozen=True)
class Case:
    case_id: str
    title: str
    bpm: float
    sections: tuple[Section, ...]
    summary: str
    focus: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class DrumHit:
    tick: int
    lane: str
    velocity: int


CASES: tuple[Case, ...] = (
    Case(
        case_id="01_jrock_verse_chorus_160",
        title="J-Rock Verse/Chorus",
        bpm=160.0,
        sections=(Section("verse", 8, 4), Section("chorus", 8, 4)),
        summary="Straight driving hats, backbeat snare, crash-heavy chorus, and tom fills.",
        focus=("snare backbeats", "open hats", "crash downbeats", "tom fills"),
        tags=("jrock", "verse-chorus", "straight"),
    ),
    Case(
        case_id="02_jrock_gallop_172",
        title="J-Rock Gallop",
        bpm=172.0,
        sections=(Section("intro", 4, 4), Section("verse", 4, 4), Section("chorus", 4, 4)),
        summary="Syncopated gallop kicks against eighth-note hats with ride-led chorus accents.",
        focus=("syncopated kick", "snare placement", "ride vs crash"),
        tags=("jrock", "gallop", "syncopated"),
    ),
    Case(
        case_id="03_mathrock_7_8_138",
        title="Math Rock 7/8",
        bpm=138.0,
        sections=(Section("theme", 10, 7), Section("fill", 2, 7)),
        summary="Odd-meter groove with displaced snare accents and rotating tom answers.",
        focus=("odd meter", "snare displacement", "tom motion"),
        tags=("mathrock", "7-8", "odd-meter"),
    ),
    Case(
        case_id="04_mathrock_linear_5_4_122",
        title="Math Rock Linear 5/4",
        bpm=122.0,
        sections=(Section("main", 8, 5), Section("bridge", 4, 5)),
        summary="Linear groove with ride ostinato, ghosted snare pickups, and low-to-high tom runs.",
        focus=("linear groove", "ghost snares", "ride clarity", "tom sequencing"),
        tags=("mathrock", "5-4", "linear"),
    ),
    Case(
        case_id="05_metal_double_bass_190",
        title="Metal Double Bass",
        bpm=190.0,
        sections=(Section("riff_a", 8, 4), Section("riff_b", 8, 4)),
        summary="Continuous double-bass sixteenths with backbeat snare and cymbal changes.",
        focus=("double bass", "snare under density", "crash/ride splits"),
        tags=("metal", "double-bass", "dense"),
    ),
    Case(
        case_id="06_metal_blast_220",
        title="Metal Blast Beat",
        bpm=220.0,
        sections=(Section("blast", 8, 4), Section("ride_break", 4, 4)),
        summary="Alternating blast patterns with aggressive snare density and cymbal swaps.",
        focus=("blast beat", "snare density", "ride vs crash"),
        tags=("metal", "blast-beat", "extreme"),
    ),
    Case(
        case_id="07_funk_ghost_notes_102",
        title="Funk Ghost Notes",
        bpm=102.0,
        sections=(Section("groove", 8, 4), Section("turnaround", 4, 4)),
        summary="Ghost-note-heavy snare groove with hat openings and syncopated kick placements.",
        focus=("snare ghosts", "hat openings", "kick syncopation"),
        tags=("funk", "ghost-notes", "syncopated"),
    ),
    Case(
        case_id="08_blues_shuffle_92",
        title="Blues Shuffle",
        bpm=92.0,
        sections=(Section("shuffle", 8, 4), Section("turnaround", 4, 4)),
        summary="Swung blues groove with triplet shuffle hats, backbeat snare, and a turnaround fill.",
        focus=("shuffle feel", "swung hats", "snare backbeat", "turnaround toms"),
        tags=("blues", "shuffle", "triplet"),
    ),
    Case(
        case_id="09_pop_anthem_124",
        title="Pop Anthem",
        bpm=124.0,
        sections=(Section("verse", 8, 4), Section("prechorus", 4, 4), Section("chorus", 8, 4)),
        summary="Clean pop groove with tight backbeats, lift into open hats, and crash-heavy chorus downbeats.",
        focus=("tight snare", "open-hat lift", "chorus crashes", "simple fills"),
        tags=("pop", "anthem", "straight"),
    ),
    Case(
        case_id="10_rnb_pocket_88",
        title="R&B Pocket",
        bpm=88.0,
        sections=(Section("pocket", 8, 4), Section("breakdown", 4, 4), Section("hook", 4, 4)),
        summary="Laid-back half-time groove with ghosted snares, sparse kicks, and hat dynamics.",
        focus=("half-time snare", "ghost notes", "hat dynamics", "kick syncopation"),
        tags=("rnb", "pocket", "half-time"),
    ),
)


def _vlq(value: int) -> bytes:
    v = max(0, min(int(value), 0x0FFFFFFF))
    out = [v & 0x7F]
    v >>= 7
    while v > 0:
        out.append(0x80 | (v & 0x7F))
        v >>= 7
    out.reverse()
    return bytes(out)


def _meta_event(meta_type: int, payload: bytes) -> bytes:
    return bytes([0xFF, meta_type & 0x7F]) + _vlq(len(payload)) + payload


def _meta_text(meta_type: int, text: str) -> bytes:
    return _meta_event(meta_type, text.encode("utf-8"))


def _note_on(note: int, velocity: int) -> bytes:
    return bytes([0x90 | DRUM_CHANNEL, note & 0x7F, max(1, min(int(velocity), 127))])


def _note_off(note: int) -> bytes:
    return bytes([0x80 | DRUM_CHANNEL, note & 0x7F, 0])


def _track_chunk(events: list[tuple[int, bytes]]) -> bytes:
    ordered = sorted(events, key=lambda item: item[0])
    body = bytearray()
    last_tick = 0
    for tick, payload in ordered:
        safe_tick = max(last_tick, int(tick))
        body.extend(_vlq(safe_tick - last_tick))
        body.extend(payload)
        last_tick = safe_tick
    body.extend(b"\x00\xFF\x2F\x00")
    return b"MTrk" + len(body).to_bytes(4, "big") + bytes(body)


def _steps_to_ticks(step: float, ticks_per_bar: int, numerator: int) -> int:
    return int(round((step / float(numerator)) * float(ticks_per_bar)))


def _bar_ticks(numerator: int, denominator: int) -> int:
    return int(round(float(TPQ) * 4.0 * float(numerator) / float(denominator)))


def _add_hit(events: list[DrumHit], start_tick: int, ticks_per_bar: int, numerator: int, lane: str, step: float, velocity: int) -> None:
    events.append(DrumHit(tick=start_tick + _steps_to_ticks(step, ticks_per_bar, numerator), lane=lane, velocity=velocity))


def _jrock_verse(bar_start: int, ticks_per_bar: int, numerator: int, bar_index: int) -> list[DrumHit]:
    events: list[DrumHit] = []
    _add_hit(events, bar_start, ticks_per_bar, numerator, "crash" if bar_index == 0 else "hi_hat", 0.0, 118 if bar_index == 0 else 92)
    for step in range(numerator * 2):
        _add_hit(events, bar_start, ticks_per_bar, numerator, "hi_hat", step / 2.0, 86 if step % 2 == 0 else 74)
    for step in (0.0, 1.5, 2.0, 3.0):
        _add_hit(events, bar_start, ticks_per_bar, numerator, "kick", step, 106)
    for step in (1.0, 3.0):
        _add_hit(events, bar_start, ticks_per_bar, numerator, "snare", step, 112)
    if bar_index == 7:
        for lane, step, velocity in (
            ("tom3", 3.25, 100),
            ("tom2", 3.5, 104),
            ("tom1", 3.75, 108),
        ):
            _add_hit(events, bar_start, ticks_per_bar, numerator, lane, step, velocity)
    return events


def _jrock_chorus(bar_start: int, ticks_per_bar: int, numerator: int, bar_index: int) -> list[DrumHit]:
    events: list[DrumHit] = []
    for step in range(numerator):
        lane = "crash" if step == 0 else "open_hat" if step == numerator - 1 else "ride"
        velocity = 120 if lane == "crash" else 92 if lane == "ride" else 98
        _add_hit(events, bar_start, ticks_per_bar, numerator, lane, float(step), velocity)
    for step in (0.0, 0.75, 1.5, 2.0, 2.75, 3.25):
        _add_hit(events, bar_start, ticks_per_bar, numerator, "kick", step, 108)
    for step in (1.0, 3.0):
        _add_hit(events, bar_start, ticks_per_bar, numerator, "snare", step, 116)
    if bar_index in {3, 7}:
        for lane, step in (("tom1", 3.25), ("tom2", 3.5), ("tom3", 3.75)):
            _add_hit(events, bar_start, ticks_per_bar, numerator, lane, step, 110)
    return events


def _case_01(case: Case) -> list[DrumHit]:
    hits: list[DrumHit] = []
    current_tick = 0
    for section in case.sections:
        ticks_per_bar = _bar_ticks(section.numerator, section.denominator)
        for bar_index in range(section.bars):
            if section.label == "verse":
                hits.extend(_jrock_verse(current_tick, ticks_per_bar, section.numerator, bar_index))
            else:
                hits.extend(_jrock_chorus(current_tick, ticks_per_bar, section.numerator, bar_index))
            current_tick += ticks_per_bar
    return hits


def _case_02(case: Case) -> list[DrumHit]:
    hits: list[DrumHit] = []
    current_tick = 0
    for section in case.sections:
        ticks_per_bar = _bar_ticks(section.numerator, section.denominator)
        for bar_index in range(section.bars):
            for step in range(section.numerator * 2):
                lane = "hi_hat" if section.label != "chorus" else "ride"
                velocity = 86 if step % 2 == 0 else 74
                if section.label == "intro" and step == 0:
                    lane = "crash"
                    velocity = 120
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, lane, step / 2.0, velocity)
            kick_steps = (0.0, 0.75, 1.5, 2.0, 2.5, 3.25)
            if section.label == "chorus":
                kick_steps = (0.0, 0.75, 1.25, 2.0, 2.5, 3.0, 3.5)
            for step in kick_steps:
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, "kick", step, 110)
            for step, vel in ((1.0, 114), (3.0, 116)):
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, "snare", step, vel)
            if bar_index == section.bars - 1:
                for lane, step, velocity in (
                    ("tom1", 3.0, 102),
                    ("tom2", 3.25, 106),
                    ("tom3", 3.5, 110),
                    ("crash", 3.75, 120),
                ):
                    _add_hit(hits, current_tick, ticks_per_bar, section.numerator, lane, step, velocity)
            current_tick += ticks_per_bar
    return hits


def _case_03(case: Case) -> list[DrumHit]:
    hits: list[DrumHit] = []
    current_tick = 0
    accent_snare_steps = (
        (1.5, 112),
        (4.0, 114),
    )
    for section in case.sections:
        ticks_per_bar = _bar_ticks(section.numerator, section.denominator)
        for bar_index in range(section.bars):
            for step in range(section.numerator * 2):
                lane = "hi_hat" if step < 10 else "ride"
                velocity = 84 if step % 2 == 0 else 72
                if bar_index == 0 and step == 0:
                    lane = "crash"
                    velocity = 118
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, lane, step / 2.0, velocity)
            for step in (0.0, 1.0, 2.5, 3.5, 5.0, 6.0):
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, "kick", step, 104)
            for step, vel in accent_snare_steps:
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, "snare", step, vel)
            if bar_index % 2 == 1:
                for lane, step, velocity in (
                    ("tom1", 4.5, 100),
                    ("tom2", 5.25, 106),
                    ("tom3", 6.0, 110),
                ):
                    _add_hit(hits, current_tick, ticks_per_bar, section.numerator, lane, step, velocity)
            current_tick += ticks_per_bar
    return hits


def _case_04(case: Case) -> list[DrumHit]:
    hits: list[DrumHit] = []
    current_tick = 0
    for section in case.sections:
        ticks_per_bar = _bar_ticks(section.numerator, section.denominator)
        for bar_index in range(section.bars):
            for step in range(section.numerator):
                lane = "ride" if section.label == "bridge" else "hi_hat"
                velocity = 88 if step in {0, 3} else 76
                if step == 0 and bar_index in {0, section.bars - 1}:
                    lane = "crash"
                    velocity = 118
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, lane, float(step), velocity)
            for step in (0.0, 1.5, 2.75, 4.0):
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, "kick", step, 102)
            for step, velocity in ((1.0, 52), (2.0, 108), (3.25, 56), (4.0, 112)):
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, "snare", step, velocity)
            if bar_index % 3 == 2:
                for lane, step, velocity in (
                    ("tom3", 3.0, 98),
                    ("tom2", 3.5, 102),
                    ("tom1", 4.0, 106),
                ):
                    _add_hit(hits, current_tick, ticks_per_bar, section.numerator, lane, step, velocity)
            current_tick += ticks_per_bar
    return hits


def _case_05(case: Case) -> list[DrumHit]:
    hits: list[DrumHit] = []
    current_tick = 0
    for section in case.sections:
        ticks_per_bar = _bar_ticks(section.numerator, section.denominator)
        for bar_index in range(section.bars):
            cym_lane = "crash" if section.label == "riff_a" or bar_index == 0 else "ride"
            for step in range(section.numerator):
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, cym_lane, float(step), 102 if step == 0 else 88)
            for step in range(section.numerator * 4):
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, "kick", step / 4.0, 98 if step % 4 else 110)
            for step in (1.0, 3.0):
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, "snare", step, 118)
            if section.label == "riff_b" and bar_index in {3, 7}:
                for lane, step, velocity in (
                    ("tom1", 2.5, 102),
                    ("tom2", 2.75, 106),
                    ("tom3", 3.0, 112),
                    ("crash", 3.5, 120),
                ):
                    _add_hit(hits, current_tick, ticks_per_bar, section.numerator, lane, step, velocity)
            current_tick += ticks_per_bar
    return hits


def _case_06(case: Case) -> list[DrumHit]:
    hits: list[DrumHit] = []
    current_tick = 0
    for section in case.sections:
        ticks_per_bar = _bar_ticks(section.numerator, section.denominator)
        for bar_index in range(section.bars):
            for step in range(section.numerator * 4):
                quarter = step / 4.0
                if section.label == "blast":
                    cym_lane = "crash" if step % 4 == 0 else "hi_hat"
                    snare_on = step % 2 == 0
                    kick_on = step % 2 == 1
                else:
                    cym_lane = "ride"
                    snare_on = step in {4, 12}
                    kick_on = step % 2 == 0
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, cym_lane, quarter, 90 if step % 4 else 112)
                if snare_on:
                    _add_hit(hits, current_tick, ticks_per_bar, section.numerator, "snare", quarter, 110 if step % 4 else 118)
                if kick_on:
                    _add_hit(hits, current_tick, ticks_per_bar, section.numerator, "kick", quarter, 104)
            if bar_index == section.bars - 1:
                for lane, step, velocity in (
                    ("tom1", 3.0, 102),
                    ("tom2", 3.25, 108),
                    ("tom3", 3.5, 114),
                    ("crash", 3.75, 122),
                ):
                    _add_hit(hits, current_tick, ticks_per_bar, section.numerator, lane, step, velocity)
            current_tick += ticks_per_bar
    return hits


def _case_07(case: Case) -> list[DrumHit]:
    hits: list[DrumHit] = []
    current_tick = 0
    for section in case.sections:
        ticks_per_bar = _bar_ticks(section.numerator, section.denominator)
        for bar_index in range(section.bars):
            for step in range(section.numerator * 4):
                lane = "hi_hat"
                velocity = 84 if step % 4 == 0 else 68
                if step in {7, 15}:
                    lane = "open_hat"
                    velocity = 92
                if bar_index == 0 and step == 0:
                    lane = "crash"
                    velocity = 116
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, lane, step / 4.0, velocity)
            for step in (0.0, 0.75, 1.5, 2.5, 3.25):
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, "kick", step, 98 if step != 0.0 else 110)
            for step, velocity in (
                (0.75, 44),
                (1.0, 110),
                (1.75, 48),
                (2.75, 46),
                (3.0, 114),
            ):
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, "snare", step, velocity)
            if section.label == "turnaround" and bar_index == section.bars - 1:
                for lane, step, velocity in (
                    ("tom1", 2.75, 96),
                    ("tom2", 3.0, 100),
                    ("tom3", 3.25, 106),
                    ("crash", 3.5, 118),
                ):
                    _add_hit(hits, current_tick, ticks_per_bar, section.numerator, lane, step, velocity)
            current_tick += ticks_per_bar
    return hits


def _case_08(case: Case) -> list[DrumHit]:
    hits: list[DrumHit] = []
    current_tick = 0
    for section in case.sections:
        ticks_per_bar = _bar_ticks(section.numerator, section.denominator)
        for bar_index in range(section.bars):
            for beat in range(section.numerator):
                for offset, velocity in ((0.0, 84), (2.0 / 3.0, 70)):
                    lane = "ride"
                    if bar_index == 0 and beat == 0 and offset == 0.0:
                        lane = "crash"
                        velocity = 116
                    _add_hit(
                        hits,
                        current_tick,
                        ticks_per_bar,
                        section.numerator,
                        lane,
                        float(beat) + offset,
                        velocity,
                    )
            for step in (0.0, 1.5, 2.0, 3.25):
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, "kick", step, 94 if step != 0.0 else 106)
            for step in (1.0, 3.0):
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, "snare", step, 110)
            if section.label == "turnaround" and bar_index == section.bars - 1:
                for lane, step, velocity in (
                    ("tom1", 2.75, 96),
                    ("tom2", 3.0, 102),
                    ("tom3", 3.333, 108),
                    ("crash", 3.666, 118),
                ):
                    _add_hit(hits, current_tick, ticks_per_bar, section.numerator, lane, step, velocity)
            current_tick += ticks_per_bar
    return hits


def _case_09(case: Case) -> list[DrumHit]:
    hits: list[DrumHit] = []
    current_tick = 0
    for section in case.sections:
        ticks_per_bar = _bar_ticks(section.numerator, section.denominator)
        for bar_index in range(section.bars):
            if section.label == "chorus":
                cym_lane = "crash"
            elif section.label == "prechorus":
                cym_lane = "open_hat"
            else:
                cym_lane = "hi_hat"
            for step in range(section.numerator * 2):
                lane = cym_lane
                velocity = 88 if step % 2 == 0 else 76
                if section.label == "chorus" and step not in {0, 4}:
                    lane = "ride"
                    velocity = 82
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, lane, step / 2.0, velocity)
            kick_steps = (0.0, 1.5, 2.0, 3.0)
            if section.label == "prechorus":
                kick_steps = (0.0, 1.0, 1.5, 2.5, 3.25)
            elif section.label == "chorus":
                kick_steps = (0.0, 1.0, 1.5, 2.0, 3.0, 3.5)
            for step in kick_steps:
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, "kick", step, 104)
            for step in (1.0, 3.0):
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, "snare", step, 114)
            if bar_index == section.bars - 1:
                for lane, step, velocity in (
                    ("tom1", 3.0, 100),
                    ("tom2", 3.25, 104),
                    ("tom3", 3.5, 108),
                    ("crash", 3.75, 120),
                ):
                    _add_hit(hits, current_tick, ticks_per_bar, section.numerator, lane, step, velocity)
            current_tick += ticks_per_bar
    return hits


def _case_10(case: Case) -> list[DrumHit]:
    hits: list[DrumHit] = []
    current_tick = 0
    for section in case.sections:
        ticks_per_bar = _bar_ticks(section.numerator, section.denominator)
        for bar_index in range(section.bars):
            for step in range(section.numerator * 4):
                lane = "hi_hat"
                velocity = 78 if step % 4 == 0 else 58
                if section.label == "hook" and step in {7, 15}:
                    lane = "open_hat"
                    velocity = 88
                if bar_index == 0 and step == 0:
                    lane = "crash"
                    velocity = 112
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, lane, step / 4.0, velocity)
            kick_steps = (0.0, 1.75, 2.5, 3.25)
            if section.label == "breakdown":
                kick_steps = (0.0, 2.75)
            for step in kick_steps:
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, "kick", step, 96 if step != 0.0 else 108)
            for step, velocity in (
                (1.5, 42),
                (2.0, 48),
                (2.75, 112),
                (3.5, 46),
            ):
                _add_hit(hits, current_tick, ticks_per_bar, section.numerator, "snare", step, velocity)
            if section.label == "hook" and bar_index == section.bars - 1:
                for lane, step, velocity in (
                    ("tom1", 2.75, 94),
                    ("tom2", 3.0, 100),
                    ("tom3", 3.25, 106),
                    ("crash", 3.5, 116),
                ):
                    _add_hit(hits, current_tick, ticks_per_bar, section.numerator, lane, step, velocity)
            current_tick += ticks_per_bar
    return hits


CASE_BUILDERS = {
    "01_jrock_verse_chorus_160": _case_01,
    "02_jrock_gallop_172": _case_02,
    "03_mathrock_7_8_138": _case_03,
    "04_mathrock_linear_5_4_122": _case_04,
    "05_metal_double_bass_190": _case_05,
    "06_metal_blast_220": _case_06,
    "07_funk_ghost_notes_102": _case_07,
    "08_blues_shuffle_92": _case_08,
    "09_pop_anthem_124": _case_09,
    "10_rnb_pocket_88": _case_10,
}


def _hits_to_track_events(hits: list[DrumHit]) -> list[tuple[int, bytes]]:
    events: list[tuple[int, bytes]] = [(0, _meta_text(0x03, "Drums"))]
    for hit in sorted(hits, key=lambda item: (item.tick, item.lane, item.velocity)):
        note = NOTE_MAP[hit.lane]
        duration = NOTE_DURATION_TICKS[hit.lane]
        events.append((hit.tick, _note_on(note, hit.velocity)))
        events.append((hit.tick + duration, _note_off(note)))
    return events


def _conductor_track(case: Case) -> list[tuple[int, bytes]]:
    events: list[tuple[int, bytes]] = [(0, _meta_text(0x03, "Conductor"))]
    tempo_us_per_quarter = int(round(60_000_000.0 / float(case.bpm)))
    events.append((0, _meta_event(0x51, tempo_us_per_quarter.to_bytes(3, "big"))))

    current_tick = 0
    for section in case.sections:
        denominator_power = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4}.get(section.denominator, 2)
        events.append((current_tick, _meta_event(0x58, bytes([section.numerator, denominator_power, 24, 8]))))
        events.append((current_tick, _meta_text(0x06, f"SECTION:{section.label}")))
        current_tick += _bar_ticks(section.numerator, section.denominator) * section.bars
    return events


def _total_ticks(case: Case) -> int:
    return sum(_bar_ticks(section.numerator, section.denominator) * section.bars for section in case.sections)


def _write_midi(case: Case, hits: list[DrumHit]) -> bytes:
    tracks = [
        _conductor_track(case),
        _hits_to_track_events(hits),
    ]
    header = (
        b"MThd"
        + (6).to_bytes(4, "big")
        + (1).to_bytes(2, "big")
        + len(tracks).to_bytes(2, "big")
        + TPQ.to_bytes(2, "big")
    )
    return header + b"".join(_track_chunk(track) for track in tracks)


def _lane_set(hits: list[DrumHit]) -> list[str]:
    order = ["kick", "snare", "hi_hat", "open_hat", "crash", "ride", "tom1", "tom2", "tom3"]
    used = {hit.lane for hit in hits}
    return [lane for lane in order if lane in used]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    expected_mid_names = {f"{case.case_id}.mid" for case in CASES}
    for midi_path in OUT_DIR.glob("*.mid"):
        if midi_path.name not in expected_mid_names:
            midi_path.unlink()

    manifest_cases = []

    for case in CASES:
        hits = CASE_BUILDERS[case.case_id](case)
        midi_path = OUT_DIR / f"{case.case_id}.mid"
        midi_path.write_bytes(_write_midi(case, hits))

        duration_sec = (_total_ticks(case) / float(TPQ)) * (60.0 / float(case.bpm))
        manifest_cases.append(
            {
                "id": case.case_id,
                "title": case.title,
                "midi_path": midi_path.name,
                "bpm": case.bpm,
                "sections": [
                    {
                        "label": section.label,
                        "bars": section.bars,
                        "numerator": section.numerator,
                        "denominator": section.denominator,
                    }
                    for section in case.sections
                ],
                "duration_sec": round(duration_sec, 3),
                "lane_set": _lane_set(hits),
                "focus": list(case.focus),
                "tags": list(case.tags),
                "summary": case.summary,
                "event_count": len(hits),
            }
        )

    manifest = {
        "format": "auralprimer_drum_benchmark_manifest_v1",
        "ticks_per_quarter": TPQ,
        "cases": manifest_cases,
    }
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
