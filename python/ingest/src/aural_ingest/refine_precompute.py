"""
Refine Candidates precompute stage.

Produces editor-time per-region candidate transcriptions for the Studio
Refine workspace. For each instrument we run the source stem through
several genuinely-different transcription ALGORITHMS; the workspace shows
the user a candidate palette per "hot spot" region of the song and lets
them pick which algorithm produces the best output for that region.

Each candidate is a distinct algorithm module under
``aural_ingest.algorithms`` invoked through the standard dispatch
(``transcribe(stem_path, instrument=...) -> list[MelodicNote]``). The
palette is PER-INSTRUMENT (see ``PALETTES`` below); the keys/melodic
palette is:

    - ``basic_pitch``       — ``piano_basic_pitch_playable`` (sparse)
    - ``basic_pitch_dense`` — ``piano_basic_pitch_clean`` (denser)
    - ``ensemble``          — ``piano_ensemble`` (basic-pitch + LH/RH split)
    - ``d3rm``              — ``piano_d3rm`` (diffusion; needs a checkpoint)
    - ``pti``               — ``piano_pti`` (needs piano_transcription_inference)

Non-keys roles get their own benchmark-selected palettes (from
``docs/research-ground-truth-benchmarks-2026-06-14.md``):

    - drums                       — ``librosa_superflux_dense``
    - bass                        — ``melodic_pyin_bass_strict``
    - guitar / *_guitar / rhythm  — ``melodic_combined_guitar``

The drums algorithm module returns ``DrumEvent`` (``.time``/``.note``)
rather than ``MelodicNote`` and its ``transcribe`` does not accept an
``instrument`` kwarg; the candidate runner adapts both shapes to
``MelodicNote`` so the region/scoring/serialization machinery is
role-agnostic.

The candidate set is DYNAMIC: an algorithm that raises (missing
checkpoint / optional dependency — ``d3rm`` and ``pti`` today) is
*omitted* from the candidate set rather than included as an empty
candidate. The set therefore contains only the algorithms that actually
produced a transcription (1..5 candidates).

Regions: beat-aligned when a stable beat grid is available (a
``beats.json`` artifact in the pack, or a ``beats`` argument): regions
group ``BEATS_PER_REGION`` beats so a region-level candidate 'apply'
stops splitting a musical phrase mid-note. Falls back to fixed
4-second windows when no usable beat grid is present. The region JSON
shape is identical either way (backward compatible).

Hot-spot heuristics: ``low_confidence`` (inter-candidate disagreement)
plus note-derived detectors — ``octave_ghost`` (a candidate emits a
pitch exactly ±12 from a consensus pitch at the same onset),
``off_chord`` (a pitch class outside the region's dominant pitch-class
set), ``density_outlier`` (one candidate's note count is a wild outlier
vs the others), and ``bass_gap`` (a bass region a candidate leaves empty
while others have content). ``clean`` when candidates broadly agree.

Scoring: each candidate's score within a region is the Jaccard agreement
of its (onset, pitch) pairs against the union of all candidates' notes in
that region. The ``auto_picked`` candidate per region is the
highest-scoring one (densest/best agreement); ties break toward earlier
display order.

Output: ``features/refine_candidates.<instrument>.json`` matching
``packages/auralsong/schemas/refine_candidates.schema.json``.

Editor-time only. The runtime game never reads this file; it only feeds
the Studio Refine workspace.

Per-instrument palette extension plan (v2)
------------------------------------------

The palette above is keys-specific. For drums, bass, and guitar the
equivalent palette would dispatch to different transcription families;
the 2026-06-14 ground-truth benchmark
(``docs/research-ground-truth-benchmarks-2026-06-14.md``) surfaced
``librosa_superflux_dense`` (drums), ``melodic_pyin_bass_strict``
(bass), and ``melodic_combined_guitar`` (guitar) as candidates to add to
those palettes when wired up.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .algorithms._common import gate_notes_by_local_energy
from .pack_paths import (
    pack_feature_dirname as pack_feature_dirname,  # re-export: keep existing imports working
    resolve_mix_path,
    resolve_stem_paths,
)
from .transcription import MelodicNote


logger = logging.getLogger(__name__)

SCHEMA_VERSION = "0.1.0"
REGION_DURATION_SEC = 4.0

# How many beats to group into one beat-aligned region. Four beats == one
# bar in common time, which keeps a region's candidate 'apply' from
# splitting a musical phrase mid-note. Only used when a stable beat grid is
# available; otherwise we fall back to REGION_DURATION_SEC windows.
BEATS_PER_REGION = 4


# ``pack_feature_dirname`` now lives in ``aural_ingest.pack_paths`` (CONTRACT
# C2 — it must also route ``.sloppak`` to ``aural``). It is re-exported at the
# top of this module so existing ``from .refine_precompute import
# pack_feature_dirname`` imports keep working.


# Candidate palette: each entry is a genuinely-different transcription
# ALGORITHM (not a post-processing variant of one model). Tuple order is
# (candidate_id, label, color, algo_module). Display order is the tuple
# order; ties in auto-pick scoring break toward earlier entries. The
# ``algo`` is the ``aural_ingest.algorithms.<algo>`` module whose
# ``transcribe(stem_path, instrument=...)`` produces the candidate.
#
# The set is dynamic at runtime: algorithms that raise (missing
# checkpoint / optional dep) are omitted (see ``run_algorithm_candidates``).
#
# ``CANDIDATE_DISPLAY`` is the keys/melodic palette and remains the default
# palette (the pure helpers below index ``CANDIDATE_IDS`` / ``CANDIDATE_DISPLAY``
# for display-order tie-breaks). Per-instrument palettes live in ``PALETTES``.
CANDIDATE_DISPLAY: list[tuple[str, str, str, str]] = [
    ("basic_pitch", "Basic Pitch", "#7c8db5", "piano_basic_pitch_playable"),
    ("basic_pitch_dense", "Basic Pitch (dense)", "#3aa2dc", "piano_basic_pitch_clean"),
    ("ensemble", "Ensemble", "#21c089", "piano_ensemble"),
    ("d3rm", "D3RM", "#d27a3c", "piano_d3rm"),
    ("pti", "Piano PTI", "#a06bd4", "piano_pti"),
]

# Per-instrument candidate palettes. Each is a list of the same
# (candidate_id, label, color, algo_module) tuples. Roles NOT listed here
# fall back to the keys palette (``CANDIDATE_DISPLAY``). The non-keys
# palettes wire the 2026-06-14 ground-truth-benchmark selections:
#   drums                        -> librosa_superflux_dense
#   bass                         -> melodic_pyin_bass_strict
#   guitar / *_guitar / rhythm   -> melodic_combined_guitar
#   vocals                       -> melodic_rmvpe
# Any candidate whose algorithm module errors / is missing is dropped, the
# same omit-on-failure behavior as d3rm / pti in the keys palette.
PALETTES: dict[str, list[tuple[str, str, str, str]]] = {
    "keys": CANDIDATE_DISPLAY,
    "melodic": CANDIDATE_DISPLAY,
    "drums": [
        ("superflux_dense", "SuperFlux (dense)", "#d27a3c", "librosa_superflux_dense"),
    ],
    "bass": [
        ("pyin_bass_strict", "pYIN Bass (strict)", "#3aa2dc", "melodic_pyin_bass_strict"),
    ],
    "guitar": [
        ("combined_guitar", "Combined Guitar", "#21c089", "melodic_combined_guitar"),
    ],
    "lead_guitar": [
        ("combined_guitar", "Combined Guitar", "#21c089", "melodic_combined_guitar"),
    ],
    "rhythm_guitar": [
        ("combined_guitar", "Combined Guitar", "#21c089", "melodic_combined_guitar"),
    ],
    "vocals": [
        ("rmvpe", "RMVPE", "#d06bc1", "melodic_rmvpe"),
    ],
}


def palette_for_instrument(instrument: str) -> list[tuple[str, str, str, str]]:
    """The candidate palette for a role, falling back to the keys palette.

    Backward compatible: ``keys`` and any unknown role resolve to
    ``CANDIDATE_DISPLAY`` (the historical keys-only palette), so existing
    keys packs precompute identically.
    """
    return PALETTES.get(instrument, CANDIDATE_DISPLAY)


CANDIDATE_IDS: list[str] = [cid for cid, _, _, _ in CANDIDATE_DISPLAY]

# candidate_id -> algorithm module name, in display order.
CANDIDATE_ALGOS: dict[str, str] = {cid: algo for cid, _, _, algo in CANDIDATE_DISPLAY}

VALID_INSTRUMENTS: frozenset[str] = frozenset(
    {"keys", "bass", "guitar", "lead_guitar", "rhythm_guitar", "drums", "vocals", "melodic"}
)

# Roles whose transcription is bass-register content; used by the bass_gap
# hot-spot detector to decide whether an empty region is an error.
BASS_ROLES: frozenset[str] = frozenset({"bass"})

# The refine_candidates schema's ``instrument`` enum is
# {keys, bass, lead_guitar, rhythm_guitar, drums, melodic} -- it has no bare
# ``guitar``. When a caller precomputes over a raw ``guitar`` stem we emit the
# schema-valid ``rhythm_guitar`` (the more common role) so the JSON validates;
# stem lookup and palette resolution still use the original role.
SCHEMA_INSTRUMENT_ALIAS: dict[str, str] = {"guitar": "rhythm_guitar"}


def schema_instrument(instrument: str) -> str:
    """Map a role to the value written into the payload's ``instrument`` field.

    Identity for every schema-valid role; ``guitar`` -> ``rhythm_guitar`` so
    the emitted JSON matches ``refine_candidates.schema.json``'s enum.
    """
    return SCHEMA_INSTRUMENT_ALIAS.get(instrument, instrument)


# ---------------------------------------------------------------------------
# Pure helpers (no PTI dependency; unit-testable without GPU / checkpoint).
# ---------------------------------------------------------------------------


def pipeline_signature(
    palette: Sequence[tuple[str, str, str, str]] | None = None,
) -> str:
    """Stable 16-char hash of the algorithm parameters + schema version.

    The Studio uses this to detect when a re-precompute has invalidated
    a stored refinement's ``auto_picked`` -- if the signature changes,
    the user's old auto-pick may no longer be in the candidate set.

    ``palette`` defaults to the keys palette (``CANDIDATE_DISPLAY``); non-keys
    instruments pass their own palette so a bass/drums/guitar signature is
    distinct from (and stable within) each role. Keys packs hash identically
    to before (backward compatible).
    """
    pal = list(palette) if palette is not None else CANDIDATE_DISPLAY
    payload = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "region_duration_sec": REGION_DURATION_SEC,
            "candidates": pal,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def notes_in_region(
    notes: Sequence[MelodicNote], t_start: float, t_end: float
) -> list[MelodicNote]:
    """Notes whose ``t_on`` falls in [t_start, t_end)."""
    return [n for n in notes if t_start <= n.t_on < t_end]


def serialize_note(n: MelodicNote) -> dict[str, object]:
    """Match the refine_candidates schema's ``note`` shape exactly.

    The schema requires ``t_off`` strictly positive (exclusiveMinimum 0).
    Transcribers usually emit ``t_off > t_on`` already, but clamp here so
    pathological zero-duration emissions don't break schema validation.
    """
    t_off = max(n.t_off, n.t_on + 1e-3)
    out: dict[str, object] = {
        "t_on": float(n.t_on),
        "t_off": float(t_off),
        "pitch": int(n.pitch),
        "velocity": int(max(1, min(127, n.velocity))),
    }
    if n.string is not None and 0 <= int(n.string) <= 8:
        string_idx = int(n.string)
        out["string"] = string_idx
        out["s"] = string_idx
    if n.fret is not None and 0 <= int(n.fret) <= 36:
        fret = int(n.fret)
        out["fret"] = fret
        out["f"] = fret
    return out


def jaccard_overlap(
    a: Sequence[MelodicNote],
    b: Sequence[MelodicNote],
    onset_tol_sec: float = 0.05,
) -> float:
    """How well do two note lists agree on (onset, pitch)?

    Two notes "match" when their onsets are within ``onset_tol_sec`` and
    pitches agree exactly. Returns |intersection| / |union|; empty-vs-empty
    is treated as fully agreeing (1.0); empty-vs-non-empty is 0.0.

    O(|a|·|b|) but |a|, |b| are at most a few dozen per region.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    matched_b: set[int] = set()
    inter = 0
    for na in a:
        for j, nb in enumerate(b):
            if j in matched_b:
                continue
            if abs(na.t_on - nb.t_on) <= onset_tol_sec and na.pitch == nb.pitch:
                matched_b.add(j)
                inter += 1
                break
    union = len(a) + len(b) - inter
    return inter / union if union > 0 else 1.0


def union_of_candidates(
    candidate_notes: Mapping[str, Sequence[MelodicNote]],
    onset_tol_sec: float = 0.05,
    display_ids: Sequence[str] | None = None,
) -> list[MelodicNote]:
    """Dedup'd "union" set across all candidates' notes.

    Two notes are considered the same when within ``onset_tol_sec`` and
    pitch-exact. The earlier candidate (per display order) wins for the kept
    representative. ``display_ids`` supplies the display order; it defaults
    to the keys palette's ``CANDIDATE_IDS`` for backward compatibility, and
    any candidate present in ``candidate_notes`` but not in ``display_ids``
    is still folded in (appended after the ordered ids) so no notes are lost.
    """
    order = list(display_ids) if display_ids is not None else list(CANDIDATE_IDS)
    # Fold in any extra candidate ids not in the declared display order, so
    # a mismatched/short display list can never silently drop a candidate.
    order += [cid for cid in candidate_notes if cid not in order]
    seen: list[MelodicNote] = []
    for cid in order:
        if cid not in candidate_notes:
            continue
        for n in candidate_notes[cid]:
            is_dup = any(
                abs(s.t_on - n.t_on) <= onset_tol_sec and s.pitch == n.pitch
                for s in seen
            )
            if not is_dup:
                seen.append(n)
    return seen


def score_candidate(
    notes: Sequence[MelodicNote], consensus_set: Sequence[MelodicNote]
) -> float:
    """Jaccard agreement of one candidate's notes with the consensus union."""
    return jaccard_overlap(notes, consensus_set)


# ---------------------------------------------------------------------------
# Note-derived hot-spot detectors. Each is pure and unit-testable over
# synthetic note lists -- it inspects the ACTUAL candidate notes in a region
# (not just agreement scores), so the Studio 'Jump' dropdown can steer users
# to real error TYPES rather than a single generic "low_confidence" bucket.
#
# The returned strings match the refine_candidates schema's hot_spot_type
# enum: octave_ghost / off_chord / density_outlier / bass_gap / low_confidence
# / clean (/ manual, which only the user authors).
# ---------------------------------------------------------------------------

# A single accidental octave collision is noise (basic_pitch vs ensemble both
# hitting a bass octave is common and legitimate); require this many to flag.
OCTAVE_GHOST_MIN_HITS = 2
# Off-chord: fraction of a candidate's notes whose pitch class is outside the
# region's dominant pitch-class set that trips the detector.
OFF_CHORD_OUTLIER_FRACTION = 0.5
# Density outlier: a candidate's note count is this many times the median
# (or 1/this of it) to count as an outlier.
DENSITY_OUTLIER_RATIO = 3.0


def detect_octave_ghost(
    candidate_notes: Mapping[str, Sequence[MelodicNote]],
    onset_tol_sec: float = 0.05,
) -> bool:
    """One candidate emits a pitch exactly ±12 from another candidate's pitch
    at the same onset (a classic octave-doubling / octave-error 'ghost').

    Compares every unordered pair of candidates: a ghost is a note in one whose
    onset matches (within tolerance) a note in the other AND whose pitch is
    exactly an octave away. Requires ``OCTAVE_GHOST_MIN_HITS`` distinct such
    collisions so a single stray octave doubling doesn't flag the whole region.
    Unordered (j > i) so each physical collision is counted once, not twice.
    """
    ids = list(candidate_notes.keys())
    hits = 0
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a = candidate_notes[ids[i]]
            b = candidate_notes[ids[j]]
            for na in a:
                for nb in b:
                    if abs(na.t_on - nb.t_on) <= onset_tol_sec and (
                        abs(na.pitch - nb.pitch) == 12
                    ):
                        hits += 1
                        if hits >= OCTAVE_GHOST_MIN_HITS:
                            return True
    return False


def _dominant_pitch_classes(
    consensus: Sequence[MelodicNote], top_n: int = 4
) -> set[int]:
    """The region's dominant pitch classes (mod 12), by consensus frequency."""
    counts: dict[int, int] = {}
    for n in consensus:
        pc = int(n.pitch) % 12
        counts[pc] = counts.get(pc, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {pc for pc, _ in ranked[:top_n]}


def detect_off_chord(
    candidate_notes: Mapping[str, Sequence[MelodicNote]],
    consensus: Sequence[MelodicNote],
) -> bool:
    """A candidate plays notes whose pitch classes sit largely outside the
    region's dominant pitch-class set (a plausibly wrong / out-of-key read).

    Needs a consensus with real harmonic content (>= 3 notes) so a sparse
    region doesn't produce a meaningless 'dominant' set. Flags when any
    candidate has >= ``OFF_CHORD_OUTLIER_FRACTION`` of its notes off the set.

    Requires >= 2 candidates: with a single-candidate palette the consensus IS
    that candidate's own notes, so comparing it against itself would flag a
    diatonic run as off-chord. A single read has no disagreement to measure.
    """
    if len(candidate_notes) < 2:
        return False
    if len(consensus) < 3:
        return False
    dominant = _dominant_pitch_classes(consensus)
    if not dominant:
        return False
    for notes in candidate_notes.values():
        if not notes:
            continue
        off = sum(1 for n in notes if (int(n.pitch) % 12) not in dominant)
        if off / len(notes) >= OFF_CHORD_OUTLIER_FRACTION:
            return True
    return False


def detect_density_outlier(
    candidate_notes: Mapping[str, Sequence[MelodicNote]],
) -> bool:
    """One candidate's note count is a wild outlier vs the others in the region.

    Requires >= 2 non-empty candidates to have a baseline to compare against.
    Flags when any candidate's count is >= ``DENSITY_OUTLIER_RATIO`` * the
    median of the others (over-transcription) or <= median / ratio while the
    median is non-trivial (drop-out). Pure count comparison -- no audio.
    """
    counts = [len(list(notes)) for notes in candidate_notes.values()]
    non_empty = [c for c in counts if c > 0]
    if len(non_empty) < 2:
        return False
    for idx in range(len(counts)):
        others = counts[:idx] + counts[idx + 1 :]
        others = [c for c in others if c > 0]
        if not others:
            continue
        med = sorted(others)[len(others) // 2]
        if med <= 0:
            continue
        c = counts[idx]
        if c >= med * DENSITY_OUTLIER_RATIO:
            return True
        if med >= 4 and c <= med / DENSITY_OUTLIER_RATIO:
            return True
    return False


def detect_bass_gap(
    candidate_notes: Mapping[str, Sequence[MelodicNote]],
    instrument: str,
) -> bool:
    """A bass region where at least one candidate is empty while another has
    content -- a dropped bass note the user should fill from the fuller read.

    Only meaningful for bass roles; returns False otherwise. Requires at least
    two candidates so 'empty' is a disagreement, not the sole read.
    """
    if instrument not in BASS_ROLES:
        return False
    lens = [len(list(notes)) for notes in candidate_notes.values()]
    if len(lens) < 2:
        return False
    return any(c == 0 for c in lens) and any(c > 0 for c in lens)


def classify_hot_spot(
    scores: Mapping[str, float],
    candidate_notes: Mapping[str, Sequence[MelodicNote]] | None = None,
    consensus: Sequence[MelodicNote] | None = None,
    instrument: str = "keys",
) -> tuple[str, float]:
    """Classify a region's hot_spot_type + confidence.

    Confidence is the average candidate score, clamped to [0, 1].

    Category order of precedence (most specific first): when the actual notes
    are provided, the note-derived detectors run before the generic
    score-only bucket, so the Studio 'Jump' dropdown gets a real error TYPE:

        - bass_gap        (bass role, a candidate dropped the note)
        - octave_ghost    (±12 collision at the same onset)
        - off_chord       (out-of-key / off-dominant read)
        - density_outlier (one candidate wildly over/under-transcribes)
        - clean           (3+ candidates agree strongly)
        - low_confidence  (otherwise)

    When ``candidate_notes`` is omitted this reduces to the original
    score-only behavior (clean when 3+ candidates score >= 0.9, else
    low_confidence), so existing callers/tests are unaffected.
    """
    vals = list(scores.values())
    if not vals:
        return ("low_confidence", 0.0)
    confidence = float(min(1.0, max(0.0, sum(vals) / len(vals))))
    high = sum(1 for v in vals if v >= 0.9)

    if candidate_notes is not None:
        cons = consensus if consensus is not None else union_of_candidates(
            candidate_notes
        )
        # Hard, unambiguous errors flag even when candidates otherwise agree
        # strongly -- an octave ghost or a dropped bass note is a real thing
        # to fix regardless of overall Jaccard agreement. (Both self-guard to
        # >= 2 candidates, so neither fires on a single-candidate palette.)
        if detect_bass_gap(candidate_notes, instrument):
            return ("bass_gap", confidence)
        if detect_octave_ghost(candidate_notes):
            return ("octave_ghost", confidence)
        # Single-candidate palettes (drums/bass/guitar) carry no disagreement
        # signal: their self-Jaccard is always 1.0 so the score-based 'clean'
        # gate below (3+ agreeing candidates) is unreachable, and the softer
        # detectors would compare a candidate against itself. Treat the lone
        # read as clean rather than flagging every region as a hot spot.
        if len(candidate_notes) < 2:
            return ("clean", confidence)
        # A region where 3+ candidates agree strongly is clean; the softer
        # detectors below (which key off disagreement) would only misfire.
        if high >= 3:
            return ("clean", confidence)
        # Softer error types -- refine the generic low_confidence bucket into
        # an actionable sub-type when the notes reveal one.
        if detect_off_chord(candidate_notes, cons):
            return ("off_chord", confidence)
        if detect_density_outlier(candidate_notes):
            return ("density_outlier", confidence)
        return ("low_confidence", confidence)

    if high >= 3:
        return ("clean", confidence)
    return ("low_confidence", confidence)


def pick_auto_candidate(
    scores: Mapping[str, float], display_ids: Sequence[str] | None = None
) -> str:
    """Highest-scoring candidate id; ties break toward earlier display order.

    ``display_ids`` supplies the display order for the tie-break; it defaults
    to the keys palette's ``CANDIDATE_IDS``. A candidate scored but absent
    from ``display_ids`` sorts after all listed ids (still deterministic).
    """
    order = list(display_ids) if display_ids is not None else list(CANDIDATE_IDS)
    if not scores:
        return order[0] if order else CANDIDATE_IDS[0]

    def _rank(cid: str) -> int:
        # Lower-index (earlier display) candidates win ties; unknown ids
        # sort last but stay deterministic.
        return order.index(cid) if cid in order else len(order)

    return max(
        scores.items(),
        key=lambda kv: (kv[1], -_rank(kv[0])),
    )[0]


def _fixed_window_boundaries(
    song_duration_sec: float, region_duration_sec: float
) -> list[tuple[float, float]]:
    """Fixed ``region_duration_sec`` windows spanning [0, song_duration_sec)."""
    out: list[tuple[float, float]] = []
    if song_duration_sec <= 0:
        return out
    n_regions = max(1, math.ceil(song_duration_sec / region_duration_sec))
    for i in range(n_regions):
        t_start = i * region_duration_sec
        t_end = min(song_duration_sec, t_start + region_duration_sec)
        if t_end > t_start:
            out.append((float(t_start), float(t_end)))
    return out


def beat_boundaries(
    beats: Sequence[Mapping[str, object]],
    song_duration_sec: float,
    beats_per_region: int = BEATS_PER_REGION,
) -> list[tuple[float, float]]:
    """Region boundaries grouped from a beat grid, on bar/beat lines.

    ``beats`` is the ``beats`` array from a ``beats.json`` artifact: each row
    has a ``t`` (seconds) and ideally a ``beat`` index where ``beat == 0``
    marks a downbeat (bar start). We prefer to anchor regions on downbeats so
    a region is a whole bar (or ``beats_per_region``/4 bars); if downbeats are
    absent or too sparse we fall back to grouping every ``beats_per_region``
    raw beat times.

    Returns ``[]`` when the grid isn't usable (too few beats) so the caller
    falls back to fixed windows. Never emits zero/negative-length regions and
    always closes the final region at ``song_duration_sec``.
    """
    times: list[float] = []
    downbeats: list[float] = []
    for row in beats:
        if not isinstance(row, dict):
            continue
        t = row.get("t")
        try:
            tf = float(t)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if tf < 0:
            continue
        times.append(tf)
        if row.get("beat") == 0:
            downbeats.append(tf)
    times = sorted(set(times))
    if len(times) < 2:
        return []

    # Prefer downbeat-anchored regions: one region per bar (or per
    # beats_per_region/4 bars if beats_per_region is a multiple of 4).
    anchors: list[float]
    downbeats = sorted(set(db for db in downbeats if db >= 0))
    if len(downbeats) >= 2:
        bars_per_region = max(1, beats_per_region // 4)
        anchors = downbeats[::bars_per_region]
    else:
        # No usable downbeats: group every ``beats_per_region`` raw beats.
        anchors = times[::max(1, beats_per_region)]

    anchors = sorted(set(a for a in anchors if 0 <= a < song_duration_sec))
    if not anchors:
        return []
    # Ensure the timeline is covered from the very start.
    if anchors[0] > 1e-6:
        anchors.insert(0, 0.0)

    out: list[tuple[float, float]] = []
    for idx, start in enumerate(anchors):
        end = anchors[idx + 1] if idx + 1 < len(anchors) else song_duration_sec
        end = min(end, song_duration_sec)
        if end > start:
            out.append((float(start), float(end)))
    if len(out) < 2:
        # A single region means the grid gave us nothing to align to; let the
        # caller fall back to fixed windows for a more useful segmentation.
        return []
    return out


def build_regions(
    candidate_notes: Mapping[str, list[MelodicNote]],
    song_duration_sec: float,
    region_duration_sec: float = REGION_DURATION_SEC,
    *,
    beats: Sequence[Mapping[str, object]] | None = None,
    beats_per_region: int = BEATS_PER_REGION,
    display_ids: Sequence[str] | None = None,
    instrument: str = "keys",
) -> list[dict[str, object]]:
    """Score each candidate per region and build region records.

    Region boundaries are BEAT-ALIGNED when a usable ``beats`` grid is
    supplied (grouping ``beats_per_region`` beats / bars), so a region-level
    'apply' stops splitting a musical phrase mid-note. Falls back to fixed
    ``region_duration_sec`` windows when no beat grid is available or the grid
    is too sparse to be useful. The emitted region shape is identical in both
    cases (backward compatible).

    Pure -- takes the already-transcribed notes and emits the regions array
    shape from the refine_candidates schema. The orchestration function below
    combines this with the actual transcribe calls + beats loading.
    """
    regions: list[dict[str, object]] = []
    if song_duration_sec <= 0:
        return regions

    boundaries: list[tuple[float, float]] = []
    if beats:
        boundaries = beat_boundaries(beats, song_duration_sec, beats_per_region)
    if not boundaries:
        boundaries = _fixed_window_boundaries(song_duration_sec, region_duration_sec)

    for i, (t_start, t_end) in enumerate(boundaries):
        if t_end <= t_start:
            continue
        in_region = {
            cid: notes_in_region(notes, t_start, t_end)
            for cid, notes in candidate_notes.items()
        }
        consensus_set = union_of_candidates(in_region, display_ids=display_ids)
        scores = {
            cid: score_candidate(notes, consensus_set)
            for cid, notes in in_region.items()
        }
        hot_spot_type, confidence = classify_hot_spot(
            scores,
            candidate_notes=in_region,
            consensus=consensus_set,
            instrument=instrument,
        )
        regions.append(
            {
                "id": f"r{i:04d}",
                "t_start": float(t_start),
                "t_end": float(t_end),
                "hot_spot_type": hot_spot_type,
                "confidence": float(confidence),
                "auto_picked": pick_auto_candidate(scores, display_ids=display_ids),
                "candidate_scores": {cid: float(s) for cid, s in scores.items()},
                "candidate_notes": {
                    cid: [serialize_note(n) for n in notes]
                    for cid, notes in in_region.items()
                },
            }
        )
    return regions


def build_candidates_block(
    candidate_ids: Sequence[str] | None = None,
    palette: Sequence[tuple[str, str, str, str]] | None = None,
) -> dict[str, dict[str, object]]:
    """The ``$.candidates`` object for the given (dynamic) candidate set.

    ``candidate_ids`` selects which of ``palette`` to declare; it defaults to
    the whole palette. ``palette`` defaults to the keys palette
    (``CANDIDATE_DISPLAY``) for backward compatibility. Order follows the
    palette regardless of the order of ``candidate_ids``. The ``algo`` module
    name is recorded under ``params`` for provenance / re-precompute.
    """
    pal = list(palette) if palette is not None else CANDIDATE_DISPLAY
    wanted = set(candidate_ids) if candidate_ids is not None else None
    return {
        cid: {"label": label, "color": color, "params": {"algo": algo}}
        for cid, label, color, algo in pal
        if wanted is None or cid in wanted
    }


def build_payload(
    *,
    instrument: str,
    candidate_notes: Mapping[str, list[MelodicNote]],
    song_duration_sec: float,
    computed_at: str | None = None,
    signature: str | None = None,
    beats: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Assemble the full schema-compliant payload.

    Pure: takes the candidate notes + song duration (+ optional beat grid),
    emits the JSON-ready dict matching ``refine_candidates.schema.json``. The
    declared ``candidates`` block reflects exactly the keys present in
    ``candidate_notes`` (the dynamic, omit-on-failure set), declared from the
    instrument's palette so every region's candidate ids reference a declared
    candidate. When ``beats`` is supplied and usable, regions are beat-aligned;
    otherwise fixed 4 s windows (backward compatible). Used by the
    orchestration function below and by tests that mock the transcribe layer.
    """
    if instrument not in VALID_INSTRUMENTS:
        raise ValueError(f"unknown instrument: {instrument!r}")
    palette = palette_for_instrument(instrument)
    display_ids = [cid for cid, _, _, _ in palette]
    return {
        "version": SCHEMA_VERSION,
        "instrument": schema_instrument(instrument),
        "computed_at": computed_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pipeline_signature": signature or pipeline_signature(palette),
        "song_duration_sec": float(song_duration_sec),
        "candidates": build_candidates_block(
            list(candidate_notes.keys()), palette=palette
        ),
        "regions": build_regions(
            candidate_notes,
            song_duration_sec,
            beats=beats,
            display_ids=display_ids,
            instrument=instrument,
        ),
    }


# ---------------------------------------------------------------------------
# Orchestration -- dispatches each candidate to its distinct algorithm module.
# Imports the heavy deps lazily so tests of the pure helpers don't need them.
# ---------------------------------------------------------------------------


def _find_stem(auralsong_root: Path, instrument: str) -> Path | None:
    """Locate a role's stem, manifest-driven first (feedpak/sloppak), then glob.

    Delegates to ``pack_paths.resolve_stem_paths`` so a manifest-listed stem
    (any layout / extension, incl. sloppak ``stems/<role>.ogg``) is found; falls
    back to the historical ``audio/stems/<role>.{wav,mp3,ogg,flac}`` glob for a
    legacy pack with no usable manifest.
    """
    stems = resolve_stem_paths(auralsong_root)
    hit = stems.get(instrument)
    if hit is not None and hit.is_file():
        return hit
    # Belt-and-suspenders: the historical direct lookup, in case a role's stem
    # exists on disk but isn't declared in the manifest.
    stem_dir = auralsong_root / "audio" / "stems"
    for ext in ("wav", "mp3", "ogg", "flac"):
        candidate = stem_dir / f"{instrument}.{ext}"
        if candidate.is_file():
            return candidate
    return None


def _find_mix(auralsong_root: Path) -> Path | None:
    """Locate the pack's full mix, manifest-driven first, then legacy glob."""
    return resolve_mix_path(auralsong_root)


def _coerce_to_melodic_notes(
    raw: object, instrument: str
) -> list[MelodicNote]:
    """Normalize an algorithm's output to ``list[MelodicNote]``.

    Melodic algorithms already return ``MelodicNote`` (``.t_on``/``.t_off``/
    ``.pitch``). The drums algorithm returns ``DrumEvent`` (``.time``/``.note``
    /``.duration``); we adapt each event to a ``MelodicNote`` so the region /
    scoring / serialization machinery is role-agnostic. Anything else is
    skipped defensively.
    """
    notes: list[MelodicNote] = []
    for ev in raw:
        if isinstance(ev, MelodicNote):
            notes.append(ev)
            continue
        # DrumEvent-shaped: time / note / velocity / duration.
        t_on = getattr(ev, "time", None)
        pitch = getattr(ev, "note", None)
        if t_on is None or pitch is None:
            # Not a shape we understand; skip rather than crash the candidate.
            continue
        dur = getattr(ev, "duration", 0.05) or 0.05
        vel = getattr(ev, "velocity", 90)
        try:
            t_on_f = float(t_on)
            notes.append(
                MelodicNote(
                    t_on=t_on_f,
                    t_off=t_on_f + max(float(dur), 1e-3),
                    pitch=int(pitch),
                    velocity=int(vel),
                    instrument=instrument,
                )
            )
        except (TypeError, ValueError):
            continue
    return notes


def _call_algorithm_transcribe(
    module: object, stem_path: Path, instrument: str
) -> list[MelodicNote]:
    """Call a module's ``transcribe`` accommodating both call signatures.

    Melodic algorithms take ``transcribe(stem_path, instrument=...)``; the
    drums algorithm takes ``transcribe(stem_path)`` (no ``instrument`` kwarg)
    and returns ``DrumEvent`` objects. We try the instrument-aware call first
    and fall back to the positional-only call on ``TypeError``, then normalize
    the result to ``MelodicNote``.
    """
    try:
        raw = module.transcribe(stem_path, instrument=instrument)  # type: ignore[attr-defined]
    except TypeError:
        raw = module.transcribe(stem_path)  # type: ignore[attr-defined]
    return _coerce_to_melodic_notes(list(raw), instrument)


def run_algorithm_candidates(
    stem_path: Path, mix_path: Path | None, instrument: str
) -> dict[str, list[MelodicNote]]:
    """Dispatch each candidate to its distinct algorithm and collect notes.

    The palette is PER-INSTRUMENT (``palette_for_instrument``): each
    candidate_id maps to an algorithm module under ``aural_ingest.algorithms``
    which we import lazily and call via ``_call_algorithm_transcribe`` (which
    tolerates both the melodic ``transcribe(stem_path, instrument=...)`` and
    the drums ``transcribe(stem_path) -> list[DrumEvent]`` signatures, and
    normalizes drum events to ``MelodicNote``).

    An algorithm that raises (missing checkpoint / optional dependency —
    ``d3rm`` and ``pti`` in the keys palette today) is OMITTED from the
    returned map rather than included as an empty candidate; a one-line
    warning is logged per skipped algorithm. The result is therefore the
    DYNAMIC candidate set: only the algorithms that actually produced a
    transcription appear, in palette display order.

    ``mix_path`` is accepted for algorithms that may want it in future, but
    the per-algorithm ``transcribe`` is the primary call.
    """
    out: dict[str, list[MelodicNote]] = {}
    for cid, _, _, algo in palette_for_instrument(instrument):
        try:
            module = importlib.import_module(f"aural_ingest.algorithms.{algo}")
            notes = _call_algorithm_transcribe(module, stem_path, instrument)
        except Exception as exc:  # pragma: no cover - depends on optional deps
            logger.warning(
                "refine candidate %r (algo %s) skipped: %s", cid, algo, exc
            )
            continue
        # Drop phantom notes transcribed from bleed/noise in sections where the
        # instrument isn't playing (keeps sparse-but-real content like an intro).
        out[cid] = gate_notes_by_local_energy(notes, stem_path)
    return out


CandidateRunner = Callable[
    [Path, "Path | None", str], Mapping[str, list[MelodicNote]]
]


def _load_beats(auralsong_root: Path) -> list[dict[str, object]]:
    """Load the ``beats`` array from a pack's beats.json, or ``[]``.

    Checks both pack layouts defensively: ``features/beats.json`` (legacy
    ``.auralsong``) and ``aural/beats.json`` (``.feedpak``), plus the
    feature-dir resolved via ``pack_feature_dirname``. Any read / parse error
    yields ``[]`` so region building falls back to fixed windows -- beats are
    an optimization, never a hard dependency.
    """
    candidates = [
        auralsong_root / "features" / "beats.json",
        auralsong_root / pack_feature_dirname(auralsong_root) / "beats.json",
        auralsong_root / "aural" / "beats.json",
    ]
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if not path.is_file():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - beats are optional
            continue
        rows = doc.get("beats") if isinstance(doc, dict) else None
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    return []


def precompute_refine_candidates(
    *,
    auralsong_root: Path,
    instrument: str,
    stem_path: Path | None = None,
    mix_path: Path | None = None,
    runner: CandidateRunner | None = None,
    beats: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """End-to-end: locate stem/mix, run the algorithms, write the JSON payload.

    ``runner`` is an injectable hook so tests can substitute the
    transcribe layer without needing the real models. It matches
    ``run_algorithm_candidates``'s signature: ``(stem_path, mix_path,
    instrument) -> {candidate_id: list[MelodicNote]}``. Whatever candidate
    ids the runner returns become the dynamic candidate set for the
    payload (1..N of the instrument's palette).

    ``beats`` overrides the on-disk beat grid; when omitted we look for a
    ``beats.json`` artifact in the pack (see ``_load_beats``). When a usable
    grid is found regions are beat-aligned, otherwise fixed 4 s windows.
    """
    if instrument not in VALID_INSTRUMENTS:
        raise ValueError(f"unknown instrument: {instrument!r}")

    resolved_stem = stem_path if stem_path is not None else _find_stem(auralsong_root, instrument)
    if resolved_stem is None or not resolved_stem.is_file():
        raise FileNotFoundError(
            f"no stem found for instrument={instrument!r} under "
            f"{auralsong_root / 'audio' / 'stems'}"
        )

    resolved_mix = mix_path if mix_path is not None else _find_mix(auralsong_root)

    runner = runner or run_algorithm_candidates
    candidate_notes = dict(runner(resolved_stem, resolved_mix, instrument))

    if not candidate_notes:
        raise RuntimeError(
            f"no transcription candidates produced for instrument={instrument!r}; "
            "all algorithms were skipped (missing checkpoints / optional deps)"
        )

    # Every algorithm gated down to zero notes -> the stem has no audible
    # content for this instrument (e.g. a keys stem that's only bleed). Don't
    # write an empty candidates file the app would offer as a dead editor.
    if not any(candidate_notes.values()):
        raise RuntimeError(
            f"no audible content for instrument={instrument!r} after the silence "
            "gate; skipping candidates for this stem"
        )

    song_duration_sec = max(
        (n.t_off for notes in candidate_notes.values() for n in notes),
        default=REGION_DURATION_SEC,
    ) + 1.0

    resolved_beats = beats if beats is not None else _load_beats(auralsong_root)

    payload = build_payload(
        instrument=instrument,
        candidate_notes=candidate_notes,
        song_duration_sec=song_duration_sec,
        beats=resolved_beats,
    )

    out_root = auralsong_root / pack_feature_dirname(auralsong_root)
    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / f"refine_candidates.{instrument}.json"
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8"
    )
    return payload
