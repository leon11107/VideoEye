"""Validate our AV1 INTER-frame per-block fields (prediction mode, motion
vector, reference frame) against an Elecard dump's strm1..strm5 block lists.

Elecard's blocks_*.csv has no coordinates, so we compare *multisets/histograms*
over all blocks of a frame (order differs, contents must match). Our frame is
located by own_poc == Elecard order_hint.

Usage: py -3.14 tools/compare_elecard_av1_inter.py [dump_dir] [sidecar.veblk]
"""
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
from src.analysis.veye_sidecar import (
    load_sidecar, _AV1_BSIZE_W, _AV1_BSIZE_H, _AV1_BSIZE_WH,
)
from src.analysis.schema import PredType
from src.analysis.labels import _AV1_PRED_MODES

DUMP = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    "tests/streams/av1_1080p_elecard_dump")
SIDECAR = sys.argv[2] if len(sys.argv) > 2 else "tests/streams/av1_probe.veblk"

# AV1 ref-frame enum (aom/av1/common/enums.h) -> Elecard name.
REF_NAMES = {0: "INTRA_FRAME", 1: "LAST_FRAME", 2: "LAST2_FRAME",
             3: "LAST3_FRAME", 4: "GOLDEN_FRAME", 5: "BWDREF_FRAME",
             6: "ALTREF2_FRAME", 7: "ALTREF_FRAME"}


def parse_inter_blocks(dirn):
    g = list((DUMP / dirn).glob("*blocks_*.csv"))
    if not g:
        return None
    inter_type, ref0, mv0 = Counter(), Counter(), Counter()
    n_intra = 0
    with open(g[0], encoding="utf-8", errors="replace") as fh:
        next(fh)
        for line in fh:
            line = line.rstrip("\n").rstrip(",")
            if "," not in line:
                continue
            name, val = line.split(",", 1)
            if name == "pr inter type":
                inter_type[val] += 1
            elif name == "pr ref_frame[0]":
                ref0[val] += 1
            elif name == "pr intra type luma":
                n_intra += 1
            elif name == "pr mv[0]":
                a, _, b = val.partition(",")
                mv0[(int(a), int(b.strip()))] += 1
    return {"inter_type": inter_type, "ref0": ref0, "mv0": mv0,
            "n_intra": n_intra}


def our_origins(fb):
    """Coding-block origins (ys, xs into the MI grid) -- same rule as
    _blocks_from_av1."""
    unit = fb.block_unit
    gh, gw = fb.bsize.shape
    bs = fb.bsize.astype(np.int32)
    valid = (bs >= 0) & (bs < len(_AV1_BSIZE_WH))
    bsc = np.where(valid, bs, 0)
    bw = _AV1_BSIZE_W[bsc]
    bh = _AV1_BSIZE_H[bsc]
    mx = np.arange(gw, dtype=np.int32)[None, :]
    my = np.arange(gh, dtype=np.int32)[:, None]
    origin = valid & (((mx * unit) % bw) == 0) & (((my * unit) % bh) == 0)
    return np.nonzero(origin)


def our_inter_stats(fb):
    ys, xs = our_origins(fb)
    pred = fb.pred[ys, xs].astype(np.int32)
    mode = fb.mode[ys, xs].astype(np.int32)
    mv = fb.mv[ys, xs]               # (N, 2, 2): [list][x,y]
    ref = fb.ref_idx[ys, xs]         # (N, 2)
    skip = fb.skip[ys, xs].astype(np.int32)
    inter = (pred == PredType.INTER) | (pred == PredType.BI)
    # AV1 skip_mode: forced compound NEAREST_NEARESTMV (mode 17) with the two
    # implicit skip-mode refs and residual skipped (skip_txfm=1). Elecard prints
    # ONLY "pr dimension" for these (no inter type/ref/mv lines), so to line up
    # with its typed list we separate them out.
    skip_mode = inter & (mode == 17) & (skip == 1)
    it = Counter()
    r0 = Counter()
    m0 = Counter()
    for i in np.nonzero(inter & ~skip_mode)[0]:
        m = int(mode[i])
        it[_AV1_PRED_MODES[m] if 0 <= m < len(_AV1_PRED_MODES) else f"m{m}"] += 1
        r0[REF_NAMES.get(int(ref[i, 0]), f"ref{int(ref[i,0])}")] += 1
        m0[(int(mv[i, 0, 0]), int(mv[i, 0, 1]))] += 1
    n_intra = int((pred == PredType.INTRA).sum())
    return {"inter_type": it, "ref0": r0, "mv0": m0, "n_intra": n_intra,
            "n_inter": int(inter.sum()), "n_skipmode": int(skip_mode.sum())}


def cmp_counter(name, e, o, top=10):
    same = (e == o)
    print(f"  {name}: {'MATCH' if same else 'DIFF'}  "
          f"(E total {sum(e.values())}, our total {sum(o.values())})")
    if not same:
        keys = sorted(set(e) | set(o), key=lambda k: -(e.get(k, 0) + o.get(k, 0)))
        for k in keys[:top]:
            mark = "" if e.get(k, 0) == o.get(k, 0) else "  <--"
            print(f"      {str(k):>16}  E:{e.get(k,0):>6}  our:{o.get(k,0):>6}{mark}")
    return same


def main():
    frames = load_sidecar(SIDECAR)
    if not frames:
        print(f"FAILED to load sidecar {SIDECAR}")
        return
    by_oh = {}
    for i in sorted(frames):
        by_oh.setdefault(frames[i].own_poc, i)

    # decode-order order_hints for strm0..strm5 (from earlier headers parse):
    # strm0=KEY(oh0); the rest are inter. We re-derive each strm's oh by reading
    # its own blocks against every frame is overkill -- instead map by the known
    # decode order_hints.
    oh_by_strm = {1: 16, 2: 8, 3: 4, 4: 2, 5: 1}

    for d in (1, 2, 3, 4, 5):
        eb = parse_inter_blocks(f"strm{d}")
        if not eb:
            continue
        oh = oh_by_strm[d]
        i = by_oh.get(oh)
        if i is None:
            print(f"strm{d} oh{oh}: no matching frame")
            continue
        os_ = our_inter_stats(frames[i])
        e_typed = sum(eb["inter_type"].values())
        e_total_pr = eb["n_intra"] + e_typed  # typed lines only
        print(f"\n=== strm{d}  order_hint {oh}  (our frame {i}) ===")
        print(f"  intra: E {eb['n_intra']} our {os_['n_intra']}   "
              f"typed-inter: E {e_typed} our {os_['n_inter'] - os_['n_skipmode']}"
              f"   skip_mode: our {os_['n_skipmode']} "
              f"(Elecard prints dimension-only)")
        cmp_counter("inter mode (excl skip_mode)", eb["inter_type"],
                    os_["inter_type"])
        cmp_counter("ref_frame[0]", eb["ref0"], os_["ref0"])
        cmp_counter("MV[0] multiset", eb["mv0"], os_["mv0"])


if __name__ == "__main__":
    main()
