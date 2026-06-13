"""Validate the background/streaming block-analysis path.

Two checks:

  A. Truncation robustness (the core correctness claim): feed the finished
     .veblk to read_incremental() in awkward byte-sized prefixes, simulating a
     file being appended to. The accumulated frame map must equal a single
     authoritative load_sidecar() of the whole file, and no partial entry may
     ever be mis-parsed.

  B. End-to-end: force a cold cache, confirm Decoder.open() returns promptly,
     progress() advances 'running' -> 'done', and the sidecar's streamed
     frames match the authoritative full parse.

Run with any python that has numpy; needs native/veye_probe.exe.
"""

import hashlib
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from src.core.decoder import Decoder
from src.core.demuxer import Demuxer
from src.analysis.veye_sidecar import (
    blocks_from_frame, load_sidecar, mvs_from_frame, qp_grid_from_frame,
    read_incremental,
)

stream = sys.argv[1] if len(sys.argv) > 1 else r"tests\streams\h264_176x144_tiny.mp4"


def cache_path(video_path: str) -> str:
    st = os.stat(video_path)
    key = hashlib.sha1(
        f"{os.path.abspath(video_path)}|{st.st_size}|{int(st.st_mtime)}".encode()
    ).hexdigest()[:16]
    return os.path.join(tempfile.gettempdir(), f"veye_{key}.veblk")


def _arr_eq(a, b) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if a.shape != b.shape:
        return False
    return np.array_equal(a.view(np.uint8), b.view(np.uint8))


def frames_equal(fb_a, fb_b) -> bool:
    """Two VeyeFrameBlocks are equal if their derived products match."""
    return (_arr_eq(blocks_from_frame(fb_a), blocks_from_frame(fb_b))
            and _arr_eq(mvs_from_frame(fb_a), mvs_from_frame(fb_b))
            and _arr_eq(qp_grid_from_frame(fb_a), qp_grid_from_frame(fb_b)))


def check_truncation_robustness(full_path: str) -> int:
    """Feed growing byte-prefixes through read_incremental; compare to full."""
    with open(full_path, "rb") as f:
        data = f.read()
    full = load_sidecar(full_path)
    assert full, "full parse empty"

    tmp = full_path + ".partial"
    acc: dict = {}
    offset, header_ok = 0, False
    # Step by an odd size so prefixes routinely cut entries mid-header and
    # mid-payload, exercising the "wait for the rest" path at every boundary.
    step = 7
    try:
        for end in range(0, len(data) + 1, step):
            with open(tmp, "wb") as f:
                f.write(data[:end])
            new, offset, header_ok = read_incremental(tmp, offset, header_ok)
            acc.update(new)
        # Final pass at full length (in case len not divisible by step).
        with open(tmp, "wb") as f:
            f.write(data)
        new, offset, header_ok = read_incremental(tmp, offset, header_ok)
        acc.update(new)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    bad = 0
    if set(acc) != set(full):
        print(f"  frame-set mismatch: incr {len(acc)} vs full {len(full)}")
        bad += 1
    for idx in sorted(set(acc) & set(full)):
        if not frames_equal(acc[idx], full[idx]):
            bad += 1
            print(f"  frame {idx}: incremental != full")
    print(f"[A] truncation robustness: {len(acc)} frames, {bad} mismatch(es)")
    return bad


def check_end_to_end(stream_path: str) -> int:
    cp = cache_path(stream_path)
    if os.path.exists(cp):
        os.remove(cp)

    demuxer, decoder = Demuxer(), Decoder()
    assert demuxer.open(stream_path), "demuxer open failed"

    t0 = time.perf_counter()
    assert decoder.open(stream_path, frames=demuxer.frames), "decoder open failed"
    open_ms = (time.perf_counter() - t0) * 1000

    seen = set()
    deadline = time.time() + 600
    while time.time() < deadline:
        ready, _total, status = decoder.analysis_progress()
        seen.add(ready)
        if status != "running":
            break
        time.sleep(0.02)
    ready, total, status = decoder.analysis_progress()
    print(f"[B] open={open_ms:.0f}ms  final ready={ready}/{total} status={status}"
          f"  observed_counts={sorted(seen)[:8]}")
    bad = 0
    if status != "done":
        print(f"  expected status 'done', got {status}")
        bad += 1

    full = load_sidecar(cp)
    sc = decoder._block_sidecar
    for idx in sorted(full or {}):
        if not _arr_eq(sc.blocks_for(idx), blocks_from_frame(full[idx])):
            bad += 1
            print(f"  frame {idx}: streamed blocks != full")
    decoder.close()
    demuxer.close()
    print(f"[B] end-to-end: {bad} mismatch(es)")
    return bad


def main():
    cp = cache_path(stream)
    # Ensure a finished file exists for check A.
    if not os.path.exists(cp):
        d, dm = Decoder(), Demuxer()
        dm.open(stream)
        d.open(stream, frames=dm.frames)
        while decoder_running(d):
            time.sleep(0.02)
        d.close()
        dm.close()

    bad = check_truncation_robustness(cp)
    bad += check_end_to_end(stream)

    if bad:
        print("=== STREAMING VALIDATION FAILED ===")
        return 1
    print("=== STREAMING VALIDATION PASSED ===")
    return 0


def decoder_running(d) -> bool:
    _r, _t, status = d.analysis_progress()
    return status == "running"


if __name__ == "__main__":
    sys.exit(main())
