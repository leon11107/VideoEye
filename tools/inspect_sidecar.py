"""Inspect a .veblk sidecar: per-frame MB-type composition sanity check."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis import veye_sidecar as vs

frames = vs.load_sidecar(sys.argv[1])
if frames is None:
    print("failed to load sidecar")
    raise SystemExit(1)

print(f"frames: {len(frames)}")
for idx in sorted(frames)[:8]:
    fb = frames[idx]
    t = fb.mb_type
    n = t.size
    intra = int(((t & vs._MB_TYPE_INTRA) != 0).sum())
    skip = int(((t & vs.MB_TYPE_SKIP) != 0).sum())
    p16 = int(((t & vs.MB_TYPE_16x16) != 0).sum())
    p8 = int(((t & vs.MB_TYPE_8x8) != 0).sum())
    qpmin = int(fb.qp.min())
    qpmax = int(fb.qp.max())
    print(f"  frame {idx:3d}: grid={fb.grid_w}x{fb.grid_h} "
          f"intra={intra}/{n} skip={skip} 16x16={p16} 8x8={p8} "
          f"qp=[{qpmin},{qpmax}]")
    blocks = vs.blocks_from_frame(fb)
    print(f"             -> {len(blocks)} partition blocks")
