"""Check crack_ratio distribution for kick-vs-snare events on Psalm 4."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(r"d:\AuralPrimer\python\ingest\src")))

from aural_ingest.algorithms._common import (
    preprocess_audio, compute_band_envelopes, onset_novelty,
    normalize_series, adaptive_peak_pick, frame_to_time,
    timbral_features, clamp,
)

wav_path = Path(r"D:\Psalms\Psalm 4\Book of Psalms - Psalm 4 - Trouble Again Stems\Book of Psalms - Psalm 4 - Trouble Again (Drums).wav")
samples, sr = preprocess_audio(wav_path, target_sr=44100, pre_emphasis_coeff=0.9, high_pass_hz=35.0)

hop = 384
hop_sec = hop / float(sr)
env = compute_band_envelopes(samples, sr, {"low": (35.0, 180.0), "mid": (180.0, 2500.0), "high": (2500.0, 12000.0)}, hop_size=hop)

low = normalize_series(env["low"])
mid = normalize_series(env["mid"])
high = normalize_series(env["high"])
n = min(len(low), len(mid), len(high))

low_n = normalize_series(onset_novelty(low))
mid_n = normalize_series(onset_novelty(mid))
high_n = normalize_series(onset_novelty(high))
novelty = normalize_series([(0.45*low_n[i])+(0.3*mid_n[i])+(0.25*high_n[i]) for i in range(n)])

peaks = adaptive_peak_pick(novelty, hop_sec=hop_sec, k=2.1, min_gap_sec=0.05, window_sec=0.38, percentile=0.85)

print(f"{'Time':>8s} {'low_hit':>8s} {'mid_hit':>8s} {'high_hit':>8s} {'low_dom':>8s} {'snare_d':>8s} {'crack_r':>8s} {'winner':>7s}")
print("-" * 68)

kick_cr = []
snare_cr = []

for idx, strength in peaks[:100]:
    t_raw = frame_to_time(idx, hop, sr)
    feat = timbral_features(samples, sr, t_raw)
    low_hit = max(low_n[max(0,idx-1):min(len(low_n),idx+2)]) if low_n else 0
    mid_hit = max(mid_n[max(0,idx-1):min(len(mid_n),idx+2)]) if mid_n else 0
    high_hit = max(high_n[max(0,idx-1):min(len(high_n),idx+2)]) if high_n else 0

    total = max(1e-9, feat["sub"]+feat["low"]+feat["mid"]+feat["snare_crack"]+feat["high"]+feat["air"])
    low_dom = clamp((feat["sub"]+(0.8*feat["low"]))/total, 0, 1)
    snare_dom = clamp((feat["mid"]+(0.9*feat["snare_crack"]))/total, 0, 1)
    high_dom = clamp((feat["high"]+(0.7*feat["air"]))/total, 0, 1)
    crack_ratio = clamp(feat["snare_crack"]/max(1e-9, feat["low"]+feat["high"]), 0, 1)

    kick_score = (0.58*low_hit) + (0.42*low_dom)
    snare_score = (0.46*mid_hit) + (0.38*snare_dom) + (0.16*crack_ratio)
    hat_score = (0.62*high_hit) + (0.38*high_dom)

    drum_class = max([("kick",kick_score),("snare",snare_score),("hat",hat_score)], key=lambda x:x[1])[0]

    if drum_class == "snare":
        snare_cr.append(crack_ratio)
    elif drum_class == "kick":
        kick_cr.append(crack_ratio)

    if drum_class == "snare" and low_hit > 0.12 and low_hit/max(1e-9,mid_hit) > 0.85 and low_dom > 0.20:
        flag = " <-- RECLASSIFY?"
    else:
        flag = ""
    print(f"{t_raw:8.3f} {low_hit:8.4f} {mid_hit:8.4f} {high_hit:8.4f} {low_dom:8.4f} {snare_dom:7.4f} {crack_ratio:8.4f} {drum_class:>7s}{flag}")

print(f"\nSnare crack_ratio: min={min(snare_cr):.4f} max={max(snare_cr):.4f} mean={sum(snare_cr)/len(snare_cr):.4f} (n={len(snare_cr)})" if snare_cr else "No snares")
print(f"Kick crack_ratio: min={min(kick_cr):.4f} max={max(kick_cr):.4f} mean={sum(kick_cr)/len(kick_cr):.4f} (n={len(kick_cr)})" if kick_cr else "No kicks")
