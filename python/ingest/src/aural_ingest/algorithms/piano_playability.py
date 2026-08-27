"""Reduce a transcribed keys part to what two human hands can play.

This is an *arranging* pass over existing note data, not a transcription
claim. It never re-runs an engine and never degrades the audio path; it
takes ``list[MelodicNote]`` in and gives ``list[MelodicNote]`` out.

It is a **salience-ranked reduction**, not a filter. When a moment holds
more notes than a player has fingers, what survives is decided by musical
importance — motif, melody, metrical accent, audio support, corroboration
between sources — never by what happens to be convenient to reach.

What it is fixing
-----------------
Whole-mix engines emit keys parts nobody can play: on
``center_muscriptor`` 39% of attack groups need four or more keys at once,
groups run to 13 notes, and 58% of sounding time sits at six or more
simultaneous notes. Real piano writing, measured on the piano-midi.de
packs in the same library, sits at 3-6% of groups with four or more notes
and 0.5-2.5% of sounding time above six.

The intended input is denser still: a union of several transcriptions of
the same part (see :mod:`aural_ingest.algorithms.piano_union`), run over
different source mixes to recover what any single pass missed. On What A
God that union is 7661 notes, 17.8 a second, against 4.8 a second in the
shipped chart.

Five failure modes, five repairs
--------------------------------
* **Overtone shadows.** A partial of a real note transcribed as its own
  note — an octave / twelfth / double-octave / seventeenth above a note
  sounding at the same instant. Velocity cannot find these (MuScriptor
  emits a constant velocity for every note), so we ask the audio instead
  (:mod:`aural_ingest.algorithms.piano_evidence`) and drop only when the
  structural signature *and* the missing audio evidence agree.
* **Sustain pile-up.** Long tails stack until a dozen notes are nominally
  held. A pianist pedals through that, so the repair is to trim the tail,
  not delete the note.
* **Too much part.** Hand feasibility bounds one attack; it does not bound
  how many attacks there are. :func:`enforce_density_budget` is the knob
  that answers "how much part is there", spending its cut on the least
  salient notes, per window so a dense chorus cannot leave the intro
  thinner than it started.
* **Over-full attacks.** More keys at one instant, or a wider span, than
  two hands reach. :func:`reduce_group_to_playable` takes notes in
  descending salience and keeps each one only if the running set still
  divides between two hands.
* **Losing the figure.** A recurring motif looks like redundancy to every
  density heuristic — the same handful of pitches over and over — so dedup
  rules and doubling penalties all eat it preferentially and it survives
  as a smear. :func:`find_motifs` locates it and the salience model makes
  it the first thing kept; :func:`make_playable` reports whether every
  motif note survived as a pass/fail gate.

The hand model is the hard constraint: notes at one attack must divide
into two non-crossing hands, each spanning no more than
:attr:`PlayabilityConfig.max_hand_span` and using no more than
:attr:`PlayabilityConfig.max_notes_per_hand` fingers. Measured: at four
fingers a hand the motif gate passes; at two it fails, whatever the
weights.

Each stage is a pure function with its own gate, matching the discipline
of :mod:`aural_ingest.algorithms.piano_cleanup` and
:mod:`aural_ingest.algorithms.guitar_cleanup`. :func:`make_playable`
composes them and returns a report alongside the notes.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from aural_ingest.transcription import MelodicNote


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlayabilityConfig:
    """Tunable limits for the playability pass.

    Span defaults come from what a hand actually reaches: an octave (12)
    is comfortable for most adults, a ninth (14) is reliable, a tenth (16)
    is a stretch. We allow a ninth per hand and let the *pair* of hands
    cover the rest.
    """

    # Attack grouping
    onset_window_sec: float = 0.05

    # Hand model
    max_hand_span: int = 14          # a major ninth
    max_notes_per_hand: int = 4

    # Density budget. The hand model alone does not bound density: four notes
    # an attack is legal, and a union of several transcriptions can be legal
    # at every attack and still be three times denser than any piano part
    # ever written. Measured references, notes per second of keys: the packs
    # the user calls good sit at 4.6-4.9, the piano-midi.de originals at
    # 3.6-5.7, an ungated five-source union at 15.3. ``None`` disables it.
    target_density_per_sec: float | None = None
    # Applied per window rather than over the whole track: a global budget
    # lets a dense chorus spend the intro's allowance and leaves the intro
    # thinner than the transcription it started from.
    density_window_sec: float = 10.0
    # Notes the primary source already had are exempt from the density cut.
    # The primary is the reading the user says sounds like the song, so the
    # budget spends supplementary notes only, and no section can end up
    # sparser than what shipped.
    density_protects_primary: bool = True

    # Sustain / pedal model. Five is the knee, measured: on the What A God
    # union, capping held notes at 8 leaves 17.5% of sounding time at six or
    # more voices, 6 leaves 14.6%, and 5 leaves 3.2%. Below 5 buys nothing
    # and only shortens more notes. Nothing is deleted at any setting.
    max_sustained: int = 5
    min_note_sec: float = 0.06
    trim_guard_sec: float = 0.01

    # Overtone-shadow cull
    shadow_intervals: tuple[int, ...] = (7, 12, 19, 24, 28, 31, 34, 36)
    shadow_evidence_floor: float = 0.02
    shadow_min_group: int = 3
    # If most of a group looks unsupported, the audio sync is wrong, not the
    # notes. Measured: packs whose rendered mix drifts against their MIDI lose
    # 14-17% of real notes to an ungated cull.
    shadow_min_supported_ratio: float = 0.5
    # Only cull inside groups that are actually crowded. A three-note chord
    # with an octave doubling is playable as written, and an octave doubling
    # is spectrally identical to an octave overtone — every partial of the
    # upper note is also a partial of the lower one — so the fit cannot tell
    # them apart and would systematically thin real octave writing. Set False
    # to trade musical content for a thinner chart.
    shadow_only_when_crowded: bool = True

    # Melody line (voice-leading tracker)
    melody_candidates: int = 3
    melody_leap_penalty: float = 2.5
    melody_free_step: int = 4        # up to a major third costs nothing
    melody_top_bonus: float = 6.0
    melody_evidence_weight: float = 30.0   # must outweigh an octave of height
    melody_break_sec: float = 1.5    # longer rest ends the phrase


DEFAULT_PLAYABILITY_CONFIG = PlayabilityConfig()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def note_key(note: MelodicNote) -> tuple[float, int]:
    """Stable identity for a note across the pass.

    Not ``id()``: every stage rebuilds notes with :func:`dataclasses.replace`,
    so object identity dies at the first stage boundary. Onset and pitch do
    not change once :func:`normalize_notes` has run, which is what makes them
    safe to key an evidence map on.
    """
    return (round(float(note.t_on), 4), int(note.pitch))


def _duration(note: MelodicNote) -> float:
    return max(0.0, float(note.t_off) - float(note.t_on))


def _sorted_notes(notes: Sequence[MelodicNote]) -> list[MelodicNote]:
    return sorted(notes, key=lambda n: (float(n.t_on), int(n.pitch), float(n.t_off)))


def group_by_onset(
    notes: Sequence[MelodicNote],
    *,
    window_sec: float = DEFAULT_PLAYABILITY_CONFIG.onset_window_sec,
) -> list[list[MelodicNote]]:
    """Chain notes whose consecutive onsets fall inside ``window_sec``.

    Matches the grouping the project already uses to count "simultaneous
    keys", so before/after numbers are comparable.
    """
    groups: list[list[MelodicNote]] = []
    current: list[MelodicNote] = []
    for note in _sorted_notes(notes):
        if current and float(note.t_on) - float(current[-1].t_on) < window_sec:
            current.append(note)
        else:
            if current:
                groups.append(current)
            current = [note]
    if current:
        groups.append(current)
    return groups


def max_polyphony(notes: Sequence[MelodicNote]) -> int:
    events: list[tuple[float, int]] = []
    for note in notes:
        if _duration(note) <= 0.0:
            continue
        events.append((float(note.t_on), 1))
        events.append((float(note.t_off), -1))
    events.sort()
    current = peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


# ---------------------------------------------------------------------------
# Hand model
# ---------------------------------------------------------------------------


def hand_split(
    pitches: Sequence[int],
    *,
    config: PlayabilityConfig = DEFAULT_PLAYABILITY_CONFIG,
) -> tuple[list[int], list[int]] | None:
    """Divide simultaneous pitches between two non-crossing hands.

    Returns ``(left, right)`` for the first feasible split, or ``None`` if
    no split satisfies the span and finger limits. Splits are tried at the
    widest interior gap first, which is where a pianist would naturally
    put the break.
    """
    ordered = sorted(int(p) for p in pitches)
    if not ordered:
        return [], []
    if len(ordered) > 2 * config.max_notes_per_hand:
        return None

    def ok(hand: list[int]) -> bool:
        if not hand:
            return True
        if len(hand) > config.max_notes_per_hand:
            return False
        return (hand[-1] - hand[0]) <= config.max_hand_span

    # Prefer a break at the largest gap, then any other break.
    candidates = list(range(len(ordered) + 1))
    gaps = {k: (ordered[k] - ordered[k - 1]) for k in range(1, len(ordered))}
    candidates.sort(key=lambda k: -gaps.get(k, 0))
    for k in candidates:
        left, right = ordered[:k], ordered[k:]
        if ok(left) and ok(right):
            return left, right
    return None


def is_hand_feasible(
    pitches: Sequence[int],
    *,
    config: PlayabilityConfig = DEFAULT_PLAYABILITY_CONFIG,
) -> bool:
    return hand_split(pitches, config=config) is not None


# ---------------------------------------------------------------------------
# Stage 1: normalise
# ---------------------------------------------------------------------------


def normalize_notes(
    notes: Sequence[MelodicNote],
    *,
    min_pitch: int = 21,
    max_pitch: int = 108,
) -> list[MelodicNote]:
    """Clamp to the keyboard, drop zero-length notes, merge same-pitch overlaps.

    Same-pitch overlap is physically impossible — one key, one finger — and
    shows up in every whole-mix engine output we have (61-117 pairs per
    pack). The later note wins its onset; the earlier one is trimmed.
    """
    cleaned: list[MelodicNote] = []
    for note in _sorted_notes(notes):
        if _duration(note) <= 0.0:
            continue
        pitch = max(min_pitch, min(max_pitch, int(note.pitch)))
        cleaned.append(replace(note, pitch=pitch, velocity=max(1, min(127, int(note.velocity)))))

    by_pitch: dict[int, list[MelodicNote]] = {}
    for note in cleaned:
        by_pitch.setdefault(int(note.pitch), []).append(note)

    out: list[MelodicNote] = []
    for pitch, seq in by_pitch.items():
        seq.sort(key=lambda n: float(n.t_on))
        for index, note in enumerate(seq):
            end = float(note.t_off)
            if index + 1 < len(seq):
                end = min(end, float(seq[index + 1].t_on))
            if end - float(note.t_on) <= 0.0:
                continue
            out.append(replace(note, t_off=end))
    return _sorted_notes(out)


# ---------------------------------------------------------------------------
# Stage 2: the melody line
# ---------------------------------------------------------------------------


def melody_line(
    notes: Sequence[MelodicNote],
    *,
    evidence: Mapping[tuple[float, int], float] | None = None,
    config: PlayabilityConfig = DEFAULT_PLAYABILITY_CONFIG,
) -> set[tuple[float, int]]:
    """Pick one melody note per attack group, by voice-leading continuity.

    "The highest note" is the usual proxy for the melody, and it is wrong
    exactly where it matters: an overtone shadow sitting an octave above
    the tune *is* the highest note, so protecting the top note protects the
    artifact and leaves the tune to be culled. A real melody moves by step;
    a shadow jumps by a fixed interval and leaves the previous line
    hanging. So this walks the groups with a small dynamic program that
    trades "high and audio-supported" against "close to the note before
    it", and the winner is protected by every later stage.

    Returns the set of :func:`note_key` values on the line.
    """
    groups = group_by_onset(notes, window_sec=config.onset_window_sec)
    if not groups:
        return set()

    def local(note: MelodicNote, top: int) -> float:
        score = float(int(note.pitch))
        if evidence is not None:
            score += config.melody_evidence_weight * float(
                evidence.get(note_key(note), 1.0)
            )
        if int(note.pitch) == top:
            score += config.melody_top_bonus
        return score

    # Candidates: the highest few notes of each group. The melody is
    # essentially never buried below the top three voices.
    candidates: list[list[MelodicNote]] = []
    for group in groups:
        ranked = sorted(group, key=lambda n: -int(n.pitch))
        candidates.append(ranked[: config.melody_candidates])

    best: list[dict[int, tuple[float, int | None]]] = []
    for index, options in enumerate(candidates):
        top = max(int(n.pitch) for n in groups[index])
        table: dict[int, tuple[float, int | None]] = {}
        gap = 0.0
        if index:
            gap = float(options[0].t_on) - float(candidates[index - 1][0].t_on)
        for pos, note in enumerate(options):
            score = local(note, top)
            back: int | None = None
            if index and gap <= config.melody_break_sec:
                choice = max(
                    range(len(candidates[index - 1])),
                    key=lambda prev: best[index - 1][prev][0]
                    - config.melody_leap_penalty
                    * max(
                        0,
                        abs(int(note.pitch) - int(candidates[index - 1][prev].pitch))
                        - config.melody_free_step,
                    ),
                )
                prev_note = candidates[index - 1][choice]
                score += best[index - 1][choice][0] - config.melody_leap_penalty * max(
                    0,
                    abs(int(note.pitch) - int(prev_note.pitch)) - config.melody_free_step,
                )
                back = choice
            elif index:
                score += max(v[0] for v in best[index - 1].values())
            table[pos] = (score, back)
        best.append(table)

    line: set[tuple[float, int]] = set()
    pos = max(best[-1], key=lambda k: best[-1][k][0])
    for index in range(len(candidates) - 1, -1, -1):
        note = candidates[index][pos]
        line.add(note_key(note))
        back = best[index][pos][1]
        if back is None:
            if index:
                pos = max(best[index - 1], key=lambda k: best[index - 1][k][0])
            continue
        pos = back
    return line


# ---------------------------------------------------------------------------
# Stage 3: overtone-shadow cull
# ---------------------------------------------------------------------------


def _shadow_partners(pitch: int, others: Sequence[int], config: PlayabilityConfig) -> bool:
    intervals = set(int(i) for i in config.shadow_intervals)
    return any((pitch - int(other)) in intervals for other in others if int(other) < pitch)


def prune_overtone_shadows(
    notes: Sequence[MelodicNote],
    *,
    evidence: Mapping[tuple[float, int], float] | None,
    melody: set[tuple[float, int]] | None = None,
    config: PlayabilityConfig = DEFAULT_PLAYABILITY_CONFIG,
) -> tuple[list[MelodicNote], int]:
    """Drop notes that are both a harmonic shadow *and* unsupported by audio.

    ``evidence`` maps :func:`note_key` to a relative fitted amplitude in
    ``[0, 1]`` (see :mod:`aural_ingest.algorithms.piano_evidence`). Without
    it the pass is a no-op: on ground-truth piano packs the structural
    signature alone flags 18-43% of *real* notes, so it must never fire on
    its own.

    Two guards keep a bad audio sync from eating the music. A group where
    most notes look unsupported is a group where the *evidence* is wrong,
    not the notes — the cull skips it. And the melody line and the lowest
    supported note are never culled.
    """
    if not evidence:
        return list(notes), 0

    melody = melody or set()
    removed = 0
    keep: list[MelodicNote] = []
    for group in group_by_onset(notes, window_sec=config.onset_window_sec):
        if len(group) < config.shadow_min_group:
            keep.extend(group)
            continue
        pitches = [int(n.pitch) for n in group]
        if config.shadow_only_when_crowded and len(group) <= config.max_notes_per_hand:
            if is_hand_feasible(pitches, config=config):
                keep.extend(group)
                continue
        supported = [
            int(n.pitch)
            for n in group
            if evidence.get(note_key(n), 1.0) >= config.shadow_evidence_floor
        ]
        if len(supported) < config.shadow_min_supported_ratio * len(group):
            # The fit does not explain this moment; distrust it entirely.
            keep.extend(group)
            continue
        protect = {min(supported)} if supported else set()
        for note in group:
            if note_key(note) in melody or int(note.pitch) in protect:
                keep.append(note)
                continue
            weak = evidence.get(note_key(note), 1.0) < config.shadow_evidence_floor
            if weak and _shadow_partners(
                int(note.pitch), [p for p in pitches if p != int(note.pitch)], config
            ):
                removed += 1
                continue
            keep.append(note)
    return _sorted_notes(keep), removed


# ---------------------------------------------------------------------------
# Stage 3: sustain trim (pedal model)
# ---------------------------------------------------------------------------


def trim_sustain(
    notes: Sequence[MelodicNote],
    *,
    config: PlayabilityConfig = DEFAULT_PLAYABILITY_CONFIG,
) -> tuple[list[MelodicNote], int]:
    """Cap how many notes are held at once by shortening tails, not deleting.

    When a new attack pushes the held count over ``max_sustained``, the
    oldest still-sounding notes are released at that attack. A pianist does
    the same thing and lets the pedal carry the sound.
    """
    ordered = _sorted_notes(notes)
    ends = {id(n): float(n.t_off) for n in ordered}
    trimmed = 0
    onsets = sorted({float(n.t_on) for n in ordered})
    active: list[MelodicNote] = []
    index = 0
    for t in onsets:
        while index < len(ordered) and float(ordered[index].t_on) <= t + 1e-9:
            active.append(ordered[index])
            index += 1
        active = [n for n in active if ends[id(n)] > t + 1e-9]
        if len(active) <= config.max_sustained:
            continue
        # Release oldest-started notes first, but never one that just started.
        stale = sorted(
            (n for n in active if float(n.t_on) < t - 1e-9),
            key=lambda n: float(n.t_on),
        )
        overflow = len(active) - config.max_sustained
        for note in stale[:overflow]:
            new_end = max(float(note.t_on) + config.min_note_sec, t - config.trim_guard_sec)
            if new_end < ends[id(note)] - 1e-6:
                ends[id(note)] = new_end
                trimmed += 1
        active = [n for n in active if ends[id(n)] > t + 1e-9]

    out = [replace(n, t_off=ends[id(n)]) for n in ordered if ends[id(n)] > float(n.t_on)]
    return _sorted_notes(out), trimmed


# ---------------------------------------------------------------------------
# Stage 4: capacity-driven reduction
# ---------------------------------------------------------------------------


_TRIAD = (0, 4, 7)
_QUALITY_INTERVALS: dict[str, tuple[int, ...]] = {
    "maj": (0, 4, 7),
    "min": (0, 3, 7),
    "dim": (0, 3, 6),
    "aug": (0, 4, 8),
    "maj7": (0, 4, 7, 11),
    "min7": (0, 3, 7, 10),
    "dom7": (0, 4, 7, 10),
    "7": (0, 4, 7, 10),
    "min7b5": (0, 3, 6, 10),
    "dim7": (0, 3, 6, 9),
    "sus2": (0, 2, 7),
    "sus4": (0, 5, 7),
    "maj6": (0, 4, 7, 9),
    "min6": (0, 3, 7, 9),
    "maj9": (0, 4, 7, 11, 2),
    "min9": (0, 3, 7, 10, 2),
    "dom9": (0, 4, 7, 10, 2),
}
_PITCH_CLASS = {
    "C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "FB": 4,
    "F": 5, "E#": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9,
    "A#": 10, "BB": 10, "B": 11, "CB": 11,
}
_MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
_MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)


def chord_pitch_classes(root: str, quality: str) -> frozenset[int]:
    """Pitch-class set for a ``harmony.json`` chord event."""
    base = _PITCH_CLASS.get(str(root).strip().upper())
    if base is None:
        return frozenset()
    intervals = _QUALITY_INTERVALS.get(str(quality).strip().lower(), _TRIAD)
    return frozenset((base + i) % 12 for i in intervals)


def key_pitch_classes(key: str, mode: str) -> frozenset[int]:
    base = _PITCH_CLASS.get(str(key).strip().upper())
    if base is None:
        return frozenset()
    scale = _MINOR_SCALE if str(mode).strip().lower().startswith("min") else _MAJOR_SCALE
    return frozenset((base + i) % 12 for i in scale)


@dataclass(frozen=True)
class HarmonyContext:
    """Chord + key context looked up by time. Both parts are optional."""

    spans: tuple[tuple[float, float, frozenset[int], int | None], ...] = ()
    key_classes: frozenset[int] = frozenset()

    def _span_at(self, t: float):
        for span in self.spans:
            if span[0] - 1e-6 <= t < span[1]:
                return span
        return None

    def chord_at(self, t: float) -> frozenset[int]:
        span = self._span_at(t)
        return span[2] if span else frozenset()

    def root_at(self, t: float) -> int | None:
        span = self._span_at(t)
        return span[3] if span else None


def harmony_context_from_json(
    harmony: Mapping[str, Any] | None,
) -> HarmonyContext:
    """Build a :class:`HarmonyContext` from a parsed ``harmony.json``."""
    if not harmony:
        return HarmonyContext()
    spans: list[tuple[float, float, frozenset[int], int | None]] = []
    for event in harmony.get("events", []) or []:
        try:
            start = float(event.get("t", 0.0))
            dur = float(event.get("duration", 0.0))
        except (TypeError, ValueError):
            continue
        root = _PITCH_CLASS.get(str(event.get("root", "")).strip().upper())
        classes = chord_pitch_classes(event.get("root", ""), event.get("quality", ""))
        if classes:
            spans.append((start, start + max(0.0, dur), classes, root))
    spans.sort()
    return HarmonyContext(
        spans=tuple(spans),
        key_classes=key_pitch_classes(harmony.get("key", ""), harmony.get("mode", "")),
    )


# ---------------------------------------------------------------------------
# Metrical position
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BeatGrid:
    """Downbeats and beats from ``song_timeline.json``.

    Accent is partly a matter of where a note falls. A note on a downbeat is
    load-bearing in a way an offbeat filler note is not, and that is exactly
    the distinction a reduction must respect when it decides what to spend
    its finger budget on.
    """

    beats: tuple[float, ...] = ()
    downbeats: tuple[float, ...] = ()
    tolerance_sec: float = 0.07

    def _near(self, times: tuple[float, ...], t: float) -> bool:
        if not times:
            return False
        index = bisect_left(times, t)
        for candidate in (index - 1, index):
            if 0 <= candidate < len(times) and abs(times[candidate] - t) <= self.tolerance_sec:
                return True
        return False

    def is_downbeat(self, t: float) -> bool:
        return self._near(self.downbeats, t)

    def is_on_beat(self, t: float) -> bool:
        return self._near(self.beats, t)


def beat_grid_from_json(
    timeline: Mapping[str, Any] | None, *, tolerance_sec: float = 0.07
) -> BeatGrid:
    if not timeline:
        return BeatGrid()
    beats: list[float] = []
    downbeats: list[float] = []
    previous_measure: Any = None
    for entry in timeline.get("beats", []) or []:
        try:
            t = float(entry.get("time"))
        except (TypeError, ValueError):
            continue
        beats.append(t)
        measure = entry.get("measure")
        if measure is not None and measure != previous_measure:
            downbeats.append(t)
            previous_measure = measure
    beats.sort()
    downbeats.sort()
    return BeatGrid(tuple(beats), tuple(downbeats), tolerance_sec)


# ---------------------------------------------------------------------------
# Recurring figures (motifs)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MotifConfig:
    """How to find a figure that recurs often enough to be structural.

    A repeated figure looks like redundancy to every density heuristic — the
    same handful of pitches over and over — so dedup, doubling penalties and
    polyphony caps all preferentially eat it, and the motif survives as a
    smear. Finding it explicitly is what lets us protect it.
    """

    min_length: int = 4              # notes in the figure
    max_length: int = 8
    min_occurrences: int = 4
    max_gap_sec: float = 1.2         # a longer rest ends the figure
    use_rhythm: bool = True
    rhythm_tolerance: float = 0.4    # relative IOI mismatch allowed
    min_distinct_intervals: int = 2  # reject a run of repeated notes
    # A figure that is mostly repeated notes ((0, 0, 7) and friends) is
    # accompaniment texture, not a motif, and it recurs everywhere.
    max_zero_interval_ratio: float = 0.5
    # A located instance must sit in the register the figure was mined in.
    # Without this, matching intervals across a dense texture finds chains
    # everywhere and "the motif" swallows the part.
    register_tolerance: int = 12
    # Hard ceiling on how much of the track may be declared inviolable. If
    # more than this is motif, the detector is over-firing, and protecting
    # everything protects nothing.
    max_coverage: float = 0.15


DEFAULT_MOTIF_CONFIG = MotifConfig()


@dataclass(frozen=True)
class MotifOccurrence:
    pattern: tuple[int, ...]
    start: float
    end: float
    keys: tuple[tuple[float, int], ...]


def _voice_sequences(
    notes: Sequence[MelodicNote], *, config: PlayabilityConfig
) -> list[list[MelodicNote]]:
    """Monophonic lines to mine: the top voice and the bottom voice.

    Mining only the top voice misses a figure voiced underneath a held chord
    tone, which is how the same motif can look absent in one section and
    present in another.
    """
    groups = group_by_onset(notes, window_sec=config.onset_window_sec)
    top = [max(g, key=lambda n: int(n.pitch)) for g in groups]
    bottom = [min(g, key=lambda n: int(n.pitch)) for g in groups]
    return [top, bottom]


def find_motifs(
    notes: Sequence[MelodicNote],
    *,
    config: PlayabilityConfig = DEFAULT_PLAYABILITY_CONFIG,
    motif_config: MotifConfig | None = None,
) -> tuple[set[tuple[float, int]], list[MotifOccurrence]]:
    """Find recurring interval figures and return the notes that carry them.

    Patterns are sequences of pitch intervals, so a figure restated a step
    higher still counts as the same figure, optionally required to match in
    rhythm too. Longer patterns are claimed first, so a six-note figure is
    not reported as three overlapping four-note ones.
    """
    motif_config = motif_config or DEFAULT_MOTIF_CONFIG
    occurrences: list[MotifOccurrence] = []
    carried: set[tuple[float, int]] = set()

    for line in _voice_sequences(notes, config=config):
        if len(line) < motif_config.min_length:
            continue
        claimed: set[int] = set()
        for length in range(motif_config.max_length, motif_config.min_length - 1, -1):
            span = length - 1
            buckets: dict[tuple[Any, ...], list[int]] = {}
            for start in range(0, len(line) - length + 1):
                window = line[start : start + length]
                gaps = [
                    float(window[i + 1].t_on) - float(window[i].t_on) for i in range(span)
                ]
                if any(gap > motif_config.max_gap_sec or gap <= 0 for gap in gaps):
                    continue
                intervals = tuple(
                    int(window[i + 1].pitch) - int(window[i].pitch) for i in range(span)
                )
                if len(set(intervals)) < motif_config.min_distinct_intervals:
                    continue
                zeros = sum(1 for i in intervals if i == 0)
                if zeros > motif_config.max_zero_interval_ratio * len(intervals):
                    continue
                signature: tuple[Any, ...] = (intervals,)
                if motif_config.use_rhythm:
                    unit = gaps[0]
                    shape = tuple(
                        round(gap / unit / motif_config.rhythm_tolerance) for gap in gaps
                    )
                    signature = (intervals, shape)
                buckets.setdefault(signature, []).append(start)

            for signature, starts in buckets.items():
                free = [
                    s
                    for s in starts
                    if not any(i in claimed for i in range(s, s + length))
                ]
                if len(free) < motif_config.min_occurrences:
                    continue
                intervals = signature[0]
                for start in free:
                    window = line[start : start + length]
                    keys = tuple(note_key(n) for n in window)
                    carried.update(keys)
                    occurrences.append(
                        MotifOccurrence(
                            pattern=tuple(intervals),
                            start=float(window[0].t_on),
                            end=float(window[-1].t_off),
                            keys=keys,
                        )
                    )
                    claimed.update(range(start, start + length))
    anchors: dict[tuple[int, ...], list[int]] = {}
    for occurrence in occurrences:
        anchors.setdefault(occurrence.pattern, []).append(occurrence.keys[0][1])
    if anchors:
        extra = locate_patterns(
            notes, anchors, config=config, motif_config=motif_config
        )
        for occurrence in extra:
            if not set(occurrence.keys) <= carried:
                carried.update(occurrence.keys)
                occurrences.append(occurrence)

    # Trim to the coverage ceiling, most-frequent patterns first: if the
    # detector has declared most of the track inviolable it has stopped
    # discriminating, and a reduction with nothing left to cut will cut the
    # motif anyway.
    # Never let the ceiling fall below what the detector's own minimum
    # evidence needs: a cap that cannot hold the required number of
    # occurrences would erase the figure instead of trimming it.
    floor = motif_config.max_length * motif_config.min_occurrences
    budget = max(floor, int(motif_config.max_coverage * max(1, len(notes))))
    if len(carried) > budget:
        counts: dict[tuple[int, ...], int] = {}
        for occurrence in occurrences:
            counts[occurrence.pattern] = counts.get(occurrence.pattern, 0) + 1
        by_pattern: dict[tuple[int, ...], list[MotifOccurrence]] = {}
        for occurrence in occurrences:
            by_pattern.setdefault(occurrence.pattern, []).append(occurrence)
        order = sorted(by_pattern, key=lambda p: (-counts[p], -len(p)))
        carried = set()
        kept: list[MotifOccurrence] = []
        for pattern in order:
            # Spread the budget across the timeline. Taking occurrences in
            # start order spends it all on the first minute and reports a
            # figure as absent from the second half of the song purely
            # because the ceiling ran out.
            group = sorted(by_pattern[pattern], key=lambda o: o.start)
            remaining = max(0, budget - len(carried))
            if remaining <= 0:
                break
            stride = max(1, len(group) * len(group[0].keys) // max(1, remaining))
            for occurrence in group[::stride]:
                if len(carried | set(occurrence.keys)) > budget:
                    break
                carried.update(occurrence.keys)
                kept.append(occurrence)
        occurrences = kept

    occurrences.sort(key=lambda occurrence: occurrence.start)
    return carried, occurrences


def locate_patterns(
    notes: Sequence[MelodicNote],
    patterns: Mapping[tuple[int, ...], Sequence[int]],
    *,
    config: PlayabilityConfig = DEFAULT_PLAYABILITY_CONFIG,
    motif_config: MotifConfig | None = None,
) -> list[MotifOccurrence]:
    """Find every instance of known figures, in any voice.

    Mining a reduced line finds the *pattern* cheaply but only sees the
    instances that happen to sit in that line — which is why a figure can
    look like it only appears in the back half of a song when really it is
    voiced under a held chord tone earlier on. Once the pattern is known,
    this walks the full texture for it: a chain across consecutive attack
    groups whose intervals match, with any note of each group eligible.

    ``patterns`` maps each pattern to the starting pitches it was mined at.
    A candidate chain must start within ``register_tolerance`` of one of
    them — matching intervals alone finds chains everywhere in a dense
    texture, and a figure restated four octaves away is not that figure.
    """
    motif_config = motif_config or DEFAULT_MOTIF_CONFIG
    groups = group_by_onset(notes, window_sec=config.onset_window_sec)
    found: list[MotifOccurrence] = []
    for pattern, anchor_pitches in patterns.items():
        anchors = sorted(set(int(p) for p in anchor_pitches))
        if not anchors:
            continue
        length = len(pattern) + 1
        for start in range(0, len(groups) - length + 1):
            window = groups[start : start + length]
            gaps = [
                float(window[i + 1][0].t_on) - float(window[i][0].t_on)
                for i in range(length - 1)
            ]
            if any(gap > motif_config.max_gap_sec or gap <= 0 for gap in gaps):
                continue
            chains: list[list[MelodicNote]] = [
                [n]
                for n in window[0]
                if any(
                    abs(int(n.pitch) - anchor) <= motif_config.register_tolerance
                    for anchor in anchors
                )
            ]
            for step, interval in enumerate(pattern, start=1):
                nxt: list[list[MelodicNote]] = []
                for chain in chains:
                    target = int(chain[-1].pitch) + interval
                    for candidate in window[step]:
                        if int(candidate.pitch) == target:
                            nxt.append(chain + [candidate])
                chains = nxt
                if not chains:
                    break
            for chain in chains:
                found.append(
                    MotifOccurrence(
                        pattern=tuple(pattern),
                        start=float(chain[0].t_on),
                        end=float(chain[-1].t_off),
                        keys=tuple(note_key(n) for n in chain),
                    )
                )
    return found


# ---------------------------------------------------------------------------
# Salience
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SalienceWeights:
    """What makes a note worth a finger. Every term is a named parameter.

    These defaults are a starting point for the sweep, not a claim. The
    sweep harness varies them and reports playability, preservation and
    density together, because they trade against each other and the right
    balance is a measurement.

    The provenance terms exist because the sources in a union are not equally
    trustworthy: the whole-mix transcription is the reading that sounds like
    the song, agreement between independent sources is corroboration, and a
    note only a wider stem mix found is the weakest of the three.
    """

    # Structural — these decide what survives a cut.
    motif: float = 20_000.0
    melody_line: float = 12_000.0
    bass_anchor: float = 9_000.0
    top_voice: float = 1_200.0

    # Metrical accent.
    downbeat: float = 2_500.0
    on_beat: float = 900.0

    # Audio support.
    audio_evidence: float = 3_000.0

    # Provenance (union input only; inert when there is a single source).
    source_primary: float = 2_500.0
    source_agreement: float = 1_100.0        # per corroborating extra source
    source_supplementary: float = 400.0

    # Harmony.
    chord_root: float = 1_100.0
    chord_tone: float = 700.0
    in_key: float = 200.0
    new_pitch_class: float = 900.0

    duration: float = 60.0
    doubling_penalty: float = 600.0
    shadow_penalty: float = 1_200.0


DEFAULT_SALIENCE_WEIGHTS = SalienceWeights()


@dataclass(frozen=True)
class SalienceContext:
    """Everything the salience score reads, gathered once per track."""

    evidence: Mapping[tuple[float, int], float] | None = None
    harmony: HarmonyContext = HarmonyContext()
    beats: BeatGrid = BeatGrid()
    melody: frozenset[tuple[float, int]] = frozenset()
    motif: frozenset[tuple[float, int]] = frozenset()
    provenance: Mapping[tuple[float, int], Sequence[str]] = MappingProxyType({})
    primary_source: str = "primary"


def note_salience(
    note: MelodicNote,
    *,
    context: SalienceContext,
    group: Sequence[MelodicNote],
    weights: SalienceWeights = DEFAULT_SALIENCE_WEIGHTS,
    config: PlayabilityConfig = DEFAULT_PLAYABILITY_CONFIG,
) -> float:
    """Musical importance of one note, independent of what else is kept.

    The new-pitch-class term is deliberately *not* here: it depends on what
    has already been selected, so it is applied as a marginal adjustment
    during selection.
    """
    key = note_key(note)
    pitch = int(note.pitch)
    pitches = [int(n.pitch) for n in group]
    score = 0.0

    if key in context.motif:
        score += weights.motif
    if key in context.melody:
        score += weights.melody_line
    if pitches and pitch == min(pitches):
        score += weights.bass_anchor
    if pitches and pitch == max(pitches):
        score += weights.top_voice

    t = float(note.t_on)
    if context.beats.is_downbeat(t):
        score += weights.downbeat
    elif context.beats.is_on_beat(t):
        score += weights.on_beat

    if context.evidence is not None:
        support = min(1.0, max(0.0, float(context.evidence.get(key, 1.0))))
        score += weights.audio_evidence * support
        if support < config.shadow_evidence_floor and _shadow_partners(
            pitch, [p for p in pitches if p != pitch], config
        ):
            score -= weights.shadow_penalty
    else:
        score += weights.audio_evidence * 0.5

    sources = tuple(context.provenance.get(key, ()) or ())
    if sources:
        if context.primary_source in sources:
            score += weights.source_primary
        else:
            score += weights.source_supplementary
        score += weights.source_agreement * max(0, len(sources) - 1)

    chord = context.harmony.chord_at(t)
    if chord:
        if (pitch % 12) in chord:
            score += weights.chord_tone
        root = context.harmony.root_at(t)
        if root is not None and (pitch % 12) == root:
            score += weights.chord_root
    if context.harmony.key_classes and (pitch % 12) in context.harmony.key_classes:
        score += weights.in_key

    score += min(2.0, _duration(note)) * weights.duration
    return score


def reduce_group_to_playable(
    group: Sequence[MelodicNote],
    *,
    context: SalienceContext | None = None,
    weights: SalienceWeights = DEFAULT_SALIENCE_WEIGHTS,
    config: PlayabilityConfig = DEFAULT_PLAYABILITY_CONFIG,
    evidence: Mapping[tuple[float, int], float] | None = None,
    harmony: HarmonyContext | None = None,
    melody: set[tuple[float, int]] | None = None,
) -> list[MelodicNote]:
    """Choose the most *salient* playable subset of one attack group.

    Not the easiest subset, and not the lowest N. Notes are taken in
    descending salience — motif first, then the melody line, then metrical
    accent and audio support — and each is kept only if the running set still
    divides between two hands. A note that would not fit is skipped, not
    substituted for something already chosen, so a high-salience note never
    loses its place to a convenient one.

    The one selection-dependent term is applied here: a pitch class already
    represented is worth less than a new one, so the finger budget buys
    distinct harmony before it buys doublings.
    """
    context = context or SalienceContext(
        evidence=evidence,
        harmony=harmony or HarmonyContext(),
        melody=frozenset(melody or ()),
    )
    notes = sorted(group, key=lambda n: int(n.pitch))
    if len(notes) <= 1:
        return list(notes)
    if is_hand_feasible([int(n.pitch) for n in notes], config=config):
        return list(notes)

    base = {
        note_key(n): note_salience(
            n, context=context, group=notes, weights=weights, config=config
        )
        for n in notes
    }

    selected: list[MelodicNote] = []
    selected_classes: set[int] = set()
    remaining = list(notes)
    while remaining:
        best_note = None
        best_value = float("-inf")
        for note in remaining:
            value = base[note_key(note)]
            if (int(note.pitch) % 12) in selected_classes:
                value -= weights.doubling_penalty
            else:
                value += weights.new_pitch_class
            if value > best_value:
                best_value, best_note = value, note
        assert best_note is not None
        trial = [int(n.pitch) for n in selected] + [int(best_note.pitch)]
        remaining.remove(best_note)
        if not is_hand_feasible(trial, config=config):
            continue
        selected.append(best_note)
        selected_classes.add(int(best_note.pitch) % 12)
    return sorted(selected, key=lambda n: int(n.pitch))


def enforce_hand_capacity(
    notes: Sequence[MelodicNote],
    *,
    context: SalienceContext | None = None,
    weights: SalienceWeights = DEFAULT_SALIENCE_WEIGHTS,
    config: PlayabilityConfig = DEFAULT_PLAYABILITY_CONFIG,
    evidence: Mapping[tuple[float, int], float] | None = None,
    harmony: HarmonyContext | None = None,
    melody: set[tuple[float, int]] | None = None,
) -> tuple[list[MelodicNote], int]:
    context = context or SalienceContext(
        evidence=evidence,
        harmony=harmony or HarmonyContext(),
        melody=frozenset(melody or ()),
    )
    kept: list[MelodicNote] = []
    removed = 0
    for group in group_by_onset(notes, window_sec=config.onset_window_sec):
        subset = reduce_group_to_playable(
            group, context=context, weights=weights, config=config
        )
        removed += len(group) - len(subset)
        kept.extend(subset)
    return _sorted_notes(kept), removed


# ---------------------------------------------------------------------------
# Stage 5: density budget
# ---------------------------------------------------------------------------


def enforce_density_budget(
    notes: Sequence[MelodicNote],
    *,
    context: SalienceContext,
    weights: SalienceWeights = DEFAULT_SALIENCE_WEIGHTS,
    config: PlayabilityConfig = DEFAULT_PLAYABILITY_CONFIG,
) -> tuple[list[MelodicNote], int]:
    """Thin the part to a target notes-per-second, lowest salience first.

    Hand feasibility bounds one attack; it does not bound how many attacks
    there are. A union of several transcriptions can satisfy the hand model
    at every moment and still be far denser than anything a person plays.
    This is the knob that answers "how much part is there", and it spends
    the cut on the least salient notes: never the motif, never the melody
    line, and never the last note of an attack, so no onset disappears.
    """
    target = config.target_density_per_sec
    if not target or not notes:
        return list(notes), 0

    groups = group_by_onset(notes, window_sec=config.onset_window_sec)
    scored: dict[int, list[tuple[float, tuple[float, int]]]] = {}
    survivors_in_group: dict[int, int] = {}
    group_of: dict[tuple[float, int], int] = {}
    window_notes: dict[int, int] = {}
    width = max(1e-6, config.density_window_sec)

    for index, group in enumerate(groups):
        survivors_in_group[index] = len(group)
        best = max(
            group,
            key=lambda n: note_salience(
                n, context=context, group=group, weights=weights, config=config
            ),
        )
        for note in group:
            key = note_key(note)
            group_of[key] = index
            window = int(float(note.t_on) // width)
            window_notes[window] = window_notes.get(window, 0) + 1
            if key in context.motif or key in context.melody or note is best:
                continue
            if config.density_protects_primary:
                sources = tuple(context.provenance.get(key, ()) or ())
                if context.primary_source in sources:
                    # The shipped transcription is the quality bar: the
                    # density budget spends supplementary notes, never the
                    # ones the primary already had.
                    continue
            scored.setdefault(window, []).append(
                (
                    note_salience(
                        note, context=context, group=group, weights=weights, config=config
                    ),
                    key,
                )
            )

    doomed: set[tuple[float, int]] = set()
    for window, count in window_notes.items():
        # Budget is per window, not global, so a dense chorus cannot spend the
        # intro's allowance and leave it thinner than it started.
        budget = int(target * width)
        surplus = count - budget
        if surplus <= 0:
            continue
        candidates = sorted(scored.get(window, []))
        for _score, key in candidates:
            if surplus <= 0:
                break
            index = group_of[key]
            if survivors_in_group[index] <= 1:
                continue
            survivors_in_group[index] -= 1
            doomed.add(key)
            surplus -= 1

    if not doomed:
        return list(notes), 0
    kept = [n for n in notes if note_key(n) not in doomed]
    return _sorted_notes(kept), len(doomed)


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------


def _distribution(notes: Sequence[MelodicNote], config: PlayabilityConfig) -> dict[str, Any]:
    groups = group_by_onset(notes, window_sec=config.onset_window_sec)
    sizes: dict[int, int] = {}
    spans: list[int] = []
    infeasible = 0
    for group in groups:
        sizes[len(group)] = sizes.get(len(group), 0) + 1
        pitches = [int(n.pitch) for n in group]
        if len(pitches) > 1:
            spans.append(max(pitches) - min(pitches))
        if not is_hand_feasible(pitches, config=config):
            infeasible += 1
    spans.sort()
    return {
        "notes": len(notes),
        "groups": len(groups),
        "group_sizes": dict(sorted(sizes.items())),
        "groups_ge4": sum(v for k, v in sizes.items() if k >= 4),
        "max_group": max(sizes) if sizes else 0,
        "max_polyphony": max_polyphony(notes),
        "unplayable_groups": infeasible,
        "span_median": spans[len(spans) // 2] if spans else 0,
        "span_max": spans[-1] if spans else 0,
    }


def make_playable(
    notes: Sequence[MelodicNote],
    *,
    evidence: Mapping[tuple[float, int], float] | None = None,
    harmony: HarmonyContext | None = None,
    beats: BeatGrid | None = None,
    provenance: Mapping[tuple[float, int], Sequence[str]] | None = None,
    primary_source: str = "primary",
    weights: SalienceWeights = DEFAULT_SALIENCE_WEIGHTS,
    motif_config: MotifConfig | None = None,
    config: PlayabilityConfig = DEFAULT_PLAYABILITY_CONFIG,
) -> tuple[list[MelodicNote], dict[str, Any]]:
    """Run the full pass and return ``(notes, report)``.

    Every context input is optional and each one only makes the ranking
    better informed: without audio the overtone cull is skipped, without a
    beat grid metrical accent drops out, without provenance the source terms
    go inert. That degradation is deliberate — missing context should cost
    accuracy, never correctness.

    The report carries the motif inventory and what happened to it, because
    a run that halves simultaneity while dropping motif notes has failed
    however good the hand-span numbers look.
    """
    harmony = harmony or HarmonyContext()
    beats = beats or BeatGrid()
    report: dict[str, Any] = {"before": _distribution(notes, config)}

    working = normalize_notes(notes)
    report["normalized_removed"] = len(notes) - len(working)

    melody = melody_line(working, evidence=evidence, config=config)
    motif_keys, occurrences = find_motifs(
        working, config=config, motif_config=motif_config
    )
    report["melody_notes"] = len(melody)
    report["motif_notes"] = len(motif_keys)
    report["motif_occurrences"] = len(occurrences)
    report["motif_patterns"] = sorted(
        {occurrence.pattern for occurrence in occurrences}
    )

    context = SalienceContext(
        evidence=evidence,
        harmony=harmony,
        beats=beats,
        melody=frozenset(melody),
        motif=frozenset(motif_keys),
        provenance=provenance or MappingProxyType({}),
        primary_source=primary_source,
    )

    working, shadow_removed = prune_overtone_shadows(
        working, evidence=evidence, melody=melody | motif_keys, config=config
    )
    report["shadow_removed"] = shadow_removed

    working, density_removed = enforce_density_budget(
        working, context=context, weights=weights, config=config
    )
    report["density_removed"] = density_removed

    working, capacity_removed = enforce_hand_capacity(
        working, context=context, weights=weights, config=config
    )
    report["capacity_removed"] = capacity_removed

    working, trimmed = trim_sustain(working, config=config)
    report["sustain_trimmed"] = trimmed

    kept_keys = {note_key(n) for n in working}
    report["motif_notes_kept"] = sum(1 for key in motif_keys if key in kept_keys)
    report["motif_intact"] = report["motif_notes_kept"] == len(motif_keys)
    report["melody_notes_kept"] = sum(1 for key in melody if key in kept_keys)

    report["after"] = _distribution(working, config)
    report["removed_total"] = len(notes) - len(working)
    report["removed_fraction"] = (
        (len(notes) - len(working)) / len(notes) if notes else 0.0
    )
    return working, report
