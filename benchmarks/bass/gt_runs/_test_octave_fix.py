"""Test a median-anchored octave correction on torchcrepe bass output.

Hypothesis: for a monophonic bass voice, if a large chunk of predicted notes
sits >= 12 semitones above the median predicted pitch, they are octave-up
harmonics. Pulling them down by 12 should recover the fundamental.
"""
from pathlib import Path
from collections import Counter
import statistics

from aural_ingest.dataset_adapters.guitarset import yield_low_string_cases
from aural_ingest.algorithms import melodic_torchcrepe
from aural_ingest.transcription import MelodicNote, score_transcription


def median_octave_collapse(notes: list[MelodicNote], *, allow_semitones_above_median: int = 7) -> list[MelodicNote]:
    """Collapse octave-up doublings for a monophonic voice.

    Anchor on the median predicted pitch. Any note whose pitch sits more
    than allow_semitones_above_median above the median is nudged down by
    12 semitones (recursively). Never nudges below MIDI 20 (E0-ish).
    """
    if len(notes) < 4:
        return notes
    pitches = sorted(n.pitch for n in notes)
    median_pitch = pitches[len(pitches) // 2]
    out = []
    for n in notes:
        p = int(n.pitch)
        while p - median_pitch > allow_semitones_above_median and p - 12 >= 20:
            p -= 12
        out.append(
            MelodicNote(t_on=n.t_on, t_off=n.t_off, pitch=p, velocity=n.velocity, instrument=n.instrument)
        )
    return out


def greedy_match(ref, pred, tol=0.05, pitch_tol=0):
    used = set()
    tp = 0
    for r in ref:
        best_i = None
        best_err = None
        for i, p in enumerate(pred):
            if i in used:
                continue
            if abs(p.t_on - r.t_on) > tol:
                continue
            if abs(p.pitch - r.pitch) > pitch_tol:
                continue
            err = abs(p.t_on - r.t_on)
            if best_err is None or err < best_err:
                best_err = err
                best_i = i
        if best_i is not None:
            used.add(best_i)
            tp += 1
    fp = len(pred) - len(used)
    fn = len(ref) - tp
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return tp, fp, fn, p, r, f1


def main():
    cases = list(
        yield_low_string_cases(
            Path(r"E:\AudioSourceOfTruthData\extracted\guitarset"),
            variant="hex_debleeded",
            limit=20,
        )
    )
    tot_raw = [0, 0, 0]
    tot_fix = [0, 0, 0]
    for case in cases:
        ref = list(case.melodic_notes)
        pred_raw = melodic_torchcrepe.transcribe(case.audio_path, instrument="bass")
        pred_fix = median_octave_collapse(pred_raw)
        _, _, _, p_r, r_r, f1_r = greedy_match(ref, pred_raw)
        _, _, _, p_f, r_f, f1_f = greedy_match(ref, pred_fix)
        tot_raw[0] += p_r
        tot_raw[1] += r_r
        tot_raw[2] += f1_r
        tot_fix[0] += p_f
        tot_fix[1] += r_f
        tot_fix[2] += f1_f
        print(f"{case.case_id[:60]:<60}  raw f1={f1_r:.3f}  fix f1={f1_f:.3f}  n_ref={len(ref)} n_raw={len(pred_raw)} n_fix={len(pred_fix)}")
    n = len(cases)
    print()
    print(f"mean over {n} cases:")
    print(f"  raw: P={tot_raw[0]/n:.3f} R={tot_raw[1]/n:.3f} F1={tot_raw[2]/n:.3f}")
    print(f"  fix: P={tot_fix[0]/n:.3f} R={tot_fix[1]/n:.3f} F1={tot_fix[2]/n:.3f}")


if __name__ == "__main__":
    main()
