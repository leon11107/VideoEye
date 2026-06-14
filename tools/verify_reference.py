"""Verify the decode pipeline pixel-aligns with a reference decoder's YUV.

The reference .yuv (e.g. from JM ldecod / HM TAppDecoder / aomdec) is in
display order. Our frame index is decode/bitstream order, so frame N maps to
reference display position index_to_display[N]. For each sampled N we compare
decode_frame(N) to that reference frame (converted to RGB through the same
swscale path) -- a mismatch means either a wrong seek/mapping or a decoder
conformance difference.

Run: py -3.14 tools/verify_reference.py <bitstream> <ref.yuv> <W> <H> [N ...]
"""

import sys

import av
import numpy as np

sys.path.insert(0, ".")
from src.core.demuxer import Demuxer
from src.core.decoder import Decoder
from src.core.frame_info import FrameType
from src.parsers.poc import create_poc_tracker

bitstream = sys.argv[1]
ref_path = sys.argv[2]
W = int(sys.argv[3])
H = int(sys.argv[4])
FRAME = W * H * 3 // 2


def ref_rgb(disp_index):
    """Reference frame at a display index -> RGB via PyAV swscale."""
    with open(ref_path, "rb") as f:
        f.seek(disp_index * FRAME)
        buf = f.read(FRAME)
    if len(buf) < FRAME:
        return None
    arr = np.frombuffer(buf, np.uint8).reshape(H * 3 // 2, W)
    return av.VideoFrame.from_ndarray(arr, format="yuv420p").to_ndarray(format="rgb24")


dem = Demuxer()
assert dem.open(bitstream), "open failed"
frames = dem.frames
is_raw = bool(frames) and all(f.pts is None for f in frames[:8])
if is_raw and any(not f.is_keyframe for f in frames):
    dem.classify_frame_types(
        lambda b: FrameType.UNKNOWN,
        poc_tracker=create_poc_tracker(dem.codec_name),
    )

dec = Decoder()
assert dec.open(bitstream, frames), "decoder open failed"
n = len(frames)
ref_frames = __import__("os").path.getsize(ref_path) // FRAME
print(f"{bitstream}: {n} coded frames, ref has {ref_frames} display frames, "
      f"poc_map={'yes' if dec._emit_order is not None else 'no'}")

if len(sys.argv) > 5:
    targets = [int(x) for x in sys.argv[5:]]
else:
    step = max(1, n // 25)
    targets = sorted(set(list(range(0, n, step)) + [n - 1, n - 2, 1, 2]))

worst = 0
fails = 0
for N in targets:
    if not 0 <= N < n:
        continue
    my = dec.decode_frame(N)
    D = dec._index_to_display.get(N, N)
    rf = ref_rgb(D)
    if my is None or rf is None:
        print(f"  N={N:4d} disp={D:4d}: MISSING (my={my is not None} ref={rf is not None})")
        fails += 1
        continue
    d = int(np.abs(my.astype(int) - rf.astype(int)).max())
    mean = float(np.abs(my.astype(int) - rf.astype(int)).mean())
    worst = max(worst, d)
    flag = "OK" if d <= 2 else ("near" if d <= 8 else "MISMATCH")
    if d > 8:
        fails += 1
    print(f"  N={N:4d} disp={D:4d}: maxdiff={d:3d} mean={mean:.3f} {flag}")

dec.close()
dem.close()
print(f"=> worst maxdiff={worst}, failures={fails}")
