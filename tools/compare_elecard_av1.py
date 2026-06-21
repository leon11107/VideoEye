"""Compare our AV1 side-info (.veblk sidecar) against an Elecard dump.

Usage:
  py -3.14 tools/compare_elecard_av1.py [dump_dir] [video] [sidecar.veblk]

Elecard dump layout (per the av1_1080p_elecard_dump folder):
  <strmN>/*.index.csv    whole-stream per-frame table (decode + display order,
                         type, quant=avg-AC-qindex, size, poc) -- same in every
                         strm dir.
  <strmN>/*.headers.csv  parsed OBU syntax: per coded frame order_hint,
                         frame_type, show_frame, base_q_idx (decode order).
  <strmN>/*.picture.csv  per *decoded* frame ac/dc qindex min/max/avg.
  <strmN>/*.blocks_*.csv per *decoded* frame flat tr/pr block list.
Only strm0..strm6 carry block/picture detail (the first 7 decoded frames);
index.csv covers all frames.

Our side: veye_probe writes the .veblk in display order; each frame's own_poc
is the AV1 order_hint, so we key our frames by order_hint to line them up with
Elecard's decode-order headers/strm dirs.
"""
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
from src.analysis.veye_sidecar import (
    load_sidecar, blocks_from_frame, intra_modes_from_frame,
    tu_luma_from_frame, qp_grid_from_frame,
)
from src.analysis.labels import _AV1_PRED_MODES

DUMP = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    "tests/streams/av1_1080p_elecard_dump")
VIDEO = sys.argv[2] if len(sys.argv) > 2 else "tests/streams/av1_1080p.mp4"
SIDECAR = sys.argv[3] if len(sys.argv) > 3 else "/tmp/av1_probe.veblk"

FT = {0: "KEY", 1: "INTER", 2: "INTRA_ONLY", 3: "SWITCH"}


def find(dirn, suffix):
    g = list((DUMP / dirn).glob(f"*{suffix}"))
    return g[0] if g else None


# ----------------------------------------------------------------- index.csv
def parse_index():
    """Whole-stream per-frame table -> list of dict rows."""
    p = find("strm0", ".index.csv")
    rows = []
    with open(p, encoding="utf-8", errors="replace") as fh:
        hdr = next(fh).rstrip("\n").split(",")
        cols = [c.strip() for c in hdr]
        for line in fh:
            parts = line.rstrip("\n").split(",")
            if len(parts) < 6 or not parts[0].strip().isdigit():
                continue
            row = {cols[i]: parts[i].strip() for i in range(min(len(cols), len(parts)))}
            rows.append(row)
    return rows


# --------------------------------------------------------------- headers.csv
def parse_headers():
    """Per coded frame (decode order): order_hint, frame_type, show_frame,
    show_existing, base_q_idx. Frames are delimited by show_existing_frame."""
    p = find("strm0", ".headers.csv")
    frames = []
    cur = None

    def num(v):
        m = re.match(r"\s*(-?\d+)", v)
        return int(m.group(1)) if m else None

    with open(p, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split(",")
            if len(parts) < 3:
                continue
            name = parts[1].strip()
            val = parts[2].strip()
            if name == "show_existing_frame":
                cur = {"show_existing": num(val), "frame_type": None,
                       "show_frame": None, "order_hint": None,
                       "base_q_idx": None}
                frames.append(cur)
            elif cur is None:
                continue
            elif name == "frame_type":
                cur["frame_type"] = num(val)
            elif name == "show_frame" and cur["show_frame"] is None:
                cur["show_frame"] = num(val)
            elif name == "order_hint" and cur["order_hint"] is None:
                cur["order_hint"] = num(val)
            elif name == "base_q_idx" and cur["base_q_idx"] is None:
                cur["base_q_idx"] = num(val)
    return frames


# ------------------------------------------------------------- picture.csv
def parse_picture(dirn):
    p = find(dirn, ".picture.csv")
    if not p:
        return None
    out = {}
    txt = open(p, encoding="utf-8", errors="replace").read()
    m = re.search(r"qp min / max \(ac\),\s*(\d+)\s*/\s*(\d+)", txt)
    if m:
        out["ac_min"], out["ac_max"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"avg \(ac\),\s*([\d.]+)", txt)
    if m:
        out["ac_avg"] = float(m.group(1))
    m = re.search(r"size \(bytes\),\s*(\d+)", txt)
    if m:
        out["size"] = int(m.group(1))
    return out


# -------------------------------------------------------------- blocks_*.csv
def parse_blocks(dirn):
    g = list((DUMP / dirn).glob("*blocks_*.csv"))
    p = g[0] if g else None
    if not p:
        return None
    tr_dim, pr_dim, pr_luma = Counter(), Counter(), Counter()
    tr_ac, tr_dc = [], []
    with open(p, encoding="utf-8", errors="replace") as fh:
        next(fh)
        for line in fh:
            line = line.rstrip("\n").rstrip(",")
            if "," not in line:
                continue
            name, val = line.split(",", 1)
            if name == "tr dimension":
                tr_dim[val] += 1
            elif name == "pr dimension":
                pr_dim[val] += 1
            elif name == "pr intra type luma":
                pr_luma[val.replace("_PRED", "")] += 1
            elif name == "tr quant":
                a, _, b = val.partition("/")
                try:
                    tr_dc.append(int(a)); tr_ac.append(int(b))
                except ValueError:
                    pass
    return {"tr_dim": tr_dim, "pr_dim": pr_dim, "pr_luma": pr_luma,
            "tr_ac": tr_ac, "tr_dc": tr_dc}


# --------------------------------------------------------------- our sidecar
def our_frame_stats(fb):
    blk = blocks_from_frame(fb)
    sz = Counter(f"{int(b['w'])}x{int(b['h'])}" for b in blk)
    intra = intra_modes_from_frame(fb)
    unit = fb.block_unit
    _FI = ("FILTER_DC", "FILTER_V", "FILTER_H", "FILTER_D157", "FILTER_PAETH")
    md = Counter()
    for r in intra:
        # Match Elecard's "pr intra type luma": palette and filter-intra blocks
        # both keep y_mode=DC, so fold those flags in (MI-granular grids).
        row, col = int(r["y"]) // unit, int(r["x"]) // unit
        pv = int(fb.palette[row, col]) if fb.palette is not None else 0
        fi = int(fb.filter_intra[row, col]) if fb.filter_intra is not None else -1
        if pv > 0:
            md["PALETTE"] += 1
        elif fi >= 0:
            md[_FI[fi] if 0 <= fi < 5 else f"FI{fi}"] += 1
        else:
            m = int(r["mode"])
            md[_AV1_PRED_MODES[m] if 0 <= m < len(_AV1_PRED_MODES) else f"m{m}"] += 1
    tx = tu_luma_from_frame(fb)
    txc = Counter(f"{int(t['w'])}x{int(t['h'])}" for t in tx)
    qp = qp_grid_from_frame(fb).astype(np.int32)
    return {"n_blk": len(blk), "sz": sz, "mode": md, "tx": txc,
            "qp_min": int(qp.min()), "qp_max": int(qp.max()),
            "qp_mean": float(qp.mean()),
            "qp_mode": int(np.bincount(qp.ravel() - qp.min()).argmax() + qp.min()),
            "n_tr": len(tx)}


def hist_line(c, top=12):
    return "  ".join(f"{k}:{v}" for k, v in c.most_common(top))


def main():
    frames = load_sidecar(SIDECAR)
    if not frames:
        print(f"FAILED to load sidecar {SIDECAR}")
        return
    # our frames keyed by order_hint (own_poc)
    by_oh = {}
    for i in sorted(frames):
        oh = frames[i].own_poc
        by_oh.setdefault(oh, i)

    hdr = parse_headers()
    idx = parse_index()
    coded = [h for h in hdr if not h["show_existing"]]

    print("=" * 78)
    print("A. PER-FRAME: type + order_hint + qindex  (Elecard decode order)")
    print("=" * 78)
    print(f"{'dec':>3} {'oh':>4} {'E.type':>6} | {'our#':>4} {'our.oh':>6} "
          f"| {'E.q(idx)':>8} {'our.qmean':>9} {'our.qmin':>8} {'our.qmax':>8}")
    # map index.csv quant by order? index.csv has no order_hint; use poc/quant
    # via decode 'stream'. Build {decode_stream -> quant} from index.csv.
    quant_by_stream = {}
    for r in idx:
        s = r.get("stream")
        if s and s.isdigit():
            q = r.get("quant", "")
            quant_by_stream[int(s)] = q
    # coded frames keep their original decode-stream index
    coded_stream = [i for i, h in enumerate(hdr) if not h["show_existing"]]
    type_ok = oh_ok = 0
    for n, (h, st) in enumerate(zip(coded, coded_stream)):
        oh = h["order_hint"]
        ours_i = by_oh.get(oh)
        our_oh = frames[ours_i].own_poc if ours_i is not None else None
        if ours_i is not None:
            s = our_frame_stats(frames[ours_i])
            qmean, qmin, qmax = f"{s['qp_mean']:.1f}", s["qp_min"], s["qp_max"]
        else:
            qmean = qmin = qmax = "-"
        eq = quant_by_stream.get(st, "?")
        match_oh = (our_oh == oh)
        oh_ok += match_oh
        print(f"{st:>3} {str(oh):>4} {FT.get(h['frame_type'],'?'):>6} | "
              f"{str(ours_i):>4} {str(our_oh):>6} | {eq:>8} "
              f"{qmean:>9} {str(qmin):>8} {str(qmax):>8}"
              f"{'' if match_oh else '   <-- oh MISMATCH'}")
    print(f"\norder_hint matched: {oh_ok}/{len(coded)} "
          f"(our distinct order_hints: {len(by_oh)})")

    print()
    print("=" * 78)
    print("B. KEYFRAME (strm0) BLOCK-LEVEL")
    print("=" * 78)
    kb = parse_blocks("strm0")
    kp = parse_picture("strm0")
    k_oh = coded[0]["order_hint"]
    ki = by_oh.get(k_oh, 0)
    s = our_frame_stats(frames[ki])
    print(f"Elecard pr blocks: {sum(kb['pr_dim'].values())}   "
          f"our coding blocks: {s['n_blk']}")
    print(f"  E pr_dim : {hist_line(kb['pr_dim'])}")
    print(f"  our size : {hist_line(s['sz'])}")
    print(f"\nElecard pr intra luma modes (count {sum(kb['pr_luma'].values())}):")
    print(f"  E  : {hist_line(kb['pr_luma'])}")
    print(f"  our: {hist_line(s['mode'])}")
    print(f"\nElecard tr blocks: {sum(kb['tr_dim'].values())}   "
          f"our tx (luma): {s['n_tr']}")
    print(f"  E tr_dim : {hist_line(kb['tr_dim'])}")
    print(f"  our tx   : {hist_line(s['tx'])}")
    if kb["tr_ac"]:
        ac = np.array(kb["tr_ac"]); dc = np.array(kb["tr_dc"])
        print(f"\nElecard tr quant  ac: min{ac.min()} max{ac.max()} "
              f"mean{ac.mean():.2f}   dc: min{dc.min()} max{dc.max()} "
              f"mean{dc.mean():.2f}")
    if kp:
        print(f"Elecard picture.csv ac: min{kp.get('ac_min')} "
              f"max{kp.get('ac_max')} avg{kp.get('ac_avg')}")
    print(f"our qp grid (current_qindex): min{s['qp_min']} max{s['qp_max']} "
          f"mean{s['qp_mean']:.2f} mode{s['qp_mode']}")

    print()
    print("=" * 78)
    print("C. PER-FRAME QINDEX vs picture.csv (strm0..strm5 detailed)")
    print("=" * 78)
    print(f"{'strm':>4} {'oh':>4} | {'E.ac_min':>8} {'E.ac_max':>8} "
          f"{'E.ac_avg':>8} | {'our_min':>7} {'our_max':>7} {'our_mean':>8}")
    for d in range(6):
        pic = parse_picture(f"strm{d}")
        if not pic or "ac_avg" not in pic:
            continue
        oh = coded[d]["order_hint"] if d < len(coded) else None
        ours_i = by_oh.get(oh)
        if ours_i is None:
            continue
        s = our_frame_stats(frames[ours_i])
        print(f"{d:>4} {str(oh):>4} | {pic['ac_min']:>8} {pic['ac_max']:>8} "
              f"{pic['ac_avg']:>8} | {s['qp_min']:>7} {s['qp_max']:>7} "
              f"{s['qp_mean']:>8.2f}")

    print()
    print("=" * 78)
    print("D. BLOCK-SIZE PARTITION per detailed frame (strm0..strm5)")
    print("=" * 78)
    print(f"{'strm':>4} {'oh':>4} {'E.type':>6} | {'E.blks':>6} {'our.blks':>8} "
          f"| {'sizes match?':>12}")
    for d in range(6):
        kb = parse_blocks(f"strm{d}")
        if not kb:
            continue
        oh = coded[d]["order_hint"] if d < len(coded) else None
        ours_i = by_oh.get(oh)
        if ours_i is None or not kb["pr_dim"]:
            continue
        s = our_frame_stats(frames[ours_i])
        e_n = sum(kb["pr_dim"].values())
        match = (kb["pr_dim"] == s["sz"])
        ft = FT.get(coded[d]["frame_type"], "?")
        print(f"{d:>4} {str(oh):>4} {ft:>6} | {e_n:>6} {s['n_blk']:>8} "
              f"| {'YES' if match else 'NO':>12}")
        if not match:
            print(f"       E  : {hist_line(kb['pr_dim'])}")
            print(f"       our: {hist_line(s['sz'])}")


if __name__ == "__main__":
    main()
