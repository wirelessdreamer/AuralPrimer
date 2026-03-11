from pathlib import Path
import math
import random
import struct
import wave


def _write_mix_wav(path: Path, *, sr: int = 48_000, duration_sec: float = 2.0) -> None:
    n = int(sr * duration_sec)
    rng = random.Random(7)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        for i in range(n):
            t = i / float(sr)
            s = (
                0.45 * math.sin(2.0 * math.pi * 60.0 * t)
                + 0.3 * math.sin(2.0 * math.pi * 1900.0 * t)
                + 0.15 * math.sin(2.0 * math.pi * 6200.0 * t)
            )
            if i % int(sr * 0.25) == 0:
                s += 0.9
            s += rng.uniform(-0.04, 0.04)
            v = int(max(-1.0, min(1.0, s)) * 32767.0)
            w.writeframesraw(struct.pack("<h", v))


def _write_drum_fixture(path: Path, *, sr: int = 48_000, duration_sec: float = 4.0) -> None:
    n = int(sr * duration_sec)
    y = [0.0 for _ in range(n)]
    rng = random.Random(19)

    beat = int(0.5 * sr)
    for beat_idx, t0 in enumerate(range(0, n, beat)):
        for i in range(int(0.08 * sr)):
            idx = t0 + i
            if idx >= n:
                break
            env = math.exp(-6.0 * (i / (0.08 * sr)))
            y[idx] += 0.8 * math.sin(2.0 * math.pi * 58.0 * (i / sr)) * env

        if beat_idx % 2 == 1:
            s0 = t0 + int(0.01 * sr)
            for i in range(int(0.05 * sr)):
                idx = s0 + i
                if idx >= n:
                    break
                env = math.exp(-11.0 * (i / (0.05 * sr)))
                y[idx] += (rng.uniform(-1.0, 1.0) * 0.55 + math.sin(2.0 * math.pi * 2200.0 * (i / sr)) * 0.22) * env

        for off in (0, int(0.25 * sr)):
            h0 = t0 + off
            for i in range(int(0.02 * sr)):
                idx = h0 + i
                if idx >= n:
                    break
                env = math.exp(-24.0 * (i / (0.02 * sr)))
                y[idx] += (((1.0 if i % 2 == 0 else -1.0) * 0.28) + rng.uniform(-0.1, 0.1)) * env

        if beat_idx % 8 == 0:
            c0 = t0 + int(0.004 * sr)
            for i in range(int(0.2 * sr)):
                idx = c0 + i
                if idx >= n:
                    break
                env = math.exp(-3.1 * (i / (0.2 * sr)))
                y[idx] += (rng.uniform(-1.0, 1.0) * 0.32 + math.sin(2.0 * math.pi * 6200.0 * (i / sr)) * 0.16) * env

    peak = max((abs(v) for v in y), default=1.0)
    y = [max(-1.0, min(1.0, (v / peak) * 0.95)) for v in y]

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        for s in y:
            w.writeframesraw(struct.pack("<h", int(s * 32767.0)))


def _write_sparse_hit_fixture(path: Path, *, sr: int = 48_000, duration_sec: float = 1.0) -> None:
    n = int(sr * duration_sec)
    y = [0.0 for _ in range(n)]
    start = int(0.1 * sr)
    length = int(0.08 * sr)

    for i in range(length):
        idx = start + i
        if idx >= n:
            break
        env = math.exp(-6.5 * (i / max(1, length)))
        y[idx] += 0.9 * math.sin(2.0 * math.pi * 60.0 * (i / sr)) * env

    peak = max((abs(v) for v in y), default=1.0)
    if peak > 0.0:
        y = [max(-1.0, min(1.0, (v / peak) * 0.95)) for v in y]

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        for s in y:
            w.writeframesraw(struct.pack("<h", int(s * 32767.0)))


def test_common_primitives_cover_processing_paths(tmp_path: Path) -> None:
    from aural_ingest.algorithms._common import (
        DRUM_CLASS_TO_MIDI,
        DrumCandidate,
        adaptive_peak_pick,
        band_pass_one_pole,
        candidates_to_events,
        classify_core_from_features,
        classify_hat_or_cymbal,
        classify_tom,
        compute_band_envelopes,
        dedup_same_class,
        enforce_refractory,
        estimate_tempo_from_onset_env,
        fallback_events_from_classes,
        frame_mean_abs_series,
        frame_rms_series,
        frame_to_time,
        high_pass_one_pole,
        low_pass_one_pole,
        merge_candidate_clusters,
        normalize_peak,
        normalize_series,
        onset_novelty,
        preprocess_audio,
        read_wav_mono_normalized,
        resample_linear,
        smooth_series,
        snap_time_to_grid,
        timbral_features,
        time_to_frame,
        trim_series_dict,
        velocity_from_strength,
    )

    wav = tmp_path / "mix.wav"
    _write_mix_wav(wav)

    samples, sr = read_wav_mono_normalized(wav)
    assert samples and sr == 48_000

    norm = normalize_peak(samples)
    assert max(abs(v) for v in norm) <= 1.0

    rs = resample_linear(norm, 48_000, 44_100)
    assert rs and len(rs) != len(norm)

    emph, esr = preprocess_audio(wav, target_sr=44_100, pre_emphasis_coeff=0.97, high_pass_hz=35.0)
    assert emph and esr == 44_100

    lp = low_pass_one_pole(emph, esr, 1200.0)
    hp = high_pass_one_pole(emph, esr, 1200.0)
    bp = band_pass_one_pole(emph, esr, 80.0, 350.0)
    assert lp and hp and bp

    rms = frame_rms_series(bp, frame_size=512, hop_size=256)
    mean_abs = frame_mean_abs_series(bp, frame_size=512, hop_size=256)
    assert rms and mean_abs

    smoothed = smooth_series(rms, 2)
    assert len(smoothed) == len(rms)

    env = normalize_series(onset_novelty(mean_abs))
    peaks = adaptive_peak_pick(env, hop_sec=256 / esr, k=1.8, min_gap_sec=0.05, density_boost=0.3)
    assert peaks

    period, conf = estimate_tempo_from_onset_env(env, 256 / esr)
    assert period >= 0.0
    assert 0.0 <= conf <= 1.0

    t = frame_to_time(peaks[0][0], 256, esr)
    frame = time_to_frame(t, 256, esr)
    assert frame >= 0

    snapped = snap_time_to_grid(t + 0.008, anchor=0.0, step=max(0.2, period), tolerance=0.02)
    assert isinstance(snapped, float)

    bands = compute_band_envelopes(
        emph,
        esr,
        {"low": (35.0, 180.0), "mid": (180.0, 2500.0), "high": (2500.0, 12000.0)},
        hop_size=256,
    )
    trimmed = trim_series_dict(bands)
    assert set(trimmed.keys()) == {"low", "mid", "high"}

    feat = timbral_features(emph, esr, t)
    assert classify_tom(feat) in {"tom_high", "tom_low", "tom_floor"}
    assert classify_hat_or_cymbal(feat, prefer_ride_when_on_grid=True, on_grid=True) in {
        "hh_closed",
        "hh_open",
        "crash",
        "ride",
    }
    assert classify_core_from_features(feat, allow_expanded=True) in {
        "kick",
        "snare",
        "hh_closed",
        "hh_open",
        "crash",
        "ride",
        "tom_high",
        "tom_low",
        "tom_floor",
    }

    cands = [
        DrumCandidate(time=0.10, drum_class="snare", strength=0.8, confidence=0.8, source="a"),
        DrumCandidate(time=0.12, drum_class="snare", strength=0.6, confidence=0.7, source="b"),
        DrumCandidate(time=0.30, drum_class="kick", strength=0.9, confidence=0.9, source="a"),
    ]
    clusters = merge_candidate_clusters(cands, window_sec=0.03)
    assert clusters
    dedup = dedup_same_class(cands, window_sec=0.03)
    refractory = enforce_refractory(dedup)
    events = candidates_to_events(refractory)
    assert events

    vel = velocity_from_strength(0.7, "kick")
    assert 25 <= vel <= 127

    fallback = fallback_events_from_classes(wav, ["kick", "snare", "hh_closed"], step_sec=0.1, velocity_base=80)
    assert fallback and fallback[0].note in DRUM_CLASS_TO_MIDI.values()


def test_common_early_return_edges() -> None:
    from aural_ingest.algorithms._common import (
        adaptive_peak_pick,
        band_pass_one_pole,
        estimate_tempo_from_onset_env,
        frame_mean_abs_series,
        frame_rms_series,
        high_pass_one_pole,
        low_pass_one_pole,
        normalize_peak,
        preprocess_audio,
        read_wav_mono_normalized,
        resample_linear,
        smooth_series,
        trim_series_dict,
    )

    missing = Path("does_not_exist.wav")
    assert read_wav_mono_normalized(missing) == ([], 0)
    assert preprocess_audio(missing, target_sr=44_100) == ([], 0)

    assert normalize_peak([]) == []
    assert resample_linear([], 48_000, 44_100) == []
    assert low_pass_one_pole([], 48_000, 1000.0) == []
    assert high_pass_one_pole([], 48_000, 1000.0) == []
    assert band_pass_one_pole([], 48_000, 80.0, 200.0) == []
    assert frame_rms_series([], 128, 64) == []
    assert frame_mean_abs_series([], 128, 64) == []
    assert smooth_series([], 1) == []
    assert trim_series_dict({"x": []}) == {"x": []}
    assert adaptive_peak_pick([0.0, 0.0, 0.0], hop_sec=0.01, k=2.0, min_gap_sec=0.05) == []

    period, conf = estimate_tempo_from_onset_env([0.0, 0.0, 0.0], 0.01)
    assert period == 0.5
    assert conf == 0.0


def test_common_silence_gate_suppresses_candidates_in_silent_regions(tmp_path: Path) -> None:
    from aural_ingest.algorithms._common import DrumCandidate, candidates_to_events

    wav = tmp_path / "sparse.wav"
    _write_sparse_hit_fixture(wav)

    candidates = [
        DrumCandidate(time=0.10, drum_class="kick", strength=0.9, confidence=0.92, source="a"),
        DrumCandidate(time=0.72, drum_class="snare", strength=0.88, confidence=0.9, source="a"),
    ]

    gated = candidates_to_events(candidates, stem_path=wav)
    ungated = candidates_to_events(candidates)

    assert len(ungated) == 2
    assert len(gated) == 1
    assert gated[0].note == 36


def test_all_drum_algorithms_exercise_detector_paths(tmp_path: Path) -> None:
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

    wav = tmp_path / "drums.wav"
    _write_drum_fixture(wav)

    modules = [
        combined_filter,
        dsp_bandpass_improved,
        dsp_spectral_flux,
        aural_onset,
        adaptive_beat_grid,
        beat_conditioned_multiband_decoder,
        dsp_bandpass,
        librosa_superflux,
    ]

    for mod in modules:
        detect = getattr(mod, "detect_candidates")
        cands = detect(wav)
        events = mod.transcribe(wav)
        assert cands
        assert events
        assert all(0 <= e.note <= 127 for e in events)
        assert all(e2.time >= e1.time for e1, e2 in zip(events, events[1:]))
