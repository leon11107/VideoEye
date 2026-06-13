"""Validate the patched-FFmpeg HEVC sidecar against an HM golden dump.

HM (the HEVC reference decoder) and our patched FFmpeg decode the *same*
bitstream, so per-min-CB QP / CU-size / prediction-type / motion vectors
must match exactly. Any mismatch is a bug in our extraction, not a
tolerance issue.

Inputs:
    --video  the .mp4/.265 to analyze (FFmpeg sidecar source)
    --golden the CSV emitted by the patched HM (VEYE_HM_DUMP=...)

The HM CSV is one row per min-CB: poc,x,y,cu_size,pred,intra_mode,qp,
mv0x,mv0y,ref0,mv1x,mv1y,ref1. POC equals display order for these streams.
"""

import argparse
import subprocess
import sys

import numpy as np

sys.path.insert(0, ".")
from src.analysis.veye_sidecar import load_sidecar


def load_golden(path):
    """CSV -> {poc: dict of (h,w) grids} keyed by field name."""
    cols = ["poc", "x", "y", "cu_size", "pred", "intra_mode", "qp",
            "mv0x", "mv0y", "ref0", "mv1x", "mv1y", "ref1"]
    data = np.loadtxt(path, delimiter=",", skiprows=1, dtype=np.int64)
    poc = data[:, 0]
    gx = data[:, 1] // 8
    gy = data[:, 2] // 8
    gw = int(gx.max()) + 1
    gh = int(gy.max()) + 1
    frames = {}
    for p in np.unique(poc):
        m = poc == p
        g = {}
        for ci, name in enumerate(cols[3:], start=3):
            grid = np.full((gh, gw), -999, dtype=np.int64)
            grid[gy[m], gx[m]] = data[m, ci]
            g[name] = grid
        frames[int(p)] = g
    return frames, gh, gw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="tests/streams/hevc_1080p.mp4")
    ap.add_argument("--golden", default="C:/tmp/hm_golden.csv")
    ap.add_argument("--probe", default="native/veye_probe.exe")
    ap.add_argument("--show", type=int, default=8,
                    help="max sample mismatches to print per field")
    args = ap.parse_args()

    print(f"loading HM golden: {args.golden}")
    golden, gh, gw = load_golden(args.golden)
    print(f"  {len(golden)} frames, grid {gw}x{gh}")

    out = "C:/tmp/_cmp.veblk"
    subprocess.run([args.probe, args.video, out], check=True)
    sidecar = load_sidecar(out)
    if not sidecar:
        print("FAIL: sidecar empty")
        return 1
    print(f"  sidecar {len(sidecar)} frames")

    totals = {"qp": [0, 0], "cu_size": [0, 0], "pred": [0, 0],
              "mv0x": [0, 0], "mv0y": [0, 0], "ref0": [0, 0],
              "mv1x": [0, 0], "mv1y": [0, 0], "ref1": [0, 0]}
    samples = {k: [] for k in totals}

    def cmp_field(name, g, s, mask, poc):
        m = mask
        tot = int(m.sum())
        bad = int((g[m] != s[m]).sum())
        totals[name][0] += tot - bad
        totals[name][1] += bad
        if bad and len(samples[name]) < args.show:
            ys, xs = np.where(m & (g != s))
            for yi, xi in zip(ys[:args.show], xs[:args.show]):
                samples[name].append(
                    f"  poc{poc} cell({xi},{yi}) golden={g[yi, xi]} sidecar={s[yi, xi]}")

    for poc in sorted(golden):
        if poc not in sidecar:
            print(f"  poc {poc} missing in sidecar")
            continue
        G = golden[poc]
        fb = sidecar[poc]
        allcells = np.ones((gh, gw), dtype=bool)

        cmp_field("qp", G["qp"], fb.qp.astype(np.int64), allcells, poc)
        cmp_field("cu_size", G["cu_size"],
                  (1 << fb.cu_log2.astype(np.int64)), allcells, poc)
        cmp_field("pred", G["pred"], fb.pred.astype(np.int64), allcells, poc)

        # Motion vectors: only where both sides agree the cell is inter/skip.
        inter = (G["pred"] >= 2) & (fb.pred.astype(np.int64) >= 2)
        l0 = inter & (G["ref0"] >= 0) & (fb.ref_idx[..., 0].astype(np.int64) >= 0)
        l1 = inter & (G["ref1"] >= 0) & (fb.ref_idx[..., 1].astype(np.int64) >= 0)
        cmp_field("ref0", G["ref0"], fb.ref_idx[..., 0].astype(np.int64), inter, poc)
        cmp_field("ref1", G["ref1"], fb.ref_idx[..., 1].astype(np.int64), inter, poc)
        cmp_field("mv0x", G["mv0x"], fb.mv[..., 0, 0].astype(np.int64), l0, poc)
        cmp_field("mv0y", G["mv0y"], fb.mv[..., 0, 1].astype(np.int64), l0, poc)
        cmp_field("mv1x", G["mv1x"], fb.mv[..., 1, 0].astype(np.int64), l1, poc)
        cmp_field("mv1y", G["mv1y"], fb.mv[..., 1, 1].astype(np.int64), l1, poc)

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
                   else "=== ALL FIELDS MATCH HM GOLDEN ==="))
    return 1 if any_bad else 0


if __name__ == "__main__":
    sys.exit(main())
