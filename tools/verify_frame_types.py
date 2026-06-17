"""Regression check: our per-frame I/P/B vs ffmpeg's authoritative pict_type.

Loads a stream through the REAL app path (MainWindow._load_file) -- so it
exercises the demuxer frame-type classification that the bar chart actually
displays -- then compares each frame's type to ffprobe's pict_type keyed by PTS.

Why this exists: the Elecard block-level golden harnesses verify the *sidecar*
(per-CU/CTU/MB) path; the bar chart's frame type comes from a different path
(demuxer.classify_frame_types) and had no ground-truth check, so a HEVC
slice-parse bug (B/P shown as I) slipped through. ffprobe is the ground truth.

Usage: py -3.14 tools/verify_frame_types.py <video> [video2 ...]
       (no args -> the three tests/streams patterns)
Exit code is non-zero if any mismatch is found.
"""
import json
import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, ".")
from PyQt6.QtWidgets import QApplication

FFPROBE = r"C:/Users/llw/app/ffprobe"
_MAP = {"I": "I", "SI": "I", "P": "P", "SP": "P", "B": "B", "BI": "B"}


def ffprobe_frames(path):
    """ffprobe pict_type list in display order, plus a pts->type map.

    Container streams carry PTS so we key by it (robust to reordering). Raw
    streams have no PTS, so we fall back to display order -- ffprobe (like our
    decoder) emits frames in display order, so a positional list lines up with
    our frames once they're sorted into display order too.
    """
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "frame=pts,pict_type", "-of", "json", path],
        capture_output=True, text=True).stdout
    by_pts, display = {}, []
    for fr in json.loads(out).get("frames", []):
        if not fr.get("pict_type"):
            continue
        t = _MAP.get(fr["pict_type"], fr["pict_type"])
        display.append(t)
        if fr.get("pts") is not None:
            by_pts[fr["pts"]] = t
    return by_pts, display


def check(app, path):
    from src.app import MainWindow
    w = MainWindow()
    w._load_file(path)
    app.processEvents()
    frames = w._barchart_view._chart._frames
    by_pts, display = ffprobe_frames(path)
    name = os.path.basename(path)

    if any(f.pts is not None for f in frames):
        ours = {f.pts: f.frame_type.value for f in frames}
        keys = sorted(set(ours) & set(by_pts))
        bad = [(k, ours[k], by_pts[k]) for k in keys if ours[k] != by_pts[k]]
        print(f"{name}: {len(keys)} frames matched by PTS, {len(bad)} mismatches")
        for k, o, g in bad[:8]:
            print(f"  pts={k}: ours={o} ffmpeg={g}")
        return len(bad) == 0

    # Raw stream: align by display order via .poc.
    pocs = [f.poc for f in frames]
    if None in pocs or len(set(pocs)) != len(pocs):
        print(f"{name}: SKIP (raw stream without a usable per-frame poc)")
        return True
    if len(display) != len(frames):
        print(f"{name}: FAIL (frame count {len(frames)} != ffprobe {len(display)})")
        return False
    ours = [f.frame_type.value for f in sorted(frames, key=lambda x: x.poc)]
    bad = [(i, o, g) for i, (o, g) in enumerate(zip(ours, display)) if o != g]
    print(f"{name}: {len(frames)} frames matched by display order, "
          f"{len(bad)} mismatches")
    for i, o, g in bad[:8]:
        print(f"  display#{i}: ours={o} ffmpeg={g}")
    return len(bad) == 0


def main():
    args = sys.argv[1:] or [
        "tests/streams/hevc_1080p.mp4",
        "tests/streams/av1_1080p.mp4",
        "tests/streams/h264_1080p_default.mp4",
        "tests/streams/h264_annexb.264",  # raw Annex-B: no PTS, align by poc
    ]
    app = QApplication([])
    ok = all(check(app, p) for p in args)
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
