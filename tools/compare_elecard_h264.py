"""Compare our H.264 per-MB block info against an Elecard CSV dump.

Usage: py -3.14 tools/compare_elecard_h264.py <elecard_csv> <our.veblk> [frame]

Elecard H.264 CSV (per macroblock): MB location, MB slice id, MB type,
MB size total\\prediction\\transform (bits), dimension, and (intra only)
sub_pdir. There is no per-MB QP field. We currently carry only mb_type + QP, so
this quantifies what aligns (location / dimension / prediction class) and what
is missing (bit sizes, detailed type, intra mode, slice id).
"""
import sys
import numpy as np

sys.path.insert(0, ".")
from src.analysis.veye_sidecar import (
    load_sidecar, blocks_from_frame, pus_from_frame, mvs_from_frame,
)
from src.analysis.schema import PredType

csv_path, veblk = sys.argv[1], sys.argv[2]
frame_idx = int(sys.argv[3]) if len(sys.argv) > 3 else 0

mbs = {}            # (x,y) -> dict
cur = None
with open(csv_path, encoding="utf-8", errors="replace") as fh:
    next(fh)
    for line in fh:
        line = line.rstrip("\n").rstrip(";")
        if ";" not in line:
            continue
        name, val = line.split(";", 1)
        if name == "MB location":
            x, y = val.split("x")
            cur = (int(x), int(y))
            mbs[cur] = {}
        elif cur is None:
            continue
        elif name == "MB slice id":
            mbs[cur]["slice"] = int(val)
        elif name == "MB type":
            mbs[cur]["type"] = val
        elif name.startswith("MB size"):
            a = val.split("\\")
            mbs[cur]["total"] = int(a[0])
            mbs[cur]["pred"] = int(a[1])
            mbs[cur]["trans"] = int(a[2])
        elif name == "dimension":
            w, h = val.split("x")
            mbs[cur]["dim"] = (int(w), int(h))
        elif name == "sub_pdir":
            mbs[cur]["sub_pdir"] = val

tot = sum(m.get("total", 0) for m in mbs.values())
pred = sum(m.get("pred", 0) for m in mbs.values())
trans = sum(m.get("trans", 0) for m in mbs.values())
types = {}
for m in mbs.values():
    types[m.get("type", "?")] = types.get(m.get("type", "?"), 0) + 1
print(f"CSV: {len(mbs)} MBs  bits total={tot} prediction={pred} transform={trans}")
print(f"CSV MB types: {types}")
print(f"CSV slice ids: {sorted({m.get('slice') for m in mbs.values()})}")

fb = load_sidecar(veblk)[frame_idx]
unit = fb.block_unit
b = blocks_from_frame(fb)
ours = {(int(r["x"]), int(r["y"])): r for r in b}
mix = {PredType.NAMES.get(int(k), int(k)): int(v)
       for k, v in zip(*np.unique(b["pred"], return_counts=True))}
print(f"\nours frame {frame_idx}: {len(ours)} MBs  pred mix={mix}  "
      f"MVs={len(mvs_from_frame(fb))}")

common = sorted(set(mbs) & set(ours))
print(f"matched by location: {len(common)}  "
      f"(CSV-only {len(set(mbs)-set(ours))}, ours-only {len(set(ours)-set(mbs))})")

# dimension (our CU is the 16x16 MB; Elecard MB dimension is also 16x16)
dim_ok = sum(1 for loc in common
             if mbs[loc].get("dim") == (int(ours[loc]["w"]), int(ours[loc]["h"])))
print(f"dimension match: {dim_ok}/{len(common)}")

# prediction-class sanity: map Elecard type prefix to our PredType
def csv_pred(t):
    if t.startswith("I_") or t.startswith("SI"):
        return PredType.INTRA
    if "Skip" in t:
        return PredType.SKIP
    if "B_" in t:
        return PredType.BI
    return PredType.INTER

pc_ok = sum(1 for loc in common
            if csv_pred(mbs[loc].get("type", "")) == int(ours[loc]["pred"]))
print(f"prediction-class match: {pc_ok}/{len(common)}")
print("\nMISSING in ours: MB bit sizes, detailed MB type, intra mode (sub_pdir),"
      " per-MB slice id")
