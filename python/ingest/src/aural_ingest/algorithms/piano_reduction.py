"""Collapse a multi-part score into a two-hand piano reduction.

This is an **arrangement** tool, not a transcription one. It never runs an
engine, never touches audio, and adds no dependency: it takes the parts the
pipeline already produced (``{role: [MelodicNote]}``, i.e. the tracks of
``aural/notes.mid``) and answers a different question about them.

Why a reduction is a better-posed question than a transcription
---------------------------------------------------------------
Whole-mix transcription mislabels instruments in dense mixes. On *Jireh*,
whole-mix MuScriptor labelled 5271 of ~6500 notes ``acoustic_piano`` and
found **zero** guitar in a minute of music that plainly has guitar. That
looks like a catastrophic failure and mostly is not one: in a big mix the
piano and the guitar are frequently playing the *same notes*, voiced
differently. The engine got the harmony right and the voicing wrong.

"Which instrument played this note" is therefore the question the audio
cannot answer, while "what would a pianist play here" is one the note data
can. This module asks the second one. The instrument labels stop being a
claim that has to be correct and become what they actually are — evidence
about which notes several parts agree on.

Simplification, then harmonisation
----------------------------------
The standard framing for reduction, in two halves, and both halves are here:

* **Simplify.** Merge what is duplicated across parts, drop the octave
  doublings an arranger would not write, then cut whatever still exceeds two
  hands. The cut is delegated to :mod:`aural_ingest.algorithms.piano_playability`,
  which already owns the hand model (span, fingers, salience ranking, motif
  protection) for a single part. Nothing about that model is re-implemented
  here; this module's job is to turn *many* parts into the one part it
  expects.
* **Harmonise.** Distribute the survivors between two non-crossing hands —
  :func:`assign_hands`. Reduction output that is only a note list has skipped
  this step: "playable" is a claim about hands, so the hands have to be in the
  output for the claim to be checkable.

Cross-part agreement is the signal, not the noise
-------------------------------------------------
The one thing this module knows that a single-part pass cannot is *how many
parts played a note*. A pitch that guitar and keys both attack at the same
instant is not two notes to a pianist — it is one key, one finger — but it is
two independent votes that the note is real. :func:`merge_parts` collapses it
to a single note and records the contributing roles as **provenance**, which
is exactly the input ``piano_playability``'s salience model already accepts
for corroboration between transcription sources. The doubling that made the
transcription look wrong is what makes the reduction confident.

Everything degrades: no beat grid means metrical accent drops out of the
ranking, no harmony context means chord-tone terms go inert, no audio
evidence means the overtone cull is skipped. Missing context costs accuracy,
never correctness.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from aural_ingest.algorithms.piano_playability import (
    DEFAULT_PLAYABILITY_CONFIG,
    DEFAULT_SALIENCE_WEIGHTS,
    BeatGrid,
    HarmonyContext,
    PlayabilityConfig,
    SalienceWeights,
    group_by_onset,
    hand_split,
    make_playable,
    note_key,
)
from aural_ingest.transcription import MelodicNote

ENGINE_ID = "piano_reduction"

# Output track names. Two instruments, one per hand, so the result reads as a
# piano part in any DAW/notation program rather than as an untagged note soup.
RIGHT_HAND_TRACK = "Piano RH"
LEFT_HAND_TRACK = "Piano LH"
RIGHT_HAND_ROLE = "piano_rh"
LEFT_HAND_ROLE = "piano_lh"

# Grand-staff range: A0..C8, the 88 keys.
KEYBOARD_MIN = 21
KEYBOARD_MAX = 108

# Which role wins a unison merge, and which role is treated as the melody the
# right hand is built around. Higher wins.
#
# The ordering is about what the role *contributes to a piano cover*, not about
# how much of the mix it occupies. Vocals carry the tune, so in a piano cover
# they become the right hand — that is the same lead-sheet reading (melody +
# chords) a piano-cover generator starts from. Rhythm guitar sits at the bottom
# because it is overwhelmingly strummed doubling of harmony that some other part
# also states: it is the part most likely to be the *duplicate* in a unison, and
# least likely to be the note worth keeping the label of.
DEFAULT_ROLE_PRIORS: dict[str, float] = {
    "vocals": 6.0,
    "lead_guitar": 5.0,
    "melodic": 4.0,
    "keys": 3.0,
    "bass": 2.0,
    "rhythm_guitar": 1.0,
}
_UNKNOWN_ROLE_PRIOR = 0.5

# Roles that never contribute notes. Drums are events, not pitches, and are
# charted separately; they are excluded by name so a caller passing a whole
# manifest's worth of parts cannot accidentally chart a kick drum as a key.
DEFAULT_DROP_ROLES: frozenset[str] = frozenset({"drums"})


@dataclass(frozen=True)
class ReductionConfig:
    """Tunables for the reduction. The hand model lives in ``playability``."""

    playability: PlayabilityConfig = DEFAULT_PLAYABILITY_CONFIG

    # Two parts attacking the same pitch this close together are one key press.
    # Deliberately the same default as the playability pass's attack-grouping
    # window: a pair of notes the reduction reports as "simultaneous" and a pair
    # it merges have to be the same pair, or the before/after counts describe
    # two different things.
    merge_window_sec: float = DEFAULT_PLAYABILITY_CONFIG.onset_window_sec

    # How many instances of one pitch class may sound at one attack. 1 doubling
    # (so two instances) keeps the outer voices — the bass anchor at the bottom
    # and the melody at the top — and drops the middle octave, which is the
    # doubling an arranger writes out first.
    max_octave_doublings: int = 1

    keyboard_min: int = KEYBOARD_MIN
    keyboard_max: int = KEYBOARD_MAX

    # Below this pitch a lone attack belongs to the left hand. `hand_split`
    # cannot decide this: with nothing to split, every arrangement of one note
    # is feasible, so a solo bass note would be handed to the right hand purely
    # because the right hand is where the function puts leftovers.
    hand_pivot: int = 60  # middle C

    role_priors: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_ROLE_PRIORS))
    drop_roles: frozenset[str] = DEFAULT_DROP_ROLES


DEFAULT_REDUCTION_CONFIG = ReductionConfig()


@dataclass(frozen=True)
class PianoReduction:
    """A two-hand reduction plus the report that makes it checkable."""

    left_hand: list[MelodicNote]
    right_hand: list[MelodicNote]
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def notes(self) -> list[MelodicNote]:
        """Both hands as one time-ordered list."""
        return sorted(
            [*self.left_hand, *self.right_hand],
            key=lambda n: (float(n.t_on), int(n.pitch)),
        )


# --------------------------------------------------------------------------- #
# Stage 1: many parts -> one part
# --------------------------------------------------------------------------- #


def fold_into_keyboard(pitch: int, *, lo: int = KEYBOARD_MIN, hi: int = KEYBOARD_MAX) -> int:
    """Octave-shift a pitch onto the 88 keys.

    Folding, not clamping. A 5-string bass low B (MIDI 23) is on the keyboard
    already, but a transcription octave error below A0, or a harmonic squeal
    above C8, is not — and clamping those to the end keys invents a pitch class
    that was never played. Shifting by octaves keeps the pitch class, which is
    the part of the note the harmony depends on.
    """
    pitch = int(pitch)
    while pitch < lo:
        pitch += 12
    while pitch > hi:
        pitch -= 12
    return pitch


def primary_role(
    parts: Mapping[str, Sequence[MelodicNote]],
    *,
    config: ReductionConfig = DEFAULT_REDUCTION_CONFIG,
) -> str:
    """The melody-bearing role present, by :data:`DEFAULT_ROLE_PRIORS`.

    Names the "primary source" for the salience model, so the tune keeps its
    fingers when a chord has to give some up.
    """
    candidates = [
        role
        for role, notes in parts.items()
        if notes and role not in config.drop_roles
    ]
    if not candidates:
        return "melodic"
    return max(candidates, key=lambda r: (config.role_priors.get(r, _UNKNOWN_ROLE_PRIOR), r))


def merge_parts(
    parts: Mapping[str, Sequence[MelodicNote]],
    *,
    config: ReductionConfig = DEFAULT_REDUCTION_CONFIG,
) -> tuple[list[MelodicNote], dict[tuple[float, int], list[str]]]:
    """Flatten parts into one stream, merging cross-part unisons.

    Returns ``(notes, provenance)`` where provenance maps each surviving note's
    :func:`~aural_ingest.algorithms.piano_playability.note_key` to the sorted
    roles that played it — the corroboration signal, ready for
    :func:`~aural_ingest.algorithms.piano_playability.make_playable`.

    A merged note spans the union of its sources (the key stays down as long as
    any part held it) and takes the loudest velocity and the highest-priority
    role's label. Same-pitch notes that are *not* simultaneous are left alone:
    a re-struck key is a real second note, and trimming its predecessor is
    already ``normalize_notes``' job.
    """
    window = max(0.0, float(config.merge_window_sec))
    by_pitch: dict[int, list[tuple[str, MelodicNote]]] = {}

    for role, notes in parts.items():
        if role in config.drop_roles:
            continue
        for note in notes:
            if float(note.t_off) - float(note.t_on) <= 0.0:
                continue
            pitch = fold_into_keyboard(
                int(note.pitch), lo=config.keyboard_min, hi=config.keyboard_max
            )
            by_pitch.setdefault(pitch, []).append((role, replace(note, pitch=pitch)))

    merged: list[MelodicNote] = []
    provenance: dict[tuple[float, int], list[str]] = {}

    for pitch, entries in by_pitch.items():
        entries.sort(key=lambda item: float(item[1].t_on))
        index = 0
        while index < len(entries):
            # Anchored cluster, not a chain: a chain of near-onsets can walk a
            # tremolo into one held note, while an anchor bounds every cluster
            # to `merge_window_sec` no matter how many parts pile in.
            anchor = float(entries[index][1].t_on)
            cluster = [entries[index]]
            index += 1
            while index < len(entries) and float(entries[index][1].t_on) - anchor <= window:
                cluster.append(entries[index])
                index += 1

            roles = sorted({role for role, _ in cluster})
            winner_role, winner = max(
                cluster,
                key=lambda item: (
                    config.role_priors.get(item[0], _UNKNOWN_ROLE_PRIOR),
                    float(item[1].t_off) - float(item[1].t_on),
                ),
            )
            note = replace(
                winner,
                t_on=min(float(n.t_on) for _, n in cluster),
                t_off=max(float(n.t_off) for _, n in cluster),
                pitch=pitch,
                velocity=max(int(n.velocity) for _, n in cluster),
                instrument=winner_role,
            )
            merged.append(note)
            provenance[note_key(note)] = roles

    merged.sort(key=lambda n: (float(n.t_on), int(n.pitch)))
    return merged, provenance


def collapse_octave_doublings(
    notes: Sequence[MelodicNote],
    *,
    config: ReductionConfig = DEFAULT_REDUCTION_CONFIG,
) -> tuple[list[MelodicNote], int]:
    """Thin a pitch class sounding in too many octaves at one attack.

    Keeps the **outer** voices and spends the cut on the middle ones: the
    bottom instance is the bass anchor and the top instance is usually the
    melody, while an inner octave of a pitch class already stated twice adds
    weight and no information. Two hands have ten fingers and a piano cover
    that spends three of them on one pitch class has spent them badly.

    This runs *before* the hand-capacity cut rather than relying on it, because
    the capacity cut only fires on groups that are already unplayable — a
    three-octave doubling inside an otherwise small chord is comfortably
    playable and still bad arranging.
    """
    limit = max(1, int(config.max_octave_doublings) + 1)
    kept: list[MelodicNote] = []
    removed = 0

    for group in group_by_onset(notes, window_sec=config.playability.onset_window_sec):
        by_class: dict[int, list[MelodicNote]] = {}
        for note in group:
            by_class.setdefault(int(note.pitch) % 12, []).append(note)
        for members in by_class.values():
            if len(members) <= limit:
                kept.extend(members)
                continue
            members.sort(key=lambda n: int(n.pitch))
            chosen = _outermost(members, limit)
            removed += len(members) - len(chosen)
            kept.extend(chosen)

    kept.sort(key=lambda n: (float(n.t_on), int(n.pitch)))
    return kept, removed


def _outermost(members: Sequence[MelodicNote], limit: int) -> list[MelodicNote]:
    """Take ``limit`` notes from a pitch-sorted run, working inward from both ends.

    Bottom, then top, then alternating inward. At the default limit of two that
    is exactly ``{lowest, highest}`` — the bass anchor and the melody — which is
    the case that matters; the alternation only decides which inner voice
    survives at looser limits, where either choice is defensible.
    """
    out: list[MelodicNote] = []
    low, high = 0, len(members) - 1
    take_low = True
    while len(out) < limit and low <= high:
        if take_low:
            out.append(members[low])
            low += 1
        else:
            out.append(members[high])
            high -= 1
        take_low = not take_low
    return out


# --------------------------------------------------------------------------- #
# Stage 3: harmonise -- put the survivors in two hands
# --------------------------------------------------------------------------- #


def assign_hands(
    notes: Sequence[MelodicNote],
    *,
    config: ReductionConfig = DEFAULT_REDUCTION_CONFIG,
) -> tuple[list[MelodicNote], list[MelodicNote]]:
    """Split notes into ``(left_hand, right_hand)``, non-crossing per attack.

    The split point is
    :func:`~aural_ingest.algorithms.piano_playability.hand_split`'s — the
    widest interior gap, which is where a pianist puts the break — so the hand
    assignment agrees by construction with the feasibility test the reduction
    was cut to satisfy. A note's hand is decided at its onset and does not
    change while it sounds.

    Attacks are grouped exactly as the playability pass groups them, so a group
    it certified playable is a group this function can actually deal out.
    """
    left: list[MelodicNote] = []
    right: list[MelodicNote] = []

    for group in group_by_onset(notes, window_sec=config.playability.onset_window_sec):
        pitches = [int(n.pitch) for n in group]
        boundary = _left_hand_ceiling(pitches, config=config)
        for note in group:
            if boundary is not None and int(note.pitch) <= boundary:
                left.append(replace(note, instrument=LEFT_HAND_ROLE))
            else:
                right.append(replace(note, instrument=RIGHT_HAND_ROLE))

    left.sort(key=lambda n: (float(n.t_on), int(n.pitch)))
    right.sort(key=lambda n: (float(n.t_on), int(n.pitch)))
    return left, right


def _left_hand_ceiling(
    pitches: Sequence[int], *, config: ReductionConfig
) -> int | None:
    """Highest pitch the left hand takes at this attack, or ``None`` for all-right."""
    if not pitches:
        return None
    split = hand_split(pitches, config=config.playability)
    if split is None:
        # Unreachable after the capacity cut, but a caller may hand us raw
        # notes: fall back to the median so the output is still two hands.
        ordered = sorted(pitches)
        return ordered[(len(ordered) - 1) // 2]
    low, _high = split
    if low:
        return low[-1]
    # `hand_split` parks an unsplittable run in the right hand. Low on the
    # keyboard that is the wrong hand, and register is the only thing left to
    # decide it by.
    return max(pitches) if max(pitches) < config.hand_pivot else None


# --------------------------------------------------------------------------- #
# The pass
# --------------------------------------------------------------------------- #


def reduce_score(
    parts: Mapping[str, Sequence[MelodicNote]],
    *,
    config: ReductionConfig = DEFAULT_REDUCTION_CONFIG,
    evidence: Mapping[tuple[float, int], float] | None = None,
    harmony: HarmonyContext | None = None,
    beats: BeatGrid | None = None,
    weights: SalienceWeights = DEFAULT_SALIENCE_WEIGHTS,
) -> PianoReduction:
    """Reduce a multi-part score to two hands.

    Merge unisons, thin octave doublings, cut to the hand model, deal into two
    hands. The report carries the input part sizes, what each stage removed,
    and the playability pass's own before/after distributions, so a run can be
    judged without re-deriving anything.
    """
    part_sizes = {role: len(list(notes)) for role, notes in parts.items()}
    skipped = {
        role: count for role, count in part_sizes.items() if role in config.drop_roles
    }

    primary = primary_role(parts, config=config)
    merged, provenance = merge_parts(parts, config=config)
    unisons_merged = sum(max(0, len(r) - 1) for r in provenance.values())

    thinned, octaves_removed = collapse_octave_doublings(merged, config=config)

    kept, play_report = make_playable(
        thinned,
        evidence=evidence,
        harmony=harmony,
        beats=beats,
        provenance=provenance,
        primary_source=primary,
        weights=weights,
        config=config.playability,
    )

    left, right = assign_hands(kept, config=config)

    corroborated = sum(1 for roles in provenance.values() if len(roles) > 1)
    report: dict[str, Any] = {
        "engine": ENGINE_ID,
        "parts": part_sizes,
        "skipped_parts": skipped,
        "primary_source": primary,
        "notes_in": sum(
            count for role, count in part_sizes.items() if role not in config.drop_roles
        ),
        "merged_notes": len(merged),
        "unisons_merged": unisons_merged,
        "corroborated_notes": corroborated,
        "octave_doublings_removed": octaves_removed,
        "playability": play_report,
        "hands": _hand_report(left, right, config=config),
        "notes_out": len(left) + len(right),
    }
    return PianoReduction(left_hand=left, right_hand=right, report=report)


def _hand_report(
    left: Sequence[MelodicNote],
    right: Sequence[MelodicNote],
    *,
    config: ReductionConfig,
) -> dict[str, Any]:
    def spans(notes: Sequence[MelodicNote]) -> int:
        widest = 0
        for group in group_by_onset(
            notes, window_sec=config.playability.onset_window_sec
        ):
            pitches = [int(n.pitch) for n in group]
            if len(pitches) > 1:
                widest = max(widest, max(pitches) - min(pitches))
        return widest

    def most_fingers(notes: Sequence[MelodicNote]) -> int:
        return max(
            (
                len(group)
                for group in group_by_onset(
                    notes, window_sec=config.playability.onset_window_sec
                )
            ),
            default=0,
        )

    return {
        "left_notes": len(left),
        "right_notes": len(right),
        "left_max_span": spans(left),
        "right_max_span": spans(right),
        "left_max_fingers": most_fingers(left),
        "right_max_fingers": most_fingers(right),
        "max_hand_span": config.playability.max_hand_span,
        "max_notes_per_hand": config.playability.max_notes_per_hand,
    }


# --------------------------------------------------------------------------- #
# MIDI I/O. `pretty_midi` is imported lazily so the logic above stays testable
# without it, matching how the rest of the package treats heavy imports.
# --------------------------------------------------------------------------- #

# `aural/notes.mid` track name -> role, the inverse of arrangement_prep's
# ROLE_TRACK_NAME. Matched case-insensitively on the instrument name.
_TRACK_NAME_ROLE: dict[str, str] = {
    "bass": "bass",
    "rhythm guitar": "rhythm_guitar",
    "lead guitar": "lead_guitar",
    "keys": "keys",
    "melodic": "melodic",
    "vocals": "vocals",
}


def role_for_track_name(name: str) -> str:
    """Role for a MIDI instrument name, falling back to a normalised name.

    An unrecognised track is kept rather than dropped — it still played notes,
    and a reduction that silently ignores a track the user can see in the file
    is worse than one that ranks it conservatively.
    """
    key = " ".join(str(name or "").strip().lower().split())
    if key in _TRACK_NAME_ROLE:
        return _TRACK_NAME_ROLE[key]
    return key.replace(" ", "_") or "melodic"


def parts_from_midi(midi_path: Path | str) -> dict[str, list[MelodicNote]]:
    """Read a multi-track MIDI into ``{role: [MelodicNote]}``.

    Drum instruments are skipped: they are events, not a pitched part, and the
    game charts them from ``drum_tab.json`` anyway.
    """
    import pretty_midi  # heavy-ish; deferred so the pure logic imports free

    midi = pretty_midi.PrettyMIDI(str(midi_path))
    parts: dict[str, list[MelodicNote]] = {}
    for instrument in midi.instruments:
        if instrument.is_drum:
            continue
        role = role_for_track_name(instrument.name)
        bucket = parts.setdefault(role, [])
        for note in instrument.notes:
            if float(note.end) <= float(note.start):
                continue
            bucket.append(
                MelodicNote(
                    t_on=float(note.start),
                    t_off=float(note.end),
                    pitch=int(note.pitch),
                    velocity=int(note.velocity),
                    instrument=role,
                )
            )
    for notes in parts.values():
        notes.sort(key=lambda n: (n.t_on, n.pitch))
    return parts


def write_reduction_midi(reduction: PianoReduction, out_path: Path | str) -> Path:
    """Write the reduction as a 2-track piano MIDI (``Piano RH`` / ``Piano LH``)."""
    import pretty_midi

    midi = pretty_midi.PrettyMIDI()
    for name, notes in (
        (RIGHT_HAND_TRACK, reduction.right_hand),
        (LEFT_HAND_TRACK, reduction.left_hand),
    ):
        instrument = pretty_midi.Instrument(program=0, is_drum=False, name=name)
        for note in notes:
            instrument.notes.append(
                pretty_midi.Note(
                    velocity=max(1, min(127, int(note.velocity))),
                    pitch=int(note.pitch),
                    start=float(note.t_on),
                    end=max(float(note.t_off), float(note.t_on) + 1e-3),
                )
            )
        midi.instruments.append(instrument)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    midi.write(str(out))
    return out


def harmony_and_beats_for_pack(
    pack_root: Path | str,
) -> tuple[HarmonyContext | None, BeatGrid | None]:
    """Best-effort chord/key + beat context from a pack, or ``(None, None)``.

    Never raises: a pack without a timeline is a less well-informed ranking,
    not a failed reduction.
    """
    import json

    from aural_ingest.algorithms.piano_playability import (
        beat_grid_from_json,
        harmony_context_from_json,
    )
    from aural_ingest.pack_paths import pack_feature_dirname

    root = Path(pack_root)
    beats: BeatGrid | None = None
    harmony: HarmonyContext | None = None

    def _read(path: Path) -> Any | None:
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return None

    timeline = _read(root / "song_timeline.json")
    if timeline:
        try:
            beats = beat_grid_from_json(timeline)
        except Exception:
            beats = None

    for candidate in (
        root / pack_feature_dirname(root) / "harmony.json",
        root / "harmony.json",
    ):
        doc = _read(candidate)
        if doc:
            try:
                harmony = harmony_context_from_json(doc)
            except Exception:
                harmony = None
            break

    return harmony, beats


def reduce_pack(
    pack_root: Path | str,
    *,
    config: ReductionConfig = DEFAULT_REDUCTION_CONFIG,
    force: bool = False,
) -> dict[str, Any]:
    """Reduce a pack's ``<features>/notes.mid`` to a two-hand piano part.

    Writes ``<features>/piano_reduction.mid`` and
    ``<features>/piano_reduction.json`` (the report) and stamps
    ``piano_reduction`` into ``manifest.yaml``. Existing output is left alone
    unless ``force``.

    This is additive analysis: it never rewrites ``notes.mid`` and never
    changes what the game charts.
    """
    import json

    from aural_ingest.pack_paths import (
        load_pack_manifest,
        pack_feature_dirname,
        update_manifest_keys,
    )

    root = Path(pack_root)
    status: dict[str, Any] = {"ok": False, "pack": str(root)}
    if not root.is_dir():
        status["error"] = f"pack not a directory: {root}"
        return status

    feature_dir = root / pack_feature_dirname(root)
    # The manifest key is the source of truth for where the score lives; the
    # conventional path is the fallback for a pack that was never stamped.
    manifest = load_pack_manifest(root) or {}
    stamped = str(manifest.get("aural_notes_mid") or "").strip()
    notes_mid = (root / stamped) if stamped else (feature_dir / "notes.mid")
    if not notes_mid.is_file():
        notes_mid = feature_dir / "notes.mid"
    if not notes_mid.is_file():
        status["error"] = f"no melodic score to reduce: {notes_mid} is missing"
        return status

    out_mid = feature_dir / "piano_reduction.mid"
    out_json = feature_dir / "piano_reduction.json"
    if out_mid.exists() and not force:
        status.update(ok=True, skipped=True, reason="already exists (use --force)")
        status["midi"] = str(out_mid)
        return status

    parts = parts_from_midi(notes_mid)
    if not any(parts.values()):
        status["error"] = f"no melodic notes in {notes_mid}"
        return status

    harmony, beats = harmony_and_beats_for_pack(root)
    reduction = reduce_score(parts, config=config, harmony=harmony, beats=beats)

    write_reduction_midi(reduction, out_mid)
    out_json.write_text(
        json.dumps(reduction.report, indent=2, sort_keys=True), encoding="utf-8"
    )

    rel = f"{pack_feature_dirname(root)}/piano_reduction.mid"
    try:
        update_manifest_keys(root, {"piano_reduction": rel})
    except Exception as exc:  # noqa: BLE001 — a stamp failure must not lose the work
        status["manifest_warning"] = f"could not stamp manifest: {exc}"

    status.update(
        ok=True,
        skipped=False,
        midi=str(out_mid),
        report_json=str(out_json),
        manifest_key=rel,
        report=reduction.report,
    )
    return status
