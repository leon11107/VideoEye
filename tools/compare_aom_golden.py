"""Validate the patched-FFmpeg AV1 sidecar against a libaom inspect golden.

libaom's reference inspector (examples/inspect.c, built with
CONFIG_INSPECTION) and our patched FFmpeg decode the *same* bitstream
through the *same* libaom decoder. Every field both sides serialize comes
from the identical insp_mi_data, so per-4x4-MI block-size / QP (current
qindex) / mode / skip / motion-vector / reference / transform-size must
match exactly. Any mismatch is a serialization or Python-parsing bug.

Two ordering quirks this script handles:

  * veye_probe emits frames in *display* order; inspect.exe emits them in
    *decode* order (hierarchical-GOP reordering), so we match by a content
    signature (the per-MI blockSize+mode grids), not by index.
  * inspect.exe emits one trailing ``null`` entry (31 vs 30), which we skip.

Golden per-frame fields (raster [row][col] == [y][x]):
    blockSize   (h,w)   BLOCK_SIZE enum         <-> sidecar bsize
    delta_q     (h,w)   current_qindex per MI   <-> sidecar qp
    mode        (h,w)   PREDICTION_MODE         <-> sidecar mode
    skip        (h,w)   skip_txfm flag          <-> sidecar skip
    transformSize (h,w) TX_SIZE                 <-> sidecar tx_size
    referenceFrame (h,w,2) [ref0, ref1]         <-> sidecar ref_idx
    motionVectors  (h,w,4) [mv0_col,mv0_row,    <-> sidecar mv (col=x, row=y)
                            mv1_col,mv1_row]

Inputs:
    --golden  inspect.exe JSON  (inspect.exe <ivf> -bs -m -r -mv -dq -s -ts)
    --sidecar the .veblk produced by veye_probe on the SAME stream
"""

import argparse
import json
import sys

import numpy as np

sys.path.insert(0, ".")
from src.analysis.veye_sidecar import load_sidecar, _CODEC_AV1


def frame_signature(bsize, mode):
    """A per-frame fingerprint stable across display/decode reordering."""
    h = hash(bsize.tobytes()) ^ (hash(mode.tobytes()) * 1000003)
    return h


def load_golden(path):
    """JSON list -> list of dicts of (h,w[,k]) int64 grids; drops null entries."""
    raw = json.load(open(path))
    frames = []
    for f in raw:
        if f is None:
            continue
        frames.append({
            "bsize": np.asarray(f["blockSize"], dtype=np.int64),
            "qp": np.asarray(f["delta_q"], dtype=np.int64),
            "mode": np.asarray(f["mode"], dtype=np.int64),
            "skip": np.asarray(f["skip"], dtype=np.int64),
            "tx_size": np.asarray(f["transformSize"], dtype=np.int64),
            "ref": np.asarray(f["referenceFrame"], dtype=np.int64),
            "mv": np.asarray(f["motionVectors"], dtype=np.int64),
            "frame": f.get("frame"),
            "frameType": f.get("frameType"),
        })
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default="C:/Users/llw/AppData/Local/Temp/insp_full.json")
    ap.add_argument("--sidecar", default="C:/Users/llw/AppData/Local/Temp/av1_final.veblk")
    ap.add_argument("--show", type=int, default=8,
                    help="max sample mismatches to print per field")
    args = ap.parse_args()

    print(f"loading golden: {args.golden}")
    golden = load_golden(args.golden)
    print(f"  {len(golden)} frames (null entries dropped)")

    sidecar = load_sidecar(args.sidecar)
    if not sidecar:
        print("FAIL: sidecar empty")
        return 1
    print(f"  sidecar {len(sidecar)} frames")

    # Build content-signature -> golden frame.
    gsig = {}
    for gi, G in enumerate(golden):
        sig = frame_signature(G["bsize"], G["mode"])
        if sig in gsig:
            print(f"  WARN: duplicate golden signature (golden frames "
                  f"{gsig[sig]} and {gi}) -- signature collision")
        gsig[sig] = gi

    totals = {k: [0, 0] for k in
              ("bsize", "qp", "mode", "skip", "tx_size",
               "ref0", "ref1", "mv0x", "mv0y", "mv1x", "mv1y")}
    samples = {k: [] for k in totals}
    matched = 0
    unmatched = []

    def cmp_field(name, g, s, mask, disp):
        m = mask
        tot = int(m.sum())
        bad = int((g[m] != s[m]).sum())
        totals[name][0] += tot - bad
        totals[name][1] += bad
        if bad and len(samples[name]) < args.show:
            ys, xs = np.where(m & (g != s))
            for yi, xi in zip(ys[:args.show], xs[:args.show]):
                samples[name].append(
                    f"  disp{disp} cell({xi},{yi}) golden={g[yi, xi]} "
                    f"sidecar={s[yi, xi]}")

    for disp in sorted(sidecar):
        fb = sidecar[disp]
        if fb.codec_id != _CODEC_AV1:
            print(f"  disp {disp}: not AV1 (codec {fb.codec_id}) -- skip")
            continue
        sig = frame_signature(fb.bsize.astype(np.int64),
                              fb.mode.astype(np.int64))
        gi = gsig.get(sig)
        if gi is None:
            unmatched.append(disp)
            continue
        matched += 1
        G = golden[gi]

        bsize_s = fb.bsize.astype(np.int64)
        mode_s = fb.mode.astype(np.int64)
        qp_s = fb.qp.astype(np.int64)
        skip_s = fb.skip.astype(np.int64)
        tx_s = fb.tx_size.astype(np.int64)
        ref0_s = fb.ref_idx[..., 0].astype(np.int64)
        ref1_s = fb.ref_idx[..., 1].astype(np.int64)
        # sidecar mv layout: [..., list, (x=col, y=row)]
        mv0x_s = fb.mv[..., 0, 0].astype(np.int64)
        mv0y_s = fb.mv[..., 0, 1].astype(np.int64)
        mv1x_s = fb.mv[..., 1, 0].astype(np.int64)
        mv1y_s = fb.mv[..., 1, 1].astype(np.int64)

        allcells = np.ones(bsize_s.shape, dtype=bool)
        cmp_field("bsize", G["bsize"], bsize_s, allcells, disp)
        cmp_field("qp", G["qp"], qp_s, allcells, disp)
        cmp_field("mode", G["mode"], mode_s, allcells, disp)
        cmp_field("skip", G["skip"], skip_s, allcells, disp)
        cmp_field("tx_size", G["tx_size"], tx_s, allcells, disp)
        cmp_field("ref0", G["ref"][..., 0], ref0_s, allcells, disp)
        cmp_field("ref1", G["ref"][..., 1], ref1_s, allcells, disp)

        # Motion vectors only where that list points at a real reference.
        # libaom INTRA_FRAME == 0; a >0 ref means the list is used.
        l0 = G["ref"][..., 0] > 0
        l1 = G["ref"][..., 1] > 0
        # golden mv layout: [mv0_col, mv0_row, mv1_col, mv1_row] (x, y, x, y)
        cmp_field("mv0x", G["mv"][..., 0], mv0x_s, l0, disp)
        cmp_field("mv0y", G["mv"][..., 1], mv0y_s, l0, disp)
        cmp_field("mv1x", G["mv"][..., 2], mv1x_s, l1, disp)
        cmp_field("mv1y", G["mv"][..., 3], mv1y_s, l1, disp)

    print(f"\nmatched {matched}/{len(sidecar)} sidecar frames to golden")
    if unmatched:
        print(f"  UNMATCHED sidecar frames (no signature hit): {unmatched}")

    print("\n=== per-field results (matched / mismatched) ===")
    any_bad = bool(unmatched)
    for name, (ok, bad) in totals.items():
        tot = ok + bad
        rate = (100.0 * ok / tot) if tot else 100.0
        flag = "" if bad == 0 else "  <-- MISMATCH"
        print(f"  {name:8s} {ok:10d}/{tot:<10d} {rate:6.2f}%{flag}")
        if bad:
            any_bad = True
            for s in samples[name]:
                print(s)

    print("\n" + ("=== MISMATCHES FOUND ===" if any_bad
                   else "=== ALL FIELDS MATCH LIBAOM INSPECT GOLDEN ==="))
    return 1 if any_bad else 0


if __name__ == "__main__":
    sys.exit(main())
