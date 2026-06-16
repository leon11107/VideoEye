"""Compare our H.264 per-MB side info against Elecard's *rich* block dump.

Usage: py -3.14 tools/compare_elecard_h264_rich.py <blocks.csv> <our.veblk> <frame>

The rich Elecard H.264 dump (one block per macroblock) carries, per MB:
  MB location / slice id / type / size total\\prediction\\transform /
  field-frame / transform 8x8 / cbp / qp / qp_delta, then per partition a
  (dimension, sub_pdir|sub_pmode[, mv\\mvd]) tuple.

We compare every field we currently export and report the rest as gaps for the
user to judge. <frame> is our sidecar frame index (display order).
"""
import sys
import numpy as np

sys.path.insert(0, ".")
from src.analysis.veye_sidecar import (
    load_sidecar, blocks_from_frame, bit_sizes_from_frame, qp_grid_from_frame,
    mvs_from_frame, _H264_BLK_SCAN,
)
from src.analysis.schema import PredType

csv_path, veblk, frame_idx = sys.argv[1], sys.argv[2], int(sys.argv[3])

# Elecard intra sub_pdir name -> canonical mode. I_16x16 uses V/H/DC/Plane;
# I_4x4/I_8x8 use the 9 directional names.
SUBP = {
    "VERTICAL_MODE": 0, "HORIZONTAL_MODE": 1, "DC_MODE": 2,
    "DIAGONAL_DOWN_LEFT_MODE": 3, "DIAGONAL_DOWN_RIGHT_MODE": 4,
    "VERTICAL_RIGHT_MODE": 5, "HORIZONTAL_DOWN_MODE": 6,
    "VERTICAL_LEFT_MODE": 7, "HORIZONTAL_UP_MODE": 8, "PLANE_MODE": 3,
}


def parse(path):
    mbs = {}
    cur = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        next(fh)
        for line in fh:
            line = line.rstrip("\n").rstrip(",")
            if "," not in line:
                continue
            name, val = line.split(",", 1)
            if name == "MB location":
                x, y = val.split("x")
                cur = (int(x), int(y))
                mbs[cur] = {"sub": [], "mv": []}
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
            elif name == "MB cbp":
                mbs[cur]["cbp"] = int(val)
            elif name == "MB qp":
                mbs[cur]["qp"] = int(val)
            elif name == "MB transform 8x8":
                mbs[cur]["t8"] = (val.strip() == "true")
            elif name == "MB dimension":
                mbs[cur].setdefault("dims", []).append(val.strip())
            elif name == "MB sub_pdir":
                mbs[cur]["sub"].append(val.strip())
            elif name == "MB sub_pmode":
                mbs[cur]["sub"].append(val.strip())
            elif name.startswith("MB mv[0]"):
                a = val.split("/")[0].split(",")     # mvx, mvy, ref
                mbs[cur].setdefault("mv0", []).append((int(a[0]), int(a[1])))
            elif name.startswith("MB mv[1]"):
                a = val.split("/")[0].split(",")
                mbs[cur].setdefault("mv1", []).append((int(a[0]), int(a[1])))
    return mbs


mbs = parse(csv_path)
fb = load_sidecar(veblk)[frame_idx]
unit = fb.block_unit
print(f"Elecard: {len(mbs)} MBs   our frame {frame_idx} poc={fb.own_poc} "
      f"grid={fb.mb_type.shape}")
types = {}
for m in mbs.values():
    t = m.get("type", "?").split("_")[0]
    types[t] = types.get(t, 0) + 1
print(f"Elecard MB type families: {types}")

bs = {(int(r["x"]), int(r["y"])): r for r in bit_sizes_from_frame(fb)}
qg = qp_grid_from_frame(fb)
b = blocks_from_frame(fb)
blk = {(int(r["x"]), int(r["y"])): r for r in b}
common = sorted(set(mbs) & set(bs))
print(f"matched by location: {len(common)}  (Elecard-only "
      f"{len(set(mbs)-set(bs))}, ours-only {len(set(bs)-set(mbs))})")


def csv_pred(t):
    if t.startswith("I_") or t.startswith("SI"):
        return PredType.INTRA
    if "Skip" in t or "Direct" in t:
        return PredType.SKIP
    if t.startswith("B_L0") or t.startswith("B_L1") or t.startswith("P_"):
        return PredType.INTER
    if t.startswith("B_Bi") or t.startswith("B_"):
        return PredType.BI
    return PredType.INTER


from collections import defaultdict
tot = tr = sl = pc = im = imn = 0
qp = qpn = 0
pc_miss = defaultdict(int)            # Elecard type -> mismatch count
for loc in common:
    c = mbs[loc]
    r = bs[loc]
    col, row = loc[0] // unit, loc[1] // unit
    tot += int(r["cu"]) == c.get("total")
    tr += int(r["tu"]) == c.get("trans")
    if "qp" in c and qg is not None:  # skip MBs carry no qp in the dump
        qpn += 1
        qp += int(qg[row, col]) == c["qp"]
    if "slice" in c:
        sl += int(fb.mb_slice_id[row, col]) == c["slice"]
    if csv_pred(c.get("type", "")) == int(blk[loc]["pred"]):
        pc += 1
    else:
        pc_miss[c.get("type", "?")] += 1
    # intra mode: compare ALL Elecard sub_pdir entries to our per-4x4 mode grid
    # (v11), mapped to the same H.264 4x4 block scan order Elecard uses.
    if c.get("type", "").startswith("I_") and c["sub"] and fb.mb_luma_mode4 is not None:
        subs = [s for s in c["sub"] if s in SUBP]
        ours16 = fb.mb_luma_mode4[row, col]      # 16 modes, H.264 block-scan
        if len(subs) == 16:                      # I_4x4: Elecard is raster order
            raster = [0] * 16
            for i in range(16):
                x4, y4 = _H264_BLK_SCAN[i]
                raster[y4 * 4 + x4] = int(ours16[i])
            for k, sp in enumerate(subs):
                imn += 1
                im += raster[k] == SUBP[sp]
        elif len(subs) == 4:                     # I_8x8: per-8x8 (block k*4)
            for k, sp in enumerate(subs):
                imn += 1
                im += int(ours16[k * 4]) == SUBP[sp]
        elif len(subs) == 1:                     # I_16x16
            imn += 1
            im += int(ours16[0]) == SUBP[subs[0]]

n = len(common)
print(f"total bits   match: {tot}/{n}")
print(f"transform    match: {tr}/{n}")
print(f"qp           match: {qp}/{qpn}  (skip MBs carry no qp in the dump)")
print(f"slice id     match: {sl}/{n}")
print(f"pred-class   match: {pc}/{n}")
if pc_miss:
    print(f"  pred-class mismatches by Elecard type: {dict(pc_miss)}")
print(f"intra mode (all sub-blocks) match: {im}/{imn}")

# motion vectors: every Elecard partition MV (per list) must appear among our
# 4 quadrant MVs for that MB (1/4-pel raw units).
if fb.mb_mv is not None:
    mv0_ok = mv0_n = mv1_ok = mv1_n = 0
    for loc in common:
        c = mbs[loc]
        col, row = loc[0] // unit, loc[1] // unit
        q = fb.mb_mv[row, col]
        our0 = {(int(q[k, 0]), int(q[k, 1])) for k in range(4)}
        our1 = {(int(q[k, 2]), int(q[k, 3])) for k in range(4)}
        for e in c.get("mv0", []):
            mv0_n += 1; mv0_ok += e in our0
        for e in c.get("mv1", []):
            mv1_n += 1; mv1_ok += e in our1
    print(f"MV L0 match: {mv0_ok}/{mv0_n}   MV L1 match: {mv1_ok}/{mv1_n}")

# gaps
multi = sum(1 for m in mbs.values() if len(m["sub"]) > 1)
print(f"\n-- remaining gaps to judge --")
print(f"Elecard MBs with >1 sub-partition (per-sub modes/dims): {multi} "
      f"(we export one representative intra mode + MB-level 16x16 dimension; "
      f"MVs are exported at 8x8-quadrant granularity)")
