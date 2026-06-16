"""Compare our HEVC per-CU/CTU side info against Elecard's rich block dump.

Usage: py -3.14 tools/compare_elecard_hevc_rich.py <blocks.csv> <our.veblk> <frame>

Elecard HEVC dump is hierarchical: per LCU (location, slice/tile idx, size
total/prediction/transform), then per CU (type, location, dimension, depth,
size total/prediction/transform), then TU (location, dimensions, qp) and PU
(intra luma_type/chroma_type, or inter type + L0/L1 mv\\mvd). Compares every
field we export and reports gaps. <frame> is our sidecar frame index (display).
"""
import sys
import numpy as np

sys.path.insert(0, ".")
from src.analysis.veye_sidecar import (
    load_sidecar, blocks_from_frame, bit_sizes_from_frame,
    ctu_bit_sizes_from_frame, qp_grid_from_frame, intra_modes_from_frame,
    mvs_from_frame,
)
from src.analysis.schema import PredType

csv_path, veblk, frame_idx = sys.argv[1], sys.argv[2], int(sys.argv[3])

cus = {}          # (x,y) -> CU dict
lcus = {}         # (x,y) -> LCU dict
cur = None        # current CU loc
lcu = None        # current LCU loc
with open(csv_path, encoding="utf-8", errors="replace") as fh:
    next(fh)
    for line in fh:
        line = line.rstrip("\n").rstrip(",")
        if "," not in line:
            continue
        name, val = line.split(",", 1)
        if name == "lcu location":
            x, y = val.split("x"); lcu = (int(x), int(y)); lcus[lcu] = {}
        elif name == "lcu slice idx":
            lcus[lcu]["slice"] = int(val)
        elif name == "lcu tile idx":
            lcus[lcu]["tile"] = int(val)
        elif name == "lcu size total":
            lcus[lcu]["total"] = int(val)
        elif name == "lcu size transform":
            lcus[lcu]["trans"] = int(val)
        elif name == "cu location":
            x, y = val.split("x"); cur = (int(x), int(y)); cus[cur] = {"lcu": lcu}
        elif cur is None:
            continue
        elif name == "cu type":
            cus[cur]["type"] = val
        elif name == "cu dimension":
            w, h = val.split("x"); cus[cur]["dim"] = (int(w), int(h))
        elif name == "cu depth":
            cus[cur]["depth"] = int(val)
        elif name == "cu size total":
            cus[cur]["total"] = int(val)
        elif name == "cu size transform":
            cus[cur]["trans"] = int(val)
        elif name == "tu qp" and "qp" not in cus[cur]:
            cus[cur]["qp"] = int(val)
        elif name == "pu intra luma_type" and "imode" not in cus[cur]:
            cus[cur]["imode"] = int(val.split()[0])
            cus[cur]["pred"] = PredType.INTRA
        elif name == "pu inter type" and "pred" not in cus[cur]:
            cus[cur]["pred"] = PredType.BI if "Bi" in val else PredType.INTER
        elif name == "pu L0 mv" and "mv0" not in cus[cur]:
            a = val.split(","); cus[cur]["mv0"] = (int(a[0]), int(a[1]))
        elif name == "pu L0 mv\\mvd" and "mv0" not in cus[cur]:
            a = val.split("/")[0].split(","); cus[cur]["mv0"] = (int(a[0]), int(a[1]))
        elif name == "pu L1 mv" and "mv1" not in cus[cur]:
            a = val.split(","); cus[cur]["mv1"] = (int(a[0]), int(a[1]))
        elif name == "pu L1 mv\\mvd" and "mv1" not in cus[cur]:
            a = val.split("/")[0].split(","); cus[cur]["mv1"] = (int(a[0]), int(a[1]))

print(f"Elecard: {len(cus)} CUs, {len(lcus)} LCUs")

fb = load_sidecar(veblk)[frame_idx]
unit = fb.block_unit
blk = {(int(r["x"]), int(r["y"])): r for r in blocks_from_frame(fb)}
bs = {(int(r["x"]), int(r["y"])): r for r in bit_sizes_from_frame(fb)}
ctu = {(int(r["x"]), int(r["y"])): r for r in ctu_bit_sizes_from_frame(fb)}
qg = qp_grid_from_frame(fb)
imode = {(int(r["x"]), int(r["y"])): int(r["mode"]) for r in intra_modes_from_frame(fb)}
mv = mvs_from_frame(fb)
print(f"our frame {frame_idx} poc={fb.own_poc}: {len(blk)} CUs, {len(ctu)} CTUs, "
      f"{len(mv)} MVs")

common = sorted(set(cus) & set(blk))
print(f"CU matched by location: {len(common)}  (Elecard-only "
      f"{len(set(cus)-set(blk))}, ours-only {len(set(blk)-set(cus))})")


def at(grid, loc):
    return grid.get(loc)


dim = dep = tot = tr = qp = qpn = im = imn = 0
for loc in common:
    c = cus[loc]
    b = blk[loc]
    col, row = loc[0] // unit, loc[1] // unit
    if c.get("dim") == (int(b["w"]), int(b["h"])):
        dim += 1
    if c.get("depth") == int(b["depth"]):
        dep += 1
    r = bs.get(loc)
    if r is not None:
        tot += int(r["cu"]) == c.get("total")
        tr += int(r["tu"]) == c.get("trans")
    if "qp" in c and qg is not None:
        qpn += 1
        qp += int(qg[row, col]) == c["qp"]
    if "imode" in c and loc in imode:
        imn += 1
        im += imode[loc] == c["imode"]
n = len(common)
print(f"CU dimension match:  {dim}/{n}")
print(f"CU depth match:      {dep}/{n}")
print(f"CU total bits match: {tot}/{n}")
print(f"CU transform match:  {tr}/{n}")
print(f"TU qp match:         {qp}/{qpn}  (CUs without residual carry no tu qp)")
print(f"intra luma_type match: {im}/{imn}")

# LCU (CTU) level
lc_tot = lc_tr = lc_sl = lc_tl = lcn = 0
ctb = fb.ctb_size
for loc, c in lcus.items():
    r = ctu.get(loc)
    if r is None:
        continue
    lcn += 1
    lc_tot += int(r["cu"]) == c.get("total")
    lc_tr += int(r["tu"]) == c.get("trans")
    if fb.slice_grid is not None and ctb:
        cy, cx = loc[1] // ctb, loc[0] // ctb
        if cy < fb.slice_grid.shape[0] and cx < fb.slice_grid.shape[1]:
            lc_sl += int(fb.slice_grid[cy, cx]) == c.get("slice")
print(f"LCU total bits match: {lc_tot}/{lcn}")
print(f"LCU transform match:  {lc_tr}/{lcn}")
print(f"LCU slice idx match:  {lc_sl}/{lcn}")

# MV: each CU's first L0/L1 mv (Elecard 1/4-pel) vs our PU MVs (pixels) at the
# CU. Our mvs are per-PU; pick any covering the CU origin with the right list.
def our_mv(loc, lst):
    m = mv[(mv["list"] == lst) & (mv["x"] <= loc[0]) & (loc[0] < mv["x"] + mv["w"])
           & (mv["y"] <= loc[1]) & (loc[1] < mv["y"] + mv["h"])]
    return None if len(m) == 0 else (round(float(m[0]["mv_x"]) * 4),
                                     round(float(m[0]["mv_y"]) * 4))


mv0_ok = mv0_n = mv1_ok = mv1_n = 0
for loc in common:
    c = cus[loc]
    if "mv0" in c:
        mv0_n += 1
        mv0_ok += our_mv(loc, 0) == c["mv0"]
    if "mv1" in c:
        mv1_n += 1
        mv1_ok += our_mv(loc, 1) == c["mv1"]
print(f"MV L0 match: {mv0_ok}/{mv0_n}   MV L1 match: {mv1_ok}/{mv1_n}")
