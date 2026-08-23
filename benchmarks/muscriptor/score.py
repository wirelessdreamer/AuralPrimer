"""Score a MuScriptor whole-mix import against the authored Suno stem MIDI.

For each song we compare the notes MuScriptor produced (pack aural/notes.mid,
one track per role) against the ground-truth authored MIDI (the per-part *.mid
files in the song's Suno "Stems" folder). We report two things per instrument:

  * coverage  -- did MuScriptor emit ANY notes for a part the author authored?
  * note F1   -- mir_eval onset+pitch note F1 (onset tol 50ms, no offset/vel),
                 the standard AMT metric, so "recovered the right notes" is
                 measured, not just "emitted something".

Authored part name -> instrument bucket. FX is intentionally ignored (it is not
a playable instrument and MuScriptor has no matching role).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import pretty_midi
from mir_eval.transcription import precision_recall_f1_overlap as prf

# Some authored Suno stem MIDIs carry an out-of-range key signature (e.g. "17
# sharps"), which mido rejects by default and would abort the whole eval on a
# single bad ground-truth file. Make the key-signature decode lenient: an
# invalid key is irrelevant to note onset/pitch scoring, so fall back to C.
import mido.midifiles.meta as _mido_meta  # noqa: E402


class _LenientKeySig(dict):
    def __getitem__(self, k):
        try:
            return super().__getitem__(k)
        except KeyError:
            return "C"


_mido_meta._key_signature_decode = _LenientKeySig(_mido_meta._key_signature_decode)

ONSET_TOL = 0.05  # seconds; mir_eval default

# Authored Suno part -> our instrument bucket.
PART_TO_BUCKET = {
    "keyboard": "keys",
    "synth": "keys",
    "bass": "bass",
    "drums": "drums",
    "percussion": "drums",
    "vocals": "vocals",
    "backing vocals": "vocals",
    "lead vocals": "vocals",
    # FX -> ignored on purpose (no playable target)
}

# MuScriptor notes.mid track name -> bucket.
TRACK_TO_BUCKET = {
    "drums": "drums",
    "bass": "bass",
    "rhythm guitar": "keys_or_guitar",  # muscriptor funnels melodic here
    "lead guitar": "keys_or_guitar",
    "vocals": "vocals",
    "keys": "keys",
    "piano": "keys",
}


def _part_bucket(filename: str) -> str | None:
    m = re.search(r"\(([^)]+)\)\.mid$", filename, re.I)
    if not m:
        return None
    return PART_TO_BUCKET.get(m.group(1).strip().lower())


def _notes_from_midi(path: Path, drums: bool | None = None) -> np.ndarray:
    """Return an (N,2) array of [onset, pitch] plus offsets as (N,3)
    [onset, offset, pitch] for mir_eval. Merges all instruments in the file."""
    pm = pretty_midi.PrettyMIDI(str(path))
    rows = []
    for inst in pm.instruments:
        if drums is True and not inst.is_drum:
            continue
        if drums is False and inst.is_drum:
            continue
        for n in inst.notes:
            rows.append((n.start, max(n.end, n.start + 1e-3), n.pitch))
    if not rows:
        return np.zeros((0, 3))
    return np.array(sorted(rows))


def _score(ref: np.ndarray, est: np.ndarray) -> dict:
    if ref.shape[0] == 0:
        return {"ref_notes": 0, "est_notes": int(est.shape[0]), "f1": None}
    if est.shape[0] == 0:
        return {"ref_notes": int(ref.shape[0]), "est_notes": 0, "f1": 0.0,
                "precision": None, "recall": 0.0}
    # Pitch-only match (ignore offsets + velocity): the AMT-standard onset metric.
    p, r, f, _ = prf(
        ref[:, [0, 1]], ref[:, 2],
        est[:, [0, 1]], est[:, 2],
        onset_tolerance=ONSET_TOL,
        offset_ratio=None,
    )
    return {"ref_notes": int(ref.shape[0]), "est_notes": int(est.shape[0]),
            "precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4)}


def score_song(pack_notes_mid: Path, stems_dir: Path) -> dict:
    # Ground truth: bucket -> merged [onset, offset, pitch].
    gt: dict[str, list] = {}
    for mid in stems_dir.glob("*.mid"):
        bucket = _part_bucket(mid.name)
        if bucket is None:
            continue
        arr = _notes_from_midi(mid)
        if arr.shape[0]:
            gt.setdefault(bucket, []).append(arr)
    gt_merged = {b: np.concatenate(v) for b, v in gt.items()}

    # MuScriptor estimate: bucket -> merged notes from notes.mid tracks.
    est: dict[str, list] = {}
    pm = pretty_midi.PrettyMIDI(str(pack_notes_mid))
    for inst in pm.instruments:
        name = (inst.name or "").strip().lower()
        bucket = TRACK_TO_BUCKET.get(name)
        if bucket is None:
            continue
        # Fold muscriptor's guitar catch-all into keys for scoring against piano.
        eff = "keys" if bucket == "keys_or_guitar" else bucket
        rows = [(n.start, max(n.end, n.start + 1e-3), n.pitch) for n in inst.notes]
        if rows:
            est.setdefault(eff, []).append(np.array(sorted(rows)))
    est_merged = {b: np.concatenate(v) for b, v in est.items()}

    buckets = sorted(set(gt_merged) | set(est_merged))
    per_inst = {}
    for b in buckets:
        ref = gt_merged.get(b, np.zeros((0, 3)))
        e = est_merged.get(b, np.zeros((0, 3)))
        s = _score(ref, e)
        s["authored"] = b in gt_merged
        s["muscriptor_emitted"] = b in est_merged
        per_inst[b] = s
    return {"instruments": per_inst}


if __name__ == "__main__":
    pack_notes = Path(sys.argv[1])
    stems = Path(sys.argv[2])
    print(json.dumps(score_song(pack_notes, stems), indent=2))
