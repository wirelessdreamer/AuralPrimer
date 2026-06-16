"""Render the 4 piano ground-truth MIDI files to WAV via pretty_midi.synthesize.

Reads the .mid files in ``D:/AudioSourceOfTruth/data/piano_synthetic/`` and
writes companion .wav files in the same directory.

Timbre caveat: pretty_midi.synthesize uses additive sine bursts per
note, NOT a sampled piano. The rendered audio is unrealistic timbre.
This benchmark therefore measures a LOWER BOUND on production keys
accuracy -- real piano material with full harmonic content gives the
trained models (piano_pti, basic_pitch, etc.) more spectral information
to work with on the pitch-detection side, but also more competing
harmonics on the onset-detection side. The synthetic case provides a
controlled sanity check that the production pipeline is not
catastrophically broken on cleanly-isolated piano notes.

For a proper realistic-timbre keys benchmark, the round's documented
next step (adding MAESTRO to ``D:/AudioSourceOfTruth/data/maestro/``)
is still required.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pretty_midi
import soundfile as sf

CORPUS_DIR = Path("D:/AudioSourceOfTruth/data/piano_synthetic")
SAMPLE_RATE = 44100


def render_one(mid_path: Path) -> Path:
    pm = pretty_midi.PrettyMIDI(str(mid_path))
    audio = pm.synthesize(fs=SAMPLE_RATE)
    # Normalize to -1 dBFS to avoid clipping with the additive sine stack
    peak = float(abs(audio).max())
    if peak > 0:
        audio = audio * (0.9 / peak)
    wav_path = mid_path.with_suffix(".wav")
    sf.write(str(wav_path), audio, SAMPLE_RATE)
    duration_sec = len(audio) / SAMPLE_RATE
    n_notes = sum(len(inst.notes) for inst in pm.instruments)
    print(
        f"  {mid_path.name:35s} -> {wav_path.name:35s}  "
        f"dur={duration_sec:5.2f}s  notes={n_notes:3d}  peak_post_norm={float(abs(audio).max()):.3f}"
    )
    return wav_path


def main() -> int:
    mids = sorted(CORPUS_DIR.glob("*.mid"))
    if not mids:
        print(f"no .mid files under {CORPUS_DIR}", file=sys.stderr)
        return 1
    print(f"Rendering {len(mids)} MIDI -> WAV via pretty_midi.synthesize (additive sine)")
    for mid in mids:
        render_one(mid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
