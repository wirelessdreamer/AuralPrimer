"""
``guitar_auto`` — split-aware guitar transcription orchestrator.

Routes a guitar stem through the deterministic lead/rhythm splitter
(``aural_ingest.guitar_split.split_lead_rhythm_guitar_stem``) and picks
a per-shape algorithm on each side:

  * lead    -> ``melodic_torchcrepe`` (monophonic, octave-clean)
  * rhythm  -> ``guitar_basic_pitch_playable`` if present, else
               ``melodic_basic_pitch`` (polyphonic)

Optional Phase 3/4 post-processors (``guitar_cleanup``,
``guitar_chord_supplement``) are imported lazily and skipped without
error when they are not merged into this branch yet.

Fallback ladder (each step is opt-in, not a silent quality-loss):

  * torchcrepe unavailable / errors -> ``melodic_combined``
  * basic_pitch model absent        -> ``melodic_combined``
  * split fails                     -> whole-stem ``melodic_combined``

``guitar_auto`` is registered as an opt-in research algorithm. It is
NOT the production default for guitar and does not appear in
``gameplay_default`` / ``fidelity_midi`` — routing is set from the
outside (profile or ``--algorithm guitar_auto`` on ``gt-benchmark``).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Callable

from aural_ingest.transcription import MelodicNote


def _transcribe_lead(
    stem_path: Path,
    *,
    instrument: str,
) -> list[MelodicNote]:
    """Lead channel: monophonic-first via torchcrepe, then combined."""
    try:
        from aural_ingest.algorithms import melodic_torchcrepe

        return list(melodic_torchcrepe.transcribe(stem_path, instrument=instrument))
    except Exception:
        pass

    from aural_ingest.algorithms import melodic_combined

    return list(melodic_combined.transcribe(stem_path, instrument=instrument))


def _transcribe_rhythm(
    stem_path: Path,
    *,
    instrument: str,
) -> list[MelodicNote]:
    """Rhythm channel: polyphonic-first via basic_pitch playable, then combined.

    The playable wrapper lives in a sibling Phase 2 branch and may not
    be importable on ``main`` yet — attempt it under lazy import and
    fall through to the shipped ``melodic_basic_pitch``.
    """
    try:
        from aural_ingest.algorithms import guitar_basic_pitch_playable  # type: ignore[attr-defined]

        return list(
            guitar_basic_pitch_playable.transcribe(stem_path, instrument=instrument)
        )
    except (ImportError, AttributeError, KeyError):
        pass
    except Exception:
        # Registered but ran and errored — keep going down the ladder.
        pass

    try:
        from aural_ingest.algorithms import melodic_basic_pitch

        notes = list(
            melodic_basic_pitch.transcribe(
                stem_path,
                instrument=instrument,
                allow_fallback=False,
            )
        )
        if notes:
            return notes
    except Exception:
        pass

    from aural_ingest.algorithms import melodic_combined

    return list(melodic_combined.transcribe(stem_path, instrument=instrument))


def _apply_optional_chord_supplement(
    notes: list[MelodicNote],
    stem_path: Path,
    *,
    instrument: str,
) -> list[MelodicNote]:
    """Phase 4 rhythm chord supplement. Skip cleanly when the module is absent."""
    try:
        from aural_ingest import guitar_chord_supplement  # type: ignore[attr-defined]
    except ImportError:
        return notes

    supplement: Callable[..., list[MelodicNote]] | None = getattr(
        guitar_chord_supplement, "supplement_rhythm_guitar", None
    )
    if supplement is None:
        return notes

    try:
        result = supplement(notes, stem_path=stem_path, instrument=instrument)
    except TypeError:
        try:
            result = supplement(notes, stem_path, instrument)
        except Exception:
            return notes
    except Exception:
        return notes
    return list(result) if result is not None else notes


def _apply_optional_cleanup(
    notes: list[MelodicNote],
    stem_path: Path,
    *,
    role: str,
    instrument: str,
) -> list[MelodicNote]:
    """Phase 3 guitar-specific gated cleanup. Skip cleanly when absent."""
    try:
        from aural_ingest import guitar_cleanup  # type: ignore[attr-defined]
    except ImportError:
        return notes

    cleanup: Callable[..., list[MelodicNote]] | None = getattr(
        guitar_cleanup, "apply_guitar_cleanup", None
    )
    if cleanup is None:
        return notes

    try:
        result = cleanup(
            notes,
            stem_path=stem_path,
            instrument=instrument,
            role=role,
        )
    except TypeError:
        try:
            result = cleanup(notes, role=role)
        except Exception:
            return notes
    except Exception:
        return notes
    return list(result) if result is not None else notes


def _merge_streams(
    lead_notes: list[MelodicNote],
    rhythm_notes: list[MelodicNote],
) -> list[MelodicNote]:
    """Concatenate + stable sort by (t_on, pitch, t_off).

    No dedup here — Phase 3 ``guitar_cleanup`` owns overlap/dup logic
    when it lands. Sorting is a merge of two already-sorted streams
    when the underlying algorithms sort their output, but we don't
    rely on that.
    """
    merged = list(lead_notes) + list(rhythm_notes)
    merged.sort(key=lambda n: (float(n.t_on), int(n.pitch), float(n.t_off)))
    return merged


def _whole_stem_fallback(
    stem_path: Path,
    *,
    instrument: str,
) -> list[MelodicNote]:
    """Split failed. Fall back to the shipped combined algorithm on the
    unsplit stem so we still emit something usable.
    """
    from aural_ingest.algorithms import melodic_combined

    return list(melodic_combined.transcribe(stem_path, instrument=instrument))


def transcribe(
    stem_path: Path,
    *,
    instrument: str = "melodic",
    keep_split_artifacts: bool = False,
    **_ignored,
) -> list[MelodicNote]:
    """Orchestrate a lead+rhythm split guitar transcription.

    ``keep_split_artifacts`` is a debug knob; when ``True`` the temp
    ``lead.wav`` / ``rhythm.wav`` are left on disk under
    ``<stem>.guitar_auto.d`` next to the source stem. Default is
    ``False`` — the temp dir is cleaned up on both success and failure.
    """
    src = Path(stem_path)
    if not src.is_file():
        return _whole_stem_fallback(src, instrument=instrument)

    # Lazy import so the module is testable without the guitar_split
    # side effects when the split is mocked.
    from aural_ingest.guitar_split import split_lead_rhythm_guitar_stem

    if keep_split_artifacts:
        split_dir = src.with_suffix(src.suffix + ".guitar_auto.d")
        split_dir.mkdir(parents=True, exist_ok=True)
        cleanup_dir = None
    else:
        split_dir = Path(tempfile.mkdtemp(prefix="guitar_auto_"))
        cleanup_dir = split_dir

    lead_wav = split_dir / "lead.wav"
    rhythm_wav = split_dir / "rhythm.wav"

    try:
        try:
            split_lead_rhythm_guitar_stem(src, lead_wav, rhythm_wav)
        except Exception:
            return _whole_stem_fallback(src, instrument=instrument)

        lead_notes = _transcribe_lead(lead_wav, instrument=instrument)
        rhythm_notes = _transcribe_rhythm(rhythm_wav, instrument=instrument)

        lead_notes = _apply_optional_cleanup(
            lead_notes, lead_wav, role="lead", instrument=instrument
        )
        rhythm_notes = _apply_optional_chord_supplement(
            rhythm_notes, rhythm_wav, instrument=instrument
        )
        rhythm_notes = _apply_optional_cleanup(
            rhythm_notes, rhythm_wav, role="rhythm", instrument=instrument
        )

        return _merge_streams(lead_notes, rhythm_notes)
    finally:
        if cleanup_dir is not None:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
