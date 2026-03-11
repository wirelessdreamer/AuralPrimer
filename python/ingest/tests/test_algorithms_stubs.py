from pathlib import Path
import math
import random
import struct
import wave


def _write_drum_rebuild_fixture(path: Path, sr: int = 48_000, duration_sec: float = 6.0) -> None:
    n = int(sr * duration_sec)
    y = [0.0 for _ in range(n)]
    rng = random.Random(12345)

    beat = int(0.5 * sr)
    for beat_idx, t0 in enumerate(range(0, n, beat)):
        # Kick pulse
        for i in range(int(0.08 * sr)):
            idx = t0 + i
            if idx >= n:
                break
            env = math.exp(-6.0 * (i / (0.08 * sr)))
            y[idx] += 0.85 * math.sin(2.0 * math.pi * 58.0 * (i / sr)) * env

        # Snare on beats 2/4
        if beat_idx % 4 in (1, 3):
            s0 = t0 + int(0.01 * sr)
            for i in range(int(0.05 * sr)):
                idx = s0 + i
                if idx >= n:
                    break
                env = math.exp(-12.0 * (i / (0.05 * sr)))
                noise = rng.uniform(-1.0, 1.0) * 0.7
                crack = math.sin(2.0 * math.pi * 2000.0 * (i / sr)) * 0.25
                y[idx] += (noise + crack) * env

        # Closed hats on 8ths
        for off in (0, int(0.25 * sr)):
            h0 = t0 + off
            for i in range(int(0.02 * sr)):
                idx = h0 + i
                if idx >= n:
                    break
                env = math.exp(-25.0 * (i / (0.02 * sr)))
                y[idx] += (((1.0 if i % 2 == 0 else -1.0) * 0.35) + rng.uniform(-0.15, 0.15)) * env

        # Open hats occasionally
        if beat_idx % 8 == 6:
            o0 = t0 + int(0.25 * sr)
            for i in range(int(0.16 * sr)):
                idx = o0 + i
                if idx >= n:
                    break
                env = math.exp(-4.0 * (i / (0.16 * sr)))
                y[idx] += (((1.0 if i % 2 == 0 else -1.0) * 0.25) + rng.uniform(-0.12, 0.12)) * env

        # Crash at phrase boundaries
        if beat_idx % 16 == 0:
            c0 = t0 + int(0.005 * sr)
            for i in range(int(0.24 * sr)):
                idx = c0 + i
                if idx >= n:
                    break
                env = math.exp(-3.2 * (i / (0.24 * sr)))
                y[idx] += (rng.uniform(-1.0, 1.0) * 0.4 + math.sin(2.0 * math.pi * 6000.0 * (i / sr)) * 0.18) * env

        # Ride pattern in second half
        if beat_idx >= 8 and beat_idx % 2 == 0:
            r0 = t0 + int(0.12 * sr)
            for i in range(int(0.1 * sr)):
                idx = r0 + i
                if idx >= n:
                    break
                env = math.exp(-7.0 * (i / (0.1 * sr)))
                y[idx] += (math.sin(2.0 * math.pi * 5000.0 * (i / sr)) * 0.2 + rng.uniform(-0.1, 0.1)) * env

        # Tom fill at bar end
        if beat_idx % 8 == 7:
            for j, freq in enumerate((180.0, 130.0, 95.0)):
                t1 = t0 + int((0.12 + (0.1 * j)) * sr)
                for i in range(int(0.09 * sr)):
                    idx = t1 + i
                    if idx >= n:
                        break
                    env = math.exp(-5.0 * (i / (0.09 * sr)))
                    y[idx] += 0.45 * math.sin(2.0 * math.pi * freq * (i / sr)) * env

    peak = max((abs(v) for v in y), default=1.0)
    if peak > 0.0:
        y = [max(-1.0, min(1.0, (v / peak) * 0.95)) for v in y]

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        for s in y:
            w.writeframesraw(struct.pack("<h", int(s * 32767.0)))


def _write_sine_wav(path: Path, sr: int = 48_000, freq_hz: float = 440.0, duration_sec: float = 1.0) -> None:
    n = int(sr * duration_sec)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        for i in range(n):
            s = 0.5 * math.sin(2.0 * math.pi * freq_hz * (i / sr))
            v = int(max(-1.0, min(1.0, s)) * 32767.0)
            w.writeframesraw(struct.pack("<h", v))


def test_algorithm_classes_expose_contract() -> None:
    from aural_ingest.algorithms import (
        adaptive_beat_grid,
        aural_onset,
        beat_conditioned_multiband_decoder,
        combined_filter,
        dsp_bandpass,
        dsp_bandpass_improved,
        dsp_spectral_flux,
        librosa_superflux,
    )

    modules = [
        adaptive_beat_grid,
        aural_onset,
        beat_conditioned_multiband_decoder,
        combined_filter,
        dsp_bandpass,
        dsp_bandpass_improved,
        dsp_spectral_flux,
        librosa_superflux,
    ]
    for mod in modules:
        assert hasattr(mod, "ALGORITHM")
        alg = mod.ALGORITHM
        assert hasattr(alg, "name")
        assert callable(getattr(alg, "transcribe"))


def test_common_post_processing_applies_dedup_refractory_and_velocity() -> None:
    from aural_ingest.algorithms._common import DrumCandidate, candidates_to_events

    candidates = [
        DrumCandidate(time=0.100, drum_class="snare", strength=0.9, confidence=0.9, source="a"),
        DrumCandidate(time=0.118, drum_class="snare", strength=0.7, confidence=0.7, source="a"),
        DrumCandidate(time=0.250, drum_class="snare", strength=0.85, confidence=0.88, source="a"),
        DrumCandidate(time=0.300, drum_class="kick", strength=0.95, confidence=0.95, source="b"),
    ]

    events = candidates_to_events(candidates)
    assert events
    # 0.100 + 0.118 collapse; refractory keeps separated 0.250 hit.
    snare_times = [e.time for e in events if e.note == 38]
    assert len(snare_times) == 2
    assert snare_times[0] == 0.1
    assert snare_times[1] == 0.25

    velocities = [e.velocity for e in events]
    assert all(25 <= v <= 127 for v in velocities)


def test_rebuild_profile_combined_is_expanded_while_adaptive_is_core_heavy(tmp_path: Path) -> None:
    from aural_ingest.algorithms import (
        adaptive_beat_grid,
        beat_conditioned_multiband_decoder,
        combined_filter,
        dsp_bandpass_improved,
        dsp_spectral_flux,
    )

    stem = tmp_path / "drums.wav"
    _write_drum_rebuild_fixture(stem)

    combined = combined_filter.transcribe(stem)
    adaptive = adaptive_beat_grid.transcribe(stem)
    hybrid = beat_conditioned_multiband_decoder.transcribe(stem)
    improved = dsp_bandpass_improved.transcribe(stem)
    spectral = dsp_spectral_flux.transcribe(stem)

    assert combined and adaptive and hybrid and improved and spectral
    assert all(e2.time >= e1.time for e1, e2 in zip(combined, combined[1:]))
    assert all(e2.time >= e1.time for e1, e2 in zip(hybrid, hybrid[1:]))

    notes_combined = {e.note for e in combined}
    notes_adaptive = {e.note for e in adaptive}
    notes_hybrid = {e.note for e in hybrid}
    notes_improved = {e.note for e in improved}
    notes_spectral = {e.note for e in spectral}

    core = {36, 38, 42}
    assert notes_adaptive.issubset({36, 38, 42, 46, 49})
    assert core.issubset(notes_adaptive)
    assert core.issubset(notes_hybrid)

    # Combined path should keep non-core lane diversity.
    assert notes_combined - core
    assert len(notes_combined) > len(notes_adaptive)
    assert notes_hybrid & {46, 49, 51}
    assert len(hybrid) >= len(adaptive)

    # Improved + spectral should both emit at least one non-core class.
    assert notes_improved - core
    assert notes_spectral - core


def test_hybrid_secondary_core_emission_stays_strict() -> None:
    from aural_ingest.algorithms.beat_conditioned_multiband_decoder import _should_emit_secondary_core

    assert _should_emit_secondary_core(
        primary_bucket="kick",
        primary_score=0.52,
        secondary_bucket="snare",
        secondary_score=0.45,
        votes={"kick": 0.28, "snare": 0.24, "hi_hat": 0.06, "cymbal": 0.0, "tom": 0.0},
        low_hit=0.38,
        snare_hit=0.31,
        low_dom=0.23,
        snare_dom=0.21,
        high_dom=0.12,
        sharp=0.29,
        zcr=0.33,
    )

    assert not _should_emit_secondary_core(
        primary_bucket="kick",
        primary_score=0.58,
        secondary_bucket="snare",
        secondary_score=0.34,
        votes={"kick": 0.31, "snare": 0.12, "hi_hat": 0.11, "cymbal": 0.0, "tom": 0.0},
        low_hit=0.4,
        snare_hit=0.19,
        low_dom=0.24,
        snare_dom=0.12,
        high_dom=0.29,
        sharp=0.31,
        zcr=0.14,
    )


def test_default_registry_contains_all_expected_algorithms() -> None:
    from aural_ingest.transcription import KNOWN_DRUM_FILTERS, build_default_drum_algorithm_registry

    reg = build_default_drum_algorithm_registry()
    for k in KNOWN_DRUM_FILTERS:
        assert k in reg
        assert callable(reg[k])


def test_common_builder_handles_nonpositive_step_size(tmp_path: Path) -> None:
    from aural_ingest.algorithms._common import build_pattern_events

    stem = tmp_path / "stem.bin"
    stem.write_bytes(b"x")

    events = build_pattern_events(stem, [36], step_sec=0.0, velocity_base=80)
    assert events
    assert events[0].time == 0.0


def test_melodic_pyin_tracks_pitch_from_waveform(tmp_path: Path) -> None:
    from aural_ingest.algorithms import melodic_pyin

    stem = tmp_path / "tone.wav"
    _write_sine_wav(stem, freq_hz=440.0, duration_sec=1.2)
    notes = melodic_pyin.transcribe(stem)
    assert notes
    assert any(67 <= n.pitch <= 71 for n in notes)


def test_melodic_basic_pitch_uses_model_gate_and_generates_poly_notes(tmp_path: Path) -> None:
    from aural_ingest.algorithms import melodic_basic_pitch

    stem = tmp_path / "tone.wav"
    _write_sine_wav(stem, freq_hz=261.63, duration_sec=1.0)

    model_path = tmp_path / "nmp.onnx"
    model_path.write_bytes(b"x")

    notes = melodic_basic_pitch.transcribe(stem, model_path=model_path)
    assert notes
    pitches = {n.pitch for n in notes}
    assert any(p + 7 in pitches for p in pitches if p + 7 <= 108)
