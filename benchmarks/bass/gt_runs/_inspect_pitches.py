"""Inspect torchcrepe vs pyin_bass_strict pitch histograms on a few bass cases."""
from pathlib import Path
from collections import Counter

from aural_ingest.dataset_adapters.guitarset import yield_low_string_cases
from aural_ingest.algorithms import melodic_torchcrepe, melodic_pyin_bass_strict


def summarize(name: str, notes):
    if not notes:
        print(f"  {name}: 0 notes")
        return
    lo = min(n.pitch for n in notes)
    hi = max(n.pitch for n in notes)
    hist = Counter(n.pitch for n in notes)
    top = sorted(hist.items(), key=lambda x: -x[1])[:8]
    print(f"  {name}: n={len(notes)} range={lo}-{hi} top_pitches={top}")


def octave_diff_count(ref_pitches, pred_pitches):
    """Count how many pred pitches sit exactly 12 semitones off any ref pitch."""
    ref_set = set(ref_pitches)
    off_by_12 = sum(1 for p in pred_pitches if p not in ref_set and ((p + 12) in ref_set or (p - 12) in ref_set))
    off_by_other = sum(1 for p in pred_pitches if p not in ref_set and (p + 12) not in ref_set and (p - 12) not in ref_set)
    exact = sum(1 for p in pred_pitches if p in ref_set)
    return exact, off_by_12, off_by_other


def main():
    cases = list(
        yield_low_string_cases(
            Path(r"E:\AudioSourceOfTruthData\extracted\guitarset"),
            variant="hex_debleeded",
            limit=5,
        )
    )
    for case in cases:
        print(f"\n=== {case.case_id} ===")
        ref = list(case.melodic_notes)
        summarize("ref", ref)
        pred_tc = melodic_torchcrepe.transcribe(case.audio_path, instrument="bass")
        pred_ps = melodic_pyin_bass_strict.transcribe(case.audio_path, instrument="bass")
        summarize("torchcrepe", pred_tc)
        summarize("pyin_bass_strict", pred_ps)
        ref_pitches = [n.pitch for n in ref]
        exact_tc, o12_tc, other_tc = octave_diff_count(ref_pitches, [n.pitch for n in pred_tc])
        exact_ps, o12_ps, other_ps = octave_diff_count(ref_pitches, [n.pitch for n in pred_ps])
        print(f"  pitch-only tally torchcrepe:      exact={exact_tc} off_by_12={o12_tc} other={other_tc}")
        print(f"  pitch-only tally pyin_bass_strict: exact={exact_ps} off_by_12={o12_ps} other={other_ps}")


if __name__ == "__main__":
    main()
