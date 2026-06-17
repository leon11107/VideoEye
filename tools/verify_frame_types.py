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


def ffprobe_types(path):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "frame=pts,pict_type", "-of", "json", path],
        capture_output=True, text=True).stdout
    res = {}
    for fr in json.loads(out).get("frames", []):
        if "pts" in fr and fr.get("pict_type"):
            res[fr["pts"]] = _MAP.get(fr["pict_type"], fr["pict_type"])
    return res


def check(app, path):
    from src.app import MainWindow
    w = MainWindow()
    w._load_file(path)
    app.processEvents()
    ours = {f.pts: f.frame_type.value for f in w._barchart_view._chart._frames}
    gt = ffprobe_types(path)
    keys = sorted(set(ours) & set(gt))
    bad = [(k, ours[k], gt[k]) for k in keys if ours[k] != gt[k]]
    name = os.path.basename(path)
    print(f"{name}: {len(keys)} frames matched by PTS, {len(bad)} mismatches")
    for k, o, g in bad[:8]:
        print(f"  pts={k}: ours={o} ffmpeg={g}")
    return len(bad) == 0


def main():
    args = sys.argv[1:] or [
        "tests/streams/hevc_1080p.mp4",
        "tests/streams/av1_1080p.mp4",
        "tests/streams/h264_1080p_default.mp4",
    ]
    app = QApplication([])
    ok = all(check(app, p) for p in args)
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
