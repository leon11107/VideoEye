"""Compare our first-frame block-level info against an Elecard CSV dump.

Usage: py -3.14 tools/compare_elecard_blocks.py <elecard_csv> <our.veblk> [frame]

Elecard CSV structure (HEVC): a 'name;value;' stream. Per LCU it emits an
`lcu ...` header, then for each CU a `cu type` + `cu size` line, and a flat
TU/PU list. Crucially that TU/PU list is the *whole LCU's* list and is repeated
after every CU header in the LCU, so only the FIRST CU of each LCU has a
TU/PU entry that actually belongs to it. Per-CU reliable fields: location,
dimension, depth, and cu size (bits). QP / intra mode are validated only on the
first-CU-of-LCU subset.
"""
import sys
import numpy as np

sys.path.insert(0, ".")
from src.analysis.veye_sidecar import (
    load_sidecar, bit_sizes_from_frame, blocks_from_frame,
    intra_modes_from_frame, qp_grid_from_frame,
)
from src.analysis.schema import PredType

csv_path, veblk = sys.argv[1], sys.argv[2]
frame_idx = int(sys.argv[3]) if len(sys.argv) > 3 else 0

cus = {}          # (x,y) -> dict, per CU (reliable fields)
lcu_first = {}    # (x,y) -> {qp, mode}, only first CU of each LCU
cur = None
new_lcu = False
in_first_cu = False
with open(csv_path, encoding="utf-8", errors="replace") as fh:
    next(fh)
    for line in fh:
        line = line.rstrip("\n").rstrip(";")
        if ";" not in line:
            continue
        name, val = line.split(";", 1)
        p = val.split("\\")
        if name == "lcu location":
            new_lcu = True
        elif name == "cu type\\location\\dimension\\depth":
            x, y = p[1].split("x")
            w, h = p[2].split("x")
            cur = (int(x), int(y))
            cus[cur] = {"w": int(w), "h": int(h), "depth": int(p[3])}
            in_first_cu = new_lcu      # this CU is the LCU's first
            if in_first_cu:
                lcu_first[cur] = {"qp": None, "mode": None}
            new_lcu = False
        elif name == "cu size total\\prediction\\transform" and cur:
            cus[cur]["total"] = int(p[0])
            cus[cur]["pred"] = int(p[1])
            cus[cur]["trans"] = int(p[2])
        elif name == "tu dimensions\\qp" and in_first_cu and lcu_first[cur]["qp"] is None:
            lcu_first[cur]["qp"] = int(p[1])
        elif name == "pu intra dimension\\luma_type\\chroma_type" \
                and in_first_cu and lcu_first[cur]["mode"] is None:
            lcu_first[cur]["mode"] = int(p[1].split()[0])

print(f"CSV: {len(cus)} CUs, {len(lcu_first)} LCUs")

from src.analysis.veye_sidecar import mvs_from_frame
fb = load_sidecar(veblk)[frame_idx]
unit = fb.block_unit
_b = blocks_from_frame(fb)
_mix = {PredType.NAMES.get(int(k), int(k)): int(v)
        for k, v in zip(*np.unique(_b["pred"], return_counts=True))} if len(_b) else {}
print(f"our frame {frame_idx} block pred mix: {_mix}  MVs: {len(mvs_from_frame(fb))}")
our = {(int(r["x"]), int(r["y"])): r for r in bit_sizes_from_frame(fb)}
blk = {(int(r["x"]), int(r["y"])): r for r in blocks_from_frame(fb)}
imode = {(int(r["x"]), int(r["y"])): int(r["mode"]) for r in intra_modes_from_frame(fb)}
qg = qp_grid_from_frame(fb)
print(f"ours: {len(our)} CUs")

common = sorted(set(cus) & set(our))
print(f"matched by location: {len(common)}  "
      f"(CSV-only {len(set(cus)-set(our))}, ours-only {len(set(our)-set(cus))})")

# per-CU reliable fields
dim_ok = depth_ok = 0
for loc in common:
    c = cus[loc]
    if (c["w"], c["h"]) == (int(our[loc]["w"]), int(our[loc]["h"])):
        dim_ok += 1
    b = blk.get(loc)
    # our CU "depth" lives in BLOCK_DTYPE["depth"]
    if b is not None and int(b["depth"]) == c["depth"]:
        depth_ok += 1
print(f"dimension match: {dim_ok}/{len(common)}")
print(f"depth match:     {depth_ok}/{len(common)}")

# QP / intra mode on the first-CU-of-LCU subset (valid)
qp_ok = qp_n = mode_ok = mode_n = 0
for loc, v in lcu_first.items():
    if loc not in our:
        continue
    col, row = loc[0] // unit, loc[1] // unit
    if v["qp"] is not None and qg is not None and row < qg.shape[0] and col < qg.shape[1]:
        qp_n += 1
        qp_ok += int(qg[row, col]) == v["qp"]
    if v["mode"] is not None and loc in imode:
        mode_n += 1
        mode_ok += imode[loc] == v["mode"]
print(f"QP match (first-CU-of-LCU):        {qp_ok}/{qp_n}")
print(f"intra mode match (first-CU-of-LCU): {mode_ok}/{mode_n}")

# aggregate bit size
tot_csv = sum(c.get("total", 0) for c in cus.values())
tot_our = int(sum(int(our[l]["cu"]) for l in common))
print(f"bit total: CSV={tot_csv} ours={tot_our} ratio={tot_our/tot_csv:.3f}")
