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
keys palette is:

    - ``basic_pitch``       — ``piano_basic_pitch_playable`` (sparse)
    - ``basic_pitch_dense`` — ``piano_basic_pitch_clean`` (denser)
    - ``ensemble``          — ``piano_ensemble`` (basic-pitch + LH/RH split)
    - ``d3rm``              — ``piano_d3rm`` (diffusion; needs a checkpoint)
    - ``pti``               — ``piano_pti`` (needs piano_transcription_inference)

The candidate set is DYNAMIC: an algorithm that raises (missing
checkpoint / optional dependency — ``d3rm`` and ``pti`` today) is
*omitted* from the candidate set rather than included as an empty
candidate. The set therefore contains only the algorithms that actually
produced a transcription (1..5 candidates).

Regions: fixed 4-second windows for v1. v2 can switch to beat-aligned
windows once we have a stable beat grid available here.

Hot-spot heuristics: ``low_confidence`` based on inter-candidate
disagreement. v2 will add ``octave_ghost`` / ``off_chord`` /
``density_outlier`` detection.

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

from .transcription import MelodicNote


logger = logging.getLogger(__name__)

SCHEMA_VERSION = "0.1.0"
REGION_DURATION_SEC = 4.0

# Candidate palette: each entry is a genuinely-different transcription
# ALGORITHM (not a post-processing variant of one model). Tuple order is
# (candidate_id, label, color, algo_module). Display order is the tuple
# order; ties in auto-pick scoring break toward earlier entries. The
# ``algo`` is the ``aural_ingest.algorithms.<algo>`` module whose
# ``transcribe(stem_path, instrument=...)`` produces the candidate.
#
# The set is dynamic at runtime: algorithms that raise (missing
# checkpoint / optional dep) are omitted (see ``run_algorithm_candidates``).
CANDIDATE_DISPLAY: list[tuple[str, str, str, str]] = [
    ("basic_pitch", "Basic Pitch", "#7c8db5", "piano_basic_pitch_playable"),
    ("basic_pitch_dense", "Basic Pitch (dense)", "#3aa2dc", "piano_basic_pitch_clean"),
    ("ensemble", "Ensemble", "#21c089", "piano_ensemble"),
    ("d3rm", "D3RM", "#d27a3c", "piano_d3rm"),
    ("pti", "Piano PTI", "#a06bd4", "piano_pti"),
]

CANDIDATE_IDS: list[str] = [cid for cid, _, _, _ in CANDIDATE_DISPLAY]

# candidate_id -> algorithm module name, in display order.
CANDIDATE_ALGOS: dict[str, str] = {cid: algo for cid, _, _, algo in CANDIDATE_DISPLAY}

VALID_INSTRUMENTS: frozenset[str] = frozenset(
    {"keys", "bass", "lead_guitar", "rhythm_guitar", "drums", "melodic"}
)


# ---------------------------------------------------------------------------
# Pure helpers (no PTI dependency; unit-testable without GPU / checkpoint).
# ---------------------------------------------------------------------------


def pipeline_signature() -> str:
    """Stable 16-char hash of the algorithm parameters + schema version.

    The Studio uses this to detect when a re-precompute has invalidated
    a stored refinement's ``auto_picked`` -- if the signature changes,
    the user's old auto-pick may no longer be in the candidate set.
    """
    payload = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "region_duration_sec": REGION_DURATION_SEC,
            "candidates": CANDIDATE_DISPLAY,
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
    return {
        "t_on": float(n.t_on),
        "t_off": float(t_off),
        "pitch": int(n.pitch),
        "velocity": int(max(1, min(127, n.velocity))),
    }


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
) -> list[MelodicNote]:
    """Dedup'd "union" set across all candidates' notes.

    Two notes are considered the same when within ``onset_tol_sec`` and
    pitch-exact. The earlier candidate (per ``CANDIDATE_DISPLAY`` order)
    wins for the kept representative.
    """
    seen: list[MelodicNote] = []
    for cid in CANDIDATE_IDS:
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


def classify_hot_spot(scores: Mapping[str, float]) -> tuple[str, float]:
    """Classify a region's hot_spot_type + confidence from candidate scores.

    v1 heuristics:
        - 3+ candidates score >= 0.9     → "clean"
        - otherwise                       → "low_confidence"

    Confidence is the average score across all candidates, clamped to
    [0, 1]. v2 will distinguish octave_ghost / off_chord / density_outlier
    by looking at the actual notes -- this v1 only inspects scores, which
    is enough to pick a category, but not enough to discriminate the
    types of "wrong" within low_confidence.
    """
    vals = list(scores.values())
    if not vals:
        return ("low_confidence", 0.0)
    confidence = float(min(1.0, max(0.0, sum(vals) / len(vals))))
    high = sum(1 for v in vals if v >= 0.9)
    if high >= 3:
        return ("clean", confidence)
    return ("low_confidence", confidence)


def pick_auto_candidate(scores: Mapping[str, float]) -> str:
    """Highest-scoring candidate id; ties break toward earlier display order."""
    if not scores:
        return CANDIDATE_IDS[0]
    return max(
        scores.items(),
        # Negative index means lower-index candidates (earlier in
        # CANDIDATE_DISPLAY) win when their score equals a later candidate's.
        key=lambda kv: (kv[1], -CANDIDATE_IDS.index(kv[0])),
    )[0]


def build_regions(
    candidate_notes: Mapping[str, list[MelodicNote]],
    song_duration_sec: float,
    region_duration_sec: float = REGION_DURATION_SEC,
) -> list[dict[str, object]]:
    """Walk fixed-size windows, score each candidate, build region records.

    Pure -- takes the already-transcribed notes and emits the regions
    array shape from the refine_candidates schema. The orchestration
    function below combines this with the actual transcribe calls.
    """
    regions: list[dict[str, object]] = []
    if song_duration_sec <= 0:
        return regions
    n_regions = max(1, math.ceil(song_duration_sec / region_duration_sec))
    for i in range(n_regions):
        t_start = i * region_duration_sec
        t_end = min(song_duration_sec, t_start + region_duration_sec)
        if t_end <= t_start:
            continue
        in_region = {
            cid: notes_in_region(notes, t_start, t_end)
            for cid, notes in candidate_notes.items()
        }
        consensus_set = union_of_candidates(in_region)
        scores = {
            cid: score_candidate(notes, consensus_set)
            for cid, notes in in_region.items()
        }
        hot_spot_type, confidence = classify_hot_spot(scores)
        regions.append(
            {
                "id": f"r{i:04d}",
                "t_start": float(t_start),
                "t_end": float(t_end),
                "hot_spot_type": hot_spot_type,
                "confidence": float(confidence),
                "auto_picked": pick_auto_candidate(scores),
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
) -> dict[str, dict[str, object]]:
    """The ``$.candidates`` object for the given (dynamic) candidate set.

    ``candidate_ids`` selects which of ``CANDIDATE_DISPLAY`` to declare; it
    defaults to the full palette. Order follows ``CANDIDATE_DISPLAY``
    regardless of the order of ``candidate_ids``. The ``algo`` module name
    is recorded under ``params`` for provenance / re-precompute.
    """
    wanted = set(candidate_ids) if candidate_ids is not None else None
    return {
        cid: {"label": label, "color": color, "params": {"algo": algo}}
        for cid, label, color, algo in CANDIDATE_DISPLAY
        if wanted is None or cid in wanted
    }


def build_payload(
    *,
    instrument: str,
    candidate_notes: Mapping[str, list[MelodicNote]],
    song_duration_sec: float,
    computed_at: str | None = None,
    signature: str | None = None,
) -> dict[str, object]:
    """Assemble the full schema-compliant payload.

    Pure: takes the candidate notes + song duration, emits the JSON-ready
    dict matching ``refine_candidates.schema.json``. The declared
    ``candidates`` block reflects exactly the keys present in
    ``candidate_notes`` (the dynamic, omit-on-failure set), so every
    region's candidate ids reference a declared candidate. Used by the
    orchestration function below and by tests that mock the transcribe
    layer.
    """
    if instrument not in VALID_INSTRUMENTS:
        raise ValueError(f"unknown instrument: {instrument!r}")
    return {
        "version": SCHEMA_VERSION,
        "instrument": instrument,
        "computed_at": computed_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pipeline_signature": signature or pipeline_signature(),
        "song_duration_sec": float(song_duration_sec),
        "candidates": build_candidates_block(list(candidate_notes.keys())),
        "regions": build_regions(candidate_notes, song_duration_sec),
    }


# ---------------------------------------------------------------------------
# Orchestration -- dispatches each candidate to its distinct algorithm module.
# Imports the heavy deps lazily so tests of the pure helpers don't need them.
# ---------------------------------------------------------------------------


def _find_stem(auralsong_root: Path, instrument: str) -> Path | None:
    stem_dir = auralsong_root / "audio" / "stems"
    for ext in ("wav", "mp3", "ogg", "flac"):
        candidate = stem_dir / f"{instrument}.{ext}"
        if candidate.is_file():
            return candidate
    return None


def _find_mix(auralsong_root: Path) -> Path | None:
    audio_dir = auralsong_root / "audio"
    for ext in ("wav", "mp3", "ogg", "flac"):
        candidate = audio_dir / f"mix.{ext}"
        if candidate.is_file():
            return candidate
    return None


def run_algorithm_candidates(
    stem_path: Path, mix_path: Path | None, instrument: str
) -> dict[str, list[MelodicNote]]:
    """Dispatch each candidate to its distinct algorithm and collect notes.

    Each candidate_id in ``CANDIDATE_DISPLAY`` maps to an algorithm module
    under ``aural_ingest.algorithms``; we import it lazily and call its
    ``transcribe(stem_path, instrument=...)``. ``piano_ensemble`` also
    accepts ``onset_tolerance_sec``, which we leave at its default.

    An algorithm that raises (missing checkpoint / optional dependency —
    ``d3rm`` and ``pti`` today) is OMITTED from the returned map rather
    than included as an empty candidate; a one-line warning is logged per
    skipped algorithm. The result is therefore the DYNAMIC candidate set:
    only the algorithms that actually produced a transcription appear, in
    ``CANDIDATE_DISPLAY`` order.

    ``mix_path`` is accepted for algorithms that may want it in future, but
    the per-algorithm ``transcribe(stem_path, instrument=...)`` is the
    primary call.
    """
    out: dict[str, list[MelodicNote]] = {}
    for cid, _, _, algo in CANDIDATE_DISPLAY:
        try:
            module = importlib.import_module(f"aural_ingest.algorithms.{algo}")
            notes = list(module.transcribe(stem_path, instrument=instrument))
        except Exception as exc:  # pragma: no cover - depends on optional deps
            logger.warning(
                "refine candidate %r (algo %s) skipped: %s", cid, algo, exc
            )
            continue
        out[cid] = notes
    return out


CandidateRunner = Callable[
    [Path, "Path | None", str], Mapping[str, list[MelodicNote]]
]


def precompute_refine_candidates(
    *,
    auralsong_root: Path,
    instrument: str,
    stem_path: Path | None = None,
    mix_path: Path | None = None,
    runner: CandidateRunner | None = None,
) -> dict[str, object]:
    """End-to-end: locate stem/mix, run the algorithms, write the JSON payload.

    ``runner`` is an injectable hook so tests can substitute the
    transcribe layer without needing the real models. It matches
    ``run_algorithm_candidates``'s signature: ``(stem_path, mix_path,
    instrument) -> {candidate_id: list[MelodicNote]}``. Whatever candidate
    ids the runner returns become the dynamic candidate set for the
    payload (1..5 of ``CANDIDATE_IDS``).
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

    song_duration_sec = max(
        (n.t_off for notes in candidate_notes.values() for n in notes),
        default=REGION_DURATION_SEC,
    ) + 1.0

    payload = build_payload(
        instrument=instrument,
        candidate_notes=candidate_notes,
        song_duration_sec=song_duration_sec,
    )

    features_dir = auralsong_root / "features"
    features_dir.mkdir(parents=True, exist_ok=True)
    out_path = features_dir / f"refine_candidates.{instrument}.json"
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8"
    )
    return payload
