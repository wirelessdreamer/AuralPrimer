"""Merge several transcriptions of the same part into one candidate superset.

Running one engine over several source mixes finds different notes. Measured
on What A God (435 s), against 23 windows where the shipped Keys track is
silent while the keys stem is audibly playing:

======================  ======  ==================  ==================
input to the engine     notes   dropout windows     notes landing in
                                filled (of 23)      those windows
======================  ======  ==================  ==================
whole mix (primary)      2084    0                   0
keys stem only           1206    6                  77
keys + guitar            3760    8                 173
keys + guitar + other    3698    9                 210
all but vocals/drums     3468    9                 186
======================  ======  ==================  ==================

So the union recovers real material the primary missed. It also multiplies
duplicates: the same note found by four sources is one note, not four. This
module does the merge and — crucially — keeps **provenance**, because which
sources found a note is evidence about the note. A note the primary found is
stronger than one only a wider mix found; a note four sources agree on is
stronger than one seen once.

Provenance is deliberately not folded into a single number here. It is
returned as the set of source ids, and
:class:`aural_ingest.algorithms.piano_playability.SalienceWeights` turns it
into a score with named, sweepable weights — because the right weighting is a
benchmark result, not something to reason into place.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Sequence

from aural_ingest.transcription import MelodicNote


@dataclass(frozen=True)
class UnionConfig:
    """How to collapse the same note reported by several sources.

    ``dedup_window_sec`` is the tolerance for calling two same-pitch reports
    one note. Engines disagree about onset by a few tens of milliseconds on
    identical material, so anything much tighter double-counts and anything
    much looser starts swallowing genuine repeated notes.

    It was 60 ms, which was worse than either. Sources read from different
    stem mixes disagree by more than that, so the window collapsed everything
    inside it -- leaving a single pair on What A God -- while 341 pairs landed
    just outside and survived as a second strike on the same key 60-150 ms
    before the real one. On a keyboard that is a flam no hand can play.

    The interval histogram says where the line goes. Real repeats pile onto
    note values: at 78.9 bpm, 436 pairs at 180-199 ms (a sixteenth) and 467 at
    380-399 ms (an eighth). The artifacts spread as a flat plateau from 60 to
    179 ms with no peak anywhere, because nothing musical lives there. 170 ms
    clears the plateau and still leaves 20 ms under the sixteenth.
    """

    dedup_window_sec: float = 0.17
    primary: str = "primary"
    # When the primary found the note, its timing wins: it is the reading the
    # user says sounds like the song.
    prefer_primary_timing: bool = True
    # A merged note lasts as long as the longest report, capped so one
    # source's runaway sustain does not swamp the rest.
    max_duration_sec: float = 6.0
    # Admit a supplementary-only note only where the primary has a hole.
    # A blanket union of five sources is 3.7x the primary's note count and
    # 15 notes a second — denser than any piano part ever written — while
    # the holes are the whole reason to run the extra sources at all. This
    # keeps the recovery and drops the bulk.
    supplement_only_in_gaps: bool = False
    gap_min_sec: float = 1.5
    gap_pad_sec: float = 0.15


DEFAULT_UNION_CONFIG = UnionConfig()


@dataclass(frozen=True)
class UnionResult:
    notes: list[MelodicNote]
    provenance: dict[tuple[float, int], tuple[str, ...]]
    per_source_counts: dict[str, int]
    merged_count: int

    def sources_for(self, key: tuple[float, int]) -> tuple[str, ...]:
        return self.provenance.get(key, ())


def _note_key(note: MelodicNote) -> tuple[float, int]:
    return (round(float(note.t_on), 4), int(note.pitch))


def union_sources(
    sources: Mapping[str, Sequence[MelodicNote]],
    *,
    config: UnionConfig = DEFAULT_UNION_CONFIG,
) -> UnionResult:
    """Merge ``{source_id: notes}`` into one list plus a provenance map.

    Same pitch within ``dedup_window_sec`` is one note. The merged note keeps
    the primary source's onset when the primary reported it, otherwise the
    earliest onset in the cluster; its release is the longest reported.
    """
    tagged: list[tuple[int, float, str, MelodicNote]] = []
    counts: dict[str, int] = {}
    for source, notes in sources.items():
        counts[source] = len(notes)
        for note in notes:
            tagged.append((int(note.pitch), float(note.t_on), source, note))
    tagged.sort(key=lambda row: (row[0], row[1]))

    merged: list[MelodicNote] = []
    provenance: dict[tuple[float, int], tuple[str, ...]] = {}
    duplicates = 0

    index = 0
    while index < len(tagged):
        pitch, start, _, _ = tagged[index]
        cluster = [tagged[index]]
        index += 1
        while (
            index < len(tagged)
            and tagged[index][0] == pitch
            and tagged[index][1] - cluster[-1][1] <= config.dedup_window_sec
        ):
            cluster.append(tagged[index])
            index += 1

        names = tuple(dict.fromkeys(row[2] for row in cluster))
        duplicates += len(cluster) - 1

        primary_rows = [row for row in cluster if row[2] == config.primary]
        if config.prefer_primary_timing and primary_rows:
            anchor = primary_rows[0][3]
        else:
            anchor = min(cluster, key=lambda row: row[1])[3]
        end = max(float(row[3].t_off) for row in cluster)
        end = min(end, float(anchor.t_on) + config.max_duration_sec)
        note = replace(anchor, t_off=max(end, float(anchor.t_on) + 1e-3))
        merged.append(note)
        provenance[_note_key(note)] = names

    if config.supplement_only_in_gaps:
        gaps = primary_gaps(sources.get(config.primary, ()), config=config)
        filtered: list[MelodicNote] = []
        for note in merged:
            key = _note_key(note)
            if config.primary in provenance.get(key, ()):
                filtered.append(note)
                continue
            if _in_any(gaps, float(note.t_on)):
                filtered.append(note)
        merged = filtered
        provenance = {
            key: names for key, names in provenance.items()
            if key in {_note_key(n) for n in merged}
        }

    merged.sort(key=lambda n: (float(n.t_on), int(n.pitch)))
    return UnionResult(
        notes=merged,
        provenance=provenance,
        per_source_counts=counts,
        merged_count=duplicates,
    )


def primary_gaps(
    primary: Sequence[MelodicNote],
    *,
    config: UnionConfig = DEFAULT_UNION_CONFIG,
) -> list[tuple[float, float]]:
    """Windows where the primary transcription has nothing to say.

    Padded at both ends: a gap window starts *at* a note's onset, so an
    unpadded window hands every candidate a free hit on the boundary note.
    """
    onsets = sorted({round(float(n.t_on), 4) for n in primary})
    windows: list[tuple[float, float]] = []
    for start, end in zip(onsets, onsets[1:]):
        if end - start < config.gap_min_sec:
            continue
        low, high = start + config.gap_pad_sec, end - config.gap_pad_sec
        if high > low:
            windows.append((low, high))
    return windows


def _in_any(windows: Sequence[tuple[float, float]], t: float) -> bool:
    return any(low <= t <= high for low, high in windows)


def notes_from_rows(rows: Iterable[Mapping[str, object]], *, instrument: str = "keys") -> list[MelodicNote]:
    """Build ``MelodicNote`` objects from plain dict rows (JSON on disk)."""
    out: list[MelodicNote] = []
    for row in rows:
        try:
            t_on = float(row["t_on"])  # type: ignore[index]
            t_off = float(row["t_off"])  # type: ignore[index]
            pitch = int(row["pitch"])  # type: ignore[index]
        except (KeyError, TypeError, ValueError):
            continue
        if t_off <= t_on:
            continue
        out.append(
            MelodicNote(
                t_on=t_on,
                t_off=t_off,
                pitch=pitch,
                velocity=int(row.get("velocity", 100) or 100),  # type: ignore[union-attr]
                instrument=instrument,
            )
        )
    return out
