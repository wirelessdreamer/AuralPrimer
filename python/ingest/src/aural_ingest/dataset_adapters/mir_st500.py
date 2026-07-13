"""MIR-ST500 singing-transcription adapter.

MIR-ST500 publishes note-level lead-vocal annotations in
``MIR-ST500_corrected.json``. The upstream prep scripts download songs into
``train/<song_id>/Mixture.mp3`` and ``test/<song_id>/Mixture.mp3``; after vocal
separation, each song directory contains ``Vocal.wav``. This adapter targets
that separated-vocal file by default and emits ``GroundTruthCase`` objects with
``instrument="vocals"`` so the melodic benchmark uses the vocals-tuned chain.

Reference: https://github.com/york135/singing_transcription_ICASSP2021
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable, Iterator

from aural_ingest.transcription import MelodicNote

from .common import GroundTruthCase


_ANNOTATION_JSON = "MIR-ST500_corrected.json"
_METADATA_CSV = "metadata.csv"
_DATASET_DIR = "MIR-ST500_20210206"

_AUDIO_FILENAMES: dict[str, tuple[str, ...]] = {
    "vocal": ("Vocal.wav", "Vocals.wav", "vocal.wav", "vocals.wav"),
    "mixture": ("Mixture.wav", "Mixture.mp3", "mixture.wav", "mix.wav"),
}


def _case_id(song_id: int) -> str:
    return f"mir_st500:{song_id:03d}"


def _song_split(song_id: int) -> str:
    return "train" if song_id <= 400 else "test"


def _split_matches(song_id: int, split: str | None) -> bool:
    if split is None or split == "all":
        return True
    return _song_split(song_id) == split


def _resolve_annotation_json(corpus_root: Path) -> Path | None:
    for candidate in _annotation_json_candidates(corpus_root):
        if candidate.is_file():
            return candidate
    return None


def _annotation_json_candidates(corpus_root: Path) -> tuple[Path, ...]:
    return (
        corpus_root / _ANNOTATION_JSON,
        corpus_root / _DATASET_DIR / _ANNOTATION_JSON,
    )


def _resolve_metadata_csv(corpus_root: Path) -> Path | None:
    for candidate in (
        corpus_root / _METADATA_CSV,
        corpus_root / _DATASET_DIR / _METADATA_CSV,
    ):
        if candidate.is_file():
            return candidate
    return None


def _load_metadata(corpus_root: Path) -> dict[int, dict[str, str]]:
    metadata_csv = _resolve_metadata_csv(corpus_root)
    if metadata_csv is None:
        return {}
    out: dict[int, dict[str, str]] = {}
    with metadata_csv.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                song_id = int(row.get("song_id") or "")
            except ValueError:
                continue
            out[song_id] = {
                key: value
                for key, value in row.items()
                if key != "song_id" and value is not None
            }
    return out


def _audio_search_roots(corpus_root: Path, song_id: int) -> tuple[Path, ...]:
    split = _song_split(song_id)
    names = (str(song_id), f"{song_id:03d}")
    roots: list[Path] = []
    base_roots = [corpus_root]
    if corpus_root.name == _DATASET_DIR:
        base_roots.append(corpus_root.parent)
    for base in base_roots:
        roots.extend(base / split / name for name in names)
        roots.extend(base / name for name in names)
    return tuple(dict.fromkeys(roots))


def _find_audio_path(corpus_root: Path, song_id: int, *, variant: str) -> Path | None:
    filenames = _AUDIO_FILENAMES.get(variant)
    if filenames is None:
        raise ValueError(
            f"unknown MIR-ST500 variant: {variant!r}; expected one of {sorted(_AUDIO_FILENAMES)}"
        )
    for root in _audio_search_roots(corpus_root, song_id):
        for filename in filenames:
            candidate = root / filename
            if candidate.is_file():
                return candidate
    return None


def diagnose_corpus(
    corpus_root: Path,
    *,
    split: str | None = "test",
    variant: str = "vocal",
    case_ids: Iterable[str] | None = None,
    limit: int | None = None,
) -> dict[str, object]:
    """Return discovery diagnostics for a prepared MIR-ST500 corpus."""
    root = Path(corpus_root)
    case_id_set = set(case_ids) if case_ids is not None else None
    report: dict[str, object] = {
        "corpus_root": str(root),
        "exists": root.exists(),
        "is_dir": root.is_dir(),
        "split": split,
        "variant": variant,
        "supported_variants": sorted(_AUDIO_FILENAMES),
        "limit": limit,
        "case_id_filter_count": len(case_id_set) if case_id_set is not None else None,
        "annotation_candidates": [str(path) for path in _annotation_json_candidates(root)],
        "annotation_json": None,
        "annotation_count": 0,
        "split_annotation_count": 0,
        "case_id_match_count": 0,
        "audio_found_count": 0,
        "notes_found_count": 0,
        "emitted_count": 0,
        "missing_audio_examples": [],
        "empty_note_examples": [],
        "reason": None,
    }
    if variant not in _AUDIO_FILENAMES:
        report["reason"] = f"unknown MIR-ST500 variant {variant!r}; expected one of {sorted(_AUDIO_FILENAMES)}"
        return report
    if not root.exists():
        report["reason"] = f"corpus root does not exist: {root}"
        return report
    if not root.is_dir():
        report["reason"] = f"corpus root is not a directory: {root}"
        return report

    annotation_json = _resolve_annotation_json(root)
    if annotation_json is None:
        report["reason"] = (
            f"missing {_ANNOTATION_JSON}; expected it at one of "
            + ", ".join(str(path) for path in _annotation_json_candidates(root))
        )
        return report
    report["annotation_json"] = str(annotation_json)

    try:
        payload = json.loads(annotation_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report["reason"] = f"could not read {annotation_json}: {exc}"
        return report
    if not isinstance(payload, dict):
        report["reason"] = f"{annotation_json} must contain a JSON object of song id to notes"
        return report

    report["annotation_count"] = len(payload)
    missing_audio_examples: list[str] = []
    empty_note_examples: list[str] = []
    emitted = 0
    split_count = 0
    case_id_match_count = 0
    audio_found_count = 0
    notes_found_count = 0

    def sort_key(item: object) -> tuple[int, str]:
        try:
            return int(str(item)), str(item)
        except ValueError:
            return 999_999, str(item)

    for raw_song_id in sorted(payload, key=sort_key):
        try:
            song_id = int(raw_song_id)
        except ValueError:
            continue
        if not _split_matches(song_id, split):
            continue
        split_count += 1
        cid = _case_id(song_id)
        if case_id_set is not None and cid not in case_id_set:
            continue
        case_id_match_count += 1
        audio_path = _find_audio_path(root, song_id, variant=variant)
        if audio_path is None:
            if len(missing_audio_examples) < 5:
                searched = [
                    str(search_root / name)
                    for search_root in _audio_search_roots(root, song_id)
                    for name in _AUDIO_FILENAMES[variant]
                ]
                missing_audio_examples.append(f"{cid}: searched {searched[:6]}")
            continue
        audio_found_count += 1
        notes = _parse_notes(payload.get(raw_song_id))
        if not notes:
            if len(empty_note_examples) < 5:
                empty_note_examples.append(cid)
            continue
        notes_found_count += 1
        emitted += 1
        if limit is not None and emitted >= limit:
            break

    report["split_annotation_count"] = split_count
    report["case_id_match_count"] = case_id_match_count
    report["audio_found_count"] = audio_found_count
    report["notes_found_count"] = notes_found_count
    report["emitted_count"] = emitted
    report["missing_audio_examples"] = missing_audio_examples
    report["empty_note_examples"] = empty_note_examples

    if emitted > 0:
        report["reason"] = None
    elif split_count == 0:
        report["reason"] = f"no annotations matched split={split!r}"
    elif case_id_match_count == 0:
        report["reason"] = "no annotations matched the case id filter"
    elif audio_found_count == 0:
        report["reason"] = (
            f"no {variant} audio files found for matched annotations; expected filenames "
            f"{list(_AUDIO_FILENAMES[variant])}"
        )
    elif notes_found_count == 0:
        report["reason"] = "matched annotations had no valid vocal notes"
    else:
        report["reason"] = "no cases emitted"
    return report


def _parse_notes(raw_notes: object) -> tuple[MelodicNote, ...]:
    notes: list[MelodicNote] = []
    if not isinstance(raw_notes, list):
        return ()
    for raw in raw_notes:
        if not isinstance(raw, (list, tuple)) or len(raw) < 3:
            continue
        try:
            t_on = float(raw[0])
            t_off = float(raw[1])
            pitch = int(round(float(raw[2])))
        except (TypeError, ValueError):
            continue
        if t_off <= t_on:
            continue
        notes.append(
            MelodicNote(
                t_on=t_on,
                t_off=t_off,
                pitch=max(0, min(127, pitch)),
                velocity=100,
                instrument="vocals",
            )
        )
    notes.sort(key=lambda note: (note.t_on, note.pitch))
    return tuple(notes)


def yield_cases(
    corpus_root: Path,
    *,
    split: str | None = "test",
    variant: str = "vocal",
    case_ids: Iterable[str] | None = None,
    limit: int | None = None,
) -> Iterator[GroundTruthCase]:
    """Yield ``GroundTruthCase``s from a prepared MIR-ST500 corpus.

    Parameters
    ----------
    corpus_root
        Directory containing ``MIR-ST500_20210206/MIR-ST500_corrected.json``
        plus ``train/<id>/Vocal.wav`` and/or ``test/<id>/Vocal.wav``. Passing
        the ``MIR-ST500_20210206`` annotation directory itself is also accepted.
    split
        ``"test"`` (default), ``"train"``, ``"all"``, or ``None``.
    variant
        ``"vocal"`` (default, separated ``Vocal.wav``) or ``"mixture"``.
    case_ids
        Optional allow-list of canonical case ids such as ``mir_st500:401``.
        Applied before ``limit`` so stratified samples are stable.
    limit
        Stop after this many emitted cases.
    """
    annotation_json = _resolve_annotation_json(corpus_root)
    if annotation_json is None:
        return

    payload = json.loads(annotation_json.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return

    case_id_set = set(case_ids) if case_ids is not None else None
    metadata_by_id = _load_metadata(corpus_root)
    count = 0

    def sort_key(item: object) -> tuple[int, str]:
        try:
            return int(str(item)), str(item)
        except ValueError:
            return 999_999, str(item)

    for raw_song_id in sorted(payload, key=sort_key):
        try:
            song_id = int(raw_song_id)
        except ValueError:
            continue
        cid = _case_id(song_id)
        if case_id_set is not None and cid not in case_id_set:
            continue
        if not _split_matches(song_id, split):
            continue

        audio_path = _find_audio_path(corpus_root, song_id, variant=variant)
        if audio_path is None:
            continue
        notes = _parse_notes(payload.get(raw_song_id))
        if not notes:
            continue

        meta = {
            "split": _song_split(song_id),
            "song_id": str(song_id),
            "signal": variant,
        }
        meta.update(metadata_by_id.get(song_id, {}))
        duration_sec = max(note.t_off for note in notes)

        yield GroundTruthCase(
            case_id=cid,
            instrument="vocals",
            audio_path=audio_path,
            duration_sec=duration_sec,
            melodic_notes=notes,
            metadata=meta,
        )
        count += 1
        if limit is not None and count >= limit:
            return
