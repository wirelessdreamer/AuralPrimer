"""
Refine Candidates precompute stage.

Produces editor-time per-region candidate transcriptions for the Studio
Refine workspace. For each instrument we run the same source stem through
four distinct variants of the transcription pipeline; the workspace
shows the user a candidate palette per "hot spot" region of the song
and lets them pick which variant produces the best output for that
region.

The 4 candidates we compute (chord-snapped deferred to v2):
    - ``stem_only``         — PTI on the stem only, no mix gate
    - ``consensus_tight``   — PTI on stem + mix, 50 ms / 1 semitone tolerances
    - ``consensus_default`` — current production default (100 ms / 2 semi)
    - ``denoise_consensus`` — piano_denoise the stem first, then default consensus

Regions: fixed 4-second windows for v1. v2 can switch to beat-aligned
windows once we have a stable beat grid available here.

Hot-spot heuristics: ``low_confidence`` based on inter-candidate
disagreement. v2 will add ``octave_ghost`` / ``off_chord`` /
``density_outlier`` detection.

Scoring: each candidate's score within a region is the Jaccard agreement
of its (onset, pitch) pairs against the union of all four candidates'
notes in that region. The ``auto_picked`` candidate per region is the
highest score, with ties broken by candidate display order so stem_only
beats consensus_tight beats consensus_default beats denoise_consensus.

Output: ``features/refine_candidates.<instrument>.json`` matching
``packages/songpack/schemas/refine_candidates.schema.json``.

Editor-time only. The runtime game never reads this file; it only feeds
the Studio Refine workspace.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from .transcription import MelodicNote


SCHEMA_VERSION = "0.1.0"
REGION_DURATION_SEC = 4.0

# Visual identity for each candidate — matches the 5-swatch palette in
# the Refine prototype mockup (docs/refine-prototype/screen-mockup.html).
# Tuple order is the display order; ties in auto-pick scoring break
# toward earlier entries.
CANDIDATE_DISPLAY: list[tuple[str, str, str, dict[str, object]]] = [
    ("stem_only", "Stem only", "#7c8db5", {}),
    (
        "consensus_tight",
        "Consensus tight",
        "#3aa2dc",
        {"onset_tolerance_sec": 0.05, "pitch_tolerance_semitones": 1},
    ),
    (
        "consensus_default",
        "Consensus default",
        "#21c089",
        {"onset_tolerance_sec": 0.10, "pitch_tolerance_semitones": 2},
    ),
    (
        "denoise_consensus",
        "Denoise + consensus",
        "#d27a3c",
        {"denoise": True, "onset_tolerance_sec": 0.10, "pitch_tolerance_semitones": 2},
    ),
]

CANDIDATE_IDS: list[str] = [cid for cid, _, _, _ in CANDIDATE_DISPLAY]

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
        # Negative index means lower-index candidates (stem_only first)
        # win when their score equals a later candidate's.
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


def build_candidates_block() -> dict[str, dict[str, object]]:
    """The ``$.candidates`` object — same shape for every instrument."""
    return {
        cid: {"label": label, "color": color, "params": dict(params)}
        for cid, label, color, params in CANDIDATE_DISPLAY
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
    dict matching ``refine_candidates.schema.json``. Used by the
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
        "candidates": build_candidates_block(),
        "regions": build_regions(candidate_notes, song_duration_sec),
    }


# ---------------------------------------------------------------------------
# Orchestration -- wires the four candidate runners to the real PTI pipeline.
# Imports the heavy deps lazily so tests of the pure helpers don't need them.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateNotes:
    """Per-candidate result of a transcribe run."""

    stem_only: list[MelodicNote]
    consensus_tight: list[MelodicNote]
    consensus_default: list[MelodicNote]
    denoise_consensus: list[MelodicNote]

    def as_dict(self) -> dict[str, list[MelodicNote]]:
        return {
            "stem_only": list(self.stem_only),
            "consensus_tight": list(self.consensus_tight),
            "consensus_default": list(self.consensus_default),
            "denoise_consensus": list(self.denoise_consensus),
        }


def _find_stem(songpack_root: Path, instrument: str) -> Path | None:
    stem_dir = songpack_root / "audio" / "stems"
    for ext in ("wav", "mp3", "ogg", "flac"):
        candidate = stem_dir / f"{instrument}.{ext}"
        if candidate.is_file():
            return candidate
    return None


def _find_mix(songpack_root: Path) -> Path | None:
    audio_dir = songpack_root / "audio"
    for ext in ("wav", "mp3", "ogg", "flac"):
        candidate = audio_dir / f"mix.{ext}"
        if candidate.is_file():
            return candidate
    return None


def run_four_candidates(
    stem_path: Path, mix_path: Path | None, instrument: str
) -> CandidateNotes:
    """Run the four PTI variants and return their notes.

    Wraps the heavy imports so the pure helpers above stay loadable in
    environments without piano_transcription_inference installed
    (i.e. every test that doesn't actually need PTI).
    """
    # Lazy imports -- PTI + librosa are both heavy and optional in CI.
    from .algorithms.piano_denoise import maybe_denoised_stem
    from .algorithms.piano_pti import transcribe, transcribe_consensus

    stem_only = list(transcribe(stem_path, instrument=instrument))
    consensus_tight = list(
        transcribe_consensus(
            stem_path,
            instrument=instrument,
            mix_path=mix_path,
            onset_tolerance_sec=0.05,
            pitch_tolerance_semitones=1,
        )
    )
    consensus_default = list(
        transcribe_consensus(
            stem_path,
            instrument=instrument,
            mix_path=mix_path,
            onset_tolerance_sec=0.10,
            pitch_tolerance_semitones=2,
        )
    )
    with maybe_denoised_stem(stem_path) as denoised_path:
        denoise_consensus = list(
            transcribe_consensus(
                denoised_path,
                instrument=instrument,
                mix_path=mix_path,
                onset_tolerance_sec=0.10,
                pitch_tolerance_semitones=2,
            )
        )
    return CandidateNotes(
        stem_only=stem_only,
        consensus_tight=consensus_tight,
        consensus_default=consensus_default,
        denoise_consensus=denoise_consensus,
    )


def precompute_refine_candidates(
    *,
    songpack_root: Path,
    instrument: str,
    stem_path: Path | None = None,
    mix_path: Path | None = None,
    runner=None,
) -> dict[str, object]:
    """End-to-end: locate stem/mix, run 4 candidates, build payload, write JSON.

    ``runner`` is an injectable hook so tests can substitute the
    transcribe layer without needing PTI installed. It should match
    ``run_four_candidates``'s signature: ``(stem_path, mix_path,
    instrument) -> CandidateNotes``.
    """
    if instrument not in VALID_INSTRUMENTS:
        raise ValueError(f"unknown instrument: {instrument!r}")

    resolved_stem = stem_path if stem_path is not None else _find_stem(songpack_root, instrument)
    if resolved_stem is None or not resolved_stem.is_file():
        raise FileNotFoundError(
            f"no stem found for instrument={instrument!r} under "
            f"{songpack_root / 'audio' / 'stems'}"
        )

    resolved_mix = mix_path if mix_path is not None else _find_mix(songpack_root)

    runner = runner or run_four_candidates
    candidates = runner(resolved_stem, resolved_mix, instrument)
    candidate_notes = candidates.as_dict() if isinstance(candidates, CandidateNotes) else dict(candidates)

    song_duration_sec = max(
        (n.t_off for notes in candidate_notes.values() for n in notes),
        default=REGION_DURATION_SEC,
    ) + 1.0

    payload = build_payload(
        instrument=instrument,
        candidate_notes=candidate_notes,
        song_duration_sec=song_duration_sec,
    )

    features_dir = songpack_root / "features"
    features_dir.mkdir(parents=True, exist_ok=True)
    out_path = features_dir / f"refine_candidates.{instrument}.json"
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8"
    )
    return payload
