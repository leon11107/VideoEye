"""Headless sanity scan over the generated stream matrix.

For every stream: open demuxer + decoder, decode a set of frames
(including random seeks), and validate decoded images and block
analysis for consistency. Exits non-zero on any failure.
"""

import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.decoder import Decoder
from src.core.demuxer import Demuxer

STREAM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "streams")

failures = []


def check(cond, msg, ctx):
    if not cond:
        failures.append(f"{ctx}: {msg}")
        print(f"  FAIL {msg}")


def validate_analysis(a, width, height, ctx):
    if a is None:
        return
    check(a.width == width and a.height == height, "analysis size mismatch", ctx)
    if a.qp_grid is not None:
        unit = a.qp_unit
        rows = (height + unit - 1) // unit
        cols = (width + unit - 1) // unit
        check(a.qp_grid.shape == (rows, cols),
              f"qp_grid shape {a.qp_grid.shape} != ({rows},{cols})", ctx)
        valid = a.qp_grid[a.qp_grid >= 0]
        if valid.size:
            check(0 <= int(valid.min()) and int(valid.max()) <= 63,
                  f"QP out of range [{valid.min()},{valid.max()}]", ctx)
    if a.mvs is not None and len(a.mvs):
        m = a.mvs
        inside = ((m["x"] + m["w"] > 0) & (m["x"] < width)
                  & (m["y"] + m["h"] > 0) & (m["y"] < height))
        frac = float(inside.mean())
        check(frac > 0.99, f"only {frac:.2%} MV blocks intersect frame", ctx)


def scan(path):
    name = os.path.basename(path)
    print(f"--- {name}")
    demuxer, decoder = Demuxer(), Decoder()
    t0 = time.perf_counter()
    try:
        if not demuxer.open(path):
            failures.append(f"{name}: demuxer open failed")
            print("  FAIL demuxer open")
            return
        if not decoder.open(path, frames=demuxer.frames):
            failures.append(f"{name}: decoder open failed")
            print("  FAIL decoder open")
            return

        total = len(demuxer.frames)
        codec = demuxer.stream_info.codec_name
        # forward, backward seek, random seek, last frame
        targets = [0, 1, 2, min(5, total - 1), 1, total - 1, total // 2]
        targets = [t for t in targets if 0 <= t < total]

        n_analysis = qp_frames = mv_frames = 0
        for idx in targets:
            rgb = decoder.decode_frame(idx)
            check(rgb is not None, f"decode_frame({idx}) returned None", name)
            if rgb is None:
                continue
            h, w = rgb.shape[:2]
            a = decoder.get_analysis(idx)
            if a is not None:
                n_analysis += 1
                validate_analysis(a, w, h, f"{name}[{idx}]")
                if a.qp_grid is not None:
                    qp_frames += 1
                if a.mvs is not None and len(a.mvs):
                    mv_frames += 1

        dt = (time.perf_counter() - t0) * 1000
        print(f"  ok: codec={codec} frames={total} analyzed={n_analysis} "
              f"qp={qp_frames} mv={mv_frames} ({dt:.0f} ms)")

        if codec == "h264":
            check(qp_frames > 0, "h264 stream produced no QP data", name)
    except Exception:
        failures.append(f"{name}: exception\n{traceback.format_exc()}")
        print(f"  FAIL exception:\n{traceback.format_exc()}")
    finally:
        decoder.close()
        demuxer.close()


def main():
    streams = sorted(
        os.path.join(STREAM_DIR, f) for f in os.listdir(STREAM_DIR)
        if f.endswith((".mp4", ".264", ".ts", ".mkv", ".265", ".ivf"))
    )
    if not streams:
        print("no streams found — run tests/gen_streams.ps1 first")
        return 1
    for s in streams:
        scan(s)

    print()
    if failures:
        print(f"=== {len(failures)} FAILURE(S) ===")
        for f in failures:
            print(f" - {f}")
        return 1
    print(f"=== ALL {len(streams)} STREAMS PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
