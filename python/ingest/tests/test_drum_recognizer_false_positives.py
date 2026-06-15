from __future__ import annotations

import math
import struct
import wave
from collections import Counter
from pathlib import Path


def _write_crash_with_quiet_residual_tail(path: Path) -> None:
    """One real crash-like hit, then high-frequency residual/tail energy.

    This reproduces the Psalm 19 recognizer failure shape without relying on
    the post-hoc stem-silence gate: child detectors can fire on the residual
    modulation, and `combined_filter` used to promote those tail candidates
    into extra crash/ride/snare events.
    """

    sr = 48_000
    n = int(round(sr * 6.0))
    samples = [0.0] * n

    # A real bright transient at 1s.
    for i in range(int(round(0.12 * sr))):
        idx = int(round(1.0 * sr)) + i
        if idx >= n:
            break
        env = math.exp(-8.0 * (i / float(int(round(0.12 * sr)))))
        samples[idx] += 0.7 * math.sin(2.0 * math.pi * 7000.0 * (i / float(sr))) * env

    # Quiet, slowly-modulated separator residual. It has high-frequency
    # content but no new stick attack.
    for i in range(int(round(1.2 * sr)), int(round(4.8 * sr))):
        t = i / float(sr)
        amp = 0.008 * (1.0 + 0.4 * math.sin(2.0 * math.pi * 3.0 * t))
        samples[i] += amp * math.sin(2.0 * math.pi * 8500.0 * t)

    # Tiny residual clicks that are visible to detectors but should not be
    # promoted into gameplay notes.
    for t0 in (2.0, 2.7, 3.4):
        for i in range(int(round(0.006 * sr))):
            idx = int(round(t0 * sr)) + i
            if idx >= n:
                break
            env = math.exp(-20.0 * (i / float(int(round(0.006 * sr)))))
            samples[idx] += 0.015 * math.sin(2.0 * math.pi * 8000.0 * (i / float(sr))) * env

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sr)
        for sample in samples:
            v = max(-1.0, min(1.0, sample))
            wav_file.writeframesraw(struct.pack("<h", int(v * 30_000)))


def _write_piano_leakage_stem(path: Path) -> None:
    """Piano-like harmonic attacks leaking into a drum stem, with no drums."""

    sr = 48_000
    n = int(round(sr * 8.0))
    samples = [0.0] * n

    for t0 in (0.8, 1.6, 2.4, 3.2, 4.0, 4.8, 5.6, 6.4):
        start = int(round(t0 * sr))
        body_len = int(round(0.42 * sr))
        for i in range(body_len):
            idx = start + i
            if idx >= n:
                break
            t = i / float(sr)
            env = math.exp(-5.0 * t)
            sample = (
                0.018 * math.sin(2.0 * math.pi * 440.0 * t)
                + 0.012 * math.sin(2.0 * math.pi * 880.0 * t)
                + 0.007 * math.sin(2.0 * math.pi * 1760.0 * t)
                + 0.003 * math.sin(2.0 * math.pi * 6500.0 * t)
            )
            samples[idx] += sample * env

        residue_len = int(round(0.060 * sr))
        for i in range(residue_len):
            idx = start + i
            if idx >= n:
                break
            t = i / float(sr)
            env = math.exp(-24.0 * t)
            samples[idx] += 0.006 * math.sin(2.0 * math.pi * 8500.0 * t) * env

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sr)
        for sample in samples:
            v = max(-1.0, min(1.0, sample))
            wav_file.writeframesraw(struct.pack("<h", int(v * 30_000)))


def test_combined_filter_rejects_residual_tail_candidates_before_event_emission(
    tmp_path: Path,
) -> None:
    from aural_ingest.algorithms import combined_filter

    fixture = tmp_path / "crash_with_residual_tail.wav"
    _write_crash_with_quiet_residual_tail(fixture)

    candidates, debug = combined_filter.detect_candidates_with_debug(fixture)
    events = combined_filter.transcribe(fixture)

    assert debug["raw_candidate_count"] > len(candidates), (
        "fixture should exercise recognizer rejection, not merely detector silence"
    )
    reject_reasons = Counter(
        decision["reject_reason"] for decision in debug["decisions"] if decision["reject_reason"]
    )
    assert reject_reasons["weak_cymbal_body"] >= 1 or reject_reasons["weak_onset_evidence"] >= 1

    # The only gameplay note should be the real transient near 1s. The quiet
    # residual tail must not become repeated crash/ride/snare notes.
    assert 1 <= len(events) <= 2
    assert all(event.time < 1.25 for event in events), [
        (event.time, event.note) for event in events
    ]


def test_beat_conditioned_rejects_piano_leakage_before_event_emission(
    tmp_path: Path,
) -> None:
    from aural_ingest.algorithms import beat_conditioned_multiband_decoder

    fixture = tmp_path / "piano_leakage_stem.wav"
    _write_piano_leakage_stem(fixture)

    candidates, debug = beat_conditioned_multiband_decoder.detect_candidates_with_debug(fixture)
    events = beat_conditioned_multiband_decoder.transcribe(fixture)

    assert debug["raw_seed_count"] > 0, (
        "fixture should exercise recognizer rejection, not detector silence"
    )
    reject_reasons = Counter(
        record["reject_reason"]
        for decision in debug["decisions"]
        for record in decision["rejected"]
        if record["reject_reason"]
    )
    assert reject_reasons["weak_hat_body"] >= 1 or reject_reasons["weak_snare_body"] >= 1

    assert candidates == []
    assert events == []
