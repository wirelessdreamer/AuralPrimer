"""Test tightening torchcrepe fmax for bass to suppress second-harmonic
octave doubling. Compare F1 at 400 Hz (production), 250 Hz, and 200 Hz
fmax caps."""
import os
os.environ.setdefault("AURAL_TORCHCREPE_MODEL", "tiny")

import importlib
import math
from pathlib import Path
from aural_ingest.dataset_adapters.guitarset import yield_low_string_cases
from aural_ingest.transcription import INSTRUMENT_FREQ_RANGES, MelodicNote
from aural_ingest.algorithms import melodic_torchcrepe as _tc_module


def transcribe_with_fmax(audio_path: Path, fmax_hz: float) -> list[MelodicNote]:
    """Run torchcrepe with a monkey-patched bass fmax."""
    original = INSTRUMENT_FREQ_RANGES["bass"]
    INSTRUMENT_FREQ_RANGES["bass"] = (original[0], float(fmax_hz))
    try:
        return _tc_module.transcribe(audio_path, instrument="bass")
    finally:
        INSTRUMENT_FREQ_RANGES["bass"] = original


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
    for fmax in (400.0, 250.0, 200.0, 180.0, 165.0):
        tot = [0, 0, 0, 0, 0, 0]  # tp fp fn p r f1
        for case in cases:
            ref = list(case.melodic_notes)
            pred = transcribe_with_fmax(case.audio_path, fmax)
            tp, fp, fn, p, r, f1 = greedy_match(ref, pred)
            tot[0] += tp
            tot[1] += fp
            tot[2] += fn
            tot[3] += p
            tot[4] += r
            tot[5] += f1
        n = len(cases)
        # micro-avg P/R/F1 too
        micro_p = tot[0] / (tot[0] + tot[1]) if (tot[0] + tot[1]) else 0.0
        micro_r = tot[0] / (tot[0] + tot[2]) if (tot[0] + tot[2]) else 0.0
        micro_f1 = 2 * micro_p * micro_r / (micro_p + micro_r) if (micro_p + micro_r) else 0.0
        print(f"fmax={fmax:6.1f} Hz  macro P={tot[3]/n:.3f} R={tot[4]/n:.3f} F1={tot[5]/n:.3f}  micro P={micro_p:.3f} R={micro_r:.3f} F1={micro_f1:.3f}  tp={tot[0]} fp={tot[1]} fn={tot[2]}")


if __name__ == "__main__":
    main()
