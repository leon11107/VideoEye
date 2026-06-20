"""Per-block AV1 ground truth via a CONFIG_INSPECTION libaom inspect.exe.

Decodes a clip with libaom's inspector and collapses its 4x4 mode-info grid
into coding blocks (same origin rule as our sidecar), then reports per-frame
block-size / intra-type / palette / intrabc distributions. This is ground truth
INDEPENDENT of Elecard and of our .veblk sidecar -- use it to cross-check both.

NOTE: libaom's insp_mi_data exposes palette size and intrabc but NOT
filter_intra_mode, so filter-intra blocks appear here as their base y_mode
(usually DC_PRED) -- the same blind spot our sidecar has. Only Elecard's own
parser distinguishes filter-intra.

Usage: py -3.14 tools/inspect_av1.py <clip.mp4|.ivf> [--limit N] [--frame K]
Env: VEYE_AOM_INSPECT overrides the inspect.exe path.
"""
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter

import numpy as np

sys.path.insert(0, ".")
from src.analysis.veye_sidecar import _AV1_BSIZE_WH

INSP = os.environ.get(
    "VEYE_AOM_INSPECT",
    r"C:\Users\llw\Desktop\aom\aom_build_insp\inspect.exe")

_BW = np.array([w for w, h in _AV1_BSIZE_WH], dtype=np.int32)
_BH = np.array([h for w, h in _AV1_BSIZE_WH], dtype=np.int32)


def run_inspect(clip, limit=None):
    """Return inspect's parsed frame list (frames carrying a blockSize grid)."""
    tmp = tempfile.gettempdir()
    ivf = os.path.join(tmp, "_veye_insp.ivf")
    js = os.path.join(tmp, "_veye_insp.json")
    if clip.lower().endswith(".ivf"):
        ivf = clip
    else:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", clip, "-c", "copy", "-f", "ivf", ivf], check=True)
    flags = ["-bs", "-m", "-plt", "-uvp", "-ibc", "-s", "-si", "-dq"]
    if limit:
        flags += ["--limit=%d" % limit]
    with open(js, "wb") as fh:
        subprocess.run([INSP, ivf, *flags], stdout=fh,
                       stderr=subprocess.DEVNULL, check=True)
    data = json.load(open(js))
    return [f for f in data if isinstance(f, dict) and "blockSize" in f]


def collapse_blocks(fr):
    """Yield one record per coding block (at its top-left MI cell)."""
    bs = np.array(fr["blockSize"], dtype=np.int32)
    mode = np.array(fr["mode"], dtype=np.int32)
    plt = np.array(fr["palette"], dtype=np.int32)
    uvp = np.array(fr.get("uv_palette", fr["palette"]), dtype=np.int32)
    ibc = np.array(fr["intrabc"], dtype=np.int32)
    skip = np.array(fr["skip"], dtype=np.int32)
    seg = np.array(fr.get("seg_id", np.zeros_like(bs)), dtype=np.int32)
    H, W = bs.shape
    valid = (bs >= 0) & (bs < len(_AV1_BSIZE_WH))
    bsc = np.where(valid, bs, 0)
    bw = _BW[bsc]
    bh = _BH[bsc]
    c = np.arange(W)[None, :]
    r = np.arange(H)[:, None]
    origin = valid & (((c * 4) % bw) == 0) & (((r * 4) % bh) == 0)
    ys, xs = np.nonzero(origin)
    return {
        "bsize": bs[ys, xs], "mode": mode[ys, xs], "plt": plt[ys, xs],
        "uvp": uvp[ys, xs], "ibc": ibc[ys, xs], "skip": skip[ys, xs],
        "seg": seg[ys, xs], "n": len(xs),
    }


# Inverse of inspect's blockSizeMap / modeMap (enum -> WxH name / mode name).
_BS_NAME = {i: f"{w}x{h}" for i, (w, h) in enumerate(_AV1_BSIZE_WH)}
_MODE = ("DC", "V", "H", "D45", "D135", "D113", "D157", "D203", "D67",
         "SMOOTH", "SMOOTH_V", "SMOOTH_H", "PAETH",
         "NEARESTMV", "NEARMV", "GLOBALMV", "NEWMV", "NEAREST_NEARESTMV",
         "NEAR_NEARMV", "NEAREST_NEWMV", "NEW_NEARESTMV", "NEAR_NEWMV",
         "NEW_NEARMV", "GLOBAL_GLOBALMV", "NEW_NEWMV", "INTRA_INVALID")


def intra_type_label(mode, plt, ibc):
    """Elecard-style intra type, to the extent inspect can tell: palette and
    intrabc are explicit; filter-intra is invisible (folds into the base mode)."""
    if plt > 0:
        return "PALETTE"
    if ibc:
        return "INTRABC"
    return _MODE[mode] if 0 <= mode < len(_MODE) else f"m{mode}"


def hist_line(c, top=14):
    return "  ".join(f"{k}:{v}" for k, v in c.most_common(top))


def main():
    clip = sys.argv[1]
    limit = None
    frame_k = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    if "--frame" in sys.argv:
        frame_k = int(sys.argv[sys.argv.index("--frame") + 1])
    frames = run_inspect(clip, limit)
    print(f"clip: {clip}   frames inspected: {len(frames)}")
    print(f"{'frm':>3} {'type':>4} {'bqi':>4} {'blocks':>7} {'palette%':>8} "
          f"{'intrabc':>7} {'plt_blocks':>10}")
    agg_bs, agg_it, agg_plt = Counter(), Counter(), Counter()
    for i, fr in enumerate(frames):
        b = collapse_blocks(fr)
        n = b["n"]
        plt_blocks = int((b["plt"] > 0).sum())
        ibc_blocks = int((b["ibc"] > 0).sum())
        ft = {0: "KEY", 1: "INTER", 2: "IONLY", 3: "SW"}.get(fr["frameType"], "?")
        print(f"{i:>3} {ft:>4} {fr['baseQIndex']:>4} {n:>7} "
              f"{100*plt_blocks/max(1,n):>7.1f}% {ibc_blocks:>7} {plt_blocks:>10}")
        if frame_k is None or frame_k == i:
            for j in range(n):
                agg_bs[_BS_NAME.get(int(b["bsize"][j]), int(b["bsize"][j]))] += 1
                agg_it[intra_type_label(int(b["mode"][j]), int(b["plt"][j]),
                                        int(b["ibc"][j]))] += 1
                if b["plt"][j] > 0:
                    agg_plt[int(b["plt"][j])] += 1

    scope = f"frame {frame_k}" if frame_k is not None else "all inspected frames"
    print(f"\n=== aggregate over {scope} ===")
    print(f"block sizes : {hist_line(agg_bs)}")
    print(f"intra type  : {hist_line(agg_it)}")
    print(f"palette size (#colors -> #blocks): "
          f"{'  '.join(f'{k}:{v}' for k,v in sorted(agg_plt.items()))}")


if __name__ == "__main__":
    main()
