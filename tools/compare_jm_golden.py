"""Validate the patched-FFmpeg H.264 sidecar against a JM golden dump.

JM (the H.264 reference decoder, ldecod) and our patched FFmpeg decode the
*same* bitstream, so per-macroblock QP and prediction type must match
exactly. Any mismatch is a bug in our extraction, not a tolerance issue.

Scope: the H.264 sidecar carries only per-MB mb_type + QP (motion vectors
come from mainline FFmpeg, validated separately). So this comparator checks
the two fields the patched code produces: QP and prediction type
(1=intra, 2=inter, 3=skip, 4=ipcm), per 16x16 macroblock.

Frame alignment: the sidecar is keyed by FFmpeg display/output order
(0..N-1). JM dumps in decode order with GOP-local POC (resets to 0 at each
IDR). We split the decode sequence into GOPs at every POC==0 and, within
each GOP, sort by POC to recover display order — matching FFmpeg's output.

The JM CSV is one row per macroblock: decidx,poc,x,y,qp,pred,mb_type.
"""

import argparse
import subprocess
import sys

import numpy as np

sys.path.insert(0, ".")
from src.analysis.veye_sidecar import (
    load_sidecar, MB_TYPE_INTRA_PCM, MB_TYPE_INTRA4x4, MB_TYPE_INTRA16x16,
    MB_TYPE_SKIP,
)

MB = 16  # H.264 macroblock size in luma pixels


def mb_pred(t: int) -> int:
    """Collapse an FFmpeg MB_TYPE bitmask to the VideoEye per-MB pred code.

    Order matches JM's classification: IPCM, then intra, then skip, else
    inter — so IPCM (which is also intra) maps to 4, not 1.
    """
    if t & MB_TYPE_INTRA_PCM:
        return 4
    if t & (MB_TYPE_INTRA4x4 | MB_TYPE_INTRA16x16):
        return 1
    if t & MB_TYPE_SKIP:
        return 3
    return 2


def load_golden(path):
    """CSV -> (per-decidx grids, decidx->display-order map, gh, gw)."""
    data = np.loadtxt(path, delimiter=",", skiprows=1, dtype=np.int64)
    decidx = data[:, 0]
    poc = data[:, 1]
    gx = data[:, 2] // MB
    gy = data[:, 3] // MB
    gw = int(gx.max()) + 1
    gh = int(gy.max()) + 1

    frames = {}          # decidx -> {"qp": grid, "pred": grid}
    poc_of = {}          # decidx -> poc (constant within a frame)
    for d in np.unique(decidx):
        m = decidx == d
        poc_of[int(d)] = int(poc[m][0])
        qp = np.full((gh, gw), -999, dtype=np.int64)
        pred = np.full((gh, gw), -999, dtype=np.int64)
        qp[gy[m], gx[m]] = data[m, 4]
        pred[gy[m], gx[m]] = data[m, 5]
        frames[int(d)] = {"qp": qp, "pred": pred}

    # Recover display order: split into GOPs at each IDR (poc==0), then
    # sort each GOP by POC and assign a global display index.
    disp = {}
    base = 0
    cur = []
    order = sorted(poc_of)
    for d in order:
        if poc_of[d] == 0 and cur:
            for rank, dd in enumerate(sorted(cur, key=lambda x: poc_of[x])):
                disp[dd] = base + rank
            base += len(cur)
            cur = []
        cur.append(d)
    for rank, dd in enumerate(sorted(cur, key=lambda x: poc_of[x])):
        disp[dd] = base + rank

    return frames, poc_of, disp, gh, gw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="tests/streams/h264_1080p_default.mp4")
    ap.add_argument("--golden", default="C:/tmp/jm_golden.csv")
    ap.add_argument("--probe", default="native/veye_probe.exe")
    ap.add_argument("--show", type=int, default=8,
                    help="max sample mismatches to print per field")
    args = ap.parse_args()

    print(f"loading JM golden: {args.golden}")
    golden, poc_of, disp, gh, gw = load_golden(args.golden)
    print(f"  {len(golden)} frames, grid {gw}x{gh}")

    out = "C:/tmp/_cmp_jm.veblk"
    subprocess.run([args.probe, args.video, out], check=True)
    sidecar = load_sidecar(out)
    if not sidecar:
        print("FAIL: sidecar empty")
        return 1
    print(f"  sidecar {len(sidecar)} frames")

    totals = {"qp": [0, 0], "pred": [0, 0]}
    samples = {k: [] for k in totals}

    def cmp_field(name, g, s, di, poc):
        h = min(g.shape[0], s.shape[0])
        w = min(g.shape[1], s.shape[1])
        g = g[:h, :w]
        s = s[:h, :w]
        valid = g != -999
        tot = int(valid.sum())
        bad = int((valid & (g != s)).sum())
        totals[name][0] += tot - bad
        totals[name][1] += bad
        if bad and len(samples[name]) < args.show:
            ys, xs = np.where(valid & (g != s))
            for yi, xi in zip(ys[:args.show], xs[:args.show]):
                samples[name].append(
                    f"  disp{di}(poc{poc}) mb({xi},{yi}) "
                    f"golden={g[yi, xi]} sidecar={s[yi, xi]}")

    for d in sorted(golden):
        di = disp[d]
        if di not in sidecar:
            print(f"  decidx {d} -> display {di} missing in sidecar")
            continue
        G = golden[d]
        fb = sidecar[di]
        if fb.mb_type is None:
            print(f"  display {di}: sidecar has no mb_type (not H.264?)")
            continue
        s_pred = np.vectorize(mb_pred)(fb.mb_type.astype(np.int64))
        cmp_field("qp", G["qp"], fb.qp.astype(np.int64), di, poc_of[d])
        cmp_field("pred", G["pred"], s_pred, di, poc_of[d])

    print("\n=== per-field results (matched / mismatched) ===")
    any_bad = False
    for name, (ok, bad) in totals.items():
        tot = ok + bad
        rate = (100.0 * ok / tot) if tot else 100.0
        flag = "" if bad == 0 else "  <-- MISMATCH"
        print(f"  {name:8s} {ok:9d}/{tot:<9d} {rate:6.2f}%{flag}")
        if bad:
            any_bad = True
            for s in samples[name]:
                print(s)

    print("\n" + ("=== MISMATCHES FOUND ===" if any_bad
                   else "=== ALL FIELDS MATCH JM GOLDEN ==="))
    return 1 if any_bad else 0


if __name__ == "__main__":
    sys.exit(main())
