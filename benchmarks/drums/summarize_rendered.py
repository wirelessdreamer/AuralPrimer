"""Summarize rendered benchmark results for adaptive_beat_grid."""
import json, sys
data = json.load(open(sys.argv[1]))
print(f"Algorithm: {data['algorithms']}")
print(f"Cases: {len(data['cases'])}")
print(f"Tolerance: {data['tolerance_ms']}ms\n")

total_tp = total_fp = total_fn = 0
all_confusions = {}
for case in data["cases"]:
    r = case["results"][0]
    o = r["overall"]
    total_tp += o["tp"]
    total_fp += o["fp"]
    total_fn += o["fn"]
    print(f"{case['case_id']:40s}  F1={o['f1']:.3f}  P={o['precision']:.3f}  R={o['recall']:.3f}  tp={o['tp']:3d}  fp={o['fp']:3d}  fn={o['fn']:3d}")
    for c in r.get("confusions", []):
        key = f"{c['reference_class']}->{c['predicted_class']}"
        all_confusions[key] = all_confusions.get(key, 0) + c["count"]

p = total_tp / max(1, total_tp + total_fp)
r = total_tp / max(1, total_tp + total_fn)
f1 = 2 * p * r / max(1e-9, p + r)
print(f"\nAGGREGATE: F1={f1:.3f}  P={p:.3f}  R={r:.3f}  tp={total_tp}  fp={total_fp}  fn={total_fn}")
print(f"\nTop confusions:")
for key, count in sorted(all_confusions.items(), key=lambda x: -x[1])[:15]:
    print(f"  {key}: x{count}")
